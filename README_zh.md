# toffee-test

[![PyPI version](https://badge.fury.io/py/toffee-test.svg)](https://badge.fury.io/py/toffee-test)

[English Version](README.md) | [中文版本](README_zh.md)

\`toffee-test\` 是 Toffee 框架的 pytest 插件，提供测试用例注册、DUT 与覆盖率资源管理，以及 HTML/JSON 测试报告。

## 安装

先安装 Toffee 及其依赖：

\`\`\`bash
pip install toffee-test
\`\`\`

或从源码安装：

\`\`\`bash
git clone https://github.com/XS-MLVP/toffee-test.git
cd toffee-test
pip install .
\`\`\`

## 使用

### 资源和测试用例

使用 \`toffee_request\` 创建 DUT 资源，使用 \`@toffee_test.testcase\` 注册测试用例：

\`\`\`python
import toffee_test

@toffee_test.fixture
def my_fixture(toffee_request: toffee_test.ToffeeRequest):
    return toffee_request.create_dut(MyDUT, "clock_pin_name")

@toffee_test.testcase
def test_adder(my_fixture):
    ...
\`\`\`

\`create_dut\` 接受 DUT 类、时钟名称、波形文件名和覆盖率文件名；
\`add_cov_groups\` 用于注册覆盖率分组及可选采样周期。

### 测试报告

使用 \`--toffee-report\` 生成报告，并用 \`--report-name\`、\`--report-dir\` 指定名称和目录：

\`\`\`bash
pytest --toffee-report --report-dir reports
\`\`\`

最终报告的 **Line Coverage → View Details** 会打开覆盖率适配器返回的 HTML
入口：VCS 使用 URG，Verilator 使用 LCOV。发布报告时应保留完整报告目录和原生
HTML 资源；没有可统计的行时显示 \`N/A\`。

将 \`--case-coverage\` 与 \`--toffee-report\` 一起使用：

\`\`\`bash
pytest --toffee-report --case-coverage
\`\`\`

VCS 写入 \`coverage_data/testcases.json\`、逐用例观测、
\`coverage_data/cumulative.json\` 和 \`coverage_data/catalog.json\`；支持 line、
branch、toggle、condition 和 FSM。Verilator 保留现有的逐用例覆盖率文件。

覆盖率汇总包含所有已登记记录，包括断言失败的用例；没有记录时显示 \`N/A\`。
VCS 可继续使用 \`set_line_coverage(request, selected_path, ignore=...)\`，随后调用
\`dut.Finish()\`。teardown 会关联 DUT、testdata、\`GetCoveragePath()\` 和
\`coverage/coverage\`；缺少接口的旧 DUT 包必须重新生成。

VCS 查询通过生成的 helper 使用 schema version 1 的 JSON 协议，超时为 120 秒，
不依赖 xcomm 的 \`CoverageClient\` 绑定。

### 失败详情

使用 \`--report-dump-json\` 时，\`tests[].phases[]\` 的失败阶段包含
\`exception\` 对象，字段为 \`phase\`、\`type\` 和 \`message\`；可跨越
\`pytest-forked\` 与 xdist worker 传输，消息长度限制为 2,000 个字符。

## 开发测试

\`\`\`bash
pytest -q tests/test_coverage_fixture.py
pytest -q tests/test_coverage_native_fixture.py
\`\`\`

原生验收前需将 \`TOFFEE_NATIVE_DUT_DIR\` 设置为启用覆盖率的 Adder DUT 包。

## 文档

- [覆盖率流程和模块边界](docs/picker-coverage-flow.zh.md)
- [覆盖率原子清单](docs/coverage_atom_inventory.md)
