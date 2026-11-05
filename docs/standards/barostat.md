# 各向同性 NPT 控压器

类别：实现合同与验收边界。更新日期：2026-11-04。NPT 路由限全量复制模式、各向同性、正交全周期盒；G-B 已通过。`npt_scr` 统计支持范围限于 216 原子 NEP-C、300 K/0 GPa、单节点冻结矩阵；NEP5/ZBL 只有另行记录的短程合同证据。阶段证据见 [NPT 矩阵报告](../status/validation/npt-validation.md#npt-matrix-20261104)、[B0 压力 oracle](../status/validation/npt-validation.md#pressure-oracle-20261103)、[B1 盒合同](../status/validation/npt-validation.md#box-contract-20261103)、[B2 Berendsen 报告](../status/validation/npt-validation.md#npt-ber-20261103) 和 [n3 最终确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)。

## 输入和边界

两种各向同性输入使用 8-token 形式：

```text
ensemble npt_ber T1 T2 Tdamp P B tau_p
ensemble npt_scr T1 T2 Tdamp P B tau_p
```

`T1/T2` 是 K，必须为有限正数；`Tdamp` 与 `tau_p` 是步数，必须为有限且不小于 1；目标压力 `P` 和控压体积模量 `B` 以 GPa 输入，`P` 可为负，`B` 必须为正。`B` 是 barostat 控制参数，不等同于所用材料实测的 bulk modulus。压力内部单位为 eV/Å³，输出 thermo 压力为 GPa，换算常数为 160.2177。12/18-token 各向异性形式、triclinic 和非周期盒不支持。

NPT 只走全量复制模式。P1 使用全量复制模式；P2/P4 即使几何满足局部域分解条件也必须留在全量复制模式，报告 `DMGMD_DOMAIN mode=m1 reason=npt-m1-only`。直接进入局部域分解模式下的 NPT 路径必须失败关闭。适用矩阵为 P1/P2/P4、HostStaged/CudaAware；CudaAware 仅在 MPI/CUDA 环境自检通过时执行。NPT 不扩展到 LJ、EAM 或其他势。

现有 NVT `nvt_nhc`/`nvt_bdp` 的数值、root 状态、seed、run/restart 和通信合同不因 NPT 改动。`npt_scr` 按其算法组合 BDP 速度重标定与随机控压；这不表示 NHC 被用于 NPT，也不改变独立 `nvt_bdp` 的合同。

## 时间顺序与输出

每步先完成 VV2 并采集同一步 thermo/压力，之后由 thermostat 缩放 owned 速度，再计算盒因子并缩放盒和 wrapped positions。SCR 的随机调用顺序是 root 先推进 BDP 所需随机数，再从同一状态调用压力项 `gasdev`；压力和体积仍取 thermostat/barostat 更新前的同一步样本。速度缩放只来自 thermostat；barostat 不缩放速度。盒缩放不作用于 `unwrapped_position`，这是兼容限制。

输出时点按字段区分如下：

| 输出 | VV2/缩放前状态 | thermostat 后、barostat 后状态 |
| --- | --- | --- |
| `thermo.out` | T、KE、PE、六个 stress 分量（压力由三个对角分量均值导出） | 九个 box 分量 |
| XYZ | force、energy、virial | box 与 wrapped positions；velocity 经 thermostat 缩放后输出 |
| `restart.xyz` | 不保存 force、virial 或能量 | box、wrapped positions、thermostat 后 velocity 与静态质量/group 信息 |

盒缩放后不立即重算 force/virial；不可要求同一行的压力与新盒重新计算后完全自洽。`unwrapped_position` 不随 barostat 缩放。`restart.xyz` 保存缩放后的盒、位置和当前速度，不保存 force、virial、系综 RNG 或控制器隐藏状态。

`B>2000 GPa` 走固定盒兼容分支。Berendsen 的固定盒行为已在 B2 实测。SCR 分支按其预注册合同消费对应 Gaussian 随机数，即使盒因子最终为 1；B3 已确认，不得因零压力系数跳过 RNG 调用而改变后续随机流。

## `npt_ber`

各向同性缩放因子沿用 GPUMD 兼容式 `1 - pc * (P_target - P_inst)`，其中 `pc = 160.2177 / (3 * tau_p * B_GPa)`；因子必须有限且为正。压缩/扩张方向只按测得的瞬时压力差决定。Berendsen 用于 GPUMD 兼容，不以其涨落证明严格 NPT 采样。

B2 已通过 P1/P2/P4、双后端路由、216/2016 原子 200-step GPUMD golden、压力方向、负目标压力、B>2000 固定盒、restart 文件和错误输入矩阵。单长 run、同进程分段与文件 restart 均有实际数值差异；restart 成功重启不等于轨迹连续。具体差异及命令见 [B2 报告](../status/validation/npt-validation.md#npt-ber-20261103)。

## `npt_scr` 与随机状态

SCR 复用 BDP thermostat，并在 root 持有连续 RNG 流与 Gaussian 缓存；每段新 `run` 或新 restart 作业重建控制器和 RNG 状态。默认 seed 与 `DMGMD_ENSEMBLE_SEED` 语法沿用 BDP 标准。SCR 随机增量的顺序固定为：BDP 使用随机流后，压力项从同一 root 流调用 `gasdev`；只广播状态和所需标量，不在各 rank 独立取样。它与上游按值 RNG 有意不同，统计判定只证明冻结体系和预算下与同条件 GPUMD 参考相容，不宣称普遍严格采样或遍历性。

锁定 GPUMD release 的 SCR RNG 以 `std::chrono::system_clock::now().time_since_epoch().count()` 构造 `std::mt19937`；DEBUG 构建才使用固定 seed 12345678。独立 GPUMD 作业按秒串行启动并记录 UTC 起止、PID 和源码/启动证据，是防止参考作业重合的保守执行规则，不代表直接观测或控制了其内部 seed。SCR 的参考输入字节相同，但候选与参考轨迹不按逐步随机流配对。

原注册的18个 formal look 总体 INCONCLUSIVE 结论保持不变。独立确认与 G-B 的通过范围严格限于 216 原子 NEP-C、300 K/0 GPa、单节点 全量复制各向同性正交全周期盒：base 为 P1/HostStaged 与 P2/P4 的 HostStaged/CudaAware；half-dt 仅为 P1/HostStaged。候选每个 seed 使用 `DMGMD_ENSEMBLE_SEED` 显式设置的注册 seed，冻结分析按原方法判断均值、方差及体积分布门；理论目标偏差单列，不改变兼容判定。该结果不证明普遍严格 NPT 采样、遍历性、非 NEP、RDMA 或跨节点支持。完整结果见[n3最终确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)和[机器索引](../status/validation/npt-scr-n3-confirmation-index.json)。

## 分段与 restart

`run` 结束后 measurement 声明清空，系综配置、当前盒、位置和速度延续；输出命令需在下一段重新声明。NPT 轨迹不承诺 split-run 或文件 restart 连续。`restart.xyz` 可作为新 `model.xyz` 启动，但不包含积分器的旧力缓存或 SCR RNG 状态；文件可读/作业可启动与轨迹相同是两个不同结论。

## 当前验收状态（2026-11-04）

| 范围 | 状态 | 证据 |
| --- | --- | --- |
| B0 压力、单位、符号和列布局 | PASS | [pressure oracle](../status/validation/npt-validation.md#pressure-oracle-20261103) |
| B1 全量复制盒变更、preflight、所有权/邻居刷新 | PASS | [box contract](../status/validation/npt-validation.md#box-contract-20261103) |
| B2 Berendsen 与分类矩阵 | PASS | [npt-ber](../status/validation/npt-validation.md#npt-ber-20261103) |
| B3 SCR 生产集成与冻结统计矩阵 | PASS；原18-look总体 INCONCLUSIVE 历史保留 | [n3最终确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104) |
| B4/G-B 完整集成、错误与回归矩阵 | PASS | [NPT 矩阵报告](../status/validation/npt-validation.md#npt-matrix-20261104) |

`npt_scr` 只在上文列出的冻结 NEP-C 单节点矩阵获得统计支持。该有限范围 PASS 不等于 v1.0 发布通过；C/D 尚未启动。
