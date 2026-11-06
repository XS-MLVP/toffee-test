from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HtmlDetail:
    """An HTML URL relative to the final report, including its resource tree."""

    path: str
    label: str = "HTML"


@dataclass(frozen=True)
class ApplicationDetail:
    """A ready-to-open URL, or artifacts for a shared native/Xpra launcher.

    href bypasses launch generation and is shown only in its matching mode.
    Without href, viewer/artifacts work with either selected GUI mode.
    """

    viewer: str
    label: str
    artifacts: dict[str, str] = field(default_factory=dict)
    scope: str = ""
    href: str = ""
    mode: str = "xpra"


@dataclass(frozen=True)
class CoverageReportContext:
    """Host-independent paths available to an optional application resolver."""

    workspace: Path
    report_dir: Path


ApplicationDetailResolver = Callable[[ApplicationDetail, CoverageReportContext], str | None]


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
    details: list[HtmlDetail | ApplicationDetail] = field(default_factory=list)

    def to_report_dict(
        self, grate: int = 99, *,
        application_detail_resolver: ApplicationDetailResolver | None = None,
        context: CoverageReportContext | None = None,
        coverage_gui: str | None = None,
    ) -> dict[str, Any]:
        """Serialize available links; application resolution is explicitly opt-in."""
        # Native dashboards may include generated wrappers or unrelated tests.
        # Keep the primary summary consistent with the selected DUT metrics.
        line = self.metrics.get("line", MetricSummary())
        descriptors = list(self.details)
        if self.detail and not any(isinstance(item, HtmlDetail) for item in descriptors):
            descriptors.insert(0, HtmlDetail(path=self.detail))
        links = []
        warnings = list(self.warnings)
        mode = coverage_gui or ("xpra" if application_detail_resolver else "off")
        for item in descriptors:
            try:
                if isinstance(item, HtmlDetail):
                    href, kind = item.path, "html"
                elif mode == "off":
                    continue
                elif item.href:
                    if item.mode != mode:
                        continue
                    href, kind = item.href, "application"
                elif application_detail_resolver is not None:
                    if context is None:
                        raise ValueError("application detail resolution requires a report context")
                    href = application_detail_resolver(item, context)
                    kind = "application"
                else:
                    continue
                if href is None:
                    continue
                if not isinstance(href, str) or not href.strip():
                    raise ValueError("detail URL must be a nonempty string")
                url = urlsplit(href)
                native_link = (
                    kind == "application" and url.scheme == "toffee-coverage"
                    and url.netloc == "open" and not url.query and not url.fragment
                    and re.fullmatch(r"/[0-9a-f]{64}", url.path)
                )
                if (url.scheme not in ("", "http", "https") and not native_link) or (url.netloc and not url.scheme) or any(
                    ord(char) < 32 or char == "\\" for char in href
                ):
                    raise ValueError("detail URL must be relative, HTTP(S), or a valid native application link")
                links.append({"label": item.label, "kind": kind, "href": href,
                              "scope": getattr(item, "scope", "")})
            except Exception as error:
                # An optional viewer must not erase valid metrics or HTML links.
                warnings.append(f"{item.label} detail unavailable: {error}")
        result = {
            "simulator": self.simulator,
            # Preserve the existing template/report JSON contract.
            "hints": line.covered,
            "total": line.total,
            "grate": grate,
            "detail": next((link["href"] for link in links if link["kind"] == "html"), ""),
            "details": links,
            "summary_file": self.summary_file,
            "metrics": {name: metric.to_dict() for name, metric in self.metrics.items()},
            "databases": list(self.databases),
            "ignore": list(self.ignore),
            "warnings": warnings,
            "error": self.error,
        }
        if self.report_metrics:
            result["report_metrics"] = {
                name: metric.to_dict() for name, metric in self.report_metrics.items()
            }
            result["report_scope"] = self.report_scope
        return result
