# 系综与恒温器

普通 NEP4/5 和 NEP-ZBL 可运行 NVE 以及三种 NVT 恒温器。NVE 按力推进坐标和速度。NVT 还根据瞬时动能调节速度。三个恒温器的区别在于何时读取动能、谁保存控制状态，以及能用哪些统计量验证。

恒温器复现的 GPUMD 源码来自 `9d23496e41319b9e2af5221a7df6285387401d1e`。本文于 2026-11-20 核对。L20 短程 golden 使用的参考二进制见[兼容矩阵](compatibility-matrix.md)。实测条件见 [NHC 验收](../status/validation/thermostat-validation.md#nhc-20261102)、[BDP 验收](../status/validation/thermostat-validation.md#bdp-20261102)和[两种并行模式的测试](../status/validation/thermostat-validation.md#thermostat-matrix-20261102)。

## 输入、温度与时间单位

```text
ensemble nve
ensemble nvt_ber T1 T2 Tdamp
ensemble nvt_nhc T1 T2 Tdamp
ensemble nvt_bdp T1 T2 Tdamp
```

每条命令选定后续 `run` 的系综；允许覆盖配置和多段运行。三种 NVT 的温度为有限正数，
单位 K；`Tdamp` 为有限实数且 ≥1，单位是**积分步数**。`time_step` 的输入单位为 fs。
NHC/BDP 要求固定、有限且正的步长；默认步长为 1 fs。输入格式和输出列见
[兼容矩阵](compatibility-matrix.md)。

每一段从 `step=0` 开始，以 `steps-1` 结束。对线性变温输入，目标温度按以下顺序计算。

```text
fraction = double(step) / double(steps)
T_target = T1 + (T2 - T1) * fraction
```

实现先求 `fraction=double(step)/double(steps)` 再乘温差，以保留原浮点顺序。
首步使用 T1，末步通常没有达到 T2；新段重新从零调度。稳态分布验收固定 T1=T2。变温斜坡的短程测试只检查目标温度能否按步调度。

所有路径沿用 `T = Σ_owned m*v²/(3N*k_B)`，其中 N 为全局原子数，
`k_B=8.617343e-5 eV/K`。分母按 3N 个自由度计算，不扣除质心项。每个原子只由负责它的 rank 计入，边界副本和其他 rank 上的完整副本都不参与求和。
`velocity` 初始化会修正全局动量，因此去质心初速不能直接作为具有 3N 个独立动量的统计 oracle。
测试中的正则矩使用显式独立 Gaussian 初速、无约束且无速度修正的相互作用体系。

## Berendsen

Berendsen 恒温器按瞬时温度和目标温度的偏差调节速度。设置 `coupling=1/Tdamp`。仅当 `coupling>1e-5` 时才执行缩放：先完成速度 Verlet 的后半步（VV2），求出全局热力学量，再将负责原子的速度乘以 `sqrt(1 + coupling*(T_target/T_inst - 1))`。

各 rank 用相同的全局温度得到相同的因子。该步骤不分配链或随机数状态，也不增加集合通信。Berendsen 的涨落不作为正则采样的证明。

## Nosé–Hoover 链

`nvt_nhc` 使用四节链、七项 Suzuki–Yoshida 权重和四次 RESPA 子步，表达式顺序复现
锁定上游的 `ensemble_nhc.cu::nhc`。每段开始时把链位置置零，链动量设为 `[1,-1,1,-1]`。链质量由该段起始目标温度和耦合时间决定。令 `dt_internal=dt_fs/10.18051`，按以下顺序计算。

```text
tau = dt_internal * Tdamp
Q_i = k_B * T1 * tau * tau
Q_0 *= 3N
```

每一段的链质量固定。即使目标温度逐步变化，也只更新该步的 `kT`。
只有 rank 0 保存链。在 VV1 之前，各 rank 对当前负责原子的动能求和。rank 0 用全局动能推进链的前半步，广播速度因子。完成 VV1、更新坐标、交换边界数据、求力、VV2 并统计全局热力学量后，rank 0 再推进链的后半步并广播因子。两次缩放都只写本 rank 负责的原子速度。

动能为零或非有限、链质量/动量或缩放因子异常时作业失败。root 的更新错误与因子通过
同一状态广播分发，随后各 rank 进入错误出口，避免 peer 永久等待。

## Bussi–Donadio–Parrinello 重标定

`nvt_bdp` 在 VV2 和全局 thermo 后运行一次随机重标定，没有前置动能归约。
沿用上游 `svr_utilities.cuh` 的 `gasdev`、`gamdev`、`resamplekin_sumnoises` 和
`resamplekin`，`ndeg=3N`、`sigma=3N*k_B*T_target/2`、`taut=Tdamp`，
将 owned 速度乘 `sqrt(new_KE/old_KE)`。缓存的 Gaussian 值属于 controller 实例，
不会被另一个 controller 或新段继承。

仅 rank 0 持有 `std::mt19937` 并取样；每步广播状态和速度因子，不广播 RNG 状态。
默认 seed 为 `12345678`，可由 `DMGMD_ENSEMBLE_SEED` 覆盖。这是为复现而采用的
兼容差异。锁定 GPUMD 的 release 构建用系统时钟设置 seed，DEBUG 分支才用该固定值。
因此 release GPUMD 的 BDP 对照比较分布，不比较逐步随机轨迹。

环境变量只接受十进制数字字符串，数值范围 `0..4294967295`；空串、符号、空白和溢出拒绝。
所有 rank 必须具有完全相同的原始字符串和设置状态：`01` 与 `1` 不同，unset 与显式默认值也不同。
在整个作业均 unset 与均显式设为 `12345678` 的两次运行，科学输出应一致。
当前仅在 `run.in` 含 `nvt_bdp` 或 `npt_scr` 声明时解析该变量。

`old_KE<=0`、非有限或负的 `new_KE`、非有限或非正的因子会在速度缩放前失败。
同二进制、同输入、同 seed、同 rank/backend 的重复运行可复现；不承诺跨工具链的
`std::uniform_real_distribution` 序列相同。

## 并行、限制与通信

P1 使用全量复制模式。P2/P4 对 NVE/NVT 按实际几何进入全量复制模式或局部域分解模式。
已验收 HostStaged 和通过环境门的 CudaAware 单节点组合。fixed/move group 仍不支持。

若同一份输入声明 NPT，默认整份作业走全量复制。NPT 的算法、重启限制及 216 原子 SCR
统计范围见[控压器标准](barostat.md)。显式启用的局部 NPT 有独立的 5760 原子 SCR 统计范围，
详见[局部 NPT 标准](domain-npt.md)。两个矩阵均为单节点冻结实验。

NHC、BDP 和两种 NPT 禁止组合 `correct_velocity` 或带第二参数的自适应 `time_step`。
以下环境变量只接受默认值或未设置。

| 变量 | 接受的显式值 |
| --- | --- |
| `DMGMD_DOMAIN_PARTITION` | `off` |
| `DMGMD_DOMAIN_OVERLAP` | `off` |
| `DMGMD_DOMAIN_SCHEDULE` | `split` |
| `DMGMD_OVERLAP_TRACE` | `0` |

以每步做温度反馈的 Berendsen 为基线，NHC 每步多一次动能 Allreduce（1 个 double），以及两次状态和因子 Bcast（每次 2 个 double）。BDP 每步只多一次这样的 Bcast。NVE 和 Berendsen 保留原来的计算与通信。
启动时 BDP 和 SCR 另做 seed 一致性控制通信。不计入逐步费用，详见
[MPI 记账](mpi-runtime.md)。这些是算法载荷与调用计数，不是性能承诺。

## 分段、输出与 restart

每段 `run` 开始时新建控制器。NHC 重新初始化链和质量，BDP 重置 RNG 和高斯缓存。使用固定 seed 时，新段从同一随机序列起点开始。需要一条连续随机流的物理过程应放在同一段 `run` 中。

thermo 取 VV2 后、末次恒温缩放前的全局量。XYZ/restart 写出的速度已经过缩放。因此，由同一步 XYZ 中的速度重算动能，使用的速度与 thermo 采样时不同。

`restart.xyz` 可用于不同 rank 数的新作业，但只保存兼容格式定义的字段。链、RNG、高斯缓存和时间均未存盘。验收检查新作业能否启动、输出是否完整。NHC/BDP 从文件重启的轨迹不保证连续。

## 统计证据的适用范围

验收入口为 [thermostat 测试](../../tests/thermostats/README.md)。输入哈希、所有 seed、
丢弃段、块长度、三次预定采样预算、误差界和最低 ESS 由 manifest 冻结。
动能的参考矩为 `E[K]=3N*k_B*T/2`、`Var[K]=3N*(k_B*T)^2/2`；
速度分量以每帧 `mean_i(m_i*v_axis²)` 检查目标 `k_B*T`。
CI 同时计入块间及独立 seed 间离散，完整落在误差带内才 PASS；区间太宽记 INCONCLUSIVE。
减半步长同时加倍步数和 Tdamp，以保持物理采样时间和耦合时间。

总动能的矩和各方向速度分量要分别检查。在额外的无相互作用谐振子诊断中，总动能在预定预算内通过，轴向均值仍为 INCONCLUSIVE。这项结果无法证明普遍遍历性。实际 NEP 体系的分量验收另列在 NHC/BDP 记录中，仍按原 seed 和置信区间评估。

同模型、同预算的 GPUMD 和 LAMMPS 对照也得到轴向 INCONCLUSIVE，各程序出现相同结果仍不足以判为 PASS。GPUMD 原生对照用的是有记录的外场、seed 和观测测试接口，不能据此把 FCP 列为原版支持的输入。[参考程序对照](../status/validation/thermostat-validation.md#thermostat-reference-controls-20261103)
