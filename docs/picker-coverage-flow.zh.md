# Picker、Toffee 与 UCAgent 覆盖率流程

本文描述当前代码中的覆盖率数据路径和模块边界。流程面向 RTL 仿真覆盖率采集、缺口建模和覆盖率闭环，不描述历史版本的兼容协议。

## 1. 模块职责

```text
VCS / Verilator 原生覆盖率数据库
        |
        v
Picker coverage executable
  - 读取仿真器数据库
  - 执行覆盖率查询
  - 返回指标和当前未覆盖项
        |
        v
xspcomm CoverageClient
  - 启动查询程序
  - 校验并解析标准输出
  - 返回 CoverageReport
        |
        v
Toffee
  - 将未覆盖项规范化为稳定 atom_id
  - 生成当前未覆盖 catalog
  - 保存累计报告和原子清单
        |
        v
UCAgent
  - 管理已接受的基线目标 U
  - 读取候选当前未覆盖集合 C
  - 计算 remaining = U & C
  - 计算 covered = U - C
  - 更新 coverage set 和 closure batch
```

Picker 不管理覆盖率闭环状态；Toffee 不判断候选是否关闭了基线目标；集合比较和闭环决策由 UCAgent 完成。

## 2. Picker 查询生命周期

生成 DUT 包后，覆盖率查询程序位于生成目录的 `coverage/coverage`。Toffee 根据 DUT Python 模块所在目录定位该程序，不依赖额外的 helper-path API。

仿真阶段的接口为：

- `SetCoverage(name)`：为下一阶段选择 VCS testdata 名称。
- `FlushCoverage()`：将当前计数提交到可查询的仿真器覆盖率数据库。
- `GetCoveragePath()`：返回 VCS `.vdb` 或 Verilator `coverage.dat` 的路径。
- `GetCoverage()`：通过 xspcomm 查询数据库并返回 `CoverageReport`。

对应实现位于 Picker 的 `template/python/dut.py`、`template/lib/dut_base.cpp` 和 `template/lib/dut_base.hpp`。VCS 与 Verilator 查询程序分别由 `template/coverage/vcs/vcs_uncover.cpp` 和 `template/coverage/verilator/verilator_coverage.cpp` 生成。

## 3. Picker 查询与报告字段

C++ 数据结构定义在 Picker 子仓库 `dependence/xcomm/include/xspcomm/xcoverage.h`，当前 `COVERAGE_SCHEMA_VERSION` 为 `1`。

重要查询字段：

```json
{
  "kinds": ["line", "toggle", "branch", "condition", "fsm"],
  "module": "Top",
  "instance": "TOP.u_dut",
  "tests": ["case_a", "case_b"]
}
```

- `kinds`：需要查询的覆盖率类型。
- `module`、`instance`：可选的设计层次过滤条件。
- `tests`：VCS testdata 选择；为空时查询当前数据库范围。

重要报告字段：

```json
{
  "schema_version": 1,
  "success": true,
  "simulator": "vcs",
  "query": {
    "kinds": ["line"],
    "module": "",
    "instance": "",
    "tests": ["case_a"]
  },
  "metrics": {
    "line": {
      "covered": 18,
      "total": 20,
      "uncovered": 2,
      "rate": 90.0
    }
  },
  "items": [
    {
      "kind": "line",
      "file": "rtl/top.sv",
      "line": 42,
      "column": 0,
      "module": "Top",
      "instance": "TOP.u_dut",
      "object": "if_enable",
      "count": 0,
      "detail": "..."
    }
  ],
  "errors": []
}
```

`metrics` 直接来自覆盖率查询器，用于报告总量和覆盖率。`items` 只表示本次查询范围内仍未覆盖的对象，不是完整设计 inventory，也不包含 `covered_items`。

## 4. Toffee 当前未覆盖 catalog

Toffee 的 Picker 适配代码位于 `toffee_test/utils/code_coverage/vcs.py`。它保留 Picker 的 `metrics`，并将每个未覆盖项规范化为 `atom_id`。atom 标识主要由以下信息构成：

```text
coverage kind
source file
module and elaborated instance
normalized object
line and column fallback
toggle bit or branch/condition/FSM detail
```

Toffee 输出目录：

```text
coverage_data/
  cumulative.json
  catalog.json
  catalog/
    sources.jsonl
    groups.jsonl
    atoms.jsonl
  native/vcs_urg/
```

`cumulative.json` 保存指标、查询范围和 catalog 引用；`catalog.json` 保存清单版本、行数和 SHA-256；JSONL 文件保存当前查询的未覆盖 source、group 和 atom。候选 preview 的 atom observation 固定为 `uncovered`。

重要 atom 字段：

```json
{
  "atom_id": "atom:<sha256>",
  "group_key": "group:<sha256>",
  "source": {
    "source_id": "source:<sha256>",
    "line": 42,
    "column": 0
  },
  "variant": {
    "transition_or_arm": "false"
  },
  "observation": "uncovered"
}
```

Toffee 生成 catalog 时不接收 UCAgent 的 target ID，不计算 `U - C`，也不输出业务含义上的 `covered_items`。

## 5. UCAgent 闭环判定

UCAgent 的 preview 处理位于 `ucagent/lang/zh/skills/coverage-refine/run-total-coverage/scripts/run_total_coverage.py`。
当前 coverage store 使用 SQLite schema 12；snapshot 只保存 revision、catalog/report 摘要、suite 摘要和各类覆盖率统计，不再保存 Picker provider identity 或 contract version。

设：

- `U`：UCAgent 当前 active closure batch 中已接受的目标 atom ID 集合。
- `C`：Toffee 候选 preview catalog 中的当前未覆盖 atom ID 集合。

UCAgent 计算：

```text
remaining = U & C
covered   = U - C
```

这里的差集只用于判断候选关闭了哪些既定缺口。覆盖率指标 `covered/total/uncovered/rate` 不通过集合差计算，仍直接采用 Picker 查询结果。

在执行差集前，UCAgent 校验报告和 catalog schema、catalog 哈希、atom 文件哈希、记录数、observation 类型以及 Toffee reconciliation。若 atom 枚举不完整、存在重复标识或报告损坏，preview 失败，不得将“目标缺失”解释为“目标已覆盖”。

UCAgent 报告中的 `covered_items` 是 closure batch 的业务统计量，表示 `U - C` 的数量；它不是 Picker 报告字段。

## 6. 当前约束

- 基线和候选必须使用相同的 RTL 构建、覆盖率类型、层次范围和 ignore 规则。
- 候选查询必须成功生成完整的当前未覆盖 catalog，才能执行差集判定。
- source file、module、instance 和 object 的规范化规则变化会改变 atom ID，应重新接受基线 snapshot。
- Picker 当前精简 item 未单独提供 FSM state/transition 的 `atom_kind`。若闭环需要区分 FSM state 与 transition，应在 Picker 查询协议中补充该语义字段，而不是由 Toffee 根据字符串推断。
