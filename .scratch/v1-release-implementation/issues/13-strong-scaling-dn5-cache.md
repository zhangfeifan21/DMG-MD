# 13: DMG-MD 自身强扩展与 DN5 缓存选择

Status: ready-for-agent

Blocked by: 03, 05, 09

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)、[09 完成三材料 SCR 科学验收](09-three-material-scr-validation.md)。

## 交付内容

在同一最终候选上交付 DMG-MD 的固定规模强扩展表、三种缓存策略的内部对照，以及普通多卡 NPT 命令的默认分派证据。10–12 使用这里选定并经过复验的默认策略。

## 正式采样条件

03 的显式局部分派回归、05 的配对计时验证和 09 的三份材料 `PASS` 齐备后开始正式内部采样。等工况独占 4 × L20，配对作业串行，按 05 固定 GPU/NUMA、CPU 总预算、功率、时钟与输出。每种材料的单卡、双卡和四卡自比使用同一冻结输入。

## 验收条件

- [ ] 按材料分别测固定规模 P=1/2/4，注明控压器、实际分派、绝对墙钟和 GPU·小时。计算 `S(P) = T(1) / T(P)`、`E(P) = S(P) / P` 与配对区间。单卡容量受限时给证据并留空相应比值，跨 NUMA 双卡只作扩展补充。
- [ ] 每种材料测 P=2/4 × HostStaged/CudaAware × `off/rebuild/affine` 的 12 格，并记录 `DMGMD_DOMAIN_SUMMARY`。`rebuild` 与 `affine` 共 8 格，各按 05 的 100,000 步、3 或 5 个区组与对数配对 95% t 区间采样。显式 `off` 的 4 格各跑 10,000 步、重复 3 次，只报稳态步时。
- [ ] 按同一材料、P 和后端比较 `T(rebuild) / T(affine)`。三材料所有 `affine` 科学配置均为 `PASS`，且每个比值区间上限均 ≥1、至少一格下限 >1 时选 `affine`，其余情形选 `rebuild`。保存逐格区间和选择依据，适用范围为三材料、单节点 P=2/4、各向同性正交全周期 NPT。
- [ ] 三材料均无可分辨的 `affine` 收益、`E(4) < 0.6` 或 `E(2) < 0.7`、重建计数或尾段及通信耗时异常，任一项都触发剖析和重测。每轮 10,000 步 × 5 次，检查稳态步时和计数。迭代至触发条件消除，或取得维护者对剩余结构限制的书面认定。
- [ ] 退出调试后，将按规则选出的默认值落实到候选。在包含这项改动的最终提交上重跑完整正式内部对照并复核选择。影响科学或分派的改动在最终候选上复验受影响的注册项和回归。留存候选与输入哈希、原始区组、末帧、实际耗时、GPU·小时及剖析结论。效率检查点用于触发调试。
- [ ] 用最终候选检查普通启动入口。在未设置 `DMGMD_DOMAIN_NPT` 的普通 P=2/4 NPT 作业上，合格输入须记录 `DMGMD_DOMAIN mode=m2a` 及所选 `DMGMD_DOMAIN_NPT` 模式。失格输入须在积分和科学输出前集体非零退出，并验证单卡重跑命令。显式 `off` 和 `DMGMD_FORCE_M1_REPLICATED` 保留作内部对照。将普通命令、实际日志及最终候选交给 10–12。

## 依据

[V1.0 验收规格，用户故事 2、10、12、19、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [局部 NPT 与 DN5 计划](../../../docs/plans/multi-gpu-barostat.md) · [局部域 NPT 合同](../../../docs/standards/domain-npt.md) · [决策 03 SCR 科学验收](../../v1-release-readiness/issues/03-scr-science-protocol.md#answer)
