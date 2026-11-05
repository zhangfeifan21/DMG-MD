# NPT SCR 独立确认：n3 迁移后重新执行

**状态：APPROVED_INDEPENDENT_CONFIRMATION。** Root 于 2026-11-04 批准本执行注册；GPU 正式采样仍须等新的 outer execution freeze、运行环境、短程验收和调度器身份完成核验。

本注册继承 [2026-11-03 协议](independent-confirmation-protocol.md) 的全部科学设计、估计量、种子、步数、burn/block/ESS、置信区间、bootstrap、容差、单次最终分析和停止规则。原协议 SHA-256 为 `fc7db423fcfbf97b15ae4d6a37bf1a3450efb304c143d0b72932d70c1930ab1a`，原 manifest SHA-256 为 `aa9d1f9ae2cabbb174fa8fb30b0798edb5f9035541763d2c7f7f6b44d3fc1580`。本次唯一机器注册为 [independent-confirmation-n3-manifest.json](independent-confirmation-n3-manifest.json)，其 SHA 由新 outer freeze 锁定。

## 迁移原因和唯一例外

原 GPUMD 二进制保持不变，但其 PTX 8.8 在 n3 的驱动 `570.86.10` 上报 CUDA error 222，无法完成初始化。新参考从相同锁定 commit `9d23496e41319b9e2af5221a7df6285387401d1e` 的独立源码副本，以 CUDA 12.8.61、`-arch=sm_89` 构建；285 个源码文件逐项核对一致。新参考 SHA-256 为 `045d1edbf893d6dcb8e18cff0bff27eea70070e078b7c4950cbe1c5cc37ac5f7`。原协议中的参考二进制路径和 SHA 仅在本次执行中由新 manifest 的 reference identity 取代。候选二进制、NEP、RNG 源审计、分析器及其统计依赖均不变。

新参考通过原 4 个 baseline、216/2016 原子的 NPT Berendsen 200-step 对照；反汇编验证 release SCR 的 `initialize_rng()` 仍调用 `system_clock::now()`。UTC-second 启动隔离继续执行，仍不声称观察到了 GPUMD 的实际内部 seed。所有证据的路径与 SHA 记录在新 manifest 的 `migration_registration` 和 outer freeze。

## 批次边界

旧确认批次的 63 条完成轨迹、6 条中断轨迹、91 条 planned 记录及旧 freeze 原样保留；旧确认未做最终统计分析。新批次在 n3 从注册初态重新执行全部 **160 条完整单链**，旧批次的任何轨迹都不进入新数据集，不拼接、不用旧前缀、不复用旧 reference。原 B3 的 18 个 formal looks 继续保持原 `INCONCLUSIVE` 结论。

本次单节点执行使用 GPU 1–7，GPU 0 排除。运行环境通过四 rank 的真实 CUDA-aware device-buffer 通信门槛，并额外冻结环境入口、PMIx/hwloc/libevent 包身份、关键运行库和环境变量。因宿主机硬 memlock 限制为 64 KiB，明确使用 `UCX_TLS=self,sm,tcp,cuda_copy,cuda_ipc`；本次不构成 InfiniBand/RDMA 或跨节点验收。

完成所有 160 条后，必须先审计每条记录的冻结身份、输入、完整行数、输出哈希、launch 时间及环境证明；之后仅运行一次原冻结分析器。任何 FAIL 或 INCONCLUSIVE 都原样报告，不增加预算或调整阈值。G-B 在 root 审核最终结果前保持 PENDING。
