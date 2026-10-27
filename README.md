# toffee-test

[![PyPI version](https://badge.fury.io/py/toffee-test.svg)](https://badge.fury.io/py/toffee-test)

[English Version](README.md) | [中文版本](README_zh.md)

toffee-test is a pytest plugin that provides testing support for the toffee framework. It includes several features to assist users in writing test cases for toffee:
- Identifies test functions as toffee test case objects, making them recognizable and executable by the toffee framework
- Offers resource management for test cases, such as DUT creation and destruction
- Provides test report generation

## Installation

Existing yield fixtures can keep `set_line_coverage(request, selected_path, ignore=...)`
followed by `dut.Finish()`. During teardown, the reporter resolves only the current
fixture through pytest, records VCS testdata separately from `GetCoveragePath()`, and
finds `coverage/coverage` beside the generated DUT module. It records metadata without
querying coverage before finalization. Mock or coverage-disabled DUTs create no native
coverage record. Outside a DUT fixture, pass `dut=dut` explicitly for VCS; the `.vdb`
suffix alone is not sufficient. Explicit `dut=None` retains raw Verilator registration.
Old exports lacking metadata/helper support fail explicitly and must be regenerated.
The fixture still owns `Finish()`; use a pytest finalizer when cleanup must survive
exceptions in reporting or other teardown code.

VCS queries run the generated helper directly using its schema-version-1 JSON
protocol and a 120-second timeout; no xcomm `CoverageClient` binding is required.
Native query failures and incompatible schemas fail explicitly.

Run `pytest -q tests/test_coverage_fixture.py` for the unchanged-fixture contract.
For native acceptance, set `TOFFEE_NATIVE_DUT_DIR` to a compiled coverage-enabled
Adder package and run `pytest -q tests/test_coverage_native_fixture.py` in its simulator
environment. This copies the package to a temporary directory, checks finalization,
and queries actual testcase coverage through the normal report provider without
modifying the original build.

- Properly install [toffee](https://github.com/XS-MLVP/toffee/tree/master/toffee) and its dependencies.

- Install toffee-test

To install toffee-test via pip:

```bash
pip install toffee-test
```

Or install the development version:

```bash
pip install toffee-test@git+https://github.com/XS-MLVP/toffee-test@master
```

Or install from source:

```bash
git clone https://github.com/XS-MLVP/toffee-test.git
cd toffee-test
pip install .
```

## Usage

### Managing Test Case Resources

toffee-test provides the `toffee_request` fixture for managing test case resources. Use `toffee_request` to create your own fixture, which can then be used within test cases.

For example, the following code creates a fixture to manage DUT creation and destruction.

```python
import toffee_test


@toffee_test.fixture
def my_fixture(toffee_request: toffee_test.ToffeeRequest):
    return toffee_request.create_dut(MyDUT, "clock_pin_name")
```

Interfaces provided in `toffee_request` include:

- `create_dut`: Creates a DUT
    - `dut_cls`: DUT class
    - `clock_name`: Clock name
    - `waveform_filename`: Waveform file name
    - `coverage_filename`: Coverage file name
- `add_cov_groups`: Adds coverage groups
    - `cov_groups`: Coverage groups
    - `periodic_sample`: Periodic sampling option


### Marking Test Cases

Use the `@toffee_test.testcase` decorator to mark test functions, making them recognizable and executable by the toffee framework.

### Generating Test Reports

The final report's **Line Coverage → View Details** opens the HTML entry supplied
by the coverage provider: VCS uses its native URG dashboard; Verilator uses the
existing LCOV page. Links are relative to the final report, so copy or serve the
whole report directory, including native HTML assets. Generation errors and
warnings are shown beside the table; zero available lines display N/A while
keeping a native details link for other metrics. Deferred collection generates
no details page.

New simulator adapters return `CodeCoverageResult(detail=...)` with a relative
HTML entry URL. `html_report_detail(report_dir, output_dir, entry_names=...)`
validates the entry and encodes its path. The adapter owns report generation
and entry selection; the reporter and template require no simulator branches.
Adapters can also return `report_metrics` and `report_scope` for the linked native
report's display summary. VCS reads exact covered/total ratios from URG's text
dashboard: the homepage then matches the full native hierarchy (including Picker
wrappers and all database tests), while `metrics` and normalized coverage JSON
retain the DUT-only query scope used by verification. The scope is shown on the
homepage; a wrapper percentage must not be interpreted as DUT line coverage.


By adding the `--toffee-report` parameter to the pytest command line, you can generate a toffee test report.

Add `--case-coverage` to generate normalized code coverage for every testcase.
The option requires `--toffee-report`. VCS writes `coverage_data/testcases.json`,
per-case `coverage_data/cases/<case-id>/observation.json`, compact
`coverage_data/cumulative.json`, and the complete atom/group inventory in
`coverage_data/catalog.json`. VCS supports line, branch, toggle, condition, and
FSM; FSM states provide context while transitions count toward the FSM rate.
Verilator keeps its existing `line_dat/testcase_coverage.json`, per-case
`code_coverage.json`, and line-range detail behavior. The option does not print
per-test coverage summaries.

```bash
pytest --toffee-report --case-coverage
```

Additionally, the `--report-name` parameter can specify the report name, and `--report-dir` can specify the report directory.

## Additional Resources

More resources are available at [toffee](https://github.com/XS-MLVP/toffee/tree/master/toffee) and [UnityChip Website](https://open-verify.cc/).
