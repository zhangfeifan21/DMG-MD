# DMG-MD V1.0 实施工单索引

本目录按 2026-11-23 修订并经维护者确认的验收规格编排，方案核对的源码为 `b103010`。完整规格和待决问题分别维护在[发布验收规格](../v1-release-readiness/spec.md)与[发布前决策地图](../v1-release-readiness/map.md)。

每张工单列出直接前置、实际作业或交付物，以及可复核的验收条件。`ready-for-agent` 表示内容已具备实施条件。认领时还要核对 `Blocked by` 和该单的采样、资源或签署条件。工单编号和决策编号分别按各自目录解释。

## 可以先做的工作

可以先做 01 的三套输入、02 的生产分派、04 的长短程参考、05 的计量验证和 06–08 的引擎先导。15 的 NEP 接口、22 的 CLI、23 的许可清单和 25 的补充 NVE 也可以开始。15 结单时需要 04 在该候选上的 100 步 smoke。

首版试用以平衡并放大的金刚石碳、致密水和 FCC 铜为对象。09 逐材料验收 SCR。10–13 以 03、09 和冻结的计时协议为正式采样条件，每批独占单节点四张 L20。10、11 做同卡数比较，12 使用固定单卡 WPE，13 衡量 DMG-MD 自身强扩展和内部缓存策略。可比格全部实测，算法不等价或容量受限的格给依据，可比待测的格继续跟踪。性能验收不设固定胜负或最低提速倍率。

14 汇合试用证据。21 的八卡窗口独立于四卡试用，长程体系为碳、水和 BaTiO₃-ZBL。25 的三引擎 NVE 单列为补充。26 从实际候选源码包复建并核对冻结证据，27 由维护者签署。

## 单节点试用、科学验证与性能

| 工单 | 直接前置 |
| --- | --- |
| [01 冻结碳、水、铜三套公开输入](issues/01-public-equilibrated-inputs.md) | 可开工 |
| [02 实现生产多卡分派与启动拒绝](issues/02-production-domain-routing.md) | 可开工 |
| [03 验证变盒、输出与跨卡重启](issues/03-box-output-cross-rank-restart.md) | 02 |
| [04 统一长短程参考并恢复 100 步 smoke](issues/04-long-reference-smoke.md) | 可开工 |
| [05 用短作业验证配对计时](issues/05-paired-job-timing.md) | 可开工 |
| [06 普通 GPUMD 同卡数 NPT 先导](issues/06-gpumd-matched-npt-pilot.md) | 可开工 |
| [07 LAMMPS/KOKKOS/NEP 同卡数 NPT 先导](issues/07-lammps-kokkos-nep-npt-pilot.md) | 可开工 |
| [08 GPUMD-WPE 单卡 NPT 先导](issues/08-wpe-single-gpu-npt-pilot.md) | 可开工 |
| [09 完成三材料 SCR 科学验收](issues/09-three-material-scr-validation.md) | 01、02 |
| [10 普通 GPUMD 正式同卡数报告](issues/10-gpumd-matched-npt-results.md) | 03、05、06、09 |
| [11 LAMMPS/KOKKOS/NEP 正式同卡数报告](issues/11-lammps-kokkos-nep-matched-results.md) | 03、05、07、09 |
| [12 GPUMD-WPE 单卡与迁移报告](issues/12-wpe-single-and-migration-results.md) | 03、05、08、09 |
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
| [25 用已安装的引擎完成 NVE 短程物理对照](issues/25-legacy-three-engine-nve-smoke.md) | 可开工 |
| [26 D4 从候选源码包独立复建并核对证据](issues/26-d4-reproduce-release-candidate.md) | 14、21、23、24 |
| [27 D5 维护者签署发布结论](issues/27-d5-maintainer-release-signoff.md) | 26，由维护者签署 |

## 决策与适用范围

输入与 SCR 的正式冻结采用决策 01、03。长程参考迁移采用决策 02。决策 04 先确定采样协议，再接收 DN5 数据作缓存采纳判断。决策 05 核对八卡机时。决策 06 规范普通 GPUMD 与 LAMMPS/KOKKOS/NEP 的来源和控压可比条件。

[决策 01 三种公开输入的规模与快照](../v1-release-readiness/issues/01-public-material-inputs.md) · [决策 02 长短程 GPUMD 参考迁移](../v1-release-readiness/issues/02-long-regression-reference.md) · [决策 03 各材料 SCR 统计协议](../v1-release-readiness/issues/03-scr-science-protocol.md) · [决策 04 计时协议与 DN5 默认策略](../v1-release-readiness/issues/04-dn5-timing-and-adoption.md) · [决策 05 八卡发布资源](../v1-release-readiness/issues/05-eight-gpu-release-site.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../v1-release-readiness/issues/06-three-engine-provenance.md)

G-A、G-B、DN0–DN4 继续按原冻结范围做回归。非 NEP 的局部多卡、各向异性或三斜 NPT、跨物理节点验证等后续工作按规格的范围条款管理。
