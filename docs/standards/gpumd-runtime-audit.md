# GPUMD 普通 NEP MD runtime 审计

类别：现行标准与代码审计证据。

## 1. 审计边界与参考版本

- 参考仓库：`../gpumd-reference`
- 参考 commit：`9d23496e41319b9e2af5221a7df6285387401d1e`
- 目标：普通经典 MD、单 bead、NEP/NEP-ZBL；NVE 及基础 NVT。
- 本文只描述实际可从该 commit 追踪的行为。不能证明的内容标记为 **UNKNOWN**。
- GPUMD 的 `Atom::number_of_atoms`、各 `GPU_Vector::size()` 和多数 kernel stride 都是全局 `N`。表中“全局假设”表示代码当前把本进程数组当成完整体系，并不代表 DMG-MD 可以沿用。

## 2. 一页关键路径

```text
main()
└─ Run::Run()
   ├─ initialize_position()
   │  ├─ get_filename_potential(run.in)
   │  ├─ get_atom_symbols(potential 第一行)
   │  ├─ read_number_of_atoms/read_line2/read_atom_line(model.xyz)
   │  └─ Group::find_size/find_contents
   ├─ allocate_memory_gpu()
   ├─ Velocity::initialize()
   └─ Run::execute_run_in()
      ├─ get_tokens() + 行内 # 截断
      ├─ Run::parse_one_keyword()
      │  ├─ Force::parse_potential() → NEP 或 NEP_MULTIGPU
      │  ├─ Integrate::parse_ensemble()
      │  ├─ Measure property 构造
      │  └─ Run::parse_run()
      └─ Run::perform_a_run()
         ├─ Integrate/Measure::initialize()
         ├─ Force::compute()                         [第一次力]
         ├─ for step
         │  ├─ Velocity::correct_velocity()
         │  ├─ Integrate::compute1()
         │  │  └─ NVE/Berendsen::compute1()
         │  │     └─ Ensemble::velocity_verlet()    [owned v/position]
         │  ├─ Force::compute()
         │  │  ├─ gpu_apply_pbc
         │  │  ├─ initialize_properties
         │  │  └─ NEP::compute()
         │  │     ├─ large: global Verlet list → filtered lists
         │  │     │  → descriptor/PE/Fp → radial force
         │  │     │  → angular partial → many-body gather → ZBL
         │  │     └─ small: expanded-image list → descriptor
         │  │        → radial/angular/ZBL Newton atomic scatter
         │  ├─ Integrate::compute2()
         │  │  └─ VV second half → Ensemble::find_thermo()
         │  │     [Berendsen 再缩放速度]
         │  └─ Measure::process()                    [thermo/XYZ/restart]
         └─ Measure/Integrate/Velocity/Force::finalize()
```

DMG-MD 的最小运行时路径应收缩为：

```text
rank 0 兼容解析并广播规范化配置
→ 分布式读取/散发 model.xyz，建立 stable global ID
→ 每 rank 加载并校验同一 NEP 参数，上传本 rank GPU
→ owned 原子迁移 + position/type halo
→ 构建“owned 中心、local 邻居”的有向邻居表
→ 计算 owned 及依赖层中心的 descriptor/partial 数据
→ 交换中间场或使用经证明的更深位置 halo
→ 只写 owned force/PE/virial
→ 全局 thermo 归约
→ 只积分 owned
→ rank 0 按 global ID 生成 GPUMD 兼容输出
```

## 3. 入口、模型读取和初始化

证据入口是 `src/main_gpumd/main.cu:29` 的 `main()`。普通路径在 `main.cu:42-50` 栈上构造 `Run`；`Run::Run()` 位于 `src/main_gpumd/run.cu:144-173`。

| 调用节点 | 输入 → 输出 | 位置 | 当前所有权/全局假设 | MPI 处置 |
| --- | --- | --- | --- | --- |
| `main()` | 无命令行输入；读取工作目录固定文件 | CPU | 单进程；`argc/argv` 未用于选文件 | **重构**启动层；rank/GPU 绑定、MPI 初始化和一致错误传播属于新 runtime |
| `Run::Run()` | 构造 `Box/Atom/Group/Force/...`，执行完整作业 | CPU | 一个对象拥有全局体系和全局状态 | **提取**控制流语义，不共享这个全局对象 |
| `initialize_position()` (`src/model/read_xyz.cu:482-529`) | `run.in`、`model.xyz` → host atom/box/group | CPU | `Atom` 保存完整 `N`，输入顺序就是持久顺序 | **重构**为兼容 reader + 分发；生成不可变 `global_id=输入行号` |
| `get_filename_potential()` (`read_xyz.cu:427-450`) | 扫描 `run.in` → potential 文件名 | CPU | 最后一个首 token 为 `potential` 的行获胜 | **提取但修正架构**：先完整 parse；兼容行为需测试多 potential/多 run |
| `get_atom_symbols()` (`read_xyz.cu:452-480`) | potential 第一行 → type symbol 顺序 | CPU | symbol 顺序定义整数 type；全局一致 | **原样保持语义**，rank 0 校验后广播映射 |
| model 第一行解析 (`read_xyz.cu:141-153`) | 一 token → `N`，要求 `N>=2` | CPU | `N` 同时控制所有 host/device 数组 | **提取**语法；分别保存 `global_count/owned_count/local_count` |
| model 第二行解析 (`read_xyz.cu:155-310`) | `pbc`、`Lattice`、`Properties` → box/列偏移 | CPU | 整行转小写；属性表面向完整文件 | **提取**兼容 parser；rank 0 形成 schema 并广播 |
| atom 行解析 (`read_xyz.cu:312-400`) | species/pos/可选 mass/charge/vel/group → SoA host 数组 | CPU | 行号是数组下标；无显式 global ID | **重构**所有权；保留解析和单位转换，新增 global ID |
| `Group::find_size/find_contents()` (`src/model/group.cu:25-72`) | 每原子 label → group size/prefix/原子下标表 | CPU | contents 是全局数组下标 | **重构**为本地 label + global group 计数；不能持久化本地下标 |
| `allocate_memory_gpu()` (`read_xyz.cu:532-557`) | host 全局数组 → GPU 全局 SoA | CPU+GPU copy | type/mass/charge/position/group 为全局 `N`；force/PE/virial 也分配全局 `N` stride | **删除此编排**，保留 SoA 物理顺序；按 local capacity 分配 |
| `Velocity::initialize()` (`src/model/velocity.cu:312-346`) | model vel 或温度/seed → GPU velocity | CPU+GPU | 随机序列和动量修正依赖原数组下标与全局汇总 | **重构**为 global-ID RNG 和 MPI 归约 |

模型数据的实际布局详见 [data-layout.md](./data-layout.md)，语法和错误条件详见 [compatibility-matrix.md](./compatibility-matrix.md)。

## 4. `run.in` 解析、命令分派与多段 run

`Run::execute_run_in()` (`src/main_gpumd/run.cu:175-207`) 逐行调用 `get_tokens()` (`src/utilities/error.cu:124-141`)。token 仅按空白切分；从第一个首字符为 `#` 的 token 起丢弃行尾。没有引号语义。每行最多 32 个 token，检查是 `>32`，虽然错误文本写“less than 32”。

`Run::parse_one_keyword()` (`run.cu:339-571`) 是实际命令集合的唯一可靠入口。未知命令进入 `PRINT_KEYWORD_ERROR` (`src/utilities/error.cuh:56-63`)。`kspace` 和 `dftd3` 是例外：dispatcher 静默不做事，等待其他构造器重新扫描文件。DMG-MD 不得复制这种模式；未支持命令应当在统一 parser 中立即报 `unsupported`。

| 节点 | 输入 → 输出 | 位置 | 全局假设 | MPI 处置 |
| --- | --- | --- | --- | --- |
| `get_tokens()` | 一行 → `vector<string>` | CPU | 无原子数据 | **共享/提取**，但用 golden test 固定空白、注释、大小写行为 |
| `parse_one_keyword()` | token → 各模块状态或立即执行 | CPU | 解析与执行交织，一个进程修改全局对象 | **重构**为 parse/validate IR + execute；rank 0 解析后广播 |
| `Force::parse_potential()` | 文件名/可选 partition direction → `Potential` | CPU+GPU | 查询本进程所有可见 GPU | **提取 NEP loader，删除设备自动枚举** |
| `Integrate::parse_ensemble()` | ensemble token → ensemble 参数 | CPU | global `N`、group contents | **提取 MVP 语法**，执行层按 owned 域工作 |
| dump 构造器 | token → `Measure::properties` | CPU | 输出对象读取全局 Atom | **提取语法和 formatter**，重构 gather/I/O |
| `Run::parse_run()` (`run.cu:665-686`) | step 数 → 立即执行一段 run | CPU | 所有状态在同一 `Run` 中 | **保持多段状态语义**，执行配置形成明确 state machine |

多段 `run` 的已证实状态：

- 保留：box、原子位置/速度、potential vector、`time_step`、`global_time`；后续 `potential` 会继续添加 potential。
- 每段重新初始化：integrator/ensemble、MC、measurement preprocess。
- 每段结束清空：measurement property 列表；`Integrate::finalize()` 清除 fix/move/deform；`Velocity::finalize()` 清除 correct-velocity 请求；`Force::finalize()` 只重置部分 HNEMD 标志。
- `max_distance_per_step` 每段结束变为 `0.0`，而对象初值为 `-1.0` (`src/main_gpumd/run.cuh:57-73`, `run.cu:332-336`)。
- `thermo.out` 和非 `*` 的 XYZ 文件以 append 打开；`restart.xyz` 每次覆盖。
- `parse_run()` 用 `delta_T=(T2-T1)/number_of_steps`；该 commit 未拒绝 0 step，因此存在除零风险。DMG-MD 在声称兼容前需以 executable 测试确认 0/负值的可观察行为。

## 5. NEP 文件加载

`Force::parse_potential()` (`src/force/force.cu:75-213`) 读取首行选择类型。普通 NEP 在一张可见 GPU 时构造 `NEP`，多张可见 GPU 时自动构造 `NEP_MULTIGPU`。这不是 MPI rank-to-GPU 绑定语义。

`NEP::NEP()` (`src/force/nep.cu:100-395`) 的最小路径如下：

| 节点 | 输入 → 输出 | 位置 | 数据/所有权 | MPI 处置 |
| --- | --- | --- | --- | --- |
| 版本行 | `nep4[_zbl]` / `nep5[_zbl]` + type 数/symbol | CPU | type 表全局一致 | **提取共享 loader**；在所有 rank 校验 hash/参数一致 |
| ZBL 行 | inner/outer，可选 typewise factor | CPU | `ZBL` 参数结构 | **共享解析和数学实现** |
| cutoff/nmax/basis/lmax/ANN | 超参数 → `ParaMB`、descriptor dim | CPU | 多处固定最大值/放大 neighbor capacity | **共享并增强显式校验**；兼容差异需测试锁定 |
| ANN 与 descriptor 参数 | 文本标量 → float host vector | CPU | 参数布局随 NEP4/5/type 数变化 | **原样共享或提取公共库** |
| `get_descriptor_parameters_type_pair()` (`nep.cu:75-98`) | 原参数 → type-pair-major | CPU | 产生 kernel 直接使用布局 | **直接复用** |
| `GPU_Vector<float>::copy_from_host` | 参数 → GPU | H2D | 每个进程/设备一份只读参数 | **每 rank 一份**；上传后可常驻 |
| `update_potential()` | 参数 base → `w0/b0/w1/b1/c/q_scaler` 指针 | CPU | 指针指向设备 buffer 内部 | **小范围重构并复用**，避免重写公式 |
| workspace 分配 | N、dim、MN → `Fp/sum_fxyz/f12/NL` | GPU | stride 是全局 `N` | **重构 capacity/stride 为 local 域** |

注意：`Force::parse_potential()` 的字符串白名单包含若干 `nep3` 名称，但本 commit 的 `NEP::NEP()` 实际版本分支只接受 NEP4/NEP5 家族。这种入口/构造器不一致不能被记为“兼容”，见 open questions。

## 6. 第一次力计算

`Run::perform_a_run()` 先 `Integrate::initialize()` 和 `Measure::initialize()`，随后在 timestep 循环前调用一次 `Force::compute()` (`run.cu:211-242`)。因此：

1. 初始坐标先被 PBC wrap；
2. force、PE、virial 被清零；
3. 建表并计算 NEP；
4. 第 0 步 `compute1()` 使用这份初始 force；
5. 没有 step 0 的 dump，dump 条件使用 `(step+1)%interval`。

普通 `Force::compute()` 是 `src/force/force.cu:771-831` 的 overload：

| 节点 | 域/设备 | 输入 → 输出 | 全局/索引依赖 | MPI 处置 |
| --- | --- | --- | --- | --- |
| `Box::set_is_orthogonal()` | CPU | `cpu_h` → flag | 无原子索引 | **共享** |
| `gpu_apply_pbc` (`force.cu:424-459`) | GPU，全 `N` | position → wrapped position | 全局 array stride `N` | **提取 kernel，launch owned；迁移前定义边界归属** |
| `initialize_properties` (`force.cu:314-333`) | GPU，全 `N` | force/PE/virial → 0 | 3N/9N SoA | **提取并只清 owned 输出；依赖层工作区另清** |
| `Potential::compute` | GPU | type/position → PE/force/virial | 当前 vector 是全局体系 | **重构接口为 owned center + local neighbors** |

对于普通 NEP，`force.temperature += delta_T` 发生在 potential 调用之前 (`force.cu:803-805`)；常规 NEP不使用该温度，但 temperature-dependent NEP 的首次调用实际为 `T1+delta_T`。该模型不在 MVP。

## 7. 单个 MD timestep

### 7.1 时间积分前半步

`Integrate::compute1()` 位于 `src/integrate/integrate.cu:332-375`，转发到所选 `Ensemble::compute1()`。NVE 的实现在 `src/integrate/ensemble_nve.cu`；公共 velocity-Verlet kernel 在 `src/integrate/ensemble.cu:176-214`：

```text
v_i ← v_i + (Δt/2) f_i/m_i
r_i ← r_i + Δt v_i       （compute1）
```

kernel 以 `mass.size()` 为全 `N`，位置/速度/力是 3N SoA。DMG-MD 必须只对 owned atoms launch；ghost 坐标由 halo exchange 更新，绝不能积分。

基础 NVT 推荐首先复刻 `nvt_ber`：`Berendsen::compute1/compute2()` 位于 `src/integrate/ensemble_ber.cu:178-234`。它复用同一 VV，第二半步后用全局瞬时温度计算缩放因子。其优点是确定性且 MPI 只需全局归约；它并非严格的 canonical sampler，不能把统计物理性质描述成与 NHC/Langevin 等价。

### 7.2 边界、迁移与 halo

GPUMD 当前没有 MPI 原子迁移。`Force::compute()` 在每次力前调用 `gpu_apply_pbc`。DMG-MD 的对应顺序应为：

```text
compute1(owned)
→ wrap 全局周期边界并确定新 owner
→ 迁移 owned 原子（携带 global ID、动态状态、group）
→ 重建或更新 position/type halo
→ 邻居/NEP
```

对于 triclinic box，domain 坐标和归属应在 fractional 坐标中定义。GPUMD `Box::apply_mic()` (`src/model/box.cuh`) 的 triclinic 分支使用逆矩阵和 `nearbyint`；正交快速分支只做一次 `±L` 修正，依赖坐标/位移已经接近主盒。

### 7.3 邻居、descriptor 与 force

large-box 顺序由 `NEP::compute_large_box()` (`src/force/nep.cu:978-1138`) 直接证明：

| 顺序 | 调用 | 中心域 | 主要结果 | MPI 关键点 |
| ---: | --- | --- | --- | --- |
| 1 | `Neighbor::find_neighbor_global()` | 全 `N` | cutoff+skin 的完整有向 ELL 表 | 重构为 owned/依赖中心 + local 邻居 |
| 2 | `find_neighbor_list_large_box` | `[N1,N2)` | typewise radial/angular ELL | 中心和邻居索引都必须是 local index |
| 3 | `find_descriptor` | `[N1,N2)` | center PE、`Fp`、angular sums | 需要邻居 position/type；输出属于中心 |
| 4 | `find_force_radial` | `[N1,N2)` | center radial force/virial | 同时读取 `Fp(center)` 和 `Fp(neighbor)` |
| 5 | `find_partial_force_angular` | `[N1,N2)` | 每条 directed edge 的 `f12` | 输出按 `slot*N+center`；邻居 owner 需要可访问反向项 |
| 6 | `Potential::find_properties_many_body` | `[N1,N2)` | center angular force/virial | 查找 neighbor row 中的 reverse edge，读取两侧 partial |
| 7 | `find_force_ZBL`（可选） | `[N1,N2)` | center PE/force/virial | 使用 angular neighbor list；无 ghost 写 |

large-box 的每个最终 kernel 只写中心原子的 force/PE/virial，没有 force atomic scatter。它适合改成 owned-center gather，但并不意味着 halo 等于 cutoff：radial force 读取邻居中心的 `Fp`，angular force 读取邻居中心的反向 directed partial。通信证明见 [kernel-inventory.md](./kernel-inventory.md)。

small-box 由 `NEP::compute_small_box()` (`nep.cu:1141-1267`) 和 `src/force/nep_small_box.cuh` 实现。它显式枚举扩展周期镜像，radial/angular/ZBL force kernel 使用 `atomicAdd` 同时写中心与邻居。这条路径映射到 owned/ghost 时会产生 ghost partial force，必须 reverse exchange，而且同一 global atom 的多个周期 image 必须可区分。第一实现切口不应选择它。

### 7.4 第二半步与 thermo

`Integrate::compute2()` (`src/integrate/integrate.cu:377-404`) 调用 ensemble 第二半步。NVE 执行：

```text
v_i ← v_i + (Δt/2) f_i/m_i
Ensemble::find_thermo()
```

`Ensemble::find_thermo()` (`src/integrate/ensemble.cu:432-673`) 的 GPU reduction 遍历全 `N`，累积动能、PE 和六个 stress 分量；温度分母使用 `3*N_temperature*kB`。DMG-MD 必须：

- kernel 只累积 owned atoms；
- PE/virial 必须只有 owner 贡献一次；
- 用 MPI all-reduce 得到全局标量；
- 温度自由度/固定组计数使用全局一致规则；
- 在 `nvt_ber` 缩放前完成归约，然后只缩放 owned velocity。

### 7.5 measurement 与输出

`Measure::process()` (`src/measure/measure.cu`) 按 `run.in` 中 property 创建顺序执行。MVP 输出路径：

| 命令 | 实现 | 时机 | MPI 处置 |
| --- | --- | --- | --- |
| `dump_thermo` | `src/measure/dump_thermo.cu` | `(step+1)%interval==0` | 全局 scalar 已归约，rank 0 append `thermo.out` |
| `dump_xyz` | `src/measure/dump_xyz.cu` | 同上 | gather owned records，按 global ID 排序，由 rank 0 格式化 |
| `dump_restart` | `src/measure/dump_restart.cu` | 同上 | gather 完整可恢复记录，rank 0 覆盖 `restart.xyz` |

该参考版本已删除 `dump_position`、`dump_velocity`、`dump_force` 和 `dump_exyz`，dispatcher 会给出迁移到 `dump_xyz` 的错误 (`run.cu:392-428`)。DMG-MD 应识别这些词并返回同等级、明确的 unsupported/migration 错误，不应伪装成旧版本兼容。

## 8. finalize 与 restart

一段 run 完成后，`Measure::finalize()` 调各 property 的 `postprocess()` 并清空列表；随后各模块 finalize (`run.cu:320-336`)。关键语义：

- `Dump_Thermo::postprocess()` 和 `Dump_XYZ::postprocess()` 关闭文件；非分离 XYZ 已 append。
- `Dump_Restart::process()` 每次用 `w` 打开固定 `restart.xyz`，输出 species、wrapped position、mass、velocity 及可选全部 group labels。
- restart 不保存 force、PE、virial、thermostat chain、RNG 状态、`global_time` 或显式 global ID。
- GPUMD 没有“读 restart”命令；`restart.xyz` 是可作为下一次 `model.xyz` 使用的 extended XYZ。文件重命名/复制流程不在代码中自动完成。

DMG-MD 为保持可见格式兼容，可以继续输出相同列；但内部必须用 stable global ID 确定输出顺序。跨 rank 数恢复的 thermostat/RNG 连续性在该 GPUMD 格式中没有表达，属于 **UNKNOWN/产品决策**，不能声称 bitwise continuation。

## 9. 节点处置总表

| 层 | 保持 | 提取公共库 | owned/ghost 重构 | 删除/不用 |
| --- | --- | --- | --- | --- |
| 文本格式 | model/NEP/run 语法、单位、错误 | parser、NEP loader、formatter | rank 0 parse/broadcast、分布式 I/O | 各模块自行二次扫描 `run.in` |
| 数值核心 | NEP 参数布局和公式 | descriptor/radial/partial/many-body/ZBL kernels | local stride、中心范围、通信接口 | `NEP_MULTIGPU` 全局 GPU0 编排 |
| MD | VV 更新次序、thermo 物理定义 | owned-domain VV/reduction kernel | migration、halo、MPI all-reduce | PIMD/MC/未支持 ensemble |
| 输出 | 文件名、列、单位、触发步 | formatter | gather/sort/global reduction | 已删除的旧 dump 实现 |

## 10. 关键结论

1. 最小数值主链是 `VV half → migrate/PBC/halo → directed full neighbor → descriptor → radial + angular partial + many-body gather + optional ZBL → VV half → global thermo → dump`。
2. large-box kernel 家族最适合作为首个复用对象；其 center-owned 写模型可避免 reverse **force** exchange，但需要交换邻居中心的 descriptor/partial 中间场，或用经证明的更深位置 halo重算这些场。
3. small-box 不是简单 fallback：它改变为 Newton atomic scatter 和显式多 image 语义，通信及身份模型完全不同。
4. 当前 `NEP_MULTIGPU` 只证明了 NEP 存在两层依赖窗口；它仍由 GPU 0 持有全局 `N`、全局 cell list 和最终全局输出，不能直接映射成 MPI runtime。

## 11. DMG-MD 最小源码闭包

历史上的独立源码清单同时混入了早期设计建议、已删除实现和当前事实。
本节只保留仍生效的源码边界；历史取舍由 Git 和
[架构决策](./architecture-decisions.md) 保存。

| 层 | 当前文件/目录 | 责任 |
| --- | --- | --- |
| compat core | `src/gpumd_compat/` | tokenizer、Box、GPU_Vector、neighbor、Potential、NEP/NEP-ZBL loader 与 kernels |
| input | `include/dmgmd/model.hpp`、`include/dmgmd/run_ir.hpp`、`src/model_parser.cpp`、`src/run_parser.cpp` | model/run 兼容解析、typed IR 和错误位置 |
| runtime | `src/runtime.cu`、`include/dmgmd/runtime.hpp` | device atoms、积分、thermo、输出与 replicated NEP adapter |
| MPI | `src/mpi_runtime.cu`、`include/dmgmd/mpi_runtime.hpp`、`include/dmgmd/partition.hpp` | lifecycle、device binding、collectives、owned range 和诊断 |
| entry | `src/dmgmd_main.cpp` | parse/validate 后启动 runtime，协调错误退出 |

构建必须显式列出这些文件，不得通过 glob 把 GPUMD 的其他模块带入。当前产品范围排除 PIMD、
MC、phonon、minimize、deposition、PLUMED、长程静电、其他势函数、高级 ensemble 和完整
measurement 系统；对应命令仍应识别并明确报 unsupported。

未来 domain decomposition 所需的 migration、halo、intermediate exchange 和 local neighbor
builder 不属于当前闭包，统一由 [域分解计划](../plans/domain-decomposition.md) 管理。
