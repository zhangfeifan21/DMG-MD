# MPI CPU/NUMA 绑定剩余验收

类别：分阶段计划。状态：**IN PROGRESS**。优先级：P0。更新日期：2026-10-21。

O5 授权范围内已补充 DMG-MD 常规启动入口、共用 core/numa/rankfile 策略、benchmark
接线及独立于 C1 的实际 task affinity 核验。当前接口、资源约束、错误行为与数据格式已
迁入[绑定合同](../standards/cpu-binding.md)，不在计划重复维护草案。
实际日期、源码/二进制、CPU 单测、core/numa/rankfile 启动与固定绑定 GPU smoke 的证据
见 [O5 记录](../status/performance/communication-computation-overlap-o5-results-20261021.md)。

## 尚未完成

1. GPUMD 单进程使用与 MPI 作业合计 P×K 相同 CPU 集合的入口及数值/性能验收。
   当前 benchmark 对 GPUMD 固定绑定明确拒绝，不能把旧直启结果称为公平 CPU 对照。
2. LAMMPS MPI 可复用策略与 observer，但本轮没有运行其固定绑定科学门槛或三引擎
   正式矩阵；待[外部参照计划](lammps-multigpu-benchmark.md)独立推进。
3. 其他拓扑、受 cpuset 限制的真实调度器 allocation、配额不足/不可读的宿主集成场景；
   当前有纯 CPU 拒绝测试，不能当作所有集群实际验收。仅支持单机、同 namespace、
   cgroup v2/Open MPI 5，跨节点不在当前实现合同。
4. 物理内存页策略/驻留及跨 NUMA 内存流量研究。CPU affinity 与 numactl 策略输出
   不构成本地内存证明；不因 O5 绑定完成便宣称该项受控。

O5 只采用本机验收的四物理核/rank 与明确 NUMA→GPU UUID 映射，不以临时未核验的
映射替代统一入口。CPU C2/C3 的进一步采样与容量研究仍由[CPU 计划](cpu-usage-study.md)
管理，固定绑定并不自动完成它们。新增 worker/progress thread 不属于本计划。
