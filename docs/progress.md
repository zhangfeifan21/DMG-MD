# DMG-MD 实现进度

更新日期：2026-09-09。

## 当前结论

新的 `dmg-md` executable 已完成 single-rank 阶段，并实现 replicated-data MPI prototype。
Open MPI+UCX 是固定运行栈，默认通信后端仍为 HostStaged，CudaAware 是已通过相同数值矩阵的
可选后端。当前明确不包含空间域分解、ghost/halo、原子迁移或真正的 phase-level NEP 中心
并行。

## 已完成

- CMake 生成 `dmg-md`，拒绝未由 `../env/md-mpi.sh` 选择的 MPI。构建不依赖
  `../gpumd-reference`：GPUMD 最小子集已在 `src/gpumd_compat/` 复现（见下）。
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
  rank-0 创建的临时目录并指向 `/dev/null`。该实现只在单节点验证，跨节点依赖共享 `/tmp`
  是已确认风险；整改方案已形成但等待审批，尚未修改 runtime。
- 启动 stdout 记录 Open MPI implementation/version、Open MPI+UCX stack、hostname、
  world/local rank、CUDA device/UUID、capability、自检结果、backend 和 center coverage proof；
  默认每步记录 collective buffer 通信量，长测可按固定步数间隔采样。
- GPUMD 最小子集（tokenizer、Box、GPU_Vector、neighbor、Potential、NEP loader、
  NEP/NEP-ZBL CUDA kernels）在 `src/gpumd_compat/` 复现（`namespace gpumd_compat`，
  来源 commit `9d23496e`，文件头含 Origin file 与裁剪说明）；newmd 中没有第二份 NEP
  数学或参数布局，构建产物也不含任何 gpumd-reference 编译输入。
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
- 新增长程正确性 suite：4096-atom C、12288-atom 水和 5000-atom BaTiO3/ZBL，十组哈希锁定
  的显式初态，100/10000/100000-step profiles，真实 `E(0)` 的长期守恒指标、确定性 NVT
  温度/RDF/MSD统计、短程逐帧比较、GPUMD↔DMG-MD 双向静态构型回放和跨 rank restart。
  release/nightly 默认覆盖双通信后端，并含四个额外 potential 兼容分支；该 suite 不采集性能数据。
- `DMGMD_COMM_LOG_INTERVAL` 可为长测设置正整数采样周期；默认仍为 1，原 MPI differential
  继续检查每一步通信记录。

## 规范环境验证

本仓库、`gpumd-reference` 与 `env` 是同级目录。从本仓库根目录执行：

```text
source ../env/md-mpi.sh
python3 tests/mpi/check_environment.py \
  --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4
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
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
ctest --test-dir build --output-on-failure
4/4 tests passed
```

CMake 实际选择 `/data/home/zhangyifei/software/openmpi-cuda` 和
`/data/home/zhangyifei/software/ucx-cuda`，并成功从 CUDA translation unit 编译 Open MPI
`mpi-ext.h`/`MPIX_Query_cuda_support()`。第四项为 `dmgmd.long_nve_analysis`，在 CPU 上检查
30 个生成模型哈希、原子数、显式速度总动量、回放转换、真实初始能量指标和统计门槛。

## Golden differential 结果

锁定 GPUMD baseline 与 DMG-MD single-rank 均在规范环境下复跑：

```text
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py --reference ../gpumd-reference/src/gpumd --device 0
PASS: all 4 baseline cases match for pinned GPUMD reference
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
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
  --candidate ./build/dmg-md --devices 0,1,2,3 --timeout 600
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

## 长程正确性 suite 状态

实现入口和完整合同为 `tests/long_nve/run_long_nve.py`、`tests/long_nve/manifest.json` 与
`tests/long_nve/README.md`。2026-09-04 在相同 Open MPI+UCX/RTX 4090 环境完成以下 smoke：

```text
PASS long-NVE profile=smoke cases=['carbon_crystal'] seeds=[0] ranks=[1, 2] backends=['HostStaged']
PASS long-NVE profile=smoke cases=['dense_water', 'batio3_zbl'] seeds=[0] ranks=[1] backends=['HostStaged']
PASS long-NVE profile=smoke cases=['carbon_crystal'] seeds=[0] ranks=[1, 2] backends=['CudaAware']
```

这些命令都先通过实际 device collective 环境预检。三个 fixture 的静态、短程、100-step
长期指标、双向构型回放和 50+50-step restart 均通过；C case 同时完成 1↔2 rank restart。
三体系合并观察到的逐原子 energy/force/virial 最大差均为 0，位置最大差
`7.105e-15 Å`，velocity 最大差 `2.776e-17 Å/fs`。

10 万步的代表性 release 切片也已实际通过：4096-atom C、seed 0、1 rank、
HostStaged，仅运行 `long` section。候选程序与 GPUMD 的能量统计相同：每原子最大
能量偏移 `3.091e-6 eV`、去趋势 RMS `1.131e-7 eV`、漂移斜率
`7.002e-13 eV/(atom fs)`，最终元素对距离直方图 L1 差为 0。

10000-step nightly 和完整的十初态、三体系、1/2/4/8-rank、100000-step release 矩阵尚未
执行，因此当前不宣称完整长期门槛已通过。当前也没有性能基准；
replicated-full NEP 阶段禁止从任何正确性作业的 wall time 推导加速或 scaling 结论。

4-rank CudaAware 静态 case 的机器可读证据格式为：

```text
DMGMD_CENTER_PARTITION global_count=8 ranks=4 missing=0 overlapping=0 owned_output_coverage=complete nep_kernel_centers=replicated-full nep_N1_N2_shard_complete=false reason=remote-Fp-and-reverse-partial-dependencies
DMGMD_COMM step=1 backend=CudaAware collective_calls=8 mpi_input_bytes_global=1856 mpi_output_bytes_global=3008 device_to_host_bytes_global=0 host_to_device_bytes_global=0 output_download_bytes=1216
```

## 下一阶段门槛

本阶段继续禁止 ghost、halo 和原子迁移。若下一阶段实现真正 NEP 中心分片，必须先提供 `Fp`
和 directed partial 的 phase-level exchange，并在相同 differential matrix 下证明分片完整；
不得直接把 `NEP::N1/N2` 改为 owned range。

## 2026-09-09：GPUMD 依赖改为仓库内复现

- 新增 `src/gpumd_compat/`（15 个文件）：从锁定 commit `9d23496e` 复制并复现
  `utilities/common.cuh`、`gpu_macro.cuh`、`error.cu/.cuh`、`gpu_vector.cuh`、
  `model/box.cu/.cuh`、`force/neighbor.cu/.cuh`、`force/potential.cu/.cuh`、
  `utilities/nep_utilities.cuh`、`force/nep.cu/.cuh`、`force/nep_small_box.cuh`。
  浮点表达式、布局、launch 参数与累加顺序未改动。
- 裁剪（每处有 `NOTE(dmg-md)` 注释）：DFTD3（run.in `dftd3` 关键字本就被拒）、
  temperature/active-learning kernel 重载（`compute(temperature)` 家族）、ILP/SW/stream
  邻居变体、双精度 many-body gather、`Group`/`my_fopen` 等不可达符号。
- `CMakeLists.txt`：删除 `GPUMD_SOURCE_DIR`/commit 校验与 `gpumd_text_core`/
  `gpumd_nep_core`，改为静态库 `gpumd_compat`；`error.cu` 仍按 C++ 编译（parser 单测
  CPU-only），库用整程序 CUDA 编译与参考 GPUMD 一致。
- newmd 源码 include 改为 `gpumd_compat/*.cuh`：`runtime.cu`（Box/GPU_Vector/NEP）、
  `model_parser.cpp`、`run_parser.cpp`（get_tokens、单位常量）。
- 验证（RTX 4090 x4，Open MPI 5.0.10 + UCX 1.22.0）：
  - `ctest` 4/4 通过；
  - `tests/baseline/run_baselines.py --candidate` 4/4 案例通过，force/energy/virial
    最大相对误差 ~2e-16（浮点噪声级），覆盖 small-box（8 A 碳盒）、large-box
    （24 A 碳/水/BaTiO3-ZBL）两条内核路径与 ZBL；
  - `tests/mpi/run_mpi_differential.py --devices 0,1,2,3`：ranks 1/2/4 x
    HostStaged/CudaAware 六组 NVE 漂移逐位一致；
  - `tests/long_nve/run_long_nve.py --profile smoke` 通过；
  - `build/compile_commands.json` 不含任何 gpumd-reference 路径。
- 规则更新：`AGENTS.md` 明确禁止 include/编译/链接/运行 gpumd-reference 代码；
  新需求一律在 `src/gpumd_compat/` 复现并加注释（决策 D-001 修订、新增 D-010）。

## 2026-09-09：审计问题 3/4/5/6 整改

- 验证矩阵补齐 release/nightly 的 HostStaged+CudaAware 默认组合，以及 NEP5、typewise
  radial/angular cutoff、flexible ZBL、typewise ZBL cutoff 四个此前缺失的 potential 分支。
  变换后的完整 potential 均锁定 SHA-256；compatibility case 执行静态和短轨迹严格差分。
- 新增确定性 `nvt_ber` 平衡/采样段，统计温度 mean/std/RMSE、时间平均 partial RDF，以及
  基于 `unwrapped_position` 的 MSD mean/final/max/slope。温度和 MSD 对 GPUMD 的多初态
  median/q95 做双侧等价检查，RDF 使用预先固定的逐 bin 门槛。
- 删除 `Potential` 中不可达的 temperature-dependent 空 `compute()`；`NEP` 的析构、普通
  `compute()` 和 neighbor getters 显式 `override`。全新 Release 构建中原 partial-overload
  NVCC warning 消失，NEP 数学和 kernel 未修改。
- 修正 `compatibility-matrix.md` 的“尚未实现”陈述，支持项改为当前 `SUPPORTED`，并把实现与
  锁定参考源码证据分开描述。
- 多节点 I/O 隔离只提交 [待审批方案](./multi-node-io-plan.md)，未改 `src/runtime.cu`。
- 验证：全新 `/tmp` Release build 的 CTest 4/4；committed golden 4/4；1/2/4 rank ×
  HostStaged/CudaAware 短矩阵通过；扩展 smoke 的 3 个物理 case 与 4 个 compatibility case
  全部通过，NVT 的 GPUMD↔DMG-MD 温度/MSD统计一致、时间平均 RDF L1 为 0；另对 C 体系
  实跑 1/2/4 rank × HostStaged/CudaAware 的 NVT 统计矩阵并通过。
- 完整十初态、1/2/4/8 rank、双后端、100000-step release **已定义但本次未执行**；不能据
  smoke 结果宣称完整 release 门槛通过。
- `build-openmpi-ucx/` 是 34 MB 的 ignored 可重建目录，且混有已移除 target 的旧静态库；在
  全新构建和上述验证通过后已清理。规范构建目录统一为 `build/`。
