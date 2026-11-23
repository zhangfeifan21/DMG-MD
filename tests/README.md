# 测试目录总览

## 选择要验证的行为

| 任务 | 入口 |
| --- | --- |
| 检查解析、系综控制、空间所有权与域布局 | [C++ 单元测试](unit/) |
| 检查邻居关系和其他 CUDA 设备端计算 | [GPU 单元测试](gpu/) |
| 核对单卡输入、物理量、输出和重启是否与普通 GPUMD 一致 | [短程 golden](baseline/README.md) |
| 查多卡迁移、边界交换、通信后端和实际数据分区 | [MPI 验收](mpi/README.md) |
| 验证 NHC 或 BDP 恒温器的计算、采样和重启 | [恒温器验收](thermostats/README.md) |
| 验证 Berendsen/SCR 控压、压力、统计性质或局部变盒 | [NPT 验收](npt/README.md) |
| 检查长期守恒、Berendsen NVT 与跨进程数重启 | [长程验收](long_nve/README.md) |
| 对比 GPUMD、LAMMPS/MatPL 与 DMG-MD 同构型的能量、力和 virial | [跨引擎静态对照](lammps_reference/README.md) |
| 测量 NVE 耗时或定位开销 | [性能与诊断工具](benchmark/README.md) |

<a id="目录职责"></a>

## 测试怎样组织

[tests/CMakeLists.txt](CMakeLists.txt) 注册常规 CTest 项，产物在 `build/tests/`。你也可以直接执行独立 C++ 或 CUDA 测试程序。Python 驱动负责生成输入、启动 MPI 作业、比较结果和保存证据。部分驱动还会加载编译好的探针库。并行测试用词见[术语表](../GLOSSARY.md)。

O65 回放需要在根 CMake 增加 `-DDMGMD_ENABLE_O65_DIAGNOSTICS=ON`，产物是 `build/dmgmd-o65-replay`。此程序由诊断流程单独调用。

## 常规构建与 CTest

在仓库根目录执行。实验室维护环境由 `../env/md-mpi.sh` 加载。普通账号使用站点提供的等价环境，具体步骤见[部署手册](../docs/operations/deployment.md)。

```bash
source ../env/md-mpi.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
```

配置与构建都需要 CUDA Toolkit 和 MPI，即使只运行 CPU 测试。GPU 测试还需要当前作业可访问设备。专项 MPI 测试应先按脚本要求做[设备和通信预检](mpi/README.md)。单 rank 通过预检后，可以开始单卡对照。多 rank 验收按对应矩阵检查 GPU 绑定和通信，再运行数值比较。

CTest 覆盖已注册的测试。完整 MPI 矩阵、golden 差分、长程与性能检查分别使用上表的入口。提交验收结果时记录跳过项、实际运行的进程数和后端，并保留对应脚本要求的证据。NPT 的 2026-11-04 独立确认及 B0–B4/G-B 的通过范围见[数值验收记录](../docs/status/validation/npt-validation.md#npt-scr-confirmation-20261104)。
