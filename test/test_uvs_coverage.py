"""Map uvcov JSON onto the shared report model, using a stand-in for the uvcov tool."""

import json
import re
import shlex
import stat
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from toffee_test.reporter import set_line_coverage
from toffee_test.simulators import get_backend
from toffee_test.utils.code_coverage import CoverageRecord, convert_case_coverage, convert_code_coverage
from toffee_test.utils.code_coverage.uvs import convert_uvs_preview_coverage


def _stmt(lines):
    un = sum(entry["un"] for entry in lines)
    cd = sum(entry["cd"] for entry in lines)
    return {"summary": {"total": {"score": 0, "un+cd": un + cd, "un": un, "cd": cd}},
            "detail": {"lines": [{"un+cd": entry["un"] + entry["cd"], **entry} for entry in lines]}}


CODE = {"instances": [
    {"id": 1, "name": "Demo_top", "stmt": _stmt([{"line": 3, "un": 1, "cd": 4}])},
    {"id": 2, "name": "uut",
     "stmt": _stmt([{"line": 5, "un": 0, "cd": 2}, {"line": 7, "un": 2, "cd": 1}, {"line": 8, "un": 1, "cd": 0}]),
     "cond": {"summary": {"total": {"un+cd": 3, "un": 1, "cd": 2}},
              "detail": {"exprs": [{"id": 1, "pid": 2, "line": 7, "expr": "go && en",
                                    "subexprs": ["go", "en"],
                                    "truth_table": {"01": 0, "10": 1, "11": 1}}]}},
     "fsm": {"summary": {"total": {"un+cd": 4, "un": 2, "cd": 2}},
             "detail": {"states": [{"name": "IDLE", "line": 5, "cd": 1}, {"name": "RUN", "line": 6, "cd": 0}],
                        "transitions": [{"name": "IDLE->RUN", "line": 7, "cd": 1},
                                        {"name": "RUN->IDLE", "line": 8, "cd": 0}]}},
     "tgl": {"summary": {"total": {"un+cd": 2, "un": 1, "cd": 1},
                         "total_bits": {"un+cd": 8, "un": 5, "cd": 3}},
             "detail": {"ports": [{"name": "clk", "cd": 2, "cd_1->0": 1, "cd_0->1": 1, "type": "input"},
                                  {"name": "rst", "cd": 1, "cd_1->0": 1, "cd_0->1": 0, "type": "input"}],
                        "signals": [{"name": "q", "cd": 0, "cd_1->0": 0, "cd_0->1": 0, "bits": [
                            {"name": "1:0", "bins": [{"name": "0->1", "cd": 0}, {"name": "1->0", "cd": 0}]},
                        ]}]}}},
    {"id": 3, "name": "s0", "stmt": _stmt([{"line": 2, "un": 0, "cd": 1}])},
    {"id": 4, "name": "s1", "stmt": _stmt([{"line": 2, "un": 1, "cd": 0}])},
]}
SUMMARY = {
    "modules": [{"id": 6, "name": "Demo_top"}, {"id": 7, "name": "Demo"}, {"id": 8, "name": "sub"}],
    "instances": [{"id": 1, "pid": -1, "name": "Demo_top", "mid": 6},
                  {"id": 2, "pid": 1, "name": "uut", "mid": 7},
                  {"id": 3, "pid": 2, "name": "s0", "mid": 8},
                  {"id": 4, "pid": 2, "name": "s1", "mid": 8}],
    "summary": {"summaryData": {"stmt": {"score": 75.0, "un+cd": 12, "un": 3, "cd": 9}}},
}


@pytest.fixture
def design(tmp_path, monkeypatch):
    """Build a compile database next to its sources and a stand-in uvcov executable."""
    package = tmp_path / "Demo"
    database = package / "Demo.cdb"
    database.mkdir(parents=True)
    (package / "Demo.v").write_text("// module not_this\nmodule Demo (\n);\nendmodule\n")
    (package / "Demo_top.sv").write_text("module Demo_top;\nendmodule\n")
    rtl = tmp_path / "Demo_RTL"
    rtl.mkdir()
    (rtl / "sub.v").write_text("  module sub;\nendmodule\n")
    compile_path = package / "build"
    entries = [
        {"fid": 1, "file_name": "/uvs/std3.v", "file_path": "/uvs/std3.v", "file_source": 1, "type": 0},
        {"fid": 2, "file_name": "Demo.v", "file_path": str(compile_path / "Demo.v"), "file_source": 0, "type": 0},
        {"fid": 3, "file_name": "Demo_top.sv", "file_path": str(compile_path / "Demo_top.sv"), "file_source": 0, "type": 0},
        {"fid": 4, "file_name": "sub.v", "file_path": str(rtl / "sub.v"), "file_source": 0, "type": 0},
    ]
    (database / "file_info.json").write_bytes(zlib.compress(json.dumps(
        {"compile_path": str(compile_path), "file_ids": entries}).encode()))
    for name in ("a.cdb", "b.cdb"):
        (tmp_path / "runs" / name / "data" / "Demo").mkdir(parents=True)

    source = tmp_path / "uvcov_data"
    source.mkdir()
    (source / "code_coverage.json").write_text(json.dumps(CODE))
    (source / "coverage.json").write_text(json.dumps(SUMMARY))
    calls = tmp_path / "uvcov_calls"
    tool = tmp_path / "uvcov"
    tool.write_text(f"""#!{sys.executable}
import shutil, sys
from pathlib import Path
open({str(calls)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
report = Path(sys.argv[sys.argv.index("-reportdir") + 1])
(report / "data").mkdir(parents=True)
(report / "dashboard.html").write_text("<html></html>")
for name in ("code_coverage.json", "coverage.json"):
    shutil.copy(Path({str(source)!r}) / name, report / "data" / name)
if "-mdb" in sys.argv:
    merged = Path(sys.argv[sys.argv.index("-mdb") + 1])
    merged.mkdir()
    (merged / "design.dat").write_text("merged compile database")
    (merged / "inputs.txt").write_text("\\n".join(sys.argv[sys.argv.index("-db") + 1:sys.argv.index("-detail")]))
""")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=":")
    monkeypatch.delenv("UVS_HOME", raising=False)
    return SimpleNamespace(root=tmp_path, package=package, database=database, calls=calls)


def _record(design, *names, ignore=()):
    return [CoverageRecord(simulator="uvs", database=str(design.database),
                           test=str(design.root / "runs" / name), ignore=list(ignore),
                           nodeid=f"test.py::{name}")
            for name in names]


def test_session_writes_the_vcs_layout(design):
    output = design.root / "report" / "coverage_data"
    result = convert_code_coverage(_record(design, "a.cdb", "b.cdb"), output)

    assert result.simulator == "uvs"
    assert result.summary_file == "coverage_data/cumulative.json"
    assert result.detail == "coverage_data/native/uvs_uvcov/dashboard.html"
    for name in ("cumulative.json", "catalog.json", "catalog/atoms.jsonl", "catalog/groups.jsonl",
                 "catalog/sources.jsonl", "native/uvs_uvcov/dashboard.html"):
        assert (output / name).is_file(), name
    call = design.calls.read_text().split()
    assert call[:4] == ["-db", str(design.database), str(design.root / "runs/a.cdb"),
                        str(design.root / "runs/b.cdb")]
    assert "-detail" in call

    # The wrapper's statements stay out; the two instances of sub add up.
    assert (result.metrics["line"].total, result.metrics["line"].uncovered) == (8, 4)
    assert (result.metrics["condition"].total, result.metrics["condition"].uncovered) == (3, 1)
    assert (result.metrics["fsm"].total, result.metrics["fsm"].uncovered) == (4, 2)
    # Toggle counts single-bit transitions, matching the expanded items.
    assert (result.metrics["toggle"].total, result.metrics["toggle"].uncovered) == (8, 5)
    assert "branch" not in result.metrics
    assert result.report_metrics["line"].total == 12
    assert json.loads((output / "cumulative.json").read_text())["simulator"] == "uvs"


def test_relative_output_directory(design, monkeypatch):
    monkeypatch.chdir(design.root)
    result = convert_code_coverage(_record(design, "a.cdb"), Path("reports/coverage_data"))
    assert result.metrics["line"].total == 8
    assert (design.root / "reports/coverage_data/native/uvs_uvcov/dashboard.html").is_file()


def test_uvd_opens_compile_and_all_selected_run_databases(design):
    from toffee_test.utils.code_coverage import CoverageReportContext

    output = design.root / "report" / "coverage_data"
    result = convert_code_coverage(_record(design, "a.cdb", "b.cdb"), output)
    context = CoverageReportContext(design.root, output.parent)
    assert all(detail['kind'] == 'html' for detail in result.to_report_dict()['details'])
    report = result.to_report_dict(context=context, coverage_gui='xpra')
    application = next(detail for detail in report['details'] if detail['kind'] == 'application')
    page = (output.parent / application['href']).read_text()
    request = json.loads(re.search(r'const coverageRequest = (.*);', page).group(1))
    argv = shlex.split(request['command'])
    assert argv == ['uvd', '-geometry', '1500x740+0+0', '-ucdb', str(output / 'merged.cdb')]
    assert (output / 'merged.cdb/inputs.txt').read_text().splitlines() == [
        str(design.database), str(design.root / 'runs/a.cdb'), str(design.root / 'runs/b.cdb'),
    ]
    assert request['cwd'] == str(output)
    assert request['mode'] == 'gui'


def test_uvd_launch_quotes_database_paths(tmp_path):
    from toffee_test.utils.code_coverage import CodeCoverageResult
    from toffee_test.utils.code_coverage.uvs import UvsCoverageProvider

    databases = [str(tmp_path / "merged with spaces '$(x)'.cdb")]
    (tmp_path / 'coverage_data').mkdir()
    result = CodeCoverageResult('uvs', databases=databases)
    details = UvsCoverageProvider().build_details(result, tmp_path / 'coverage_data')
    page = (tmp_path / details[0].href).read_text()
    request = json.loads(re.search(r'const coverageRequest = (.*);', page).group(1))
    argv = shlex.split(request['command'])
    assert argv[3:5] == ['-ucdb', databases[0]]
    assert len(argv) == 5


def test_uvd_page_failure_preserves_coverage(tmp_path, monkeypatch):
    from toffee_test.utils.code_coverage import CodeCoverageResult, HtmlDetail
    from toffee_test.utils.code_coverage import uvs

    def fail(*args, **kwargs):
        raise OSError('Cannot write application page')

    monkeypatch.setattr(uvs, 'write_xpra_link', fail)
    result = CodeCoverageResult('uvs', detail='native/dashboard.html', databases=['dut.cdb'])
    assert uvs.UvsCoverageProvider().build_details(result, tmp_path) == [HtmlDetail('native/dashboard.html')]
    assert 'Cannot write application page' in result.warnings[0]


def test_large_regression_fits_xpra_command_limit(design):
    names = [f'long_case_name_{index}.cdb' for index in range(200)]
    for name in names:
        (design.root / 'runs' / name).mkdir()
    records = _record(design, *names)
    assert len(shlex.join(['uvd', '-ucdb', str(design.database), *[r.test for r in records]])) > 16384
    output = design.root / 'report/coverage_data'
    result = convert_code_coverage(records, output)
    application = next(detail for detail in result.details if hasattr(detail, 'href'))
    page = (output.parent / application.href).read_text()
    request = json.loads(re.search(r'const coverageRequest = (.*);', page).group(1))
    assert len(request['command']) < 16384
    merged_inputs = (output / 'merged.cdb/inputs.txt').read_text().splitlines()
    assert merged_inputs[0] == str(design.database)
    assert set(merged_inputs[1:]) == {record.test for record in records}


def test_every_uncovered_target_becomes_one_atom(design):
    output = design.root / "report" / "coverage_data"
    convert_code_coverage(_record(design, "a.cdb"), output)
    catalog = json.loads((output / "catalog.json").read_text())
    assert catalog["reconciliation"]["unresolved"] == []
    assert catalog["reconciliation"]["duplicate_identities"] == []
    assert catalog["reconciliation"]["enumerated_atom_count"] == 4 + 1 + 2 + 5


def test_ucagent_checker_reads_the_line_summary(design):
    """Follow the fields UCAgent's parse_un_coverage_json reads."""
    output = design.root / "report" / "coverage_data"
    convert_code_coverage(_record(design, "a.cdb"), output)
    data = json.loads((design.root / "report" / "line_dat" / "code_coverage.json").read_text())
    assert data["overview"]["total"]["line"] == 8
    assert data["overview"]["miss"]["line"] == 4
    uncovered = data["uncovered"]["data"]
    assert set(uncovered) == {str(design.package / "Demo.v"), str(design.root / "Demo_RTL/sub.v")}
    demo = uncovered[str(design.package / "Demo.v")]
    assert demo["total"]["line"] == 6
    assert demo["modules"]["Demo"]["miss"]["line"] == 3
    assert demo["modules"]["Demo"]["line"] == ["7-8"]
    assert demo["modules"]["Demo"]["fsm"][0]["states"] == ["RUN"]
    assert uncovered[str(design.root / "Demo_RTL/sub.v")]["modules"]["sub"]["line"] == ["2-2"]


def test_toggle_vectors_expand_to_bits(design):
    output = design.root / "report" / "coverage_data"
    convert_code_coverage(_record(design, "a.cdb"), output)
    data = json.loads((design.root / "report" / "line_dat" / "code_coverage.json").read_text())
    toggles = {item["object"]: item
               for item in data["uncovered"]["data"][str(design.package / "Demo.v")]["modules"]["Demo"]["toggle"]}
    assert toggles["q"]["bits"] == {"0->1": ["0-1"], "1->0": ["0-1"]}
    assert toggles["rst"]["transitions"] == ["0->1"]


def test_ignore_rules_remove_files_and_lines(design):
    output = design.root / "report" / "coverage_data"
    result = convert_code_coverage(
        _record(design, "a.cdb", ignore=["*/Demo_RTL/sub.v", "*/Demo/Demo.v:8"]), output
    )
    # sub.v (2 statements, 1 missed) goes away entirely, then one uncovered line.
    assert (result.metrics["line"].total, result.metrics["line"].uncovered) == (5, 2)
    data = json.loads((design.root / "report" / "line_dat" / "code_coverage.json").read_text())
    assert list(data["uncovered"]["data"]) == [str(design.package / "Demo.v")]
    assert data["uncovered"]["data"][str(design.package / "Demo.v")]["modules"]["Demo"]["line"] == ["7-7"]


def test_case_report_queries_only_its_own_run(design):
    output = design.root / "report" / "coverage_data" / "cases" / "abc"
    record = _record(design, "a.cdb")[0]
    result = convert_case_coverage(record, output)
    assert result.metrics["line"].total == 8
    assert Path(result.summary_file) == output / "observation.json"
    report = json.loads((output / "observation.json").read_text())
    assert report["scope"]["testdata"] == record.test
    assert report["simulator"] == "uvs"
    assert "atom_inventory" not in report
    assert design.calls.read_text().split()[:3] == ["-db", str(design.database), record.test]
    assert sorted(path.name for path in output.iterdir()) == ["observation.json"]


def test_preview_publishes_the_catalog_without_gaps(design):
    output = design.root / "preview"
    convert_uvs_preview_coverage(_record(design, "a.cdb"), output)
    report = json.loads((output / "cumulative.json").read_text())
    assert report["metadata"]["preview"] is True
    assert (output / "catalog.json").is_file()


def test_missing_run_is_a_warning_until_nothing_is_left(design):
    output = design.root / "report" / "coverage_data"
    result = convert_code_coverage(_record(design, "a.cdb", "missing.cdb"), output)
    assert len(result.warnings) == 1 and "missing.cdb" in result.warnings[0]
    assert str(design.root / "runs/missing.cdb") not in design.calls.read_text()
    with pytest.raises(ValueError, match="Finish"):
        convert_code_coverage(_record(design, "missing.cdb"), output)


def test_invalid_compile_database_is_rejected(design):
    """A run database passed by mistake has no file_info.json."""
    record = CoverageRecord(simulator="uvs", database=str(design.root / "runs" / "a.cdb"),
                            test=str(design.root / "runs" / "a.cdb"))
    with pytest.raises(ValueError, match="compile database"):
        convert_code_coverage([record], design.root / "report" / "coverage_data")


def test_relative_run_path_is_passed_to_uvcov_as_absolute(design, monkeypatch):
    monkeypatch.chdir(design.root)
    record = CoverageRecord(simulator="uvs", database=str(design.database), test="runs/a.cdb")
    convert_code_coverage([record], design.root / "report" / "coverage_data")
    assert str(design.root / "runs/a.cdb") in design.calls.read_text().split()


def test_empty_uvcov_report_is_an_error(design):
    (design.root / "uvcov_data" / "code_coverage.json").write_text(json.dumps(
        {"instances": [{"id": 2, "name": "uut"}]}))
    with pytest.raises(RuntimeError, match="no coverage targets"):
        convert_code_coverage(_record(design, "a.cdb"), design.root / "report" / "coverage_data")


def test_uvcov_failure_is_reported(design):
    (design.root / "uvcov").write_text(f"#!{sys.executable}\nimport sys\nprint('bad database'); sys.exit(3)\n")
    with pytest.raises(RuntimeError, match="bad database"):
        convert_code_coverage(_record(design, "a.cdb"), design.root / "report" / "coverage_data")


def test_record_uses_the_compile_database_and_the_run_path(tmp_path):
    class Dut:
        SIMULATOR = "uvs"

        def GetCoveragePath(self):
            return str(tmp_path / "Demo.cdb")

    request = SimpleNamespace(node=SimpleNamespace(nodeid="test.py::test_add[0]"), scope="function")
    run = str(tmp_path / "data" / "test_add[0].cdb")
    set_line_coverage(request, run, dut=Dut())
    record = json.loads(request.node.__line_coverage__)
    assert record["simulator"] == "uvs"
    assert record["database"] == str(tmp_path / "Demo.cdb")
    assert record["test"] == run
    assert record["datfile"] == run


def test_dut_without_coverage_database_fails_closed():
    request = SimpleNamespace(node=SimpleNamespace(), scope="function")
    dut = SimpleNamespace(SIMULATOR="uvs", GetCoveragePath=lambda: "")
    with pytest.raises(RuntimeError, match="picker -c"):
        set_line_coverage(request, "test.cdb", dut=dut)


def test_backend_runs_each_test_in_its_own_process():
    backend = get_backend("uvs")
    assert backend.forked_execution
    assert backend.coverage_name("case") == "case.cdb"
