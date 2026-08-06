from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import MetricSummary


def load_schema_v2_summary(path: str | Path, simulator: str) -> dict[str, MetricSummary]:
    summary_path = Path(path)
    with summary_path.open(encoding="utf-8") as source:
        report: dict[str, Any] = json.load(source)

    if report.get("schema_version") != 2:
        raise ValueError(f"unsupported coverage schema in '{summary_path}': expected version 2")
    provider = report.get("provider") or {}
    actual_simulator = str(provider.get("simulator") or "").lower()
    if actual_simulator and actual_simulator != simulator:
        raise ValueError(
            f"coverage summary '{summary_path}' is for {actual_simulator}, expected {simulator}"
        )

    raw_metrics = (report.get("summary") or {}).get("metrics") or {}
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
