# DMG-MD

DMG-MD 是面向 NVIDIA GPU 的经典分子动力学软件。它读取 GPUMD 格式的 `run.in`、`model.xyz` 和势文件，用 MPI 将同一体系的计算分给多张 GPU。每个 MPI 进程对应一张 GPU。

## 当前能运行什么

当前已实现 NEP4/5 和 NEP-ZBL 势，支持 NVE、三种 NVT，以及各向同性正交全周期盒的 Berendsen/SCR NPT。LJ 与 EAM/alloy 尚待开发，V1.0 尚未发布。具体输入和限制见[兼容矩阵](docs/standards/compatibility-matrix.md)。

已有验证来自单节点。NPT 默认使用全量复制模式，局部域分解需显式开启，并从日志确认实际路径。碳、水和金属的 SCR 科学验证及相对 GPUMD-WPE 单卡的正式提速验收完成后，才开放实验室试用。[当前状态与证据](docs/status/current.md)

## 运行第一个作业

先按[原生构建与运行指南](docs/operations/deployment.md)准备环境、编译、自检，再运行附带的单卡碳示例。已有 GPUMD 输入的用户，应先查兼容矩阵中的命令、系综和盒限制。

准备尝试多卡时，先读[多卡实现导读](docs/reference/multi-gpu-implementation.md)，了解原子怎样分区，以及如何确认实际使用了局部求力。比较运行效率时，按[性能方法与结果](docs/status/performance/npt-benchmarks.md)区分迁移加速比、自身强扩展和用卡成本。

## 接手开发

[文档索引](docs/README.md)按运行、实现、验证和计划提供入口。先读[术语表](GLOSSARY.md)和[架构决策](docs/standards/architecture-decisions.md)，再沿 [MPI 运行时](docs/standards/mpi-runtime.md)与[数据布局](docs/standards/data-layout.md)追踪代码。测试入口见[测试目录总览](tests/README.md)，近期任务见[V1.0 开发进度](docs/plans/dmg-md-v1.0-progress.md)。
