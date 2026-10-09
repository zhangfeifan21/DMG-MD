# 多 GPU Benchmark 实测结果（2026-09-26）

类别：进度与实测备忘。结果来自用户提供的另一台服务器；本工作区只读取结果文件，未在本机重跑。

## 测试范围与来源

原始目录：[dmgmd-bench-docker-confirm-20260926-174639](../../dmgmd-bench-docker-confirm-20260926-174639)。汇总以 `summary.csv`、`summary.md`、`metadata.json`、`preflight.txt` 为准，逐项记录位于各 trial 子目录。此次完成 36 个汇总配置：3 个算例 × 3 个实现（DMG-MD HostStaged、DMG-MD CudaAware、GPUMD）× 4 个 rank 数；每项 5/5 次有效重复。DMG-MD 覆盖 1/2/4/8 rank、两种通信后端；GPUMD 覆盖 1/2/4/8 rank。

- 算例：carbon_200k（196,608 atoms）、carbon_1m（1,048,576 atoms）、water_400k（393,216 atoms）；均为 strong scaling。
- profile：500 步 warmup、1500 步计时、5 次重复、至少 20 秒；GPU 设备 0–7。
- 环境预检：Open MPI 5.0.10、UCX 1.18.1、`cuda_copy/cuda_ipc`，8-rank CudaAware probe 通过。
- DMG-MD binary SHA-256：`4e53a89a3d61d0c4d4037c7237b2ce2a6a33b22296435afc0a70a3dc8942b856`；GPUMD binary SHA-256：`06772432037e6f981d0ce4e1dda092a8eaff0a28b2d11f13d1a886b3f2081822`；GPUMD reference commit：`9d23496e41319b9e2af5221a7df6285387401d1e`。
- metadata 中 hostname 为容器 ID `483b5c9d6ec8`，不是宿主机身份；此目录未提供可核实的物理节点型号、CPU 型号/核心数或完整 Docker 启动记录。因此本报告不推断 CPU 资源，也不把容器 hostname 当服务器名称。

## 结果摘要

下表为 DMG-MD 相对自身 1-rank 同后端的 strong-scaling speedup；每个结果为五次有效重复的中位数口径。效率为 speedup/rank 数。

| 算例 | 后端 | 2 rank speedup | 4 rank speedup | 8 rank speedup | 8 rank 效率 | 8 rank ms/step |
|---|---|---:|---:|---:|---:|---:|
| carbon_200k | HostStaged | 1.90× | 3.49× | 6.47× | 0.81 | 48.55 |
| carbon_200k | CudaAware | 1.89× | 3.45× | 6.31× | 0.79 | 49.52 |
| carbon_1m | HostStaged | 2.13× | 4.24× | 7.66× | 0.96 | 250.46 |
| carbon_1m | CudaAware | 2.12× | 4.21× | 7.52× | 0.94 | 253.65 |
| water_400k | HostStaged | 1.71× | 3.18× | 5.39× | 0.67 | 26.00 |
| water_400k | CudaAware | 1.67× | 3.05× | 5.17× | 0.65 | 26.73 |

同一 rank 数下，DMG-MD 单 rank与 GPUMD 基线大致相当；rank 增加后，三组算例中 DMG-MD 的多 rank效率均高于 GPUMD。比如 8-rank 的 carbon_1m，DMG-MD 获得 7.52–7.66× 相对自身单 rank的加速，而 GPUMD 为 5.41×。water_400k 的 DMG-MD 8-rank 加速为 5.17–5.39×，GPUMD 为 3.37×。这些结论限于该单机八卡 strong-scaling 测试，不代表跨节点表现、其他工作负载或 CPU 使用特征。

HostStaged 与 CudaAware 在本批结果相近，HostStaged略快；这与“CudaAware 必然更快”的假设不符，后续重叠实验应将两者都作为基线。原始 `summary.csv` 的完整测量值、波动范围与所有中间 rank 结果保留在结果目录，不在本文重复维护。

## 复现信息与限制

metadata 记录命令以 `tests/benchmark/run_benchmark.py --profile standard` 为基础，指定三组算例、500 warmup、1500 steps、5 repeats、20 秒最短测量、devices 0–7 与 ranks 1/2/4/8。原始 argv 和二进制路径见 `metadata.json`。容器内结果已通过 MPI/CUDA 环境预检。此次没有从本机访问/复现另一台机器，也没有独立重验输出物理正确性；性能结论仅描述 benchmark harness 的 status=ok 结果。
