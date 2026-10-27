# toffee-test

[![PyPI version](https://badge.fury.io/py/toffee-test.svg)](https://badge.fury.io/py/toffee-test)

[English Version](README.md) | [中文版本](README_zh.md)

toffee-test 是一个用于为 toffee 框架提供测试支持的 Pytest 插件，他为 toffee 框架提供了以下测试功能，以便于用户编写测试用例。
- 将测试用例函数标识为 toffee 的测试用例对象，使其可以被 toffee 框架识别并执行
- 提供了测试用例资源的管理功能，例如 DUT 创建、销毁等
- 提供了测试报告生成功能

## 安装

- 正确安装 [toffee](https://github.com/XS-MLVP/toffee/tree/master/toffee) 及其依赖

- 安装 toffee-test

通过 pip 安装 toffee-test

```bash
pip install toffee-test
```

或安装开发版本

```bash
pip install toffee-test@git+https://github.com/XS-MLVP/toffee-test@master
```

或通过源码安装

```bash
git clone https://github.com/XS-MLVP/toffee-test.git
cd toffee-test
pip install .
```

## 使用

### 管理测试用例资源

toffee-test 提供了 `toffee_request` Fixture，可用于管理测试用例资源。使用时利用 `toffee_request` 创建自己的 Fixture，然后在测试用例中使用。

例如以下案例中创建了一个 Fixture 用于管理 DUT 的创建和销毁。

```python
import toffee_test


@toffee_test.fixture
def my_fixture(toffee_request: toffee_test.ToffeeRequest):
    return toffee_request.create_dut(MyDUT, "clock_pin_name")
```

toffee_request 中提供的接口如下：

- `create_dut`：创建 DUT
    - `dut_cls`：DUT 类
    - `clock_name`：时钟名称
    - `waveform_filename`：波形文件名
    - `coverage_filename`：覆盖文件名
- `add_cov_groups`：添加覆盖组
    - `cov_groups`：覆盖组
    - `periodic_sample`：是否周期采样


### 标识测试用例

通过 `@toffee_test.testcase` 装饰器标识测试用例函数，使其可以被 toffee 框架识别并执行。

### 生成测试报告

通过在 pytest 命令行中添加 `--toffee-report` 参数，可以生成 toffee 测试报告。

增加 `--case-coverage` 后，会为每个测试用例生成标准化代码覆盖率。该参数必须与
`--toffee-report` 一起使用。VCS 写入 `coverage_data/testcases.json`、逐用例
`coverage_data/cases/<case-id>/observation.json`、紧凑累计报告 `coverage_data/cumulative.json`
和完整原子目录 `coverage_data/catalog.json`。VCS 支持 line、branch、toggle、condition、FSM；FSM state
只提供上下文，transition 计入 FSM 覆盖率。Verilator 保留原有 `line_dat/testcase_coverage.json`、逐用例
`code_coverage.json` 与 line-range 行为。本参数不会逐用例打印覆盖率摘要。

```bash
pytest --toffee-report --case-coverage
```


最终报告的 **Line Coverage → View Details** 打开覆盖率提供方返回的 HTML
入口：VCS 使用原生 URG 报告，Verilator 保留 LCOV 页面。链接相对于最终报告，
复制或提供网页服务时应保留整个报告目录及原生报告资源。生成错误和警告显示在
表格旁；没有可统计的行时显示 N/A，仍保留原生详情入口以查看其他指标。
延迟收集模式不生成详情页。

新增仿真器适配器通过 `CodeCoverageResult(detail=...)` 返回相对 HTML 地址；
可使用 `html_report_detail(report_dir, output_dir, entry_names=...)` 校验入口并
编码路径。适配器负责生成报告和选择入口，报告层及模板无需按仿真器添加分支。
适配器也可返回 `report_metrics` 和 `report_scope`，提供原生网页的展示汇总。
VCS 从 URG 文本报告读取精确命中数和总数，首页与原生报告的完整层次范围一致
（包含 picker 包装模块及数据库全部测试）；`metrics` 和规范化覆盖率 JSON
继续保持验证使用的 DUT 查询范围。首页会注明范围，包装模块的百分比不代表 DUT 行覆盖率。

此外，`--report-name` 参数可以指定报告名称，`--report-dir` 参数可以指定报告存放目录。

## 更多资源

现有 yield fixture 可保持 `set_line_coverage(request, selected_path, ignore=...)`，随后按原顺序调用 `dut.Finish()`。
toffee-test 在 teardown 中通过 pytest 获取当前 fixture 的 DUT，自动关联 testdata、`GetCoveragePath()` 返回的真实 VDB 和模块旁的 `coverage/coverage`。
此时只登记元数据，不提前查询未完成的覆盖率；Mock 或未启用覆盖率的 DUT 不生成原生覆盖率记录。
在 DUT fixture 外调用时，VCS 仍需显式传 `dut=dut`，不能仅凭 `.vdb` 后缀推断；显式 `dut=None` 保留原 Verilator 登记行为。
缺少元数据接口或查询程序的旧包必须重新生成。`Finish()` 仍由 fixture 管理；若要覆盖上报或其他 teardown 异常，建议注册 pytest finalizer。

VCS 查询直接调用生成包的 helper，使用 schema-version-1 JSON 协议及 120 秒超时，
不再依赖 xcomm 的 `CoverageClient` 绑定。原生查询失败或 schema 不匹配会明确报错。

更多资源可在 [toffee](https://github.com/XS-MLVP/toffee/tree/master/toffee) 和 [万众一芯开放验证](https://open-verify.cc/) 中获取。
