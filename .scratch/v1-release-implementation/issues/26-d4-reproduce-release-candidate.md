# 26: D4 从候选源码包独立复建并核对证据

Status: ready-for-agent

Blocked by: 14, 21, 23, 24

直接前置工单为[14 交付可复现的单节点试用作业](14-single-node-trial-handoff.md)、[21 完成八卡 100,000 步 release 矩阵](21-long-release-eight-gpu.md)、[23 D2 核查许可证与第三方分发范围](23-d2-licenses-notice.md)、[24 D3 交付原生构建指南与各势示例](24-d3-clean-native-examples.md)。

## 交付内容

独立复现者拿到与发布清单完全相符的候选源码包，可在干净环境重建，维护者可由包的身份追溯全部冻结的正确性与性能证据。

## 验收条件

- [ ] 为实际源码包、版本、源码提交及必要工作树内容、示例输入、势与取得方式、构建配置生成可核查的版本记录和校验和，核对 LICENSE、NOTICE、第三方与最终装包清单。
- [ ] 由独立复现者以普通账号在新的干净目录从该包原生构建，运行帮助/版本、GPU/MPI 探针、既有分层回归及每种势示例。从同一包和说明得到可核对的版本和科学输出。
- [ ] 将候选绑定到 G-A/G-B/G-C 的冻结证据、生产分派、三材料 SCR、100/10,000/100,000 步长程和八卡完整矩阵。记录原始产物、阈值与审阅位置，区分历史范围、当前复跑及本轮适用性。
- [ ] 归档同卡数普通 GPUMD、同卡数 LAMMPS/KOKKOS/NEP、固定单卡 WPE 的单卡与迁移报告，并附强扩展、DN5、配对区间和 GPU·小时。核验可比格有原始实测，候选或运行默认值有影响时在最终身份上重跑相关格。
- [ ] 交付源码包、复建记录、支持矩阵、正确性/性能索引及待签署清单。若引用 25 的补充 NVE，记录其独立范围和来源。

## 依据

[V1.0 验收规格，用户故事 8、13、16、18、19、20、21、22](../../v1-release-readiness/spec.md) · [正式发布验收](../../../docs/plans/dmg-md-v1.0-development-workflow.md#发布验收) · [原生部署与环境自检](../../../docs/operations/deployment.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)
