# 局部域 NPT 实验合同

本实验检查多卡各自负责一部分原子时，改变周期盒后还能否用正确的邻居求力。默认 NPT 使用全量复制模式。两种显式实验设置都在局部域分解模式下积分，区别在于何时重建邻居表。

| `DMGMD_DOMAIN_NPT` | 行为 |
| --- | --- |
| `off`（默认） | 含 NPT 声明的整个作业使用全量复制模式 |
| `rebuild` | 实际变盒后，在下次求力前强制重建，是对照基线 |
| `affine` | 只有在形变及原子位移足够小、证明条件成立时才复用缓存，否则重建 |

所有 rank 的变量设置状态和原始值必须一致。单 rank（P1）始终使用全量复制模式。多 rank（P>1）启动时须通过局部域分解几何检查，`affine` 还须通过 cell 索引上界检查。如果初始盒满足 NPT 的基础条件但不具备局部资格，当前实现回退到全量复制模式。进入局部积分后，任一候选盒失格都会使所有 rank 在提交前终止。

日志同时出现 `DMGMD_DOMAIN mode=m2a` 和 `DMGMD_DOMAIN_NPT mode=rebuild|affine` 才能确认进入实验局部路径。[机器矩阵](../../tests/npt/domain-manifest.json)定义验收条件。实验已通过其中记录的 DN0–DN4 范围，DN5 性能、生产分派和跨节点均待验收。[2026-11-16 验收](../status/validation/domain-npt-validation.md#dn4-final-20261116)、[已决策待实现的生产路由](architecture-decisions.md#production-domain-routing)

## 启动作业

输入仍使用现有 `npt_ber` / `npt_scr` 语法。在仓库根目录 source `../env/md-mpi.sh` 并通过环境自检后，进入包含 `model.xyz`、势文件和 `run.in` 的作业目录，可显式运行以下示例。将设备编号和程序路径换成实际分配及构建的位置：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 DMGMD_DOMAIN_NPT=affine \
  DMGMD_COMM_BACKEND=HostStaged DMGMD_DOMAIN_PARTITION=off \
  DMGMD_DOMAIN_OVERLAP=off mpirun -n 4 /absolute/path/dmg-md
```

将 `affine` 换成 `rebuild` 可运行强制重建基线，换成 `off` 则使用全量复制 NPT。实际路由以日志为准；已完成与未完成的验收范围见[验证记录](../status/validation/domain-npt-validation.md)。

## 一步中的压力、旧力和邻居状态

运行顺序为 `VV1 → force → VV2 → thermo → thermostat → barostat → output`。VV1 和 VV2 是速度 Verlet 的前后半步。瞬时压力为 `(2K+trace(W))/(3V)`，其中 K 为总动能，W 为全体系 virial 张量，V 为采样时的盒体积。求和只计各 rank 负责的原子（owned），每个原子恰计一次。

改变盒长后保留旧力，以及本步输出所需的能量、力和 virial。下一步 VV1 仍使用旧力，在下一次求力前才完成迁移、重建或刷新。新 `run` 段首也先检查盒和邻居数据，再重算初始力。盒和布局分别记录变更代次，求力前须用当前盒刷新相邻进程的原子副本（ghost）。

每个新段重置控制器和 RNG。同一段内的缓存重建保留随机状态。SCR 即使走固定盒分支，也照常消费规定的随机数。

## 验收检查

确定性字段沿用[短程容差](../../tests/baseline/manifest.json)，MPI 和压力检查沿用各自的测试程序。P2/P4 均须覆盖 HostStaged 和 CudaAware。矩阵分别检查下列行为。

- 同一快照的能量、力、全部 virial 分量和压力，以及变盒前后的差分。
- 旧力、段首求力、多段切换、固定盒的随机数消费，以及跨 rank 数 restart。
- 初始回退、积分途中几何失格、各 rank 设置不一致、空 rank 和通信计数。

机器矩阵列出应运行的项目，实际完成范围以验收记录为准。若新增项目需要不同阈值，应先通过独立重复基线冻结阈值，不根据候选结果放宽。

## 理想几何证明与限制

考虑理想的均匀缩放。令 `H0` 为最近一次重建时的盒矩阵，当前盒为 `a H0`。把原子经过周期边界后的连续坐标写成 `x_i=a(q_i+u_i)`。其中 `q_i` 是参考坐标，`|u_i|≤δ` 限制原子在参考坐标系里的位移。

对当前截断范围内的一条边，固定同一周期像 n，三角不等式给出 `|q_j-q_i+H0 n|≤R/a+2δ`。取覆盖实际单边 cutoff 的 `R*`。若 `2δ+max(0,1/a-1)R*≤skin`，这条边在参考盒内的长度不超过 `R+skin`。对最终力所需的边和计算其邻居中间量所需的边各用一次，就覆盖了两跳依赖。

两端原子位移都会消耗邻居表预留宽度（skin）。盒膨胀时仍要计位移，压缩时即使原子没有额外漂移，也会消耗这份余量。先判断剩余余量非负，负值不得平方后再作判断。

实际程序用当前与参考对角盒的保守伸缩下界。每个原子另存已折回盒内的参考坐标和跨越周期边界的次数。GPU 在执行 wrap 的同一分支更新计数，然后直接计算连续位移。这样，缩放和 wrap 对已保存坐标引入的舍入都计入位移。浮点、cell 扫描和分片（slab）的条件见[有限精度证明](domain-npt-rounding-proof.md)。编译、精度和表示必须满足证明前提。独立 CPU 对照与 GPU 数值验收分别记录。

## 已通过的 SCR 统计范围

SCR 大盒冻结统计矩阵已通过完整性和科学审核。体系为单节点 NEP4-C、5760 原子，
300 K/0 GPa，各向同性、正交且全周期。base 为 P1/HostStaged/off 与 P2/P4 两种通信
后端各自的 off、rebuild、affine。half 限 P1/HostStaged/off、P2/HostStaged/affine
和 P4/CudaAware/affine。

验收包含 152 条链、一次最终分析和 151 个注册区间。响应、有效样本量、误差和停止规则见
[统计注册](../../tests/npt/domain-statistics-registration.json)。审核摘要及原始归档标识见
[DN4 验收记录](../status/validation/domain-npt-validation.md#dn4-final-20261116)。
原始审核文件不在本次 L20 checkout。

216 原子全量复制 G-B 是独立结论。Berendsen 的体积涨落不用于证明 NPT 采样。
DN4 统计支持限于上述单节点条件。

## 有限精度判据的实现义务

对实际对角盒定义 `A=H H0^-1`，取向下舍入的伸缩下界 `α≤min diag(A)`。若当前核函数接受的边长至多为 `R+E_force`，且建表时所有严格短于 `R+skin−E_build` 的边都能入表，可以按以下条件决定复用。其中 `δ` 覆盖每个 owned 原子在参考坐标系中的连续位移和累积误差。

```
2δ + max(0, 1/α − 1) R* + E_force/min(α,1) + E_build < skin
```

所有上界、除法和最后的比较都须向保守方向舍入。若结果非有限或无法证明上界，则重建。[建表核函数](../../src/gpumd_compat/neighbor.cu)用 float 计算位移、最小像（MIC）和距离平方，只在 `d2 < cutoff_square` 时收录邻居。临界边可能被排除，所以充分条件使用严格不等式。

参考半径还须覆盖 cell 枚举和 typewise、NEP5、ZBL 核函数的输入舍入。`E_force` 和 `E_build` 须各自有误差界。盒膨胀时仍单独保留求力误差项，否则小截断半径的边可能漏算。`δ` 要计入每次缩放、wrap 和周期跨越。只对当前 wrapped 坐标取一次 MIC 无法还原连续位移。两跳依赖中的每条边都要满足该判据，数值界及适用范围见舍入证明和相应源码。

## 状态与运行约束

在 `rebuild` 模式中，实际变盒会标记待重建。下一步 VV1 用完旧力后，程序重建缓存。在 `affine` 模式中，每步检查上述余量。通过时保留 ghost 名单，只刷新坐标。未通过时执行与强制重建相同的操作。段首若有待处理的盒变化，或新启用展开坐标而重新上传布局，必须先准备好求力数据。盒因子恰为 1 时没有变盒事件。

缓存记录参考盒、布局和实际 NEP 邻居表/工作区各自的代数。halo 就绪状态还须同时匹配当前步、布局和盒代数。参考 slab 在重建时更新，当前物理 slab 在提交盒时更新。实验分区、重叠、调度和 trace 须使用[规定的默认值](ensembles.md#并行限制与通信)。

每次实际变盒前新增一次 uint32 OR，汇总各 rank 的候选盒预检结果。普通步求力前仍只有一次缓存原因 OR。无需对全局位移取 MAX，也无需交换 N 个原子的全部坐标。

持续变盒且无输出时，每步执行一次 8-double SUM、两次 uint32 OR。Berendsen 的温控因子由各 rank 从相同的全局热力学量得出，控压的状态和因子由 rank 0 做一次 2-double 广播。SCR 则分别为温控和控压各做一次这样的广播。盒因子恰为 1 时省去提交前的 OR。启动、段首及请求输出的通信另计。

`DMGMD_DOMAIN_SUMMARY` 中的 `cache_reuses`、`cache_rebuilds`、`affine_invalid_rebuilds`、`affine_budget_rebuilds` 和 `box_rebuilds` 是本 rank 记录的段内全局判定次数。段首重建另沿用 `rebuild_steps` 的计数方式。

首次建表和提交候选盒前，程序都检查实际 NEP cell 扫描的整数上界。初始盒不满足 `affine` 的索引条件时回退。运行途中候选盒失格时，所有 rank 共同终止。若几何仍可安全建表，但超出形变复用的证明范围，则只能重建。
