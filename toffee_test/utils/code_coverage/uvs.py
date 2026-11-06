"""UVS skeleton: parse coverage and describe detail links; providers own viewer links."""

from pathlib import Path

from .models import ApplicationDetail, CodeCoverageResult, CoverageRecord, HtmlDetail
from .provider import CoverageProvider


class UvsCoverageProvider(CoverageProvider):
    """Integrate the main index.html; per-case conversion is not implemented yet."""

    # Match Picker SIMULATOR / GetSimulator(); use ("uvs", "othername") for aliases.
    simulator_names = ("uvs",)

    def convert_session(
        self, records: list[CoverageRecord], output_dir: Path, **options,
    ) -> CodeCoverageResult:
        """Part 1: read UVS artifacts from records, merging/exporting as needed.

        Return measured line coverage, the HTML entry and a viewable coverage database path:
            from .models import MetricSummary

            return CodeCoverageResult(
                simulator=records[0].simulator,
                metrics={"line": MetricSummary(
                    covered=80, total=100, uncovered=20, rate=80.0,
                    available=True, details_available=True,
                )},
                detail="line_dat/native/uvs/index.html",
                databases=["/work/demo/coverage_database"],
            )

        The numbers above illustrate the format; replace them with real statistics.
        Keep HTML and its assets inside output_dir. detail is relative to the main
        index.html; use common.html_report_detail() instead of hardcoding directories.
        databases contains one absolute path to an actual viewable coverage file or directory.
        The UVS coverage format and UVD coverage CLI are not verified here; the
        example path is a placeholder, not a claimed filename extension. USDB
        waveform output must not be assumed to be a coverage database.
        Use detail="" without HTML, or databases=[] without an application database.
        """
        raise NotImplementedError("uvs session coverage is not implemented")

    def build_details(
        self, result: CodeCoverageResult, output_dir: Path,
    ) -> list[HtmlDetail | ApplicationDetail]:
        """Part 2: turn artifact paths into descriptors for the main report's Detail column.

        Example return format:
            [
                HtmlDetail(path="line_dat/native/uvs/index.html", label="HTML"),
                ApplicationDetail(
                    viewer="uvd", label="UVD",
                    artifacts={"database": "/work/demo/coverage_database"},
                ),
            ]

        If you already have a viewer page, return its URL directly instead:
            ApplicationDetail(
                viewer="uvd", label="UVD", mode="xpra",
                href="https://viewer.example/sessions/123/view",
            )
        href may also be relative to index.html, e.g. "apps/uvd.html".
        It must open a page, not a POST-only session-creation API endpoint.
        Toffee validates and displays the URL without calling a resolver.
        mode="xpra" shows it only with --report-custom=coverage-gui-xpra; HTML always shows.

        For click-to-launch Xpra, generate your own page or use the optional helper:
            from .xpra import write_xpra_link
            href = write_xpra_link(output_dir, ["uvd", "/work/demo/coverage_database"], label="UVD")
            ApplicationDetail(viewer="uvd", label="UVD", href=href, mode="xpra")
        Replace argv with your application's actual invocation. The page calls the
        host's generic POST /api/xpra/sessions with command/mode/name/cwd; UCAgent
        needs no simulator-specific configuration. Do not start sessions while
        generating reports. Existing session URLs must remain valid when opened.

        Without href, viewer/artifacts above are used by the native handler only
        (or an explicitly supplied custom resolver). Xpra needs a provider URL.
        This scaffold does not guess the real UVD CLI; add its link once known.
        Return only available artifacts. label is the main report's link text.
        """
        if result.error:
            return []
        if result.details:
            return list(result.details)
        details = []
        if result.detail:
            details.append(HtmlDetail(path=result.detail, label="HTML"))
        if result.databases:
            details.append(ApplicationDetail(
                viewer="uvd", label="UVD",
                artifacts={"database": result.databases[0]},
                scope=result.report_scope or "Full native database",
            ))
        return details
