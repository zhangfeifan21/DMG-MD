# 域分解与 halo 通信协议（设计稿）

类别：实施中计划。状态：IN PROGRESS——M0、M1 已于 2026-09-15 实施并验收；M2a 已于
2026-09-18 实施并通过专属验收矩阵（见 [status/current.md](../status/current.md)），
其生效合同已并入 [replicated-mpi.md](../standards/replicated-mpi.md) 的 M2a 节与
[data-layout.md](../standards/data-layout.md)；M2a nightly 已于 2026-09-18 手动执行并
通过，100000-step 长程 release 矩阵仍未执行。
M2b 及之后仍待后续审批。

本文档规定 DMG-MD 从 replicated-data 原型演进为 owned/ghost 域分解
runtime 的数据面协议。M0、M1 与 M2a 已按维护者指令实施；实测记录见
[status/current.md](../status/current.md)。本文保留 M2a 的设计依据与 M2b 起的
未实施合同；生效合同以 standards 为准。

前置阅读：[replicated-mpi.md](../standards/replicated-mpi.md)（现行协议）、
[risk-and-backlog.md](./risk-and-backlog.md)（风险与待办登记）、
[kernel-inventory.md](../standards/kernel-inventory.md)（kernel 证据）。

## 1. 动机与现状核算

### 1.1 GPUMD 参考 `NEP_MULTIGPU` 的反例与证据价值

锁定参考 commit 的单进程多卡实现（`gpumd-reference/src/force/nep_multigpu.cu`）每次力调用
执行：

1. GPU 0 建全局 cell list 并把 prefix sum 拷回 host（`nep_multigpu.cu:1457-1474`）；
2. 串行地把每个 GPU 的坐标窗口（owned 两侧各 4 个 `rc/2` cell ≈ 2rc）从 GPU 0
   `gpuMemcpyDeviceToDevice` 分发（`nep_multigpu.cu:1552-1582`，pack kernel
   `distribute_position` `nep_multigpu.cu:1249-1281`）；
3. 各 GPU 在自有 stream 上分相位计算（`nep_multigpu.cu:1584-1755`），逐设备
   `gpuDeviceSynchronize`（`nep_multigpu.cu:1757-1762`）；
4. 串行地把各 GPU **owned 段**的 force/PE/virial 拷回 GPU 0（`nep_multigpu.cu:1764-1802`）。

velocity-Verlet 不在多卡路径内分担：单进程主循环（`gpumd-reference/src/main_gpumd/run.cu`）
在 `integrate.compute1`（`run.cu:257`）与 `integrate.compute2`（`run.cu:291`）之间调用
force（`run.cu:273-283`）；两个半步都由 `Ensemble::velocity_verlet`
（`gpumd-reference/src/integrate/ensemble.cu:348`，kernel `gpu_velocity_verlet`
`ensemble.cu:113`、重载 `ensemble.cu:176`）以全部原子数 launch，且 `NEP_MULTIGPU::compute`
返回前把 device 切回 0（`nep_multigpu.cu:1762`）。因此该设计中 **GPU 0 独自承担全部积分**，
其余 GPU 是无状态的力加速器；每次力调用都有一轮全量窗口分发 + owned 段回收。

该实现的价值是**窗口数学的数值证据**（§4 引用），其编排（GPU 0 全局持有、串行分发/回收、
单方向 slab、16 GPU 上限 `nep_multigpu.cuh:159`）被 kernel-inventory §7 与 AGENTS.md 明确
列为不可复用。DMG-MD 已另行解决积分去中心化（§1.2）。

### 1.2 DMG-MD 现行 M1 协议与开销

M1 已把积分、thermo 与输出权威从 M0 的连续 `OwnedRange` 改为空间 slab 派生的
global-ID/index-list 所有权；原子跨 slab 时只做逻辑所有权移交。但数据面仍是
**replicated-full**：每个 rank 保留 N 个槽位，每步仍在完整 N 个中心上执行 ordinary NEP，
当前没有 ghost、halo 或点对点邻居通信。精确时序和字节合同以
[replicated-mpi.md](../standards/replicated-mpi.md) 为准。

无 adaptive timestep、输出和所有权变化的 P>1 普通步为：

| 步骤 | 权威域 | 通信 |
| --- | --- | --- |
| VV first half、position/unwrapped 更新 | current owned index list | — |
| position 复制 | current ownership | indexed Allgatherv：输入 `24N`、输出 `24NP` |
| ordinary NEP | replicated-full，`N1=0, N2=N` | — |
| next owner map 一致性门 | 固定尺寸 hash | Allreduce：输入/输出各 `16P` |
| VV second half、thermo、控温 | next owned index list | thermo Allreduce：输入/输出各 `64P` |

owner map 发生变化时，在切换 epoch 前另以旧 ownership Allgather 最新 velocity；启用
unwrapped 时连同 unwrapped 一起同步。`correct_velocity` 触发步另有 velocity Allgatherv +
Bcast；输出步按 owned index list Gatherv 并在 rank 0 scatter 回 replicated slot 顺序。

因此 M1 的两个结构性开销仍然存在：

1. **每 rank 对全体系 N 个原子做完整 NEP**（`nep_kernel_centers=replicated-full`）——多卡
   不减少每卡计算量；
2. **每步一个 O(N) position Allgatherv**，迁移/修速/输出步还有额外 O(N) collective——
   多节点流量仍随 P 放大。

M0 仅删除无消费者的逐步 velocity Allgatherv（§2）；M1 只建立空间所有权与正确的逻辑
迁移协议（§3）。从 M2 起才以 owned+ghost 本地布局和点对点 halo 交换替换 replicated-full
数据面，使每 rank 的 NEP 工作量趋向 `N/P + halo`、通信收缩到相邻 slab 边界带（§4–§8）。

## 2. M0：删除每步 velocity Allgatherv

状态：已实施（2026-09-15）。现行合同与字节表以
[replicated-mpi.md](../standards/replicated-mpi.md) 为准；本节的行号引用基于实施前代码，
仅作证据保留。

### 2.1 消费者清单（证据）

对复制态（非 owned）velocity 的全部读取点逐项核查：

| 读取点 | 位置 | 是否读非 owned |
| --- | --- | --- |
| VV first/second half | kernel `runtime.cu:311-339`，launch `runtime.cu:972-982, 986-991` | 否，`[begin,end)` 即 owned |
| 自适应时间步 | `adaptive_time_step` `runtime.cu:847-871` | 否：整数组 D2H（`runtime.cu:856-857`）但循环只遍历 owned（`runtime.cu:860-865`） |
| thermo 动能求和 | `find_owned_thermo_sums` launch `runtime.cu:457-459` | 否，owned 区间 |
| Berendsen 缩放 | `scale_owned_velocity` `runtime.cu:995-1008` | 否，owned 区间 |
| 输出快照 | `gather_owned_snapshot` `runtime.cu:224-253` | 否，Gatherv 只取 owned 切片 |
| NEP 力计算 | `NepForce::compute` `runtime.cu:486-496` | 否，NEP 只读 position |
| **`correct_velocity`** | `correct_device_velocity` `runtime.cu:873-908` | **是**：root 把完整复制态 position+velocity D2H（`runtime.cu:882-885`）后对全体系做 CPU 修正（`runtime.cu:886-903`） |

初始化不受影响：无输入速度时 root 生成后 `broadcast_doubles` 完整数组
（`runtime.cu:1081-1088`）；`velocity` 命令同（`runtime.cu:1111-1118`）。段首初始力
（`runtime.cu:956`）只依赖 position。

### 2.2 M0 协议

1. 删除 `run_segment` 末尾的每步 velocity Allgatherv（`runtime.cu:1009-1010`）；
2. **例外**：本步触发 `correct_velocity`（`step % interval == 0`，`runtime.cu:963-965`）时，
   在调用 `correct_device_velocity` **之前**补一次 velocity Allgatherv，恢复复制态供 root
   读取；修正后的 `broadcast_device`（`runtime.cu:905-907`）本就会重新复制完整数组，段内
   后续步骤无需再同步；
3. position Allgatherv、thermo Allreduce、输出 Gatherv 全部保持不变。

### 2.3 通信量变化与验证要求

| 情形 | 每步 MPI input | 每步 MPI output |
| --- | ---: | ---: |
| 现行（普通步） | `48N + 64P` | `48NP + 64P` |
| M0（普通步） | `24N + 64P` | `24NP + 64P` |
| M0（correct_velocity 触发步，不含既有 Bcast） | `48N + 64P` | `48NP + 64P` |

普通步通信量减半（HostStaged 的 D2H/H2D 同步减半）。验证：

- M0 只删除无消费者的集合通信，**轨迹与输出文件必须与现行实现逐字节一致**；现有
  1/2/4-rank × HostStaged/CudaAware differential 矩阵（`tests/mpi/run_mpi_differential.py`）
  不改容差直接复用；
- `DMGMD_COMM` 每步记录（`mpi_runtime.cu:927-941`）的 velocity 字段随协议更新，
  differential 测试中对通信记录的断言同步修订；
- `docs/standards/replicated-mpi.md` 的每步顺序与字节表
  须随实现一并修订。

附注（非必须）：`adaptive_time_step` 在启用 `max_dist` 时每步把完整 3N velocity D2H
（`runtime.cu:856-857`）却只读 owned——可改为只拷 owned 切片，数值不变，属独立小清理。

## 3. 所有权模型：空间 slab 分解

### 3.1 从下标分块到空间 slab（M1 已实施）

M0 的 `balanced_owned_range` 按全局下标均衡切块，已在 M1 中随
`include/dmgmd/partition.hpp` 一起退役。M1 实际落地的所有权表示（现行合同见
[replicated-mpi.md](../standards/replicated-mpi.md)，实现为
`include/dmgmd/spatial_ownership.hpp`）：

- 沿 partition 轴把 box 切成 P 个等宽 fractional half-open slab，每个 rank 拥有一个
  slab；坐标到 owner 的映射是独立、可 CPU 单测的纯函数（内部边界属右侧 slab，`s=0`
  属第一个 slab，精确 `s=1` 归最后一个 slab，周期越界与 `wrap_positions` 的
  `<0/+1`、`>1/-1` 单次调整一致）；
- 所有权是 **global_id 上的逻辑归属**：`owner_by_slot[N]` 单一事实源 +
  owned index list（按 global_id 升序）+ 派生 mask + `slot_of_global_id` 显式置换；
  不创建 owned+ghost 本地数组、不压缩 local_count、不重排 per-atom 数组（M2 内容）；
- partition 轴选择：box 最长边，tie 规则与 `nep_multigpu.cu:1438-1446` 的级联一致
  （y 胜 x/y、y/z 平手，x 胜 x/z 平手，立方盒选 y）；GPUMD `potential` 第三参数
  （x/y/z）仍被 DMG-MD parser 拒绝（`src/run_parser.cpp`），M1 不恢复该语法；
- M2a **保持 M1 的等宽 fractional slab 边界**，不为了仿照 `NEP_MULTIGPU` 而改成
  `rc/2` cell 对齐。参考实现的 10-bin/5rc 约束来自其窗口分发编排，并不是当前
  `gpumd_compat::Neighbor` 的接口合同；后者的 Verlet 构建半径是 `rc + skin`，cell-list
  尺度也由该半径决定。M2a 应按 §4 实际推导出的坐标 halo 深度 `d_coord` 做物理宽度
  守卫：`slab_width >= d_coord` 才能只与直接左右邻居交换。除此之外还必须通过现行
  `NEP::compute` 的 large-box 判据。任一判据不满足时不得改变已有输入的可运行性，而是
  明确记录原因并回退到 M1 replicated-full 路径。

### 3.2 数据面计数与身份

`AtomCounts`（`include/dmgmd/model.hpp:10-19`）已按 R2 的要求定义
`global_count / owned_count / ghost_count / local_count() = owned + ghost`，且
`HostAtoms` 已显式声明 "global_count is metadata and is never an addressing stride"
（`model.hpp:23-25`）。M1/M2 落实为：

- `local_count` 是所有 SoA per-atom 数组的唯一 stride（position/velocity/force/
  potential/virial/global_id/type/mass，`runtime.cu:154-212` 的 `DeviceAtoms`）；
- `global_id`（64-bit，源自输入行号，`model.hpp:32`、`runtime.cu:201`）跨迁移稳定，
  是输出排序（rank 0 `output_order`）与通信边身份的基础；
- 每个 local 槽位带 owner rank 与 ghost image/shift 元数据（ghost 槽位）；
- NEP 中间量 `Fp / sum_fxyz / f12x,y,z / NN / NL`（`src/gpumd_compat/nep.cu:398-410`
  一带分配）全部改为 `local_count` stride。

现有 M1 `run_replicated` 不删除，作为 P=1 和不满足 M2a eligibility 的兼容路径保留；新增
local-domain 路径才以本节不变量替代其中的 "no ghosts" 守卫。P=1 必须始终走现有路径，
退化为 `owned = N、ghost = 0`，输出与现行单 rank 路径逐字节一致（§10 验收门）。

### 3.3 box 范围（第一切口）

M2a local-domain 路径只支持**正交全周期大盒**。启动时由 runtime dispatcher 在读取 box 和
potential 元数据后判断：P=1 固定走现有路径；P>1 且 box/半径/slab 宽度满足 M2a 判据时走
local-domain；其余当前 M1 已支持的输入继续走 replicated-full，并输出机器可读 fallback
原因。不得把原本能运行的小盒改成报错。triclinic / 非周期输入在 P>1 下继续保持现行明确
`unsupported` 行为，不得静默投影；拓展其支持范围不属于 M2a。

每次启动由 rank 0 输出一条稳定可解析记录，例如
`DMGMD_DOMAIN mode=m2a|m1-fallback axis=... d_dep=... d_coord=... reason=...`；M2a 还应输出
各 rank 的 owned/dependency-ghost/coordinate-only-ghost/local_count，供验收断言实际命中
local-domain，而不是只看到数值 PASS。

### 3.4 本地排序稳定性

本地数组按 cell 顺序排列；cell 内按 `global_id` 排序（Q15）。这使 ELL 邻居表行排序
（`gpu_sort_neighbor_list`，`src/gpumd_compat/neighbor.cuh:128-152`）对 rank 数稳定，
减弱 R4/R16 的归约顺序分叉。排序改变与 GPUMD 的逐位差异须先量化并写入容差层级
（§10）。

## 4. halo 深度证据链

### 4.1 两跳依赖（R1 的 kernel 证据）

NEP large-box 力计算对位置的依赖是两跳的：

1. **第一跳**：owned 中心 n1 的 radial force 读邻居的 `Fp[n2]`
   （`src/gpumd_compat/nep.cu:763-764`：`g_Fp[N*n + n1]` 与 `g_Fp[N*n + n2]` 同时进入
   力累加）；many-body force 经 `find_properties_many_body`
   （`nep.cu:1153-1163`，wrapper `src/gpumd_compat/potential.cu:208-226`）读反向
   directed partial `f12(n2,n1)`（kernel `gpu_find_force_many_body` `potential.cu:79`，
   反向 partial 读取循环 `potential.cu:118-164`，从 n2 的邻居行查 n1）。
2. **第二跳**：`Fp(n2)` 与 `f12(n2,·)` 由以 n2 为中心的 descriptor/partial 相位产生
   （`find_descriptor` `nep.cu:521`/launch `nep.cu:1086`；`find_partial_force_angular`
   `nep.cu:814`/launch `nep.cu:1133`），它们需要 **n2 的邻居** 的位置，即距 owned
   2rc 内的第三原子。

结论：owned 原子的力需要覆盖"力邻居 cutoff + 该邻居自身 descriptor/partial cutoff"的
两跳坐标闭包；统一 cutoff 的无 skin 简化才是 **owned ± 2rc**。仅交换一跳坐标 halo 会
产生"看似连续、系统性错误"的边界力（`risk-and-backlog.md` R1 失败模式）。

### 4.2 三层窗口定义

`NEP_MULTIGPU` 的窗口布局（`gpumd-reference/src/force/nep_multigpu.cuh:42-55`、分区计算
`nep_multigpu.cu:1476-1544`，`docs/standards/kernel-inventory.md` §7 的映射表）给出三层域的
数值语义。本项目只复用其依赖关系证据，不复用 cell 对齐或分发编排：

| 域 | 窗口（不含 skin，统一 cutoff 简写） | NEP 工作内容 | MPI 概念 |
| --- | --- | --- | --- |
| 力域 `[N1,N2)` | owned | radial force（`nep_multigpu.cu:1658`）、many-body（`:1707`）、ZBL（`:1731`） | owned atoms |
| descriptor 域 `[N4,N5)` | owned ± rc | 邻居表（`:1603`）、descriptor/Fp（`:1631`）、angular partial（`:1683`） | dependency centers |
| 坐标域 `[0,N3)` | owned ± 2rc | 仅可寻址（descriptor 域的邻居候选） | coordinate ghosts |

### 4.3 skin 与重建的修正

`NEP_MULTIGPU` 每步重建邻居表，窗口精确取 rc/2rc。DMG-MD 的 `gpumd_compat::Neighbor`
使用 Verlet skin：skin = 1 Å，任一原子相对参考位置位移 > skin/2 即重建
（`src/gpumd_compat/neighbor.cu:407-414`），构建半径 `rc + skin`
（`neighbor.cu:454`）。因此：

- 定义 `R_force(i,j)` 为现行 large-box kernel 中 owned 中心 i 的某项力实际会消费邻居 j
  的最大距离，`R_dep(j,k)` 为 descriptor/partial 中心 j 实际会消费候选 k 的最大距离；
  两者必须忠实包含 typewise 过滤和 ZBL 当前所复用的邻居表语义；
- **dependency/descriptor 域深度**
  `d_dep = max(i,j) R_force(i,j) + skin`；
- **坐标 ghost 深度**
  `d_coord = max(i,j,k)[R_force(i,j) + R_dep(j,k)] + 2*skin`。两次 skin 分别属于
  i-j 和 j-k 两张缓存邻居边；若成员只在重建时重估，写成 `+ skin` 会遗漏在重建间隔内
  新进入第二跳 cutoff 的候选；
- ghost **位置每步刷新**（每原子 24 B），type/mass/group 随成员变化交换；
- 重建判据是全局 OR：任一 rank 的 owned 原子超阈值 ⇒ 全体重建（R11）；迁移
  完成后强制重建并作废全部 NEP 中间量（`risk-and-backlog.md` §5 不变量 7）。

### 4.4 有效半径的确定（R12，M2a 实现要求）

不能只从一个名义 `rc_max` 猜 halo：radial pair cutoff 是 typewise 平均
（`nep.cu:748` `rc = (rc_radial[t1] + rc_radial[t2]) * 0.5f`）；当前 large-box filter 先以
radial cutoff `continue`，再填 angular list，ZBL 也消费该 angular list。M2a 必须在 potential
加载后枚举实际类型对/三元组，按**现有 kernel 的有效消费路径**计算 §4.3 的 `R_force`、
`R_dep`、`d_dep`、`d_coord` 并写入启动日志。M2a 不借机改变 `Ra > Rr` 或 ZBL 被现有列表
截断的数值语义；这些语义的产品裁决仍属于 B2。若无法证明某分支的有效上界，就 fail closed
到 M1，而不是低估 halo。pair 平均必须复现 kernel 的 float `(a+b)*0.5f` 舍入；host
上界取该结果与 loaded-float 操作数精确平均的较大者，kernel 向上舍入时再向 `+inf`
扩一个 double ULP，不能用纯 double 平均低估 mixed-type cutoff。

### 4.5 双 oracle 策略（Q4 已确认）

按 Q4 维护者决策："先实现保守两跳 oracle，再以其验证 `Fp` + partial staged protocol"：

- **M2a 保守两跳深位置 halo**（本文档主线）：交换 owned ± `d_coord` 的坐标，
  各 rank 对 descriptor 域做冗余计算（含 halo 中心的 descriptor/partial），力域只算
  owned。**不需要任何中间量通信**，many-body 反向边查找全在本机（局部整数下标有效，
  无跨 rank 边匹配问题，Q5 的边键仅在 M2b 出现）。
- **M2b 分阶段交换**（后续可选）：坐标 halo 只到 `d_dep`，另交换 halo 中心的
  `Fp`/`sum_fxyz`/directed partial。通信字节数未必更少（`Fp` 每原子
  `(n_max_radial+1) + (n_max_angular+1)(L_max+1)` 个 float，可能大于深层坐标带），
  但消除 halo 冗余 descriptor 计算。**只有与 M2a 逐原子 force/virial 等价后才可作为
  生产协议**（Q4 验收条款）。

## 5. NEP 中心分片

### 5.1 现状与目标

现行 `compute_large_box`（`src/gpumd_compat/nep.cu:1027-1187`）所有相位共用同一中心区间
`[N1,N2)`（grid 定义 `nep.cu:1037`）：邻居表（launch `nep.cu:1045`，kernel `nep.cu:464`）、
descriptor（`nep.cu:1086`）、radial force（`nep.cu:1112`）、angular partial
（`nep.cu:1133`）、many-body（`nep.cu:1153-1163`）、ZBL（`nep.cu:1166-1186`）。
分片目标（复用优先级遵循 kernel-inventory §9 第 2/3 条，只做小范围参数化，不改数学）：

1. `Potential` 基类（`src/gpumd_compat/potential.cuh:64-65` 现有 `N1/N2`）增加
   descriptor 域成员 `ND1/ND2`；默认 `ND1 = 0, ND2 = N`；
2. 邻居表、descriptor、angular partial 以 `[ND1,ND2)` 为中心区间 launch；radial force、
   many-body、ZBL 保持 `[N1,N2)`；
3. 所有以全局 `N` 为 stride 的中间量地址（如 `g_NL[N*i1 + n1]` `nep.cu:736`、
   `g_Fp[N*n + n1]` `nep.cu:763-764`）改为 `local_count` stride——这些 kernel 本就把
   N 作为参数传入，参数化即可；
4. 单 rank 且 `ND = N1..N2 = 0..N` 时，所有 launch 配置与现行逐位一致（§10 退化验收门）。

`NepForce` 构造器中强制复制态的 `N1 = 0; N2 = N`（`runtime.cu:482-483`）在 M2 改为
`N1 = 0; N2 = owned_count; ND1 = 0; ND2 = owned + dependency_ghost_count`，其注释引用的完整性证明由
本协议替换：descriptor 域覆盖 `[N4,N5)` 后，`Fp(n2)` 与反向 partial 全部本机可得，
`nep_N1_N2_shard_complete` 的启动记录随之改写。

M2a 不得靠每次 local_count 改变时重新解析 potential。参数加载与按原子数分配 workspace
必须可分离（或等价地支持 deferred workspace）：potential 只解析一次并导出上述 domain
半径/large-box eligibility；确定模式后才按 `local_count` 分配/重建 workspace 并显式作废
neighbor cache；domain compute 接收 force/dependency 中心域。eligible M2a 路径不得为了判定
模式而在 GPU 上先持久分配一份 global-N atom/NEP scratch。现有 `compute` 和 P=1 launch 路径
保持不变。逻辑 `local_count` 必须作为独立参数传入 domain compute/rebuild，不能从为兼容
零字节分配而填充过的 `GPU_Vector::size()` 反推；所有空中心域（含 owned/ghost 都为零的
真正空 rank）必须在 launch 前安全短路，不能访问填充元素或生成零/负 grid。

### 5.2 邻居构建器的域分离（R8）

现行 `gpu_find_neighbor_ON1` 要求候选 n2 也在中心域 `[N1,N2)` 内
（参考实现 `gpumd-reference/src/force/neighbor.cu:144`，`risk-and-backlog.md` R8）。新接口必须
分离**中心域**（写 NL 行的原子，`[ND1,ND2)`）与**候选域**（可进入邻居行的原子，全部
local 槽位 owned+ghost）。ELL 容量按 `neighbor.cu:478-483` 的 `(rc+skin)^3/rc^3` 放大
逻辑对 local 体系重算（Q13 的越界行为保持"安全报错"）。

domain 路径的每个邻居行按候选 `global_id`（相同 ID 时再按 image）确定性排序，但 NL 中仍
保存 local index。many-body 的反向边二分查找必须用同一 global-ID/image 比较键，不能继续
假设 local index 顺序；legacy P=1/M1 路径的 local-index 排序不改，以保护逐字节退化门。

### 5.3 输出清理与 `neighbor.out` 副作用

`clear_owned_properties`（`runtime.cu:290-309`，launch `runtime.cu:492-493`）保持清
`[0, local_count)`：ghost 槽位的 NEP scratch 写入无害，输出 Gatherv 只取 owned 切片
（`runtime.cu:224-253`），不触发 R6 的 ghost 所有权问题。

`compute_large_box` 每 1000 次调用 append `neighbor.out` 的隐式 D2H 副作用
（`nep.cu:1063-1078`，R25/Q18）：M2a 保留单文件/单记录语义。各 rank 只计算本地
dependency 中心的最大邻居数，经 `MPI_MAX` 聚合后仅 rank 0 使用既有格式写一条记录；
采样点位于本次 typewise 表生成之后，空 dependency 域贡献零；非 root 不得 append。
记录若发生在 step force，两个 scalar MPI_MAX 必须进入该步 `CommunicationVolume`；
fallback/P=1 继续走现有行为。

## 6. 每步协议（M2 目标时序）

替换 `docs/standards/replicated-mpi.md` 的复制态协议。slab 分解下每方向的通信对象 ≤ 2 个相邻
rank（PBC 下首尾互为邻居；P=1 时全部为空操作）：

1. **（correct_velocity 触发步）** 按 `(global_id, owned position, owned velocity)` gather
   到 root，root 按 global ID 恢复全局顺序并复用现有 CPU 修正（静态 mass/group 元数据可
   保留一份 host 全局副本），再按 current owner scatter 回各 rank 的 owned local 槽位。
   不得把全局 `3N` 数据写入 `local_count` device array，也不得复用要求全局 stride 的 M1
   indexed Allgatherv；
2. 自适应时间步：owned max|v| host Allreduce（`runtime.cu:847-871`，不变）；
3. VV first half，仅 owned（+ unwrapped 跟踪 `runtime.cu:969-981`，不变）；
4. owned 位置 wrap 进全局 box（`wrap_positions` kernel `runtime.cu:255-288` 改为仅对
   owned 槽位 launch；ghost 存源端 wrap 后坐标，距离经 `apply_mic` 修正，与 GPUMD
   存储 wrap 坐标 + MIC 的惯例一致）；
5. **迁移检查**：owned 原子跨出本 slab ⇒ 执行 §8 迁移，强制邻居表重建；
6. **halo 交换**：向相邻 rank 发送落入其坐标 ghost 带的 owned 原子位置，接收并写入
   本 rank ghost 槽位（§7）；
7. **本地分片 NEP**：`[全局 OR 重建判定]` → 邻居表（中心 `[ND1,ND2)`）→ descriptor
   → radial force（`[N1,N2)`）→ angular partial（`[ND1,ND2)`）→ many-body
   （`[N1,N2)`）→ 可选 ZBL（`[N1,N2)`）；
8. VV second half，仅 owned；
9. thermo：owned 求和 + 8-double Allreduce（`runtime.cu:446-469`，不变；ghost 的
   PE/virial scratch 不参与）；
10. Berendsen 缩放，仅 owned（不变）；
11. **无每步 velocity 集合通信**（M0 已删）；
12. 输出步：使用新的 local-owned-prefix gather，同时携带 global ID；rank 0 按 global ID
    恢复 N 条记录后复用现有 formatter。M1 的 indexed gather/scatter 依赖 replicated global
    slot/stride，不能直接用于 M2a。

进入第一个 `run` 前的初始力同样必须先完成 owned local 初始化、halo topology/position 交换和
NEP workspace 建立；多段 run 延续同一 domain state，不得在段间重新膨胀为 replicated device
arrays。

happens-before 链（kernel-inventory §8）：迁移完成 → halo 到达 → [重建 OR 判定] →
descriptor 完成 → radial/partial/many-body → VV second half → thermo Allreduce →
thermostat → measurement。第一实现继续只用默认 stream + 阻塞语义（Q40：引入非默认
stream/graph 前重新审计 event 协议）。

### 6.1 通信后端扩展

`MpiRuntime` 现只有集合通信（`src/mpi_runtime.cu` 全文无点对点）。halo 交换需要新增：

- `MPI_Isend/MPI_Irecv + MPI_Waitall` 的点对点封装，语义纳入
  HostStaged（pack kernel `mpi_runtime.cu:163-177` → pinned → p2p → H2D → unpack
  `mpi_runtime.cu:179-191`）与 CudaAware（device 指针直传）双后端；
- CudaAware 的数值自检（`mpi_runtime.cu:348-430`，现覆盖 Allreduce/Allgatherv/Gatherv/
  Bcast 四类）扩展点对点 Send/Recv 自检，通过才允许启用，否则回退 HostStaged——
  沿用两道门机制；
- `DMGMD_COMM` 每步记录（`mpi_runtime.cu:927-941`）增加 p2p send/recv 字节字段，
  并区分 halo payload、migration payload 与 topology/count control；字段定义为全 rank
  聚合值或明确标记 local 值，不得沿用含糊口径，测试逐字段核算；
- P=2 的 left/right peer 是同一个 rank，必须用独立方向 tag/buffer 防止错配；P=1 为
  no-op；所有零 count（含空 rank）必须合法；
- 本节的 p2p device-buffer 自检属于 **M2a 启用门**，同步扩展
  `tests/mpi/check_environment.py`，不延后到 M3。

### 6.2 多节点 rank 布局

slab 邻居应映射到同节点：rank 顺序按 slab 空间顺序排列，`MPI_Comm_split_type`
得到的 node-local 组（`mpi_runtime.cu:227-231`）内连续。P 个 slab 跨 K 个节点时，
跨网络的点对点边只有 K-1 条（slab 邻接链的节点交界），替代现行每个 Allgatherv 穿越
全网络 `P-1` 次的模式。

## 7. ghost 生命周期

| 阶段 | 规则 |
| --- | --- |
| 创建/成员重估 | 每次邻居表重建时，由位置落入坐标 ghost 带（owned ± `d_coord`）判定；来源为相邻 rank 的 owned 原子（PBC 下含首尾 wrap image） |
| 每步刷新 | 仅位置（24 B/原子）；type/mass/group_labels 随成员变化交换一次 |
| 禁止事项 | 不得被积分（VV 只 launch owned 区间）、不得计入 thermo（`find_owned_thermo_sums` 只扫 owned）、不得直接输出（Gatherv 只取 owned）、force/PE/virial 输出无权威（AGENTS.md 不可破坏约束） |
| 身份 | ghost 槽位携带 `(global_id, image_shift)`；M2b 的通信边键为 `(center_gid, neighbor_gid, image)`（Q5） |
| 作废 | 迁移或重建导致成员变化时，ghost 槽位的全部 NEP 中间量（邻居行、Fp、partial）作废重算 |

槽位布局：local 数组前 `[0, owned)` 为 owned；随后是 dependency ghost；最后是仅坐标可
寻址的 ghost。各段按 `(source face, source rank, global_id, image)` 确定性排序以保证
pack/unpack 连续（复用 `pack_owned_soa/unpack_global_soa` 的 SoA 打包思路，
`mpi_runtime.cu:163-191`）。成员关系在重建步交换并缓存；普通步只按缓存计划刷新位置。

每 force 步在 debug 构建断言 `risk-and-backlog.md` §5 的全部不变量（owner 唯一性、ghost 不进
thermo、local index < local_count、行容量不越界等）。

## 8. 迁移协议

- **触发**：VV first half + wrap 后逐个 owned 原子重新计算目标 owner；负载再均衡暂不在
  第一切口；不得假设一步最多跨一个 slab；
- **载荷**：`global_id`、type、mass、group_labels、wrap 后 position、velocity、
  unwrapped（如启用）——即 `HostAtoms`/`DeviceAtoms` 的全部持久 per-atom 字段
  （`model.hpp:32-52`、`runtime.cu:154-212`）；
- **身份稳定**：`global_id` 源自输入行号且不变；rank 0 输出按 global ID 排序
  （`output_order`，`runtime.cu:817` 一带），跨 rank 数 restart 的既有能力
  （`dump_restart` 写全局顺序，`runtime.cu:810-843`）保持；
- **PBC**：跨周期端迁移到 wrap 邻居 slab；unwrapped 坐标的 image 簿记随载荷传递；
- **路由**：迁移不是 halo，只与左右邻居交换不够。先做 all-rank count handshake，再把每个
  原子直接发送到其最终 owner（`Alltoallv` 或等价的 count + p2p）；一步跨多 slab、空 rank
  和零 count 都必须完成且不死锁。迁移 payload 在 first half 后携带 half-step velocity；旧
  force/PE/virial 不迁移，因为 halo 完成后会重新计算；
- **作废**：迁移完成后强制邻居表重建，旧邻居表/边映射/descriptor/partial 全部作废
  （R11、`risk-and-backlog.md` §5.7）；`GPU_Vector` 容量变化引发的 view 悬空按 R27 用
  epoch/versioned view 防；
- **group labels**：现行复制态下 identity 在全 rank 可用；域分解后 labels 随原子迁移
  （R14），dump group 时由 rank 0 从 Gatherv 载荷重建全局 contents。

## 9. 通信量核算

记 `P` = rank 数，`N` = 全局原子数，`ρ` = 数密度，slab 轴长 `L`，横截面积 `A = V/L`，
`d = d_coord`（坐标 ghost 深度），`s = d_dep`（dependency/descriptor 域深度）。统一 cutoff
时保守简式为 `d = 2rc_max + 2*skin`、`s = rc_max + skin`；实现使用 §4.3 的 typewise 上界。

| 方案 | 每 rank 每步接收 | 通信模式 | 每 rank NEP 计算量 |
| --- | --- | --- | --- |
| 现行 replicated | `24N`（pos）+ `24N`（vel） | 全员 Allgatherv ×2 | 全体系 N（descriptor+force 全做） |
| M0 replicated | `24N`（pos） | 全员 Allgatherv ×1 | 全体系 N |
| M2a 两跳 halo | `≈ 24·ρAd`（两侧合计） | 仅 ≤2 相邻 rank 点对点 | descriptor 相位 `N/P + ρAs·2`；力相位 `N/P` |

thermo Allreduce（`64P`）、自适应时间步（`8P`）与输出步 Gatherv（`152N`）各方案不变。

数值例（正交全周期，`200×200×200 Å`，`ρ = 0.05 Å⁻³` ⇒ `N = 400,000`；统一
`rc_max = 5 Å`，`skin = 1 Å` ⇒ 保守 `d = 12 Å`，`s = 6 Å`；P = 4，
`L/P = 50 Å >= d`，且全局 box 满足 large-box 判据）：

- 现行/M0：每 rank 每步接收 position `24 × 400,000 = 9.6 MB`（M0 后不再有同量
  velocity 全量）；
- M2a：每 rank 每步接收 `24 × 0.05 × (200×200) × 12 ≈ 0.58 MB`，且只与相邻 slab 通信；
- M2a descriptor 相位冗余开销：`ρAs·2 / (N/P) = 2s/(L/P) = 12/50 ≈ 24%`（力相位无冗余）。

结论：M2a 的通信收益来自三点——字节数下降约一个量级（上例 ~18×）、模式从全员集合
通信变为邻居点对点（多节点下可完全留在节点内，§6.2）、NEP 计算量从 N 降为
`N/P + 24%` 冗余。单轴 slab 的已知局限：`L/P` 受 `>= d_coord` 守卫限制，高 P 强扩展时
冗余比 `2s/(L/P)` 上升，3D 分解（§12）是后续正解，与 `NEP_MULTIGPU` 的单轴局限
（kernel-inventory §7 "不能映射" 清单）一致。

M2b 的取舍（Q4）：深层带消除节省 `ρA(d−s)` 原子的坐标字节，但新增 halo 中心
`Fp`（每原子 `(n_max_radial+1) + (n_max_angular+1)(L_max+1)` 个 float）与 directed
partial 的交换；字节上未必占优，收益主要是消除 halo 冗余 descriptor 计算。以 M2a 为
oracle 逐原子比对后裁决（§10）。

replicated 阶段"不发布多卡性能结论"的约定（`docs/standards/replicated-mpi.md`）在 M3 才由
scaling 基准解除；本文档以上为解析上界，不构成实测承诺。

## 10. 验证计划

四层验证框架（AGENTS.md）映射到里程碑验收门：

- **M0**：现有 `tests/mpi/run_mpi_differential.py` 1/2/4-rank × HostStaged/CudaAware
  矩阵不改容差直接通过；轨迹/输出与现行实现逐字节一致；通信记录断言随协议修订。
- **M1**（已实施，2026-09-15）：golden 差分（初始 per-atom energy/force/virial、短
  NVE 轨迹、thermo）在既有容差内通过；专属迁移矩阵 `tests/mpi/run_mpi_migration.py`
  （跨 slab 边界、一步跨多 slab、PBC 端点 wrap、暂时空 slab、多段 run、
  correct_velocity、跨 rank 数 restart）通过；P=1 与 M0 逐字节一致。
- **M2a**（本地布局 + halo + 中心分片）：
  - **单 rank 退化门**：P=1 时 ghost=0、所有中心区间退化为 `0..N`，输出与现行单 rank
    逐字节一致；
  - **fallback 门**：既有 24 Å/small-box differential 与 migration fixtures 继续通过，且
    明确断言日志为 M1 fallback；不得把它们误当成 M2a 覆盖；
  - **CPU/domain-layout 单测**：eligibility、M1 等宽边界、typewise 两跳半径、P=2 同 peer
    双方向、mixed-type float cutoff 向上舍入、真正 `local_count=0`、确定性槽位布局和
    malformed exchange plan；
  - **新增 `tests/mpi/run_mpi_domain.py`**：使用满足 large-box 与 `slab_width >= d_coord`
    的专用大盒，覆盖 2/4 rank × HostStaged/CudaAware；以同一输入 P=1 为数值 oracle；
  - **边界 fixture**：`s=0`、`s=1`、恰在 slab 边界、周期首尾；并加入三原子链，令 owned
    i 的力依赖 ghost j 的 descriptor/partial，而 j 又依赖位于一跳 halo 外的 k，以直接
    证明两跳闭包；
  - **迁移 fixture**：普通跨界、一步跨多 slab 的 direct routing、周期端点、暂时空 slab、
    correct_velocity、输出/restart 跨 rank 数；
  - **空域/周期记录 fixture**：两原子聚集在 rank 0，P=4 的 rank 2 连续 1000 步保持
    `local_count=0`；force call 1000 的 `neighbor.out` 与两次 MPI_MAX 通信量精确匹配 P=1
    oracle/字节模型；
  - **势分支 fixture**：NEP5、mixed typewise radial/angular cutoff、flexible ZBL、
    typewise ZBL 各以两步 large-box P=1 oracle 覆盖 2/4 rank × 双后端；
  - per-atom energy/force/virial 与 P=1/committed golden 在既有容差内；owned global ID
    全局恰好一次，ghost 不进积分/thermo/输出，local index/ELL 容量合法；普通 M2a 步不得
    出现 position Allgatherv，p2p/control 字节按精确模型断言；
  - `tests/long_nve/run_long_nve.py` 的 smoke 小盒保持 P=1 M1 oracle；nightly 使用针对 4 rank、
    release 使用针对 8 rank 的长轴大盒，所有 P>1 配置必须命中 M2a，且最大-rank slab 宽度
    大于 `2*d_coord`；M2a nightly 已于 2026-09-18 手动执行并通过，100000-step 长程
    release 仍待执行；
  - debug 构建启用 `risk-and-backlog.md` §5 不变量断言。
- **M2b**：与 M2a oracle 的逐原子 force/virial 等价（Q4 验收）+ 同套长程守恒。
- **M3**：多节点 I/O 的严格双物理节点验收按
  [multi-node-io.md](./multi-node-io.md) 执行（IN PROGRESS）；跨节点 restart；
  强扩展 scaling 基准（在此之后才允许发布性能结论）。

容差层级（R16）：逐字段精确（M0、单 rank 退化）→ 确定性数值容差（分片累加顺序差异，
需在 M1 量化并记录 reduction 顺序与 rank 数）→ 统计比较（长轨迹混沌分叉）。

## 11. 风险与未决问题映射

| 项 | 处置 | 章节 |
| --- | --- | --- |
| R1 两跳依赖 | 坐标 halo = `max(R_force + R_dep) + 2*skin`；M2a 不交换中间量即闭环 | §4 |
| R2 计数/stride 混用 | `AtomCounts` 已类型化；logical local_count 与 allocation capacity 分离并显式传入 domain kernel | §3.2、§5.1 |
| R3 迁移身份 | global_id 稳定；输出按 global ID 排序；载荷清单固定 | §8 |
| R4 归约/温控/RNG rank 依赖 | thermo 仍是 owned 求和 + 全局 Allreduce；Berendsen 用归约后全局温度；RNG 改造不在本切口 | §6 |
| R5 NEP_MULTIGPU 编排 | 不复用其编排（AGENTS.md）；仅引用窗口数学证据 | §1.1、§4 |
| R6 ghost 输出所有权 | 输出只承认 owned；clear 保持 `[0,local)` 仅作 scratch | §5.3 |
| R7 邻居表互反/排序 | 深位置 halo 保证行完整；行排序键改 global ID（Q15） | §3.4 |
| R8 候选域=中心域假设 | 邻居构建器接口分离 center/candidate 域 | §5.2 |
| R11 重建全局 OR / 迁移作废 | 全 rank OR；迁移后强制重建 | §4.3、§8 |
| R12 typewise/ZBL 截断半径 | loader 后按现有 kernel 消费路径和 float 舍入枚举类型对/三元组求上界；不改变数值语义 | §4.4 |
| R16 MPI 归约非结合 | 容差三层级，M1 量化 | §10 |
| R25 `neighbor.out` 副作用 | 本次邻居表生成后取 local max，经 MPI_MAX 聚合，仅 rank0 写；周期归约精确记账 | §5.3 |
| R26 CUDA-aware 同步 | p2p 自检两道门；默认 stream + 阻塞语义（Q40） | §6.1 |
| R27 容量变化 view 失效 | epoch/versioned view | §8 |
| R28 多节点临时目录 | 整改与双节点验收进行中（[multi-node-io.md](./multi-node-io.md)） | §10 |

Q4（halo 协议选择）：本文档按已确认决策落地为 M2a 先行、M2b 以 oracle 验证（§4.5）。
Q5（通信边匹配键）：仅 M2b 需要，键定义见 §7。Q14（skin 语义）：保持 skin=1 Å 与
位移 > skin/2 判据不变（§4.3）。Q15（排序键）：cell 内按 global ID（§3.4），容差待
M1 量化。

## 12. 里程碑路线图

```text
M0  删除每步 velocity Allgatherv（correct_velocity 触发步保留恢复复制）【已实施 2026-09-15】
    门：differential 矩阵逐字节一致                        ← 独立，可先行
M1  空间 slab 所有权 + 迁移机制（数据仍复制、仍 indexed Allgather、NEP 仍全量）
    【已实施 2026-09-15；所有权为 global_id + index list/mask，indexed collective、
     双 epoch 迁移时序；M2a 保持等宽 fractional slab，并另做 large-box/d_coord eligibility】
    门：golden 差分 + 迁移 fixture + 跨 rank restart
M2a 本地 owned/ghost 布局 + p2p 深位置 halo + NEP 中心/descriptor 域分片
    【已实施 2026-09-18：单 rank 逐字节退化、fallback 兼容、专属大盒边界/两跳/
     迁移矩阵全过；M2a nightly 已于 2026-09-18 手动执行并通过；100000-step release 待执行】
    门：单 rank逐字节退化 + fallback兼容 + 专属大盒边界/两跳/迁移矩阵 + nightly；
        100000-step release 仍是后续门槛
M2b （可选）Fp/partial 分阶段交换，替代深位置 halo
    门：与 M2a 逐原子等价（Q4）
M3  多节点硬化：rank-slab 节点布局、多节点 I/O、scaling 基准
    门：跨节点 restart + 性能结论解禁
后续 3D 分解、triclinic/非周期、small-box、更多 ensemble
```

依赖关系：M0 独立；M1 → M2a → M2b；M3 依赖 M2a（M2b 可与 M3 并行）。
每个里程碑的实现必须同步更新 `docs/standards/replicated-mpi.md`（协议与字节表）或以本文档的
对应章节替代之，并保持 `DMGMD_COMM` 记录与测试断言一致。
