# DMG-MD 实现进度

更新日期：2026-09-03。

## 当前结论

新的 `dmg-md` executable 已完成 single-rank 阶段，并实现 replicated-data MPI prototype。
Open MPI+UCX 是固定运行栈，默认通信后端仍为 HostStaged，CudaAware 是已通过相同数值矩阵的
可选后端。当前明确不包含空间域分解、ghost/halo、原子迁移或真正的 phase-level NEP 中心
并行。

## 已完成

- CMake 生成 `dmg-md`，校验 GPUMD reference commit 必须是
  `9d23496e41319b9e2af5221a7df6285387401d1e`，并拒绝未由 `../env/md-mpi.sh` 选择的 MPI。
- runtime 固定 Open MPI+UCX；使用 shared communicator 的 local rank 选择 CUDA device，并用
  UUID 检查同一节点没有两个 rank 占用同一 GPU。
- 默认 HostStaged 使用 pinned host send/receive buffer。CudaAware 先要求
  `MPIX_Query_cuda_support()` 在所有 rank 返回 true，再要求 `MPI_Allreduce(MPI_IN_PLACE)`、
  `MPI_Allgatherv`、`MPI_Gatherv`、`MPI_Bcast` 四类 device-pointer 数值自检全部成功。
- `tests/mpi/check_environment.py` 在 numerical suite 前验证 executable/linkage、Open MPI CUDA
  build、UCX PML、`cuda_copy/cuda_ipc`、GPU 数量/唯一绑定及实际 device collectives。
- 每 rank 保留完整 replicated input；balanced owned range 唯一负责积分、thermo 和输出。
  position/velocity 每步 Allgatherv，thermo 从 owned local sums Allreduce。
- rank 0 是唯一 formatter/writer；非零 rank 的 GPUMD `neighbor.out` 内部写入被隔离到
  rank-0 创建的临时目录并指向 `/dev/null`。
- 启动 stdout 记录 Open MPI implementation/version、Open MPI+UCX stack、hostname、
  world/local rank、CUDA device/UUID、capability、自检结果、backend 和 center coverage proof；
  每步记录 collective buffer 通信量。
- 从只读 GPUMD 源树直接编译 tokenizer、Box、NEP loader、neighbor、NEP/NEP-ZBL CUDA
  kernels 和 many-body Potential 核心；newmd 中没有第二份 NEP 数学或参数布局。
- `model.xyz` parser 支持 PBC、9 分量 Lattice、扩展 Properties、未知 property 宽度、
  species/type、默认/显式 mass、charge、velocity、group，并保持 GPUMD 单位转换。
- `run.in` 先解析为带源文件/行号的 typed command IR，再由 runtime 顺序执行。未知或未支持
  命令在 GPU 初始化前 fail fast，不会被静默忽略。
- host/device Atom 数据显式区分 `global_count`、`owned_count`、`ghost_count`、`local_count`
  和 `global_id`。replicated runtime 要求 `ghost_count == 0`；NEP 完整中心计算产生 scratch，
  再由 MPI `OwnedRange` 限定 authoritative output。
- 完成 velocity-Verlet NVE、Berendsen NVT、owned-only thermo、`dump_thermo`、
  `dump_xyz` 和 `dump_restart`。输出按 `global_id` 排序，保持 GPUMD 文件名、header、列序、
  precision、单位、append/overwrite 和多段 run 行为。

## 规范环境验证

本仓库、`gpumd-reference` 与 `env` 是同级目录。从本仓库根目录执行：

```text
source ../env/md-mpi.sh
python3 tests/mpi/check_environment.py \
  --candidate ./build-openmpi-ucx/dmg-md --devices 0,1,2,3 --ranks 4
PASS environment: mpirun (Open MPI) 5.0.10; UCX 1.22.0; cuda_copy/cuda_ipc; CudaAware probe ranks=4
```

2026-09-03 沙箱外实际环境为 8 张 NVIDIA GeForce RTX 4090、driver `580.173.02`、Open MPI
`5.0.10` 和 UCX `1.22.0`。`ompi_info` 证明 Open MPI 以脚本中的 CUDA/UCX 构建，
`ucx_info -d` 列出 `cuda_copy`、`cuda_ipc`。

初次检查同时发现两个环境脚本层问题，并已在 `env/md-mpi.sh` 修正：

- spawned rank 未找到 Open MPI DSO，只选择 `accelerator/null`；现在分别设置 Open MPI 与外部
  PMIx 的 MCA component path，`MPIX_Query_cuda_support()` 返回 true；
- 系统 HCOLL 抢占 device-pointer `MPI_Allreduce` 后 SIGSEGV；现在固定 UCX PML 并通过
  `OMPI_MCA_coll=^hcoll` 排除 HCOLL。

修正后仅 source 脚本、不附加临时命令行环境变量，4-rank device-pointer collective 探针通过。
受限沙箱本身仍可能看不到 NVIDIA driver；GPU 结论必须来自按 `AGENTS.md` 授权执行的沙箱外
预检，不能由沙箱内 `cudaErrorNoDevice` 推断。

## 构建与 CPU tests

```text
source ../env/md-mpi.sh
cmake -S . -B build-openmpi-ucx -DCMAKE_BUILD_TYPE=Release
cmake --build build-openmpi-ucx -j 4
ctest --test-dir build-openmpi-ucx --output-on-failure
3/3 tests passed
```

CMake 实际选择 `/data/home/zhangyifei/software/openmpi-cuda` 和
`/data/home/zhangyifei/software/ucx-cuda`，并成功从 CUDA translation unit 编译 Open MPI
`mpi-ext.h`/`MPIX_Query_cuda_support()`。

## Golden differential 结果

锁定 GPUMD baseline 与 DMG-MD single-rank 均在规范环境下复跑：

```text
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py --reference ../gpumd-reference/src/gpumd --device 0
PASS: all 4 baseline cases match for pinned GPUMD reference
python3 tests/baseline/run_baselines.py --candidate ./build-openmpi-ucx/dmg-md --device 0
PASS: all 4 baseline cases match for candidate
```

覆盖 `single_small_static`、`single_large_nve`、`multi_nvt_restart` 和
`nep_zbl_boundary`。DMG-MD 的 energy、force、position、temperature、restart 字段为零差；
最大非零差为 velocity `8.674e-19`、virial `1.776e-15`、XYZ stress `6.353e-22`，均远低于
已校准容差。

默认 MPI 命令先自动重复环境预检，再运行两后端的 1/2/4-rank 完整矩阵：

```text
source ../env/md-mpi.sh
python3 tests/mpi/run_mpi_differential.py \
  --candidate ./build-openmpi-ucx/dmg-md --devices 0,1,2,3 --timeout 600
PASS HostStaged ranks=1: NVE max_excursion=1.746927e-06 eV/atom slope=3.238564e-06 eV/(atom fs)
PASS HostStaged ranks=2: NVE max_excursion=1.746927e-06 eV/atom slope=3.238564e-06 eV/(atom fs)
PASS HostStaged ranks=4: NVE max_excursion=1.746927e-06 eV/atom slope=3.238564e-06 eV/(atom fs)
PASS CudaAware  ranks=1: NVE max_excursion=1.746927e-06 eV/atom slope=3.238564e-06 eV/(atom fs)
PASS CudaAware  ranks=2: NVE max_excursion=1.746927e-06 eV/atom slope=3.238564e-06 eV/(atom fs)
PASS CudaAware  ranks=4: NVE max_excursion=1.746927e-06 eV/atom slope=3.238564e-06 eV/(atom fs)
PASS: replicated-data MPI differential matrix ranks=[1, 2, 4] backends=['HostStaged', 'CudaAware']
```

该矩阵逐 case 检查 initial per-atom energy/force/9-virial、短轨迹、NVE 漂移、不同 rank 数
thermo header/列/segment/row 结构、rank-0-only 文件集合、启动记录、GPU UUID、center coverage
proof、逐步通信量，以及 cross-rank/cross-backend direct differential。

4-rank CudaAware 静态 case 的机器可读证据格式为：

```text
DMGMD_CENTER_PARTITION global_count=8 ranks=4 missing=0 overlapping=0 owned_output_coverage=complete nep_kernel_centers=replicated-full nep_N1_N2_shard_complete=false reason=remote-Fp-and-reverse-partial-dependencies
DMGMD_COMM step=1 backend=CudaAware collective_calls=8 mpi_input_bytes_global=1856 mpi_output_bytes_global=3008 device_to_host_bytes_global=0 host_to_device_bytes_global=0 output_download_bytes=1216
```

## 下一阶段门槛

本阶段继续禁止 ghost、halo 和原子迁移。若下一阶段实现真正 NEP 中心分片，必须先提供 `Fp`
和 directed partial 的 phase-level exchange，并在相同 differential matrix 下证明分片完整；
不得直接把 `NEP::N1/N2` 改为 owned range。
