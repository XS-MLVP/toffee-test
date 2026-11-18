"""Resolve provider-owned artifacts referenced by coverage summary JSON files."""

from __future__ import annotations

import json
import os
from pathlib import Path


def get_coverage_artifact_path(summary_file: str | Path, artifact: str) -> Path | None:
    """Return an artifact path without requiring that artifact to exist.

    ``artifacts`` maps names to paths relative to the summary JSON, so a report
    directory can be moved intact. A missing/null entry means that artifact is
    unavailable. Every summary must explicitly provide the ``artifacts`` object;
    no filenames are inferred and no fallback to a different run is performed.
    """
    summary_file = Path(summary_file)
    with summary_file.open(encoding="utf-8") as source:
        summary = json.load(source)
    if not isinstance(summary, dict):
        raise ValueError("coverage summary must be a JSON object")
    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("coverage summary must declare an artifacts object; regenerate the report with the current Toffee version")
    value = artifacts.get(artifact)
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\0" in value or Path(value).is_absolute():
        raise ValueError(f"coverage artifact {artifact!r} must be a nonempty relative path")
    # Normalize '..' without resolving symlinks: consumers still validate the
    # actual file and can reject symlink artifacts under their own contracts.
    return Path(os.path.abspath(summary_file.parent / value))
