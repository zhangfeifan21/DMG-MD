# 24: D3 交付原生构建指南与各势示例

Status: ready-for-agent

Blocked by: 19, 22

直接前置工单为[19 C5 验收势与六种系综的支持矩阵](19-c5-force-ensemble-matrix.md)、[22 D1 交付 CLI、版本和环境探针](22-d1-cli-version-probe.md)。

## 交付内容

普通组员仅凭用户指南和已有兼容依赖，即可从干净源码完成原生构建，验证 GPU/MPI 环境并运行各势示例。

## 验收条件

- [ ] 在新目录、普通账号、已有兼容 CUDA、CUDA-aware Open MPI 与 UCX 的节点，按指南从干净 checkout 执行原生 CMake 配置、构建及自检，记录实际编译器、依赖、构建选项、设备架构和版本输出。
- [ ] 按已验收的势清单逐势提供可运行示例、取得势的方式和预期科学输出。实际执行 NEP、LJ、EAM/alloy 及声明支持的示例，使用 C5 矩阵核对盒型、卡数、系综、输出格式及新作业重启。
- [ ] 在分配的设备上都能照说明执行单卡探针和多卡探针。按组员迁移流程准备结构、势和多段输入，演示一个合格的 NEP 局部作业和一个有可执行单卡恢复指引的失格作业。
- [ ] 普通账号保留从零开始的命令与输出，对照指南逐步复现。写明已验证支持范围、依赖及已知限制，使 D4 能对打包后的来源重新执行同一流程。

## 依据

[V1.0 验收规格，用户故事 1、7、16、17、18、20](../../v1-release-readiness/spec.md) · [原生部署与环境自检](../../../docs/operations/deployment.md) · [正式发布验收](../../../docs/plans/dmg-md-v1.0-development-workflow.md#发布验收) · [系综与重启合同](../../../docs/standards/ensembles.md)
