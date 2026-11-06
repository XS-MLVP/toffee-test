"""Local desktop protocol for coverage applications; no HTTP service is required."""

import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

from .models import ApplicationDetail, CoverageReportContext


SCHEME = "toffee-coverage"
DESKTOP_ID = "org.toffee.Coverage.desktop"
DEFAULT_VIEWERS = {"verdi": ["verdi", "-cov", "-covdir", "{database}"]}


def state_dir() -> Path:
    """Locate private, per-user native launch registrations."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "toffee-test/native"


def _write_private(path: Path, text: str) -> None:
    """Create a private launch receipt without following an existing file or symlink."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(text)


def _artifacts(payload: dict, workspace: Path) -> dict[str, str]:
    """Resolve existing native inputs inside the explicitly selected workspace."""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("Application artifacts must be a nonempty mapping")
    resolved = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError("Invalid artifact name")
        if not isinstance(value, str):
            raise ValueError("Artifact paths must be strings")
        path = (workspace / value).resolve(strict=True)
        if not path.is_relative_to(workspace) or not (path.is_file() or path.is_dir()):
            raise ValueError("Application artifacts must remain inside the workspace")
        if path.is_dir() and any(
            child.is_symlink() and not child.resolve().is_relative_to(workspace)
            for child in path.rglob("*")
        ):
            raise ValueError("Database symlinks must remain inside the workspace")
        resolved[key] = str(path)
    return resolved


def resolve_native_detail(detail: ApplicationDetail, context: CoverageReportContext) -> str:
    """Register trusted local artifacts and return a direct desktop protocol link."""
    workspace = context.workspace.resolve(strict=True)
    report_dir = context.report_dir.resolve()
    directory = report_dir / "coverage_apps"
    if not report_dir.is_relative_to(workspace) or not directory.resolve().is_relative_to(report_dir):
        raise ValueError("Native report files must remain inside the workspace")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", detail.viewer):
        raise ValueError("Invalid native viewer identifier")
    artifacts = _artifacts(detail.artifacts, workspace)
    token = secrets.token_hex(32)
    payload = json.dumps({
        "schema_version": 1, "viewer": detail.viewer, "label": detail.label,
        "workspace": str(workspace), "artifacts": artifacts,
    }, indent=2) + "\n"
    # The handler trusts only this private receipt, never browser-supplied paths
    # or the editable copy placed alongside the HTML report.
    _write_private(state_dir() / "links" / f"{token}.json", payload)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{detail.viewer}-{token}.native.json").write_text(payload, encoding="utf-8")
    return f"{SCHEME}://open/{token}"


def read_native_link(uri: str) -> dict:
    """Resolve a capability minted by this user's local report generation."""
    url = urlsplit(uri)
    if (url.scheme != SCHEME or url.netloc != "open" or url.query or url.fragment
            or not re.fullmatch(r"/[0-9a-f]{64}", url.path)):
        raise ValueError("Invalid native coverage link")
    path = state_dir() / "links" / (url.path[1:] + ".json")
    if path.is_symlink():
        raise ValueError("Native receipt must not be a symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("This native link is not registered for this user; regenerate the report locally") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Invalid native coverage receipt")
    return payload


def validate_viewers(viewers: dict) -> dict:
    """Validate operator-owned argv lists; whole artifact arguments are substituted later."""
    if not isinstance(viewers, dict):
        raise ValueError("Viewer configuration must be a JSON object")
    for name, argv in viewers.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name)
                or not isinstance(argv, list) or not argv
                or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv)
                or "{" in argv[0] or "}" in argv[0]):
            raise ValueError("Viewer configuration must map viewer IDs to nonempty argv lists")
    return viewers


def launch_native(uri: str) -> dict:
    """Open a registered coverage database directly in the local desktop application."""
    payload = read_native_link(uri)
    workspace = Path(payload["workspace"]).resolve(strict=True)
    artifacts = _artifacts(payload["artifacts"], workspace)
    viewers = dict(DEFAULT_VIEWERS)
    config = state_dir() / "viewers.json"
    if config.exists():
        viewers.update(validate_viewers(json.loads(config.read_text(encoding="utf-8"))))
    viewer = payload["viewer"]
    if viewer not in viewers:
        raise ValueError(f"No local command configured for {viewer}; run register-native --viewer-config")
    argv = [
        artifacts[arg[1:-1]] if arg.startswith("{") and arg.endswith("}") else arg
        for arg in viewers[viewer]
    ]
    with tempfile.NamedTemporaryFile(prefix="toffee-native-", suffix=".log", delete=False) as log:
        child = subprocess.Popen(argv, cwd=workspace, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT,
                                 shell=False, start_new_session=True)
    try:
        code = child.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        return {"pid": child.pid, "log": log.name}
    if code:
        raise ValueError(f"Application exited with status {code}; see {log.name}")
    return {"pid": child.pid, "log": log.name}


def _desktop_arg(value: str) -> str:
    """Quote a fixed argument using the Desktop Entry Exec escaping rules."""
    if any(ord(char) < 32 for char in value):
        raise ValueError("Desktop handler paths must not contain control characters")
    # Simple paths need no quotes; this also works with minimal xdg-open parsers.
    if re.fullmatch(r"[A-Za-z0-9_./:-]+", value):
        return value
    escaped = "".join("\\" + c if c in '\\"$`' else c for c in value)
    return '"' + escaped.replace("\\", "\\\\").replace("%", "%%") + '"'


def register_native(viewers: dict | None = None) -> Path:
    """Install this user's Linux desktop URI handler and trusted viewer configuration."""
    if not sys.platform.startswith("linux"):
        raise ValueError("Native protocol registration currently supports Linux desktops only")
    xdg_mime = shutil.which("xdg-mime")
    if xdg_mime is None:
        raise ValueError("Install xdg-utils to register the native coverage protocol")
    config = state_dir() / "viewers.json"
    commands = dict(DEFAULT_VIEWERS)
    if config.exists():
        commands.update(validate_viewers(json.loads(config.read_text(encoding="utf-8"))))
    if viewers is not None:
        commands.update(validate_viewers(viewers))
    # Desktop launchers need not inherit the invoking terminal's PATH.
    for name, argv in commands.items():
        executable = shutil.which(argv[0])
        if executable:
            commands[name] = [executable, *argv[1:]]
    config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config.write_text(json.dumps(commands, indent=2) + "\n", encoding="utf-8")
    os.chmod(config, 0o600)
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    launcher = data_home / "toffee-test/native-handler.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    package_root = str(Path(__file__).resolve().parents[3])
    launcher.write_text(
        "import sys\nsys.path.insert(0, " + repr(package_root) + ")\n"
        "from toffee_test.cli import main\nmain(['native-open', *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    desktop = data_home / "applications" / DESKTOP_ID
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=Toffee Coverage\n"
        "Comment=Open local coverage applications\nNoDisplay=true\nTerminal=false\n"
        f"Exec={_desktop_arg(sys.executable)} {_desktop_arg(str(launcher))} %u\n"
        f"MimeType=x-scheme-handler/{SCHEME};\n",
        encoding="utf-8",
    )
    updater = shutil.which("update-desktop-database")
    if updater:
        subprocess.run([updater, str(desktop.parent)], check=True, capture_output=True, text=True)
    subprocess.run([xdg_mime, "default", DESKTOP_ID, f"x-scheme-handler/{SCHEME}"],
                   check=True, capture_output=True, text=True)
    return desktop
