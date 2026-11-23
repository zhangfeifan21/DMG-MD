# 三种公开算例按什么规模和快照冻结

Status: open
Type: grilling
Label: wayfinder:grilling
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)

## Question

首版已选金刚石碳、致密水和 FCC 铜。每种材料放大到什么规模，怎样确认最终快照已平衡？选取共同的盒、坐标与速度，记录势、步长、温压、耦合参数和输出频率。哪些规模可以在普通 GPUMD、LAMMPS/KOKKOS/NEP 与 DMG-MD 的相同卡数上运行，同时覆盖 WPE 单卡及 DMG-MD 单卡？不重叠的容量范围另列。现有 20 步输入可作为先导起点。

已知输入、原子数和公开 WPE 身份见[三种材料的短程检查](../../../docs/status/performance/npt-benchmarks.md#目前有哪些实测)。
