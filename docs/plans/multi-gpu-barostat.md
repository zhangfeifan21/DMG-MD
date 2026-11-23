# 局部 NPT 与 DN5 开发计划

状态：IN PROGRESS。核对日期：2026-11-20。

DN0–DN4 已在限定范围完成数值验收。生产分派边界已经确定；DN5 仍需验证局部 NPT 策略的实际收益及采用条件。近期实施顺序统一在[开发进度](dmg-md-v1.0-progress.md)维护，本文保留局部 NPT 特有的技术问题。

## 已完成的数值基础

`DMGMD_DOMAIN_NPT=rebuild` 在实际变盒后强制重建缓存。`affine` 根据位移和盒形变消耗的安全余量，决定刷新还是重建。

已有证据覆盖单节点碳的统计，以及积分次序、边界数据就绪和提交新盒前的几何检查。具体盒、步长、rank 数及通信后端见[局部 NPT 标准](../standards/domain-npt.md)，已执行结果见[验证记录](../status/validation/domain-npt-validation.md)。

## DN5 开发与验收

生产分派按已确定的 [D-013](../standards/architecture-decisions.md#production-domain-routing)实现，实施顺序见开发进度。本专题关注局部 NPT 的缓存策略、变盒边界和性能证据。

采用生产入口时，继续核对旧力的使用次序、提交新盒前的全 rank 资格检查，以及下一次求力前的刷新或重建。`rebuild` 提供强制重建对照，`affine` 只有通过位移和形变余量检查才复用；具体条件由局部 NPT 标准维护。

DN5 在固定卡数、后端、输入与输出频率下比较内部 `off/rebuild/affine`，量化缓存复用的影响。P1 和同卡数全量复制保留为数值参照。碳、水和铜的最终输入通过科学检查后，外部性能对照包括同卡数普通 GPUMD、同卡数 LAMMPS/KOKKOS/NEP 及固定单卡 WPE，另报 DMG-MD 的自身强扩展。[比较矩阵](../status/performance/npt-benchmarks.md#performance-matrix)

三种材料的 SCR 科学验收和三项对标报告用于试用判断，Berendsen 单列预平衡结果与时长。默认缓存策略采纳以实测完整轨迹的耗时、统计区间和适用工况为依据。双物理节点以及三维分解、负载均衡和中间场交换另见[扩展计划](domain-decomposition.md)。
