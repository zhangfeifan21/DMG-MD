# 各向同性 NPT 控压器

两种控压器都在步末按同一因子缩放三个盒长，只适用于正交、全周期盒。`npt_ber` 按压力差调整盒长，常用于预平衡。`npt_scr` 还抽取随机数。解释输出时，需区分采集压力的时刻和提交新盒的时刻。

截至 2026-11-20，默认的全量复制模式已通过 G-B 验收。SCR 统计结果仅覆盖 216 原子 NEP-C 冻结矩阵。局部域分解模式另有[5760 原子实验](domain-npt.md)。NEP5/ZBL 的证据限于[短程 NPT 验收](../status/validation/npt-validation.md#npt-matrix-20261104)。

## 输入和边界

两种各向同性输入使用 8-token 形式：

```text
ensemble npt_ber T1 T2 Tdamp P B tau_p
ensemble npt_scr T1 T2 Tdamp P B tau_p
```

| 输入量 | 单位 | 物理意义和范围 |
| --- | --- | --- |
| `T1/T2` | K | 起始和终止目标温度，均为有限正数 |
| `Tdamp`、`tau_p` | 积分步数 | 温度、压力的耦合参数，均为有限实数且不小于 1 |
| `P` | GPa | 目标压力，可为负，须为有限值 |
| `B` | GPa | 用于控制盒变化幅度的体积模量，须有限且为正。材料实测的体积模量与这个输入参数要分别理解 |

运行时用 eV/Å³ 计算压力，`thermo.out` 用 GPa，换算常数为 160.2177。12 或 18 个 token 的各向异性形式、三斜盒及非周期盒均不支持。

默认 `DMGMD_DOMAIN_NPT=off` 时，只要 `run.in` 含任一 NPT 声明，整份作业就走全量复制，
日志为 `DMGMD_DOMAIN mode=m1 reason=npt-m1-only`。P1 始终走全量复制。
P>1 可显式选择 `rebuild` 或 `affine`，须满足局部域分解的几何判据。回退与运行中的
失败条件见[局部 NPT](domain-npt.md)。当前势函数限于普通 NEP 和 NEP-ZBL。

NPT 要求有限正的固定步长，禁止修速和自适应步长，实验分区、overlap、调度和 trace
须使用[环境默认值](ensembles.md#并行限制与通信)。这四个变量的原始值和设置状态须在所有
rank 一致。CudaAware 需通过 MPI/CUDA 环境自检。

独立 NVT 的链、随机种子、通信和重启规则见[恒温器标准](ensembles.md)。`npt_scr` 使用 BDP 速度重标定，随后执行随机控压。NHC 只用于单独的 NVT 路径。

## 时间顺序与输出

速度 Verlet 积分完成后半步（VV2）时，程序采集热力学量，得到本步压力和体积。恒温器随后更新本 rank 负责的原子（owned）的速度。最后，控压器取同一份压力和体积样本计算盒因子，缩放盒与折回盒内的位置（wrapped positions）。速度只受恒温器缩放，展开坐标 `unwrapped_position` 保留原值。

因此同一步输出会同时出现缩放前的物理量和缩放后的几何量。

| 输出 | VV2 后采样的量（缩放前） | 步末写出的状态（缩放后） |
| --- | --- | --- |
| `thermo.out` | T、KE、PE、六个 stress 分量（压力由三个对角分量均值导出） | 九个 box 分量 |
| XYZ | force、energy、virial | box 与 wrapped positions；velocity 经 thermostat 缩放后输出 |
| `restart.xyz` | 不保存 force、virial 或能量 | box、wrapped positions、thermostat 后 velocity 与静态质量/group 信息 |

改变盒长后，程序不会立即重算力和 virial。用本行新盒重新计算压力，比较的是另一个时刻。`restart.xyz` 也没有保存系综 RNG 和控制器内部状态。

若 `B>2000 GPa`，程序走固定盒分支，盒因子为 1。Berendsen 的固定盒行为已在 B2 实测。SCR 仍须抽取压力项所需的高斯随机数，以保持后续随机序列（B3）。

## `npt_ber`

盒可变时，Berendsen 根据瞬时压力和目标压力的差值决定压缩或扩张。两种压力都使用内部单位，沿用 GPUMD 的缩放式 `1 - pc * (P_target - P_inst)`，其中 `pc = 160.2177 / (3 * tau_p * B_GPa)`。缩放因子必须有限且为正。Berendsen 的涨落不作为严格 NPT 采样证据。

验收覆盖 P1/P2/P4、两种通信后端的路由，以及 216 和 2016 原子的 200 步 GPUMD golden。另检查压力方向、负目标压力、`B>2000` 的固定盒行为、restart 和非法输入。单段长 run、同进程分段 run 和文件 restart 有实际数值差异。[B2 命令和结果](../status/validation/npt-validation.md#npt-ber-20261103)

## `npt_scr` 与随机状态

只有 rank 0 持有随机数生成器和缓存的高斯随机数。每一步先由 BDP 恒温器取样，再由控压器调用同一随机流的 `gasdev`。rank 0 把状态和所需标量广播给其他 rank。每个新 `run` 和每次从 restart 启动作业都会重建控制器和随机状态。默认 seed 和环境变量 `DMGMD_ENSEMBLE_SEED` 的语法见[系综标准](ensembles.md)。

DMG-MD 有意沿用同一随机状态，上游实现按值传递 RNG。历史 G-B 使用的 GPUMD release 用 `std::chrono::system_clock::now().time_since_epoch().count()` 构造 `std::mt19937`，DEBUG 构建才使用固定 seed 12345678。测试按秒串行启动独立的 GPUMD 作业，并记录 UTC 起止时间、PID、源码和启动证据。这样可避开参考作业同时启动，但无法从这些记录读出或设置其内部 seed。

SCR 的参考输入字节相同。候选程序为每次运行显式设置注册的 `DMGMD_ENSEMBLE_SEED`，比较两者的采样分布。原注册的 18 个分析节点（formal look）总体为 INCONCLUSIVE。后续独立确认使用新的证据，按冻结方法检查均值、方差和体积分布。与理论目标的偏差另列。

独立确认及 G-B 通过的条件是单节点、216 原子 NEP-C、300 K/0 GPa、各向同性正交全周期盒，全量复制模式。原步长（base）覆盖 P1/HostStaged 和 P2/P4 各自的 HostStaged、CudaAware。半步长（half-dt）只覆盖 P1/HostStaged。结果只说明该预算下与同条件 GPUMD 相容。普遍严格采样、遍历性、非 NEP 势、RDMA 和跨节点均超出此次验收。[最终独立确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)、[机器索引](../status/validation/evidence/npt-scr-gb/paired-analysis.json)

## 分段与 restart

同一进程的下一段 `run` 沿用系综配置、当前盒、位置和速度。段末会清空 measurement 声明，需要输出时须在下一段重新声明。文件 restart 可作为新 `model.xyz` 启动。它缺少积分器的旧力缓存和 SCR 随机状态。分段 run 和文件 restart 都不保证延续原轨迹。

## 当前验收状态（2026-11-04）

| 范围 | 状态 | 证据 |
| --- | --- | --- |
| B0 压力、单位、符号和列布局 | PASS | [pressure oracle](../status/validation/npt-validation.md#pressure-oracle-20261103) |
| B1 全量复制盒变更、preflight、所有权/邻居刷新 | PASS | [box contract](../status/validation/npt-validation.md#box-contract-20261103) |
| B2 Berendsen 与分类矩阵 | PASS | [npt-ber](../status/validation/npt-validation.md#npt-ber-20261103) |
| B3 SCR 生产集成与冻结统计矩阵 | 后续独立确认 PASS。原 18 个分析节点总体为 INCONCLUSIVE | [最终独立确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104) |
| B4/G-B 完整集成、错误与回归矩阵 | PASS | [NPT 矩阵报告](../status/validation/npt-validation.md#npt-matrix-20261104) |

`npt_scr` 的全量复制 G-B 与实验局部大盒结果分别限定于各自冻结 NEP-C 单节点矩阵；后者的精确条件见[局部 NPT 标准](domain-npt.md)。v1.0 发布门槛见[开发进度](../plans/dmg-md-v1.0-progress.md)。
