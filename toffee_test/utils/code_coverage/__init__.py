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
    "register_coverage_provider",
]


_CASE_PROVIDERS: dict[str, Any] = {}
_SESSION_PROVIDERS: dict[str, Any] = {}
_BUILTINS_READY = False


def register_coverage_provider(
    simulator: str,
    *,
    case_converter: Any,
    session_converter: Any,
) -> None:
    key = str(simulator).strip().lower()
    _CASE_PROVIDERS[key] = case_converter
    _SESSION_PROVIDERS[key] = session_converter


def _ensure_builtin_providers() -> None:
    global _BUILTINS_READY
    if _BUILTINS_READY:
        return
    from .verilator import convert_verilator_case_coverage, convert_verilator_coverage
    from .vcs import convert_vcs_case_coverage, convert_vcs_coverage

    register_coverage_provider(
        "verilator",
        case_converter=convert_verilator_case_coverage,
        session_converter=convert_verilator_coverage,
    )
    register_coverage_provider(
        "vcs",
        case_converter=convert_vcs_case_coverage,
        session_converter=convert_vcs_coverage,
    )
    _BUILTINS_READY = True


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
    _ensure_builtin_providers()
    converter = _CASE_PROVIDERS.get(normalized.simulator)
    if converter is None:
        raise ValueError(f"unsupported coverage simulator: {normalized.simulator}")
    return converter(normalized, path, **provider_options)


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
    _ensure_builtin_providers()
    converter = _SESSION_PROVIDERS.get(simulator)
    if converter is None:
        raise ValueError(f"unsupported coverage simulator: {simulator}")
    return converter(normalized, path, **provider_options)
