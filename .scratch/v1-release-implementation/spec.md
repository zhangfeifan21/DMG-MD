# DMG-MD V1.0 实施工单索引

本索引沿用 27 张实施单的编号。2026-11-25 依据六张已解决决策单修订验收条件与直接前置，核对的 L20 源码为 `6c06899`。[发布验收规格](../v1-release-readiness/spec.md) · [发布前决策地图](../v1-release-readiness/map.md#decisions-so-far)

`Blocked by` 只引用本目录的实施单。工单另列实际采样、资源或人工签署条件。`ready-for-agent` 表示工单写明了可执行的工作，开始正式作业还须具备对应证据。

## 可以先做的工作

01 已交付三套[冻结输入](../../tests/npt/public_inputs/README.md)。04 可迁移参考并做校准，05 可验证计时器，06–08 可从短输入核验引擎。02 已完成通用失格拒绝，03 已用 `rebuild` 和 `affine` 验证局部变盒、输出及六种跨卡数重启方向。[03 验收记录](../../docs/status/validation/domain-npt-validation.md#local-box-restart-20261127)15 的接口设计、22 的 CLI 和 23 的许可清单也可开始。15 结单时须有 04 在同一候选上的 smoke。

09 在 01、02 后逐材料登记先导并作正式 SCR 判定。03 的分派回归、05 的计量验证和 09 的三份 `PASS` 齐备后，13 先做内部对照和必要的调试，选出并复验普通多卡 NPT 的生产默认策略。10、11、12 随后用同一最终候选和各自的引擎先导结果做外部对标。每批独占 4 × L20，可比格须有实测，不适用格须有原因和日志。14 汇合三项报告、强扩展与科学证据，交付可复现的试用作业。

04 的 smoke 通过后，20 负责四卡 nightly、旧结果归档及 L20 截至 P4 的独立 release 记录。21 到机后在同一台八卡机、同一份候选二进制上跑完整 release，包括 P=1/2/4。25 的碳短程记录已保留，新增的三材料四引擎同卡数 NVE 表依赖 01 的冻结快照和 05 的计量。该表单列交给 26。26 核对候选源码包、L20 试用与计时和八卡正确性证据，最后由 27 的维护者签署。

## 单节点试用、科学验证与性能

| 工单 | 直接前置 |
| --- | --- |
| [01 冻结碳、水、铜三套公开输入](issues/01-public-equilibrated-inputs.md) | 已完成，三材料四引擎留出终验通过 |
| [02 实现生产多卡分派与启动拒绝](issues/02-production-domain-routing.md) | 无前置，已完成 |
| [03 验证变盒、输出与跨卡重启](issues/03-box-output-cross-rank-restart.md) | 02 已完成，本单双卡、四卡验收通过 |
| [04 统一长短程参考并恢复 100 步 smoke](issues/04-long-reference-smoke.md) | 可开工 |
| [05 用短作业验证配对计时](issues/05-paired-job-timing.md) | 可开工 |
| [06 普通 GPUMD 同卡数 NPT 先导](issues/06-gpumd-matched-npt-pilot.md) | 可开工 |
| [07 LAMMPS/KOKKOS/NEP 同卡数 NPT 先导](issues/07-lammps-kokkos-nep-npt-pilot.md) | 可开工 |
| [08 GPUMD-WPE 单卡 NPT 先导](issues/08-wpe-single-gpu-npt-pilot.md) | 可开工 |
| [09 完成三材料 SCR 科学验收](issues/09-three-material-scr-validation.md) | 01、02 |
| [10 普通 GPUMD 正式同卡数报告](issues/10-gpumd-matched-npt-results.md) | 03、05、06、09、13 |
| [11 LAMMPS/KOKKOS/NEP 正式同卡数报告](issues/11-lammps-kokkos-nep-matched-results.md) | 03、05、07、09、13 |
| [12 GPUMD-WPE 单卡与迁移报告](issues/12-wpe-single-and-migration-results.md) | 03、05、08、09、13 |
| [13 DMG-MD 自身强扩展与 DN5 缓存选择](issues/13-strong-scaling-dn5-cache.md) | 03、05、09 |
| [14 交付可复现的单节点试用作业](issues/14-single-node-trial-handoff.md) | 10、11、12、13 |

## 力场与长程回归

| 工单 | 直接前置 |
| --- | --- |
| [15 C0 统一求力接口并保持 NEP 兼容](issues/15-c0-nep-force-interface.md) | 可开工，结单需 04 |
| [16 C1 统一势输入与诊断](issues/16-c1-potential-input-contract.md) | 15 |
| [17 C2 交付单卡 LJ](issues/17-c2-single-gpu-lj.md) | 16 |
| [18 C3 交付单卡 EAM/alloy](issues/18-c3-single-gpu-eam-alloy.md) | 17 |
| [19 C5 验收势与六种系综的支持矩阵](issues/19-c5-force-ensemble-matrix.md) | 03、18 |
| [20 恢复四卡 10,000 步 nightly](issues/20-long-nightly-four-gpu.md) | 04 |
| [21 完成八卡 100,000 步 release 矩阵](issues/21-long-release-eight-gpu.md) | 20，另需八卡分配 |

## 部署、补充对照与发布

| 工单 | 直接前置 |
| --- | --- |
| [22 D1 交付 CLI、版本和环境探针](issues/22-d1-cli-version-probe.md) | 可开工 |
| [23 D2 核查许可证与第三方分发范围](issues/23-d2-licenses-notice.md) | 可开工 |
| [24 D3 交付原生构建指南与各势示例](issues/24-d3-clean-native-examples.md) | 19、22 |
| [25 完成三材料四引擎同卡数 NVE 对照](issues/25-legacy-three-engine-nve-smoke.md) | 01、05 |
| [26 D4 从候选源码包独立复建并核对证据](issues/26-d4-reproduce-release-candidate.md) | 14、21、23、24、25 |
| [27 D5 维护者签署发布结论](issues/27-d5-maintainer-release-signoff.md) | 26，由维护者签署 |

## 决策与适用范围

决策 01、03 确定输入与逐材料 SCR 验收，决策 02 确定长短程参考迁移。决策 04 规定区组计时、调试和默认策略选择，决策 05 确定八卡站点和到机预检。决策 06 规定引擎身份、控压可比性和补充 NVE。各项先导填入实测值后再冻结正式采样与预算。

[决策 01 三种公开输入的规模与快照](../v1-release-readiness/issues/01-public-material-inputs.md) · [决策 02 长短程 GPUMD 参考迁移](../v1-release-readiness/issues/02-long-regression-reference.md) · [决策 03 各材料 SCR 统计协议](../v1-release-readiness/issues/03-scr-science-protocol.md) · [决策 04 计时协议与 DN5 默认策略](../v1-release-readiness/issues/04-dn5-timing-and-adoption.md) · [决策 05 八卡发布资源](../v1-release-readiness/issues/05-eight-gpu-release-site.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../v1-release-readiness/issues/06-three-engine-provenance.md)

G-A、G-B、DN0–DN4 继续按原冻结范围做回归。非 NEP 的局部多卡、各向异性或三斜 NPT、跨物理节点验证等后续工作按规格的范围条款管理。
