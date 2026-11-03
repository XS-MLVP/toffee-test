"""Opt-in acceptance of unchanged yield fixtures with a real compiled Adder."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(not os.environ.get("TOFFEE_NATIVE_DUT_DIR"), reason="requires a compiled Adder and simulator runtime")
def test_native_legacy_fixture(tmp_path):
    """Use a relocated DUT copy and finalize after the old three-argument reporter call."""
    source = Path(os.environ["TOFFEE_NATIVE_DUT_DIR"]).resolve()
    shutil.copytree(source, tmp_path / "Adder")
    test = tmp_path / "test_native.py"
    test.write_text('''
import json
from pathlib import Path
import pytest
from Adder import DUTAdder
from toffee_test.reporter import set_line_coverage
from toffee_test.utils.code_coverage import convert_case_coverage

@pytest.fixture
def dut(request):
    """Keep the original fixture's reporter and Finish ordering."""
    instance = DUTAdder()
    simulator = instance.GetSimulator()
    assert instance.GetCovMetrics() != 0
    path = str(Path("case.vdb" if simulator == "vcs" else "case.dat").resolve())
    instance.SetCoverage(path)
    instance.SetWaveform(str(Path("case.fsdb" if simulator == "vcs" else "case.fst").resolve()))
    yield instance
    set_line_coverage(request, path, ignore=[])
    record = json.loads(request.node.__line_coverage__)
    assert record["simulator"] == simulator
    if simulator == "vcs":
        database = Path(instance.GetCoveragePath()).resolve()
        database.relative_to(Path.cwd())
        assert record["database"] == str(database)
        assert record["test"] == "case"
    else:
        assert record["database"] == path
    instance.Finish()
    if simulator == "vcs":
        assert (database / "snps/coverage/db/testdata/case/line.verilog.data.xml").is_file()
    else:
        assert Path(path).stat().st_size > 0
    result = convert_case_coverage(record, Path("coverage_result"))
    assert not result.error
    assert result.simulator == simulator
    assert any(metric.available and metric.total > 0 for metric in result.metrics.values())
    assert all(0 <= metric.covered <= metric.total for metric in result.metrics.values())
    summary = json.loads(Path(result.summary_file).read_text())
    assert summary["scope"]["type"] == "testcase"
    if simulator == "vcs":
        assert summary["scope"]["testdata"] == "case"
    Path("finished.json").write_text(json.dumps(record))

def test_add(dut):
    """Drive a known-correct low-width transaction on the archived example."""
    dut.a.value, dut.b.value, dut.cin.value = 1, 2, 0
    dut.Step(2)
    assert dut.sum.value == 3
''', encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(Path(__file__).parents[1]), environment.get("PYTHONPATH", "")])
    if (tmp_path / "Adder/libDPIAdder.so").is_file():
        environment["LD_PRELOAD"] = str(tmp_path / "Adder/libUTAdder.so")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(test)], cwd=tmp_path,
                            env=environment, text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "finished.json").is_file()
