# DMG-MD 最小源码清单

## 1. 判定原则

参考 GPUMD commit：`9d23496e41319b9e2af5221a7df6285387401d1e`。分类描述的是技术复用方式，不替代许可证审查。GPUMD 源文件带 GPLv3 声明；把源码复制或链接进 DMG-MD 前必须由维护者确认许可证与发布策略。

分类：

1. **可原样共享**：数学或小型无状态代码可保持实现与数值顺序。
2. **应提取为公共库**：有价值的验证实现被全局对象/文件 I/O包裹，应抽出纯组件。
3. **必须为 owned/ghost 重构**：接口或数据结构根本依赖全局 `N`、全局数组下标或单进程 I/O。
4. **当前产品不需要**：replicated-data 阶段仍明确排除的高级模块。
5. **不确定，需实验**：语义/数值/通信代价尚未证明。

“原样共享”不等于把整个 `.cu` 原样复制；通常只指表中列出的符号。

## 2. 类别 1：可原样共享

| GPUMD 文件/符号 | 用途 | 原样边界 | 证据/限制 |
| --- | --- | --- | --- |
| `src/utilities/nep_utilities.cuh` 的 `find_fc*`, `find_fn*`, `accumulate_s`, `find_q`, `apply_ann_*`, `accumulate_f12`, `find_f_and_fp_zbl` | NEP/NEP-ZBL 数学核心 | device helper 函数及相关常数/模板 | `src/force/nep.cu:488-975` 和 `nep_small_box.cuh` 直接调用；产品约束禁止重写公式 |
| `src/force/nep.cu:75-98` `get_descriptor_parameters_type_pair()` | 把 descriptor 参数变换为 kernel 布局 | 纯 host 数据变换 | 输出由 `ANN::c_type_pair` 使用；需补独立单元测试 |
| `src/model/box.cuh` 的 inverse/MIC 数学 | orthogonal/triclinic minimum image | 数学函数和 `h` 分量顺序 | class 内还混有全局 box 状态，应通过小 view 暴露 |
| `src/utilities/common.cuh` 的物理常数 | 单位、Boltzmann 常数、压力转换 | 被 MVP 使用的常数 | `TIME_UNIT_CONVERSION=10.18051`、`PRESSURE_UNIT_CONVERSION=160.2177` 等必须锁定 |
| `src/force/neighbor.cuh:112-136` `gpu_sort_neighbor_list` | ELL 每行排序 | 在 ELL pitch/row 语义不变时 | many-body reverse-edge 二分查找依赖排序 |

不建议“清理”这些 helper 的浮点表达式、float/double 混合或累加顺序，直到 golden tests 已建立。

## 3. 类别 2：应提取为公共库

| 文件/符号 | 建议公共组件 | 需要切掉的外围依赖 |
| --- | --- | --- |
| `src/force/nep.cu/.cuh` `NEP::NEP`, `update_potential` | `NepModelLoader`、只读 device parameter view | 固定文件打开、全局 N workspace 分配、DFTD3 二次扫描、Potential 继承 |
| `src/force/nep.cu:436-975` large-box kernels | `nep_cuda_core` | `GPU_Vector` 具体所有者、隐式全局 stride、`N1/N2` 成员 |
| `src/force/potential.cu:170-333` float `gpu_find_force_many_body` | many-body center-gather kernel | `Potential` 的 global vector wrapper；显式传 atom stride/center domain |
| `src/force/neighbor.cu/.cuh` cell-list/sort 算法 | local device neighbor builder | 候选域被限制为 `[N1,N2)`、全局 `N`、host D2H rebuild check |
| `src/model/read_xyz.cu` model schema/atom line解析 | `ModelXyzParser` | 立即分配全局 Atom、读取 `run.in` 找 potential、`exit(1)` |
| `src/main_gpumd/run.cu` tokenizer/命令参数验证 | `RunInParser` + typed IR | parse 与执行耦合、各模块再次扫描文件、全局对象状态 |
| `src/integrate/ensemble.cu:176-214` VV kernel | owned-domain integrator kernel | `mass.size()==global N` 假设 |
| `src/integrate/ensemble.cu:434-673` thermo kernel/公式 | local thermo accumulator | GPU kernel内直接除全局 `N_temperature` 和 volume；MPI 应延迟除法至 global reduction 后 |
| `src/measure/dump_thermo.cu`、`dump_xyz.cu`、`dump_restart.cu` | GPUMD-compatible formatter | 直接 D2H 全局 Atom、固定单进程 FILE、GPU global reduction |

公共库边界应同时服务“GPUMD reference adapter（若以后构建）”和 DMG-MD，不要把 MPI communicator、rank 0 I/O 或 domain decomposition 塞进 NEP 数学库。

## 4. 类别 3：必须为 owned/ghost 重构

### 4.1 用户指定重点文件逐项结论

| 文件 | 结论 | 必须重构的原因 |
| --- | --- | --- |
| `src/main_gpumd/main.cu` | 重写 runtime 入口 | 栈上构造单一 `Run`，无 MPI/rank/GPU 绑定，固定工作目录输入 |
| `src/main_gpumd/run.cu/.cuh` | 保留顺序语义，重写状态机 | parser/执行耦合；`Atom`/global time/多段 run 全在单进程对象 |
| `src/model/read_xyz.cu` | 提取 parser 后重写分发 | 一次分配完整 `N`；输入行下标是隐式身份 |
| `src/model/atom.cu/.cuh` | 重写 | `number_of_atoms` 同时是全局总数、local capacity、SoA stride；无 global ID/owner/image |
| `src/model/box.cu/.cuh` | 保留数学、重构 domain view | box 本身可复制，但 PBC/邻居构建假定一份完整体系；NewMD 当前只支持正交全周期 |
| `src/model/group.cu/.cuh` | 重写 | `contents` 是完整 Atom 数组下标；global group size 与 local labels 混合 |
| `src/force/force.cu/.cuh` | 重写 orchestration | 全 N PBC/clear；potential vector；自动按可见 GPU 选择 `NEP_MULTIGPU` |
| `src/force/potential.cu/.cuh` | wrapper 重写，kernel 提取 | `N1/N2` 成员但 vector stride仍是全 N；反向边查找依赖 global row |
| `src/force/nep.cu/.cuh` | loader/core 提取，workspace/orchestration 重写 | workspace 全 N；neighbor 和 small/large dispatch 内置；无通信阶段 |
| `src/force/neighbor.cu/.cuh` | 重写域和索引 | cell list全 N；candidate 限制等于中心域；无 owned/ghost/image/global ID |
| `src/integrate/integrate.cu/.cuh` | 重写控制层 | ensemble对象读取完整 Atom；fix/move/group lifecycle 与一段 run绑定 |
| `src/integrate/ensemble.cu` | kernel提取，wrapper重写 | 所有原子被积分/归约；温度自由度是全局 host 数 |
| `src/integrate/ensemble_nve.cu/.cuh` | 保留调用顺序，重写数据接口 | `Atom&` 全局对象 |
| `src/integrate/ensemble_ber.cu/.cuh` | 保留公式/顺序，重写 global reduction | thermostat在 local kernel后直接读取 GPU thermo；MPI 需 all-reduce barrier |
| `src/measure/measure.cu/.cuh` | 重写 rank-aware调度 | property 直接访问全局 Atom/I/O；多 property 生命周期单进程 |
| `src/measure/dump_thermo.cu/.cuh` | formatter提取，I/O重写 | global thermo和固定 append文件 |
| `src/measure/dump_xyz.cu/.cuh` | parser/formatter提取，gather重写 | D2H 完整数组；默认原子数组顺序；group contents索引；GPU全局 sum |
| `src/measure/dump_restart.cu/.cuh` | formatter提取，gather重写 | 固定覆盖文件；无 global ID；完整数组序 |
| `src/model/velocity.cu/.cuh` | 重写 RNG/修正 | RNG seed含本地数组下标；CPU全局动量/角动量修正和完整 D2H/H2D |

### 4.2 必须新增、GPUMD 中不存在的 runtime 组件

- MPI lifecycle、rank-to-device binding 与“每 rank 只见/只用一张 GPU”的校验；
- fractional-space domain decomposition；
- `global_id`、owner、image、owned/local counts；
- 原子迁移（持久字段 pack/unpack）；
- position/type halo exchange；
- NEP intermediate `Fp`/directed partial exchange，或可配置深 halo；
- 固定 Open MPI+UCX 的 HostStaged/CudaAware backend、capability query、数值自检和同步管理；
- owned-only local reductions + MPI collectives；
- rank 0 gather、global-ID 稳定排序和兼容 formatter；
- 跨 rank 数 restart 的 repartition loader；
- 所有 rank 一致错误传播，避免某 rank `exit(1)` 造成 hang。

## 5. 类别 4：第一阶段不需要

以下目录/模块不应进入最小链接闭包；parser 对相应命令仍需识别并明确报 unsupported：

| 范围 | GPUMD 代表文件/命令 |
| --- | --- |
| PIMD/多 bead | `src/integrate/ensemble_pimd*`, `dump_beads` |
| Monte Carlo | `src/mc/`, `mc` |
| phonon/cohesive/elastic | `src/phonon/`, `compute_phonon`, `compute_cohesive`, `compute_elastic` |
| minimize | `src/minimize/`, `minimize` |
| deposition | `src/main_gpumd/deposition*` |
| PLUMED | `src/measure/plumed*`, `plumed` |
| 长程静电/DFTD3 | `kspace`, `dftd3` 及相关实现 |
| 其他势函数 | `src/force/` 中 Tersoff、SW、EAM、LJ、ILP、D3 等；仅保留 NEP依赖文件 |
| 高级 ensemble | NPT、NPH、NHC/MTTK、Langevin/BAO/BDP/QTB/TTM/MSST 等（基础 NVT 首选只做 `nvt_ber`） |
| 非 MVP measurement | DOS/SDC/MSD/RDF/ADF/HAC/HNEMD/SHC/modal/observer/netcdf 等 |
| 外加物理 | electron_stop、add_force/random_force/spring/efield、move/deform、active learning |

构建层面不要沿用 GPUMD 顶层 CMake 的目录 glob；应显式列出最小库文件，防止不支持模块以静态初始化或 parser 分支形式悄然进入。

## 6. 类别 5：需实验验证

| 文件/功能 | 为什么不确定 | 所需实验 |
| --- | --- | --- |
| `src/force/nep_small_box.cuh` | atomic Newton scatter、多周期 image、硬编码 MN=2000；与 large 路径数值累加顺序不同 | 小盒/多 image golden、ghost reverse force、overflow/容量测试 |
| `src/force/nep_multigpu.cu/.cuh` | GPU0全局体系，不能复用编排；但 N1..N5 显示双层依赖 | 用其与单 GPU large path 对比，验证 2rc重复计算与分阶段交换等价性 |
| typewise cutoff | global neighbor按 radial max建，filter先 radial后 angular；实际格式允许 per-type值 | 构造 `Ra>Rr` 和强不对称 typewise 模型，确认GPUMD可观察行为/合法性 |
| ZBL halo | ZBL kernel使用 angular list而非独立 ZBL list | `rc_outer` 大于 angular cutoff 的合成模型；确认应兼容截断还是拒绝 |
| `correct_velocity` | 很多长 NVE 用户可能依赖；当前算法CPU全局、数组下标相关 | 真实 run.in 语料统计；MPI global-ID实现与单 GPU动量/角动量对比 |
| `fix` | 可能是常用固体/边界功能，但会影响 group、DOF和迁移 | 用户样例统计；固定组跨 rank thermo/输出测试 |
| triclinic MPI | 产品兼容需要，但 NewMD未实现；rank surface/halo更复杂 | fractional decomposition、倾斜盒跨周期边界测试 |
| neighbor.out 副作用 | NEP 每1000次计算自动 append；是否算兼容输出未定 | GPUMD实际文件 golden；产品决定是否MVP保留 |
| restart连续性 | GPUMD格式不含 time/thermostat/RNG/global ID | 跨 rank NVE可恢复；NVT只比较格式/统计或扩展sidecar的产品决策 |

## 7. 最小构建闭包建议

不是立即移动文件，而是未来实现的逻辑目标：

```text
compat_io
  model_xyz_parser
  run_in_parser + typed command IR
  nep_model_loader
  thermo/xyz/restart formatter

nep_cuda_core
  nep parameter/device views
  nep_utilities device helpers
  local neighbor builder + sorted directed list
  descriptor/radial/partial/many-body/ZBL kernels

md_core
  box + units
  owned/local atom storage
  velocity-Verlet + Berendsen
  local thermo accumulator

distributed_runtime
  MPI/device binding
  decomposition/migration/halo/intermediate exchange
  collectives
  rank-0 I/O
```

流程映射：

| 目标步骤 | 最小组件 |
| --- | --- |
| read `model.xyz` | compat parser → global IDs → scatter |
| parse supported `run.in` | tokenizer/typed IR → rank-consistent validation |
| load NEP | common loader → per-rank immutable device parameter view |
| initialize atoms | owned/local storage + velocity global-ID RNG |
| integrate owned | VV kernel with owned range |
| exchange halo | migration + position/type/intermediate communication |
| build local neighbor | directed, reciprocal, sorted list over local indices |
| evaluate NEP | reused large-box core kernels |
| global reductions | local owned thermo + MPI all-reduce |
| compatible outputs | gather by global ID + exact GPUMD formatter |

## 8. 当前 DMG-MD 源码的处置（2026-09-02 已实施）

| 当前文件 | 处置 |
| --- | --- |
| `include/dmgmd/model.hpp` | host Atom 模型；显式 global/owned/ghost 和 stable global ID |
| `include/dmgmd/run_ir.hpp`, `src/run_parser.cpp` | GPUMD token 规则上的 typed command IR；parse 与执行分离 |
| `src/model_parser.cpp` | model.xyz schema、单位、默认质量和 type 映射兼容层 |
| `src/runtime.cu` | local-stride device storage、single-rank NEP adapter、owned-only VV/thermo 和兼容输出 |
| `src/dmgmd_main.cpp` | `dmg-md` 入口；先完整 parse/validate，再初始化 GPU/runtime |
| GPUMD replicated core | 2026-09-09 起在 `src/gpumd_compat/` 内复现（commit `9d23496e`）：tokenizer、Box、GPU_Vector、neighbor、Potential、NEP/NEP-ZBL kernels；构建不再编译或链接 `../gpumd-reference` |
| 旧 `include/newmd`、PBC/CSR/DeviceBuffer 尝试 | 已删除；它们的单一 count 和独立 Box/neighbor 实现不再参与架构 |
