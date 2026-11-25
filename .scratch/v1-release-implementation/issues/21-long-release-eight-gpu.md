# 21: 完成八卡 100,000 步 release 矩阵

Status: ready-for-agent

Blocked by: 20

直接前置工单为[20 恢复四卡 10,000 步 nightly](20-long-nightly-four-gpu.md)。

## 交付内容

维护者获得同一台八卡机、同一份候选二进制的完整长程正确性记录。资源窗口预计为 2026 年 12 月下旬，预计到位 1 至 2 台 8 × RTX 5090。实际交付型号、卡数或窗口不符时重开决策 05。到机检查可提前准备，执行完整矩阵须有 20 的 nightly 通过记录。

## 验收条件

- [ ] 到机后实录 GPU 型号、8 个 UUID、PCIe/NUMA 拓扑、逐 rank 的 CPU/GPU 绑定及驱动、CUDA、MPI、UCX 版本。若为 RTX 5090，构建须显式包含 `sm_120`（架构参数含 `120`）。记录候选的构建参数与哈希，并核对能在该机运行的普通 GPUMD 5.9.1 `11ba6fb` 参考构建及实际二进制 SHA-256。
- [ ] 以 8 rank 分别做 HostStaged、CudaAware 预检，实测 GPU 点对点访问和 UCX `cuda_ipc`。CudaAware 须有 `cuda_aware_p2p_self_test=passed`。发生回退时归档实际后端和原因，两后端均通过后才进入矩阵。
- [ ] 同一台机器、同一候选重跑 release 清单全部 7 例、种子 0 至 4、P=1/2/4/8 与双后端。碳、水、BaTiO₃-ZBL 的 NVE 和 NVT Berendsen 各 100,000 步，4 个派生例按清单核对静态量与短轨迹。逐格核对输入、实际路径、步数、输出与退出码。
- [ ] 按冻结阈值核对漂移、热力学和结构统计、双向同构型回放及跨 rank 重启。将原始检查点、参考身份、失败或重跑记录与结果关联，回放及重启同在该八卡机完成。
- [ ] 交付可重算的完整单机覆盖表，资源或格缺失时保留待测。L20 上的 nightly、截至 P4 的 release 和三项性能报告各按原站点单列。

## 依据

[V1.0 验收规格，用户故事 2、6、7、13、19、20](../../v1-release-readiness/spec.md) · [决策 05 八卡发布资源](../../v1-release-readiness/issues/05-eight-gpu-release-site.md#answer) · [长程 profile 与矩阵](../../../tests/long_nve/README.md) · [冻结 golden 与误差约定](../../../docs/standards/golden-test-standard.md) · [决策 02 长短程参考迁移](../../v1-release-readiness/issues/02-long-regression-reference.md#answer)
