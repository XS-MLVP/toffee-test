"""Focused regressions for explicit report paths and real pytest output."""

import json
import os
import shutil
from pathlib import Path

import pytest

from toffee_test import gate_toffee_test_file
from toffee_test.utils.code_coverage import get_coverage_artifact_path
from toffee_test.utils.code_coverage.common import write_line_coverage_summary


def _write_json(path, value):
    """Create a JSON fixture without imposing any report-directory convention."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path

def _report_tree(tmp_path):
    """Create differently named report, summary, and LCOV files in separate folders."""
    root = tmp_path / "arbitrary output with spaces"
    report = _write_json(root / "execution-result.json", {
        "coverages": {"line": {"coverage_json": "observations/session-summary.json"}},
    })
    summary = _write_json(root / "observations/session-summary.json", {
        "artifacts": {"lcov": "../native/details/current-run.lcov"},
    })
    lcov = root / "native/details/current-run.lcov"
    lcov.parent.mkdir(parents=True)
    lcov.write_text("SF:/rtl/top.v\nDA:1,1\nend_of_record\n", encoding="utf-8")
    return report, summary, lcov

def test_checker_json_is_selected_instead_of_compacted_cumulative_summary(tmp_path):
    """The checker-facing JSON must retain details omitted by cumulative output."""
    report, summary, lcov = _report_tree(tmp_path)
    cumulative = _write_json(report.parent / "statistics/cumulative.json", {
        "uncovered": {"data": {}}, "artifacts": {},
    })
    _write_json(report, {"coverages": {"line": {
        "coverage_json": "observations/session-summary.json",
        "summary_file": "statistics/cumulative.json",
    }}})
    assert gate_toffee_test_file(report, "coverage_json") == summary
    assert gate_toffee_test_file(report, "coverage_json") != cumulative
    assert gate_toffee_test_file(report, "lcov") == lcov
    _write_json(report, {"coverages": {"line": {
        "summary_file": "statistics/cumulative.json",
    }}})
    assert gate_toffee_test_file(report, "coverage_json") is None
    assert gate_toffee_test_file(report, "lcov") is None

def test_declared_missing_lcov_is_not_replaced_by_old_output(tmp_path):
    """A missing selected LCOV cannot fall back to merged.info."""
    report, summary, _ = _report_tree(tmp_path)
    _write_json(summary, {"artifacts": {"lcov": "missing-run.lcov"}})
    summary.with_name("merged.info").write_text("stale", encoding="utf-8")
    assert gate_toffee_test_file(report, "coverage_json") == summary
    with pytest.raises(FileNotFoundError):
        gate_toffee_test_file(report, "lcov")

@pytest.mark.parametrize("reference", ["/tmp/absolute-summary.json", "../outside.json"])
@pytest.mark.parametrize("file_type", ["coverage_json", "lcov"])
def test_invalid_summary_reference_is_rejected(tmp_path, reference, file_type):
    """Summary declarations must be relative paths contained by the report root."""
    report = _write_json(tmp_path / "reports/result.json", {
        "coverages": {"line": {"coverage_json": reference}},
    })
    with pytest.raises(ValueError):
        gate_toffee_test_file(report, file_type)

@pytest.mark.parametrize("target", ["summary", "lcov"])
def test_symlink_escape_is_rejected(tmp_path, target):
    """Lexically contained references cannot follow symlinks outside the report."""
    report, summary, _ = _report_tree(tmp_path)
    outside = _write_json(tmp_path / "outside.json", {"artifacts": {}})
    link = report.parent / "escape-link"
    link.symlink_to(outside)
    if target == "summary":
        _write_json(report, {"coverages": {"line": {"coverage_json": "escape-link"}}})
    else:
        _write_json(summary, {"artifacts": {"lcov": "../escape-link"}})
    with pytest.raises(ValueError):
        gate_toffee_test_file(report, "coverage_json" if target == "summary" else "lcov")

def test_real_pytest_report_without_coverage_has_no_coverage_files(pytester, monkeypatch):
    """The public lookup handles an actual minimal plugin-generated JSON report."""
    project_root = Path(__file__).parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [
        str(project_root), os.environ.get("PYTHONPATH"),
    ])))
    pytester.makepyfile("def test_plain():\n    assert 1 + 1 == 2\n")
    result = pytester.runpytest_subprocess(
        "-q", "--toffee-report", "--report-dump-json", "--report-name=index.html",
        "--report-dir=arbitrary nested/output", timeout=30,
    )
    result.assert_outcomes(passed=1)
    report = pytester.path / "arbitrary nested/output/toffee_report.json"
    before = report.read_bytes()
    assert json.loads(before)["coverages"] == {"line": {}, "functional": {}}
    assert gate_toffee_test_file(report, "report_json") == report
    assert gate_toffee_test_file(report, "coverage_json") is None
    assert gate_toffee_test_file(report, "lcov") is None
    assert report.read_bytes() == before


def test_summary_links_survive_export_and_report_move(tmp_path):
    """Both summaries point to the provider's current LCOV after moving a report."""
    report = tmp_path / "original report"
    output = report / "coverage_data"
    output.mkdir(parents=True)
    native = output / "details/current.info"
    native.parent.mkdir()
    native.write_text("current LCOV", encoding="utf-8")
    summary = {"overview": {}, "artifacts": {"lcov": "details/current.info"}}
    (output / "code_coverage.json").write_text(json.dumps(summary), encoding="utf-8")
    write_line_coverage_summary(summary, output)
    assert summary["artifacts"]["lcov"] == "details/current.info"
    assert not (report / "line_dat/merged.info").exists()

    moved = tmp_path / "moved report"
    shutil.copytree(report, moved)
    for relative in ("coverage_data/code_coverage.json", "line_dat/code_coverage.json"):
        path = get_coverage_artifact_path(moved / relative, "lcov")
        assert path == moved / "coverage_data/details/current.info"
        assert path.read_text() == "current LCOV"


def test_current_pytest_execution_publishes_paths_and_conversion_failures(pytester, monkeypatch):
    """Run twice in one output directory, then fail without exposing old artifacts."""
    root_source = str(Path(__file__).resolve().parents[1])
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [root_source, os.environ.get("PYTHONPATH")])))
    pytester.makeconftest('''
import json
import os
import pytest
from toffee_test.reporter import set_code_coverage
from toffee_test.utils.code_coverage import CoverageRecord, CodeCoverageResult, register_coverage_provider

def pytest_configure(config):
    def convert(records, output_dir):
        run = os.environ['REPORT_RUN']
        if run == 'failed':
            raise RuntimeError('converter failed for current run')
        root = output_dir.parent
        summary = root / ('observations-' + run) / 'full.json'
        lcov = root / ('native-' + run) / 'lines.lcov'
        summary.parent.mkdir(parents=True, exist_ok=True)
        lcov.parent.mkdir(parents=True, exist_ok=True)
        lcov.write_text('SF:/rtl/top.v\\nDA:1,1\\nend_of_record\\n')
        summary.write_text(json.dumps({
            'overview': {'total': {'line': 1}, 'miss': {'line': 0}},
            'artifacts': {'lcov': '../native-' + run + '/lines.lcov'},
        }))
        return CodeCoverageResult('report_files_test',
            summary_file='not-the-checker.json',
            coverage_json=summary.relative_to(root).as_posix())
    register_coverage_provider('report_files_test', case_converter=convert, session_converter=convert)

@pytest.fixture
def coverage(request):
    yield
    set_code_coverage(request, CoverageRecord('report_files_test', 'test.db'))
''')
    pytester.makepyfile('def test_device(coverage):\n    assert True\n')
    root = pytester.path / "nested reports" / "chosen output"
    report = root / "toffee_report.json"
    args = ["-q", "--toffee-report", "--report-dump-json", "--report-name=results.html", f"--report-dir={root}"]
    for run in ("first", "second"):
        monkeypatch.setenv("REPORT_RUN", run)
        result = pytester.runpytest_subprocess(*args)
        result.assert_outcomes(passed=1)
        assert gate_toffee_test_file(report, "report_json") == report
        summary = gate_toffee_test_file(report)
        assert summary == root / f"observations-{run}/full.json"
        assert gate_toffee_test_file(report, "lcov") == root / f"native-{run}/lines.lcov"
        assert json.loads(summary.read_text())["overview"]["total"]["line"] == 1
    # The earlier run's files remain present; the latest conversion error wins.
    monkeypatch.setenv("REPORT_RUN", "failed")
    result = pytester.runpytest_subprocess(*args)
    result.assert_outcomes(passed=1)
    import pytest
    for kind in ("coverage_json", "lcov"):
        with pytest.raises(RuntimeError, match="converter failed for current run"):
            gate_toffee_test_file(report, kind)
    assert gate_toffee_test_file(report, "report_json") == report
