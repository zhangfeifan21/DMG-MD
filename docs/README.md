# DMG-MD 文档

## 运行模拟

| 你要完成的事 | 阅读入口 |
| --- | --- |
| 配置环境、编译并运行第一个单卡作业 | [原生构建与运行](operations/deployment.md) |
| 核对已有 GPUMD 输入是否受支持 | [兼容矩阵](standards/compatibility-matrix.md) |
| 理解多卡分工，并从日志确认实际模式 | [多卡实现导读](reference/multi-gpu-implementation.md) |
| 查看试用条件及已验证的范围 | [当前状态](status/current.md) |
| 比较同卡数 GPUMD、LAMMPS/KOKKOS/NEP、单卡 WPE，以及 DMG-MD 单卡能力与强扩展 | [性能方法、可比工况与结果](status/performance/npt-benchmarks.md#performance-matrix) |

<a id="输入并行和数值合同"></a>
## 理解和修改实现

[术语表](../GLOSSARY.md)解释各篇共用的概念。[架构决策](standards/architecture-decisions.md)记录已作出的取舍，其中待实现的决策有单独标记。具体行为以对应标准为准。

| 主题 | 标准与源码证据 |
| --- | --- |
| 原子分工、积分次序和通信 | [MPI 运行时](standards/mpi-runtime.md)、[数据布局](standards/data-layout.md) |
| 计算中心和边界数据 | [计算分区](standards/domain-partition.md)、[halo 生命周期](standards/halo-lifecycle.md) |
| 缓存与热力学计算 | [缓存有效性](standards/cache-validity.md)、[热力学量的计算时机](standards/thermo-demand.md) |
| 系综、控压与变盒 | [系综与恒温器](standards/ensembles.md)、[控压器](standards/barostat.md)、[局部 NPT](standards/domain-npt.md)、[形变误差界证明](standards/domain-npt-rounding-proof.md) |
| CPU 资源与观测 | [CPU 绑定](standards/cpu-binding.md)、[CPU 采样](standards/cpu-sampling.md) |
| GPUMD 复现与引擎对照 | [GPUMD 调用过程](reference/gpumd-runtime-audit.md)、[内核清单](reference/kernel-inventory.md)、[golden 验收规则](standards/golden-test-standard.md)、[跨引擎对照](standards/cross-engine-reference.md) |

测试怎么运行、怎样读取结果，见[测试目录总览](../tests/README.md)。修改前还须按仓库 [AGENTS.md](../AGENTS.md) 核对仓库内兼容代码的来源和相应验证要求。

<a id="科学验收与后续工作"></a>
## 查验历史结果

下列页面按测试目标保留日期节。每项结论只适用于当次记录的版本、体系和配置；原始数据的留存范围由各页说明。测试方法已经定义，不代表该测试已经执行。

| 要查的结果 | 记录 |
| --- | --- |
| 单卡数值基线 | [短程 golden](status/validation/baseline-results.md) |
| 系综开发的起点和共享控制器 | [开发前基线](status/validation/ensemble-baseline.md)、[输入与控制器](status/validation/ensemble-infrastructure.md) |
| NHC、BDP 及重启 | [恒温器验证](status/validation/thermostat-validation.md) |
| 压力、盒、Berendsen 和 SCR | [NPT 验证](status/validation/npt-validation.md)、[SCR 原始配对分析](status/validation/evidence/npt-scr-gb/paired-analysis.json) |
| 大盒局部 NPT | [局部 NPT 验证](status/validation/domain-npt-validation.md) |
| 分区和边界交换 | [分区、halo 与重叠验证](status/validation/communication-computation-overlap.md) |
| CPU 采样与参考引擎 | [CPU 采样验证](status/validation/cpu-sampling.md)、[LAMMPS 静态对照](status/validation/lammps-reference.md) |

## 查找后续工作

[V1.0 开发进度](plans/dmg-md-v1.0-progress.md)维护近期任务和依赖，[开发与发布验收](plans/dmg-md-v1.0-development-workflow.md)维护正式版范围和通过条件。[风险与待办](plans/risk-and-backlog.md)记录尚未解决的风险。

专题计划补充各自的技术问题：[局部 NPT 与 DN5](plans/multi-gpu-barostat.md)、[域分解扩展](plans/domain-decomposition.md)、[通信重叠](plans/communication-computation-overlap.md)、[CPU 绑定](plans/mpi-cpu-binding.md)、[CPU 采样](plans/cpu-usage-study.md)、[LAMMPS 对照](plans/lammps-multigpu-benchmark.md)和[双节点 I/O](plans/multi-node-io.md)。

代理协作约定见[领域文档](agents/domain.md)、[工单路径](agents/issue-tracker.md)和[分流状态](agents/triage-labels.md)。
