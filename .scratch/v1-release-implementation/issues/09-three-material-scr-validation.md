# 09: 完成三材料 SCR 科学验收

Status: ready-for-agent

Blocked by: 01, 02

直接前置工单为[01 冻结碳、水、铜三套公开输入](01-public-equilibrated-inputs.md)、[02 实现生产多卡分派与启动拒绝](02-production-domain-routing.md)。

## 交付内容

对最终平衡态金刚石碳、致密水和 FCC 铜给出三份可单独审阅的科学结论，作为正式计时的科学前提。

每种材料可独立准备和分析。实际正式采样以该材料已冻结的快照、预注册协议和可核验的科学参考为前提。

## 验收条件

- [ ] 在查看各材料正式样本前，按现有 SCR 决策逐材料登记快照和势哈希、参考和候选身份、参与配置、独立种子和链数、平衡舍弃段、采样长度、最低有效样本量（ESS）、统计区间、判据及不确定时的处置。
- [ ] 在同一最终快照先核对按 ID 对齐的逐原子能量、力、virial 及张量约定。记录参考、阈值、各分量的误差与对应输出。静态核对通过的材料进入 SCR 采样。
- [ ] 在注册的单卡和实际局部多卡配置上运行独立链，记录分派、种子、退出码与原始序列。逐材料核算温度、压力、体积均值及体积涨落、有效样本量和不确定区间，保留分析重放方式。
- [ ] Berendsen 使用独立的预平衡记录，核对收敛方向、稳定性及该段用时。已有 216 原子和 5,760 原子碳的历史统计仅作为各自范围的回归证据。
- [ ] 分别给碳、水、铜标明通过、失败或不确定，附逐项阈值与结果对照。仅三者均通过才解锁 10–13 的正式采样。失败或样本量不足时保留结果及待处理项。

## 依据

[V1.0 验收规格，用户故事 2、8、9、10、19、22](../../v1-release-readiness/spec.md) · [决策 01 三种公开输入的规模与快照](../../v1-release-readiness/issues/01-public-material-inputs.md) · [决策 03 各材料 SCR 统计协议](../../v1-release-readiness/issues/03-scr-science-protocol.md) · [NPT 科学回归](../../../tests/npt/README.md) · [历史局部 NPT 统计范围](../../../docs/status/validation/domain-npt-validation.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)
