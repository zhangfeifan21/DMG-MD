# AGENTS.md

## 项目定位

本仓库开发 DMG-MD（Distributed Multi-GPU Molecular Dynamics Runtime），目标是支持多节点、多 GPU 的经典分子动力学。每个 MPI rank 只控制一张 GPU，默认用中文汇报。

## 开始任务前

先读[当前状态](docs/status/current.md)和[开发进度](docs/plans/dmg-md-v1.0-progress.md)，核对当前实现、已有证据和待办。正式版范围见[开发与发布验收](docs/plans/dmg-md-v1.0-development-workflow.md)。当前工作仍包括 V1.0 开发，旧 NEP/NVE/基础 NVT 原型不限制后续目标；计划能力也不能写成已支持。

兼容目标是直接读取 GPUMD 的 `model.xyz`、势文件和 `run.in`。已声明支持的功能，其语法、默认值、单位、校验、输出文件名、列和物理语义，以[兼容矩阵](docs/standards/compatibility-matrix.md)及对应数值基线为准。有意偏离须在标准中记录并验证，例如 BDP 的固定默认 seed。未支持的命令必须报明确的 `unsupported` 错误，不能静默忽略。

## V1.0 开发顺序

G-A、G-B 和实验局部 NPT 的 DN0–DN4 已在各自冻结范围通过。近期优先生产分派以及公开碳、水、FCC 铜的单节点 NPT。三种 SCR 各做科学验收，Berendsen 单列为预平衡。性能按三项对标的报告完整性验收，不设最低加速比。C0–C3/C5 和 D1–D5 仍待完成。

V1.0 的势范围为 NEP4/5、NEP-ZBL、LJ 和 EAM/alloy，目前只有 NEP 已实现。已确定的生产规则要求：单卡可使用全系统实现，多卡必须局部求力；缺少局部实现的组合只开放单卡，全量复制保留显式测试入口。当前程序仍可能启动回退，这项生产规则尚待实现。[分派决策](docs/standards/architecture-decisions.md#production-domain-routing)

## 局部 NPT 实验开发（2026-11-16 完成 DN4）

`DMGMD_DOMAIN_NPT=off|rebuild|affine` 默认 `off`。满足几何和组合限制的 NEP 作业，可显式选择强制重建或形变缓存复用。段首求力、旧力使用次序、根进程随机状态和边界数据准备见[局部 NPT 标准](docs/standards/domain-npt.md)。实验验收不扩大原 V1.0 P0 或 216 原子 SCR 的证据范围。

大盒 SCR 统计只支持单节点、5,760 原子 NEP4-C、300 K、0 GPa 和各向同性正交全周期盒。基准步长覆盖 P1/HostStaged/off，以及 P2/P4 两后端各自的 off、rebuild、affine。半步长仅覆盖 P1/HostStaged/off、P2/HostStaged/affine 和 P4/CudaAware/affine。[DN 最终验证](docs/status/validation/domain-npt-validation.md#dn4-final-20261116)

DN5 的性能和默认采用仍待验收，跨节点仍待实测。

<a id="当前并行基线全量复制模式局部域分解模式"></a>
## 当前并行基线：全量复制模式/局部域分解模式

`run_replicated` 是模式调度器。NEP 参数只解析一次，选择路径后才分配工作区。正式术语见[术语表](GLOSSARY.md)，机器字段保持原文。

| 模式 | 当前选择条件与数据职责 |
| --- | --- |
| 全量复制模式（replicated-full） | P=1 固定使用；满足基础盒约束、但不满足局部资格的 P>1 输入也会回退。每 rank 持有全部 N 个槽位，积分、热力学贡献和输出仍按空间所有权分工。迁移是稳定全局 ID 的逻辑交接，P1 与单 rank 基线逐字节一致 |
| 局部域分解模式（local-domain） | P>1、正交全周期、满足 NEP 大盒条件及 `slab_width >= d_coord`。设备槽位依次为负责原子、依赖副本、仅坐标副本，`local_count` 是统一数组跨度。负责原子按 gid 排序，边界副本按 `(source face, source rank, global_id)` 排序，保留单 rank 和全量复制路径 |

两跳位置边界按内核实际使用路径证明：`d_dep = max R_force + skin`，`d_coord = max(R_force+R_dep) + 2*skin`。无法证明上界时拒绝局部路径。HostStaged 和 CudaAware 使用独立方向 tag，CudaAware 先通过设备缓冲点对点自检。力中心为 `[0, owned)`，依赖中心为 `[0, owned+dep)`，候选为 `[0, local_count)`。NEP 邻居行按全局 ID 排序。

普通分派或实验局部资格检查后，rank 0 输出 `DMGMD_DOMAIN mode=m2a|m1-fallback ...`。默认 NPT 则输出 `DMGMD_DOMAIN mode=m1 reason=npt-m1-only ...`。小盒可能因 `slab_width < d_coord` 回退；P>1 的三斜或非周期输入仍报 `unsupported`。P1 和全量复制保留单次周期折回语义，局部域分解在边界交换或求力前拒绝不支持的单步多盒位移，不能用最小镜像距离抹去真实位移。

所有用户可见输出由 rank 0 写入。不能使 GPUMD 兼容行为或 DMG-MD 单 rank 基线回归。无法从源码和测试证明的行为标记为 `UNKNOWN`。

修改并行执行或数据布局前，读取[MPI 运行时](docs/standards/mpi-runtime.md)和[数据布局](docs/standards/data-layout.md)。优化和实际默认值见[多卡实现导读](docs/reference/multi-gpu-implementation.md)。编写性能计划、运行器或结论时，逐项报告同卡数普通 GPUMD、同卡数 LAMMPS/KOKKOS/NEP、固定单卡 GPUMD-WPE，并单列 DMG-MD 的单卡表现与自身强扩展。[对标矩阵与可比条件](docs/status/performance/npt-benchmarks.md#performance-matrix)

## GPUMD 兼容实现与数值验证

DMG-MD 在 `src/gpumd_compat/` 内实现所需的 GPUMD 子集。源码源自上游提交 `9d23496e41319b9e2af5221a7df6285387401d1e`，各文件头注明上游路径及裁剪内容。构建只编译仓库内的兼容代码，运行时独立执行求力和积分。外部引擎由测试工具单独启动。新增兼容实现须注明用途、上游文件和符号。源码审计记录实际调用的函数或 CUDA kernel。

浮点表达式、内存布局、内核启动参数或累加顺序发生变化时，重跑短程 golden 和对应长程回归。普通 GPUMD 5.9.1 生成了 L20 的短程 golden。各套科学回归的输入、程序身份和容差见[数值验证标准](docs/standards/golden-test-standard.md)。

## 性能对照引擎

L20 使用普通 GPUMD 5.9.1、GPUMD-WPE 1.0.0 和 LAMMPS 22 Jul 2025 Update 4（KOKKOS 4.6.2、MatPL NEP）做性能对照。计时直接运行已安装的可执行文件，报告记录当次版本。换用其他版本时，按相同工况检查物理量、实际后端和设备分配。输入和输出放在作业目录。[已测构建及对照标准](docs/standards/cross-engine-reference.md#benchmark-engines)

势文件转换须保留原文件和转换后的 SHA-256，标明等价范围。只有所有元素的径向 cutoff 相同、角向 cutoff 也相同时，才可将逐元素行折叠为统一行。先核对同快照的能量、力和 virial，再解释耗时。[输入等价条件](docs/standards/cross-engine-reference.md#比较对象与等价输入)

## 不可破坏的产品约束

- 不重新实现 NEP 数学公式。NEP 数学子集由 `src/gpumd_compat/` 的复现内核提供
  （上游来源见文件头），在其上只做小范围参数化；重写公式前必须先建立
  golden test 并获得维护者确认。
- 一 MPI rank 只控制一张 GPU。全量复制模式每 rank 持有完整输入坐标和类型，但积分、thermo 与输出写权限只属于 owned atoms；已实现的局部域分解模式使用 rank-local owned/ghost 布局和明确通信工作区。
- owned atoms 与 ghost atoms 必须有不同的生命周期和写权限；ghost 不得被积分、重复计入 thermo 或直接输出。
- 必须有跨迁移保持稳定的全局原子 ID；本地数组下标不能承担持久身份。
- 不假设 NEP halo 等于某一个 cutoff。位置、descriptor/导数、directed partial force 和最终 force 的每一层依赖都必须有 kernel 证据。
- 使用 Newton scatter 写边界副本的部分力时，必须定义反向力交换（reverse force exchange）。采用中心原子 gather 时，必须定义描述符或部分力中间量的前向交换（forward exchange）。
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
- Open MPI+UCX 是全量复制模式/局部域分解模式 runtime 的固定依赖；允许使用 Open MPI 的 `mpi-ext.h`/
  `MPIX_*`，不再维护 MPICH、MVAPICH 或其他 MPI 实现兼容性；
- Python 只用于测试、验证和分析脚本，核心 runtime 不依赖 Python；
- GPU 常驻数据只在初始化、通信、输出或验证需要时传回主机。

- Node.js、npm、npx 由当前用户通过用户级 fnm 安装和管理，不属于项目本地依赖；fnm 根目录为
  `~/.local/share/fnm`。检查 Node.js 环境时，必须先确认 fnm 及其 shell 初始化；在未加载用户
  `.bashrc` 的非交互 shell 中 `command -v node` 为空，不得据此判断 Node.js 未安装。应优先检查
  `fnm --version`、`fnm list`，并在加载 fnm 环境后记录 `node --version`、`npm --version` 和
  `npx --version`。

## Git 与基线管理

- 优先使用现有 Git 仓库作为版本基线。
- 开始修改前，使用 `git status --short` 了解工作区状态。
- 如果已有有效 Git 历史，不额外创建文件哈希、基线快照、校验清单或备份目录。
- 优先使用 `git diff`、`git status` 和现有提交历史确认文件变化，不重复建立 Git 已经提供的版本追踪能力。
- 保留任务开始前已有的未提交修改和未跟踪文件。
- 禁止擅自执行 `git reset --hard`、`git clean` 或其他可能丢失用户数据的回退操作。
- 只有 Git 无法覆盖的数据、明确的完整性需求或高风险不可逆操作，才考虑额外快照或哈希。
  数值 golden、测试 manifest 和跨引擎势文件转换等已有明确要求的验证材料，按相关任务的既有合同维护。
- 不得提交或推送，除非维护者明确要求。

## 最小改动原则

- 优先修改现有实现，而不是建立新抽象。
- 不为假设中的未来需求建立基础设施。
- 不创建未要求的工作日志、验证报告或临时文档；项目或任务明确要求的证据记录优先更新现有文件。
- 避免为验证机制再建立验证机制。
- 只修改完成当前任务所必需的文件。

## 测试与验证

- 验证工作必须与实际修改的风险相称。
- 优先运行与改动直接相关的现有测试。
- 一个稳定的修改阶段通常只需要一轮对应验证。
- 验证通过后停止，不为了增加信心而反复测试。
- 文档和注释修改默认不触发完整测试套件。
- 安全、数据迁移、公共 API 等高风险修改，以及项目明确要求的检查，不受上述限制。
- 验证失败时分析原因，不盲目反复运行相同测试。

## 构建与测试

实验室原生构建与环境选择见[部署指南](docs/operations/deployment.md)。构建和运行均须通过相同的 MPI/CUDA 门槛；容器内置 PMIx 使用编译时组件目录，无需宿主机路径。
原生构建示例：

    source ../env/md-mpi.sh
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release

构建：

    cmake --build build -j

测试：

    ctest --test-dir build --output-on-failure

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

1. 由 golden 清单标明来源的 GPUMD 单 GPU 数值基线；
2. DMG-MD 单 rank；
3. MPI 复制数据原型；
4. MPI domain decomposition。

比较邻居关系、每原子能量/力/virial、总 thermo、短轨迹、NVE 漂移、输出字节结构和 restart。不得为了让测试通过而随意放宽容差；容差必须来自重复的基线实验，并区分逐字段精确、确定性数值容差和随机/混沌轨迹的统计比较。

新增系综与势按 v1.0 流程的 L0–L6 分层验收。既有短程 golden、MPI 与长程
NVE/`nvt_ber` 回归必须保留；NHC/BDP 的独立标量、统计、双路径和 restart 验收入口为
[`tests/thermostats/README.md`](tests/thermostats/README.md)。随机系综须验证同 seed 重复与
预注册的多种子统计，不能用 Berendsen 涨落或单条轨迹证明正则/NPT 采样。
未来 NPT/新势须通过各自 Gate 与势×系综联合矩阵，不能以解析成功或现有 NEP/NVT 通过代替。

GPUMD、LAMMPS、DMG-MD 的横向 benchmark 使用同一物理体系和模型，允许经证明
等价的格式/单位转换；先比较能量、力、virial 和轨迹等物理量，再比较运行效率。
横向对照不要求输入文件或不同软件的输出字节相同；现有 DMG-MD 对 GPUMD 的
兼容性 golden 回归仍保持其独立门槛。

## 文档治理

### 事实来源

测试行为以脚本为准；case、profile、输入哈希、容差和统计门槛以对应 `manifest.json` 为准。测试 README 说明操作，[golden 标准](docs/standards/golden-test-standard.md)说明验收原则。文档引用这些来源，避免维护另一套参数。

### 标准和计划

`docs/standards/` 维护当前生效的规则。代码、支持范围、数据布局或测试门槛变化时，同一修改须更新对应标准。已确定但待实现的架构决策须明确标记，并说明现行行为。

`docs/plans/` 标明 `PROPOSED`、`APPROVED` 或 `IN PROGRESS`。README 和标准按实际状态引用计划。计划完成后，将最终规则写入标准、实测结果写入状态记录，并删除已完成的重复步骤。

### 验证记录

`docs/status/` 记录实际状态和执行结果。通过声明包含执行日期、代码 revision、环境、精确命令和结果；测试已定义与测试已通过分别表述。

同一测试目标追加到稳定主题文件的独立日期节，保留每次实际日期和时间。目标不同或证据结构独立时才新建报告。汇总链接到对应日期节，并说明原始证据的留存范围。历史摘要缺少身份或命令时，明确缺项，不能推测补齐。

公开性能记录注明实际输入、引擎、独占资源、采样方法和适用范围。旧计时只保留在本地汇总。站点账户、绝对路径、部署日志及带站点信息的冻结注册留在协调目录；公开科学结果保留原判定和范围。公开的参考程序安装位置使用可移植路径，保留实际版本与可执行文件身份。

报告文件名使用稳定的英文主题 slug，不含执行日期；重复执行和历史版本用日期节与稳定锚点区分。Git 历史保留旧快照，工作树不另建 archive 副本。机器索引和数据文件遵循各自规则。当前状态、标准和计划随事实同步。

### 写作和引用

正文先说明对象、行为和条件，再给源码符号、公式或实现编号。使用平实中文，拆开长句，删除套话、重复总结和无事实支持的形容词。列表用于真正并列的内容，复杂机制可按需要展开解释。完成后逐篇检查中英文空格、标点、标题层级和术语。

正文、标题和注释中的运行模式，使用全量复制模式、局部域分解模式和分阶段中间场交换方案。共用定义维护在 [GLOSSARY.md](GLOSSARY.md)，正文首次出现仍作必要解释。真实机器字段、代码符号和按 SHA 冻结的历史注册保留原文。

新文档从[文档索引](docs/README.md)按主题链接。移动或整合文档后，检查全部旧路径、Markdown 文件及片段链接、源码注释和测试 docstring。修改标题时保留已有引用所需的锚点。

## Agent skills

### Issue tracker

规格与工单保存在 `.scratch/<feature>/`。[工单约定](docs/agents/issue-tracker.md)

### Triage labels

工单通过 `Status:` 行记录分流状态。[状态对照表](docs/agents/triage-labels.md)

### Domain docs

DMG-MD 使用一套术语和架构决策。[领域文档读取约定](docs/agents/domain.md)

## Agent 工作方式

仅在用户明确要求并行工作时使用子代理。
仅主代理可以创建子代理，子代理不得继续委派。
子代理默认使用 gpt-6-luna，处理边界明确的任务。
主代理负责规划、架构决策、最终代码审查和结果整合。
避免多个代理重复探索同一问题，返回简洁结论和必要证据。

非平凡任务依次执行以下步骤：

1. 检查本仓库的 Git 状态和已有用户修改；
2. 阅读相关说明、构建脚本和测试；
3. 用 `rg` 从入口沿调用和 buffer 读写追踪；
4. 给出文件、符号、执行位置、数据所有权和 MPI 影响；
5. 做最小且在授权范围内的修改；
6. 构建或执行与风险相称的测试；
7. 检查本次 diff 及输出位置，保留已有工作区改动。

若任务会改变 NEP 数学、数据布局、精度、原子所有权、通信半径或兼容性承诺，应先完成证据和 golden test 设计，再请求维护者确认实现方向。

## 完成条件

- 用户要求的行为已经实现。
- 完成与风险相称的验证。
- 检查最终 diff，没有无关改动，并保留任务开始前已有的未提交修改和未跟踪文件。
- 满足以上条件后立即结束任务。
