# 各向同性 NPT 数值验收

<a id="pressure-oracle-20261103"></a>
<a id="box-contract-20261103"></a>
## 压力、盒和积分顺序

2026-11-03 的 B0/B1 验证了压力和 virial 单位、六个应力分量，以及盒缩放和输出次序。CPU 合成输入经过生产 writer；五组单卡零步长构型与锁定 GPUMD 逐项对照。压缩和拉伸近零压参考态时，压力按相反方向变化。[控压器标准](../../standards/barostat.md)

<a id="npt-ber-20261103"></a>
<a id="npt-output-20261103"></a>
<a id="npt-regression-20261103"></a>
<a id="npt-regression-20261104"></a>
## 短程、重启与错误处理

Berendsen 通过 200 步单卡 GPUMD 对照、P1/P2/P4 全量复制验收，以及负压和大体模量边界检查。B4 另覆盖盒输出、跨 rank 新作业 restart、设置不一致、错误输入和旧 NVE/NVT 回归。NEP5、含 ZBL 模型及水的短程结果各有对应测试。

Berendsen 的体积涨落不能证明平衡态 NPT 分布。执行方法见[测试手册](../../../tests/npt/README.md)。

<a id="npt-scr-20261103"></a>
<a id="npt-scr-final-20261103"></a>
## 2026-11-03 初次 SCR 统计

18 个预注册分析节点的总体判定为 INCONCLUSIVE，各节点的科学结论和样本身份继续保留。后续独立确认另采新的完整链。[18 次分析结果](npt-scr-formal-results.json)

<a id="npt-scr-confirmation-20261104"></a>
<a id="npt-matrix-20261104"></a>
## 2026-11-04 SCR 独立确认与 G-B

独立确认固定使用 216 原子 NEP-C、300 K 和 0 GPa，在单节点全量复制模式完成 160 条新链。最终分析按预注册执行一次。

主比较是单卡 HostStaged 候选与普通 GPUMD。基准步长和半步长各有 32 条候选链及 32 条参考链；基准步长另比较 P2/P4 的 HostStaged 和 CudaAware。主比较和 MPI 对照的均值、方差、体积累积分布及有效样本量在该范围通过。

两个主比较的体积方差比区间为 [0.93544, 1.02032] 和 [0.93370, 1.02895]。完整配对分析保留估计量、置信区间、20,000 次 bootstrap 设置和逐项判定。[原始配对分析](evidence/npt-scr-gb/paired-analysis.json) · [统计协议](../../../tests/npt/independent-confirmation-protocol.md)

基准和半步长的理论动能目标诊断均为 `THEORY_TARGET_INCONCLUSIVE`。这一诊断与同条件 GPUMD 相容性分别判定，原始配对分析保留两者。

实验局部 NPT 的大盒统计另有独立证据，不能将本节全量复制结论扩展过去。[局部 NPT 验收](domain-npt-validation.md#dn4-final-20261116)
