# 测试目录总览

本页说明测试源码、执行入口和验收范围。各子目录 README 记录各自的运行方法与证据要求；普通测试的 CMake target 与 CTest 注册集中在 [`tests/CMakeLists.txt`](CMakeLists.txt)。

## 目录职责

- [`unit/`](unit/)：小型 C++ 单元测试，验证解析、系综控制、空间所有权与域布局等基础逻辑。
- [`gpu/`](gpu/)：需要 CUDA 设备的 GPU 测试，例如邻居关系与设备端计算路径。
- [`mpi/`](mpi/README.md)：MPI 生命周期、halo、通信后端和集成验证脚本；需按具体测试要求提供 MPI ranks 与 GPU。
- [`thermostats/`](thermostats/README.md)：NHC/BDP 等恒温器及系综的定向验收。
- [`benchmark/`](benchmark/README.md)：基准测试、性能诊断、采样和优化实验工具。
- [`baseline/`](baseline/README.md)：单 GPU golden 基线与差分验证。
- [`long_nve/`](long_nve/README.md)：长程 NVE/NVT、restart 等长时间正确性验收。
- [`npt/`](npt/README.md)：NPT预注册输入、统计器与Berendsen/SCR/GPU集成验收；2026-11-04独立确认及B0–B4/G-B在所列范围PASS，见[最终结果](../docs/status/validation/npt-validation.md#npt-scr-confirmation-20261104)。
- [`lammps_reference/`](lammps_reference/README.md)：与锁定 LAMMPS reference 的输入、静态物理量和 smoke 对照。

Python 脚本负责准备 fixture、调度 MPI/GPU 作业、比较输出及整理证据。独立 C++/CUDA 单测编译后可由 CTest 调用，也可直接运行；共享 probe 编译为库供测试驱动使用，MPI 集成测试由 Python 驱动完成环境门槛检查后启动。普通测试的 target 仍由 `tests/CMakeLists.txt` 集中定义，产物位于 `build/tests/`。O65 replay 是例外：根 CMake 在显式设置 `-DDMGMD_ENABLE_O65_DIAGNOSTICS=ON` 时构建 `build/dmgmd-o65-replay`，它不属于普通 CTest。

## 常规构建与 CTest

在仓库根目录使用项目 MPI/CUDA 环境入口：

```bash
source ../env/md-mpi.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
```

配置和构建仍要求可用的 CUDA 编译器/Toolkit 与 MPI 安装，即使当前只运行 CPU-only 测试。含 GPU 的测试要求可见且可用的 GPU；MPI 集成测试还要求其指定的 rank 数、MPI 启动器及每 rank GPU 条件满足。可用四卡环境按 MPI README 执行 GPU 环境预检：`python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4`。具体门槛以各子目录说明和脚本预检为准。

CTest 只覆盖已注册的测试集合，不等于完整 MPI 矩阵、golden differential、长程正确性或性能验收。任何因环境、设备或前置条件跳过的测试都应记录为 skip/未执行，不能计作通过；相关验收应使用对应子目录脚本并保留其要求的原始证据。
