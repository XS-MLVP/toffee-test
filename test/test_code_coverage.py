import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from toffee_test.utils.code_coverage import (
    CoverageRecord,
    convert_case_coverage,
    convert_code_coverage,
    normalize_vcs_testdata_name,
)
from toffee_test.utils.code_coverage.common import load_provider_summary
from toffee_test.utils.code_coverage.vcs import convert_vcs_preview_coverage, find_urg_entry


class _Vector(list):
    def push_back(self, value):
        self.append(value)


def _fake_item(value):
    defaults = {
        "module": "", "instance": "", "object": "", "covered": 0,
        "coverable": 1, "count": 0, "status": 0, "detail": "",
        "atom_kind": "", "native_id": "", "native_name": "",
        "from_state": "", "to_state": "", "counts_toward_rate": True,
    }
    data = {**defaults, **value}
    data["location"] = SimpleNamespace(
        file=str(value.get("file", "")),
        line=int(value.get("line", 0)),
        column=int(value.get("column", 0)),
    )
    data["kind"] = str(value.get("kind", ""))
    return SimpleNamespace(**data)


@pytest.fixture(autouse=True)
def fake_xspcomm(monkeypatch):
    class CoverageQuery:
        def __init__(self):
            self.kinds = _Vector()
            self.tests = _Vector()

    class CoverageProviderInfo:
        def __init__(self):
            self.simulator = ""
            self.database = ""
            self.executable = ""

    class CoverageClient:
        def __init__(self, provider):
            self.provider = provider
            self.database = provider.database
            self.helper = provider.executable

        def Query(self, query):
            command = [self.helper, "--database", self.database]
            for kind in query.kinds:
                command.extend(("--kind", str(kind)))
            for test in query.tests:
                command.extend(("--test", str(test)))
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            if completed.returncode:
                return SimpleNamespace(success=False, errors=[completed.stderr], schema_version=0)
            raw = json.loads(completed.stdout)
            query_raw = raw.get("query") or {}
            metrics = [
                SimpleNamespace(kind=name, **value)
                for name, value in (raw.get("metrics") or (raw.get("summary") or {}).get("metrics") or {}).items()
            ]
            return SimpleNamespace(
                schema_version=int(raw.get("schema_version", 0)),
                success=bool(raw.get("success", True)),
                simulator=str(raw.get("simulator", "vcs")),
                query=SimpleNamespace(tests=_Vector(query_raw.get("tests") or list(query.tests))),
                metrics=metrics,
                items=_Vector(_fake_item(item) for item in raw.get("items") or []),
                errors=_Vector(raw.get("errors") or []),
            )

        def LastError(self):
            return "fake picker client failed"

    module = SimpleNamespace(
        CoverageClient=CoverageClient,
        CoverageProviderInfo=CoverageProviderInfo,
        CoverageQuery=CoverageQuery,
        CoverageKindFromString=lambda value: str(value),
        CoverageKindName=lambda value: str(value),
    )
    monkeypatch.setitem(sys.modules, "xspcomm", module)


def write_summary(path: Path, simulator: str, covered: int, total: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 5,
                "simulator": simulator,
                "summary": {
                    "metrics": {
                        "line": {
                            "covered": covered,
                            "total": total,
                            "uncovered": total - covered,
                            "rate": covered / total * 100 if total else 0,
                            "available": True,
                            "details_available": True,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def make_fake_urg(path: Path, entry: str = "dashboard.html") -> Path:
    script = path / "urg"
    script.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

report = pathlib.Path(sys.argv[sys.argv.index('-report') + 1])
report.mkdir(parents=True, exist_ok=True)
(report / %r).write_text('<html>URG</html>', encoding='utf-8')
print('Load Failed: source annotation warning only', file=sys.stderr)
""" % entry,
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def make_fake_helper(path: Path, *, include_extended: bool = False) -> Path:
    script = path / "coverage"
    report = {
        "schema_version": 1,
        "success": True,
        "simulator": "vcs",
        "query": {"kinds": ["line", "toggle", "branch"]},
        "design_revision": "design-test",
        "inventory_revision": "inventory-test",
        "metrics": {
                "line": {"covered": 2, "total": 5, "uncovered": 3, "rate": 40.0, "available": True},
                "toggle": {"covered": 1, "total": 3, "uncovered": 2, "rate": 100 / 3, "available": True},
                "branch": {"covered": 1, "total": 2, "uncovered": 1, "rate": 50.0, "available": True},
        },
        "breakdown": {
            "files": {
                "/rtl/top.v": {
                    "metrics": {
                        "line": {"covered": 2, "total": 5, "uncovered": 3, "rate": 40.0},
                        "toggle": {"covered": 1, "total": 3, "uncovered": 2, "rate": 100 / 3},
                        "branch": {"covered": 1, "total": 2, "uncovered": 1, "rate": 50.0},
                    },
                    "modules": {
                        "Top": {
                            "metrics": {
                                "line": {"covered": 2, "total": 5, "uncovered": 3, "rate": 40.0},
                                "toggle": {"covered": 1, "total": 3, "uncovered": 2, "rate": 100 / 3},
                                "branch": {"covered": 1, "total": 2, "uncovered": 1, "rate": 50.0},
                            }
                        }
                    },
                }
            }
        },
        "items": [
            {"kind": "line", "file": "/rtl/top.v", "module": "Top", "line": 11, "column": 0, "object": "line", "detail": "11"},
            {"kind": "line", "file": "/rtl/top.v", "module": "Top", "line": 12, "column": 0, "object": "line", "detail": "12"},
            {"kind": "line", "file": "/rtl/top.v", "module": "Top", "line": 15, "column": 0, "object": "line", "detail": "15"},
            {"kind": "toggle", "file": "/rtl/top.v", "module": "Top", "line": 20, "column": 0, "object": "data[0]", "detail": "1 -> 0"},
            {"kind": "toggle", "file": "/rtl/top.v", "module": "Top", "line": 20, "column": 0, "object": "data[1]", "detail": "0 -> 1"},
            {"kind": "branch", "file": "/rtl/top.v", "module": "Top", "line": 30, "column": 0, "object": "valid", "detail": "false"},
        ],
        "errors": [],
    }
    if include_extended:
        report["metrics"].update({
            "condition": {"covered": 1, "total": 2, "uncovered": 1, "rate": 50.0, "available": True},
            "fsm": {"covered": 1, "total": 2, "uncovered": 1, "rate": 50.0, "available": True},
        })
        report["items"].extend([
            {
                "kind": "condition", "file": "/rtl/top.v", "module": "Top", "line": 40,
                "column": 0, "object": "ready && valid", "detail": "10",
                "atom_kind": "condition_bin", "native_id": "10", "native_name": "10",
                "coverable": 1, "counts_toward_rate": True,
            },
            {
                "kind": "fsm", "file": "/rtl/top.v", "module": "Top", "line": 50,
                "column": 0, "object": "state", "detail": "idle->busy",
                "atom_kind": "fsm_transition", "native_id": "idle->busy",
                "native_name": "idle->busy", "from_state": "idle", "to_state": "busy",
                "coverable": 1, "counts_toward_rate": True,
            },
        ])
    script.write_text(
        "#!/usr/bin/env python3\nimport json, pathlib, sys\n"
        "pathlib.Path(__file__).with_suffix('.args').write_text(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps(" + repr(report) + "))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def make_fake_fsm_schema1_helper(path: Path) -> Path:
    script = path / "coverage_fsm"
    report = {
        "schema_version": 1,
        "success": True,
        "simulator": "vcs",
        "query": {"kinds": ["fsm"]},
        "metrics": {
            "fsm": {
                "covered": 8,
                "total": 14,
                "uncovered": 6,
                "rate": 400 / 7,
                "available": True,
            }
        },
        "items": [
            {
                "kind": "fsm", "file": "/rtl/top.v", "module": "Top",
                "line": 307, "object": "state", "detail": "'h3",
            },
            *[
                {
                    "kind": "fsm", "file": "/rtl/top.v", "module": "Top",
                    "line": line, "object": "state", "detail": detail,
                }
                for line, detail in (
                    (489, "'h2->'h0"),
                    (489, "'h3->'h0"),
                    (489, "'h4->'h0"),
                    (489, "'h6->'h0"),
                    (529, "'h5->'h4"),
                    (556, "'h3->'h4"),
                )
            ],
        ],
        "errors": [],
    }
    script.write_text(
        "#!/usr/bin/env python3\nimport json\nprint(json.dumps(" + repr(report) + "))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def make_vdb(path: Path) -> Path:
    path.mkdir()
    (path / "snps" / "coverage" / "db").mkdir(parents=True)
    return path


def test_load_provider_summary(tmp_path):
    summary = tmp_path / "summary.json"
    write_summary(summary, "vcs", 7, 10)

    line = load_provider_summary(summary, "vcs")["line"]

    assert (line.covered, line.total, line.uncovered, line.rate) == (7, 10, 3, 70.0)
    assert line.available and line.details_available


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("test_random_alu[arith_logic]", "test_random_alu_arith_logic_"),
        ("nested/test_random_branch[eq_ne].dat", "test_random_branch_eq_ne_"),
        ("case-a_1", "case-a_1"),
    ],
)
def test_vcs_testdata_name_matches_picker(value, expected):
    assert normalize_vcs_testdata_name(value) == expected


def test_vcs_merges_selected_testdata_into_new_report(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path)
    case_summary = tmp_path / "case.json"
    merged_summary = tmp_path / "merged.json"
    write_summary(case_summary, "vcs", 3, 10)
    write_summary(merged_summary, "vcs", 8, 10)
    records = [
        CoverageRecord("vcs", str(database), test="case_a", summary=str(case_summary), helper=str(helper)),
        CoverageRecord(
            "vcs",
            str(database),
            test="case_b",
            summary=str(merged_summary),
            helper=str(helper),
            summary_scope="database",
        ),
    ]

    result = convert_code_coverage(
        records, tmp_path / "line_dat", urg=str(make_fake_urg(tmp_path))
    )

    assert result.metrics["line"].covered == 2
    assert result.summary_file == "line_dat/code_coverage.json"
    report = json.loads((tmp_path / result.summary_file).read_text())
    assert report["schema_version"] == 5
    assert report["metadata"]["tests"] == ["case_a", "case_b"]
    module = report["uncovered"]["data"]["/rtl/top.v"]["modules"]["Top"]
    assert module["line"] == ["11-12", "15-15"]
    assert module["toggle"][0]["transitions"] == ["0 -> 1", "1 -> 0"]
    assert module["branch"][0]["arms"] == ["false"]
    assert result.detail == "line_dat/vcs_urg/dashboard.html"
    assert "Load Failed" in (tmp_path / "line_dat/urg.stderr.log").read_text()
    assert result.warnings


def test_vcs_code_coverage_matches_golden_json(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path)
    summary = tmp_path / "summary.json"
    write_summary(summary, "vcs", 2, 5)
    result = convert_code_coverage(
        [
            CoverageRecord("vcs", str(database), test="case_b", summary=str(summary), helper=str(helper)),
            CoverageRecord("vcs", str(database), test="case_a", summary=str(summary), helper=str(helper)),
        ],
        tmp_path / "line_dat",
        urg=str(make_fake_urg(tmp_path)),
        source_root=tmp_path,
    )
    actual = json.loads((tmp_path / result.summary_file).read_text())
    actual["metadata"]["database"] = "<DATABASE>"
    atom_inventory = actual.pop("atom_inventory")
    for key in ("provider", "snapshot", "metrics", "gaps"):
        actual.pop(key, None)
    actual["schema_version"] = 2
    for value in actual["detail_summary"].values():
        value.pop("enumerated_rate_atoms", None)
    actual["uncovered"]["schema"]["file_path"]["total"] = ["line", "toggle", "branch"]
    actual["uncovered"]["schema"]["file_path"]["miss"] = ["line", "toggle", "branch"]
    actual["uncovered"]["schema"]["file_path"]["modules"]["module_name"]["total"] = ["line", "toggle", "branch"]
    actual["uncovered"]["schema"]["file_path"]["modules"]["module_name"]["miss"] = ["line", "toggle", "branch"]
    golden = json.loads(
        (Path(__file__).parent / "fixtures" / "vcs_code_coverage.golden.json").read_text()
    )

    assert actual == golden
    assert atom_inventory["reconciliation"]["duplicate_identities"] == []
    assert atom_inventory["reconciliation"]["provider_item_count"] == atom_inventory["reconciliation"]["enumerated_atom_count"]
    assert all(atom["identity"]["instance"] == "" for atom in atom_inventory["atoms"])
    modules = [
        module
        for file_data in actual["uncovered"]["data"].values()
        for module in file_data["modules"].values()
    ]
    line_miss = sum(
        int(end) - int(start) + 1
        for module in modules
        for start, end in (value.split("-", 1) for value in module["line"])
    )
    toggle_miss = sum(item.get("atom_count", len(item["transitions"])) for module in modules for item in module["toggle"])
    branch_miss = sum(item.get("atom_count", len(item["arms"])) for module in modules for item in module["branch"])
    assert (line_miss, toggle_miss, branch_miss) == tuple(
        actual["overview"]["miss"][name] for name in ("line", "toggle", "branch")
    )


def test_vcs_case_coverage_uses_testdata_and_normalized_schema(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path)
    record = CoverageRecord(
        "vcs",
        str(database),
        test="case_a",
        helper=str(helper),
        nodeid="tests/test_top.py::test_a",
        ignore=["/rtl/top.v:12"],
    )

    result = convert_case_coverage(record, tmp_path / "line_dat" / "cases" / "abc")
    report = json.loads(Path(result.summary_file).read_text())
    assert report["schema_version"] == 5
    atom_inventory = report["atom_inventory"]
    assert atom_inventory["observation"] == "uncovered"
    assert report["scope"] == {
        "type": "testcase",
        "nodeid": "tests/test_top.py::test_a",
        "testdata": "case_a",
    }
    assert report["overview"]["total"] == {"line": 4, "toggle": 3, "branch": 2}
    assert report["overview"]["miss"] == {"line": 2, "toggle": 2, "branch": 1}
    module = report["uncovered"]["data"]["/rtl/top.v"]["modules"]["Top"]
    assert module["line"] == ["11-11", "15-15"]


def test_vcs_case_coverage_normalizes_parametrized_testdata(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path)
    result = convert_case_coverage(
        CoverageRecord(
            "vcs",
            str(database),
            test="test_random_alu[arith_logic]",
            helper=str(helper),
            nodeid="tests/test_random.py::test_random_alu[arith_logic]",
        ),
        tmp_path / "case",
    )

    report = json.loads(Path(result.summary_file).read_text())
    assert report["scope"]["nodeid"].endswith("test_random_alu[arith_logic]")
    assert report["scope"]["testdata"] == "test_random_alu_arith_logic_"
    assert report["metadata"]["tests"] == ["test_random_alu_arith_logic_"]
    args = json.loads(helper.with_suffix(".args").read_text())
    test_index = args.index("--test")
    assert args[test_index + 1] == "test_random_alu_arith_logic_"
    assert "--detail" not in args
    assert args.count("case_a") == 0


def test_vcs_preview_publishes_current_uncovered_atoms(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path)
    record = CoverageRecord("vcs", str(database), test="case_a", helper=str(helper))
    convert_vcs_preview_coverage([record], tmp_path / "preview", helper=str(helper))

    args = json.loads(helper.with_suffix(".args").read_text())
    assert "--detail" not in args
    manifest = json.loads((tmp_path / "preview/catalog.json").read_text())
    atoms = [
        json.loads(line)
        for line in (tmp_path / "preview/catalog/atoms.jsonl").read_text().splitlines()
    ]
    assert manifest["observation"] == "uncovered"
    assert len(atoms) == 6
    assert all(atom["observation"] == "uncovered" for atom in atoms)


def test_vcs_schema1_fsm_auxiliary_does_not_count_toward_rate(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_fsm_schema1_helper(tmp_path)
    record = CoverageRecord("vcs", str(database), test="case_a", helper=str(helper))

    result = convert_case_coverage(record, tmp_path / "case")
    report = json.loads(Path(result.summary_file).read_text())
    inventory = report["atom_inventory"]
    reconciliation = inventory["reconciliation"]

    assert reconciliation["provider_item_count"] == 7
    assert reconciliation["enumerated_atom_count"] == 7
    assert reconciliation["unresolved"] == []
    assert report["detail_summary"]["fsm"]["enumerated_rate_atoms"] == 6
    assert report["detail_summary"]["fsm"]["unresolved"] == 0
    auxiliary = next(
        atom
        for atom in inventory["atoms"]
        if atom["identity"]["transition_or_arm"] == "'h3"
    )
    assert auxiliary["identity"]["atom_kind"] == "fsm_auxiliary"
    assert auxiliary["identity"]["counts_toward_rate"] is False


def test_vcs_rejects_multiple_vdbs(tmp_path):
    first = make_vdb(tmp_path / "first.vdb")
    second = make_vdb(tmp_path / "second.vdb")
    first_summary = tmp_path / "first.json"
    second_summary = tmp_path / "second.json"
    write_summary(first_summary, "vcs", 4, 10)
    write_summary(second_summary, "vcs", 5, 10)

    with pytest.raises(ValueError, match="exactly one shared VDB"):
        convert_code_coverage(
            [
                CoverageRecord("vcs", str(first), summary=str(first_summary)),
                CoverageRecord("vcs", str(second), summary=str(second_summary)),
            ],
            tmp_path / "line_dat",
            urg=str(make_fake_urg(tmp_path)),
        )


def test_vcs_deduplicates_tests_and_applies_uncovered_line_filters(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path)
    summary = tmp_path / "case.json"
    write_summary(summary, "vcs", 2, 5)
    records = [
        CoverageRecord("vcs", str(database), test="case_b", summary=str(summary), helper=str(helper), ignore=["/rtl/top.v:12,20"]),
        CoverageRecord("vcs", str(database), test="case_a", summary=str(summary), helper=str(helper), ignore=["/rtl/top.v:12,20"]),
        CoverageRecord("vcs", str(database), test="case_a", summary=str(summary), helper=str(helper), ignore=["/rtl/top.v:12,20"]),
    ]

    result = convert_code_coverage(
        records, tmp_path / "line_dat", urg=str(make_fake_urg(tmp_path))
    )
    report = json.loads((tmp_path / result.summary_file).read_text())
    module = report["uncovered"]["data"]["/rtl/top.v"]["modules"]["Top"]

    assert report["metadata"]["tests"] == ["case_a", "case_b"]
    assert module["line"] == ["11-11", "15-15"]
    assert module["toggle"] == []
    assert report["overview"]["total"] == {"line": 4, "toggle": 0, "branch": 2}
    assert report["overview"]["miss"] == {"line": 2, "toggle": 0, "branch": 1}
    args = json.loads(helper.with_suffix(".args").read_text())
    assert args.count("case_a") == args.count("case_b") == 1


def test_vcs_file_filter_removes_file_from_totals_and_details(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path)
    summary = tmp_path / "case.json"
    write_summary(summary, "vcs", 2, 5)

    result = convert_code_coverage(
        [CoverageRecord("vcs", str(database), test="case_a", summary=str(summary), helper=str(helper), ignore=["*top.v"])],
        tmp_path / "line_dat",
        urg=str(make_fake_urg(tmp_path)),
    )
    report = json.loads((tmp_path / result.summary_file).read_text())

    assert report["uncovered"]["data"] == {}
    assert report["overview"]["total"] == {"line": 0, "toggle": 0, "branch": 0}
    assert report["overview"]["miss"] == {"line": 0, "toggle": 0, "branch": 0}


def test_vcs_condition_and_fsm_are_native_atoms(tmp_path):
    database = make_vdb(tmp_path / "Top.vdb")
    helper = make_fake_helper(tmp_path, include_extended=True)
    summary = tmp_path / "summary.json"
    write_summary(summary, "vcs", 2, 5)

    result = convert_code_coverage(
        [CoverageRecord("vcs", str(database), test="case_a", summary=str(summary), helper=str(helper))],
        tmp_path / "coverage_data",
        urg=str(make_fake_urg(tmp_path)),
    )
    report = json.loads((tmp_path / result.summary_file).read_text())
    assert result.detail == "coverage_data/native/vcs_urg/dashboard.html"
    assert report["metrics"]["condition"]["total"] == 2
    assert report["metrics"]["fsm"]["total"] == 2
    module = report["gaps"]["/rtl/top.v"]["modules"]["Top"]
    assert "overview" not in report and "uncovered" not in report
    assert module["condition"][0]["bins"] == ["10"]
    assert module["fsm"][0]["transitions"] == []
    assert "atom_inventory" not in report
    catalog = report["catalog"]
    assert catalog["atom_count"] == 8
    assert (tmp_path / "coverage_data" / catalog["path"]).is_file()
    manifest = json.loads((tmp_path / "coverage_data/catalog.json").read_text())
    assert manifest["observation"] == "uncovered"
    groups = {
        row["group_key"]: row["identity"]
        for row in map(json.loads, (tmp_path / "coverage_data/catalog/groups.jsonl").read_text().splitlines())
    }
    common = manifest["common_identity"]
    identities = []
    for row in map(json.loads, (tmp_path / "coverage_data/catalog/atoms.jsonl").read_text().splitlines()):
        identities.append({**common, **groups[row["group_key"]], **row["variant"]})
    assert any(
        item["metric"] == "condition" and item["transition_or_arm"] == "10"
        for item in identities
    )
    assert any(
        item["metric"] == "fsm" and item["transition_or_arm"] == "idle->busy"
        for item in identities
    )


def test_find_urg_entry_falls_back_to_nested_html(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    entry = nested / "report.html"
    entry.write_text("report", encoding="utf-8")

    assert find_urg_entry(tmp_path) == entry


def test_rejects_mixed_simulators(tmp_path):
    with pytest.raises(ValueError, match="different simulators"):
        convert_code_coverage(
            [
                CoverageRecord("vcs", "/tmp/a.vdb"),
                CoverageRecord("verilator", "/tmp/a.dat"),
            ],
            tmp_path,
        )


def test_legacy_verilator_record_uses_provider_neutral_result(tmp_path, monkeypatch):
    from toffee_test.utils.code_coverage import verilator

    database = tmp_path / "coverage.dat"
    database.write_text("# SystemC::Coverage-3\n", encoding="utf-8")

    def fake_convert(records, output_dir):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "merged.info").write_text("", encoding="utf-8")
        (output / "code_coverage.json").write_text(
            json.dumps(
                {
                    "overview": {
                        "total": {"line": 10, "branch": 4},
                        "miss": {"line": 2, "branch": 1},
                    }
                }
            ),
            encoding="utf-8",
        )
        return str(output / "merged.info"), [(str(database), [], [])]

    monkeypatch.setattr(verilator, "convert_legacy_verilator", fake_convert)
    genhtml = tmp_path / "genhtml"
    genhtml.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys
output = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])
(output / 'index.html').write_text('LCOV', encoding='utf-8')
""",
        encoding="utf-8",
    )
    genhtml.chmod(0o755)

    result = convert_code_coverage(
        [{"data": str(database), "ignore": []}],
        tmp_path / "line_dat",
        genhtml=str(genhtml),
    )

    assert result.simulator == "verilator"
    assert (result.metrics["line"].covered, result.metrics["line"].total) == (8, 10)
    assert result.detail == "line_dat/index.html"


def test_verilator_case_coverage_writes_normalized_json_only(tmp_path, monkeypatch):
    from toffee_test.utils.code_coverage import verilator

    database = tmp_path / "case.dat"
    database.write_text("# SystemC::Coverage-3\n", encoding="utf-8")

    def fake_convert(records, output_dir):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        (output / "merged.info").write_text("", encoding="utf-8")
        (output / "merged.dat").write_text("", encoding="utf-8")
        (output / "code_coverage.json").write_text(
            json.dumps(
                {
                    "description": "Code coverage summary.",
                    "simulator": "verilator",
                    "overview": {
                        "total": {"line": 10, "branch": 4},
                        "miss": {"line": 2, "branch": 1},
                    },
                    "uncovered": {"schema": {}, "data": {}},
                }
            ),
            encoding="utf-8",
        )
        return str(output / "merged.info"), [(str(database), [], [])]

    monkeypatch.setattr(verilator, "convert_legacy_verilator", fake_convert)
    record = CoverageRecord(
        "verilator",
        str(database),
        nodeid="tests/test_top.py::test_a",
    )

    result = convert_case_coverage(record, tmp_path / "line_dat" / "cases" / "abc")
    report = json.loads(Path(result.summary_file).read_text())

    assert report["schema_version"] == 2
    assert report["scope"]["nodeid"] == "tests/test_top.py::test_a"
    assert result.metrics["line"].covered == 8
    assert not (Path(result.summary_file).parent / "merged.info").exists()
    assert not (Path(result.summary_file).parent / "merged.dat").exists()


def test_toffee_request_records_vcs_after_finish(tmp_path, monkeypatch):
    from toffee_test.request import ToffeeRequest

    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    calls = []

    class FakeDut:
        def __init__(self, **kwargs):
            assert kwargs["coverage_filename"] == ""

        def GetWaveFormat(self):
            return ""

        def GetCovMetrics(self):
            return 1

        def GetSimulator(self):
            return "vcs"

        def SetCoverage(self, name):
            calls.append(("set", name))

        def GetCoveragePath(self):
            return str(tmp_path / "Top.vdb")

        def GetCoverageHelperPath(self):
            return str(tmp_path / "coverage")

        def Finish(self):
            calls.append(("finish",))
            return 0

    class Config:
        option = SimpleNamespace(report=[str(tmp_path / "report.html")])

        @staticmethod
        def getoption(name):
            return {"--toffee-report": True, "--no-func-cov": True}[name]

    node = SimpleNamespace(name="test_case")
    request = SimpleNamespace(
        config=Config(),
        node=node,
        path=tmp_path / "test_module.py",
        scope="function",
    )
    wrapper = ToffeeRequest(request)

    wrapper.create_dut(FakeDut)
    wrapper.finish(request)

    assert calls[0][0] == "set"
    assert not calls[0][1].endswith(".dat")
    assert calls[-1][0] == "finish"
    record = json.loads(node.__line_coverage__)
    assert record["simulator"] == "vcs"
    assert record["database"] == str(tmp_path / "Top.vdb")
    assert record["test"] == calls[0][1]
    assert record["helper"] == str(tmp_path / "coverage")
    assert record["nodeid"].endswith("test_module.py::test_case")
    assert record["summary"] is None
    assert record["summary_scope"] == "test"
    assert record["worker"] == "master"


def test_testcase_coverage_index_contains_code_and_functional_links(tmp_path, monkeypatch):
    from toffee.funcov import get_func_full_name
    from toffee_test import reporter

    def sample_case():
        pass

    nodeid = "tests/test_top.py::test_sample[one]"
    function_id = get_func_full_name(sample_case)
    group = {
        "name": "FG-CORE",
        "points": [
            {
                "name": "FC-PATH",
                "bins": [
                    {"name": "CK-HIT", "hints": 3},
                    {"name": "CK-MISS", "hints": 0},
                ],
                "functions": {"CK-MISS": [function_id]},
            }
        ],
    }
    record = CoverageRecord(
        "vcs",
        str(tmp_path / "Top.vdb"),
        test="case_a",
        helper=str(tmp_path / "coverage"),
        nodeid=nodeid,
    )
    phase_report = SimpleNamespace(duration=0.25)
    phase_report.__coverage_group__ = [
        {"hash": "group", "id": "worker", "data": json.dumps(group)}
    ]
    phase_report.__line_coverage__ = record.to_dict()
    test = {
        "item": SimpleNamespace(
            nodeid=nodeid,
            function=sample_case,
            path=tmp_path / "tests" / "test_top.py",
            name="test_sample[one]",
        ),
        "status": {"word": "PASSED"},
        "phases": [{"report": phase_report}],
    }

    def fake_case_coverage(record, output_dir):
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        summary = output / "code_coverage.json"
        summary.write_text("{}", encoding="utf-8")
        return SimpleNamespace(
            metrics={
                "line": SimpleNamespace(
                    covered=7, total=10, uncovered=3, rate=70.0
                )
            }
        )

    monkeypatch.setattr(reporter, "convert_case_coverage", fake_case_coverage)
    reporter.set_output_report(str(tmp_path / "report" / "report.html"))

    relative = reporter.__update_testcase_coverage__([test])
    index = json.loads((tmp_path / "report" / relative).read_text())
    rows_path = tmp_path / "report" / "coverage_data" / index["cases"]["path"]
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    case = next(row for row in rows if row["nodeid"] == nodeid)
    provider = next(value for value in index["providers"] if value["id"] == case["provider_ref"])
    assert index["schema_version"] == 2
    assert index["cases"]["rows"] == 1
    assert provider["testdata"] == "case_a"

    assert case["status"] == "passed"
    assert case["duration"] == 0.25
    assert case["functional"]["declared"] == ["FG-CORE/FC-PATH/CK-MISS"]
    assert case["functional"]["hit"] == [
        {"id": "FG-CORE/FC-PATH/CK-HIT", "count": 3}
    ]
    assert case["code"]["line"] == {
        "covered": 7,
        "total": 10,
        "miss": 3,
        "rate": 70.0,
    }
    assert index["functional_index"]["FG-CORE/FC-PATH/CK-MISS"][
        "declared_by"
    ] == [nodeid]
    assert index["functional_index"]["FG-CORE/FC-PATH/CK-HIT"]["hit_by"] == [
        nodeid
    ]


def test_testcase_coverage_metadata_mode_skips_vdb_query(tmp_path, monkeypatch):
    from toffee_test import reporter

    nodeid = "tests/test_top.py::test_sample"
    record = CoverageRecord(
        "vcs", str(tmp_path / "Top.vdb"), test="case_a", helper=str(tmp_path / "coverage"), nodeid=nodeid
    )
    phase_report = SimpleNamespace(duration=0.1)
    phase_report.__line_coverage__ = record.to_dict()
    test = {
        "item": SimpleNamespace(
            nodeid=nodeid, path=tmp_path / "tests" / "test_top.py", name="test_sample", function=lambda: None
        ),
        "status": {"word": "PASSED"},
        "phases": [{"report": phase_report}],
    }

    def unexpected_query(*_args, **_kwargs):
        raise AssertionError("metadata mode must not query per-case VDB coverage")

    monkeypatch.setenv("TOFFEE_CASE_COVERAGE_MODE", "metadata")
    monkeypatch.setattr(reporter, "convert_case_coverage", unexpected_query)
    reporter.set_output_report(str(tmp_path / "report" / "report.html"))
    relative = reporter.__update_testcase_coverage__([test])
    index = json.loads((tmp_path / "report" / relative).read_text())
    rows_path = tmp_path / "report" / "coverage_data" / index["cases"]["path"]
    case = next(json.loads(line) for line in rows_path.read_text().splitlines())
    provider = next(value for value in index["providers"] if value["id"] == case["provider_ref"])

    assert case["coverage_mode"] == "metadata"
    assert "coverage" not in case
    assert provider["testdata"] == "case_a"
    assert index["summary"]["coverage_errors"] == 0


def test_testcase_coverage_index_keeps_failed_case_without_database(tmp_path):
    from toffee_test import reporter

    def failed_case():
        pass

    nodeid = "tests/test_top.py::test_failed"
    test = {
        "item": SimpleNamespace(
            nodeid=nodeid,
            function=failed_case,
            path=tmp_path / "tests" / "test_top.py",
            name="test_failed",
        ),
        "status": {"word": "FAILED"},
        "phases": [{"report": SimpleNamespace(duration=0.1)}],
    }
    reporter.set_output_report(str(tmp_path / "report" / "report.html"))

    relative = reporter.__update_testcase_coverage__([test])
    index = json.loads((tmp_path / "report" / relative).read_text())
    case = index["testcases"][nodeid]

    assert index["summary"] == {
        "total": 1,
        "passed": 0,
        "failed": 1,
        "skipped": 0,
        "error": 0,
        "coverage_errors": 0,
    }
    assert case["included_in_merged"] is False
    assert case["coverage"] is None
    assert case["code"] == {}
    assert case["error"] == ""


def test_testcase_coverage_supports_forked_function_without_source(tmp_path):
    from toffee_test import reporter

    namespace = {}
    exec(compile("def test_forked(): pass\n", "<pytest-forked>", "exec"), namespace)
    test_function = namespace["test_forked"]
    source_path = tmp_path / "tests" / "test_top.py"
    nodeid = "tests/test_top.py::test_forked"
    test = {
        "item": SimpleNamespace(
            nodeid=nodeid,
            function=test_function,
            path=source_path,
            name="test_forked",
        ),
        "status": {"word": "PASSED"},
        "phases": [{"report": SimpleNamespace(duration=0.1)}],
    }
    reporter.set_output_report(str(tmp_path / "report" / "report.html"))

    relative = reporter.__update_testcase_coverage__([test])
    index = json.loads((tmp_path / "report" / relative).read_text())

    assert index["testcases"][nodeid]["source"] == {
        "file": str(source_path),
        "function": "test_forked",
    }


def test_process_context_supports_forked_function_without_source(tmp_path):
    from toffee_test import reporter

    namespace = {}
    exec(compile("def test_forked(): pass\n", "<pytest-forked>", "exec"), namespace)
    nodeid = "tests/test_top.py::test_forked"
    context = {
        "metadata": {},
        "tests": [
            {
                "item": SimpleNamespace(
                    nodeid=nodeid,
                    function=namespace["test_forked"],
                    path=tmp_path / "tests" / "test_top.py",
                    name="test_forked",
                ),
                "status": {"word": "PASSED"},
                "phases": [],
            }
        ],
        "session": SimpleNamespace(),
    }
    config = SimpleNamespace(
        option=SimpleNamespace(toffee_report_dump_json=False),
        getoption=lambda name: {"--case-coverage": False}[name],
    )
    reporter.set_output_report(str(tmp_path / "report" / "report.html"))

    reporter.process_context(context, config)

    assert context["test_abstract_info"] == {nodeid: "PASSED"}


def test_process_context_does_not_generate_case_index_without_switch(tmp_path):
    from toffee_test import reporter

    def sample_case():
        pass

    context = {
        "metadata": {},
        "tests": [
            {
                "item": SimpleNamespace(function=sample_case),
                "status": {"word": "PASSED"},
                "phases": [],
            }
        ],
        "session": SimpleNamespace(),
    }
    config = SimpleNamespace(
        option=SimpleNamespace(toffee_report_dump_json=False),
        getoption=lambda name: {"--case-coverage": False}[name],
    )
    reporter.set_output_report(str(tmp_path / "report" / "report.html"))

    reporter.process_context(context, config)

    assert context["case_coverage"] is None
    assert not (tmp_path / "report" / "line_dat" / "testcase_coverage.json").exists()
