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
HTML 资源。汇总页面保持 master 分支布局，仅覆盖率详情链接由后端决定。

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

主报告按 DUT 及配置的排除规则显示行覆盖率，保持 master 分支的展示方式。
VCS 原生 URG 详情同样应用文件排除规则，原生统计不替代 DUT 指标。

### VCS 逐用例隔离

运行 Picker 生成的 VCS DUT 时会自动启用逐用例 fork。每个用例的 fixture、测试体、
teardown 和 `DUT.Finish()` 都在独立子进程中完成：

```bash
export GLIBC_TUNABLES=glibc.rtld.optional_static_tls=262144
pytest --toffee-report --case-coverage
pytest -n 2
```

`--toffee-forked` 仍可作为兼容参数显式传入。该行为支持 POSIX `fork`。DUT 应由 Picker 重新导出，并包含
`GetSimulator()`、`GetCoveragePath()` 和覆盖率 helper；旧 DUT 包应重新编译。

已有的 `fixture → create_dut() → DUT` 用法可以继续使用，无需改为
`toffee_request.create_dut()`。自动识别会检查引用的 Python 工厂函数、闭包及模块属性，
在创建 VCS DUT 之前进入子进程；函数式 fixture 中原有的 yield、覆盖率上报和
`Finish()` 顺序保持不变。报告标题和用户信息会随测试报告传回主进程。

覆盖率汇总同时写入 `line_dat/code_coverage.json`，保留 `overview` 和
`uncovered` 字段，现有 UCAgent checker 不需要修改路径或解析方式。
Linux 下外层工具终止 pytest 时，逐例子进程会随之终止；单例超时后先发送
SIGTERM，未退出则升级为 SIGKILL，避免测试进程继续占用输出管道。

仿真器差异由后端注册表管理：后端可以声明是否需要进程隔离、波形命名和覆盖率文件
命名；覆盖率转换器通过 `register_coverage_provider()` 注册。VCS 使用 FSDB 和 VCS
原生覆盖率查询，Verilator 使用 FST/数据文件和 LCOV，pytest 主执行流程不需要知道
这些细节。新增仿真器时只需增加后端及对应的 coverage provider。

### 失败详情

使用 \`--report-dump-json\` 时，\`tests[].phases[]\` 的失败阶段包含
\`exception\` 对象，字段为 \`phase\`、\`type\` 和 \`message\`；可跨越
\`pytest-forked\` 与 xdist worker 传输，消息长度限制为 2,000 个字符。

## Adder 示例

仓库根目录提供编译、运行多用例和生成报告的一步入口：

```bash
make test_Adder                 # Verilator：FST、DAT、LCOV
make test_vcs_Adder             # VCS：自动逐例 fork、FSDB、VDB、URG
make test_Adder SIM=vcs         # 同上
```

两套示例共享 128 位 Adder RTL 和 6 组测试，产物分别存放于
`outputs/Adder/verilator/` 与 `outputs/Adder/vcs/`。
环境准备、并行运行和参数见 [示例说明](examples/Adder/README.md)。

## 开发测试

\`\`\`bash
pytest -q test/test_coverage_fixture.py
pytest -q test/test_coverage_native_fixture.py
\`\`\`

原生验收前需将 \`TOFFEE_NATIVE_DUT_DIR\` 设置为启用覆盖率的 Adder DUT 包。

## 文档

- [覆盖率流程和模块边界](docs/picker-coverage-flow.zh.md)
- [覆盖率原子清单](docs/coverage_atom_inventory.md)
