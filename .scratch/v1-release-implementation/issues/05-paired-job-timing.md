# 05: 用短作业验证配对计时

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

提交一组可复核的短作业后，运行器能从实际启动到必需输出写完生成配对记录，供各引擎先导和正式计时复用。

可先用短作业和可手算的样本验证计量方法。正式采用的预热、重复、区间方法由计时决策登记。

## 验收条件

- [ ] 用可重复的短算例跑完整一对作业，记录单调墙钟的开始和结束、退出码、作业目录、预期文件与实际末帧。本机引擎的 run/loop 计时另列。混合 NPT→NVT 的段时可以同整段墙钟一起查看。
- [ ] 每次记录输入和二进制哈希、算法、精度、物理步长、步数、输出频率、介质、实际 GPU、CPU/NUMA、后端和分派。相同物理与 I/O 要求以及同卡数时相同 CPU 总预算可从两份记录核对。
- [ ] 可指定并复现预热、配对重复、交错执行顺序和区间算法。以手算可复核的样本检查绝对时间、按约定方向的耗时比、配对置信区间、稳态 ms/step 或 atom-step/s，以及 GPU·小时 = 卡数 × 墙钟小时。
- [ ] 用非零退出、缺末帧或身份不匹配的例子验证该次测量不会进入倍率。保留重复序号、缺测原因和原始时长，可从原始记录重建结果。先导产物注明规模及用途。

## 依据

[V1.0 验收规格，用户故事 10、11、12、19、21、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [现有计时入口和 profile](../../../tests/benchmark/README.md)
