__all__ = [
    "preprocess_verilator_coverage",
    "filter_coverage",
    "verilator_coverage_miss",
]

import fnmatch
import heapq
import json
import math
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterable, Counter

from .models import VerilatorCoverage, MetricStats, ModuleCoverage, FileCoverage, CoverageSummary


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


def verilator_coverage_miss(merged_coverage: list[tuple[VerilatorCoverage, int]], out_file: str) -> CoverageSummary:
    # Parse missing coverage
    coverages = merged_coverage
    total = MetricStats()
    miss = MetricStats()
    # Parse file
    collect_lines: defaultdict[tuple[str, int], int] = defaultdict(int)
    collect_modules: dict[tuple[str, int], str] = {}
    # file_lines_hit: dict[str, set[int]] = {}
    miss_tree: dict[str, FileCoverage] = {}
    for meta, hit in coverages:
        path = meta.path
        module_name = meta.module_name
        # setdefault() would construct a throwaway default on every iteration
        file_cov = miss_tree.get(path)
        if file_cov is None:
            file_cov = miss_tree[path] = FileCoverage()
        module_cov = file_cov.modules.get(module_name)
        if module_cov is None:
            module_cov = file_cov.modules[module_name] = ModuleCoverage()
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
        key = (path, meta.line)
        collect_lines[key] += hit
        collect_modules[key] = meta.module_name
        for block_line in meta.block_set:
            if block_line == meta.line:
                continue
            block_key = (path, block_line)
            collect_lines[block_key] += hit
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
        file_cov = miss_tree.get(file)
        if file_cov is None:
            file_cov = miss_tree[file] = FileCoverage()
        module_cov = file_cov.modules.get(module_name)
        if module_cov is None:
            module_cov = file_cov.modules[module_name] = ModuleCoverage()
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
    sim = "verilator"
    overview = {
        "total": asdict(total),
        "miss": asdict(miss),
    }
    uncovered = {
        "schema": miss_schema,
        "data": final_miss,
    }
    summary = CoverageSummary(
        desc,
        sim,
        overview,
        uncovered
    )
    # Export json
    with open(out_file, "w") as f:
        json.dump(summary._asdict(), f, indent=2)
    return summary


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


def _extract_path(raw_entry: str) -> str:
    # Fast extraction of the 'f' (file path) field, avoids full entry parsing
    i = raw_entry.find("\x01f\x02")
    if i == -1:
        return ""
    j = raw_entry.find("\x01", i + 3)
    if j == -1:
        return raw_entry[i + 3:]
    return raw_entry[i + 3:j]


def filter_coverage(
        merged_entries: list[tuple[str, int]],
        ignore_patterns: set[str],
        ignore_miss_line_ranges: dict[str, set[int]],
) -> list[tuple[VerilatorCoverage, int]]:
    """Filter merged raw entries and return the sorted (VerilatorCoverage, hit) list.

    Entries are kept as raw strings until they survive the ignore checks, so
    ignored entries are never parsed into VerilatorCoverage objects.
    """
    filtered_coverage: list[tuple[VerilatorCoverage, int]] = []
    # The same path is shared by a large number of entries, cache per-path decisions
    path_ignore_cache: dict[str, bool] = {}
    path_range_cache: dict[str, set[int]] = {}

    for raw_entry, hit in merged_entries:
        path = _extract_path(raw_entry)
        # Remove files to be filtered
        is_ignore = path_ignore_cache.get(path)
        if is_ignore is None:
            is_ignore = any(fnmatch.fnmatch(path, pat) for pat in ignore_patterns)
            path_ignore_cache[path] = is_ignore
        if is_ignore:
            continue
        meta = VerilatorCoverage(raw_entry)
        # Only ignore miss record
        if hit != 0:
            filtered_coverage.append((meta, hit))
            continue
        # Remove miss lines to be filtered
        if path in path_range_cache:
            range_filter = path_range_cache[path]
        else:
            range_filter = get_range_filter(path, ignore_miss_line_ranges)
            path_range_cache[path] = range_filter
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

    # Full-field key: ties beyond (path, line, ..., hit) are ordered by content,
    # so the output does not depend on the (possibly parallel) merge order
    filtered_coverage.sort(key=lambda x: (
        x[0].path, x[0].line, x[0].column, x[0].type, x[0].module_name, x[1],
        x[0].comment, x[0].hierarchy, x[0].block,
    ))
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
            # Line format: C '<entry>' <hit>; int() tolerates the trailing newline
            l = line.find(" ")
            r = line.rfind(" ")
            entry = line[l + 1:r]
            verilator_coverage_dat[entry] += int(line[r + 1:])
        return verilator_coverage_dat


# Calibration for the serial-vs-parallel parsing decision, measured with 37MB /
# ~400k-line verilator coverage dat files:
_PARSE_BYTES_PER_SEC = 2.0e8                # single-core parse throughput (measured ~2.5e8)
_WORKER_COUNTER_OVERHEAD_S = 0.1            # parent-side unpickle + Counter.merge per ~400k-entry counter
_MIN_PARALLEL_TOTAL_BYTES = 200 * 1024 ** 2  # below this, pool startup + merging never pay off
_MAX_PARALLEL_WORKERS = 8


def _available_cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))  # respects container cpu limits on Linux
    except AttributeError:  # macOS / Windows
        return os.cpu_count() or 1


def _plan_workers(coverage_files: list[str]) -> int:
    """Decide how many worker processes to parse with; 1 means stay serial.

    Parallelism only pays off when the estimated serial parse time clearly
    exceeds the fixed costs (pool startup plus parent-side counter merging),
    so require a minimum total size. The worker count balances parse wall time
    against per-worker result overhead: roughly sqrt(serial_time / overhead).
    """
    if len(coverage_files) < 2:
        return 1
    try:
        total = sum(os.path.getsize(f) for f in coverage_files)
    except OSError:
        return 1
    if total < _MIN_PARALLEL_TOTAL_BYTES:
        return 1
    est_serial_s = total / _PARSE_BYTES_PER_SEC
    optimal = math.ceil(math.sqrt(est_serial_s / _WORKER_COUNTER_OVERHEAD_S))
    return max(2, min(optimal, _MAX_PARALLEL_WORKERS, _available_cpus(), len(coverage_files)))


def _parse_merge_group(group: list[str]) -> Counter:
    c = Counter()
    for f in group:
        c.update(count_verilator_coverage_hit(f))
    return c


def _merge_parallel(coverage_files: list[str], workers: int) -> list[tuple[str, int]]:
    # Longest-processing-time-first assignment keeps worker groups size-balanced;
    # the (cum_size, index) heap key makes the grouping deterministic.
    sized = sorted(((os.path.getsize(f), f) for f in coverage_files), reverse=True)
    heap = [(0, i, []) for i in range(workers)]
    for size, f in sized:
        cum, idx, group = heapq.heappop(heap)
        group.append(f)
        heapq.heappush(heap, (cum + size, idx, group))
    groups = [group for _, _, group in heap]
    merged = Counter()
    # pool.map yields results in input order, so the merge order is deterministic
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for partial in pool.map(_parse_merge_group, groups):
            merged.update(partial)
    return list(merged.items())


def merge_verilator_coverage(coverage_files: Iterable[str]) -> list[tuple[str, int]]:
    """Merge raw coverage entries of all dat files into [(raw_entry, hit), ...]"""
    coverage_files = list(coverage_files)
    workers = _plan_workers(coverage_files)
    if workers > 1:
        try:
            return _merge_parallel(coverage_files, workers)
        except Exception:
            # Environments without multiprocessing support (e.g. sandboxed CI)
            # fall back to the serial path, which produces identical results
            pass
    # Parsing is CPU-bound (no speedup from threads under the GIL), keep it serial
    c = Counter()
    for f in coverage_files:
        c.update(count_verilator_coverage_hit(f))
    return list(c.items())


def preprocess_verilator_coverage(
        line_coverage_list: list[dict]
) -> tuple[list[tuple[str, int]], list[tuple], set[str], dict[str, set[int]]]:
    dat_list, ignore_info, ignore_patterns, ignore_miss_lines = process_coverage_list(line_coverage_list)
    # Merge coverage data first
    merged_entries = merge_verilator_coverage(dat_list)
    return merged_entries, ignore_info, ignore_patterns, ignore_miss_lines
