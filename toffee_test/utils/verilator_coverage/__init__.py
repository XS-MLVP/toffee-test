__all__ = ["convert_verilator_coverage"]

import os
import subprocess
from pathlib import Path

from .models import VerilatorCoverage


def convert_verilator_coverage(line_coverage_list: list[dict], output_dir) -> tuple[str, list]:
    from .processor import (
        preprocess_verilator_coverage,
        filter_coverage,
        verilator_coverage_miss,
    )

    assert isinstance(line_coverage_list, list), "Invalid line coverage list"
    merged_coverage, ignore_info, ignore_pattern, ignore_miss_range = preprocess_verilator_coverage(line_coverage_list)
    # Sort miss_range
    filtered_coverage = filter_coverage(merged_coverage, ignore_pattern, ignore_miss_range)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    verilator_coverage_miss(filtered_coverage, os.path.join(output_dir, "code_coverage.json"))
    merged_info = os.path.join(output_dir, "merged.info")
    verilator_coverage_to_lcov(filtered_coverage, merged_info)

    return merged_info, ignore_info


def verilator_coverage_to_lcov(coverages: list[tuple[VerilatorCoverage, int]], outfile: str):
    outfile_dat = Path(outfile).with_suffix(".dat")
    with outfile_dat.open("w", encoding="utf-8") as f:
        f.write("# SystemC::Coverage-3\n")
        for e, h in coverages:
            f.write(f"C '{e}' {h}\n")
    cmd = [
        "verilator_coverage",
        "-write-info", outfile, outfile_dat,
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, shell=False, check=True)
