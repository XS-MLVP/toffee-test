from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .models import (
    CodeCoverageResult,
    CoverageRecord,
    MetricSummary,
    normalize_vcs_testdata_name,
)

__all__ = [
    "CodeCoverageResult",
    "CoverageRecord",
    "MetricSummary",
    "normalize_vcs_testdata_name",
    "convert_case_coverage",
    "convert_code_coverage",
]


def convert_case_coverage(
    record: CoverageRecord | Mapping[str, Any],
    output_dir: str | Path,
    **provider_options: Any,
) -> CodeCoverageResult:
    normalized = (
        record if isinstance(record, CoverageRecord) else CoverageRecord.from_dict(record)
    ).normalized()
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    if normalized.simulator == "verilator":
        from .verilator import convert_verilator_case_coverage

        return convert_verilator_case_coverage(normalized, path, **provider_options)
    if normalized.simulator == "vcs":
        from .vcs import convert_vcs_case_coverage

        return convert_vcs_case_coverage(normalized, path, **provider_options)
    raise ValueError(f"unsupported coverage simulator: {normalized.simulator}")


def convert_code_coverage(
    records: list[CoverageRecord | Mapping[str, Any]],
    output_dir: str | Path,
    **provider_options: Any,
) -> CodeCoverageResult:
    normalized = [
        (record if isinstance(record, CoverageRecord) else CoverageRecord.from_dict(record)).normalized()
        for record in records
    ]
    if not normalized:
        raise ValueError("no code coverage records")
    simulators = {record.simulator for record in normalized}
    if len(simulators) != 1:
        raise ValueError(f"cannot merge coverage from different simulators: {sorted(simulators)}")

    simulator = normalized[0].simulator
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    if simulator == "verilator":
        from .verilator import convert_verilator_coverage

        return convert_verilator_coverage(normalized, path, **provider_options)
    if simulator == "vcs":
        from .vcs import convert_vcs_coverage

        return convert_vcs_coverage(normalized, path, **provider_options)
    raise ValueError(f"unsupported coverage simulator: {simulator}")
