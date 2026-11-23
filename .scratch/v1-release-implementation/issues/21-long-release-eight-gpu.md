# 21: 完成八卡 100,000 步 release 矩阵

Status: ready-for-agent

Blocked by: 20

直接前置工单为[20 恢复四卡 10,000 步 nightly](20-long-nightly-four-gpu.md)。

## 交付内容

维护者获得正式 V1.0 长程正确性证据，能逐格追溯所有体系、种子、rank 和通信后端。

可提前协调机时。执行本单完整 profile 还需资源决策单提供的八 GPU 可用及绑定证据。

## 验收条件

- [ ] 在现有八卡资源决策中取得可核验的分配或窗口，记录实际拓扑、GPU 身份、逐 rank 绑定与 MPI/CUDA 后端探针。确认可用的八张独立设备和运行环境后再启动完整矩阵。
- [ ] 用锁定的 release 几何运行碳、水、BaTiO₃-ZBL，各取五个种子（0–4），NVE 与 NVT Berendsen 各 100,000 步，P=1/2/4/8 和 HostStaged/CudaAware 全覆盖。按清单核对每个格的参数、实际路径、输出和步数。
- [ ] 按冻结阈值验收能量漂移、热力学与结构统计、双向同构型静态回放和跨 rank 数重启。将清单要求的派生例、参考身份、原始检查点、失败及重跑逐一归档。
- [ ] 交付可重算的完整覆盖表。资源未落实或个别格缺失时保留待测状态。四卡 nightly 在自己的 profile 登记。明确本次使用的物理节点与结论适用范围。

## 依据

[V1.0 验收规格，用户故事 2、6、7、13、19、20](../../v1-release-readiness/spec.md) · [决策 05 八卡发布资源](../../v1-release-readiness/issues/05-eight-gpu-release-site.md) · [长程 profile 与矩阵](../../../tests/long_nve/README.md) · [冻结 golden 与误差约定](../../../docs/standards/golden-test-standard.md)
