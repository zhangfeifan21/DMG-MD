# DMG-MD

DMG-MD 是面向多节点、多 GPU 经典分子动力学的 runtime。当前仓库包含与锁定 GPUMD
reference 兼容的单 rank 路径，以及一 MPI rank 一张 GPU 的 replicated-data prototype。

当前版本直接读取 GPUMD 格式的 `model.xyz`、NEP/NEP-ZBL potential 和 `run.in`。GPUMD
的最小数值核心已在 `src/gpumd_compat/` 中复现（tokenizer、Box、GPU_Vector、邻居构建、
NEP loader 及 CUDA kernels，复制自锁定 commit `9d23496e`），构建与运行均不依赖
`../gpumd-reference`。MPI 原型在每个 rank 保留完整坐标/类型，只分片积分、thermo 和
authoritative per-atom output；空间域分解、ghost、halo、原子迁移尚未实现。

## 当前支持范围

- NEP4、NEP5 及对应的 NEP-ZBL potential；
- `potential`、`velocity`、`time_step`；
- `ensemble nve` 和 `ensemble nvt_ber`；
- `correct_velocity`；
- `dump_thermo`、`dump_xyz`、`dump_restart`；
- `run` 及多段 run。

`run.in` 会先完整解析为带文件和行号的 command IR，再开始执行。未知命令、已知但尚未支持
的命令和 ensemble subtype 都会立即失败，不会被静默忽略。

## 依赖

- CMake 3.24 或更高版本；
- 支持 C++17 的 host compiler；
- CUDA Toolkit 和支持的 NVIDIA GPU；
- Open MPI+UCX（含 CUDA support、UCX PML、`cuda_copy` 和 `cuda_ipc`）；项目使用 Open MPI
  `mpi-ext.h`/`MPIX_Query_cuda_support()`，不支持替换为 MPICH、MVAPICH 或其他 MPI。

构建与运行不依赖 `../gpumd-reference`：DMG-MD 需要的 GPUMD 最小子集（tokenizer、Box、
GPU_Vector、邻居构建、Potential、NEP/NEP-ZBL CUDA kernels）已在 `src/gpumd_compat/`
中复现，锁定来源 commit `9d23496e41319b9e2af5221a7df6285387401d1e`。该参考 checkout 仅
用于生成和比对 golden 基线（见下文 Golden differential），并保持只读。

仓库的上级 `env/md-mpi.sh` 是唯一受支持的工具链入口。CMake 会拒绝未从该脚本选择的
MPI，避免把旧 system MPI/UCX 混入构建。

## 构建与测试

```bash
source ../env/md-mpi.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
```

项目默认构建 CUDA 架构 `75;80;86;89;90`，可通过
`-DCMAKE_CUDA_ARCHITECTURES=<architecture>` 覆盖。

## 运行

多 rank 运行示例：

```bash
source ../env/md-mpi.sh
mpiexec -n <ranks> /absolute/path/to/dmg-md
```

默认通信后端是 `HostStaged`：CUDA device buffer → pinned host buffer → Open MPI/UCX →
pinned host buffer → CUDA device buffer。可选 `CudaAware` 必须显式请求，并通过启动时覆盖
`MPI_Allreduce(MPI_IN_PLACE)`、`MPI_Allgatherv`、`MPI_Gatherv`、`MPI_Bcast` 的
device-pointer 数值自检后才会启用：

```bash
DMGMD_COMM_BACKEND=CudaAware mpiexec -n 4 /absolute/path/to/dmg-md
```

启动先用 Open MPI `MPIX_Query_cuda_support()` 记录 capability；请求 `CudaAware`（或设置
`DMGMD_CUDA_AWARE_PROBE=1`）后还必须通过主动数值自检。只有 capability 为 supported 且自检
passed，生产 collective 才能接收 device pointer；否则回退 `HostStaged`。HostStaged 始终是
正确性默认路径。

每个 rank 启动时记录 Open MPI library version、Open MPI+UCX stack、hostname、world/local
rank、CUDA ordinal/UUID、capability、自检结果、实际 backend 和 ordinary single-device NEP
策略。默认每步的 collective buffer 字节数写到 rank 0 stdout；长期正确性测试可设置正整数
`DMGMD_COMM_LOG_INTERVAL` 做低频采样。物理链路字节数取决于 collective 算法，不伪装成
精确值。每个 run segment 和整个 replicated runtime 还会输出 rank 0 汇总的 `DMGMD_TIMING`，
包含各 rank wall time 的 min/mean/max 和按最慢 rank 计算的全局 atom-steps/s；它是诊断记录，
不是当前正确性 suite 的性能通过门槛。

发生异常时，出错 rank 会在 `MPI_Abort` 前写入并 flush 一条带 world/local rank、hostname 和
错误类别的 `DMGMD_ERROR` 到 stderr。异常路径不执行可能死锁的 MPI 日志汇聚；Open MPI/PRRTE
把远端 stderr 转发到 `mpirun` 启动端，由调用方统一捕获。

在同时包含 `run.in` 和 `model.xyz` 的工作目录执行：

```bash
/absolute/path/to/newmd/build/dmg-md
```

potential 文件路径按 `run.in` 中的 `potential` 命令解释。输出文件名、列顺序、默认值和单位
由 GPUMD golden tests 锁定。

## Golden differential

先验证原 GPUMD baseline，再比较 `dmg-md`：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py \
  --reference ../gpumd-reference/src/gpumd --device 0
python3 tests/baseline/run_baselines.py \
  --candidate ./build/dmg-md --device 0
```

MPI 1/2/4 rank 矩阵默认同时测试 HostStaged 与 CudaAware。脚本会在任何 MD case 之前验证
Open MPI/UCX 安装与链接、`cuda_copy/cuda_ipc`、GPU 数量/唯一绑定及 device-pointer
collectives；预检失败时不会启动数值测试：

```bash
python3 tests/mpi/run_mpi_differential.py \
  --candidate ./build/dmg-md --devices 0,1,2,3
```

100/10000/100000-step 长程正确性 suite 独立运行，不把长轨迹混入短程 committed golden，也
不采集性能数据。除 NVE 外，它还比较确定性 NVT 的温度统计、时间平均 RDF 和 MSD；
nightly/release 默认覆盖 HostStaged 与 CudaAware，并包含 NEP5、typewise cutoff、flexible ZBL
和 typewise ZBL cutoff 的静态/短轨迹分支。smoke 示例：

```bash
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build/dmg-md --devices 0 --profile smoke \
  --report /tmp/dmgmd-long-nve-smoke.json
```

完整方法、4096/12288/5000 原子 fixture、十初态 release 矩阵、NVT统计、双向构型回放和跨
rank restart 见 [长程正确性测试说明](tests/long_nve/README.md)。

实现协议、中心分片完整性结论和逐步通信量公式见
[replicated-mpi.md](docs/replicated-mpi.md)。

## VS Code / clangd

测试源码依赖 CMake target 提供的 C++17、include 路径和 compile definitions。请先运行一次
CMake configure，并让编辑器读取：

```text
build/compile_commands.json
```

使用 Microsoft C/C++ extension 时，可将 `C_Cpp.default.compileCommands` 指向
`${workspaceFolder}/build/compile_commands.json`；使用 clangd 时可设置
`--compile-commands-dir=build`。请把包含本 README 和顶层 `CMakeLists.txt` 的目录作为 VS Code
workspace root；如果打开的是它的父目录，compile database 路径需要相应写成
`newmd/build/compile_commands.json`。

`tests/model_parser_tests.cpp` 中的 `DMGMD_SOURCE_DIR` 是
`dmgmd_model_parser_tests` target 专属的 compile definition。如果编辑器没有加载上述 compile
database，它会错误地认为该宏未定义，并且可能找不到 `dmgmd/model.hpp`；CMake 构建本身不会
出现这个问题。

## 文档

- [当前进度与验证结果](docs/progress.md)
- [架构决策](docs/decisions.md)
- [当前数据布局](docs/data-layout.md)
- [输入兼容矩阵](docs/compatibility-matrix.md)
- [多节点 rank I/O 隔离待审批方案](docs/multi-node-io-plan.md)
- [Golden test 说明](tests/baseline/README.md)
- [长程 NVE/NVT 正确性测试](tests/long_nve/README.md)
