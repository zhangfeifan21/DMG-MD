# 系综与恒温器

类别：现行实现合同。更新日期：2026-11-04。适用于锁定 GPUMD
`9d23496e41319b9e2af5221a7df6285387401d1e` 的普通 NEP4/5、NEP-ZBL 路径。
验收范围与统计限制见 [NHC 记录](../status/validation/thermostat-validation.md#nhc-20261102)、
[BDP 记录](../status/validation/thermostat-validation.md#bdp-20261102)和[双路径矩阵](../status/validation/thermostat-validation.md#thermostat-matrix-20261102)。

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

每段以零基 `step=0,...,steps-1` 调度：

```text
fraction = double(step) / double(steps)
T_target = T1 + (T2 - T1) * fraction
```

实现先求 `fraction=double(step)/double(steps)` 再乘温差，以保留原浮点顺序。
首步使用 T1，末步通常没有达到 T2；新段重新从零调度。稳态分布验收使用 T1=T2，
ramp 的短程功能检查不能替代平衡分布证据。

所有路径沿用 `T = Σ_owned m*v²/(3N*k_B)`，其中 N 为全局原子数，
`k_B=8.617343e-5 eV/K`。不扣除质心自由度；ghost 和全量复制模式的其他 rank 副本不参与求和。
`velocity` 初始化会修正全局动量，因此去质心初速不能直接作为具有 3N 个独立动量的统计 oracle。
测试中的正则矩使用显式独立 Gaussian 初速、无约束且无速度修正的相互作用体系。

## Berendsen

`nvt_ber` 保留原表达式和输出顺序：`coupling=1/Tdamp`，仅当 `coupling>1e-5` 时，
VV2 和全局 thermo 之后将 owned 速度乘
`sqrt(1 + coupling*(T_target/T_inst - 1))`。各 rank 对同一全局温度计算因子，
不分配链或随机数状态，也不增加 collective。Berendsen 的涨落不用于证明正则采样。

## Nosé–Hoover 链

`nvt_nhc` 使用四节链、七项 Suzuki–Yoshida 权重和四次 RESPA 子步，表达式顺序复现
锁定上游的 `ensemble_nhc.cu::nhc`。段首链位置为零、链动量为 `[1,-1,1,-1]`。
令 `dt_internal=dt_fs/10.18051`，则：

```text
tau = dt_internal * Tdamp
Q_i = k_B * T1 * tau * tau
Q_0 *= 3N
```

质量在段首固定，ramp 不重建质量；每步更新中的 `kT` 使用当前目标温度。
链仅由 rank 0 持有。VV1 **之前**归约当前 owned 动能，root 更新链前半步并广播速度因子；
完成 VV1、位置/halo/力、VV2 和原有全局 thermo 后，再更新链后半步并广播因子。
两个因子均只缩放当前 owned 速度。非 root 没有第二份链。

动能为零或非有限、链质量/动量或缩放因子异常时作业失败。root 的更新错误与因子通过
同一状态广播分发，随后各 rank 进入错误出口，避免 peer 永久等待。

## Bussi–Donadio–Parrinello 重标定

`nvt_bdp` 在 VV2 和全局 thermo 后运行一次随机重标定，没有前置动能归约。
沿用上游 `svr_utilities.cuh` 的 `gasdev`、`gamdev`、`resamplekin_sumnoises` 和
`resamplekin`，`ndeg=3N`、`sigma=3N*k_B*T_target/2`、`taut=Tdamp`，
将 owned 速度乘 `sqrt(new_KE/old_KE)`。缓存的 Gaussian 值属于 controller 实例，
不会被另一个 controller 或新段继承。

仅 rank 0 持有 `std::mt19937` 并取样；每步广播状态和速度因子，不广播 RNG 状态。
默认 seed 为 **12345678**，可由 `DMGMD_ENSEMBLE_SEED` 覆盖。这是为复现而采用的
有意兼容偏离：锁定 GPUMD 的 release 构建使用 wall-clock seed，其 DEBUG 分支才用该固定值。
因此 release GPUMD 的 BDP 对照比较分布，不比较逐步随机轨迹。

环境变量只接受十进制数字字符串，数值范围 `0..4294967295`；空串、符号、空白和溢出拒绝。
所有 rank 必须具有完全相同的原始字符串和设置状态：`01` 与 `1` 不同，unset 与显式默认值也不同。
在整个作业均 unset 与均显式设为 `12345678` 的两次运行，科学输出应一致。
非 BDP 作业不解析这个变量。

`old_KE<=0`、非有限或负的 `new_KE`、非有限或非正的因子会在速度缩放前失败。
同二进制、同输入、同 seed、同 rank/backend 的重复运行可复现；不承诺跨工具链的
`std::uniform_real_distribution` 序列相同。

## 并行、限制与通信

P1 使用全量复制模式；P2/P4 按真实几何判据进入全量复制回退路径或局部域分解模式，支持 HostStaged 和通过环境门的
CudaAware。没有测试专用强制路由。单节点已验收范围不推导出跨节点保证。
势函数支持仍限 NEP4/5 与 NEP-ZBL；fixed/move group 仍不支持。`npt_ber` 的已验收范围、输出时点、重启限制及全量复制路径见[控压器标准](barostat.md)。`npt_scr` 的 G-B 已通过，但统计支持仅限 216 原子 NEP-C、300 K/0 GPa、单节点全量复制模式下的各向同性正交全周期矩阵：base 为 P1/HostStaged 与 P2/P4 双后端，half-dt 仅 P1/HostStaged；NEP5/ZBL 仅有另行记录的短程合同证据。原注册18个 formal look 总体 INCONCLUSIVE 的历史结论保留；本次结果不外推至普遍严格 NPT 采样、遍历性、RDMA 或跨节点。详见[NPT/G-B矩阵](../status/validation/npt-validation.md#npt-matrix-20261104)、[n3最终确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)与[机器索引](../status/validation/npt-scr-n3-confirmation-index.json)。

NHC/BDP 与 `correct_velocity`、带第二参数的自适应 `time_step` 组合会在运行前拒绝。
还要求以下实验选项为默认值（unset 也接受）：

| 变量 | 接受的显式值 |
| --- | --- |
| `DMGMD_DOMAIN_PARTITION` | `off` |
| `DMGMD_DOMAIN_OVERLAP` | `off` |
| `DMGMD_DOMAIN_SCHEDULE` | `split` |
| `DMGMD_OVERLAP_TRACE` | `0` |

新路径不改变 NVE/Berendsen 的通信和数值行为。相对每步反馈的 Berendsen，NHC 增加一次
1-double owned KE Allreduce 和两次 2-double 状态/因子 Bcast；BDP 只增加一次这样的 Bcast。
启动时 BDP 另做 seed 一致性控制通信，不计入逐步费用；详见
[MPI 记账](mpi-runtime.md)。这些是算法载荷与调用计数，不是性能承诺。

## 分段、输出与 restart

每段 `run` 新建 controller：NHC 链和质量重建；BDP RNG 与 Gaussian 缓存重置。
同一固定 seed 在多段运行中会重复使用同一随机序列。连续物理过程如需连续随机流，应在一个
`run` 中执行；跨段持续流不是当前合同。

thermo 使用 VV2 后、末次恒温缩放前的全局量；XYZ/restart 的 velocity 是缩放后的 owned 速度。
两者处于不同反馈时点，不能将同一步 thermo 的 KE 与 XYZ 速度直接要求逐位相等。

`restart.xyz` 仍只有原兼容状态字段，不保存链、RNG/cache 或时间。将它作为新 `model.xyz`
可以在不同 rank 数启动新作业；NHC/BDP 的轨迹**不连续**，不能用它恢复隐藏状态。
验收检查新作业能运行和输出完整，不声称与原作业连续延伸逐步相同。

## 统计证据的适用范围

验收入口为 [thermostat 测试](../../tests/thermostats/README.md)。输入哈希、所有 seed、
丢弃段、块长度、三次预定采样预算、误差界和最低 ESS 由 manifest 冻结。
动能的参考矩为 `E[K]=3N*k_B*T/2`、`Var[K]=3N*(k_B*T)^2/2`；
速度分量以每帧 `mean_i(m_i*v_axis²)` 检查目标 `k_B*T`。
CI 同时计入块间及独立 seed 间离散，完整落在误差带内才 PASS；区间太宽记 INCONCLUSIVE。
减半步长同时加倍步数和 Tdamp，以保持物理采样时间和耦合时间。

轨迹相容、总 K 矩通过和所有动量方向都能充分混合是不同结论。
额外的无相互作用谐振子诊断在固定预算内总 K 通过，但轴向均值仍 INCONCLUSIVE；
它不能作为普遍遍历性的证明。实际 NEP 体系的分量验收另列在 NHC/BDP 记录，
不以缩窄该诊断 CI、改 seed 或只报告总 K 来替代。
相同模型/预算的 [GPUMD 与 LAMMPS 对照](../status/validation/thermostat-validation.md#thermostat-reference-controls-20261103)
也保留轴向 INCONCLUSIVE；对照之间的一致非通过不构成该诊断的 PASS。
GPUMD 原生对照使用明确记录的外场/seed/观测测试接口，不是 stock FCP 输入能力声明。
