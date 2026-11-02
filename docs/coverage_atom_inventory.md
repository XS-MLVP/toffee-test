# Coverage Atom Catalog

This document defines the persisted atom catalog produced by toffee-test and consumed by UCAgent.

Picker 返回覆盖率指标和当前未覆盖项。Toffee 将未覆盖项规范化为 atom，并保存
`coverage_data/cumulative.json`、`coverage_data/catalog.json` 以及
`coverage_data/catalog/{sources,groups,atoms}.jsonl`。

catalog 表示一次查询的当前未覆盖集合，不是 Picker 构造的完整设计 inventory。Toffee 不接收覆盖率闭环目标，也不判断 atom 是否被候选关闭。UCAgent 使用 active closure target 集合 `U` 和当前未覆盖集合 `C` 计算：

```text
remaining = U & C
covered   = U - C
```

完整流程、字段和代码位置见 `docs/picker-coverage-flow.zh.md`。
