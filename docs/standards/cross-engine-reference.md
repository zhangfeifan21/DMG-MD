# GPUMD、LAMMPS、DMG-MD 横向对照标准

三引擎对照先回答同一初态能否得到相容的能量、力和维里张量，再比较完整模拟段的用时。静态物理量检查和 NVE 计时编排已生效（ACTIVE）。固定 CPU 核数与 NUMA 放置的正式性能验收仍属 P0 计划。

## 参考引擎与身份检查

普通 GPUMD 5.9.1 提供短程数值 golden，LAMMPS/MatPL 提供独立的物理对照。本页的计时方式属于已有的三引擎 NVE 工具。实验室 NPT 迁移使用另一项比较，以 GPUMD-WPE 单卡为性能基线。[NPT 性能规程](../status/performance/npt-benchmarks.md)

LAMMPS 源码锁定 `stable_22Jul2025_update4`，另锁定 MatPL overlay 版本。确切的
commit 和 overlay 文件哈希见 [overlay manifest](../../third_party/lammps-matpl-overlay/manifest.json)。
在 L20，后续对照选用 Spack 的 `ed-gpumd/5.9.1-r1` 与
`ed-lammps/22Jul2025u4-matpl-r1`。MatPL 构建使用 KOKKOS CUDA 后端和 double 精度。
L20 的静态运行证据见[LAMMPS 参考验收](../status/validation/lammps-reference.md#l20-lammps-reference-20261120)。

NVE 计时程序会检查与 DMG-MD 并列的参考源码目录。GPUMD 的提交须匹配
[benchmark manifest](../../tests/benchmark/manifest.json)。LAMMPS 与 MatPL 各自须有匹配
提交的源码目录，且没有工作区改动。`--reference` 和 `--lammps-bin` 仅指定运行的可执行文件。
L20 的短程 golden 已使用 GPUMD 5.9.1，现行 benchmark manifest 仍锁定
`9d23496e`。在 benchmark 迁移前，L20 当前配置无法通过这一参考身份检查。
见[部署手册](../operations/deployment.md)和
[L20 入口检查记录](../status/validation/lammps-reference.md#l20-benchmark-reference-gate-20261120)。

## 比较对象与等价输入

三个程序须模拟同一物理体系。使用相同的 NEP 模型、元素、原子顺序、质量、初始位置和速度、周期盒、系综及物理步长。GPUMD 和 DMG-MD 直接读取 GPUMD 输入。LAMMPS 从同一 `model.xyz` 生成 `data.lmp`，用 `pair_style matpl/nep/kk` 求力。

LAMMPS 的 `units metal` 用 ps 和 Å/ps 表示时间与速度。0.1 fs 对应 `timestep 0.0001`，以 Å/fs 给出的速度须乘 1000。邻居表的 skin 显式设为 1 Å。原子 ID 与模型行号相同，元素类型按势文件头排序。

MatPL 无法直接读取逐元素 NEP `cutoff` 行。如果所有元素的径向截断半径相同，并且所有角向截断半径也相同，转换器可以把该行写成统一格式。任一半径不满足相等条件便报错。typewise ZBL 截断是另一项功能，此转换不涉及它。

以 `dense_water_typewise_cutoff` 为例，O–O、O–H 和 H–H 三类原子对的径向、角向截断半径分别都是 6 Å、4 Å。GPUMD/DMG-MD 用原文件，LAMMPS 用折叠后的文件。这个实验检验两种写法的物理等价性。MatPL 本身仍不支持原行的语法。

转换器须保留原势、LAMMPS 所用势、转换规则和各自哈希，再核对结构字段、单位和逐原子数值。未能证明转换等价、势加载失败或实际 pair style 回退时，该算例不能用于三软件速度比。DMG-MD 对 GPUMD 的仓库 golden 另有独立的字节结构检查。

## 物理正确性与误差

在同一初态上，先核对原子数、元素、坐标和速度，再比较总势能、逐原子势能、力及 virial。短程 NVE 检查数值是否有限和能量怎样变化。长程比较守恒量和统计量。

静态比较使用 `tests/lammps_reference/compare_static.py`。机器可读的误差阈值只存于 `tests/lammps_reference/manifest.json`，按实测差异和所选 MatPL 推理精度校准。不同引擎的浮点精度和归约顺序允许有差异，但不能为单项失败放宽阈值。温度自由度的定义也可能不同，应同时比较动能。长程 NVE/NVT 三引擎正式统计门槛仍待后续 profile 验收。

LAMMPS 的 `compute stress/atom NULL virial` 六列顺序为 xx、yy、zz、xy、xz、yz，
符号与 virial 相反，`metal` 单位为 bar·Å³。转换到本项目逐原子 virial（eV）时
乘 `-1/1602176.5`；该因子取自锁定 LAMMPS 的 `src/update.cpp`。比较器按原子 ID
恢复 GPUMD/DMG-MD 的输入行序后再比较。未经排序的 MPI dump 行序可能不同。

## 性能比较

同一算例通过物理正确性检查后，才可用于正式计时。三个引擎使用相同算例、seed、GPU 集合、预热步数、正式步数和重复方案。GPUMD 由一个进程控制 P 张卡。LAMMPS 和 DMG-MD 启动 P 个 MPI rank，每个 rank 使用一张卡。LAMMPS 启动包装器为每个 rank 指定唯一的 GPU UUID，再以 `-k on g 1 -sf kk` 运行。

| 引擎 | 第二段 MD 运行采用的计时字段 |
| --- | --- |
| GPUMD | `Time used for this run` |
| LAMMPS | `Loop time` |
| DMG-MD | `DMGMD_TIMING seconds_max` |

这三项都计量本引擎的完整 MD 运行段，起止边界略有差别，不能从中单独求出 NEP 核函数的耗时。

正式计时须独占节点，固定每个 rank 的 CPU 核数、NUMA 放置、GPU 设备和编译精度，并核验实际配置。结果要报告多次重复的波动，保留原始日志、可执行文件和源码身份。

当前工具已能安排三个引擎运行，DMG-MD 也能按固定 CPU/NUMA 放置启动并独立核验。GPUMD 的聚合入口和 LAMMPS 的正式绑定验收仍未完成，现有 smoke/pilot 计时只验证流程。算例、profile、步数和重复次数以 `tests/benchmark/manifest.json` 为准。[CPU/NUMA 绑定](cpu-binding.md)

外部单位和张量语义依据：[GPUMD typewise cutoff](https://gpumd.org/dev/nep/input_parameters/cutoff.html)、
[LAMMPS `units metal`](https://docs.lammps.org/units.html)、
[LAMMPS `compute stress/atom`](https://docs.lammps.org/compute_stress_atom.html)。
