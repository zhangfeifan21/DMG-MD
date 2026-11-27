# 12: GPUMD-WPE 单卡与迁移报告

Status: ready-for-agent

Blocked by: 03, 05, 08, 09, 13

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)、[08 GPUMD-WPE 单卡 NPT 先导](08-wpe-single-gpu-npt-pilot.md)、[09 完成三材料 SCR 科学验收](09-three-material-scr-validation.md)、[13 DMG-MD 自身强扩展与 DN5 缓存选择](13-strong-scaling-dn5-cache.md)。

## 交付内容

原来用单卡 WPE 的组员能同时查到单卡能力和迁到 DMG-MD 局部多卡后的等待时间与 GPU 成本。

## 正式采样条件

确认 03 的分派回归通过、09 交付三份材料 `PASS`。取得本引擎先导的逐格结论，以及 13 的最终内部对照和默认路径复验记录。外部计时采用 13 的最终候选。多卡用未设置 `DMGMD_DOMAIN_NPT` 的普通启动命令，保存实际局部模式和选定策略日志。若调试改变科学或分派行为，先在最终身份上复验受影响的证据。

在 4 × L20 上独占串行采样。按 05 用每格 10,000 步 × 5 次先导测变异系数，>2% 用 5 个正式区组，否则用 3 个。复用符合条件的先导数据并在采样前冻结重复数。按材料随机交错，每个区组先另跑 1,000 步预热。正式轨迹各 100,000 步，固定 GPU 放置、CPU 总预算、NUMA、时钟与功率、物理任务和输出介质。按 05 登记实际输出介质。

## 验收条件

- [ ] 按 08 复核冻结势哈希、宿主 `a0e55bc`、二进制 SHA-256 和 WPE 1.0.0 唯一标记。单卡 UUID 与遥测相符，设备数为 1，多卡 NEP 标记零次。DMG-MD 分别保存单卡全体系和 13 所选默认的两卡、四卡局部分派记录。
- [ ] 按三材料分别填写 SCR 与 Berendsen 的单卡比 `T(WPE,1) / T(DMG,1)`，以及迁移比 `T(WPE,1) / T(DMG,2)` 和 `T(WPE,1) / T(DMG,4)`。每项列出分子、分母的绝对墙钟与各自用卡数。
- [ ] 逐格列配对重复、置信区间、稳态步时或吞吐、两侧 GPU·小时和可追溯日志。单卡装不下的规模作为容量结果，缺少相应单卡绝对时长时，将该配对倍率留空。可比待测项在验收前补测。
- [ ] 交付可复算的单卡与跨卡数两组结论，注明实测 WPE 的版本及结果适用范围。混合多段作业同时给出整份作业和分段耗时。
- [ ] 原始记录含输入、两侧二进制哈希、实际设备与绑定、退出码和末帧。复算每组绝对墙钟、对数配对 95% t 区间及两侧 GPU·小时。任一方尾段 >1% 时，另报扣尾段的比值。

## 依据

[V1.0 验收规格，用户故事 10、11、19、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [决策 06 引擎身份与控压可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer)
