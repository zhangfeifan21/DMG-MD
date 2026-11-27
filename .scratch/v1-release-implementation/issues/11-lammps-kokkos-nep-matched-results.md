# 11: LAMMPS/KOKKOS/NEP 正式同卡数报告

Status: ready-for-agent

Blocked by: 03, 05, 07, 09, 13

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)、[07 LAMMPS/KOKKOS/NEP 同卡数 NPT 先导](07-lammps-kokkos-nep-npt-pilot.md)、[09 完成三材料 SCR 科学验收](09-three-material-scr-validation.md)、[13 DMG-MD 自身强扩展与 DN5 缓存选择](13-strong-scaling-dn5-cache.md)。

## 交付内容

组员能按相同卡数、CPU 总预算和物理任务比较 LAMMPS + KOKKOS CUDA + MatPL NEP 与 DMG-MD，并看见每个不可比格的证据。

## 正式采样条件

确认 03 的分派回归通过、09 交付三份材料 `PASS`。取得本引擎先导的逐格结论，以及 13 的最终内部对照和默认路径复验记录。外部计时采用 13 的最终候选。多卡用未设置 `DMGMD_DOMAIN_NPT` 的普通启动命令，保存实际局部模式和选定策略日志。若调试改变科学或分派行为，先在最终身份上复验受影响的证据。

在 4 × L20 上独占串行采样。按 05 用每格 10,000 步 × 5 次先导测变异系数，>2% 用 5 个正式区组，否则用 3 个。复用符合条件的先导数据并在采样前冻结重复数。按材料随机交错，每个区组先另跑 1,000 步预热。正式轨迹各 100,000 步，固定 GPU 放置、CPU 总预算、NUMA、时钟与功率、物理任务和输出介质。按 05 登记实际输出介质。

## 验收条件

- [ ] 三种最终快照各按 07 复核原势、转换势哈希、元素映射、同快照静态量和 CPU/NUMA。每个实测格保留 `matpl/nep/kk`、KOKKOS CUDA、实际 fix、各 rank 的互异 GPU UUID、`Loop time` 的进程数、步数和原子数及候选的实际分派。
- [ ] 三材料的 SCR 在 P=1、2、4 均填不适用并留空倍率，本构建没有 SCR。Berendsen 仅在该材料同快照弛豫方向和平台均温、均压、均体积全部符合 07 的预注册容差时配对。报告列出物理核验结果，并注明 `press/berendsen` 在主机执行，其开销计入墙钟。
- [ ] 对全部可比格计算 `T(LAMMPS/KK/NEP,P) / T(DMG,P)`，列出两侧绝对墙钟、稳态步时或吞吐、配对原始值、重复数、区间与 GPU·小时。每个不可比或容量格给出原因与日志，倍率留空。可比且未采样的项保持待测并阻止本报告验收。
- [ ] 交付三材料逐格清单、不可比项的依据和可重放的有效配对。若所有格都有不适用的实证，交付含原因和日志的完整矩阵。由两侧原始墙钟复算稳态步时、对数配对 95% t 区间及 GPU·小时。任一方尾段 >1% 时另报扣尾段的比值。25 的 NVE 结果另表归档。

## 依据

[V1.0 验收规格，用户故事 10、19、21、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md) · [L20 的 LAMMPS 参考核验](../../../docs/status/validation/lammps-reference.md)
