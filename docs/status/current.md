# DMG-MD 当前实现进度

类别：进度与实测备忘。  
更新日期：2026-09-14。  
代码基线：`5b1e8ce`；工作区未提交修改不计入已验证结论。

## 当前结论

`dmg-md` 已完成 single-rank 路径和一 MPI rank 一 GPU 的 replicated-data MPI prototype。
当前每个 rank 仍保存完整坐标和类型，并执行完整 ordinary NEP scratch；积分、thermo、输出记录
和 rank 0 I/O 按 balanced owned range 唯一归属。

尚未实现空间域分解、ghost/halo、原子迁移或 phase-level NEP 中心并行。因此当前多 GPU
结果是正确性原型，不是可发布的 scaling 结果。

## 已实现能力

- 构建和运行固定使用 `../env/md-mpi.sh` 提供的 Open MPI+UCX/CUDA 栈；
- shared local rank 绑定唯一 GPU，并校验同节点 CUDA UUID 不重复；
- 默认 HostStaged，CudaAware 需同时通过 `MPIX_Query_cuda_support()` 和四类 device-buffer
  collective 数值自检；
- GPUMD 最小数值核心位于 `src/gpumd_compat/`，构建和运行不依赖
  `../gpumd-reference`；
- 支持 NEP4、NEP5、对应 ZBL/typewise/flexible 分支，以及 NVE、`nvt_ber`、
  `correct_velocity`、thermo/XYZ/restart 和多段 run；
- `model.xyz` 和 `run.in` 先完成兼容解析与 fail-fast 校验，再初始化 GPU；
- position/velocity 每步 Allgatherv，owned thermo Allreduce，输出由 rank 0 按 global ID
  恢复稳定顺序；
- runtime 输出 `DMGMD_COMM`、center coverage、MPI/GPU 环境记录和
  `DMGMD_TIMING phase=run/total`；
- long-NVE runner 支持 stage 哈希 checkpoint、失败分类、干净重试、保留失败尝试和断点续跑。

现行合同分别见：

- [输入输出兼容矩阵](../standards/compatibility-matrix.md)；
- [数据布局](../standards/data-layout.md)；
- [replicated MPI 协议](../standards/replicated-mpi.md)；
- [Golden Test 标准](../standards/golden-test-standard.md)。

## 已记录的验证结果

### CPU 与短程 differential

2026-09-09 的记录包括：

- CTest 4/4；
- committed baseline 4/4；
- DMG-MD single-rank candidate baseline 4/4；
- 1/2/4 rank × HostStaged/CudaAware 共六组 MPI differential 全部通过；
- `src/gpumd_compat/` 切换后，force/energy/virial 差异仍在浮点噪声量级。

baseline 的环境、命令、case 和校准证据见 [baseline-results.md](./baseline-results.md)。

### 长程 suite

已记录通过：

- 三个物理 fixture 的 smoke 切片；
- C case 的 1↔2 rank restart 和 CudaAware smoke；
- 4096-atom C、seed 0、1 rank、HostStaged 的 100000-step `long` section；
- 四个 potential 兼容变体和 C 体系的 NVT 扩展 smoke。

尚未完整执行 nightly，以及 release 的三体系、5 个 seed、1/2/4/8 rank、双后端全矩阵。
定义但未完整执行的矩阵不得写成已通过。

## 已知缺口

- 非零 rank 的 legacy `neighbor.out` 当前依赖 rank 0 创建的临时目录；跨节点 node-local
  `/tmp` 风险仍未整改，见 [多节点 I/O 计划](../plans/multi-node-io.md)；
- domain decomposition、ghost/halo、migration 和真正的 NEP 中心分片仍是计划，见
  [域分解计划](../plans/domain-decomposition.md)；
- malformed potential corpus、若干 cutoff/ZBL 边界和 future command 语义仍待验证，见
  [风险与待办](../plans/risk-and-backlog.md)；
- replicated-full 阶段禁止从 `DMGMD_TIMING` 或正确性作业 wall time 推导性能结论。

## 最近变更

2026-09-14 新增：

- `DMGMD_TIMING` 的 run-segment 与 total 汇总；
- long-NVE stage checkpoint、输出哈希核验与 `--resume-work`；
- 默认一次干净重试、失败尝试归档和 CUDA OOM 等失败分类；
- 对上述编排逻辑的 CPU 单元测试。

这些功能改变长作业的可恢复性和诊断信息，不改变物理验收容差，也没有引入性能通过门槛。
