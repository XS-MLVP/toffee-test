from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .models import MetricSummary


def load_provider_summary(path: str | Path, simulator: str) -> dict[str, MetricSummary]:
    summary_path = Path(path)
    with summary_path.open(encoding="utf-8") as source:
        report: dict[str, Any] = json.load(source)

    if report.get("schema_version") not in {1, 5}:
        raise ValueError(f"unsupported coverage schema in '{summary_path}': expected version 1")
    provider = report.get("provider") or {}
    actual_simulator = str(report.get("simulator") or provider.get("simulator") or "").lower()
    if actual_simulator and actual_simulator != simulator:
        raise ValueError(
            f"coverage summary '{summary_path}' is for {actual_simulator}, expected {simulator}"
        )

    raw_metrics = report.get("metrics") or (report.get("summary") or {}).get("metrics") or {}
    metrics: dict[str, MetricSummary] = {}
    for name, raw in raw_metrics.items():
        total = int(raw.get("total", 0))
        covered = int(raw.get("covered", raw.get("hit", 0)))
        uncovered = int(raw.get("uncovered", raw.get("miss", total - covered)))
        metrics[str(name).lower()] = MetricSummary(
            covered=covered,
            total=total,
            uncovered=uncovered,
            rate=float(raw.get("rate", 100.0 * covered / total if total else 0.0)),
            available=bool(raw.get("available", True)),
            details_available=bool(raw.get("details_available", False)),
        )
    return metrics


def html_report_detail(
    report_dir: str | Path,
    output_dir: str | Path,
    *,
    entry_names: tuple[str, ...] = ("index.html",),
) -> str:
    """Return a portable URL to a provider's generated HTML report.

    Providers own report generation and entry preference. The report directory
    (including CSS, JS and child pages) stays under the coverage output directory;
    the final Toffee report links to it without knowing the simulator.
    """
    report_dir = Path(report_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if not report_dir.is_relative_to(output_dir):
        raise ValueError("HTML report must be inside the coverage output directory")
    entry = next(
        (report_dir / name for name in entry_names if (report_dir / name).is_file()),
        None,
    )
    if entry is None:
        raise FileNotFoundError(f"coverage provider did not generate an HTML entry in '{report_dir}'")
    entry = entry.resolve()
    if not entry.is_relative_to(output_dir):
        raise ValueError("HTML entry must be inside the coverage output directory")
    return quote(entry.relative_to(output_dir.parent).as_posix(), safe="/")
