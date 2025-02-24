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

此外，`--report-name` 参数可以指定报告名称，`--report-dir` 参数可以指定报告存放目录。

## 更多资源

更多资源可在 [toffee](https://github.com/XS-MLVP/toffee/tree/master/toffee) 和 [万众一芯开放验证](https://open-verify.cc/) 中获取。
