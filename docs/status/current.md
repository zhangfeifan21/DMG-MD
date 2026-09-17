# DMG-MD 当前实现进度

类别：进度与实测备忘。

更新日期：2026-09-15。

代码基线：`490277f`（M0）加上工作区未提交的 M1 修改；未提交修改不计入历史已验证结论，
提交后须以最终 revision 补记 M1 验证记录。

## 当前结论

`dmg-md` 已完成 single-rank 路径和一 MPI rank 一 GPU 的 replicated-data MPI runtime。
当前每个 rank 仍保存完整坐标和类型，并执行完整 ordinary NEP scratch；积分、thermo、输出
记录和 rank 0 I/O 按空间 slab 所有权唯一归属（M1，工作区未提交）：P=1 为平凡映射，
P>1 沿最长边等宽 fractional slab，原子跨 slab 时做 global_id 上的逻辑所有权迁移。

尚未实现 ghost/halo、本地数组压缩或 phase-level NEP 中心并行（M2 起）。因此当前多 GPU
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
- position 每步 indexed Allgatherv（M1：按 owned index list 打包、按 global_id/slot
  scatter plan 还原），ownership map hash 每步 Allreduce 校验（P>1），迁移步用旧
  ownership 恢复最新 velocity/unwrapped，owned thermo Allreduce，velocity 不再每步复制
  （M0），输出由 rank 0 按 global ID 恢复稳定顺序；
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

- R28 多节点 rank I/O 隔离整改尚未形成带最终提交 revision 的验证记录，严格双物理节点
  （互不可见 TMPDIR）验收也尚未执行，见 [multi-node-io.md](../plans/multi-node-io.md)；
- M1（空间 slab 所有权 + global_id 逻辑迁移）已在工作区实施并通过迁移矩阵、
  differential 与 long-NVE smoke（无提交 revision，验证记录待提交后补记）；
  M2/M3（ghost/halo、本地布局、NEP 中心分片、点对点通信）仍是计划，见
  [域分解计划](../plans/domain-decomposition.md)；M0 已实施（见下）；
- malformed potential corpus、若干 cutoff/ZBL 边界和 future command 语义仍待验证，见
  [风险与待办](../plans/risk-and-backlog.md)；
- replicated-full 阶段禁止从 `DMGMD_TIMING` 或正确性作业 wall time 推导性能结论。

## 最近变更

2026-09-15 M0（删除每步 velocity Allgatherv）已实施并验收：

- 修改 `src/runtime.cu`：删除 `run_segment` 段末的每步 velocity Allgatherv；
  correct_velocity 触发步（`step % interval == 0`）在调用 `correct_device_velocity`
  之前补一次 velocity Allgatherv 恢复复制态，修正后的 Bcast 重新复制完整数组；
- 同步修订 `tests/mpi/run_mpi_differential.py` 的 `collective_calls` 下限断言
  （3→2，普通步只剩 position Allgatherv + thermo Allreduce）与
  `docs/standards/replicated-mpi.md` 的每步顺序和字节表；
- 验证环境：`../env/md-mpi.sh`（Open MPI 5.0.10 + UCX 1.22.0），4× RTX 4090，
  `source ../env/md-mpi.sh` 后：
  - `ctest --test-dir build`：4/4 通过；
  - `python3 tests/mpi/run_mpi_differential.py --candidate ./build/dmg-md --devices
    0,1,2,3`：1/2/4 rank × HostStaged/CudaAware 六组全 PASS，容差未放宽，
    NVE excursion/slope 与基线一致；
  - pre/post 二进制 A/B（同一 GPU 矩阵、同一输入）：committed 四个 baseline case
    全部输出（含 multi_nvt_restart 的 initial+resume 段）120 个文件、以及自建
    correct_velocity 输入（25 步、interval 10、NVE/NVT 两体系）48 个文件，
    pre/post 逐字节一致（`cmp`，0 差异）；
  - `DMGMD_COMM` 字节核算与修订后标准逐字段吻合：普通步 MPI input `24N+64P`、
    output `24NP+64P`（输出步另加 `152N` gather）；correct_velocity 触发步
    input `72N+64P`、output `72NP+64P`；HostStaged 普通步 D2H/H2D 各减
    `24N`/`24NP`（实例 N=8、P=2：D2H 1728→1536、H2D 896→512 B）。
    基线二进制为修改前构建（`f1480c9` 工作树，`/tmp/dmg-md-preM0`）。

2026-09-14 新增：

- `DMGMD_TIMING` 的 run-segment 与 total 汇总；
- long-NVE stage checkpoint、输出哈希核验与 `--resume-work`；
- 默认一次干净重试、失败尝试归档和 CUDA OOM 等失败分类；
- 对上述编排逻辑的 CPU 单元测试。

这些功能改变长作业的可恢复性和诊断信息，不改变物理验收容差，也没有引入性能通过门槛。
