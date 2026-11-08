"""UVS coverage provider: run uvcov and map its JSON onto the shared report model.

A DUT has one compile database (``<DUT>/<Top>.cdb``) and every test writes its
own run database (the path given to ``SetCoverage``).  ``CoverageRecord.database``
is the compile database and ``CoverageRecord.test`` is the run database.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Any, Iterator

from .common import html_report_detail, write_line_coverage_summary
from .models import ApplicationDetail, CodeCoverageResult, CoverageRecord, HtmlDetail, MetricSummary
from .provider import CoverageProvider
from .xpra import write_xpra_link
from .vcs import (
    _apply_ignores,
    _build_code_coverage_report,
    _compact_cumulative,
    _externalize_atom_catalog,
    _update_rate,
    _write_json_document,
    find_urg_entry,
)


# uvcov metric -> report metric. UVS has no branch metric.
UVCOV_METRICS = {"stmt": "line", "cond": "condition", "tgl": "toggle", "fsm": "fsm"}
UNKNOWN_FILE = "<unknown>"
_MODULE_DECLARATION = re.compile(
    r"^\s*(?:module|macromodule)\s+(?:(?:automatic|static)\s+)?(\w+)", re.MULTILINE
)


class UvsCoverageProvider(CoverageProvider):
    """Convert UVS coverage and open the selected databases in UVD."""

    simulator_names = ("uvs",)

    def convert_case(self, record, output_dir, **options):
        return convert_uvs_case_coverage(record, output_dir, **options)

    def convert_session(self, records, output_dir, **options):
        return convert_uvs_coverage(records, output_dir, **options)

    def build_details(self, result, output_dir):
        if result.error:
            return []
        details = [HtmlDetail(path=result.detail, label="HTML")] if result.detail else []
        if result.databases:
            try:
                argv = ["uvd", "-geometry", "1500x740+0+0", "-ucdb", result.databases[0]]
                href = write_xpra_link(
                    output_dir, argv, label="UVD coverage", cwd=str(Path(output_dir).resolve()),
                )
            except (OSError, ValueError) as error:
                result.warnings.append(f"UVD detail unavailable: {error}")
                return details
            details.append(ApplicationDetail(
                viewer="uvd", label="Open UVD", mode="xpra", href=href,
                scope=result.report_scope,
            ))
        return details


def _resolve_uvcov(uvcov: str | None) -> str:
    if uvcov:
        return uvcov
    home = os.environ.get("UVS_HOME")
    if home and (Path(home) / "bin" / "uvcov").is_file():
        return str(Path(home) / "bin" / "uvcov")
    found = shutil.which("uvcov")
    if found:
        return found
    raise FileNotFoundError("uvcov was not found; set UVS_HOME or add uvcov to PATH")


def _validated_database(records: list[CoverageRecord]) -> Path:
    databases = {str(Path(record.database).resolve()) for record in records}
    if len(databases) != 1:
        raise ValueError("UVS coverage requires exactly one shared compile database")
    database = Path(databases.pop())
    if database.suffix.lower() != ".cdb" or not (database / "file_info.json").is_file():
        raise ValueError(f"invalid UVS compile database: '{database}'")
    return database


def _existing_runs(records: list[CoverageRecord]) -> tuple[list[str], list[str]]:
    """Return usable run databases and warnings for tests that wrote none.

    A test that aborted before ``Finish`` has no data, because UVS writes the
    run database in ``UvsFinish``.
    """
    runs = sorted({str(record.test) for record in records if record.test})
    existing = [run for run in runs if Path(run).is_dir()]
    warnings = [f"No UVS coverage data for test: {run}" for run in runs if run not in existing]
    if not existing:
        raise ValueError("no UVS run database was written; did the tests call Finish()?")
    return existing, warnings


def _run_uvcov(uvcov: str | None, database: Path, runs: list[str], report_dir: Path,
               merged_database: Path | None = None) -> None:
    report_dir = report_dir.resolve()
    command = [_resolve_uvcov(uvcov), "-db", str(database), *(str(Path(run).resolve()) for run in runs),
               "-detail", "-reportdir", str(report_dir)]
    if merged_database is not None:
        command += ["-mdb", str(merged_database.resolve())]
    completed = subprocess.run(command, cwd=report_dir.parent, capture_output=True, text=True,
                               errors="replace", check=False)
    if completed.returncode:
        raise RuntimeError(f"uvcov failed: {(completed.stdout + completed.stderr)[-2000:]}")


def _load_file_info(database: Path) -> dict[str, Any]:
    data = (database / "file_info.json").read_bytes()
    try:
        data = zlib.decompress(data)
    except zlib.error:
        pass
    return json.loads(data)


def _module_files(database: Path) -> dict[str, str]:
    """Map module names to source files.

    uvcov reports lines per instance without a file name.  Picker compiles
    inside ``<DUT>/build`` and removes it afterwards, keeping the sources in
    ``<DUT>``, so paths below the compile directory are resolved there.
    """
    info = _load_file_info(database)
    compile_path = Path(info.get("compile_path") or "")
    modules: dict[str, str] = {}
    for entry in info.get("file_ids") or []:
        if int(entry.get("file_source", 0)) != 0:  # file shipped with the simulator
            continue
        path = Path(entry["file_path"])
        if path.is_relative_to(compile_path):
            path = database.parent / path.relative_to(compile_path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in _MODULE_DECLARATION.findall(text):
            modules.setdefault(name, str(path))
    return modules


def _instance_scopes(summary: dict[str, Any]) -> dict[int, tuple[str, str]]:
    """Map uvcov instance ids to (hierarchical path, module name)."""
    instances = {instance["id"]: instance for instance in summary.get("instances") or []}
    module_names = {module["id"]: module["name"] for module in summary.get("modules") or []}

    def path(instance: dict[str, Any]) -> str:
        parent = instances.get(instance.get("pid"))
        return f"{path(parent)}.{instance['name']}" if parent else instance["name"]

    return {
        identifier: (path(instance), module_names.get(instance.get("mid"), ""))
        for identifier, instance in instances.items()
    }


def _bit_indices(name: str) -> Iterator[int]:
    high, _, low = str(name).partition(":")
    first, last = sorted((int(high), int(low or high)))
    return iter(range(first, last + 1))


def _toggle_items(detail: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for signal in [*(detail.get("ports") or []), *(detail.get("signals") or [])]:
        name = signal["name"]
        if "bits" not in signal:
            for transition in ("0->1", "1->0"):
                if not signal.get(f"cd_{transition}"):
                    yield {"kind": "toggle", "object": name, "detail": transition}
            continue
        for bit_range in signal["bits"]:
            for transition in bit_range.get("bins") or []:
                if not transition.get("cd"):
                    for bit in _bit_indices(bit_range["name"]):
                        yield {"kind": "toggle", "object": f"{name}[{bit}]",
                               "detail": transition["name"]}


def _metric_items(metric: str, detail: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield one item for every uncovered target that uvcov counts."""
    if metric == "stmt":
        for entry in detail.get("lines") or []:
            # uvcov counts statements per line without identifying them.
            for index in range(int(entry.get("un", 0))):
                yield {"kind": "line", "line": entry["line"], "detail": f"stmt{index}"}
    elif metric == "cond":
        for expression in detail.get("exprs") or []:
            for vector, hits in (expression.get("truth_table") or {}).items():
                if not hits:
                    yield {"kind": "condition", "line": expression["line"],
                           "object": expression["expr"], "detail": vector}
    elif metric == "tgl":
        yield from _toggle_items(detail)
    elif metric == "fsm":
        # uvcov lists the states of all FSMs in an instance without naming the FSM.
        for state in detail.get("states") or []:
            if not state.get("cd"):
                yield {"kind": "fsm", "line": state["line"], "object": "fsm",
                       "detail": state["name"], "atom_kind": "fsm_state",
                       "native_name": state["name"]}
        for transition in detail.get("transitions") or []:
            if not transition.get("cd"):
                source, _, target = transition["name"].partition("->")
                yield {"kind": "fsm", "line": transition["line"], "object": "fsm",
                       "detail": transition["name"], "atom_kind": "fsm_transition",
                       "from_state": source, "to_state": target}


def _empty_metrics() -> dict[str, dict[str, Any]]:
    # UCAgent reads the line entry of every file, so all metrics are always present.
    return {name: {"covered": 0, "total": 0, "uncovered": 0, "rate": 0.0}
            for name in UVCOV_METRICS.values()}


def _read_uvcov_report(report_dir: Path, database: Path, tests: list[str]) -> dict[str, Any]:
    """Build the raw report that the shared VCS report builder consumes."""
    code = json.loads((report_dir / "data" / "code_coverage.json").read_text(encoding="utf-8"))
    summary = json.loads((report_dir / "data" / "coverage.json").read_text(encoding="utf-8"))
    scopes = _instance_scopes(summary)
    module_files = _module_files(database)
    generated_top = f"{database.stem}_top"

    metrics = _empty_metrics()
    files: dict[str, Any] = {}
    items: list[dict[str, Any]] = []
    for instance in code.get("instances") or []:
        instance_path, module = scopes.get(instance["id"], (instance["name"], instance["name"]))
        if module == generated_top:  # testbench wrapper generated by Picker
            continue
        file_path = module_files.get(module, UNKNOWN_FILE)
        file_data = files.setdefault(file_path, {"metrics": _empty_metrics(), "modules": {}})
        module_data = file_data["modules"].setdefault(module, {"metrics": _empty_metrics()})
        for metric, kind in UVCOV_METRICS.items():
            data = instance.get(metric)
            if not data:
                continue
            totals = data["summary"].get("total_bits") or data["summary"]["total"]
            for target in (metrics, file_data["metrics"], module_data["metrics"]):
                target[kind]["total"] += int(totals["un+cd"])
                target[kind]["covered"] += int(totals["cd"])
            for item in _metric_items(metric, data.get("detail") or {}):
                items.append({
                    "file": file_path, "line": 0, "column": 0, "module": module,
                    "instance": instance_path, "object": "", "count": 0, "detail": "",
                    "covered": 0, "coverable": 1, "counts_toward_rate": True, **item,
                })
    # uvcov exits with 0 even if it could not read a run database.
    if not any(value["total"] for value in metrics.values()):
        raise RuntimeError(f"uvcov reported no coverage targets for {database}")
    for target in (metrics, *(file_data["metrics"] for file_data in files.values()),
                   *(module["metrics"] for file_data in files.values()
                     for module in file_data["modules"].values())):
        for value in target.values():
            _update_rate(value)
    for value in metrics.values():
        value.update(available=True, details_available=True)
    return {
        "schema_version": 1,
        "simulator": "uvs",
        "metrics": metrics,
        "summary": {"metrics": metrics, "total_uncovered": len(items)},
        "breakdown": {"files": files},
        "items": items,
        "query": {"tests": tests},
    }


def _report_from_uvcov(
    report_dir: Path,
    database: Path,
    runs: list[str],
    ignore: list[str],
    detail: str,
) -> dict[str, Any]:
    tests = [Path(run).stem for run in runs]
    raw = _read_uvcov_report(report_dir, database, tests)
    patterns, line_ranges = _apply_ignores(raw, ignore)
    report = _build_code_coverage_report(
        raw,
        str(database),
        tests,
        detail,
        patterns + [
            f"{path}:{','.join(str(line) for line in sorted(lines))}"
            for path, lines in sorted(line_ranges.items())
        ],
    )
    report["simulator"] = "uvs"
    return report


def _metric_summaries(report: dict[str, Any]) -> dict[str, MetricSummary]:
    return {
        name: MetricSummary(
            covered=int(value["covered"]), total=int(value["total"]),
            uncovered=int(value["uncovered"]), rate=float(value["rate"]),
            available=True, details_available=True,
        )
        for name, value in report["metrics"].items()
    }


def _native_report_metrics(report_dir: Path) -> dict[str, MetricSummary]:
    summary = json.loads((report_dir / "data" / "coverage.json").read_text(encoding="utf-8"))
    native = (summary.get("summary") or {}).get("summaryData") or {}
    metrics = {}
    for metric, kind in UVCOV_METRICS.items():
        value = native.get(metric)
        if value:
            metrics[kind] = MetricSummary(
                covered=int(value["cd"]), total=int(value["un+cd"]), uncovered=int(value["un"]),
                rate=float(value["score"]), available=True, details_available=True,
            )
    return metrics


def convert_uvs_case_coverage(
    record: CoverageRecord,
    output_dir: Path,
    *,
    uvcov: str | None = None,
) -> CodeCoverageResult:
    if not record.test:
        raise ValueError("UVS testcase coverage requires a run database")
    database = _validated_database([record])
    runs, _ = _existing_runs([record])
    with tempfile.TemporaryDirectory(prefix="toffee-uvcov-") as directory:
        report_dir = Path(directory) / "report"
        _run_uvcov(uvcov, database, runs, report_dir)
        report = _report_from_uvcov(report_dir, database, runs, list(record.ignore), "")
    report["metadata"].pop("html", None)
    report["scope"] = {"type": "testcase", "nodeid": record.nodeid, "testdata": record.test}
    normalized_layout = "coverage_data" in output_dir.parts
    if normalized_layout:
        report.pop("atom_inventory", None)
    summary_path = output_dir / ("observation.json" if normalized_layout else "code_coverage.json")
    _write_json_document(summary_path, report)
    return CodeCoverageResult(
        simulator="uvs",
        metrics=_metric_summaries(report),
        summary_file=str(summary_path),
        databases=[record.database],
        ignore=list(record.ignore),
    )


def convert_uvs_preview_coverage(
    records: list[CoverageRecord],
    output_dir: Path,
    *,
    uvcov: str | None = None,
) -> CodeCoverageResult:
    """Write the candidate query's current uncovered atom catalog."""
    database = _validated_database(records)
    runs, warnings = _existing_runs(records)
    ignore = list(dict.fromkeys(item for record in records for item in record.ignore))
    with tempfile.TemporaryDirectory(prefix="toffee-uvcov-") as directory:
        report_dir = Path(directory) / "report"
        _run_uvcov(uvcov, database, runs, report_dir)
        report = _report_from_uvcov(report_dir, database, runs, ignore, "")
    report["metadata"]["preview"] = True
    # UCAgent owns accepted targets and computes U-C.  Toffee publishes C
    # without applying workflow-specific target filtering.
    report["uncovered"]["data"] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = _metric_summaries(report)
    _externalize_atom_catalog(report, output_dir)
    _compact_cumulative(report)
    _write_json_document(output_dir / "cumulative.json", report)
    return CodeCoverageResult(
        simulator="uvs", metrics=metrics, summary_file="coverage_data/cumulative.json",
        databases=[str(database)], ignore=ignore, warnings=warnings,
    )


def convert_uvs_coverage(
    records: list[CoverageRecord],
    output_dir: Path,
    *,
    uvcov: str | None = None,
) -> CodeCoverageResult:
    database = _validated_database(records)
    runs, warnings = _existing_runs(records)
    ignore = list(dict.fromkeys(item for record in records for item in record.ignore))
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_layout = output_dir.name == "coverage_data"
    native_dir = output_dir / "native" / "uvs_uvcov" if normalized_layout else output_dir / "uvs_uvcov"
    shutil.rmtree(native_dir, ignore_errors=True)
    native_dir.parent.mkdir(parents=True, exist_ok=True)
    merged_database = output_dir / "merged.cdb"
    shutil.rmtree(merged_database, ignore_errors=True)
    _run_uvcov(uvcov, database, runs, native_dir, merged_database)
    if not (merged_database / "design.dat").is_file():
        warnings.append(f"uvcov generated no merged coverage database in '{merged_database}'")
    entry = find_urg_entry(native_dir)
    if entry is None:
        raise RuntimeError(f"uvcov generated no HTML entry in '{native_dir}'")
    detail = html_report_detail(
        native_dir, output_dir, entry_names=(entry.relative_to(native_dir).as_posix(),)
    )
    report = _report_from_uvcov(native_dir, database, runs, ignore, detail)
    report_metrics = _native_report_metrics(native_dir)
    report_scope = "Native report: full hierarchy, including generated wrappers and all database tests."
    report["metadata"]["report_summary"] = {
        "scope": report_scope,
        "metrics": {name: metric.to_dict() for name, metric in report_metrics.items()},
    }
    metrics = _metric_summaries(report)
    write_line_coverage_summary(report, output_dir)
    summary_path = output_dir / ("cumulative.json" if normalized_layout else "code_coverage.json")
    if normalized_layout:
        _externalize_atom_catalog(report, output_dir)
        _compact_cumulative(report)
    _write_json_document(summary_path, report)
    return CodeCoverageResult(
        simulator="uvs",
        metrics=metrics,
        detail=detail,
        summary_file=f"{output_dir.name}/{summary_path.name}",
        databases=[str(merged_database.resolve())] if (merged_database / "design.dat").is_file() else [],
        ignore=ignore,
        warnings=warnings,
        report_metrics=report_metrics,
        report_scope=report_scope,
    )
