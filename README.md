# toffee-test

[![PyPI version](https://badge.fury.io/py/toffee-test.svg)](https://badge.fury.io/py/toffee-test)

[English Version](README.md) | [中文版本](README_zh.md)

\`toffee-test\` is a pytest plugin for the Toffee framework. It provides test-case
registration, DUT and coverage resource management, and HTML/JSON reports.

## Installation

Install Toffee and its dependencies first:

\`\`\`bash
pip install toffee-test
\`\`\`

Install from source:

\`\`\`bash
git clone https://github.com/XS-MLVP/toffee-test.git
cd toffee-test
pip install .
\`\`\`

## Usage

### Resources and test cases

Use \`toffee_request\` to create DUT resources and
\`@toffee_test.testcase\` to register a test:

\`\`\`python
import toffee_test

@toffee_test.fixture
def my_fixture(toffee_request: toffee_test.ToffeeRequest):
    return toffee_request.create_dut(MyDUT, "clock_pin_name")

@toffee_test.testcase
def test_adder(my_fixture):
    ...
\`\`\`

\`create_dut\` accepts a DUT class, clock name, waveform filename, and coverage
filename. \`add_cov_groups\` registers coverage groups and an optional sampling
period.

### Reports

Add \`--toffee-report\` to generate a report. Use \`--report-name\` and
\`--report-dir\` to control its name and location:

\`\`\`bash
pytest --toffee-report --report-dir reports
\`\`\`

The final report's **Line Coverage → View Details** link opens the HTML entry
provided by the coverage adapter. VCS uses its native URG dashboard; Verilator
uses its LCOV page. Keep the complete report directory and native HTML assets
together when publishing the report. If no lines are available, the summary
displays \`N/A\`.

Adapters return a validated relative detail URL through
\`CodeCoverageResult(detail=...)\`. They may also return \`report_metrics\` and
\`report_scope\`; the report template does not contain simulator-specific
branches.

### Per-test coverage

Add \`--case-coverage\` together with \`--toffee-report\`:

\`\`\`bash
pytest --toffee-report --case-coverage
\`\`\`

VCS writes \`coverage_data/testcases.json\`, per-test observations under
\`coverage_data/cases/\`, \`coverage_data/cumulative.json\`, and
\`coverage_data/catalog.json\`. It supports line, branch, toggle, condition,
and FSM coverage. Verilator keeps its existing per-test coverage files.

Coverage summaries include every registered hardware coverage record, including
tests that fail assertions. A run with no registered records displays \`N/A\`.

For VCS, call \`set_line_coverage(request, selected_path, ignore=...)\`, then
call \`dut.Finish()\`. During teardown, the reporter associates the fixture DUT,
selected VCS testdata, \`GetCoveragePath()\`, and the generated
\`coverage/coverage\` helper. Outside a DUT fixture, pass \`dut=dut\` explicitly;
\`dut=None\` retains raw Verilator registration. Old DUT packages without the
metadata/helper interface must be regenerated.

VCS queries invoke the generated helper through its schema-version-1 JSON
protocol with a 120-second timeout and do not require an xcomm
\`CoverageClient\` binding. Query failures and incompatible schemas are reported
explicitly.

### Failure details

With \`--report-dump-json\`, failed entries in \`tests[].phases[]\` contain an
\`exception\` object with \`phase\`, \`type\`, and \`message\`. These fields
survive \`pytest-forked\` and xdist transport; messages are limited to 2,000
characters.

## Development

\`\`\`bash
pytest -q tests/test_coverage_fixture.py
\`\`\`

For native VCS acceptance, set \`TOFFEE_NATIVE_DUT_DIR\` to a freshly compiled
coverage-enabled Adder package and run:

\`\`\`bash
pytest -q tests/test_coverage_native_fixture.py
\`\`\`

## Documentation

- [Coverage flow and module boundaries](docs/picker-coverage-flow.zh.md)
- [Coverage atom catalog](docs/coverage_atom_inventory.md)
