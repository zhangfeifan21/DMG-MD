# LAMMPS 多 GPU + MPI NEP 性能对比计划

类别：待实施计划。状态：**IN PROGRESS**。优先级：**P0**（2026-10-09 新增）。

## 已实现的对照入口

锁定 LAMMPS `stable_22Jul2025_update4`、MatPL commit
`21fabf34eadc57a717f4c7fbce9bb80aad5fd991` 与 GPU NEP pair style
`matpl/nep/kk`。参考源码保持只读；经验证的补丁和构建产物放在 `newmd` 内。
安装与构建状态、GPU 静态物理量和短 benchmark smoke 见
[2026-10-09 验证记录](../status/lammps-reference-20261009.md)。

- `tests/benchmark/run_benchmark.py` 已加入 `lammps` engine 和 `--lammps-bin`，
  与 `gpumd`/`dmgmd` 按同一个 manifest、case、seed、设备顺序、warmup、steps、
  repeats 编排；LAMMPS 与 GPUMD 各自保留引擎身份。
- 从同一 `model.xyz` 转换 LAMMPS data，保留原子 ID、类型、坐标、速度、盒长和单位；
  同一 `nep.txt` 用 `pair_style matpl/nep/kk`，对齐 NVE、时间步、邻域/skin、
  thermo 输出频率与 MPI/GPU 数。`units metal` 的时间单位为 ps：现有 0.1 fs
  时间步写成 `timestep 0.0001`，`model.xyz` 的 Å/fs 速度乘 1000 写入 data 的
  Å/ps；DMG-MD 当前 neighbor skin 为 1 Å，LAMMPS 不能沿用 metal 默认 2 Å。
  转换器记录原始/目标哈希，静态比较器逐字段校验。横向通过条件是**物理量等价，不是文件
  哈希一致**；typewise cutoff 仅在逐元素径向值全相同、角向值全相同时折叠为
  统一行，其他情况拒绝转换。详见[三引擎对照标准](../standards/cross-engine-reference.md)。
- 每个 rank 只使用对应 GPU UUID，使用 `-k on g 1 -sf kk`，核验实际
  `matpl/nep/kk` KOKKOS GPU 路径；当前 smoke 尚未提供受控 CPU/NUMA 绑定。

## 尚需完成的 P0 验收

- 采用固定 CPU 核数与 NUMA 策略（见 [MPI CPU 绑定计划](./mpi-cpu-binding.md)），
  LAMMPS/DMG-MD 各 K 核/rank，GPUMD 单进程使用相同总量 P×K 核。三者均保存实际
  affinity、GPU UUID、MPI/UCX/CUDA 版本、
  编译精度、温度/时钟及原始日志。独占节点、相同 GPU 集合、随机化 trial 顺序。

GPUMD/LAMMPS 静态对照已经覆盖 baseline 四个 case 和 long-NVE 七个 case，
比较了相同初态的
每原子能量、力、virial 和总势能，并通过机器可读容差；现有 GPUMD committed
golden 字节级门槛仍独立执行。还需验证三引擎短程 NVE 的动能/势能趋势、有限值、
输出原子数及长程守恒/统计量。任何 pair style 回退、
GPU 未参与、势不兼容或物理量未过门槛的 trial 不进入正式速度比。

LAMMPS 1/2 GPU 与三引擎单 GPU smoke 已证明编排和 GPU NEP 路径可执行；因缺少固定
CPU/NUMA 绑定，不发布正式效率结论。下一步用现有 carbon_200k、carbon_1m、
water_400k 的 1/2/4/8 GPU 强扩展矩阵，至少五次独立重复；固定每 rank CPU
核数和同一 NUMA 策略。
预热与正式段分开；LAMMPS 的计时以其完整 `run` 循环墙钟为主，同时保存 LAMMPS
Loop time/性能分项、外层进程墙钟与 DMG-MD 对应 run 段。若实现无法对齐起止边界，
明示差异，不用不同口径的子阶段直接排名。报告各引擎的 ms/step、atom-step/s、
自身 P=1 强扩展与同 P 的速度比、重复波动和缺失项；单节点结果不外推到跨节点。

本计划不修改 DMG-MD 的 NEP 数学或三个 reference checkout；它们均与
`newmd` 并列且严格只读。通信计算重叠 P0 的
同步/重叠 A/B 继续使用同一固定 CPU 绑定配置，LAMMPS 作为外部性能参照，不充当
DMG-MD 内部 overlap 正确性 oracle。

外部接口与单位依据：[MatPL GPU NEP 接口](https://github.com/LonxunQuantum/lammps-MatPL)、
[LAMMPS KOKKOS 说明](https://docs.lammps.org/Speed_kokkos.html)、
[LAMMPS metal 单位](https://docs.lammps.org/units.html)。
