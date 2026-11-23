# 通信与计算重叠的后续工作

状态：PROPOSED。

单槽位边界副本、计算中心分区，以及显式的提前计算与边界后计算调度已经实现。重叠开关默认关闭。现有实现与默认值见[多卡实现导读](../reference/multi-gpu-implementation.md)，正确性和缓冲生命周期见[验证记录](../status/validation/communication-computation-overlap.md)。

<a id="o8-5"></a>
## 实验候选

拆分中间量计算、紧凑索引和向条带内部收缩的计算区，用来研究 NEP 描述符与力的调度方式。这些候选保留显式实验入口。

若新的独占 NPT 计时发现通信或边界计算限制了整步收益，再从同一快照比较现行调度和候选。逐项核对计算中心是否完整且唯一、边界数据何时可用，以及数值是否一致。代码中的 producer、early 和 late 分别对应中间量计算、可提前执行的计算和等待边界后的计算。

<a id="o10"></a>
## O10 后端选择

O10 暂缓。若正式 NPT 剖析显示小规模归约或点对点通信占主要时间，先分别测量主机暂存、设备缓冲、MPI 等待和温压反馈的成本。

候选须在相同物理任务上完成整步对照，并分别记录 HostStaged 和 CudaAware。测量条件见[性能方法](../status/performance/npt-benchmarks.md)。
