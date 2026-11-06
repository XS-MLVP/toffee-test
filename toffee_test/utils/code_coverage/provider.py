"""Coverage provider contract and adapter for existing function registrations."""

from pathlib import Path

from .models import CodeCoverageResult, CoverageRecord, HtmlDetail


class CoverageProvider:
    """Own simulator conversion; presentation and application hosting stay shared."""

    # Picker SIMULATOR / GetSimulator() values accepted by this provider.
    simulator_names: tuple[str, ...] = ()

    def convert_case(self, record: CoverageRecord, output_dir: Path, **options) -> CodeCoverageResult:
        """Convert one testcase, or explicitly report an unsupported operation."""
        raise NotImplementedError(f"{record.simulator} testcase coverage is not implemented")

    def convert_session(self, records: list[CoverageRecord], output_dir: Path, **options) -> CodeCoverageResult:
        """Convert the selected session using the simulator's native toolchain."""
        raise NotImplementedError(f"{records[0].simulator} session coverage is not implemented")

    def build_details(self, result: CodeCoverageResult, output_dir: Path) -> list:
        """Use explicit descriptors, falling back to the legacy HTML entry."""
        return list(result.details) or ([HtmlDetail(path=result.detail)] if result.detail else [])


class FunctionCoverageProvider(CoverageProvider):
    """Keep function-based third-party registrations working unchanged."""

    def __init__(self, case_converter, session_converter):
        """Retain the two public conversion callables."""
        self.case_converter = case_converter
        self.session_converter = session_converter

    def convert_case(self, record, output_dir, **options):
        """Forward testcase conversion without changing provider options."""
        return self.case_converter(record, output_dir, **options)

    def convert_session(self, records, output_dir, **options):
        """Forward session conversion without changing provider options."""
        return self.session_converter(records, output_dir, **options)
