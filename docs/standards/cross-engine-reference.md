# GPUMD、LAMMPS、DMG-MD 横向对照标准

三引擎对照先回答同一初态能否得到相容的能量、力和维里张量，再比较完整模拟段的用时。静态物理量检查和 NVE 计时编排已生效（ACTIVE）。固定 CPU 核数与 NUMA 放置的正式性能验收仍属 P0 计划。

<a id="参考引擎与身份检查"></a>
<a id="benchmark-engines"></a>
## L20 性能测试使用的版本

| 性能对照引擎 | 已测构建 | 对照方式 |
| --- | --- | --- |
| 普通 GPUMD | 5.9.1，`11ba6fb`，Release，sm_89 | 与 DMG-MD 使用相同卡数，P>1 时核对单进程多卡 NEP 分派 |
| GPUMD-WPE（GPUMD + WPE） | GPUMD 宿主 `a0e55bc`，Wisevolve Potential Engine 1.0.0，Modern CUDA 12.8.1 发行库和 v1.0.0 集成层 | WPE 固定单卡，核对作业日志中的 Wisevolve 引擎标记 |
| LAMMPS + KOKKOS + NEP | 22 Jul 2025 Update 4（`611ca3b7`），MatPL `21fabf34`，KOKKOS 4.6.2，CUDA/OpenMP，double | 与 DMG-MD 使用相同卡数，核对实际使用的 `matpl/nep/kk` 与每个 rank 的 GPU |

表中版本描述本次实验环境。其他安装可直接参加计时，报告注明实际使用的版本和可执行文件。计时入口从指定路径或 `PATH` 选取程序，物理工况、执行后端与用卡数决定哪些结果可比。普通 GPUMD 另为短程数值 golden 提供参考，其身份由该套回归的 manifest 管理。[计时命令](../../tests/benchmark/README.md) · [数值基线](golden-test-standard.md)

NVE 短程运行只验证编排。三材料 NPT 分别核验控压算法和取样条件后再作正式比较。[NPT 对标矩阵](../status/performance/npt-benchmarks.md#performance-matrix) · [L20 静态验证](../status/validation/lammps-reference.md#l20-lammps-reference-20261120)

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
乘 `-1/1602176.5`；该因子取自上述 LAMMPS 构建对应的 `src/update.cpp`。比较器按原子 ID
恢复 GPUMD/DMG-MD 的输入行序后再比较。未经排序的 MPI dump 行序可能不同。

## 性能比较

同一算例通过物理正确性检查后，才可用于正式计时。三个引擎使用相同算例、seed、GPU 集合、预热步数、正式步数和重复方案。GPUMD 由一个进程控制 P 张卡。LAMMPS 和 DMG-MD 启动 P 个 MPI rank，每个 rank 使用一张卡。LAMMPS 启动包装器为每个 rank 指定唯一的 GPU UUID，再以 `-k on g 1 -sf kk` 运行。

| 引擎 | 第二段 MD 运行采用的计时字段 |
| --- | --- |
| GPUMD | `Time used for this run` |
| LAMMPS | `Loop time` |
| DMG-MD | `DMGMD_TIMING seconds_max` |

这三项都计量本引擎的完整 MD 运行段，起止边界略有差别，不能从中单独求出 NEP 核函数的耗时。

正式计时须独占节点，固定每个 rank 的 CPU 核数、NUMA 放置、GPU 设备和编译精度，并核验实际配置。结果要报告多次重复的波动，保留原始日志，随结果记录当次引擎版本及可执行文件身份。

当前工具已能安排三个引擎运行，DMG-MD 也能按固定 CPU/NUMA 放置启动并独立核验。GPUMD 的聚合入口和 LAMMPS 的正式绑定验收仍未完成，现有 smoke/pilot 计时只验证流程。算例、profile、步数和重复次数以 `tests/benchmark/manifest.json` 为准。[CPU/NUMA 绑定](cpu-binding.md)

外部单位和张量语义依据：[GPUMD typewise cutoff](https://gpumd.org/dev/nep/input_parameters/cutoff.html)、
[LAMMPS `units metal`](https://docs.lammps.org/units.html)、
[LAMMPS `compute stress/atom`](https://docs.lammps.org/compute_stress_atom.html)。
