#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"
XSV_ROOT="$(cd -- "${REPO_ROOT}/.." && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
REAL_VCS_HOME="${VCS_HOME:-/home/sfangyy/EDAHome/vcs/W-2024.09-SP1}"
VDB="${VDB:-${XSV_ROOT}/picker/output/Coverage/dual_port_stack.vdb}"
SUMMARY="${SUMMARY:-${XSV_ROOT}/picker/output/Coverage/.both_ports.coverage.json}"
TESTS="${TESTS:-both_ports}"
OUTPUT_DIR="${OUTPUT_DIR:-/tmp/toffee-test-vcs-coverage}"
SOURCE_ROOT="${SOURCE_ROOT:-$(dirname -- "${VDB}")}"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

[[ -x "${REAL_VCS_HOME}/bin/urg" ]] || fail "URG not found: ${REAL_VCS_HOME}/bin/urg"
[[ -d "${VDB}/snps/coverage/db" ]] || fail "invalid VDB: ${VDB}"
[[ -f "${SUMMARY}" ]] || fail "coverage summary not found: ${SUMMARY}"
[[ -d "${SOURCE_ROOT}" ]] || fail "source root not found: ${SOURCE_ROOT}"
[[ -n "${OUTPUT_DIR}" && "${OUTPUT_DIR}" != "/" ]] || fail "unsafe OUTPUT_DIR: ${OUTPUT_DIR}"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || fail "Python not found: ${PYTHON_BIN}"

URG_VCS_HOME="${REAL_VCS_HOME}"
if [[ ! -x /bin/echo ]]; then
  # Build a read-only shadow of VCS_HOME and patch only the temporary vcs shell
  # wrapper. Vendor binaries and data remain symlinked to the real installation.
  URG_VCS_HOME="${OUTPUT_DIR}.vcs-home"
  rm -rf -- "${URG_VCS_HOME}"
  mkdir -p -- "${URG_VCS_HOME}/bin"
  for entry in "${REAL_VCS_HOME}"/*; do
    [[ "$(basename -- "${entry}")" == "bin" ]] && continue
    ln -s -- "${entry}" "${URG_VCS_HOME}/$(basename -- "${entry}")"
  done
  for entry in "${REAL_VCS_HOME}/bin"/*; do
    [[ "$(basename -- "${entry}")" == "vcs" ]] && continue
    ln -s -- "${entry}" "${URG_VCS_HOME}/bin/$(basename -- "${entry}")"
  done
  sed 's#/bin/echo#echo#g' \
    "${REAL_VCS_HOME}/bin/vcs" > "${URG_VCS_HOME}/bin/vcs"
  chmod +x "${URG_VCS_HOME}/bin/vcs"
fi

rm -rf -- "${OUTPUT_DIR}"
mkdir -p -- "${OUTPUT_DIR}"

export TOFFEE_URG_VCS_HOME="${URG_VCS_HOME}"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export VDB SUMMARY TESTS OUTPUT_DIR SOURCE_ROOT

"${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

from toffee_test.utils.code_coverage import CoverageRecord, convert_code_coverage

tests = sorted(set(filter(None, os.environ["TESTS"].split(","))))
result = convert_code_coverage(
    [
        CoverageRecord(
            simulator="vcs",
            database=os.environ["VDB"],
            test=test,
            summary=os.environ["SUMMARY"],
        )
        for test in tests
    ],
    Path(os.environ["OUTPUT_DIR"]),
    vcs_home=os.environ["TOFFEE_URG_VCS_HOME"],
    source_root=os.environ["SOURCE_ROOT"],
)
report = result.to_report_dict()
print(json.dumps(report, indent=2))

detail = Path(os.environ["OUTPUT_DIR"]).parent / report["detail"]
summary = Path(os.environ["OUTPUT_DIR"]).parent / report["summary_file"]
if not detail.is_file():
    raise SystemExit(f"detail entry was not generated: {detail}")
if not summary.is_file():
    raise SystemExit(f"stable summary was not generated: {summary}")

code_coverage = json.loads(summary.read_text(encoding="utf-8"))
if code_coverage.get("schema_version") != 1:
    raise SystemExit("unexpected code coverage schema")
if code_coverage["selection"]["tests"] != tests:
    raise SystemExit("selected testdata do not match the current session")
for name, total in code_coverage["overview"]["total"].items():
    covered = code_coverage["overview"]["covered"][name]
    miss = code_coverage["overview"]["miss"][name]
    if covered + miss != total:
        raise SystemExit(f"invalid {name} summary: {covered} + {miss} != {total}")
for file_data in code_coverage["uncovered"]["data"].values():
    for module_data in file_data["modules"].values():
        for line_range in module_data["line"]:
            start, end = map(int, line_range.split("-", 1))
            if start > end:
                raise SystemExit(f"invalid uncovered line range: {line_range}")
PY

printf '\nVCS coverage converter passed.\n'
printf 'HTML entries:\n'
find "${OUTPUT_DIR}/vcs_urg" -type f -name '*.html' -print | sort | sed -n '1,10p'
printf 'stdout: %s\n' "${OUTPUT_DIR}/urg.stdout.log"
printf 'stderr: %s\n' "${OUTPUT_DIR}/urg.stderr.log"
