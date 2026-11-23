# 17: C2 交付单卡 LJ

Status: ready-for-agent

Blocked by: 16

直接前置工单为[16 C1 统一势输入与诊断](16-c1-potential-input-contract.md)。

## 交付内容

用户能在单卡用 GPUMD 格式的元素对 LJ 参数运行单组分和多组分作业，并按锁定参考检查能量、力和 virial。

## 验收条件

- [ ] 覆盖 `lj T symbols` 和 T×T 元素对的 epsilon、sigma、cutoff，验证实际按元素顺序选取各参数。缺行、参数数量错误或不合法的输入得到可复现的诊断。
- [ ] 在截断两侧及边界用静态快照核对未平移的截断能量、力与 virial，并对照锁定参考的精度和单位。用两种及以上元素验证非同类原子对的参数。
- [ ] 用缩盒后进入截断范围的原子对验证新邻居参加下一次求力。完成短 NVE、thermo、轨迹与 restart 的单卡作业级对照，冻结通过的参考、输入和差异。
- [ ] 交付可单独运行的 LJ 示例和复查命令，登记待 C5 统一执行的六系综矩阵与单卡支持限制。

## 依据

[V1.0 验收规格，用户故事 1、14、16、19](../../v1-release-readiness/spec.md) · [C0–C5 力场验收](../../../docs/plans/dmg-md-v1.0-development-workflow.md#力场开发卡) · [冻结 golden 与误差约定](../../../docs/standards/golden-test-standard.md)
