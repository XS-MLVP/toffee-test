"""Optional provider helper for links using UCAgent's generic Xpra session API."""

import json
from pathlib import Path
import shlex
from urllib.parse import quote
import uuid


def write_xpra_link(output_dir: Path, argv: list[str], *, label: str, cwd: str = ".") -> str:
    """Write a click-to-launch page and return its URL relative to index.html.

    Providers own argv and artifact paths. Use absolute artifact paths so reports
    in nested directories work with the host's default workspace. This helper
    only writes HTML; the authenticated host starts a session when clicked.
    output_dir is the provider's coverage directory beside the final index.html.
    A provider may instead return any existing viewer URL without this helper.
    """
    if not argv or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in argv):
        raise ValueError("Xpra argv must contain nonempty strings without NUL characters")
    output_dir = Path(output_dir).resolve()
    directory = output_dir / "applications"
    if not directory.resolve().is_relative_to(output_dir):
        raise ValueError("Application pages must remain inside the coverage directory")
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"command": shlex.join(argv), "mode": "gui", "name": label, "cwd": cwd}
    # JSON embedded in a script must not be able to close that script element.
    request = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c")
    template = Path(__file__).resolve().parents[2] / "templates/html/coverage_launch.html"
    page = directory / f"{uuid.uuid4().hex}.html"
    page.write_text(template.read_text(encoding="utf-8").replace("__COVERAGE_REQUEST__", request), encoding="utf-8")
    return quote(page.relative_to(output_dir.parent).as_posix(), safe="/")
