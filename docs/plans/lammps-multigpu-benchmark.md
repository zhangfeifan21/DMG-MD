# LAMMPS 多 GPU 对照剩余验收

类别：实施计划。状态：**IN PROGRESS**。优先级：P0。更新日期：2026-10-16。

输入转换、静态物理量比较和三引擎 benchmark 编排已实现；锁定版本、势等价转换、单位、
张量与计时规则统一见[三引擎标准](../standards/cross-engine-reference.md)，CLI 见
[LAMMPS README](../../tests/lammps_reference/README.md)和[benchmark README](../../tests/benchmark/README.md)。
不在计划维护另一份转换规则。

[2026-10-09 验收](../status/validation/lammps-reference-20261009.md)记录 GPUMD/LAMMPS
11 个静态 case、LAMMPS 1/2 GPU 和三引擎单 GPU smoke。正式效率结果尚无。

## 剩余步骤

1. 接入[统一 CPU/NUMA 策略](mpi-cpu-binding.md)：DMG-MD/LAMMPS 各 K 核/rank，
   GPUMD 单进程 P 张 GPU 使用相同总量 P×K 核。保留实际 affinity、quota、NUMA、GPU UUID、
   版本/编译精度、温度时钟和独占条件；不能从 affinity 推断内存页放置。
2. 验证正式性能 case 的同势静态正确性、三引擎短程 NVE 的有限值/动能/势能趋势与原子数，
   建立长程守恒/统计门槛。pair-style 回退、未用 GPU、势不兼容或物理量失败不得进入正式速度比。
3. 对 carbon_200k/carbon_1m/water_400k 做 P1/2/4/8，至少五次独立重复，随机化顺序；
   warmup/steps 以 manifest 和显式覆盖为准。不同引擎 run 计时边界的差异按标准说明。
4. 报 ms/step、atom-step/s、自身单卡强扩展、同 P 三引擎速度比、离散度和缺项，保留完整
   metadata/日志/输入转换哈希。单节点结果不推断跨节点。

reference checkout 保持只读，补丁/工作源码/构建位于 newmd 内；保留的 LAMMPS cache 含旧
绝对路径，不能当可迁移重建目录。overlap A/B 独立使用 DMG-MD oracle，不等待 LAMMPS
才验证正确性；外部效率比较在上述验收后发布。
