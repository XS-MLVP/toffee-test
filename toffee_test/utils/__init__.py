import os
import re
import subprocess
import json
import base64



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


def convert_line_coverage(line_coverage_list: list[dict], output_dir):
    from .verilator_coverage import convert_verilator_coverage
    merged_info ,final_ignore_info = convert_verilator_coverage(line_coverage_list, output_dir)
    su, so, se = exe_cmd(["genhtml", "--branch-coverage", merged_info, "-o", output_dir])
    assert su, f"Failed to convert line coverage: {se}"
    return parse_lines(so), final_ignore_info

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
