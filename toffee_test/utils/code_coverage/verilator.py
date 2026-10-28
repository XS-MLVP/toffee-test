from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..verilator_coverage import convert_verilator_coverage as convert_legacy_verilator
from .common import html_report_detail
from .models import CodeCoverageResult, CoverageRecord, MetricSummary


def _range_atom_count(values: list[str]) -> int:
    count = 0
    for value in values:
        start, separator, end = str(value).partition("-")
        try:
            count += int(end) - int(start) + 1 if separator else 1
        except ValueError:
            continue
    return count


def _add_gap_metadata(summary: dict) -> None:
    """Describe Verilator's line-range gap precision and reconcile detail counts."""
    metadata = summary.setdefault("metadata", {})
    metadata["detail_level"] = {
        "line": "range",
        "toggle": "line_range",
        "branch": "line_range",
        "expr": "line_range",
    }
    totals = summary.get("overview", {}).get("miss", {})
    data = summary.get("uncovered", {}).get("data", {})
    names = ("line", "toggle", "branch", "expr")
    atom_counts = {name: 0 for name in names}
    gap_counts = {name: 0 for name in names}
    for file_data in data.values():
        for module in file_data.get("modules", {}).values():
            for name in names:
                ranges = module.get(name, [])
                atom_counts[name] += _range_atom_count(ranges)
                gap_counts[name] += len(ranges)
    summary["detail_summary"] = {
        name: {
            "metric_items": int(totals.get(name, 0)),
            "enumerated_atoms": atom_counts[name],
            "gap_count": gap_counts[name],
            "unresolved": max(0, int(totals.get(name, 0)) - atom_counts[name]),
        }
        for name in names
        if name in totals
    }


def _metrics_from_summary(summary: dict) -> dict[str, MetricSummary]:
    totals = summary.get("overview", {}).get("total", {})
    misses = summary.get("overview", {}).get("miss", {})
    metrics = {}
    for name, raw_total in totals.items():
        total = int(raw_total)
        uncovered = int(misses.get(name, 0))
        covered = total - uncovered
        metrics[name] = MetricSummary(
            covered=covered,
            total=total,
            uncovered=uncovered,
            rate=100.0 * covered / total if total else 0.0,
            available=True,
            details_available=True,
        )
    return metrics


def convert_verilator_case_coverage(
    record: CoverageRecord,
    output_dir: Path,
) -> CodeCoverageResult:
    _, ignore = convert_legacy_verilator(
        [{"data": record.database, "ignore": record.ignore}], str(output_dir)
    )
    summary_path = output_dir / "code_coverage.json"
    with summary_path.open(encoding="utf-8") as source:
        summary = json.load(source)
    summary["schema_version"] = 2
    _add_gap_metadata(summary)
    summary["scope"] = {
        "type": "testcase",
        "nodeid": record.nodeid,
        "testdata": None,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for artifact in (output_dir / "merged.info", output_dir / "merged.dat"):
        artifact.unlink(missing_ok=True)
    return CodeCoverageResult(
        simulator="verilator",
        metrics=_metrics_from_summary(summary),
        summary_file=str(summary_path),
        databases=[record.database],
        ignore=ignore,
    )


def convert_verilator_coverage(
    records: list[CoverageRecord], output_dir: Path, *, genhtml: str = "genhtml"
) -> CodeCoverageResult:
    legacy_records = [
        {"data": record.database, "ignore": record.ignore}
        for record in records
    ]
    merged_info, ignore = convert_legacy_verilator(legacy_records, str(output_dir))
    command = [genhtml, "--branch-coverage", merged_info, "-o", str(output_dir)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    (output_dir / "genhtml.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (output_dir / "genhtml.stderr.log").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(
            f"genhtml failed with exit code {completed.returncode}; "
            f"see '{output_dir / 'genhtml.stderr.log'}'"
        )
    detail = html_report_detail(output_dir, output_dir)

    summary_path = output_dir / "code_coverage.json"
    with summary_path.open(encoding="utf-8") as source:
        summary = json.load(source)
    summary["schema_version"] = 2
    _add_gap_metadata(summary)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    metrics = _metrics_from_summary(summary)

    return CodeCoverageResult(
        simulator="verilator",
        metrics=metrics,
        detail=detail,
        summary_file=f"{output_dir.name}/{summary_path.name}",
        databases=list(dict.fromkeys(record.database for record in records)),
        ignore=ignore,
    )
