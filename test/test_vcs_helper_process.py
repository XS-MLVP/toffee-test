"""Exercise the VCS helper protocol without an xcomm coverage client."""

import json
import subprocess

import pytest

from toffee_test.utils.code_coverage.vcs import _query_picker_helper


def test_native_helper_without_xcomm(monkeypatch):
    """Preserve native metric totals and pass names as individual arguments."""
    def run(command, **options):
        assert command[:3] == ["/tmp/helper path", "--database", "/tmp/design.vdb"]
        assert command[-2:] == ["--test", "case with spaces"]
        assert options["timeout"] == 120
        report = {"schema_version": 1, "success": True, "simulator": "vcs",
                  "query": {"tests": ["case with spaces"]}, "items": [],
                  "metrics": {"line": {"covered": 2, "total": 2, "uncovered": 0, "rate": 100}}}
        return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

    monkeypatch.setattr(subprocess, "run", run)
    report = _query_picker_helper("/tmp/helper path", "/tmp/design.vdb", ["case with spaces"])
    assert report["summary"]["metrics"]["line"]["covered"] == 2


@pytest.mark.parametrize("output", ["not json", '{"schema_version": 5}',
                                   '{"schema_version": 1, "success": false}'])
def test_invalid_helper_report_is_rejected(monkeypatch, output):
    """Never turn malformed or unsuccessful reports into coverage success."""
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 0, output, ""))
    with pytest.raises(RuntimeError):
        _query_picker_helper("helper", "design.vdb", [])


def test_helper_failure_is_reported(monkeypatch):
    """Expose native helper failures rather than silently reporting zero gaps."""
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 1, "", "no usable testdata"))
    with pytest.raises(RuntimeError, match="no usable testdata"):
        _query_picker_helper("helper", "design.vdb", [])
