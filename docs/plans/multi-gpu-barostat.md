# 局部 NPT 与 DN5 开发计划

状态：IN PROGRESS。核对日期：2026-11-20。

DN0–DN4 已在限定范围完成数值验收。生产分派边界已经确定；DN5 仍需验证局部 NPT 策略的实际收益及采用条件。近期实施顺序统一在[开发进度](dmg-md-v1.0-progress.md)维护，本文保留局部 NPT 特有的技术问题。

## 已完成的数值基础

`DMGMD_DOMAIN_NPT=rebuild` 在实际变盒后强制重建缓存。`affine` 根据位移和盒形变消耗的安全余量，决定刷新还是重建。

已有证据覆盖单节点碳的统计，以及积分次序、边界数据就绪和提交新盒前的几何检查。具体盒、步长、rank 数及通信后端见[局部 NPT 标准](../standards/domain-npt.md)，已执行结果见[验证记录](../status/validation/domain-npt-validation.md)。

## DN5 开发与验收

生产分派按已确定的 [D-013](../standards/architecture-decisions.md#production-domain-routing)实现，实施顺序见开发进度。本专题关注局部 NPT 的缓存策略、变盒边界和性能证据。

采用生产入口时，继续核对旧力的使用次序、提交新盒前的全 rank 资格检查，以及下一次求力前的刷新或重建。`rebuild` 提供强制重建对照，`affine` 只有通过位移和形变余量检查才复用；具体条件由局部 NPT 标准维护。

性能对照固定卡数、后端、输入和输出频率，比较内部 `off/rebuild/affine`，确认缓存复用节省了哪些工作。P1 和相同卡数的全量复制保留数值参照。碳、水和铜的最终生产输入分别完成同快照能量、力、virial 及平衡态检查，再按[测量方法](../status/performance/npt-benchmarks.md)比较 WPE 单卡与 DMG-MD 1/2/4 卡。

SCR 决定试用条件，Berendsen 单独记录预平衡结果和时长。局部策略采用须有完整轨迹的耗时证据。双物理节点以及三维分解、负载均衡和中间场交换的后续工作见[扩展计划](domain-decomposition.md)。
