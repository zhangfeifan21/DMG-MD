# AGENTS.md

## 项目定位

本仓库开发 **DMG-MD**（Distributed Multi-GPU Molecular Dynamics Runtime）。目标是一 MPI rank 对应一张 GPU 的多节点、多 GPU 经典分子动力学 runtime。

面向用户的兼容目标是：继续直接读取 GPUMD 的 `model.xyz`、NEP/NEP-ZBL potential 文件和 `run.in`。对已经声明支持的功能，输入语法、默认值、单位、校验、输出文件名、输出列及物理语义必须与锁定的 GPUMD 参考版本一致；对未支持命令必须识别并报出明确的 `unsupported` 错误，不得静默忽略。

当前第一阶段范围仅包括：

- 普通经典 MD，单 bead；
- NEP/NEP-ZBL；
- NVE 和一种经验证的基础 NVT；
- owned/ghost 域分解、halo 通信、全局归约和兼容输出所需的最小功能。

当前不要求 PIMD、MC、phonon、minimize、deposition、PLUMED、长程静电、其他势函数及完整 measurement 系统。

默认使用中文报告结论，即使任务说明使用英文。

## 当前阶段：审计与设计

在维护者明确批准实现切口前：

- 不实现 MPI；
- 不改变现有生产代码行为；
- 不进行大规模源码移动或架构重写；
- 可以在 `docs/` 中补充审计、设计和验证文档；
- 必须把无法从代码证明的行为标记为 `UNKNOWN`，不得推测为兼容。

当前审计的权威交付物位于：

- `docs/critical-path.md`
- `docs/kernel-inventory.md`
- `docs/data-layout.md`
- `docs/minimal-source-manifest.md`
- `docs/compatibility-matrix.md`
- `docs/mpi-risks.md`
- `docs/golden-test-plan.md`
- `docs/open-questions.md`

## GPUMD 参考仓库

GPUMD 位于：

    ../gpumd-reference

该目录是只读参考，禁止修改。审计结论必须记录参考 commit，并引用具体文件、类、函数或 CUDA kernel；使用 `rg` 跟踪实际调用和符号，不能只根据文件名推测行为。

未经维护者明确许可，不把 GPUMD 源文件整体复制到本仓库。允许研究其算法、数据依赖、文件格式、数值结果和测试基线。

## 不可破坏的产品约束

- 不重新实现 NEP 数学公式。优先复用或小范围重构 GPUMD 已验证的 CUDA 内核。
- 一 MPI rank 只控制一张 GPU，只持有 owned atoms、ghost atoms 和明确的通信工作区。
- owned atoms 与 ghost atoms 必须有不同的生命周期和写权限；ghost 不得被积分、重复计入 thermo 或直接输出。
- 必须有跨迁移保持稳定的全局原子 ID；本地数组下标不能承担持久身份。
- 不假设 NEP halo 等于某一个 cutoff。位置、descriptor/导数、directed partial force 和最终 force 的每一层依赖都必须有 kernel 证据。
- 任何使用 Newton scatter 并写 ghost partial force 的路径都必须定义 reverse force exchange；采用中心原子 gather 的路径必须定义中间 descriptor/partial-force 的 forward exchange。
- 每原子能量和 virial 的所有权必须唯一，温度、总能量和 stress 只从 owned atoms 归约。
- PBC、triclinic box、单位、精度、力号和 virial 分量顺序在更改前必须建立 GPUMD golden test。
- rank 0 I/O 必须恢复 GPUMD 的稳定原子顺序、文件名、列顺序和格式；restart 必须支持跨不同 rank 数恢复。
- `NEP_MULTIGPU` 是单进程、单节点实现，依赖 GPU 0 的全局体系。不得把它直接实例化为每个 MPI rank 的并行层。

## 建议的数据面边界

后续实现应明确区分：

- `global_count`：全局原子数，只用于元数据和归约；
- `owned_count`：当前 rank 积分、拥有能量/virial、参与输出的原子数；
- `local_count = owned_count + ghost_count`：本 rank 可寻址坐标与邻域数据；
- `global_id[local]`、owner rank、ghost image/shift；
- position/type/mass/group 等持久或 halo 字段；
- NEP descriptor 导数 `Fp`、angular sums、directed partial force 等分阶段通信字段；
- 邻居表中心域与邻居可寻址域。

所有 SoA buffer 都必须显式记录 stride；不能把 GPUMD 的全局 `N` 隐式替换成本地 `N` 后假定索引仍正确。

## 语言与工具链

- C++17、CUDA、CMake；
- MPI 是目标 runtime 的必需依赖，但当前审计阶段不添加；
- Python 只用于测试、验证和分析脚本，核心 runtime 不依赖 Python；
- GPU 常驻数据只在初始化、通信、输出或验证需要时传回主机。

## 构建与测试

配置：

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release

构建：

    cmake --build build -j

测试：

    ctest --test-dir build --output-on-failure

修改生产代码前后应检查 `git status` 和 `git diff`，保留用户已有修改。不得使用破坏性 Git 操作，不得提交或推送，除非维护者明确要求。

## 数值验证规则

GPUMD 是数值和兼容性参考。验证至少分四层：

1. 锁定 GPUMD 单 GPU基线；
2. DMG-MD 单 rank；
3. MPI 复制数据原型；
4. MPI domain decomposition。

比较邻居关系、每原子能量/力/virial、总 thermo、短轨迹、NVE 漂移、输出字节结构和 restart。不得为了让测试通过而随意放宽容差；容差必须来自重复的基线实验，并区分逐字段精确、确定性数值容差和随机/混沌轨迹的统计比较。

## Agent 工作方式

非平凡任务按以下顺序进行：

1. 检查两个仓库的状态和已有用户修改；
2. 阅读相关说明、构建脚本和测试；
3. 用 `rg` 从入口沿调用和 buffer 读写追踪；
4. 给出文件、符号、执行位置、数据所有权和 MPI 影响；
5. 做最小且在授权范围内的修改；
6. 构建或执行与风险相称的测试；
7. 检查 diff，确认未修改 `../gpumd-reference` 和无关文件。

若任务会改变 NEP 数学、数据布局、精度、原子所有权、通信半径或兼容性承诺，应先完成证据和 golden test 设计，再请求维护者确认实现方向。
