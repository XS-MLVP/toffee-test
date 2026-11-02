"""Simulator-aware reporting must record VDB metadata, never label it Verilator."""

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from toffee_test.reporter import set_line_coverage


def test_vcs_record_resolves_database_and_helper(tmp_path, monkeypatch):
    """The selected coverage filename becomes testdata, not the database path."""
    module = ModuleType("generated_coverage_dut")
    module.__file__ = str(tmp_path / "__init__.py")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    helper = tmp_path / "coverage" / "coverage"
    helper.parent.mkdir()
    helper.write_text("helper")
    database = tmp_path / "Adder.vdb"

    class Dut:
        """Model the generated Picker coverage metadata contract."""
        SIMULATOR = "vcs"
        __module__ = module.__name__

        def GetCoveragePath(self):
            """Return the compiled VCS database rather than SetCoverage's name."""
            return str(database)

    request = SimpleNamespace(node=SimpleNamespace(nodeid="test.py::test_add[0]"), scope="function")
    set_line_coverage(request, "/tmp/test_add[0].vdb", dut=Dut())
    record = json.loads(request.node.__line_coverage__)
    assert record["simulator"] == "vcs"
    assert record["database"] == str(database)
    assert record["helper"] == str(helper)
    assert record["test"] == "test_add_0_"


def test_old_vcs_dut_fails_closed():
    """Old exports must not silently become false Verilator coverage records."""
    request = SimpleNamespace(node=SimpleNamespace(), scope="function")
    with pytest.raises(RuntimeError, match="regenerated DUT"):
        set_line_coverage(request, "test.vdb", dut=SimpleNamespace(SIMULATOR="vcs"))
    assert not hasattr(request.node, "__line_coverage__")


def test_verilator_record_keeps_existing_path():
    """Legacy and explicit Verilator callers retain their existing behavior."""
    for dut in (None, SimpleNamespace(SIMULATOR="verilator")):
        request = SimpleNamespace(node=SimpleNamespace(), scope="function")
        set_line_coverage(request, "test.dat", ignore="Dut.ignore", dut=dut)
        record = json.loads(request.node.__line_coverage__)
        assert record["simulator"] == "verilator"
        assert record["database"] == "test.dat"
        assert record["ignore"] == ["Dut.ignore"]
