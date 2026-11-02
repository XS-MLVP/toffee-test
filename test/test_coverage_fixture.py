"""Legacy coverage calls resolve the active fixture without simulator-specific templates."""

import json
from types import SimpleNamespace

import pytest

from toffee_test.reporter import set_line_coverage


@pytest.mark.parametrize("scope", ["function", "module", "session"])
@pytest.mark.parametrize("simulator", ["vcs", "verilator"])
def test_original_fixture_teardown_order(pytester, scope, simulator):
    """Real pytest requests expose the yielded DUT before the existing Finish call."""
    pytester.makepyfile(f'''
import json
from pathlib import Path
import pytest
from toffee_test.reporter import set_line_coverage

class Dut:
    """Model generated metadata without finalizing native output early."""
    SIMULATOR = {simulator!r}
    finished = False
    def GetCoveragePath(self):
        """Return the native database, not the per-test selected name."""
        assert not self.finished
        return str(Path(__file__).parent / "native.vdb")
    def GetCovMetrics(self):
        """Enable native coverage."""
        return 1
    def Finish(self):
        """Finalize only when the original template reaches its final call."""
        assert not self.finished
        self.finished = True

@pytest.fixture(scope={scope!r})
def device(request):
    """Retain the original three-argument coverage API and teardown order."""
    dut = Dut()
    helper = Path(__file__).parent / "coverage" / "coverage"
    helper.parent.mkdir(exist_ok=True)
    helper.write_text("helper")
    yield dut
    selected = "case[1].vdb" if Dut.SIMULATOR == "vcs" else "case.dat"
    set_line_coverage(request, selected, ignore=["Dut.ignore"])
    record = json.loads(request.node.__line_coverage__)
    assert record["simulator"] == Dut.SIMULATOR
    assert not dut.finished
    if Dut.SIMULATOR == "vcs":
        assert record["database"] == str(Path(__file__).parent / "native.vdb")
        assert record["test"] == "case_1_"
        assert record["helper"] == str(helper)
    else:
        assert record["database"] == selected
    dut.Finish()
    assert dut.finished

def test_device(device):
    """Exercise a yielded fixture so pytest controls cached results and teardown."""
    assert not device.finished
''')
    result = pytester.runpytest("-q", "-p", "no:toffee-test")
    result.assert_outcomes(passed=1)


def test_inference_never_searches_unrelated_fixtures():
    """Lookup is constrained to the fixture making the reporting call."""
    calls = []

    def get_fixture(name):
        """An unrelated fixture must not be used as a DUT candidate."""
        calls.append(name)
        return object()

    request = SimpleNamespace(node=SimpleNamespace(), scope="function",
                              fixturename="reporting", getfixturevalue=get_fixture)
    with pytest.raises(RuntimeError, match="current yielded DUT"):
        set_line_coverage(request, "case.vdb")
    assert calls == ["reporting"]
    assert not hasattr(request.node, "__line_coverage__")


@pytest.mark.parametrize("metrics", [None, 0])
def test_mock_or_disabled_coverage_is_not_fabricated(metrics):
    """Synthetic fixtures never create a misleading Verilator coverage record."""
    dut = SimpleNamespace(SIMULATOR="vcs", GetCovMetrics=lambda: metrics)
    request = SimpleNamespace(node=SimpleNamespace(), scope="function", fixturename="dut",
                              getfixturevalue=lambda name: dut)
    set_line_coverage(request, "case.vdb")
    assert not hasattr(request.node, "__line_coverage__")


def test_explicit_dut_none_disables_inference():
    """Existing explicit callers retain control over raw coverage registration."""
    request = SimpleNamespace(node=SimpleNamespace(), scope="function", fixturename="dut",
                              getfixturevalue=lambda name: pytest.fail("Unexpected fixture lookup"))
    set_line_coverage(request, "case.dat", dut=None)
    assert json.loads(request.node.__line_coverage__)["simulator"] == "verilator"


def test_unavailable_vcs_context_fails_before_registration():
    """A suffix is not sufficient to invent a native database or simulator record."""
    request = SimpleNamespace(node=SimpleNamespace(), scope="function")
    with pytest.raises(RuntimeError, match="explicit dut"):
        set_line_coverage(request, "case.vdb")
    assert not hasattr(request.node, "__line_coverage__")


def test_old_auto_discovered_dut_fails_closed():
    """Automatic discovery must retain the native metadata validation contract."""
    dut = SimpleNamespace(SIMULATOR="vcs")
    request = SimpleNamespace(node=SimpleNamespace(), scope="function", fixturename="dut",
                              getfixturevalue=lambda name: dut)
    with pytest.raises(RuntimeError, match="regenerated DUT"):
        set_line_coverage(request, "case.vdb")
    assert not hasattr(request.node, "__line_coverage__")


def test_explicit_dut_takes_precedence():
    """Callers outside their DUT fixture can select a device without unrelated lookup."""
    request = SimpleNamespace(node=SimpleNamespace(), scope="function", fixturename="other",
                              getfixturevalue=lambda name: pytest.fail("Unexpected fixture lookup"))
    set_line_coverage(request, "case.dat", dut=SimpleNamespace(SIMULATOR="verilator"))
    assert json.loads(request.node.__line_coverage__)["simulator"] == "verilator"
