# 同卡数引擎的身份与 NPT 可比工况怎样核验

Status: resolved
Type: grilling
Label: wayfinder:grilling
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)

## Question

普通 GPUMD 和 LAMMPS/KOKKOS/NEP 在 P=1、2、4 的作业直接使用已安装的程序，报告登记实际版本。如何从作业日志核实普通 GPUMD 的多卡 NEP、LAMMPS 的 `matpl/nep/kk`、所用势和实际 GPU 数？WPE 的单卡作业须能识别 Wisevolve 后端。[L20 已测构建](../../../docs/standards/cross-engine-reference.md#benchmark-engines)

对金刚石碳、致密水和 FCC 铜逐项确定 SCR 生产、Berendsen 预平衡中哪些组合满足相同控制算法、耦合参数、模型和输出条件。当前 LAMMPS `npt/kk` 使用 Nosé–Hoover，Berendsen 组合仍待物理验证。哪些格可以配对计时，哪些只能列不适用及原因？NVE 结果只归入其实际工况。[三引擎参考标准](../../../docs/standards/cross-engine-reference.md) · [三材料 NPT 矩阵](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)

## Answer

2026-11-25，维护者确认第 1–9 问。查核时的源码提交为 `e46ef67`。源码事实取自已安装的 GPUMD 5.9.1 源码与 LAMMPS 22 Jul 2025 Update 4（MatPL `21fabf34`）源码，日志标记取自 2026-11-23 L20 碳 NVE 短程作业（7 个作业）。以下核验规则尚未在 NPT 作业上实测，由实施工单 [06 普通 GPUMD 同卡数 NPT 先导](../../v1-release-implementation/issues/06-gpumd-matched-npt-pilot.md)、[07 LAMMPS/KOKKOS/NEP 同卡数 NPT 先导](../../v1-release-implementation/issues/07-lammps-kokkos-nep-npt-pilot.md) 和 [08 GPUMD-WPE 单卡 NPT 先导](../../v1-release-implementation/issues/08-wpe-single-gpu-npt-pilot.md) 落实。

**已核对的事实（源码可确认）**

- 普通 GPUMD 的 `force.cu` 只在势为 `nep4`/`nep4_zbl`（及 temperature 变体）且可见 GPU 数大于 1 时构造 `NEP_MULTIGPU`，构造时打印 `Try to use P GPUs for the NEP part.`。卡数由 `CUDA_VISIBLE_DEVICES` 决定。`NEP_MULTIGPU::compute` 每步按当前盒计算 rc/2 格数，最长方向每卡少于 10 格或任一周期方向少于 3 格时 `exit(1)`。NPT 盒收缩可能在运行中触发该退出。
- 三种势都是 NEP4，cutoff 行均为统一格式：碳 `nep4 1 C`（7/4 Å），水 `nep4 2 O H`（6.0/4.0 Å），铜为 16 元素 `nep4_zbl`（6/5 Å，`zbl 1 2`）。LAMMPS 转换不涉及 typewise cutoff 折叠。MatPL `nepkk.cu` 接受 `nep4_zbl`。
- Berendsen 温控两边公式相同（GPUMD `sqrt(1+(T0/T-1)/τ_T)`，LAMMPS `temp/berendsen` `sqrt(1+dt/t_period·(T0/T-1))`）。控压缩放不同：GPUMD 每轴乘线性因子 `1-(p0-p)/(3Kτ_p)`，LAMMPS `press/berendsen` 用 `pow(1-dt/p_period·(p0-p)/K, 1/3)`，一阶相同、二阶不同。
- 本构建 `lmp -h` 列出 `nve/kk`、`temp/berendsen/kk`、`press/berendsen`、`npt/kk`、`press/langevin`。`press/berendsen` 没有 KOKKOS 版本。没有 SCR 实现。
- 现有 `tests/benchmark/run_benchmark.py` 已检查：GPUMD 的 `Try to use P GPUs` 有无，WPE 的 `Potential engine: Wisevolve Potential Engine` 标记，LAMMPS 的 `KOKKOS mode`、`pair matpl/nep/kk`、`NEP Kokkos potential ... loaded successfully` 与逐 rank `LAMMPS_GPU_BIND` UUID 互异，DMG-MD 的 `DMGMD_MPI` UUID 与 `DMGMD_DOMAIN mode`。GPUMD/WPE 日志只打印 `number of GPUs = N`，不打印 UUID。

**决议**

1. **普通 GPUMD 的身份。** 作业同时满足以下条件才计入：P>1 时 `Try to use P GPUs for the NEP part.` 恰好出现一次且 `number of GPUs = P`，P=1 时没有该行；势行与材料一致（`Use the NEP4 potential with K atom type(s)`，铜另有 ZBL 行）；两段都有 `Time used for this run`。GPU 身份用 `CUDA_VISIBLE_DEVICES` 中的 UUID，再以 telemetry 交叉确认选中的卡有显存占用、未选中的卡空闲。
2. **LAMMPS/KOKKOS/NEP 的身份。** 保留现有检查，另加：`Loop time ... on P procs` 的 rank 数、步数、原子数正确；日志中实际出现的 fix 名称与登记的组合一致（如 `nve/kk`、`temp/berendsen/kk`、`press/berendsen`）；铜核对 `pair_coeff` 的元素映射，类型 1 对应 16 元素势中的 Cu。
3. **GPUMD-WPE 的识别。** `Potential engine: Wisevolve Potential Engine 1.0.0` 恰好出现一次，`number of GPUs = 1`，没有 `Try to use`。记录宿主版本行和二进制 SHA-256。宿主为 `a0e55bc`，不是 5.9.1，报告注明其 `npt_scr`/`npt_ber` 来自该宿主。
4. **势身份。** 每个作业目录的 `nep.txt` 计算 SHA-256，与[三种公开算例按什么规模和快照冻结](01-public-material-inputs.md)冻结的哈希比对。LAMMPS 另登记转换后文件的哈希和转换规则。三种势都不需要折叠 cutoff 行，转换只涉及格式。哈希不符的作业作废，不计时。
5. **SCR 生产的可比格。** 普通 GPUMD 在 P=1、2、4 与 DMG-MD 同卡数配对。WPE 以 P=1 分别对 DMG-MD P=1、2、4 配对。LAMMPS 1、2、4 卡均列为不适用，原因是本构建没有 SCR，`npt/kk` 为 Nosé–Hoover，`press/langevin` 是另一种算法。不另行计时 LAMMPS 的 Nosé–Hoover NPT，避免被当作 SCR 结果引用。
6. **LAMMPS Berendsen 的配对条件。** 参数映射为 `t_period = 40·dt`、`p_period = 1000·dt`、`modulus` 取同一 K（换算成 bar）、`iso` 控压，dt 与 GPUMD 相同（碳、水、铜依次为 1、0.5、1.5 fs）。从同一冻结快照出发，GPUMD 与 LAMMPS 的弛豫方向一致，平台段均温、均压、均体积之差在预先登记的容差内。容差在先导阶段由 GPUMD 自身重复运行定出，不为通过而放宽。控压缩放一阶相同、二阶不同，写入报告。任一材料不通过，该材料的 LAMMPS Berendsen 格列为不适用并写明原因。
7. **主机端 `press/berendsen`。** 照常计时，这是 LAMMPS 用户可用的组合。报告在该格注明 `press/berendsen` 没有 KOKKOS 版本、在主机执行。
8. **补充 NVE。** 三种材料都补一组四引擎同卡数 NVE，使用冻结快照，单列一张表并注明系综，不进入 NPT 验收，不与 NPT 倍率合并。重复次数按[三项对标怎样采样并据此选择局部 NPT 缓存策略](04-dn5-timing-and-adoption.md)的区组规则，GPU·小时预算随地图上待定的总预算一起确定。
9. **核验失败。** 第 1–4 条任一项不满足，或作业中途退出（含盒收缩后 `NEP_MULTIGPU` 退出），该作业判为无效，不计时，修正后重跑。若属结构原因（如盒收缩到多卡格子门槛以下），该格改为不适用，写明原因并登记日志位置。
