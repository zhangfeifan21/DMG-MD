# LAMMPS 多 GPU 对照剩余验收

状态：IN PROGRESS。优先级：P0。更新日期：2026-10-21。

输入转换、静态物理量比较和三引擎 benchmark 编排已经实现。版本、势转换、单位、张量和计时规则以[跨引擎标准](../standards/cross-engine-reference.md)为准。操作入口见[LAMMPS 手册](../../tests/lammps_reference/README.md)和[性能工具](../../tests/benchmark/README.md)。

[2026-10-09 验收](../status/validation/lammps-reference.md#lammps-reference-20261009)完成了 GPUMD/LAMMPS 的 11 个静态 case、LAMMPS 1/2 GPU 和三引擎单 GPU smoke。正式效率结果尚未产生。当前 L20 的参考身份阻断另见[运行条件](../status/validation/lammps-reference.md#l20-benchmark-reference-gate-20261120)。

## 剩余步骤

1. 完成[跨引擎 CPU/NUMA 绑定验收](mpi-cpu-binding.md)。DMG-MD 本机入口已验收，LAMMPS 已接入但尚未执行固定绑定科学检查，GPUMD 合计 P×K 核的入口未实现。DMG-MD/LAMMPS 各 K 核/rank，GPUMD 单进程使用 P 张 GPU 时分配相同总量 P×K 核。
2. 保存实际 affinity、quota、NUMA、GPU UUID、版本、编译精度、温度时钟和独占条件。CPU affinity 不能证明内存页实际位于哪一个 NUMA 节点。
3. 验证正式性能 case 的同势静态物理量、三引擎短 NVE 的有限值、动能和势能趋势及原子数，建立长程守恒与统计判据。pair-style 回退、未使用 GPU、势不兼容或物理量失败时，不进入正式速度比较。
4. 对 `carbon_200k`、`carbon_1m` 和 `water_400k` 做 P1/P2/P4/P8，各至少五次独立重复，随机安排顺序。预热和步数以 manifest 及显式覆盖为准，各引擎计时边界的差异按标准说明。
5. 报告 ms/step、atom-step/s、自身单卡强扩展、同 P 的三引擎速度比、离散度和缺项，保留 metadata、日志及输入转换哈希。结论只覆盖实际测试的节点范围。

参考 checkout 保持只读，补丁、独立工作源码和构建放在本仓库内。现存 LAMMPS cache 含旧绝对路径，不能直接用于另一目录重建。DMG-MD 的重叠 A/B 正确性比较使用自身数值参照，可独立执行；外部效率比较须完成上述验收后再发布。
