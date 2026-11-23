# 12: GPUMD-WPE 单卡与迁移报告

Status: ready-for-agent

Blocked by: 03, 05, 08, 09

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)、[08 GPUMD-WPE 单卡 NPT 先导](08-wpe-single-gpu-npt-pilot.md)、[09 完成三材料 SCR 科学验收](09-three-material-scr-validation.md)。

## 交付内容

原来用单卡 WPE 的组员能同时查到单卡能力和迁到 DMG-MD 局部多卡后的等待时间与 GPU 成本。

## 正式采样条件

首个正式样本前，冻结预热、重复数、配对交错顺序和区间算法。确认三种 SCR 科学验收与真实多卡分派回归均已通过，单节点每批独占四张 L20，串行执行配对作业。固定并记录 GPU 集合、时钟与功率设置、CPU 总预算、NUMA 放置、输出介质及物理任务。每条轨迹从启动计到约定输出写完。

## 验收条件

- [ ] 在冻结快照记录所用 WPE 宿主及发行库，从日志核验实际引擎，WPE 全程仅使用一张已分配 GPU。DMG-MD 端记录一张卡的全体系以及两卡、四卡的实际局部分派。
- [ ] 按三材料分别填写 SCR 与 Berendsen 的单卡比 `T(WPE,1) / T(DMG,1)`，以及迁移比 `T(WPE,1) / T(DMG,2)` 和 `T(WPE,1) / T(DMG,4)`。每项列出分子、分母的绝对墙钟与各自用卡数。
- [ ] 逐格列配对重复、置信区间、稳态步时或吞吐、两侧 GPU·小时和可追溯日志。单卡装不下的规模作为容量结果，缺少相应单卡绝对时长时，将该配对倍率留空。可比待测项在验收前补测。
- [ ] 交付可复算的单卡与跨卡数两组结论，注明实测 WPE 的版本及结果适用范围。混合多段作业同时给出整份作业和分段耗时。
- [ ] 原始记录含输入与二进制哈希、实际设备与绑定、退出码、末帧和每次配对值。审阅者能复算绝对时长、区间及 GPU·小时。各方向的性能结果均予报告，验收不设固定胜负或最低提速倍率。

## 依据

[V1.0 验收规格，用户故事 10、11、19、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)
