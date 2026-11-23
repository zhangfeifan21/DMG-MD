# MPI CPU/NUMA 绑定剩余验收

状态：IN PROGRESS。优先级：P0。更新日期：2026-10-21。

O5 范围内已完成 DMG-MD 常规启动入口、共用 core/numa/rankfile 策略、benchmark 接入，以及独立于 C1 的实际线程 affinity 核验。现行接口、资源限制、错误行为和数据格式见[CPU 绑定标准](../standards/cpu-binding.md)。

同卡数比较普通 GPUMD、LAMMPS/KOKKOS/NEP 与 DMG-MD 时固定相同的 CPU 总预算和 NUMA 放置。WPE 单卡对 DMG-MD 单卡也匹配预算，跨卡数迁移逐端报告所用资源。[测量方法](../status/performance/npt-benchmarks.md#performance-matrix)

## 尚未完成

| 工作 | 当前缺口 |
| --- | --- |
| GPUMD 的 CPU 集合 | 尚未实现单进程使用合计 P×K 核的入口及数值、性能验收。benchmark 当前明确拒绝 GPUMD 固定绑定，旧直启结果不能充当公平 CPU 对照 |
| LAMMPS 验收 | 已可复用绑定策略和 observer，但未执行固定绑定科学检查及三引擎正式矩阵。[独立对照计划](lammps-multigpu-benchmark.md) |
| 其他拓扑和调度环境 | 真实 cpuset allocation、配额不足或不可读等宿主集成场景仍待测。已有纯 CPU 拒绝测试，只能证明对应分支；当前实现仅支持单机、同 namespace、cgroup v2 和 Open MPI 5 |
| 内存放置 | 物理内存页策略、实际驻留和跨 NUMA 流量仍待研究。CPU affinity 与 numactl 策略输出不足以证明内存本地化 |

O5 只采用本机验收的四物理核/rank，以及明确的 NUMA→GPU UUID 映射。新环境先核验映射，再通过统一入口启动。

CPU 采样的 C2/C3 和容量研究由[CPU 计划](cpu-usage-study.md)维护，绑定完成不代表这些实验完成。新增 worker 或 progress thread 不属于本计划。
