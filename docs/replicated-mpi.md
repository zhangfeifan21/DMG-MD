# Replicated-data MPI prototype

## 数据所有权与每步顺序

每个 rank 的 device arrays 都以全局 `N` 为 SoA stride，持有完整 position/type/mass 等输入。
rank `r` 唯一拥有半开区间 `owned=[begin_r,end_r)`；区间使用 quotient/remainder 均衡划分，
按 rank 顺序连续且覆盖 `[0,N)`。

每步执行顺序为：

1. 只对 owned 区间做 velocity-Verlet first half；
2. 将 owned position 打包成 AoS，以 `MPI_Allgatherv` 恢复每张 GPU 的完整 position；
3. 每张 GPU 用 ordinary `NEP` 对完整 replicated coordinates 计算 scratch output；
4. 只对 owned 区间做 velocity-Verlet second half；
5. 只对 owned kinetic/PE/virial 求 local sum，再对 8 个 double 做 `MPI_Allreduce`；
6. NVT 如需缩放，只写 owned velocity；
7. `MPI_Allgatherv` owned velocity，恢复 replicated velocity；
8. 输出步只 `MPI_Gatherv` owned position/velocity/force/PE/virial 到 rank 0；rank 0 按 global ID
   写文件。

non-owned NEP output 只是 scratch，不参加积分、thermo 或输出。它没有 ghost 身份；本阶段
`ghost_count` 必须为 0，也没有 halo、迁移或 neighbor cache 跨步所有权。

## NEP 中心分片完整性证明

启动时每个 rank 建立 owned mask，再对 N 个整数做 `MPI_Allreduce(SUM)`。每个元素必须严格为
1；否则运行失败。rank 0 同时记录每个半开区间、missing 数和 overlapping 数。这证明
**authoritative owned output partition** 完整且无重叠。

它不证明把 GPUMD `NEP::N1/N2` 直接设为 owned 区间后数学仍完整。代码证据表明该做法当前
不完整：

- radial force 读取中心邻居的 `Fp[n2]`（锁定 GPUMD `src/force/nep.cu:727-728`）；
- many-body force 读取反向 directed partial `f12(n2,n1)`
  （`src/force/potential.cu:209-250`）；
- 这些分片外 intermediate 不会由仅覆盖 `[N1,N2)` 的 descriptor/partial kernels 生成。

因此启动记录明确包含：

```text
owned_output_coverage=complete
nep_kernel_centers=replicated-full
nep_N1_N2_shard_complete=false
```

本阶段选择完整 NEP scratch 以保持数值正确性，然后仅承认 owned 区间。未来若要真正减少 NEP
中心计算，必须先暴露 descriptor、`Fp`、directed partial 的 phase boundary 并交换这些中间量；
不能直接放宽 `N1/N2`。这不是 ghost/迁移实现。

## 通信后端

唯一受支持的运行栈是由仓库同级 `env/md-mpi.sh` 选择的 Open MPI+UCX。脚本固定 UCX PML、
Open MPI/PMIx component path，并排除 HCOLL；测试不得回退到 system MPI/UCX。

`HostStaged` 是默认及必需后端。每次 device collective 都严格执行：

```text
CUDA source --pack--> CUDA send
CUDA send --D2H--> pinned host send
pinned host send --Open MPI/UCX collective--> pinned host receive
pinned host receive --H2D--> CUDA receive --unpack--> CUDA destination
```

`CudaAware` 使用相同的 pack/unpack，把中间 send/receive device pointer 交给 Open MPI。
启动先调用 `MPIX_Query_cuda_support()`；只有所有 rank 都报告 supported，才允许进入
device-buffer `MPI_Allreduce(MPI_IN_PLACE)`、`MPI_Allgatherv`、`MPI_Gatherv` 和 `MPI_Bcast` 数值
自检。四类 collective、CUDA 同步和结果校验全部通过后才选择 CudaAware，否则回退
HostStaged。因此 capability query 只是安全前置条件，不会替代数值自检。

## 每步通信量

rank 0 每步输出一行 `DMGMD_COMM`。记录的是 collective API 的全局 input/output buffer 字节，
而不是 MPI 私有算法的物理 wire traffic。

令 `P=world_size`、`N=global_count`。没有 adaptive timestep 和 dump snapshot 的普通一步：

| collective | 全局 MPI input bytes | 全局 MPI output bytes |
| --- | ---: | ---: |
| position Allgatherv (3 doubles/atom) | `24N` | `24NP` |
| thermo Allreduce (8 doubles/rank) | `64P` | `64P` |
| velocity Allgatherv (3 doubles/atom) | `24N` | `24NP` |
| 合计 | `48N + 64P` | `48NP + 64P` |

HostStaged 同一步的 D2H/H2D 分别也是 `48N + 64P` 和 `48NP + 64P`。adaptive timestep 另加
一个 host double max Allreduce，即 MPI input/output 各 `8P`。

若本步触发 `correct_velocity`，另加一个 3N-double velocity Bcast：MPI input `24N`、output
`24NP`；HostStaged 同时增加 D2H `24N` 和 H2D `24NP`。

输出步 gather 的基础 snapshot 包含 position(3)、velocity(3)、force(3)、PE(1)、virial(9)，
额外 MPI input/output 各 `19 * 8N = 152N` bytes；unwrapped position 再加 `24N`。HostStaged
增加同量 D2H；CudaAware 在 rank 0 增加同量 output download。启动日志、输入 fingerprint 和
中心 coverage 属于一次性 control-plane 通信，不计入 step 行。

## I/O 与 NEP_MULTIGPU

所有 thermo/XYZ/restart formatter 只在 world rank 0 调用。GPUMD ordinary NEP 内部会周期性
append `neighbor.out`；非零 rank 被切换到由 rank 0 创建的临时工作目录，其中
`neighbor.out` 指向 `/dev/null`，所以作业目录仍只有 rank 0 写。正常退出时 rank 0 清理临时
目录。

runtime 直接构造 ordinary `NEP`，并在选择 CUDA device 后不再枚举设备决定势实现；没有
构造 `NEP_MULTIGPU`，因此 MPI rank 看见多张本机 GPU 也不会自动占用它们。

## 验证入口

`tests/mpi/check_environment.py` 首先检查指定 executable 的 Open MPI/UCX 链接、CUDA build
capability、UCX `cuda_copy/cuda_ipc`、GPU 唯一绑定及实际 device-pointer collectives。只有该
独立门槛通过，`tests/mpi/run_mpi_differential.py` 才对 1/2/4 rank 运行全部已锁定 case，并检查：

- 初始 per-atom energy/force/9-component virial 对 GPUMD golden；
- 短 NVE trajectory 和由 thermo 得到的 max excursion/drift slope；
- 不同 rank 数 thermo header、列数、segment/row 结构；
- 每 rank 启动记录、唯一 GPU UUID、owned coverage proof 和每步通信记录；
- 默认对 HostStaged 和 CudaAware 运行同一矩阵并做 cross-backend differential。
