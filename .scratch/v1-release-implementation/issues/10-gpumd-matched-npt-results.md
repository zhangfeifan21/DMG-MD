# 10: 普通 GPUMD 正式同卡数报告

Status: ready-for-agent

Blocked by: 03, 05, 06, 09, 13

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)、[06 普通 GPUMD 同卡数 NPT 先导](06-gpumd-matched-npt-pilot.md)、[09 完成三材料 SCR 科学验收](09-three-material-scr-validation.md)、[13 DMG-MD 自身强扩展与 DN5 缓存选择](13-strong-scaling-dn5-cache.md)。

## 交付内容

组员能在相同预算下从实测读出普通 GPUMD 与 DMG-MD 在每种材料的 1 对 1、2 对 2 和 4 对 4 卡各需多久。

## 正式采样条件

确认 03 的分派回归通过、09 交付三份材料 `PASS`。取得本引擎先导的逐格结论，以及 13 的最终内部对照和默认路径复验记录。外部计时采用 13 的最终候选。多卡用未设置 `DMGMD_DOMAIN_NPT` 的普通启动命令，保存实际局部模式和选定策略日志。若调试改变科学或分派行为，先在最终身份上复验受影响的证据。

在 4 × L20 上独占串行采样。按 05 用每格 10,000 步 × 5 次先导测变异系数，>2% 用 5 个正式区组，否则用 3 个。复用符合条件的先导数据并在采样前冻结重复数。按材料随机交错，每个区组先另跑 1,000 步预热。正式轨迹各 100,000 步，固定 GPU 放置、CPU 总预算、NUMA、时钟与功率、物理任务及 NVMe 输出。

## 验收条件

- [ ] 三种最终快照各按 06 复核势哈希、NEP4 元素数、铜 ZBL 标记、两段时间行和实际用卡。P>1 的多卡 NEP 标记恰好一次，单卡零次。保存 UUID 与遥测、两侧实际分派、退出码和末帧。
- [ ] 为各材料分别填写 SCR 生产与 Berendsen 预平衡。每个可比格按配对协议完成计时，计算 `T(GPUMD,P) / T(DMG,P)`，分子、分母使用相同材料、卡数、物理任务和输出。
- [ ] 交付逐格报告和可重算的原始测量，逐对列出两侧绝对墙钟、稳态步时或吞吐、重复数、配对置信区间及 GPU·小时。对容量受限或物理条件不等价的格子注明原因。可比而未测的格子标记为待测，交付前补齐。
- [ ] 原始记录含输入和二进制哈希、绑定和每次配对耗时。从启动到输出写完退出计进程墙钟，复算稳态步时、对数配对 95% t 区间与 GPU·小时。任一方尾段 >1% 时，另报扣尾段的比值。较慢或区间含 1 的结果也须交付。

## 依据

[V1.0 验收规格，用户故事 10、19、21、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md)
