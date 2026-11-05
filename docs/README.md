# DMG-MD 文档

类别：文档索引。更新日期：2026-11-05。

项目正按 [v1.0 开发流程](plans/dmg-md-v1.0-development-workflow.md)推进正式版：
Gate 0、A0–A6/G-A 与 B0–B4/G-B 已完成；B3 原注册18个 formal look 的总体 INCONCLUSIVE 历史保留，独立确认通过，B4 测试维护和文档收口也已通过。`npt_scr` 的统计支持严格限于 216 原子 NEP-C、300 K/0 GPa、单节点全量复制模式各向同性正交全周期盒：base 为 P1/HostStaged 与 P2/P4 双后端，half-dt 仅 P1/HostStaged；NEP5/ZBL 仅有另行记录的短程合同证据。LJ、EAM/alloy、C/D 阶段与发布交付仍未完成。先看[逐 Step 进度](plans/dmg-md-v1.0-progress.md)、
[当前状态](status/current.md)与[兼容矩阵](standards/compatibility-matrix.md)。

O 系列是与 v1.0 产品化独立的性能主线。按优化目标查看[通信与计算成本](status/performance/communication-computation-overlap-results.md)、
[NEP 执行成本](status/performance/nep-execution-cost.md)、[候选验证](status/performance/nep-candidate-validation.md)、
[缓存归约](status/performance/cache-reduction-results.md)、[按需 thermo](status/performance/thermo-demand-results.md)
与[producer 调度](status/performance/producer-schedule-results.md)；每份报告按日期记录各次执行，O10 当前暂缓。
公共共享服务器上的 overlap/O 系列记录属于历史探索；对应原始 ignored 目录已按授权清理，后续性能参考与采用判断以专用服务器 benchmark 为准，当前生产默认不变。清理范围和结果见[构建与历史产物清理记录](status/validation/documentation-build-cleanup.md#artifact-cleanup-20261105)。NPT/G-B 最终证据入口为[新机确认索引](status/validation/npt-scr-n3-confirmation-index.json)和[最终报告](status/validation/npt-validation.md#npt-scr-confirmation-20261104)；保留的正式分析结果位于 `status/validation/evidence/npt-scr-gb/results/n3/paired-analysis.json`，ignored 的运行数据与诊断产物仍位于 `artifacts/validation/npt-scr-gb/`；正式小型 evidence 包只保留 paired-analysis。迁移审计与执行过程摘要见最终报告；中间日志、locator、源/输入快照及收据未保留，因此不能据此声称可从已删过程文件完整复算。

| 目录 | 内容 | 维护边界 |
| --- | --- | --- |
| `standards/` | 已生效的产品、runtime 与测试合同 | 随代码更新，不复制历史验收表 |
| `reference/` | 锁定 GPUMD 的调用链与 kernel 证据 | 与 DMG-MD 当前实现区分 |
| `operations/` | 部署与运行方法 | CLI 以脚本为准 |
| `status/current.md` | 当前状态入口 | 汇总能力、最新证据和缺口 |
| `status/validation/` | 按测试目标组织的正确性和工具验收 | 同一目标的每次执行追加独立日期节，结论限于记录的源码、二进制与环境 |
| `status/performance/` | 按测试目标组织的成本和性能实测 | 同一目标追加日期节并记录身份、环境、命令、结果和限制；原始证据按明确的保留范围管理 |
| `plans/` | 未完成的方案、验收和风险 | 完成部分迁入标准和实测记录 |

## 当前合同

- 产品：[架构决策](standards/architecture-decisions.md)、[输入/输出兼容矩阵](standards/compatibility-matrix.md)、[系综与恒温器](standards/ensembles.md)、[各向同性 NPT 控压器](standards/barostat.md)。
- Runtime：[MPI 双路径与通信记账](standards/mpi-runtime.md)、[数据布局](standards/data-layout.md)、[缓存有效性证明](standards/cache-validity.md)。
- O2–O4/O6.5/O8.5/O8.6/O9/O11：[NEP 分区与实验重叠](standards/domain-partition.md)、[halo 生命周期](standards/halo-lifecycle.md)。
- O8：[局部域分解模式 thermo 消费者、按需执行与诊断就绪](standards/thermo-demand.md)。
- 验证：[Golden Test 原则](standards/golden-test-standard.md)、[三引擎对照](standards/cross-engine-reference.md)、[CPU 采样](standards/cpu-sampling.md)。
- 启动：[单节点 CPU/NUMA 绑定与独立核验](standards/cpu-binding.md)。
- 参考证据：[GPUMD runtime 审计](reference/gpumd-runtime-audit.md)、[NEP kernel 与依赖](reference/kernel-inventory.md)。

## 操作入口

- [Docker 快速开始](../README.md#docker-部署推荐跨服务器使用)、[跨服务器部署](operations/deployment.md)。
- [测试目录总览](../tests/README.md)：源码、构建与 CTest 的范围说明；具体测试目录：[单 GPU baseline](../tests/baseline/README.md)、[MPI/halo](../tests/mpi/README.md)、[长程 NVE/NVT](../tests/long_nve/README.md)、[NHC/BDP 验收](../tests/thermostats/README.md)、[NPT 验收](../tests/npt/README.md)。
- [Benchmark](../tests/benchmark/README.md)、[LAMMPS 输入与静态比较](../tests/lammps_reference/README.md)。

## 验证记录

按测试目标查找报告；每份报告内用日期节区分不同执行，日期、时间和运行身份均保留。

| 测试目标 | 报告与对应执行 |
| --- | --- |
| v1.0 系综开发基线 | [A0：2026-10-30](status/validation/ensemble-baseline.md#ensemble-baseline-20261030) |
| 系综输入与共享控制器 | [A1：2026-10-30](status/validation/ensemble-infrastructure.md#ensemble-parser-20261030)；[A2：10-30 实施、11-02 验收](status/validation/ensemble-infrastructure.md#ensemble-control-20261102) |
| 恒温器实现、双路径验收与跨引擎诊断 | [A3 NHC](status/validation/thermostat-validation.md#nhc-20261102)、[A4 BDP](status/validation/thermostat-validation.md#bdp-20261102)、[A5 矩阵](status/validation/thermostat-validation.md#thermostat-matrix-20261102)、[A6 文档终检](status/validation/thermostat-validation.md#thermostat-docs-20261102)、[harmonic 对照](status/validation/thermostat-validation.md#thermostat-reference-controls-20261103) |
| NPT 控压器与 G-B | [NPT/G-B 主题报告](status/validation/npt-validation.md#npt-matrix-20261104)；机器索引：[原18-look](status/validation/npt-scr-formal-index.json)、[独立确认](status/validation/npt-scr-n3-confirmation-index.json)。G-B PASS 仅适用于报告所列冻结矩阵，不外推跨节点/RDMA或普遍严格 NPT |
| 通信/计算重叠的正确性与生命周期 | [O2 分区](status/validation/communication-computation-overlap.md#overlap-partition-20261015)、[O3 halo](status/validation/communication-computation-overlap.md#halo-lifecycle-20261015)、[O4 overlap](status/validation/communication-computation-overlap.md#overlap-o4-20261016) |
| CPU 采样工具 | [C1 验收](status/validation/cpu-sampling.md#cpu-sampling-c1-20261009) |
| LAMMPS/MatPL 静态物理量与 smoke | [三引擎对照](status/validation/lammps-reference.md#lammps-reference-20261009) |
| 文档维护与构建产物清理 | [O5 后续整理](status/validation/documentation-build-cleanup.md#documentation-build-cleanup-20261021) |
| Docker、benchmark 工具与早期 baseline | [Docker](status/validation/docker-validation.md)、[benchmark](status/validation/benchmark-validation.md)、[baseline](status/validation/baseline-results.md) |

## 性能与成本记录

按优化目标查找报告；各日期节保留当次执行时间、代码身份、环境、命令、结果与证据位置。

| 优化目标 | 报告与对应执行 |
| --- | --- |
| 通信与计算重叠成本 | [O1 基线](status/performance/communication-computation-overlap-results.md#o1-20261012-report)、[O4 固定资源检查](status/performance/communication-computation-overlap-results.md#o4-20261016-report)、[O5 正式矩阵](status/performance/communication-computation-overlap-results.md#o5-20261021-report)、[O5 后续分析](status/performance/communication-computation-overlap-results.md#o5-cost-20261021-report) |
| NEP 执行成本 | [O6](status/performance/nep-execution-cost.md#o6-results-20261021-report)、[O6.5](status/performance/nep-execution-cost.md#o65-results-20261027-report) |
| 缓存判定归约 | [O7](status/performance/cache-reduction-results.md#o7-results-20261023-report) |
| 按需 NVE thermo | [O8](status/performance/thermo-demand-results.md#o8-results-20261026-report) |
| producer 调度 | [O8.5](status/performance/producer-schedule-results.md#o85-results-20261027-report)、[O8.6](status/performance/producer-schedule-results.md#o86-results-20261028-report) |
| NEP 候选独立验证与采用边界 | [O9 证据复核](status/performance/nep-candidate-validation.md#o9-results-20261026-report)、[O9 独立确认](status/performance/nep-candidate-validation.md#o9-results-20261029-report)、[O11 限定复验](status/performance/nep-candidate-validation.md#o11-results-20261030-report) |
| 多 GPU benchmark | [单机八卡](status/performance/benchmark-multigpu-results.md#multigpu-20260926-report) |
| 早期分步成本 | [Stage 2](status/performance/stage2-cost-report.md) |

O2/O3 的局部成本 A/B 随各自验收记录保存，不构成实际 overlap 收益。

## 剩余计划

正式版产品化独立于 O 系列并行性能优化，由 [DMG-MDv1.0开发流程](plans/dmg-md-v1.0-development-workflow.md) 管理。
该计划已完成 Gate 0、A0–A6/G-A 与 B0–B4/G-B；B3 原注册统计总体 INCONCLUSIVE 的历史结论保留，独立确认与 B4 测试维护/文档收口均通过。NHC/BDP 与全量复制模式`npt_ber` 已完成所列 NEP 验收；`npt_scr` 统计支持仅限 216 原子 NEP-C、300 K/0 GPa、单节点全量复制模式各向同性正交全周期冻结矩阵。C/D 阶段、非 NEP 势和发布交付仍待后续，v1.0 尚未发布。
逐 Step 的当前 revision、证据与阻塞项见[执行进度记录](plans/dmg-md-v1.0-progress.md)。

| 优先级 | 计划 | 状态 |
| --- | --- | --- |
| P0（正式版） | [DMG-MDv1.0开发流程](plans/dmg-md-v1.0-development-workflow.md) | IN PROGRESS；G0、A0–A6/G-A、B0–B4/G-B 完成；B3 原注册统计 INCONCLUSIVE 历史保留，独立确认与 B4 测试维护通过；C/D、新势函数和发布交付待完成，v1.0 尚未发布 |
| P0 | [MPI CPU 核数与 NUMA 绑定](plans/mpi-cpu-binding.md) | IN PROGRESS；DMG-MD 入口已验收，跨引擎/调度器仍待验收 |
| P0 | [LAMMPS 多 GPU benchmark](plans/lammps-multigpu-benchmark.md) | IN PROGRESS；静态/smoke 完成，动力学与正式矩阵待完成 |
| 条件性 | [通信与计算性能优化 O10](plans/communication-computation-overlap.md) | O10 暂缓、PROPOSED；O11 的公共服务器建议仅属历史探索，专用服务器 benchmark 用于后续性能参考与采用判断，当前默认不变 |
| 辅助 | [CPU 使用率研究](plans/cpu-usage-study.md) | IN PROGRESS；C0/C1 完成，O5 窄 C2 已测，完整 C2/C3 待执行 |
| 后续 | [域分解扩展](plans/domain-decomposition.md)、[风险与待办](plans/risk-and-backlog.md) | PROPOSED / IN PROGRESS；分阶段中间场交换方案、release 等未完成 |
| 暂缓 | [双物理节点 I/O 验收](plans/multi-node-io.md) | PROPOSED；按维护者决定暂缓 |

## 维护规则

测试脚本决定执行行为，manifest 决定 case/profile/哈希/容差，测试 README 维护操作方法。
标准解释合同；实测按测试目标写入稳定主题文件，并记录日期、时间、revision/dirty 身份、环境、精确命令、结果与限制。原始证据保留范围随记录说明；经用户授权的结果保留型清理后，不得声称原始文件仍在或可离线复算。
未运行、skip 和子集通过须分别标注；本机生成物不属于随仓库分发的文档。

英文报告文件名使用稳定主题 slug，不含执行日期。同一测试目标的后续执行追加到稳定主题文件的新日期节，并用稳定锚点区分；只有目标或证据结构独立时才新建报告。Git 历史保留旧快照，不另建 archive 副本。当前状态入口、标准和计划随事实同步；机器索引与数据文件按各自合同管理，不受报告文件名规则限制。
汇总链接应指向对应日期节。若原始目录已清理，历史路径与命令仅作为执行记录，不应保留为指向缺失产物的活动链接。计划完成后删除重复步骤，Git 保存旧方案，不另建 archive 副本。移动或整合文档时同步检查
全部旧路径引用、本地 Markdown 文件与片段链接、源码注释和测试 docstring；新增文档须从本页链接。
