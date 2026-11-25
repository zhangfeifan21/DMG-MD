# 14: 交付可复现的单节点试用作业

Status: ready-for-agent

Blocked by: 10, 11, 12, 13

直接前置工单为[10 普通 GPUMD 正式同卡数报告](10-gpumd-matched-npt-results.md)、[11 LAMMPS/KOKKOS/NEP 正式同卡数报告](11-lammps-kokkos-nep-matched-results.md)、[12 GPUMD-WPE 单卡与迁移报告](12-wpe-single-and-migration-results.md)、[13 DMG-MD 自身强扩展与 DN5 缓存选择](13-strong-scaling-dn5-cache.md)。

## 交付内容

首次试用者拿到冻结输入和命令后，能在单节点完成 NPT 作业，并按自己的卡数查到对应科学结论、耗时和 GPU 成本。

## 验收条件

- [ ] 交付版本化的三个输入包、普通单卡与局部 2/4 卡启动方式、失败后的重跑和跨卡 restart 示例。组员或独立普通账号从新作业目录复跑，不设置缓存环境变量，在日志核对 13 的实际默认分派，检查退出码、thermo、轨迹和 restart。
- [ ] 逐材料索引 09 的三份 `PASS`、Berendsen 预平衡、13 在最终候选上的内部对照与自身强扩展，以及 10–12 在同一候选上的三项外部报告。列出单卡、同卡数跨引擎、WPE 迁移和强扩展各自的绝对时长与分母。
- [ ] 对照实际采用的候选、缓存配置、快照和输出核查报告的身份。发生影响结论的更改时复跑受影响的科学或计时格。保存复验结果与候选身份的对应关系。
- [ ] 在试用说明中登记 4 × L20 的复现命令、结果与限制。每个可比格有实测，不适用格附原因与日志，较慢或未能分辨的比值照实交付。既有 G-A、G-B、DN0–DN4 按各自范围引用。

## 依据

[V1.0 验收规格，用户故事 1、2、4、7、8、9、10、11、12、21、22](../../v1-release-readiness/spec.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [开发进度与试用顺序](../../../docs/plans/dmg-md-v1.0-progress.md) · [原生部署与环境自检](../../../docs/operations/deployment.md) · [决策 04 区组计时与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer) · [决策 06 引擎身份与控压可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer)
