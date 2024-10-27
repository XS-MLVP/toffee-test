import os
import re
import subprocess


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


def convert_line_coverage(dat_file, output_dir):
    if isinstance(dat_file, list):
        for f in dat_file:
            assert os.path.exists(f), f"File not found: {f}"
        dat_file = " ".join(dat_file)
    else:
        assert os.path.exists(dat_file), f"File not found: {dat_file}"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    merged_info = os.path.join(output_dir, "merged.info")
    su, so, se = exe_cmd(["verilator_coverage  -write-info", merged_info, dat_file])
    assert su, f"Failed to convert line coverage: {se}"
    su, so, se = exe_cmd(["genhtml", merged_info, "-o", output_dir])
    assert su, f"Failed to convert line coverage: {se}"
    return parse_lines(so)
