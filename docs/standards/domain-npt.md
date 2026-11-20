# 局部域 NPT 实验合同

本文件与 [机器矩阵](../../tests/npt/domain-manifest.json) 规定实验合同。DN0–DN4 已在[验证记录](../status/validation/domain-npt-validation.md#dn4-final-20261116)所列有限范围通过；这不表示成为默认功能。`DMGMD_DOMAIN_NPT=off|rebuild|affine` 默认 off；所有 rank 设置须一致，实验 partition/overlap 保持 off。P1 使用现有全量复制，P2/P4 局部结果必须同时有 `mode=m2a` 和实际 DOMAIN_NPT 路径标志；回退结果不得计作局部通过。初始几何不合格回退，运行中候选盒不合格则提交前全 rank 一致失败。

输入仍使用现有 `npt_ber` / `npt_scr` 语法。在仓库根目录 source `../env/md-mpi.sh` 并通过环境自检后，进入包含 `model.xyz`、势文件和 `run.in` 的作业目录，可显式运行：

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 DMGMD_DOMAIN_NPT=affine \
  DMGMD_COMM_BACKEND=HostStaged DMGMD_DOMAIN_PARTITION=off \
  DMGMD_DOMAIN_OVERLAP=off mpirun -n 4 /absolute/path/dmg-md
```

将 `affine` 换成 `rebuild` 可运行强制重建基线，换成 `off` 则使用全量复制 NPT。实际路由以日志为准；已完成与未完成的验收范围见[验证记录](../status/validation/domain-npt-validation.md)。

沿用 `VV1 → force → VV2 → thermo → thermostat → barostat → output`，压力为 `(2K+trace(W))/(3V)`，只统计 owned。缩盒后保留旧力与输出所需的 E/F/virial；下一 VV1 消费旧力后，下一 force 前统一完成迁移、重建或刷新。新 run 段首 force 同样要先 readiness。每段重置控制器与 RNG；同段缓存重建不重置 RNG，SCR 的固定盒分支保留规定随机消费。box generation 与 layout epoch 分离，变盒后 ghost 在求力前按新盒刷新。

确定性字段沿用现有 [baseline 容差](../../tests/baseline/manifest.json)，MPI、压力检查沿用各自现有 runner，不因候选结果放宽。P2/P4 和 HostStaged/CudaAware 均须执行；矩阵还包括同快照 E/F/全部 virial/pressure、变盒差分、旧力/段首/多段切换、fixed-box RNG、跨 rank restart、初始回退、运行时几何失败、rank 设置不一致、空 rank 和通信计数。新项若现有阈值不适用，须先基于独立重复基线另行冻结，不能用候选结果定门。机器矩阵列出要求不等于其已经执行。

## 理想几何证明与限制

重建参考盒 `H0`，当前盒理想地为 `a H0`，连续 lift 满足 `x_i=a(q_i+u_i)` 且 `|u_i|≤δ`。对当前 cutoff 内的任一边及同一周期像，三角不等式给 `|q_j-q_i+H0 n|≤R/a+2δ`。若 `2δ+max(0,1/a-1)R*≤skin` 且 `R*` 覆盖所有实际单边 cutoff，则该边在参考 `R+skin` 内；对 force 与 dependency 两条边分别应用，得到两跳闭包。先判预算非负，不能将负预算平方。扩张仍消耗位移预算，压缩即使没有 drift 也消耗预算。

实际实现使用当前与参考对角矩阵的保守伸缩下界，并单独保留 wrapped reference 和周期 image 计数。image 更新与实际 wrap 位于同一个 kernel 分支；直接计算连续参考度量，缩放和 wrap 的实际保存坐标误差均包含在度量中。浮点、cell 扫描和 slab 的实现级界见[舍入证明](domain-npt-rounding-proof.md)。证明的构建、精度及表示前提必须满足，独立 CPU oracle 和 GPU 数值验收分别记录。

SCR 大盒冻结统计矩阵已通过完整性和科学审核：单节点 NEP4-C、5760 原子、300 K/0 GPa、各向同性正交全周期体系；base 为 P1/HostStaged/off 与 P2/P4 两后端各自的 off、rebuild、affine，half 仅 P1/HostStaged/off、P2/HostStaged/affine、P4/CudaAware/affine。共 152 条链和唯一一次最终分析；151 个注册区间均已生成。具体参照、响应、ESS、误差与停止规则见[注册](../../tests/npt/domain-statistics-registration.json)和[最终审核](../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v8/final-audit.json)。既有 216 原子全量复制 G-B 是独立结论，不可直接外推；Ber 不以体积涨落证明 NPT。该有限结果不证明其他势、跨节点或普遍遍历性。

## 有限精度判据的实现义务

对实际对角盒定义 `A=H H0^-1`，使用向下取整的 `α≤min diag(A)`。设当前 kernel 接受的作用边真实长度不超过 `R+E_force`，重建时所有真实距离严格小于 `R+skin−E_build` 的边一定进入参考表；`δ` 是每个 owned 连续 lift 在参考度量中的位移上界（含全部累计误差）。那么逐边的充分条件可写成

```
2δ + max(0, 1/α − 1) R* + E_force/min(α,1) + E_build < skin
```

各上界、除法和最终比较都需向外舍入；非有限值或无法建立上界时重建。使用严格不等式是因为 [邻居构建 kernel](../../src/gpumd_compat/neighbor.cu) 以 float 位移、float MIC、float 距离平方和 `d2 < cutoff_square` 建表，临界点不能假定收入。参考半径还必须覆盖 cell 枚举，以及 typewise、NEP5、ZBL 实际 kernel 的输入舍入；`E_force`、`E_build` 不能用未论证常数代替。`δ` 还必须计入每次缩放、坐标 wrap 与周期 lift 的误差；逐步更新应保留跨越信息，不能只从当前 wrapped 坐标取一次 MIC。实际误差上界和检查范围由舍入证明与代码共同限定；扩张时仍独立支付 force 误差，避免小 typewise 半径漏掉误差项。两跳闭包对两条边分别使用本判据。

## 状态与运行约束

`rebuild` 在每次实际变盒后标记 pending，在下一 VV1 消费旧力之后重建；`affine` 在连续步检查上述预算，成立则仅刷新固定成员 ghost，否则走同一重建事务。段首有 pending 盒变化或新启用 unwrapped 引起的 layout 重传时，先完成 readiness 再重算力。factor 恰为 1 不制造盒事件。

缓存分别绑定参考盒 generation、layout epoch、实际 NEP neighbor/workspace generation；halo-ready 绑定 step、layout 与 box generation。参考 slab 随重建更新，当前物理 slab 随盒提交更新。分区、overlap 和非 split 调度仍由 NPT 组合守卫拒绝。每次实际盒变化新增一次计入通信账本的 uint32 preflight OR，普通 force 前保留一次 cache reason OR，无缓存位移 MAX 或 N 大小的全局坐标通信。

控制标量沿用既有分支：无状态 Berendsen 温控由各 rank 使用同一全局 thermo 计算，控压 status/factor 由 root 广播；SCR 的温控和控压各有一次 root 广播。故持续变盒、无输出步每步为一次 8-double SUM、两次 uint32 OR，以及 Berendsen 一次或 SCR 两次 2-double 广播。factor 恰为 1 时没有提交前 OR；启动、段首及请求输出的通信另计。

`DMGMD_DOMAIN_SUMMARY` 的 cache_reuses/cache_rebuilds 和 affine_invalid_rebuilds/affine_budget_rebuilds/box_rebuilds 是段内全局决议在本 rank 的计数；段首的重建另保留原 rebuild_steps 口径。真实 NEP cell scan 的整数上界在首次建表和候选盒提交前检查，初始 affine 输入不合格回退，运行中不合格共同终止。证明外的有限几何只允许保守重建，不宣称形变复用。
