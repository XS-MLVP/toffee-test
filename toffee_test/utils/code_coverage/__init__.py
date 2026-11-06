from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .models import (
    ApplicationDetail,
    CoverageReportContext,
    HtmlDetail,
    CodeCoverageResult,
    CoverageRecord,
    MetricSummary,
    normalize_vcs_testdata_name,
)
from .provider import CoverageProvider, FunctionCoverageProvider

__all__ = [
    "ApplicationDetail",
    "CoverageReportContext",
    "HtmlDetail",
    "CoverageProvider",
    "CodeCoverageResult",
    "CoverageRecord",
    "MetricSummary",
    "normalize_vcs_testdata_name",
    "convert_case_coverage",
    "convert_code_coverage",
    "register_coverage_provider",
    "get_coverage_provider",
]


_PROVIDERS: dict[str, CoverageProvider] = {}
_BUILTINS_READY = False


def register_coverage_provider(
    simulator: str | CoverageProvider,
    provider: CoverageProvider | None = None,
    *,
    case_converter: Any = None,
    session_converter: Any = None,
) -> None:
    """Register declared provider names, an explicit name, or converter functions."""
    if isinstance(simulator, CoverageProvider):
        if provider is not None or case_converter is not None or session_converter is not None:
            raise TypeError("a provider instance must be registered without other arguments")
        names = _provider_simulator_names(simulator)
        for name in names:
            _PROVIDERS[name] = simulator
        return
    key = str(simulator).strip().lower()
    if not key:
        raise ValueError("coverage simulator name must not be empty")
    if provider is None:
        if not callable(case_converter) or not callable(session_converter):
            raise TypeError("provide a CoverageProvider or both conversion functions")
        provider = FunctionCoverageProvider(case_converter, session_converter)
    elif not isinstance(provider, CoverageProvider) or case_converter is not None or session_converter is not None:
        raise TypeError("provide a CoverageProvider without conversion functions")
    _PROVIDERS[key] = provider


def _provider_simulator_names(provider: CoverageProvider) -> tuple[str, ...]:
    """Validate and normalize a provider's declared Picker simulator identifiers."""
    names = provider.simulator_names
    if not isinstance(names, (tuple, list)) or not names:
        raise ValueError("provider.simulator_names must be a nonempty tuple/list of names")
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError("provider.simulator_names must contain nonempty strings")
    return tuple(dict.fromkeys(name.strip().lower() for name in names))


def _ensure_builtin_providers() -> None:
    global _BUILTINS_READY
    if _BUILTINS_READY:
        return
    from .verilator import VerilatorCoverageProvider
    from .vcs import VcsCoverageProvider
    from .uvs import UvsCoverageProvider

    for provider in (VerilatorCoverageProvider(), VcsCoverageProvider(), UvsCoverageProvider()):
        for name in _provider_simulator_names(provider):
            _PROVIDERS.setdefault(name, provider)
    _BUILTINS_READY = True


def get_coverage_provider(simulator: str) -> CoverageProvider:
    """Resolve a registered simulator without duplicating simulator-name checks."""
    _ensure_builtin_providers()
    key = str(simulator).strip().lower()
    if key not in _PROVIDERS:
        raise ValueError(f"unsupported coverage simulator: {simulator}")
    return _PROVIDERS[key]


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
    provider = get_coverage_provider(normalized.simulator)
    return provider.convert_case(normalized, path, **provider_options)


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
    provider = get_coverage_provider(simulator)
    result = provider.convert_session(normalized, path, **provider_options)
    result.details = provider.build_details(result, path)
    return result
