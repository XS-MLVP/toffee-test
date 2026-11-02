"""Check simulator isolation through pytest's real reporting lifecycle."""

import json
import os
from pathlib import Path

import pytest

from toffee_test.simulators import get_backend


@pytest.mark.skipif(not hasattr(os, "fork"), reason="VCS isolation requires POSIX fork")
@pytest.mark.parametrize("simulator", ["vcs", "verilator"])
@pytest.mark.parametrize("workers", [0, 2])
def test_simulator_lifecycle_keeps_report_cases(pytester, monkeypatch, simulator, workers):
    root = Path(__file__).parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")])))
    pytester.makepyfile(f'''
import os
import pytest
from toffee_test.simulators import get_backend
from toffee.funcov import CovGroup

PARENT_PID = os.getpid()

class FakeDut:
    SIMULATOR = {simulator!r}
    def __init__(self, **kwargs):
        assert (os.getpid() != PARENT_PID) == get_backend(self.SIMULATOR).forked_execution
    def GetWaveFormat(self):
        return ""
    def GetCovMetrics(self):
        return 0
    def Finish(self):
        assert (os.getpid() != PARENT_PID) == get_backend(self.SIMULATOR).forked_execution

@pytest.fixture
def dut(toffee_request):
    device = toffee_request.create_dut(FakeDut)
    toffee_request.add_cov_groups(CovGroup("fixture_group"), periodic_sample=False)
    return device

@pytest.mark.parametrize("value", [0, 1])
def test_device(dut, value):
    assert dut.SIMULATOR == {simulator!r}
''')
    args = ["-q", "--toffee-report", "--report-dump-json", "--report-dir=reports", "--report-name=index.html"]
    if workers:
        args += ["-n", str(workers)]
    result = pytester.runpytest_subprocess(*args)
    result.assert_outcomes(passed=2)
    report = json.loads((pytester.path / "reports" / "toffee_report.json").read_text())
    assert len(report["tests"]) == 2
    assert all(len(test["phases"]) == 3 for test in report["tests"])
    assert all(test["status"]["category"] == "passed" for test in report["tests"])
    assert all(test["ended"] >= test["started"] for test in report["tests"])
    assert report["coverages"]["functional"]["group_num_total"] == 1
    assert (pytester.path / "reports" / "index.html").is_file()


def test_verilator_coverage_path_without_native_metadata():
    """Picker exposes GetCoveragePath only on its VCS Python wrapper."""
    assert get_backend("verilator").coverage_path(object(), "case.dat") == "case.dat"
    with pytest.raises(RuntimeError, match="GetCoveragePath"):
        get_backend("vcs").coverage_path(object(), "case")
