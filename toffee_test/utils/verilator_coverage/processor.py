__all__ = [
    "preprocess_verilator_coverage",
    "filter_coverage",
    "verilator_coverage_miss",
]

import fnmatch
import json
import os
from concurrent.futures import as_completed, ThreadPoolExecutor
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterable, Counter

from .models import VerilatorCoverage, MetricStats, ModuleCoverage, FileCoverage


def merge_intervals(intervals: Iterable[int]) -> list[range]:
    if not intervals:
        return []
    sorted_intervals = sorted(intervals)
    final: list[range] = []
    i = 0
    while i < len(sorted_intervals):
        l = sorted_intervals[i]
        j = i
        while j + 1 < len(sorted_intervals) and sorted_intervals[j + 1] == sorted_intervals[j] + 1:
            j += 1
        r = sorted_intervals[j]
        final.append(range(l, r + 1))
        i = j + 1
    return final


def _merge_consecutive_lines(lines: set[int]) -> tuple[str, ...]:
    sorted_lines = merge_intervals(lines)
    return tuple(f"{x.start}-{x.stop - 1}" for x in sorted_lines)


def verilator_coverage_miss(merged_coverage: list[tuple[VerilatorCoverage, int]], out_file: str) -> None:
    # Parse missing coverage
    coverages = merged_coverage
    total = MetricStats()
    miss = MetricStats()
    # Parse file
    collect_lines: dict[tuple[str, int], int] = {}
    collect_modules: dict[tuple[str, int], str] = {}
    # file_lines_hit: dict[str, set[int]] = {}
    miss_tree: dict[str, FileCoverage] = {}
    for meta, hit in coverages:
        module_name = meta.module_name
        # Set default
        file_cov = miss_tree.setdefault(meta.path, FileCoverage())
        module_cov = file_cov.modules.setdefault(module_name, ModuleCoverage())
        # Update
        is_miss = hit == 0
        if meta.type == "line":
            # if hit > 0:
            #     file_lines_hit.setdefault(meta.path, set())
            #     file_lines_hit[meta.path].add(meta.line)
            # for block_line in meta.block_set:
            #     if block_line == meta.line:
            #         continue
            #     if hit > 0:
            #         file_lines_hit[meta.path].add(block_line)
            pass
        elif meta.type == "toggle":
            total.toggle += 1
            file_cov.total.toggle += 1
            module_cov.total.toggle += 1
            miss.toggle += is_miss
            file_cov.miss.toggle += is_miss
            module_cov.miss.toggle += is_miss
            # file_lines_hit.setdefault(meta.path, set())
            # if hit > 0 and meta.line not in file_lines_hit[meta.path]:
            #     file_lines_hit[meta.path].add(meta.line)
        elif meta.type == "branch":
            total.branch += 1
            file_cov.total.branch += 1
            module_cov.total.branch += 1
            miss.branch += is_miss
            file_cov.miss.branch += is_miss
            module_cov.miss.branch += is_miss
            # if hit > 0:
            #     file_lines_hit.setdefault(meta.path, set())
            #     file_lines_hit[meta.path].add(meta.line)
            #     for block_line in meta.block_set:
            #         if block_line == meta.line:
            #             continue
            #         file_lines_hit[meta.path].add(block_line)
        elif meta.type == "expr":
            total.expr += 1
            file_cov.total.expr += 1
            module_cov.total.expr += 1
            miss.expr += is_miss
            file_cov.miss.expr += is_miss
            module_cov.miss.expr += is_miss
            # if hit > 0:
            #     file_lines_hit.setdefault(meta.path, set())
            #     file_lines_hit[meta.path].add(meta.line)
            #     for block_line in meta.block_set:
            #         if block_line == meta.line:
            #             continue
            #         file_lines_hit[meta.path].add(block_line)
        # Record the hit results of line coverage
        key = (meta.path, meta.line)
        old = collect_lines.get(key, 0)
        collect_lines[key] = old + hit
        collect_modules[key] = meta.module_name
        for block_line in meta.block_set:
            if block_line == meta.line:
                continue
            block_key = (meta.path, block_line)
            old = collect_lines.get(block_key, 0)
            collect_lines[block_key] = old + hit
            collect_modules[block_key] = meta.module_name
        if is_miss:
            metric_set = module_cov.get_metric_set(meta.type)
            if meta.block:
                s = meta.block_set
                metric_set.update(s)
            else:
                metric_set.add(meta.line)
    # Update line coverage
    for (file, line), hit in collect_lines.items():
        module_name = collect_modules[(file, line)]
        file_cov = miss_tree.setdefault(file, FileCoverage())
        module_cov = file_cov.modules.setdefault(module_name, ModuleCoverage())
        total.line += 1
        file_cov.total.line += 1
        module_cov.total.line += 1
        if hit > 0:
            continue
        miss.line += 1
        file_cov.miss.line += 1
        module_cov.miss.line += 1
        module_cov.line.add(line)
    final_miss: dict[str, dict[str, dict[str, dict]]] = {}
    for path, file_cov in miss_tree.items():
        final_miss[path] = {
            "total": asdict(file_cov.total),
            "miss": asdict(file_cov.miss),
            "modules": {},
            # "line_hit": _merge_consecutive_lines(file_lines_hit[path]),
        }
        for module_name, module_cov in file_cov.modules.items():
            final_miss[path]["modules"][module_name] = {
                "total": asdict(module_cov.total),
                "miss": asdict(module_cov.miss),
                "line": _merge_consecutive_lines(module_cov.line),
                "toggle": _merge_consecutive_lines(module_cov.toggle),
                "branch": _merge_consecutive_lines(module_cov.branch),
                "expr": _merge_consecutive_lines(module_cov.expr),
            }
    metric_fields = [x.name for x in fields(MetricStats)]
    miss_schema = {
        "file_path": {
            "total": metric_fields,
            "miss": metric_fields,
            "modules": {
                "module_name": {
                    "total": metric_fields,
                    "miss": metric_fields,
                    "line": ["lines"],
                    "toggle": ["lines"],
                    "branch": ["lines"],
                    "expr": ["lines"],
                }
            }
        },
    }

    desc = f"Code coverage summary. `data` field contains uncovered lines"
    empty_coverage = {
        "description": desc,
        "simulator": "verilator",
        "overview": {
            "total": asdict(total),
            "miss": asdict(miss),
        },
        "uncovered": {
            "schema": miss_schema,
            "data": final_miss,
        },
    }
    # Export json
    with open(out_file, "w") as f:
        json.dump(empty_coverage, f, indent=2)


def get_range_filter(path: str, ignore_miss_line_ranges: dict[str, set[int]]) -> set[int]:
    if path in ignore_miss_line_ranges:
        return ignore_miss_line_ranges[path]
    final = set()

    for k, v in ignore_miss_line_ranges.items():
        if k[0] != '/' and path.endswith(k):
            final.update(v)

    return final


def filter_ranges(input_ranges: list[range], range_filters: set[int]) -> list[range]:
    raw_range = set()
    for r in input_ranges:
        raw_range.update(r)
    if len(raw_range) < len(range_filters):
        cooked = raw_range - range_filters
    else:
        cooked = raw_range.difference(range_filters)
    res = merge_intervals(cooked)
    return res


def filter_coverage(
        merged_coverage: list[tuple[VerilatorCoverage, int]],
        ignore_patterns: set[str],
        ignore_miss_line_ranges: dict[str, set[int]],
) -> list[tuple[VerilatorCoverage, int]]:
    filtered_coverage: list[tuple[VerilatorCoverage, int]] = []

    for meta, hit in merged_coverage:
        # Remove files to be filtered
        is_ignore = False
        for pat in ignore_patterns:
            if fnmatch.fnmatch(meta.path, pat):
                is_ignore = True
                break
        if is_ignore:
            continue
        # Only ignore miss record
        if hit != 0:
            filtered_coverage.append((meta, hit))
            continue
        # Remove miss lines to be filtered
        range_filter = get_range_filter(meta.path, ignore_miss_line_ranges)
        if range_filter:
            if meta.block:
                new_blocks = filter_ranges(meta.block, range_filter)
                # Ignore the empty miss line
                if not new_blocks:
                    continue
                meta.block = new_blocks
            elif meta.line in range_filter:
                continue
        filtered_coverage.append((meta, hit))
    return filtered_coverage


def parse_ignore_miss_lines(pat: str, miss_lines: dict[str, set[int]]) -> bool:
    nu_start = pat.rfind(":")
    if not pat.endswith(":") and nu_start != -1:
        file = pat[:nu_start]
        range_str = pat[nu_start + 1:].split(",")
        # Process ranges to list
        miss_lines.setdefault(file, set())
        to_be_ignored = miss_lines[file]
        for s in range_str:
            if "-" in s:
                l, r = map(int, s.split("-"))
                to_be_ignored.update(range(l, r + 1))
            else:
                l = int(s)
                to_be_ignored.add(l)
        return True
    return False


def process_coverage_list(coverage_list: Iterable[dict]) -> tuple[
    list[str],
    list[tuple],
    set[str],
    dict[str, set[int]]
]:
    dat_files: list[str] = []
    ignore_infos: list[tuple] = []
    ignore_patterns: set[str] = set()
    ignore_miss_lines: dict[str, set[int]] = {}

    for line_data in coverage_list:
        # Handle dat file
        dat_file = line_data["data"]
        assert os.path.exists(dat_file), f"Invalid data file: '{dat_file}'"
        dat_files.append(dat_file)
        # Handle ignore
        ignore_items = line_data["ignore"]
        assert isinstance(ignore_items, list), f"Invalid ignore list: '{ignore_items}'"
        # find all .ignore files
        ignore_file_list = []
        for f in ignore_items:
            path = Path(f)
            if parse_ignore_miss_lines(f, ignore_miss_lines):
                continue
            elif path.is_dir():
                ignores = path.rglob("*.ignore")
                ignore_file_list.extend(ignores)
            elif path.is_file():
                ignore_file_list.append(f)
            else:
                ignore_patterns.add(f)
        # Parse the content of ignore file
        explicit_patterns = list(ignore_patterns)
        process_ignore_files(ignore_file_list, ignore_patterns, ignore_miss_lines)
        ignore_infos.append((dat_file, ignore_file_list, explicit_patterns))

    return dat_files, ignore_infos, ignore_patterns, ignore_miss_lines


def process_ignore_files(
        ignore_files: Iterable[str],
        patterns: set[str],
        miss_lines: dict[str, set[int]]
) -> None:
    for ignore_file in ignore_files:
        for i, ln in enumerate(open(ignore_file).readlines()):
            ln = ln.strip()
            # Process comment
            comment_start = ln.find("#")
            if ln.startswith("#"):
                continue
            elif comment_start != -1:
                ln = ln[:comment_start].rstrip()
            # Process line numbers to be ignored
            if parse_ignore_miss_lines(ln, miss_lines):
                continue
            for c in ["'", "\""]:
                assert c not in ln, f"Invalid line number({i}): '{ln}'"
            patterns.add(ln)


def count_verilator_coverage_hit(path: str) -> Counter:
    verilator_coverage_dat = Counter()
    with open(path, "r") as f:
        first_line = f.readline().strip()
        if first_line != r"# SystemC::Coverage-3":
            # Invalid verilator coverage file
            return verilator_coverage_dat
        for line in f:
            line = line.strip()
            l = line.find(" ")
            r = line.rfind(" ")
            entry = line[l + 1:r]
            hit = line[r + 1:]
            verilator_coverage_dat[entry] += int(hit)
        return verilator_coverage_dat


def merge_verilator_coverage(coverage_files: Iterable[str]) -> list[tuple[VerilatorCoverage, int]]:
    c = Counter()
    with ThreadPoolExecutor() as pool:
        futures = {pool.submit(count_verilator_coverage_hit, f) for f in coverage_files}
        for future in as_completed(futures):
            res = future.result()
            c.update(res)
    coverages: list[tuple[VerilatorCoverage, int]] = [
        (VerilatorCoverage(cov), hit) for cov, hit in c.items()
    ]
    coverages.sort()
    return coverages


def preprocess_verilator_coverage(
        line_coverage_list: list[dict]
) -> tuple[list[tuple[VerilatorCoverage, int]], list[tuple], set[str], dict[str, set[int]]]:
    dat_list, ignore_info, ignore_patterns, ignore_miss_lines = process_coverage_list(line_coverage_list)
    # Merge coverage data first
    merged_coverage = merge_verilator_coverage(dat_list)
    return merged_coverage, ignore_info, ignore_patterns, ignore_miss_lines
