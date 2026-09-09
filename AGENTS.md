# AGENTS.md

## 项目定位

本仓库开发 **DMG-MD**（Distributed Multi-GPU Molecular Dynamics Runtime）。目标是一 MPI rank 对应一张 GPU 的多节点、多 GPU 经典分子动力学 runtime。

面向用户的兼容目标是：继续直接读取 GPUMD 的 `model.xyz`、NEP/NEP-ZBL potential 文件和 `run.in`。对已经声明支持的功能，输入语法、默认值、单位、校验、输出文件名、输出列及物理语义必须与锁定的 GPUMD 参考版本一致；对未支持命令必须识别并报出明确的 `unsupported` 错误，不得静默忽略。

当前产品范围仅包括：

- 普通经典 MD，单 bead；
- NEP/NEP-ZBL；
- NVE 和一种经验证的基础 NVT；
- owned/ghost 域分解、halo 通信、全局归约和兼容输出所需的最小功能。

当前不要求 PIMD、MC、phonon、minimize、deposition、PLUMED、长程静电、其他势函数及完整 measurement 系统。

默认使用中文报告结论，即使任务说明使用英文。

## 当前阶段：replicated-data MPI prototype

当前已批准的实现切口是 Open MPI+UCX 的 replicated-data 原型：

- 一 MPI rank 对应一张 GPU；每个 rank 暂时保留完整坐标和类型；
- 中心原子按连续全局下标分片，积分、thermo 归约和输出只承认 owned range；
- 每步允许 Open MPI collective，默认走 HostStaged，CudaAware 必须先通过 Open MPI capability
  query 和运行时数值自检；
- 所有用户可见文件只由 rank 0 写；
- 不实现 ghost、halo 或原子迁移，也不进行大规模源码移动或架构重写；
- 不得使原 GPUMD 和 DMG-MD 单 rank 行为回归；
- 无法从代码和测试证明的行为仍标记为 `UNKNOWN`，不得推测为兼容。

本阶段的实现、所有权、通信量和验证契约位于 `docs/replicated-mpi.md`。

当前设计与审计的权威交付物位于：

- `docs/critical-path.md`
- `docs/kernel-inventory.md`
- `docs/data-layout.md`
- `docs/minimal-source-manifest.md`
- `docs/compatibility-matrix.md`
- `docs/mpi-risks.md`
- `docs/golden-test-plan.md`
- `docs/open-questions.md`

## GPUMD 复现边界（强制）

DMG-MD 曾经直接编译 `../gpumd-reference` 内的源文件（tokenizer、Box、neighbor、
Potential、NEP kernels）。该模式已于 2026-09-09 废除。当前规则：

- **禁止直接调用 GPUMD 代码**。DMG-MD 的任何源文件不得 `#include` 指向
  `../gpumd-reference` 的路径，CMake 不得编译或链接该目录中的任何文件；
  运行时也不得以 dlopen、子进程等方式调用 GPUMD 可执行文件或库。
- 所需的 GPUMD 最小子集**只能在 newmd 仓库内复现**：位于
  `src/gpumd_compat/`（命名空间 `gpumd_compat`），复制自锁定 commit
  `9d23496e41319b9e2af5221a7df6285387401d1e`，文件头注明 Origin file 与裁剪说明。
- `src/gpumd_compat/` 的数值（浮点表达式、内存布局、kernel launch 参数、累加顺序）
  与参考实现保持一致；对其任何修改都必须连同 golden 基线（`tests/baseline`、
  `tests/long_nve`）一起重新验证，不得"顺手清理"。
- 新增需要 GPUMD 已验证实现的代码时，先在 `src/gpumd_compat/` 中复现并加注释
  （用途 + GPUMD 对应文件/符号），再接入 DMG-MD runtime；不得回到直接引用
  `../gpumd-reference` 的做法。
- `../gpumd-reference` 只作为只读对照与 golden 基线生成源（`run_baselines.py
  --reference` / `--update-goldens`），不参与 DMG-MD 构建与运行。审计结论仍须记录
  参考文件、类、函数或 CUDA kernel；使用 `rg` 跟踪实际调用和符号。

## 不可破坏的产品约束

- 不重新实现 NEP 数学公式。NEP 数学子集由 `src/gpumd_compat/` 的复现内核提供
  （复制自 GPUMD 锁定 commit），在其上只做小范围参数化；重写公式前必须先建立
  golden test 并获得维护者确认。
- 一 MPI rank 只控制一张 GPU。当前 replicated-data 原型允许每 rank 持有完整输入坐标和类型，但积分、thermo 与输出写权限只属于 owned atoms；进入 domain decomposition 后才收敛为 owned、ghost 和明确通信工作区。
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
- Open MPI+UCX 是 replicated-data runtime 的固定依赖；允许使用 Open MPI 的 `mpi-ext.h`/
  `MPIX_*`，不再维护 MPICH、MVAPICH 或其他 MPI 实现兼容性；
- Python 只用于测试、验证和分析脚本，核心 runtime 不依赖 Python；
- GPU 常驻数据只在初始化、通信、输出或验证需要时传回主机。
- Node.js、npm、npx 由当前用户通过用户级 fnm 安装和管理，不属于项目本地依赖；fnm 根目录为
  `~/.local/share/fnm`。检查 Node.js 环境时，必须先确认 fnm 及其 shell 初始化；在未加载用户
  `.bashrc` 的非交互 shell 中 `command -v node` 为空，不得据此判断 Node.js 未安装。应优先检查
  `fnm --version`、`fnm list`，并在加载 fnm 环境后记录 `node --version`、`npm --version` 和
  `npx --version`。

## 构建与测试

从本仓库根目录先加载唯一受支持的 MPI/CUDA 环境，再配置：

    source ../env/md-mpi.sh
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release

构建：

    cmake --build build -j

测试：

    ctest --test-dir build --output-on-failure

修改生产代码前后应检查 `git status` 和 `git diff`，保留用户已有修改。不得使用破坏性 Git 操作，不得提交或推送，除非维护者明确要求。

### GPU 测试环境

- 受限沙箱通常无法访问 NVIDIA driver/GPU；沙箱内的 `nvidia-smi` 失败、`cudaErrorNoDevice` 或 CUDA 初始化失败，不能单独作为宿主机无 GPU 或实现失败的结论。
- `../env/md-mpi.sh` 是构建和测试唯一受支持的环境入口。每个新的 shell 都必须先 source；不得
  静默回退到 system MPI/UCX、旧的临时 UCX 或只靠额外命令行变量修补环境。
- 脚本当前固定 Open MPI+UCX PML、各自的 MCA component path，并排除不能处理本项目 CUDA
  buffer 的 HCOLL。修改 Open MPI、UCX、CUDA 或脚本后，必须先运行
  `python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3`；该门槛检查
  executable/linkage、Open MPI CUDA support、UCX `cuda_copy/cuda_ipc`、GPU 唯一绑定和实际
  device-pointer collectives。门槛未通过时不得启动 numerical differential matrix。
- CUDA 数值验收应先在沙箱内完成可做的静态检查和 CPU 测试，再在获得授权后使用 `sandbox_permissions=require_escalated` 到沙箱外 source 同一脚本，依次运行环境预检、单 rank golden 和 MPI GPU 测试。
- 不得因为沙箱隔离而反复把 GPU 测试仅记录为“待验证”；应保存沙箱外的精确命令、GPU/MPI 环境和结果。若沙箱外仍失败，再按真实的 driver、GPU、MPI 或代码错误诊断。
- 沙箱外测试不放宽一 rank 一 GPU、CudaAware 自检和默认 HostStaged 等约束。

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
