import os
import re
import subprocess
import copy
import json
import base64
from typing import List, Iterable

from .uncovered import verilator_coverage_miss


def exe_cmd(cmd):
    if isinstance(cmd, list):
        cmd = " ".join(cmd)

    result = subprocess.run(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    success = result.returncode == 0

    stdout = result.stdout.decode("utf-8")
    stderr = result.stderr.decode("utf-8")
    return success, stdout, stderr


def parse_lines(text: str):
    pattern = r"lines\.+: \d+\.\d+% \((\d+) of (\d+) lines\)\n"
    match = re.search(pattern, text)
    if match:
        return tuple(map(int, match.groups()))
    return -1, -1


def merge_verilator_coverage(coverage_files: list[str]) -> list[tuple[str, int]]:
    from collections import Counter
    from concurrent.futures import ProcessPoolExecutor, as_completed
    def _count_verilator_coverage_hit(path: str) -> Counter:
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

    c = Counter()
    with ProcessPoolExecutor() as executor:
        futures = {executor.submit(_count_verilator_coverage_hit, f) for f in coverage_files}
        for future in as_completed(futures):
            res = future.result()
            c.update(res)
    res = sorted(c.items())
    return res


def convert_line_coverage(line_coverage_list, output_dir):
    from pathlib import Path
    assert isinstance(line_coverage_list, list), "Invalid line coverage list"
    coverage_dat_list: list[str] = []
    ignore_files: list[str] = []
    final_ignore_info = []
    for ldata in line_coverage_list:
        fdat = ldata["data"]
        fign = ldata["ignore"]
        if isinstance(fign, str):
            fign = [fign]
        assert isinstance(fign, list), f"Invalid data file: '{fign}'"
        assert os.path.exists(fdat), f"Invalid data file: '{fdat}'"
        ignore_text = []
        # find all .ignore files
        ignore_file_list = []
        for f in fign:
            path = Path(f)
            assert path.exists(), f"Ignore file/path does not exist: '{f}'"
            if path.is_dir():
                ignores = path.rglob("*.ignore")
                ignore_file_list.extend(ignores)
            else:
                ignore_file_list.append(f)
        for ignore_file in ignore_file_list:
            for i, ln in enumerate(open(ignore_file).readlines()):
                ln = ln.strip()
                if ln.startswith("#"):
                    continue
                for c in ["'", "\""]:
                    assert c not in ln, f"Invalid line number({i}): '{ln}'"
                ignore_text.append(f"\'{ln}\'")
        coverage_dat_list.append(fdat)
        ignore_files.extend(ignore_text)
        final_ignore_info.append((fdat, copy.deepcopy(ignore_file_list)))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    merged_coverage = merge_verilator_coverage(coverage_dat_list)
    verilator_coverage_miss(merged_coverage, os.path.join(output_dir, "coverage.json"))
    merged_info = os.path.join(output_dir, "merged.info")
    lcov_remove_ingore(ignore_files, merged_info)
    su, so, se = exe_cmd(["genhtml", "--branch-coverage", merged_info, "-o", output_dir])
    assert su, f"Failed to convert line coverage: {se}"
    return parse_lines(so), final_ignore_info


def verilator_coverage_to_lcov(merged_coverage: list[tuple[str, int]], outfile: str):
    outfile_dat = ".".join((outfile, "dat"))
    with open(outfile_dat, "w", encoding="utf-8") as f:
        f.write(r"# SystemC::Coverage-3")
        for e, h in merged_coverage:
            f.write(f"C {e} {h}\n")
    cmd = [
        "verilator_coverage",
        "-write-info", outfile, outfile_dat,
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, shell=False, check=True)


def lcov_remove_ingore(ignore_list: List[str], out_file: str, max_kbytes=10) -> None:
    max_bytes = max_kbytes * 1024
    cmd_list = ["lcov", "-o", out_file, "-r", out_file]
    cmd_len = len(" ".join(cmd_list)) + 1
    to_be_ignored = []
    total_size = cmd_len
    for f in ignore_list:
        f_len = 3 + len(f)  # The length of '-a ' is 3
        if f_len + total_size > max_bytes:
            _lcov_merge(to_be_ignored, out_file)  # Merge into the temp
            total_size = cmd_len
            to_be_ignored.clear()
        total_size += f_len
        to_be_ignored.append(f)
    # There are still unmerged files here
    _lcov_merge(to_be_ignored, out_file)


def _lcov_merge(list_to_merge: Iterable[str], out_file: str) -> None:
    cmd = ["lcov", "-o", out_file, "-r", out_file]
    cmd.extend(list_to_merge)
    subprocess.run(cmd, stdout=subprocess.PIPE, shell=False, check=True)


def base64_encode(data):
    input_bytes = json.dumps(data).encode('utf-8')
    base64_bytes = base64.b64encode(input_bytes)
    base64_str = base64_bytes.decode('utf-8')
    return base64_str


def base64_decode(base64_str):
    base64_bytes = base64_str.encode('utf-8')
    input_bytes = base64.b64decode(base64_bytes)
    return json.loads(input_bytes.decode('utf-8'))


def get_toffee_custom_key_value():
    import pytest
    return getattr(pytest, "toffee_custom_key_value", {})


def set_toffee_custom_key_value(value):
    import pytest
    assert isinstance(value, dict), "Invalid custom key value"
    pytest.toffee_custom_key_value = value
