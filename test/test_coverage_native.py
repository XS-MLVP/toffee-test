"""Native desktop links, real local process dispatch and provider integration."""

import json
from pathlib import Path
import sys

import pytest

from toffee_test.utils.code_coverage import (
    ApplicationDetail, CodeCoverageResult, CoverageReportContext, HtmlDetail, MetricSummary,
)
from toffee_test.utils.code_coverage import native
from toffee_test.utils.code_coverage.native import (
    launch_native, read_native_link, register_native, resolve_native_detail, state_dir,
)
from toffee_test.utils.code_coverage.uvs import UvsCoverageProvider


@pytest.fixture(autouse=True)
def isolated_desktop(tmp_path, monkeypatch):
    """Keep every test receipt and desktop setting outside the actual user profile."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def make_link(tmp_path):
    """Generate a native entry with a literal path containing shell punctuation."""
    report = tmp_path / "reports"
    report.mkdir(exist_ok=True)
    database = tmp_path / "design with spaces;echo ignored.vdb"
    database.mkdir(exist_ok=True)
    uri = resolve_native_detail(
        ApplicationDetail("verdi", "Verdi", {"database": str(database)}),
        CoverageReportContext(tmp_path, report),
    )
    return uri, report, database


def test_protocol_launches_local_process_without_web_server(tmp_path):
    """The URI launches a real application directly and preserves literal arguments."""
    uri, report, database = make_link(tmp_path)
    assert uri.startswith("toffee-coverage://open/")
    assert not list(report.rglob("*.html"))
    observed = tmp_path / "observed.json"
    script = tmp_path / "viewer.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n"
    )
    (state_dir() / "viewers.json").write_text(json.dumps({
        "verdi": [sys.executable, str(script), str(observed), "-covdir", "{database}"],
    }))
    assert not observed.exists()
    result = launch_native(uri)
    assert result["pid"] > 0
    assert json.loads(observed.read_text()) == ["-covdir", str(database)]


@pytest.mark.parametrize("uri", [
    "https://other.test/open",
    "toffee-coverage://open/../outside",
    "toffee-coverage://open/" + "a" * 64 + "?command=sh",
    "toffee-coverage://elsewhere/" + "a" * 64,
    "toffee-coverage://open/" + "a" * 64 + "#fragment",
])
def test_untrusted_uri_shapes_cannot_supply_paths_or_commands(uri):
    """Only locally minted capability IDs may select native inputs."""
    with pytest.raises(ValueError, match="Invalid native coverage link"):
        launch_native(uri)


def test_unknown_or_other_user_link_is_rejected():
    """A copied or fabricated link cannot reference arbitrary local manifests."""
    with pytest.raises(ValueError, match="not registered"):
        launch_native("toffee-coverage://open/" + "a" * 64)


def test_report_manifest_is_not_launch_authority(tmp_path):
    """Editing the report copy cannot change the trusted database or execute commands."""
    uri, report, database = make_link(tmp_path)
    manifest = next(report.rglob("*.native.json"))
    manifest.write_text('{"viewer":"shell","artifacts":{"command":"rm"}}')
    assert read_native_link(uri)["artifacts"]["database"] == str(database)
    assert read_native_link(uri)["viewer"] == "verdi"


def test_artifact_escape_is_rechecked_when_opening(tmp_path):
    """A registered database may not later become an external symlink."""
    uri, _, database = make_link(tmp_path)
    database.rmdir()
    database.symlink_to(tmp_path.parent)
    with pytest.raises(ValueError, match="inside the workspace"):
        launch_native(uri)


def test_missing_application_has_actionable_error(tmp_path):
    """A missing configured executable fails instead of reporting a GUI as opened."""
    uri, _, _ = make_link(tmp_path)
    (state_dir() / "viewers.json").write_text(json.dumps({
        "verdi": [str(tmp_path / "missing-viewer"), "{database}"],
    }))
    with pytest.raises(FileNotFoundError):
        launch_native(uri)


def test_desktop_registration_preserves_uri_argument(tmp_path, monkeypatch):
    """The one-time registration binds the URI scheme to this Python installation."""
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
    monkeypatch.setattr(native.subprocess, "run", run)
    monkeypatch.setattr(native.shutil, "which",
                        lambda name: "/usr/bin/xdg-mime" if name == "xdg-mime" else None)
    desktop = register_native({"verdi": ["/opt/verdi/bin/verdi", "-covdir", "{database}"]})
    assert "MimeType=x-scheme-handler/toffee-coverage;" in desktop.read_text()
    assert " %u\n" in desktop.read_text()
    assert "Terminal=false" in desktop.read_text()
    assert calls == [["/usr/bin/xdg-mime", "default", native.DESKTOP_ID,
                      "x-scheme-handler/toffee-coverage"]]
    launcher = tmp_path / "data/toffee-test/native-handler.py"
    assert "main(['native-open', *sys.argv[1:]])" in launcher.read_text()
    assert json.loads((state_dir() / "viewers.json").read_text())["verdi"][0] == "/opt/verdi/bin/verdi"


@pytest.mark.parametrize("html,application", [(True, True), (True, False), (False, True)])
def test_uvs_main_report_result(tmp_path, html, application):
    """UVS link construction consumes parsed statistics and actual session output paths."""
    output = tmp_path / "line_dat"
    html_dir = output / "native/uvs"
    html_dir.mkdir(parents=True)
    (html_dir / "index.html").write_text("<html>Coverage</html>")
    database = tmp_path / "coverage_database"
    database.mkdir()
    metrics = {"line": MetricSummary(2, 3, 1, 200 / 3, True, True)}
    # Part 1 supplies statistics and artifact paths; part 2 builds link descriptors.
    result = CodeCoverageResult(
        "uvs", metrics=metrics,
        detail="line_dat/native/uvs/index.html" if html else "",
        databases=[str(database)] if application else [],
    )
    expected = ([HtmlDetail("line_dat/native/uvs/index.html")] if html else [])
    if application:
        expected.append(ApplicationDetail("uvd", "UVD", {"database": str(database)}, "Full native database"))
    assert result.metrics == metrics
    assert UvsCoverageProvider().build_details(result, output) == expected


def test_native_link_is_not_an_html_url(tmp_path):
    """Only application descriptors may link to the narrowly allowed desktop scheme."""
    uri, _, _ = make_link(tmp_path)
    report = CodeCoverageResult("test", details=[HtmlDetail(uri)]).to_report_dict()
    assert report["details"] == []
    assert report["warnings"]


@pytest.mark.parametrize("mode", ["off", "native", "xpra", "xpra-source", "xpra-link", "xpra-provider"])
def test_gui_modes_in_real_pytest_report(pytester, monkeypatch, mode):
    """The mode controls final HTML/JSON and native descriptors without starting a GUI."""
    pytester.makepyfile(coverage_host='def resolve(detail, context):\n    assert context.workspace.is_dir()\n    return "host-app.html"\n')
    pytester.makeconftest('''
from pathlib import Path
import pytest
from toffee_test.reporter import set_code_coverage
from toffee_test.utils.code_coverage import *
def pytest_configure(config):
    database = Path("dut.vdb").resolve()
    database.mkdir(exist_ok=True)
    def convert(records, output):
        return CodeCoverageResult("native_test", metrics={"line": MetricSummary(1, 2, available=True)},
            details=[HtmlDetail("native.html"), ApplicationDetail("verdi", "Verdi", {"database": str(database)})])
    register_coverage_provider("native_test", case_converter=convert, session_converter=convert)
@pytest.fixture
def coverage(request):
    yield
    set_code_coverage(request, CoverageRecord("native_test", "dut.vdb"))
''')
    pytester.makepyfile('def test_example(coverage):\n    assert True\n')
    args = ["--toffee-report", "--report-name=index.html", "--report-dir=report",
            "--report-dump-json", "--coverage-gui=" + mode.split("-")[0]]
    if mode == "xpra-provider":
        conftest = pytester.path / "conftest.py"
        text = conftest.read_text().replace('return CodeCoverageResult(', 'result = CodeCoverageResult(')
        text = text.replace('    register_coverage_provider(', '        from toffee_test.utils.code_coverage.vcs import VcsCoverageProvider\n        result.details = [HtmlDetail("native.html")]\n        result.databases = [str(database)]\n        result.details = VcsCoverageProvider().build_details(result, output)\n        return result\n    register_coverage_provider(')
        conftest.write_text(text)
    if mode == "xpra-link":
        conftest = pytester.path / "conftest.py"
        conftest.write_text(conftest.read_text().replace(
            'ApplicationDetail("verdi", "Verdi", {"database": str(database)})',
            'ApplicationDetail("verdi", "Verdi", href="https://viewer.example/session/1")'))
    if mode == "xpra-source":
        from toffee_test.utils import base64_encode
        args.append("--custom-key-value=" + base64_encode({
            "coverage_gui": {"resolver": "coverage_host:resolve"},
        }))
    if mode == "xpra":
        args.append("--coverage-application-resolver=coverage_host:resolve")
    if mode == "off":
        # Off must not import or execute even an explicitly supplied host resolver.
        args.append("--coverage-application-resolver=missing_module:resolve")
    result = pytester.runpytest(*args)
    result.assert_outcomes(passed=1)
    page = (pytester.path / "report/index.html").read_text()
    assert ">HTML</a>" in page
    assert (">Verdi</a>" in page) == (mode != "off")
    from test_coverage_report_details import Links
    links = Links()
    links.feed(page)
    assert ("host-app.html" in links.hrefs) == (mode in ("xpra", "xpra-source"))
    if mode == "xpra-link":
        assert "https://viewer.example/session/1" in links.hrefs
        assert not (pytester.path / "report/coverage_apps").exists()
    manifests = list((pytester.path / "report/coverage_apps").glob("*.native.json"))
    assert len(manifests) == int(mode == "native")


def test_host_can_register_callback_during_pytest_configure(pytester):
    """Host registration is checked after all pytest_configure hooks have run."""
    pytester.makeconftest("""
def pytest_configure(config):
    config.toffee_coverage_application_resolver = lambda detail, context: "host.html"
""")
    pytester.makepyfile("def test_example():\n    assert True\n")
    result = pytester.runpytest("--coverage-gui=xpra")
    result.assert_outcomes(passed=1)


def test_native_rejects_competing_host_resolver(pytester):
    """A mode cannot accidentally mix local launches and host-generated pages."""
    result = pytester.runpytest("--coverage-gui=native",
                               "--coverage-application-resolver=missing_module:resolve")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*only used with --coverage-gui=xpra*"])


def test_native_anchor_does_not_open_a_browser_tab(tmp_path):
    """file:// reports send native links to the OS instead of an intermediate page."""
    from html.parser import HTMLParser
    from test_coverage_report_details import render_coverage
    uri, _, _ = make_link(tmp_path)
    class Anchors(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                links.append(dict(attrs))
    links = []
    page = render_coverage({
        "hints": 2, "total": 3, "grate": 99,
        "details": [{"label": "Verdi", "kind": "application", "href": uri}],
    })
    Anchors().feed(page)
    native_anchor = next(link for link in links if link["href"] == uri)
    assert "target" not in native_anchor
    assert "data-coverage-application" in native_anchor
