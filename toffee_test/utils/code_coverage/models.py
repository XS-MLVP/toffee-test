from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping


def normalize_vcs_testdata_name(value: str) -> str:
    """Return the testdata name used by Picker's VCS runtime.

    Picker derives the name from the path stem and replaces punctuation that
    VCS cannot use in a testdata directory.  Keeping the same normalization in
    Toffee is particularly important for parametrized pytest names such as
    ``test_random[seed0]``.
    """
    name = Path(str(value)).stem
    if not name or name == ".":
        return "coverage"
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


@dataclass(frozen=True)
class CoverageRecord:
    simulator: str
    database: str
    test: str | None = None
    summary: str | None = None
    helper: str | None = None
    nodeid: str | None = None
    ignore: list[str] = field(default_factory=list)
    worker: str = "master"
    summary_scope: str = "test"

    def normalized(self) -> "CoverageRecord":
        if self.simulator.lower() != "vcs" or not self.test:
            return self
        test = normalize_vcs_testdata_name(self.test)
        return self if test == self.test else replace(self, test=test)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CoverageRecord":
        # Records written by older toffee-test releases only contained data/ignore.
        database = value.get("database", value.get("data", value.get("datfile")))
        if not database:
            raise ValueError("coverage record has no database path")
        return cls(
            simulator=str(value.get("simulator") or "verilator").lower(),
            database=str(database),
            test=None if value.get("test") is None else str(value["test"]),
            summary=None if value.get("summary") is None else str(value["summary"]),
            helper=None if value.get("helper") is None else str(value["helper"]),
            nodeid=None if value.get("nodeid") is None else str(value["nodeid"]),
            summary_scope=str(value.get("summary_scope") or "test"),
            ignore=list(value.get("ignore") or []),
            worker=str(value.get("worker") or "master"),
        ).normalized()


@dataclass(frozen=True)
class MetricSummary:
    covered: int = 0
    total: int = 0
    uncovered: int = 0
    rate: float = 0.0
    available: bool = False
    details_available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CodeCoverageResult:
    simulator: str
    metrics: dict[str, MetricSummary] = field(default_factory=dict)
    # Provider-owned HTML entry URL relative to the final Toffee report.
    # Keep the complete HTML directory beside the report for portable links.
    detail: str = ""
    summary_file: str = ""
    databases: list[str] = field(default_factory=list)
    ignore: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    # Optional presentation summary from the linked native report. Workflow
    # metrics above retain their original DUT/filter/test selection semantics.
    report_metrics: dict[str, MetricSummary] = field(default_factory=dict)
    report_scope: str = ""

    def to_report_dict(self, grate: int = 99) -> dict[str, Any]:
        line = (self.report_metrics or self.metrics).get("line", MetricSummary())
        result = {
            "simulator": self.simulator,
            # Preserve the existing template/report JSON contract.
            "hints": line.covered,
            "total": line.total,
            "grate": grate,
            "detail": self.detail,
            "summary_file": self.summary_file,
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
            "databases": list(self.databases),
            "ignore": list(self.ignore),
            "warnings": list(self.warnings),
            "error": self.error,
        }
        if self.report_metrics:
            result["report_metrics"] = {
                name: metric.to_dict() for name, metric in self.report_metrics.items()
            }
            result["report_scope"] = self.report_scope
        return result
