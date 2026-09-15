# DMG-MD 文档索引

类别：文档索引。

本文档是 `docs/` 的统一入口。文档按“当前生效的标准、带日期的进度证据、尚未实施的计划”
分类；代码和测试中的事实发生变化时，不应在三类文档中复制维护同一个参数表。

## 现行标准

这些文档描述已经生效的产品、架构和测试合同：

- [架构决策](./standards/architecture-decisions.md)
- [输入/输出兼容矩阵](./standards/compatibility-matrix.md)
- [数据结构与内存布局](./standards/data-layout.md)
- [Replicated-data MPI 协议](./standards/replicated-mpi.md)
- [GPUMD runtime 审计与最小源码闭包](./standards/gpumd-runtime-audit.md)
- [NEP kernel 与通信依赖](./standards/kernel-inventory.md)
- [Golden Test 标准](./standards/golden-test-standard.md)

测试的机器可读参数仍以 `tests/**/manifest.json` 和测试脚本为第一事实源；标准文档解释合同，
不复制全部 profile 和容差。

## 进度与实测备忘

这些文档只说明某个日期、revision 和环境下已经完成或实际运行的内容：

- [当前实现进度](./status/current.md)
- [GPUMD/DMG-MD baseline 实测结果](./status/baseline-results.md)

“测试已定义”不等于“当前 revision 已执行通过”。新的通过结论必须在此类文档中记录日期、
代码 revision、环境、精确命令和结果。

## 待实施计划

这些内容尚未成为生产合同：

- [域分解与 halo 通信计划](./plans/domain-decomposition.md)（IN PROGRESS：M0 已实施，M1 起待审批）
- [多节点 rank I/O 隔离验收计划](./plans/multi-node-io.md)（IN PROGRESS）
- [风险与待办登记](./plans/risk-and-backlog.md)

计划获批并实施后，应把最终合同迁入 `standards/`，把实测结果写入 `status/`，再从计划中删除
已经完成的详细执行步骤。Git 历史负责保存旧方案，不在当前工作树中另建重复的归档副本。

## 维护规则

- 每份文档开头必须标明类别；计划还必须标明 `PROPOSED`、`APPROVED` 或 `IN PROGRESS`。
- 标准只用来描述当前有效行为；未实现内容只能链接到计划。
- 状态文档不得成为容差、profile 或支持范围的唯一来源。
- 修改测试时同时检查脚本、manifest、相邻测试 README 和 Golden Test 标准。
- 修改当前 runtime 协议时同步更新 `replicated-mpi.md`；域分解真正启用后再调整其归类。
- 文档移动或改名后必须扫描仓库中的 Markdown 链接、源码注释和测试 docstring。
