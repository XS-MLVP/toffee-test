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


def convert_line_coverage(dat_file, output_dir, ignore_dirs=[]):
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
    for ignore in ignore_dirs:
        if not os.path.exists(ignore):
            continue
        ignore_file_list = []
        if os.path.isfile(ignore):
            ignore_file_list.append(ignore)
        elif os.path.isdir(ignore):
            for root, _, files in os.walk(ignore):
                for f in files:
                    if f.endswith(".ignore"):
                        ignore_file_list.append(os.path.join(root, f))
        for ignore_file in ignore_file_list:
            ignore_text = []
            for i, ln in enumerate(open(ignore_file).readlines()):
                ln = ln.strip()
                if ln.startswith("#"):
                    continue
                for c in ["'", "\""]:
                    assert c not in ln, f"Invalid line number({i}): '{ln}'"
                ignore_text.append(f"\'{ln}\'")
            su, so, se = exe_cmd(
                    ["lcov", "--remove", merged_info, " ".join(ignore_text), "--output-file", merged_info]
                )
            assert su, f"Failed to remove line with file: '{ignore_file}'"
    su, so, se = exe_cmd(["genhtml", merged_info, "-o", output_dir])
    assert su, f"Failed to convert line coverage: {se}"
    return parse_lines(so)
