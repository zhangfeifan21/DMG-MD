# 11: LAMMPS/KOKKOS/NEP 正式同卡数报告

Status: ready-for-agent

Blocked by: 03, 05, 07, 09

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)、[07 LAMMPS/KOKKOS/NEP 同卡数 NPT 先导](07-lammps-kokkos-nep-npt-pilot.md)、[09 完成三材料 SCR 科学验收](09-three-material-scr-validation.md)。

## 交付内容

组员能按相同卡数、CPU 总预算和物理任务比较 LAMMPS + KOKKOS CUDA + MatPL NEP 与 DMG-MD，并看见每个不可比格的证据。

## 正式采样条件

首个正式样本前，冻结预热、重复数、配对交错顺序和区间算法。确认三种 SCR 科学验收与真实多卡分派回归均已通过，单节点每批独占四张 L20，串行执行配对作业。固定并记录 GPU 集合、时钟与功率设置、CPU 总预算、NUMA 放置、输出介质及物理任务。每条轨迹从启动计到约定输出写完。

## 验收条件

- [ ] 在三种最终输入上重新核对势与转换、同快照能量/力/virial、两侧的 P=1、2、4 以及一 rank 一卡和 CPU/NUMA 放置。每个实测格留存 `matpl/nep/kk`、KOKKOS CUDA 和候选实际分派的作业证据。
- [ ] 为各材料的 SCR 和 Berendsen 分别填写可比性矩阵。现有 `npt/kk` 的 Nosé–Hoover 作业在 SCR 对照格写明不等价。Berendsen 依据最终配置的自由度、耦合、体模量与盒响应验证决定是否配对。
- [ ] 对全部可比格计算 `T(LAMMPS/KK/NEP,P) / T(DMG,P)`，列出两侧绝对墙钟、稳态步时或吞吐、配对原始值、重复数、区间与 GPU·小时。每个不可比或容量格给出原因与日志，倍率留空。可比且未采样的项保持待测并阻止本报告验收。
- [ ] 即使某个材料或某类控压无等价工况，仍交付完整的同卡数报告，让审阅者能找到可重放的输入、后端证据以及每格的结论。补充 NVE 的数据注明其系综与测量范围。
- [ ] 原始记录含输入与二进制哈希、实际设备与绑定、退出码、末帧和每次配对值。审阅者能复算绝对时长、区间及 GPU·小时。各方向的性能结果均予报告，验收不设固定胜负或最低提速倍率。

## 依据

[V1.0 验收规格，用户故事 10、19、21、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md) · [L20 的 LAMMPS 参考核验](../../../docs/status/validation/lammps-reference.md)
