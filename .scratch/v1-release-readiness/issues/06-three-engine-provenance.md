# 同卡数引擎的身份与 NPT 可比工况怎样核验

Status: open
Type: grilling
Label: wayfinder:grilling
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)

## Question

普通 GPUMD 和 LAMMPS/KOKKOS/NEP 在 P=1、2、4 的作业直接使用已安装的程序，报告登记实际版本。如何从作业日志核实普通 GPUMD 的多卡 NEP、LAMMPS 的 `matpl/nep/kk`、所用势和实际 GPU 数？WPE 的单卡作业须能识别 Wisevolve 后端。[L20 已测构建](../../../docs/standards/cross-engine-reference.md#benchmark-engines)

对金刚石碳、致密水和 FCC 铜逐项确定 SCR 生产、Berendsen 预平衡中哪些组合满足相同控制算法、耦合参数、模型和输出条件。当前 LAMMPS `npt/kk` 使用 Nosé–Hoover，Berendsen 组合仍待物理验证。哪些格可以配对计时，哪些只能列不适用及原因？NVE 结果只归入其实际工况。[三引擎参考标准](../../../docs/standards/cross-engine-reference.md) · [三材料 NPT 矩阵](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)
