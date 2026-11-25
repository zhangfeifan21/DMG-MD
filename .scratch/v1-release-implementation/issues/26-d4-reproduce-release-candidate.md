# 26: D4 从候选源码包独立复建并核对证据

Status: ready-for-agent

Blocked by: 14, 21, 23, 24, 25

直接前置工单为[14 交付可复现的单节点试用作业](14-single-node-trial-handoff.md)、[21 完成八卡 100,000 步 release 矩阵](21-long-release-eight-gpu.md)、[23 D2 核查许可证与第三方分发范围](23-d2-licenses-notice.md)、[24 D3 交付原生构建指南与各势示例](24-d3-clean-native-examples.md)、[25 完成三材料四引擎同卡数 NVE 对照](25-legacy-three-engine-nve-smoke.md)。

## 交付内容

独立复现者拿到与发布清单完全相符的候选源码包，可在干净环境重建，维护者可由包的身份追溯全部冻结的正确性与性能证据。

## 验收条件

- [ ] 为实际源码包、版本、源码提交及必要工作树内容、示例输入、势与取得方式、构建配置生成可核查的版本记录和校验和，核对 LICENSE、NOTICE、第三方与最终装包清单。
- [ ] 由独立复现者以普通账号在新的干净目录从该包原生构建，运行帮助/版本、GPU/MPI 探针、既有分层回归及每种势示例。从同一包和说明得到可核对的版本和科学输出。
- [ ] 将同一候选绑定到 G-A/G-B/G-C、生产分派、09 的三份 SCR 判定、校准与长程 smoke。按站点列 L20 的 nightly 和至 P4 的 release，再列 21 在同一台八卡机、同一份二进制上的完整矩阵。标明阈值、命令、原始证据与复验范围。
- [ ] 归档 14 的 L20 试用记录、10–12 对最终默认策略的三项外部 NPT 报告，以及 13 的自身强扩展、缓存选择、配对区间与 GPU·小时。核对可比格的原始计时，候选或默认值改变时复验受影响的科学和性能格。
- [ ] 将 25 的三材料四引擎同卡数 NVE 表单独绑定到候选，并核对输入、物理误差、引擎身份与用卡成本。交付源码包、普通账号复建记录、支持矩阵、各项证据索引及待签署清单。

## 依据

[V1.0 验收规格，用户故事 8、13、16、18、19、20、21、22](../../v1-release-readiness/spec.md) · [正式发布验收](../../../docs/plans/dmg-md-v1.0-development-workflow.md#发布验收) · [原生部署与环境自检](../../../docs/operations/deployment.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [决策 04 区组计时与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer) · [决策 05 八卡发布站点](../../v1-release-readiness/issues/05-eight-gpu-release-site.md#answer) · [决策 06 引擎身份与控压可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer)
