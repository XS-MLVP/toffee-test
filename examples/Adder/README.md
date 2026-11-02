# Adder 多用例示例

VCS 和 Verilator 共用 `Adder.v`、`test_adder.py`，覆盖 zero、small、boundary、
carry_chain、alternating、固定种子 random 共 6 组用例，每例创建并销毁独立 DUT。
测试检查完整的 128 位 sum 和 cout，同时采样 cin/cout 功能覆盖率。
fixture 还检查进程 ID，确认 VCS 自动 fork、Verilator 在当前进程运行。

## 环境

需要 Python 3.11+、Make、CMake、C++ 编译器、SWIG，以及已构建的 `../picker`。
在虚拟环境中安装 Toffee master 分支和当前插件：

```bash
python -m pip install -e ../toffee -e .
```

以上命令在仓库根目录执行，`../toffee` 应已检出 master。
Verilator 需要 FST 构建依赖（包括 LZ4/zlib 头文件）和 LCOV 的 `genhtml`。
VCS 需要有效 license、VCS/Verdi 环境，默认使用 `--verdi-mode modern`；
旧版本可传入 `VERDI_MODE=legacy`。NixOS 上可先进入 `ue` 提供的 EDA/FHS 环境。

## 编译、测试和报告

在仓库根目录执行：

```bash
make test_Adder
make test_vcs_Adder
make test_Adder SIM=vcs
make test_Adder PYTEST_ARGS='-n 2'
make test_vcs_Adder PYTEST_ARGS='-n 2'
```

也可在 `examples/Adder/` 中执行 `make test_Adder`（Verilator）或
`make test_Adder SIM=vcs`。
每次完整运行重新导出 DUT；`make run` 复用对应输出目录中已经生成的 DUT。
默认使用同级 Picker 构建产物，并优先从同级 Toffee 和本仓库加载 Python 源码。
所有示例均启用 `--toffee-report --case-coverage --report-dump-json`，
VCS 在启动 Python 前设置 `GLIBC_TUNABLES=glibc.rtld.optional_static_tls=262144`。

可覆盖 `PICKER`、`PYTHON`、`TOFFEE_ROOT`、`JOBS`（默认 4）、`PICKER_ARGS` 和
`PYTEST_ARGS`，以及 `OUTPUT_ROOT`（默认仓库根目录的 `outputs/`），例如：

```bash
make test_Adder PYTHON=/path/to/venv/bin/python PICKER=/path/to/picker
```

## 产物

所有生成内容存放在仓库根目录的 `outputs/Adder/<simulator>/` 下，
其中 `<simulator>` 为 `vcs` 或 `verilator`：

- `.pytest_cache/` 及仿真器运行日志：pytest 缓存和原生运行过程文件。
- `build/Adder/`：生成的 DUT 包；VCS 原始数据库位于 `build/Adder/Adder.vdb`。
- `reports/index.html`：测试报告及覆盖率详情入口。
- `reports/toffee_report.json`：报告上下文。
- `reports/*.fsdb`（VCS）或 `reports/*.fst`（Verilator）：逐例波形。
- `reports/*.dat`：Verilator 逐例原始覆盖率。
- `reports/coverage_data/testcases.json`、`cases/`：逐例覆盖率索引和数据。
- VCS 的 `coverage_data/cumulative.json`、`catalog.json` 和原生 URG 页面；
  Verilator 的 `coverage_data/code_coverage.json` 和 LCOV 页面。

`coverage_data/` 路径均相对于 `reports/`。保留完整报告目录以使用详情链接；
重新编译会替换原 DUT 和 VDB，需留存原始数据库时先复制 `build/Adder/`。
根目录 `make clean_Adder` 清理两个后端的构建和报告；`make -C examples/Adder clean SIM=vcs`
仅清理 VCS 产物（`SIM=verilator` 对应 Verilator）。

## 覆盖率口径

主报告展示当前用例的 DUT 覆盖率；示例通过 `ignore_patterns=["*Adder_top.sv"]`
排除 Picker 生成的 DPI 包装文件，保留 `Adder.v` 的全部计数。

主报告只展示行覆盖率。Adder 的逻辑是一条连续赋值 `assign`；VCS 默认不把
连续赋值纳入行覆盖率，因此示例在编译时增加 `-cm_line contassign`。
该选项必须重新编译 DUT 后才生效，不能只重新生成报告。
未启用时行计数为 `0/0`，无法反映该连续赋值的行覆盖情况。
功能覆盖率独立统计 cin/cout 的 4 个 bin，全部命中时为 100%。

VCS 原生 URG 详情同样应用文件排除规则，不统计 `Adder_top.sv` 的覆盖率。
主报告和原生详情都只使用 Adder 模块的计数。主报告保持 master 分支布局，
仅“View Details”的链接根据后端决定；诊断信息保留在日志和 JSON 中。
原生数据库仍保留 Picker 包装模块，这是运行仿真所需的结构。
