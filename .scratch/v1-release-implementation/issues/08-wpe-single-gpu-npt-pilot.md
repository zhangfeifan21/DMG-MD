# 08: GPUMD-WPE 单卡 NPT 先导

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

在现有短输入上识别 WPE 宿主与实际加载的引擎。与 01 共用静态校准和留出快照的核验记录，供 12 在冻结输入上复核单卡及迁移配对。

## 验收条件

- [ ] 登记实际宿主版本、可执行文件 SHA-256、集成层和发行库。L20 核验对象是宿主 `a0e55bc`、Wisevolve 1.0.0，`npt_scr` 与 `npt_ber` 来自该宿主。逐作业核对原势 SHA-256，正式输入对照 01 的冻结哈希。
- [ ] 只暴露一个 GPU UUID，用遥测交叉核对该卡占用、其余卡空闲。日志中 `Potential engine: Wisevolve Potential Engine 1.0.0` 恰好一次、`number of GPUs = 1`，多卡 `Try to use` 标记零次。以错误标记或回退样例验证该作业会作废。
- [ ] 对三材料以普通 GPUMD 为参考，在独立快照上按 ID、单位和分量核对能量、力与 virial。查清 WPE 能量与 virial 差异来源，记录适用容差，供 01 在留出帧上终验。分别运行 SCR、Berendsen 短段，核对输出、步数及盒响应。
- [ ] 交付引擎日志、设备、势、退出码和可重放的每材料清单。列出 WPE(1) 对 DMG-MD(1/2/4) 的三个配对。身份或势不符作废重跑，模型或单卡容量受限时附证据。

## 依据

[V1.0 验收规格，用户故事 8、9、11、19、22](../../v1-release-readiness/spec.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md) · [决策 03 SCR 科学验收](../../v1-release-readiness/issues/03-scr-science-protocol.md#answer) · [决策 06 引擎身份与控压可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer)
