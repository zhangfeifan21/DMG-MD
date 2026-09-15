# 域分解与 halo 通信协议（设计稿）

类别：待实施计划。状态：PROPOSED，未实施。

本文档规定 DMG-MD 从 replicated-data 原型演进为 owned/ghost 域分解
runtime 的数据面协议，并定义 M0 快速优化。实现前须按 AGENTS.md 由维护者确认方向；
本文档不修改任何生产代码。

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

### 1.2 DMG-MD 现行协议与开销

DMG-MD 已把积分、thermo 与输出权威按 `OwnedRange` 分布到各 rank（`src/runtime.cu:910-1048`），
力不汇总回任何单一 GPU。但数据面仍是复制态，每步（`docs/standards/replicated-mpi.md`）：

| 步骤 | 位置 | 通信 |
| --- | --- | --- |
| VV first half（仅 owned） | `runtime.cu:972-982` | — |
| **position Allgatherv** | `runtime.cu:983-984` | 输入 `24N`、输出 `24NP` |
| 全体系 NEP（`N1=0, N2=N`，`runtime.cu:482-483`） | `runtime.cu:985` | — |
| VV second half（仅 owned） | `runtime.cu:986-991` | — |
| thermo 8-double Allreduce | `runtime.cu:992-993` | `64P` / `64P` |
| Berendsen 缩放（仅 owned） | `runtime.cu:995-1008` | — |
| **velocity Allgatherv** | `runtime.cu:1009-1010` | 输入 `24N`、输出 `24NP` |
| 输出步 Gatherv 到 rank 0 | `runtime.cu:1012-1023` | 输出步 `152N` |

两个结构性开销：

1. **每 rank 对全体系 N 个原子做完整 NEP**（`nep_N1_N2_shard_complete=false`，
   `runtime.cu:476-484`）——多卡不减少每卡计算量，只复制计算；
2. **每步两个 O(N) Allgatherv**——多节点时穿越网络 `P-1` 次，随 P 线性放大。

优化分两层：M0 删除无消费者的 velocity Allgatherv（§2）；M1/M2 用空间 slab 所有权 +
点对点 halo 交换替换复制态（§3-§8），使每 rank 的 NEP 计算量降为 `N/P + halo`，
通信降为仅相邻 rank 的边界带交换。这正是多节点扩展的前提：slab 邻居可映射到同节点，
网络流量从全员集合通信收缩为节点内点对点。

## 2. M0：删除每步 velocity Allgatherv

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

### 3.1 从下标分块到空间 slab

现行 `balanced_owned_range`（`include/dmgmd/partition.hpp:20-35`）按全局下标均衡切块，
与空间无关，这是复制态阶段刻意简化的产物（见
`docs/standards/replicated-mpi.md`）。域分解阶段改为：

- 沿一个 partition 轴把 box 切成 P 个连续 slab，每个 rank 拥有一个 slab；
- slab 边界对齐 `rc/2` cell 网格（与 cell list 一致：`src/gpumd_compat/neighbor.cu:298`
  `rc_cell_list = 0.5 * rc`；`NEP_MULTIGPU` 同用 `rc/2`，`nep_multigpu.cu:1424-1427`）；
- 守卫：每 rank 沿轴 bins ≥ 10（即 ≥ 5rc），不满足则明确报错，沿用
  `nep_multigpu.cu:1451-1455` 的判据语义——该判据同时保证 ghost 带不超过相邻 slab 的
  owned 深度，避免三 rank 链式依赖；
- partition 轴选择：默认取 box 最长方向（`nep_multigpu.cu:1438-1446` 的语义）；GPUMD
  `potential` 第三参数（x/y/z）当前被 DMG-MD parser 拒绝（`src/run_parser.cpp:163-168`），
  是否恢复该语法以覆盖分区方向，作为 M1 的输入兼容决策单独确认。

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

`run_replicated` 的 "no ghosts" 守卫（`runtime.cu:1061-1065`）在 M2 退役，由本节不变量
替代；单 rank（P=1）必须退化为 `owned = N、ghost = 0`，输出与现行单 rank 路径逐字节一致
（§10 验收门）。

### 3.3 box 范围（第一切口）

按 Q6 已确认决策：第一切口只支持**正交全周期大盒**。triclinic / 非周期方向输入 parse 后
明确报 `unsupported`，不得静默投影（`risk-and-backlog.md` §4 列出的全部倾斜盒用例移入后续
里程碑）。非周期方向在 3D 分解（§12）前不参与切分。

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

结论：owned 原子的力需要 **owned ± 2rc 内全部原子的坐标**，仅交换 rc 深度坐标 halo 会
产生"看似连续、系统性错误"的边界力（`risk-and-backlog.md` R1 失败模式）。

### 4.2 三层窗口定义

`NEP_MULTIGPU` 的窗口布局（`gpumd-reference/src/force/nep_multigpu.cuh:42-55`、分区计算
`nep_multigpu.cu:1476-1544`，`docs/standards/kernel-inventory.md` §7 的映射表）给出三层域的数值语义，
本项目按 MPI 概念重新表述（cell 宽 `rc/2`，2 cells = 1 rc）：

| 域 | 窗口（不含 skin） | NEP 工作内容 | MPI 概念 |
| --- | --- | --- | --- |
| 力域 `[N1,N2)` | owned | radial force（`nep_multigpu.cu:1658`）、many-body（`:1707`）、ZBL（`:1731`） | owned atoms |
| descriptor 域 `[N4,N5)` | owned ± rc | 邻居表（`:1603`）、descriptor/Fp（`:1631`）、angular partial（`:1683`） | dependency centers |
| 坐标域 `[0,N3)` | owned ± 2rc | 仅可寻址（descriptor 域的邻居候选） | coordinate ghosts |

### 4.3 skin 与重建的修正

`NEP_MULTIGPU` 每步重建邻居表，窗口精确取 rc/2rc。DMG-MD 的 `gpumd_compat::Neighbor`
使用 Verlet skin：skin = 1 Å，任一原子相对参考位置位移 > skin/2 即重建
（`src/gpumd_compat/neighbor.cu:407-414`），构建半径 `rc + skin`
（`neighbor.cu:454`）。因此：

- **坐标 ghost 深度 = 2rc_max + skin**：descriptor 域中心（owned ± (rc_max + skin)）
  的构建半径内候选原子最远落在 owned ± (2rc_max + skin)，保证整个重建间隔内
  rc 邻域可寻址；
- **descriptor 域 = owned ± (rc_max + skin)**：ghost 成员在每次重建时重估；
- ghost **位置每步刷新**（每原子 24 B），type/mass/group 随成员变化交换；
- 重建判据是全局 OR：任一 rank 的 owned 原子超阈值 ⇒ 全体重建（R11）；迁移
  完成后强制重建并作废全部 NEP 中间量（`risk-and-backlog.md` §5 不变量 7）。

### 4.4 有效半径的确定（R12，M2 前置任务）

`rc_max` 不能直接取 `paramb.rc`：radial pair cutoff 是 typewise 平均
（`nep.cu:748` `rc = (rc_radial[t1] + rc_radial[t2]) * 0.5f`），ZBL outer cutoff 可能大于
angular cutoff（Q11），typewise 截断的两跳组合上界由 i-j-k 类型三元组决定（Q12）。
M2 动工前必须在 potential loader 后枚举类型对/三元组，计算几何上界并写入启动日志；
`Ra > Rr` 等非法组合按 Q10 的 loader 决策处理。

### 4.5 双 oracle 策略（Q4 已确认）

按 Q4 维护者决策："先实现保守两跳 oracle，再以其验证 `Fp` + partial staged protocol"：

- **M2a 保守两跳深位置 halo**（本文档主线）：交换 owned ± (2rc_max + skin) 的坐标，
  各 rank 对 descriptor 域做冗余计算（含 halo 中心的 descriptor/partial），力域只算
  owned。**不需要任何中间量通信**，many-body 反向边查找全在本机（局部整数下标有效，
  无跨 rank 边匹配问题，Q5 的边键仅在 M2b 出现）。
- **M2b 分阶段交换**（后续可选）：坐标 halo 只到 rc_max + skin，另交换 halo 中心的
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
`N1 = 0; N2 = owned_count; ND1 = 0; ND2 = owned + rc_ghost`，其注释引用的完整性证明由
本协议替换：descriptor 域覆盖 `[N4,N5)` 后，`Fp(n2)` 与反向 partial 全部本机可得，
`nep_N1_N2_shard_complete` 的启动记录随之改写。

### 5.2 邻居构建器的域分离（R8）

现行 `gpu_find_neighbor_ON1` 要求候选 n2 也在中心域 `[N1,N2)` 内
（参考实现 `gpumd-reference/src/force/neighbor.cu:144`，`risk-and-backlog.md` R8）。新接口必须
分离**中心域**（写 NL 行的原子，`[ND1,ND2)`）与**候选域**（可进入邻居行的原子，全部
local 槽位 owned+ghost）。ELL 容量按 `neighbor.cu:478-483` 的 `(rc+skin)^3/rc^3` 放大
逻辑对 local 体系重算（Q13 的越界行为保持"安全报错"）。

### 5.3 输出清理与 `neighbor.out` 副作用

`clear_owned_properties`（`runtime.cu:290-309`，launch `runtime.cu:492-493`）保持清
`[0, local_count)`：ghost 槽位的 NEP scratch 写入无害，输出 Gatherv 只取 owned 切片
（`runtime.cu:224-253`），不触发 R6 的 ghost 所有权问题。

`compute_large_box` 每 1000 次调用 append `neighbor.out` 的隐式 D2H 副作用
（`nep.cu:1063-1078`，R25/Q18）：非 root rank 已由 `RankIoIsolation` 重定向到
`/dev/null`（`runtime.cu:80-137`）。分片后每 rank 只记录自身域的计数，M2 实现时从
"rank 0 聚合"与"明确不支持该输出"二选一，随实现提交一并裁决。

## 6. 每步协议（M2 目标时序）

替换 `docs/standards/replicated-mpi.md` 的复制态协议。slab 分解下每方向的通信对象 ≤ 2 个相邻
rank（PBC 下首尾互为邻居；P=1 时全部为空操作）：

1. **（correct_velocity 触发步）** velocity 全量 Allgatherv → root CPU 修正 →
   `broadcast_device`（维持 M0 例外语义，跨段频率通常 ≥ 100 步，成本可接受；
   owned 归约化改造按 R24 另行立项）；
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
12. 输出步：Gatherv owned 切片到 rank 0，rank 0 按 global ID 排序输出（不变）。

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
  differential 测试可断言新记账。

### 6.2 多节点 rank 布局

slab 邻居应映射到同节点：rank 顺序按 slab 空间顺序排列，`MPI_Comm_split_type`
得到的 node-local 组（`mpi_runtime.cu:227-231`）内连续。P 个 slab 跨 K 个节点时，
跨网络的点对点边只有 K-1 条（slab 邻接链的节点交界），替代现行每个 Allgatherv 穿越
全网络 `P-1` 次的模式。

## 7. ghost 生命周期

| 阶段 | 规则 |
| --- | --- |
| 创建/成员重估 | 每次邻居表重建时，由 cell 位置落入坐标 ghost 带（owned ± (2rc_max + skin)）判定；来源为相邻 rank 的 owned 原子（PBC 下含首尾 wrap image） |
| 每步刷新 | 仅位置（24 B/原子）；type/mass/group_labels 随成员变化交换一次 |
| 禁止事项 | 不得被积分（VV 只 launch owned 区间）、不得计入 thermo（`find_owned_thermo_sums` 只扫 owned）、不得直接输出（Gatherv 只取 owned）、force/PE/virial 输出无权威（AGENTS.md 不可破坏约束） |
| 身份 | ghost 槽位携带 `(global_id, image_shift)`；M2b 的通信边键为 `(center_gid, neighbor_gid, image)`（Q5） |
| 作废 | 迁移或重建导致成员变化时，ghost 槽位的全部 NEP 中间量（邻居行、Fp、partial）作废重算 |

槽位布局：local 数组前 `[0, owned)` 为 owned，`[owned, local_count)` 为 ghost，按来源
rank 分组排序以保证 pack/unpack 连续（复用 `pack_owned_soa/unpack_global_soa` 的
SoA 打包思路，`mpi_runtime.cu:163-191`）。

每 force 步在 debug 构建断言 `risk-and-backlog.md` §5 的全部不变量（owner 唯一性、ghost 不进
thermo、local index < local_count、行容量不越界等）。

## 8. 迁移协议

- **触发**：owned 原子的 cell 跨出本 rank slab 边界（§6 步骤 5 检查；Verlet 重建间隔内
  位移 ≤ skin/2，边界附近原子的检查粒度与之匹配）；负载再均衡暂不在第一切口；
- **载荷**：`global_id`、type、mass、group_labels、wrap 后 position、velocity、
  unwrapped（如启用）——即 `HostAtoms`/`DeviceAtoms` 的全部持久 per-atom 字段
  （`model.hpp:32-52`、`runtime.cu:154-212`）；
- **身份稳定**：`global_id` 源自输入行号且不变；rank 0 输出按 global ID 排序
  （`output_order`，`runtime.cu:817` 一带），跨 rank 数 restart 的既有能力
  （`dump_restart` 写全局顺序，`runtime.cu:810-843`）保持；
- **PBC**：跨周期端迁移到 wrap 邻居 slab；unwrapped 坐标的 image 簿记随载荷传递；
- **作废**：迁移完成后强制邻居表重建，旧邻居表/边映射/descriptor/partial 全部作废
  （R11、`risk-and-backlog.md` §5.7）；`GPU_Vector` 容量变化引发的 view 悬空按 R27 用
  epoch/versioned view 防；
- **group labels**：现行复制态下 identity 在全 rank 可用；域分解后 labels 随原子迁移
  （R14），dump group 时由 rank 0 从 Gatherv 载荷重建全局 contents。

## 9. 通信量核算

记 `P` = rank 数，`N` = 全局原子数，`ρ` = 数密度，slab 轴长 `L`，横截面积 `A = V/L`，
`d = 2rc_max + skin`（坐标 ghost 深度），`s = rc_max + skin`（descriptor 域深度）。

| 方案 | 每 rank 每步接收 | 通信模式 | 每 rank NEP 计算量 |
| --- | --- | --- | --- |
| 现行 replicated | `24N`（pos）+ `24N`（vel） | 全员 Allgatherv ×2 | 全体系 N（descriptor+force 全做） |
| M0 replicated | `24N`（pos） | 全员 Allgatherv ×1 | 全体系 N |
| M2a 两跳 halo | `≈ 24·ρAd`（两侧合计） | 仅 ≤2 相邻 rank 点对点 | descriptor 相位 `N/P + ρAs·2`；力相位 `N/P` |

thermo Allreduce（`64P`）、自适应时间步（`8P`）与输出步 Gatherv（`152N`）各方案不变。

数值例（正交全周期，`200×200×200 Å`，`ρ = 0.05 Å⁻³` ⇒ `N = 400,000`；`rc_max = 5 Å`，
`skin = 1 Å` ⇒ `d = 11 Å`，`s = 6 Å`；P = 4，`L/P = 50 Å ≥ 5rc` 守卫满足）：

- 现行/M0：每 rank 每步接收 position `24 × 400,000 = 9.6 MB`（M0 后不再有同量
  velocity 全量）；
- M2a：每 rank 每步接收 `24 × 0.05 × (200×200) × 11 ≈ 0.53 MB`，且只与相邻 slab 通信；
- M2a descriptor 相位冗余开销：`ρAs·2 / (N/P) = 2s/(L/P) = 12/50 ≈ 24%`（力相位无冗余）。

结论：M2a 的通信收益来自三点——字节数下降约一个量级（上例 ~18×）、模式从全员集合
通信变为邻居点对点（多节点下可完全留在节点内，§6.2）、NEP 计算量从 N 降为
`N/P + 24%` 冗余。单轴 slab 的已知局限：`L/P` 受 `≥ 5rc` 守卫限制，高 P 强扩展时
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
- **M1**（空间所有权 + 迁移机制，数据仍复制、仍 Allgather）：golden 差分（初始
  per-atom energy/force/virial、短 NVE 轨迹、thermo）在既有容差内；迁移 fixture
  （原子跨 slab 边界、PBC 端点 wrap）；跨 rank 数 restart。
- **M2a**（本地布局 + halo + 中心分片）：
  - **单 rank 退化门**：P=1 时 ghost=0、所有中心区间退化为 `0..N`，输出与现行单 rank
    逐字节一致；
  - **边界 fixture**：2-rank，原子贴 slab 边界（s=0、s=1、恰在边界）、跨一个周期的
    构型（`risk-and-backlog.md` §4 用例子集，正交全周期内）；
  - per-atom energy/force/virial 对 GPUMD golden；短轨迹 + `tests/long_nve/`
    `run_long_nve.py` 的守恒统计（4096/12288/5000 原子、100,000 步、漂移斜率、RDF、
    MSD）；
  - debug 构建启用 `risk-and-backlog.md` §5 不变量断言。
- **M2b**：与 M2a oracle 的逐原子 force/virial 等价（Q4 验收）+ 同套长程守恒。
- **M3**：`check_environment.py` 扩展点对点 device 自检；多节点 I/O 的严格双物理节点验收按
  [multi-node-io.md](./multi-node-io.md) 执行（IN PROGRESS）；跨节点 restart；
  强扩展 scaling 基准（在此之后才允许发布性能结论）。

容差层级（R16）：逐字段精确（M0、单 rank 退化）→ 确定性数值容差（分片累加顺序差异，
需在 M1 量化并记录 reduction 顺序与 rank 数）→ 统计比较（长轨迹混沌分叉）。

## 11. 风险与未决问题映射

| 项 | 处置 | 章节 |
| --- | --- | --- |
| R1 两跳依赖 | 坐标 halo = 2rc_max + skin；M2a 不交换中间量即闭环 | §4 |
| R2 计数/stride 混用 | `AtomCounts` 已类型化；local_count 为唯一 stride；kernel 参数化 | §3.2、§5.1 |
| R3 迁移身份 | global_id 稳定；输出按 global ID 排序；载荷清单固定 | §8 |
| R4 归约/温控/RNG rank 依赖 | thermo 仍是 owned 求和 + 全局 Allreduce；Berendsen 用归约后全局温度；RNG 改造不在本切口 | §6 |
| R5 NEP_MULTIGPU 编排 | 不复用其编排（AGENTS.md）；仅引用窗口数学证据 | §1.1、§4 |
| R6 ghost 输出所有权 | 输出只承认 owned；clear 保持 `[0,local)` 仅作 scratch | §5.3 |
| R7 邻居表互反/排序 | 深位置 halo 保证行完整；行排序键改 global ID（Q15） | §3.4 |
| R8 候选域=中心域假设 | 邻居构建器接口分离 center/candidate 域 | §5.2 |
| R11 重建全局 OR / 迁移作废 | 全 rank OR；迁移后强制重建 | §4.3、§8 |
| R12 typewise/ZBL 截断半径 | M2 前置任务：loader 后枚举类型对/三元组求上界（Q11/Q12） | §4.4 |
| R16 MPI 归约非结合 | 容差三层级，M1 量化 | §10 |
| R25 `neighbor.out` 副作用 | rank0 聚合 vs 明确不支持，随 M2 裁决（Q18） | §5.3 |
| R26 CUDA-aware 同步 | p2p 自检两道门；默认 stream + 阻塞语义（Q40） | §6.1 |
| R27 容量变化 view 失效 | epoch/versioned view | §8 |
| R28 多节点临时目录 | 整改与双节点验收进行中（[multi-node-io.md](./multi-node-io.md)） | §10 |

Q4（halo 协议选择）：本文档按已确认决策落地为 M2a 先行、M2b 以 oracle 验证（§4.5）。
Q5（通信边匹配键）：仅 M2b 需要，键定义见 §7。Q14（skin 语义）：保持 skin=1 Å 与
位移 > skin/2 判据不变（§4.3）。Q15（排序键）：cell 内按 global ID（§3.4），容差待
M1 量化。

## 12. 里程碑路线图

```text
M0  删除每步 velocity Allgatherv（correct_velocity 触发步保留恢复复制）
    门：differential 矩阵逐字节一致                        ← 独立，可先行
M1  空间 slab 所有权 + 迁移机制（数据仍复制、仍 Allgather、NEP 仍全量）
    门：golden 差分 + 迁移 fixture + 跨 rank restart
M2a 本地 owned/ghost 布局 + p2p 深位置 halo + NEP 中心/descriptor 域分片
    门：单 rank 逐字节退化 + 边界 fixture + 长程守恒
M2b （可选）Fp/partial 分阶段交换，替代深位置 halo
    门：与 M2a 逐原子等价（Q4）
M3  多节点硬化：点对点后端自检、rank-slab 节点布局、多节点 I/O、scaling 基准
    门：check_environment 扩展 + 跨节点 restart + 性能结论解禁
后续 3D 分解、triclinic/非周期、small-box、更多 ensemble
```

依赖关系：M0 独立；M1 → M2a → M2b；M3 依赖 M2a（M2b 可与 M3 并行）。
每个里程碑的实现必须同步更新 `docs/standards/replicated-mpi.md`（协议与字节表）或以本文档的
对应章节替代之，并保持 `DMGMD_COMM` 记录与测试断言一致。
