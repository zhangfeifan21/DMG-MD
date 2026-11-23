# 14: 交付可复现的单节点试用作业

Status: ready-for-agent

Blocked by: 10, 11, 12, 13

直接前置工单为[10 普通 GPUMD 正式同卡数报告](10-gpumd-matched-npt-results.md)、[11 LAMMPS/KOKKOS/NEP 正式同卡数报告](11-lammps-kokkos-nep-matched-results.md)、[12 GPUMD-WPE 单卡与迁移报告](12-wpe-single-and-migration-results.md)、[13 DMG-MD 自身强扩展与 DN5 缓存选择](13-strong-scaling-dn5-cache.md)。

## 交付内容

首次试用者拿到冻结输入和命令后，能在单节点完成 NPT 作业，并按自己的卡数查到对应科学结论、耗时和 GPU 成本。

## 验收条件

- [ ] 交付有版本标识的三个输入包、单卡与局部 2/4 卡启动方式、支持范围、失败后的重跑和跨卡 restart 示例。让组员或独立普通账号从新作业目录按说明启动，核对退出码、分派、thermo、轨迹和 restart。
- [ ] 逐材料索引已通过的 SCR 科学报告、Berendsen 预平衡，以及三份正式外部对标与 DMG-MD 自身强扩展和缓存选择。单卡、同卡数跨引擎、WPE 跨资源迁移及自身扩展各有可追溯分母。
- [ ] 对照实际采用的候选、缓存配置、快照和输出核查报告的身份。发生影响结论的更改时复跑受影响的科学或计时格。逐格核对可比项均已实测、不可比项有证据，保留较慢或区间未分辨的结果。
- [ ] 在试用材料中记录单节点验收结论、复现命令和当前限制。将限定于既有配置的 G-A、G-B、DN0–DN4 回归及其历史结果注明范围。

## 依据

[V1.0 验收规格，用户故事 1、2、4、7、8、9、10、11、12、21、22](../../v1-release-readiness/spec.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [开发进度与试用顺序](../../../docs/plans/dmg-md-v1.0-progress.md) · [原生部署与环境自检](../../../docs/operations/deployment.md)
