# LAMMPS 多 GPU 对照剩余验收

状态：IN PROGRESS。优先级：P0。更新日期：2026-10-21。

2026-11-23 的 V1.0 性能范围要求在相同卡数下比较 LAMMPS/KOKKOS/NEP 与 DMG-MD，首选公开碳、水、FCC 铜的 NPT。下列既有三引擎输入转换、静态比较和计时编排覆盖 NVE。NPT 要另验等价的温控、控压和势参数，逐材料报告能成立的组合。[三项性能矩阵](../status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎标准](../standards/cross-engine-reference.md) · [LAMMPS 手册](../../tests/lammps_reference/README.md) · [性能工具](../../tests/benchmark/README.md)

[2026-10-09 验收](../status/validation/lammps-reference.md#lammps-reference-20261009)完成了 GPUMD/LAMMPS 的 11 个静态 case、LAMMPS 1/2 GPU 和三引擎单 GPU smoke。正式效率结果尚未产生。L20 后续改用已安装的可执行文件，见[运行记录](../status/validation/lammps-reference.md#l20-installed-nve-20261123)。

## 既有 NVE 对照的剩余步骤

1. 完成[跨引擎 CPU/NUMA 绑定验收](mpi-cpu-binding.md)。DMG-MD 本机入口已验收，LAMMPS 已接入但尚未执行固定绑定科学检查，GPUMD 合计 P×K 核的入口未实现。DMG-MD/LAMMPS 各 K 核/rank，GPUMD 单进程使用 P 张 GPU 时分配相同总量 P×K 核。
2. 保存实际 affinity、quota、NUMA、GPU UUID、版本、编译精度、温度时钟和独占条件。CPU affinity 不能证明内存页实际位于哪一个 NUMA 节点。
3. 验证正式性能 case 的同势静态物理量、三引擎短 NVE 的有限值、动能和势能趋势及原子数，建立长程守恒与统计判据。pair-style 回退、未使用 GPU、势不兼容或物理量失败时，不进入正式速度比较。
4. 对 `carbon_200k`、`carbon_1m` 和 `water_400k` 做 P1/P2/P4/P8，各至少五次独立重复，随机安排顺序。预热和步数以 manifest 及显式覆盖为准，各引擎计时边界的差异按标准说明。
5. 报告 ms/step、atom-step/s、自身单卡强扩展、同 P 的三引擎速度比、离散度和缺项，保留 metadata、日志及输入转换哈希。结论只覆盖实际测试的节点范围。

LAMMPS 计时直接使用站点提供的 KOKKOS/NEP 可执行文件，报告登记当次版本和精度。DMG-MD 的重叠 A/B 正确性比较使用自身数值参照，可独立执行；外部效率比较须完成上述验收后再发布。
