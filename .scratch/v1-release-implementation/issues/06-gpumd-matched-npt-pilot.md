# 06: 普通 GPUMD 同卡数 NPT 先导

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

从现有公开短输入开始，验证普通 GPUMD 在 P=1、2、4 实际使用的设备、NEP 和控压算法。向 01 提供可复用的静态核验记录，向 10 提供逐材料、逐卡数的 NPT 作业证据。

## 验收条件

- [ ] 记录实际版本、二进制 SHA-256、构建信息和 GPU 型号。逐作业核对原势 SHA-256，正式输入以 01 的冻结哈希为准。普通 GPUMD 的性能记录使用当次构建，短程数值 golden 另按参考清单锁定身份。
- [ ] 日志须有 `number of GPUs = P`，P>1 时 `Try to use P GPUs for the NEP part.` 恰好一次，P=1 时零次。核对 NEP4 元素数、铜的 ZBL 标记，以及两个运行段各自的 `Time used for this run`。以 `CUDA_VISIBLE_DEVICES` 的 UUID 和显存遥测交叉确认选中卡有占用、其余卡空闲。
- [ ] 在碳、水、铜分别检查同快照能量、力、virial，复核 `npt_scr`、`npt_ber` 的温压、阻尼、步数和输出。普通 GPUMD 多卡 NEP 按当前盒检查网格，最长方向每卡至少 10 格，各周期方向至少 3 格。记录运行中盒收缩导致的退出。
- [ ] 按材料、控压器和 P 交付实际分派、退出码、输出及可比性。身份、势或设备不符的作业作废并修正重跑。网格或容量等结构限制附日志列不适用，可比而未跑的格保留待测。

## 依据

[V1.0 验收规格，用户故事 8、19、21、22](../../v1-release-readiness/spec.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md)
