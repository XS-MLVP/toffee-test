"""Discover files from one completed test report without directory conventions."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .utils.code_coverage.artifacts import get_coverage_artifact_path


def gate_toffee_test_file(
    report_file: str | Path, file_type: str = "coverage_json",
) -> Path | None:
    """Return an existing file declared by the specified test report.

    ``report_file`` is the exact JSON report path known by the test executor.
    Supported types are ``report_json``, ``coverage_json`` (the full, unmodified
    checker input), and ``lcov``. Paths are resolved from report metadata on
    every call, never cached or discovered by scanning directories.

    Return None when coverage or the requested artifact was not provided. Raise
    FileNotFoundError for a declared missing file, ValueError for malformed data
    or escaping paths, and RuntimeError for a recorded coverage conversion error.
    This function reads files only; derived checker statistics must be written to
    a separate file rather than replacing the returned coverage JSON.
    """
    if file_type not in ("report_json", "coverage_json", "lcov"):
        raise ValueError(f"unsupported Toffee report file type: {file_type!r}")
    report_file = Path(os.path.abspath(report_file))
    with report_file.open(encoding="utf-8") as source:
        report = json.load(source)
    if not isinstance(report, dict):
        raise ValueError("Toffee test report must be a JSON object")
    if file_type == "report_json":
        return report_file
    coverages = report.get("coverages")
    if not isinstance(coverages, dict) or not isinstance(coverages.get("line"), dict):
        raise ValueError("Toffee report must declare coverages and coverages.line objects")
    line = coverages["line"]
    error = line.get("error")
    if error:
        raise RuntimeError(f"Toffee coverage conversion failed: {error}")
    reference = line.get("coverage_json", "")
    if reference == "":
        return None
    if not isinstance(reference, str) or "\0" in reference or Path(reference).is_absolute():
        raise ValueError("coverages.line.coverage_json must be a nonempty report-relative path")
    root = report_file.parent.resolve()
    summary = Path(os.path.abspath(report_file.parent / reference))
    if not summary.resolve().is_relative_to(root):
        raise ValueError("coverage JSON must remain inside the report directory")
    if not summary.is_file():
        raise FileNotFoundError(f"Declared coverage JSON is missing: {summary}")
    # Validate the contract even when only the JSON itself is requested.
    lcov = get_coverage_artifact_path(summary, "lcov")
    if file_type == "coverage_json":
        return summary
    if lcov is None:
        return None
    if not lcov.resolve().is_relative_to(root):
        raise ValueError("LCOV artifact must remain inside the report directory")
    if not lcov.is_file():
        raise FileNotFoundError(f"Declared LCOV artifact is missing: {lcov}")
    return lcov
