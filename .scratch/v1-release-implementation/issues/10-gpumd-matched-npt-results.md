# 10: 普通 GPUMD 正式同卡数报告

Status: ready-for-agent

Blocked by: 03, 05, 06, 09

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)、[06 普通 GPUMD 同卡数 NPT 先导](06-gpumd-matched-npt-pilot.md)、[09 完成三材料 SCR 科学验收](09-three-material-scr-validation.md)。

## 交付内容

组员能在相同预算下从实测读出普通 GPUMD 与 DMG-MD 在每种材料的 1 对 1、2 对 2 和 4 对 4 卡各需多久。

## 正式采样条件

首个正式样本前，冻结预热、重复数、配对交错顺序和区间算法。确认三种 SCR 科学验收与真实多卡分派回归均已通过，单节点每批独占四张 L20，串行执行配对作业。固定并记录 GPU 集合、时钟与功率设置、CPU 总预算、NUMA 放置、输出介质及物理任务。每条轨迹从启动计到约定输出写完。

## 验收条件

- [ ] 在三材料的最终快照记录普通 GPUMD 的实际版本，复核静态量与控压语义。两侧都确认 P=1、2、4 中对应的设备。多卡时保留普通 GPUMD 单进程多卡 NEP 与 DMG-MD 实际局部分派的证据。
- [ ] 为各材料分别填写 SCR 生产与 Berendsen 预平衡。每个可比格按配对协议完成计时，计算 `T(GPUMD,P) / T(DMG,P)`，分子、分母使用相同材料、卡数、物理任务和输出。
- [ ] 交付逐格报告和可重算的原始测量，逐对列出两侧绝对墙钟、稳态步时或吞吐、重复数、配对置信区间及 GPU·小时。对容量受限或物理条件不等价的格子注明原因。可比而未测的格子标记为待测，交付前补齐。
- [ ] 原始记录含输入与二进制哈希、实际设备与绑定、退出码、末帧和每次配对值。审阅者能复算绝对时长、区间及 GPU·小时。各方向的性能结果均予报告，验收不设固定胜负或最低提速倍率。

## 依据

[V1.0 验收规格，用户故事 10、19、21、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md)
