# DMG-MD 文档

类别：文档索引。更新日期：2026-10-30。

先看[当前状态](status/current.md)。代码已完成默认关闭的 O4 普通步实际重叠与本轮验收；
默认仍立即 finish。O5 本机正式实验已完成，采用范围与限制见[O5 记录](status/performance/communication-computation-overlap-o5-results-20261021.md)。
O6 已完成[稳态成本定位](status/performance/overlap-o6-results-20261021.md)：carbon 主损失位于 radial，硬件原因 INCONCLUSIVE，未采用新的 NEP 执行改动。
O7 的实现、语义证明与配对验收见[O7 记录](status/performance/overlap-o7-results-20261023.md)。
O8 已采用[按需 NVE thermo](status/performance/overlap-o8-results-20261026.md)，原 full/O7/O8 三版本实测与 CPU 诊断已完成。
O9 已完成[执行候选证据复核](status/performance/overlap-o9-results-20261026.md)，真实 NCU 重试仍受权限限制，以 NO_ADOPTION 结束，未改 NEP 执行。
O6.5 已完成[真实 mask 与 A–D 因果对照](status/performance/overlap-o65-results-20261027.md)，两目标的紧凑映射支持进入独立整步实验，未采用。
O8.5 已完成 [producer 候选、完整科学门槛与 120 个六组合实测](status/performance/overlap-o85-results-20261027.md)；
拆分成本下降，相对 HostStaged/full 仅本轮 water/P8/HostStaged 显示净收益，尚未采用。
O8.6 已完成[紧凑索引/几何内缩、完整门槛与 240 个正式 trial](status/performance/overlap-o86-results-20261028.md)：
B/A 四组确认，C/B wait 增量未确认；water/CudaAware 仍未胜默认路径，未采用。
[O9 独立确认](status/performance/overlap-o9-results-20261029.md)已完成：300 个正式 trial、12 个 CPU 诊断，预选 B 在三个核心 scope 确认净收益，交 O11；默认不变。O11 后续已完成，见[800 个 trial 的限定采用复验](status/performance/overlap-o11-results-20261030.md)；仅建议 carbon/P2 两后端手动 B-wait，water/P8 本轮未通过采用门槛。O10 暂缓。

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
- O2–O4/O8.5/O8.6：[NEP 分区与实验重叠](standards/domain-partition.md)、[halo 生命周期](standards/halo-lifecycle.md)。
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

- [2026-10-28：O8.6 紧凑索引/几何内缩、240 个正式 trial 与独立 trace/CPU（COMPLETE）](status/performance/overlap-o86-results-20261028.md)。
- [2026-10-28：O8.5 producer 调度、完整 consumer 与独立六组合实测（COMPLETE）](status/performance/overlap-o85-results-20261027.md)。
- [2026-10-27：O6.5 真实图/mask、同快照 A–D 对照与候选交接](status/performance/overlap-o65-results-20261027.md)。

- [2026-10-30：O11 的 800 个正式 trial、限定采用建议及离线复算](status/performance/overlap-o11-results-20261030.md)。
- [2026-10-29：O9 执行候选独立确认、300 个 trial 与限定范围收益](status/performance/overlap-o9-results-20261029.md)。
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

正式版产品化独立于 O 系列并行性能优化，由 [DMG-MDv1.0开发流程](plans/dmg-md-v1.0-development-workflow.md) 管理。
该计划已完成静态可行性审计，状态为 PROPOSED；不代表新系综、NPT 或非 NEP 势已支持。

| 优先级 | 计划 | 状态 |
| --- | --- | --- |
| P0（正式版） | [DMG-MDv1.0开发流程](plans/dmg-md-v1.0-development-workflow.md) | PROPOSED；恒温器、控压器、势函数与发布交付；审计及待决策项见 §0.5/§5 |
| P0 | [MPI CPU 核数与 NUMA 绑定](plans/mpi-cpu-binding.md) | IN PROGRESS；DMG-MD 入口已验收，跨引擎/调度器仍待验收 |
| P0 | [LAMMPS 多 GPU benchmark](plans/lammps-multigpu-benchmark.md) | IN PROGRESS；静态/smoke 完成，动力学与正式矩阵待完成 |
| 条件性 | [通信与计算性能优化 O10](plans/communication-computation-overlap.md) | O10 暂缓、PROPOSED；O11 限定复验已完成，仅 carbon/P2 两后端手动 B-wait，默认不变 |
| 辅助 | [CPU 使用率研究](plans/cpu-usage-study.md) | IN PROGRESS；C0/C1 完成，O5 窄 C2 已测，完整 C2/C3 待执行 |
| 后续 | [域分解扩展](plans/domain-decomposition.md)、[风险与待办](plans/risk-and-backlog.md) | PROPOSED / IN PROGRESS；M2b、release 等未完成 |
| 暂缓 | [双物理节点 I/O 验收](plans/multi-node-io.md) | PROPOSED；按维护者决定暂缓 |

## 维护规则

测试脚本决定执行行为，manifest 决定 case/profile/哈希/容差，测试 README 维护操作方法。
标准解释合同；实测保留日期、revision/dirty 身份、环境、精确命令、结果与原始证据位置。
未运行、skip 和子集通过须分别标注；本机生成物不属于随仓库分发的文档。

计划完成后删除重复步骤，Git 保存旧方案，不另建 archive 副本。移动文档时同步检查
Markdown 链接、源码注释和测试 docstring；新增文档须从本页链接。
