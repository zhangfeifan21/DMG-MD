# AGENTS.md

## 项目定位

本仓库开发 **DMG-MD**（Distributed Multi-GPU Molecular Dynamics Runtime）。目标是一 MPI rank 对应一张 GPU 的多节点、多 GPU 经典分子动力学 runtime。

面向用户的兼容目标是：继续直接读取 GPUMD 的 `model.xyz`、potential 文件和 `run.in`，按阶段扩展已验证的势与系综。对已经声明支持的功能，输入语法、默认值、单位、校验、输出文件名、输出列及物理语义以锁定 GPUMD 参考版本和现行兼容矩阵为准；有意兼容偏离（如 BDP 固定默认 seed）必须在标准中明确记录并验证。对未支持命令必须识别并报出明确的 `unsupported` 错误，不得静默忽略。

项目已进入 **v1.0 正式版开发**，主线由
[开发流程](docs/plans/dmg-md-v1.0-development-workflow.md)管理，逐 Step 状态与证据见
[开发进度](docs/plans/dmg-md-v1.0-progress.md)。必须区分发布目标与当前实现，不能将
旧 NEP/NVE/基础 NVT 原型范围当作后续开发上限，也不能将计划功能写成已经支持。

v1.0 的 P0 发布目标：

- 普通经典 MD，单 bead；NEP4/5/NEP-ZBL、LJ、EAM/alloy；
- `nve`、`nvt_ber`、`nvt_nhc`、`nvt_bdp`、`npt_ber`、`npt_scr`；
- M1 覆盖上述势与六种系综；NPT 限各向同性、正交全周期盒；M2a 限 NEP 的 NVE 与三种 NVT；
- 版本/CLI、错误与 restart 合同、用户文档、许可证与第三方声明、性能回归、打包和发布验收。

Tersoff 1989、Langevin/BAOAB、NPT 三个对角分量与可选 restart sidecar 属于 P1（v1.1 候选）。
M2a NPT/非 NEP、NPH、MTTK、Parrinello–Rahman、三斜 NPT、多势混合、变盒命令与
minimize、PLUMED/长程静电等暂缓；PIMD、MC、phonon、deposition 和完整 measurement
系统不在本轮 v1.0 范围。详细优先级与边界以开发流程 §1 为准。

默认使用中文报告结论，即使任务说明使用英文。

## v1.0 当前阶段与实施顺序

截至 2026-11-03，Gate 0 默认决策已确认，A0–A6 已完成，G-A 在所列 NEP/单节点范围通过；
下一步是 **B0：压力 oracle 与单位/布局验收**，B/C 阶段尚未启动，v1.0 尚未发布。
后续按流程推进 B0/B1/G-B、C0–C3/C5 与 D 阶段；A7/C4 为独立 P1 可选项，不阻塞 P0。
执行开发任务时一次只推进获派的 Step，按任务卡完成依赖、停止条件、回归和证据记录；
文档维护不自动授权启动后续开发 Step。

当前已实现能力与边界：

- 势仍为 NEP4/5 与 NEP-ZBL；系综为 `nve`、`nvt_ber`、`nvt_nhc`、`nvt_bdp`。
  NHC/BDP 已覆盖 M1 P1/P2/P4、M2a P2/P4 与所列双后端验收；NPT、LJ、EAM 尚未实现。
- M1/M2a 共用 `EnsembleController`；NHC 链与 BDP RNG 只由 rank 0 持有，速度缩放只作用于 owned。
  三种 NVT 沿用 `3N` 温度自由度和步数型 Tdamp；NHC 质量使用 `dt_internal * Tdamp`。
- NHC/BDP 要求固定正步长，拒绝 `correct_velocity`、自适应步长及非默认实验分区/重叠选项。
  BDP 默认 seed 为 `12345678`，`DMGMD_ENSEMBLE_SEED` 的设置状态和原始字符串须在各 rank 一致。
- 每段 `run` 重建链/RNG/cache；`restart.xyz` 不保存这些隐藏状态，跨 rank 恢复是新作业，
  不承诺 NHC/BDP 轨迹连续。详细合同见[系综标准](docs/standards/ensembles.md)与
  [兼容矩阵](docs/standards/compatibility-matrix.md)。
- 统计结论限于验收体系；额外 harmonic 诊断总 K 通过、轴向均值 INCONCLUSIVE，
  不作普遍遍历性承诺。性能门仍为 PENDING-D4，跨节点与 release 长程未完成。

## 当前并行基线：M1/M2a

当前已实施的代码基线是 M2a：一 MPI rank 对应一张 GPU，`run_replicated` 是模式
调度器——potential 只解析一次（deferred-workspace NEP），按 typewise 半径判定
eligibility 后在两条数据面之间分派：

- **M1 replicated-full**（P=1 恒定，及不满足 M2a 判据的 P>1 输入）：每 rank 持有
  全部 N 个槽位，积分/thermo/输出权威按空间 slab 所有权（global_id 逻辑归属 +
  indexed collectives）迁移；P=1 与 M0 逐字节一致；小盒（如 24 Å fixture）因
  `slab_width < d_coord` 自动走此路径，行为不变；
- **M2a local-domain**（P>1、正交全周期、NEP large-box 判据、
  `slab_width >= d_coord`）：rank-local owned/ghost 布局（owned | dependency
  ghosts | coordinate-only ghosts，local_count 唯一 stride）、保守两跳位置 halo
  （`d_dep = max R_force + skin`、`d_coord = max(R_force+R_dep) + 2*skin`，按
  kernel 实际消费路径枚举，无法证明上界 fail closed）、p2p halo/迁移
  （HostStaged/CudaAware 双后端、独立方向 tag、CudaAware 需通过 device-buffer
  p2p 自检）、NEP 力中心 `[0, owned)` / 依赖中心 `[0, owned+dep)` / 候选
  `[0, local_count)` 分片（global-ID 行排序，legacy P1/M1 路径不变）；
- rank 0 输出 `DMGMD_DOMAIN mode=m2a|m1-fallback ...` 机器可读记录；triclinic/
  非周期在 P>1 下保持 M1 的 unsupported 错误。P1/M1 保留单次 wrap 调整；
  M2a 在 halo/force 前拒绝不受支持的 multi-box 单步位移，不靠 MIC 消去位移；
- 所有用户可见文件只由 rank 0 写；不得使原 GPUMD 和 DMG-MD 单 rank 行为回归；
- 无法从代码和测试证明的行为仍标记为 `UNKNOWN`，不得推测为兼容。

双路径的布局、半径、每步顺序、通信量与验证契约位于
`docs/standards/mpi-runtime.md` 与 `docs/standards/data-layout.md`。所有文档从
`docs/README.md` 进入：

- `docs/standards/`：当前产品、架构和测试合同；
- `docs/reference/`：锁定 GPUMD 调用链与 kernel 证据；
- `docs/operations/`：操作手册；
- `docs/status/current.md`：当前状态；`status/validation/` 和 `status/performance/` 按测试目标维护稳定主题报告，每次执行追加带日期的结果节；
- `docs/plans/`：剩余方案、风险和待办。

M2a 已实施并通过专属验收矩阵（`tests/mpi/run_mpi_domain.py`）；2026-09-18 手动执行的
nightly/长程矩阵也已通过。100000-step release 矩阵仍未执行；2026-09-26 单节点
多 GPU benchmark 已另行记录，不代表跨节点或固定 CPU 绑定下的结果。M2b（Fp/partial 分阶段交换）、3D 分解、
triclinic/非周期 local-domain 仍待后续审批。

## 独立性能主线：O1–O9 + O11 限定复验；O10 暂缓

O 系列由 `docs/plans/communication-computation-overlap.md` 分步管理，与 v1.0 产品化主线
无阶段门依赖，共享既有代码与回归合同；不替代 B/C/D 阶段或发布验收。
O6 已完成稳态拆分诊断，carbon 主损失位于 radial，硬件计数器权限
受限使微架构原因仍为 INCONCLUSIVE，首轮 O9 以 NO_ADOPTION 结束，报告见
`docs/status/performance/nep-execution-cost.md#o6-results-20261021-report`。O7 于 2026-10-23 已采用一次 reason OR
缓存判定；golden/MPI/nightly NVE、实际调用计数与 60 个配对 trial 完成，整步收益
INCONCLUSIVE，见 `docs/status/performance/cache-reduction-results.md#o7-results-20261023-report`。O8 于 2026-10-26
完成并采用按需 NVE thermo：NVE 只在 thermo/XYZ 输出步计算，NVT 保留每步反馈；
292 个调用计数作业、MPI/nightly、105 个正式 trial 和 8 个 CPU 诊断完成。
相对 O7 三组增量确认、一组 INCONCLUSIVE，见 `docs/status/performance/thermo-demand-results.md#o8-results-20261026-report`。
O9 于 2026-10-26 完成证据门槛复核；真实 NCU 稳态重试仍受权限拒绝，
无可信执行候选，按原完成条件以 NO_ADOPTION 结束，未改 NEP 执行；见
`docs/status/performance/nep-candidate-validation.md#o9-results-20261026-report`。O6.5 于 2026-10-27 完成真实
图/mask 与同快照 A–D 对照：30 个正式 trial、18 份 trace、50 个独立重放进程及完整
nightly 42 配置通过。carbon radial / water angular partial 紧凑映射相对 C 的目标耗时
分别下降约 29.71%/12.32%，支持独立整步试验；D 仍慢于 A，微架构原因仍 INCONCLUSIVE，
未采用、未测候选整步净收益，见 `docs/status/performance/nep-execution-cost.md#o65-results-20261027-report`。
O8.5 于 2026-10-28 完成只拆 filter/descriptor、力计算完整执行的显式 producer 候选：
GPU CTest 18、producer MPI 313 次执行、受控调度 27、完整 nightly 42 + restart 12、
120 个六组合正式 trial、24 份 trace 与 8 个 CPU 诊断通过。基础 launch 从 10 降为 7，
四组拆分成本下降；相对同后端候选 full，water/HostStaged、carbon/CudaAware、
water/CudaAware 的探索区间确认收益，carbon/HostStaged 仍慢。相对 HostStaged/full
只有本轮 water/P8/HostStaged 确认约 1.0193×，须交 O9 用新样本独立确认；见
`docs/status/performance/producer-schedule-results.md#o85-results-20261027-report`。未采用，生产默认仍
HostStaged/full、overlap=off、schedule=split。
O8.6 于 2026-10-28 完成同 E/H 紧凑索引 B 和几何内缩 C；两路线分别通过完整 MPI、
调度 27、nightly 42 + restart 12，240 个正式 trial、48 份 trace、24 个 CPU 诊断及离线复算通过。
B/A 的四组 serial/wait 增量获探索区间支持，C/B wait 增量均未确认；相对候选 HostStaged/full，
carbon 双后端及 water/HostStaged 的 B/C 有本轮净收益，water/CudaAware 仍慢。未采用，见
`docs/status/performance/producer-schedule-results.md#o86-results-20261028-report`。
O9 于 2026-10-29 完成独立确认：300 个正式 trial、12 个 CPU 诊断及新科学/MPI/调度门槛通过，
相同二进制的完整 nightly/GPU 验收经原始清单校验后复用。B 在 carbon_200k/P2 双后端及
water_400k/P8/HostStaged 的校正净收益区间通过，water/P8/CudaAware 仍慢于默认路径；
water/P4 有探索支持，carbon_1m/高重建/空 early 限制外推。见
`docs/status/performance/nep-candidate-validation.md#o9-results-20261029-report`。结论为 CONFIRMED_SCOPED_CANDIDATE，
后续 O11 于 2026-10-30 完成限定范围复验：800 个正式 trial（一次预定追加）、
80 个短程科学对照、48 个 CPU 诊断及 31,293 项封存文件离线复算通过。
仅建议 carbon_200k/P2 两后端手动 B-wait，相对 HostStaged/full 为 1.0210×/1.0199×；
water/P8/HostStaged 区间仍跨 1，本轮不建议采用；water/P8/CudaAware 也未胜默认路径。
water/P4 仍为探索支持，未覆盖的完整矩阵不作外推。见
`docs/status/performance/nep-candidate-validation.md#o11-results-20261030-report`。O10 为条件性 PROPOSED、暂缓；
默认不变，首轮 O9 及后续独立确认历史保留。
跨引擎绑定与 LAMMPS 对照仍由
`docs/plans/mpi-cpu-binding.md`、`docs/plans/lammps-multigpu-benchmark.md` 独立管理。
LAMMPS 输入、静态物理量对照与三引擎 benchmark smoke 已实施；正式效率矩阵仍待
跨引擎 CPU/NUMA 绑定验收和更多正确性验收。
C1 与 O1 expected-cores/expected-numa 参数只观测；O5 已补充 DMG-MD 共用
core/numa/rankfile 启动及独立 affinity audit，合同见 `docs/standards/cpu-binding.md`。
跨引擎绑定、任意调度器与内存页放置仍待验收；
O1 时间线、O2 默认关闭的串行分区、O3 halo 生命周期已实施并验收。默认路径仍 begin 后立即
finish，再计算 NEP；O4 已接入默认关闭的普通步实际 overlap，已验收、仍默认关闭；
O5 本机正式矩阵、四阶段归因和独立采用验证于 2026-10-21 完成；仍默认 off，
限定收益与默认后端比较的范围见 docs/status/current.md，
M2b 不是当前下一实施切口。

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

## 外部 reference 的只读边界（强制）

`../gpumd-reference`、`../lammps-reference` 和 `../lammps-matpl-reference` 均为锁定的
**严格只读 reference**。不得在其中应用补丁、复制源码、运行原位构建、生成日志或输出、
清理文件，也不得为了测试修改势文件。执行参考程序时把工作目录和输出目录放在 `newmd`
或临时目录；每次相关工作前后核对三个 checkout 的 Git 状态，并检查是否新增被忽略的构建产物。

LAMMPS 锁定 `stable_22Jul2025_update4`（commit
`611ca3b7f8525ba802373b04f9e3d632d515f7e3`），MatPL 锁定 commit
`21fabf34eadc57a717f4c7fbce9bb80aad5fd991`。2026-10-09 从原位安装迁出的
MatPL 补丁源码、CMake 修改和逐文件 SHA-256 保存在
`third_party/lammps-matpl-overlay/`；当时的构建产物保存在忽略版本控制的
`build-lammps-nep-kk-preserved/`。后者的 CMake cache 包含旧绝对路径，只用于保留
已验证的可执行文件，不能直接当作可迁移的重建目录。

如需修改或重建 LAMMPS，先从只读 `../lammps-reference` 在 `newmd` 内生成独立工作源码
（例如 `build-lammps-matpl-src/`），再应用上述 overlay 或新的本地补丁；构建目录也必须在
`newmd` 内。不能把 `../lammps-matpl-reference` 当作可编辑插件工作树。任何用于
GPUMD/LAMMPS 比较的势文件转换都应保留原文件及转换后 SHA-256，并明确标注等价范围。
维护者已批准以物理量等价而非文件哈希一致作为三引擎横向判定：所有元素的径向 cutoff
相同且角向 cutoff 相同时，可把 typewise 行折叠为统一行；若任一值不同必须拒绝该转换。
转换规则、静态物理量门槛和性能比较边界见 `docs/standards/cross-engine-reference.md`。

## 不可破坏的产品约束

- 不重新实现 NEP 数学公式。NEP 数学子集由 `src/gpumd_compat/` 的复现内核提供
  （复制自 GPUMD 锁定 commit），在其上只做小范围参数化；重写公式前必须先建立
  golden test 并获得维护者确认。
- 一 MPI rank 只控制一张 GPU。M1 每 rank 持有完整输入坐标和类型，但积分、thermo 与输出写权限只属于 owned atoms；已实现的 M2a 使用 rank-local owned/ghost 布局和明确通信工作区。
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
- Open MPI+UCX 是 M1/M2a runtime 的固定依赖；允许使用 Open MPI 的 `mpi-ext.h`/
  `MPIX_*`，不再维护 MPICH、MVAPICH 或其他 MPI 实现兼容性；
- Python 只用于测试、验证和分析脚本，核心 runtime 不依赖 Python；
- GPU 常驻数据只在初始化、通信、输出或验证需要时传回主机。
- Node.js、npm、npx 由当前用户通过用户级 fnm 安装和管理，不属于项目本地依赖；fnm 根目录为
  `~/.local/share/fnm`。检查 Node.js 环境时，必须先确认 fnm 及其 shell 初始化；在未加载用户
  `.bashrc` 的非交互 shell 中 `command -v node` 为空，不得据此判断 Node.js 未安装。应优先检查
  `fnm --version`、`fnm list`，并在加载 fnm 环境后记录 `node --version`、`npm --version` 和
  `npx --version`。

## 构建与测试

原生部署从本仓库根目录加载 `../env/md-mpi.sh`；Docker 使用仓库
`docker/md-mpi.sh` 安装到 `/opt/dmgmd/env/md-mpi.sh`，entrypoint 自动加载。
两种方式均须通过相同的 MPI/CUDA 门槛；容器内置 PMIx 使用编译时组件目录，无需宿主机路径。
原生构建示例：

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

- 测试行为以测试脚本为第一事实源；case、profile、输入哈希、容差和统计门槛以对应
  `manifest.json` 为第一事实源。测试目录 README 说明操作方法，
  `docs/standards/golden-test-standard.md` 说明验收原则，不得复制维护另一套参数。
- `docs/standards/` 只描述当前已经生效的合同。代码、支持范围、数据布局或测试门槛变化时，
  必须在同一修改中更新对应标准。
- `docs/status/` 只记录实际状态或验证结果。通过声明必须包含执行日期、代码 revision、环境、
  精确命令和结果；“测试已定义”不得写成“当前已通过”。同一测试目标的重复执行应追加到稳定主题文件的
  独立日期节，保留每次执行的实际日期与时间；仅在测试目标不同或证据结构确实独立时新建报告。
  汇总和索引须链接到对应日期节，不能把不同执行合并成一个无日期结论。产物目录、日志、命令及原始
  证据中实际使用的日期路径属于证据身份，文档整合时不得改写。
- `docs/plans/` 必须标明 `PROPOSED`、`APPROVED` 或 `IN PROGRESS`，不得被 README 或标准描述为
  已实现。计划完成后，将最终合同迁入 standards、实测结果写入 status，并删除已完成的重复步骤。
- 新文档必须从 `docs/README.md` 按主题归类和链接。文档移动或报告整合后，使用 `rg` 检查全部旧路径引用、
  Markdown 文件与片段链接、源码注释和测试 docstring；Git 历史保存旧快照，不在工作树中新建重复 archive 文档。

## Agent 工作方式

仅在用户明确要求并行工作时使用子代理。
仅主代理可以创建子代理，子代理不得继续委派。
子代理默认使用 gpt-6-luna，处理边界明确的任务。
主代理负责规划、架构决策、最终代码审查和结果整合。
避免多个代理重复探索同一问题，返回简洁结论和必要证据。

非平凡任务按以下顺序进行：

1. 检查本仓库与三个只读 reference checkout 的状态和已有用户修改；
2. 阅读相关说明、构建脚本和测试；
3. 用 `rg` 从入口沿调用和 buffer 读写追踪；
4. 给出文件、符号、执行位置、数据所有权和 MPI 影响；
5. 做最小且在授权范围内的修改；
6. 构建或执行与风险相称的测试；
7. 检查 diff，确认未修改 `../gpumd-reference` 和无关文件。

若任务会改变 NEP 数学、数据布局、精度、原子所有权、通信半径或兼容性承诺，应先完成证据和 golden test 设计，再请求维护者确认实现方向。
