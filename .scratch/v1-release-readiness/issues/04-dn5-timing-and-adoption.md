# 三项对标怎样采样并据此选择局部 NPT 缓存策略

Status: open
Type: grilling
Label: wayfinder:grilling
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)
Blocked by: 03

## Question

在公开碳、水、FCC 铜的 NPT 科学与分派检查通过后，怎样为三项外部对标冻结预热、重复数、交错顺序、计时边界和配对区间？普通 GPUMD、LAMMPS/KOKKOS/NEP 各与 DMG-MD 同卡数比较，WPE 固定单卡，分别报告单卡比和跨卡数迁移比。DMG-MD 自身强扩展与同卡数内部复制、强制重建、形变复用另测。以哪些测量和适用范围为依据选择默认缓存策略？

2026-11-23 已确定三项都须报告，验收关注科学正确性、可比性与报告完整性，对速度不设门槛。科学上不可比或资源不容纳的组合列出原因，可比但未采样的项保留为待测。各材料的控压矩阵与指标定义见[性能规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)。

先确认[三种 SCR 工况各用什么统计标准验收](03-scr-science-protocol.md)。
