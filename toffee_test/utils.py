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


def convert_line_coverage(line_coverage_list, output_dir):
    assert isinstance(line_coverage_list, list), "Invalid line coverage list"
    coverage_info_list = []
    for ldata in line_coverage_list:
        fdat = ldata["data"]
        fign = ldata["ignore"]
        if isinstance(fign, str):
            fign = [fign]
        assert isinstance(fign, list), f"Invalid data file: '{fign}'"
        assert os.path.exists(fdat), f"Invalid data file: '{fdat}'"
        su, so, se = exe_cmd(["verilator_coverage  -write-info", fdat+".info", fdat])
        assert su, f"Failed to convert line coverage({fdat}): {se}"
        ignore_text = []
        # find all .ignore files
        ignore_file_list = []
        for f in fign:
            if os.path.isdir(f):
                for root, _, files in os.walk(f):
                    for file in files:
                        if file.endswith(".ignore"):
                            ignore_file_list.append(os.path.join(root, file))
            else:
                assert os.path.exists(f), f"Not find ignore file: '{f}'"
                ignore_file_list.append(f)
        for ignore_file in ignore_file_list:
            for i, ln in enumerate(open(ignore_file).readlines()):
                ln = ln.strip()
                if ln.startswith("#"):
                    continue
                for c in ["'", "\""]:
                    assert c not in ln, f"Invalid line number({i}): '{ln}'"
                ignore_text.append(f"\'{ln}\'")
        su, so, se = exe_cmd(
                    ["lcov", "--remove", fdat+".info", " ".join(ignore_text), "--output-file", fdat+".info"]
                )
        assert su, f"Failed to remove line with file: '{ignore_file}'"
        coverage_info_list.append("'%s'" % fdat+".info")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    merged_info = os.path.join(output_dir, "merged.info")
    merged_args = []
    for c in coverage_info_list:
        merged_args.extend(["-a", c])
    su, so, se = exe_cmd(["lcov", *merged_args, "--output-file", merged_info])
    assert su, f"Failed to merge line coverage: {se}"
    su, so, se = exe_cmd(["genhtml", merged_info, "-o", output_dir])
    assert su, f"Failed to convert line coverage: {se}"
    return parse_lines(so)
