# DMG-MD 文档

类别：文档索引。更新日期：2026-10-26。

先看[当前状态](status/current.md)。代码已完成默认关闭的 O4 普通步实际重叠与本轮验收；
默认仍立即 finish。O5 本机正式实验已完成，采用范围与限制见[O5 记录](status/performance/communication-computation-overlap-o5-results-20261021.md)。
O6 已完成[稳态成本定位](status/performance/overlap-o6-results-20261021.md)：carbon 主损失位于 radial，硬件原因 INCONCLUSIVE，未采用新的 NEP 执行改动。
O7 的实现、语义证明与配对验收见[O7 记录](status/performance/overlap-o7-results-20261023.md)。
O8 已采用[按需 NVE thermo](status/performance/overlap-o8-results-20261026.md)，原 full/O7/O8 三版本实测与 CPU 诊断已完成。
O9 已完成[执行候选证据复核](status/performance/overlap-o9-results-20261026.md)，真实 NCU 重试仍受权限限制，以 NO_ADOPTION 结束，未改 NEP 执行。

| 目录 | 内容 | 维护边界 |
| --- | --- | --- |
| `standards/` | 已生效的产品、runtime 与测试合同 | 随代码更新，不复制历史验收表 |
| `reference/` | 锁定 GPUMD 的调用链与 kernel 证据 | 与 DMG-MD 当前实现区分 |
| `operations/` | 部署与运行方法 | CLI 以脚本为准 |
| `status/current.md` | 当前状态入口 | 汇总能力、最新证据和缺口 |
| `status/validation/` | 带日期的正确性和工具验收 | 结果限于记录的源码、二进制与环境 |
| `status/performance/` | 带日期的成本和性能实测 | 保留测量条件、原始数据位置和限制 |
| `plans/` | 未完成的方案、验收和风险 | 完成部分迁入标准和实测记录 |

## 当前合同

- 产品：[架构决策](standards/architecture-decisions.md)、[输入/输出兼容矩阵](standards/compatibility-matrix.md)。
- Runtime：[MPI 双路径与通信记账](standards/mpi-runtime.md)、[数据布局](standards/data-layout.md)、[缓存有效性证明](standards/cache-validity.md)。
- O2–O4：[NEP 分区与实验重叠](standards/domain-partition.md)、[halo 生命周期](standards/halo-lifecycle.md)。
- O8：[M2a thermo 消费者、按需执行与诊断就绪](standards/thermo-demand.md)。
- 验证：[Golden Test 原则](standards/golden-test-standard.md)、[三引擎对照](standards/cross-engine-reference.md)、[CPU 采样](standards/cpu-sampling.md)。
- 启动：[单节点 CPU/NUMA 绑定与独立核验](standards/cpu-binding.md)。
- 参考证据：[GPUMD runtime 审计](reference/gpumd-runtime-audit.md)、[NEP kernel 与依赖](reference/kernel-inventory.md)。

## 操作入口

- [Docker 快速开始](../README.md#docker-部署推荐跨服务器使用)、[跨服务器部署](operations/deployment.md)。
- 测试目录：[单 GPU baseline](../tests/baseline/README.md)、[MPI/halo](../tests/mpi/README.md)、[长程 NVE/NVT](../tests/long_nve/README.md)。
- [Benchmark](../tests/benchmark/README.md)、[LAMMPS 输入与静态比较](../tests/lammps_reference/README.md)。

## 验证记录

| 日期 | 范围 |
| --- | --- |
| 2026-10-21 | [O5 后续文档整理、构建中间文件清理与离线重算](status/validation/documentation-build-cleanup-20261021.md) |
| 2026-10-16 | [O4 普通步重叠](status/validation/overlap-o4-20261016.md) |
| 2026-10-15 | [O3 halo 生命周期](status/validation/halo-lifecycle-20261015.md)、[O2 串行分区](status/validation/overlap-partition-20261015.md) |
| 2026-10-09 | [CPU 采样 C1](status/validation/cpu-sampling-c1-20261009.md)、[LAMMPS 静态与 smoke](status/validation/lammps-reference-20261009.md) |
| 2026-09-23 | [Docker 适配与待验收项](status/validation/docker-validation.md) |
| 2026-09-22 | [Benchmark 工具](status/validation/benchmark-validation.md) |
| 2026-09-09 | [Committed baseline 与重复校准](status/validation/baseline-results.md) |

## 性能与成本记录

- [2026-10-26：O9 执行候选证据复核、NCU 实际重试与 NO_ADOPTION](status/performance/overlap-o9-results-20261026.md)。
- [2026-10-26：O8 按需 NVE thermo、消费者与三版本实测](status/performance/overlap-o8-results-20261026.md)。
- [2026-10-23：O7 单次缓存 OR、数值验收与独立配对实测](status/performance/overlap-o7-results-20261023.md)。

- [2026-10-21：O6 稳态 kernel 对照、无诊断复验与硬件指标限制](status/performance/overlap-o6-results-20261021.md)。
- [2026-10-21：O5 正式性能、成本归因与采用验证（10-16 开始）](status/performance/communication-computation-overlap-o5-results-20261021.md)。
- [2026-10-21：O5 后续分析——4.60 ms 拆分成本、模式解释与优化切口](status/performance/overlap-o5-cost-analysis-20261021.md)。
- [2026-10-16：O4 固定资源检查](status/performance/communication-computation-overlap-results-20261016.md)。
- [2026-10-12：O1 串行时间线与 CPU 基线](status/performance/overlap-baseline-20261012.md)。
- [2026-09-26：单机八卡 benchmark](status/performance/benchmark-multigpu-results-20260926.md)。
- [2026-09-22：统一重建与分步成本](status/performance/stage2-cost-report.md)。

O2/O3 的局部成本 A/B 随各自验收记录保存，不构成实际 overlap 收益。

## 剩余计划

| 优先级 | 计划 | 状态 |
| --- | --- | --- |
| P0 | [MPI CPU 核数与 NUMA 绑定](plans/mpi-cpu-binding.md) | IN PROGRESS；DMG-MD 入口已验收，跨引擎/调度器仍待验收 |
| P0 | [LAMMPS 多 GPU benchmark](plans/lammps-multigpu-benchmark.md) | IN PROGRESS；静态/smoke 完成，动力学与正式矩阵待完成 |
| P0 | [通信与计算性能优化 O10–O11](plans/communication-computation-overlap.md) | PROPOSED；O1–O9 本机阶段完成，O9 以 NO_ADOPTION 结束，后续条件性后端评估与采用复验 |
| 辅助 | [CPU 使用率研究](plans/cpu-usage-study.md) | IN PROGRESS；C0/C1 完成，O5 窄 C2 已测，完整 C2/C3 待执行 |
| 后续 | [域分解扩展](plans/domain-decomposition.md)、[风险与待办](plans/risk-and-backlog.md) | PROPOSED / IN PROGRESS；M2b、release 等未完成 |
| 暂缓 | [双物理节点 I/O 验收](plans/multi-node-io.md) | PROPOSED；按维护者决定暂缓 |

## 维护规则

测试脚本决定执行行为，manifest 决定 case/profile/哈希/容差，测试 README 维护操作方法。
标准解释合同；实测保留日期、revision/dirty 身份、环境、精确命令、结果与原始证据位置。
未运行、skip 和子集通过须分别标注；本机生成物不属于随仓库分发的文档。

计划完成后删除重复步骤，Git 保存旧方案，不另建 archive 副本。移动文档时同步检查
Markdown 链接、源码注释和测试 docstring；新增文档须从本页链接。
