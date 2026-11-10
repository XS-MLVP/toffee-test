"""Check simulator isolation through pytest's real reporting lifecycle."""

import json
import os
from pathlib import Path

import pytest

from toffee_test.simulators import get_backend


@pytest.mark.skipif(not hasattr(os, "fork"), reason="VCS isolation requires POSIX fork")
@pytest.mark.parametrize("simulator", ["uvs", "vcs", "verilator"])
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


@pytest.mark.skipif(not hasattr(os, "fork"), reason="VCS isolation requires POSIX fork")
@pytest.mark.parametrize("simulator", ["uvs", "vcs", "verilator"])
@pytest.mark.parametrize("workers", [0, 2])
def test_factory_fixture_preserves_existing_report_setters(pytester, monkeypatch, simulator, workers):
    """Existing yielded DUT factories need neither new fixtures nor markers."""
    root = Path(__file__).parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")])))
    pytester.makepyfile(device=f'''
import os
from toffee_test.reporter import set_func_coverage, set_title_info, set_user_info
from toffee.funcov import CovGroup, Eq

PARENT = os.getpid()
class DUT:
    SIMULATOR = {simulator!r}
    def __init__(self):
        assert (os.getpid() != PARENT) == {simulator in ('uvs', 'vcs')!r}
    def Finish(self):
        assert (os.getpid() != PARENT) == {simulator in ('uvs', 'vcs')!r}

def create_dut():
    return nested_factory()

def nested_factory():
    return DUT()
''')
    pytester.makeconftest('''
import pytest
import device

@pytest.fixture
def dut(request):
    instance = device.create_dut()
    group = device.CovGroup("FG-ADDER")
    group.add_cover_point(1, {"CK-ADD": device.Eq(1)}, name="FC-ADD")
    instance.fc_cover = {"FG-ADDER": group}
    yield instance
    device.set_func_coverage(request, group)
    device.set_title_info("Existing UCAgent fixture")
    device.set_user_info("UCAgent", "test@example.invalid")
    instance.Finish()

@pytest.fixture
def env(dut):
    return dut
''')
    pytester.makepyfile('''
import pytest

@pytest.mark.parametrize("index", [0, 1])
def test_add(env, index):
    group = env.fc_cover["FG-ADDER"]
    group.mark_function("FC-ADD", test_add, ["CK-ADD"])
    group.sample()
''')
    args = ["-q", "--toffee-report", "--report-dump-json", "--report-dir=reports", "--report-name=index.html"]
    if workers:
        args += ["-n", str(workers)]
    result = pytester.runpytest_subprocess(*args)
    result.assert_outcomes(passed=2)
    report = json.loads((pytester.path / "reports/toffee_report.json").read_text())
    assert len(report["tests"]) == 2
    assert report["title"] == "Existing UCAgent fixture"
    assert report["user"]["name"] == "UCAgent"
    assert report["coverages"]["functional"]["bin_num_hints"] == 1
    assert report["coverages"]["functional"]["groups"][0]["point_functions"]


def test_factory_detection_supports_closures_and_recursive_helpers():
    """Inspection terminates even if a referenced helper graph contains cycles."""
    from types import SimpleNamespace
    from toffee_test.plugin import _fixture_declares_forked_dut

    class DUT:
        SIMULATOR = "vcs"

    def factory():
        factory()
        return DUT()

    def fixture():
        return factory()

    item = SimpleNamespace(
        fixturenames=["dut"],
        _fixtureinfo=SimpleNamespace(name2fixturedefs={"dut": [SimpleNamespace(func=fixture)]}),
    )
    assert _fixture_declares_forked_dut(item)


def test_checker_line_summary_does_not_require_new_path_or_schema(tmp_path):
    """Both providers export the ordinary overview/uncovered JSON beside native data."""
    from toffee_test.utils.code_coverage.common import write_line_coverage_summary

    summary = {"overview": {"total": {"line": 5}, "miss": {"line": 1}}, "uncovered": {"data": {}}}
    write_line_coverage_summary(summary, tmp_path / "coverage_data")
    assert json.loads((tmp_path / "line_dat/code_coverage.json").read_text()) == summary


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
def test_fork_timeout_kills_a_child_ignoring_sigterm(pytester, monkeypatch):
    """A testcase timeout must remain bounded even if native code ignores TERM."""
    root = Path(__file__).parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")])))
    pytester.makepyfile('''
import signal
import time
import pytest
class DUT:
    SIMULATOR = "vcs"
@pytest.fixture
def dut():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    return DUT()
def test_block(dut):
    time.sleep(30)
''')
    result = pytester.runpytest_subprocess("-q", "--toffee-forked-timeout=0.5", timeout=10)
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*timed out after 0.5s*"])


@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="requires Linux parent-death signal")
@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGKILL"])
def test_fork_child_exits_when_pytest_controller_is_killed(tmp_path, signal_name):
    """External test runners can stop pytest without leaving a testcase alive."""
    import signal
    import subprocess
    import sys
    import time

    root = Path(__file__).parents[1]
    test = tmp_path / "test_parent_death.py"
    test.write_text('''
import os
import time
from pathlib import Path
import pytest
class DUT:
    SIMULATOR = "vcs"
@pytest.fixture
def dut():
    Path(__file__).with_name("child.pid").write_text(str(os.getpid()))
    return DUT()
def test_block(dut):
    time.sleep(30)
''')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(root), os.environ.get("PYTHONPATH")])))
    worker = subprocess.Popen([sys.executable, "-m", "pytest", "-q", str(test)],
                              cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = None
    try:
        deadline = time.monotonic() + 10
        pid_file = tmp_path / "child.pid"
        while not pid_file.exists() and time.monotonic() < deadline and worker.poll() is None:
            time.sleep(0.02)
        assert pid_file.exists(), "testcase fixture never started"
        child = int(pid_file.read_text())
        worker.send_signal(getattr(signal, signal_name))
        worker.communicate(timeout=3)
        assert worker.returncode == -getattr(signal, signal_name)
        deadline = time.monotonic() + 1
        while True:
            try:
                state = Path(f"/proc/{child}/stat").read_text().split()[2]
            except (FileNotFoundError, ProcessLookupError):
                state = "X"
            if state in {"X", "Z"} or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert state in {"X", "Z"}
    finally:
        if worker.poll() is None:
            worker.kill()
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        worker.communicate(timeout=5)
