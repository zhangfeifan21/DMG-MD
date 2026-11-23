# 08: GPUMD-WPE 单卡 NPT 先导

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

组员能识别实际加载的 WPE 引擎，在一张 GPU 上用同快照运行 SCR 和 Berendsen，并将该作业与普通 GPUMD 的结果区分。

## 验收条件

- [ ] 记录当次 WPE 宿主、集成层和发行库的已知版本、可执行文件及实际 GPU。L20 的已测组合为宿主 `a0e55bc` 和 Wisevolve 1.0.0。
- [ ] 在公开碳、水、FCC 铜的短作业中仅暴露一张卡，从日志验证实际 WPE 引擎标记和设备。给出无 WPE 标记或回退到其他求力后端的拒绝证据。
- [ ] 在相同盒、坐标、速度及势的快照上核对逐原子能量、力与 virial，并分别运行 SCR、Berendsen 短段，核对输出、单位、步数和盒响应。
- [ ] 交付可按冻结输入重放的启动方式及先导清单，标出后续 WPE(1) 对 DMG(1)、DMG(2)、DMG(4) 的三个配对。有容量或模型限制时附作业证据。

## 依据

[V1.0 验收规格，用户故事 8、9、11、19、22](../../v1-release-readiness/spec.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md)
