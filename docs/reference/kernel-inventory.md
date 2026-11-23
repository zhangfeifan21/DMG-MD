# NEP MD CUDA kernel 清单与通信依赖

类别：GPUMD 内核与依赖的源码审计。核对日期：2026-10-16。

## 1. 范围和记号

本文核对的 GPUMD 源码提交是 `9d23496e41319b9e2af5221a7df6285387401d1e`。所有 `src/force`、`src/integrate` 等上游路径和行号都指这个历史提交。L20 的短程 golden 由普通 GPUMD 5.9.1 生成。[兼容矩阵](../standards/compatibility-matrix.md)

下面沿单 bead 普通 NEP/NEP-ZBL 的初始力和 NVE/`nvt_ber` 每步计算，列出各 CUDA 核函数从哪些原子读取数据、向哪些原子写回。范围包括 thermo 和 XYZ 输出，不含 PIMD、附加外力、其他势及其他 measurement 核函数。

上游通常一次启动全 N 个原子。DMG-MD 在仓库内的 `src/gpumd_compat` 复现带 Origin 注记的数值子集，构建不使用外部参考源码。局部域分解模式另行划分计算中心、按全局 ID 排序，并管理缓存、分区和通信（O2/O3）。[MPI 标准](../standards/mpi-runtime.md)、[分区标准](../standards/domain-partition.md)

| 记号 | 含义 |
| --- | --- |
| `N` | 上游全体系原子数，也是大多数 SoA/ELL 数组的步长 |
| `Nc` | DMG-MD 最终力中心数，局部域分解模式取 owned_count |
| `Nl` | 本 rank 能访问的负责原子和边界副本数（local_count） |
| `NL[neighbor_ordinal * N + center]` | ELL 邻居表中，给定中心、给定邻居序号的槽位 |
| `x[0:N], y[0:N], z[0:N]` | 向量 SoA 的三个分量块。virial 有 9 个同样长度的分量块 |
| `Rr(a,b)=(rc_radial[a]+rc_radial[b])/2` | a、b 类型对的径向截断半径，角向半径 `Ra(a,b)` 同理 |

本文的“完整邻居表”对每条有向边 `i→j` 都保存反向边 `j→i`，每行按原子下标升序排列。多体力汇总时按邻居行做二分查找，两项条件缺一不可。

## 2. timestep 外壳 kernel

| kernel / 文件 | launch 域 | 读 | 写 | atomic | 完整表/ghost/MPI 语义 |
| --- | --- | --- | --- | --- | --- |
| `gpu_velocity_verlet`，`src/integrate/ensemble.cu:176-214` | 全 `N`，一 thread/atom；compute1/2 各一次 | mass、force、velocity | velocity；compute1 还写 position | 否 | 参考程序积分全部原子。MPI 只更新本 rank 负责的原子，不得积分边界副本 |
| `gpu_apply_pbc`，`src/force/force.cu:424-459` | 全 `N` | box inverse、position SoA | wrapped position SoA | 否 | 参考程序写全局数组。局部域分解在迁移和 halo 交换前折回本 rank 的负责原子。三斜盒若要分解，需按分数坐标处理归属，当前多 rank 拒绝该几何 |
| `initialize_properties`，`src/force/force.cu:314-333` | 全 `N` | 无 | force 3N、PE N、virial 9N 清零 | 否 | MPI 只需保证 owned 输出唯一；若 kernel 工作区覆盖 ghost，要独立清理 |
| `gpu_find_thermo_instant_temperature`，`src/integrate/ensemble.cu:434-633` | 固定 8 blocks × 1024 threads；每 block 一种量 | mass、velocity、PE、virial | `thermo[0:8]` | 否；block reduction | 当前读全 `N`。MPI 先 local-owned reduction，再 all-reduce；不能计 ghost |
| `gpu_berendsen_temperature`，`src/integrate/ensemble_ber.cu:70-83` | 全 `N` | target/coupling、`thermo[0]`、velocity | velocity | 否 | all-reduce 得到全局温度后只缩放 owned |
| `gpu_sum`，`src/measure/dump_xyz.cu:32-54` | 6 blocks × 1024；每 block 一个 virial 分量 | virial 6×N | 6 个总 virial | 否 | XYZ comment 用；MPI 改成 owned local sum + global reduction |

`fix`/`move` 启用后，`Ensemble::velocity_verlet()` 走分离的 `gpu_velocity_verlet_v` 和 `gpu_velocity_verlet_x` (`ensemble.cu:216-295`)，并读取每原子 group label。DMG-MD 当前只支持无 fix/move 的上述系综，解析器须明确拒绝未支持组合。

## 3. large-box 邻居表 kernel

上游 `Neighbor::find_neighbor_global()`（`src/force/neighbor.cu:756-800`）在第一次求力前建表。以后按位移检查缓存，若有原子相对参考位置移动超过 `skin/2` 则重建。`Neighbor::initialize()` 在 `neighbor.cu:824-832` 把固定 `skin=1 Å` 的容量按 `(rc+skin)^3/rc^3` 放大。

| kernel/辅助操作 | 域 | buffer（布局） | atomic | 数据/同步证据 | MPI 处置 |
| --- | --- | --- | --- | --- | --- |
| `gpu_check_atom_distance` (`neighbor.cu:646-684`) | 全 `N` | 读 old/new position SoA；写单一 device counter | block sum 后 `atomicAdd` | wrapper 每次把 counter H2D/D2H (`741-753`)，形成 host sync | 本地检查负责原子的位移上界，再用 MPI 汇总超限原因。迁移后须重建 |
| `find_cell_counts` (`neighbor.cu:42-60`) | 所有可入 cell 的原子 | 读 position；写 `cell_count[cell]` | 是 | cell size=`rc_build/2` | local cell list 可含 owned+ghost；需明确重复 image |
| `thrust::exclusive_scan` (`neighbor.cu:196-197`) | cell 数 | count → prefix | 内部实现 | 在同一默认 stream 排在 contents 前 | 可共享，或换成等价 scan |
| `find_cell_contents` (`neighbor.cu:62-83`) | 所有可入 cell 的原子 | 读 position/prefix；写 `cell_contents` | 对 cell insertion counter atomic | contents 值是当前数组下标 | 重构为 local index；全局 ID 另存 |
| `gpu_find_neighbor_ON1` (`neighbor.cu:85-162`) | `[N1,N2)` 中心 | cell arrays、type（未实际用于距离）、position；写 `NN/NL` ELL | 否 | 只接受候选 `n2>=N1 && n2<N2` (`144`)；当前调用为 `[0,N)` | 必须去掉“邻居也在中心域”的条件，改成 owned/dependency center + `[0,Nl)` candidates |
| `gpu_sort_neighbor_list` (`src/force/neighbor.cuh:112-136`) | 当前全 `N` blocks；一 block/row | `NN/NL` | 否 | many-body 反向边的二分查找需要排序 | 直接复用算法；只 sort 有效中心行，并保证反向行存在 |
| `gpu_update_xyz0` (`neighbor.cu:688-697`) | 全 `N` | current position → reference position | 否 | rebuild 末尾执行 | 保存 owned reference；ghost 更新/迁移使 local identity 变化时重建 |

`gpu_find_local_neighbor_from_global` (`neighbor.cu:699-737`) 只在其他势/局部 cutoff wrapper 使用；普通 `NEP::compute_large_box()` 直接执行下一节的 `find_neighbor_list_large_box`。

## 4. large-box NEP 核心 kernel

直接调用顺序见 `NEP::compute_large_box()` (`src/force/nep.cu:978-1138`)。

### 4.1 完整表

| kernel / 文件 | 用途与 launch domain | 读取 buffer | 写入 buffer | atomic | 邻居/ghost/通信结论 |
| --- | --- | --- | --- | --- | --- |
| `find_neighbor_list_large_box`，`nep.cu:436-486` | 一 thread/中心 `[N1,N2)`；把 Verlet 表过滤成 typewise radial/angular 表 | type N；position 3N SoA；global `NN/NL` ELL | `NN_radial/NN_angular` N；`NL_*` ELL | 否 | 读邻居 position/type。代码先 `d2>=Rr² continue`，只有 radial 内才检查 angular (`465-480`)，因此隐含 `Ra<=Rr`；未见显式校验 |
| `find_descriptor`，`nep.cu:488-659` | 一 thread/中心；descriptor + ANN | center/neighbor type、position；两张完整有向过滤表；float parameters | `PE[center]` double；`Fp[d*N+center]` float；`sum_fxyz[k*N+center]` float；普通 model 不写 virial | 否 | 只需一跳 position/type；产生中心拥有的能量和中间场。不读邻居 descriptor |
| `find_force_radial`，`nep.cu:661-772` | 一 thread/最终 force 中心 | radial `NN/NL`、type/position、`Fp(center)` **和 `Fp(neighbor)`** (`727-728`) | center force 3N、center virial 9N | 否 | 无 ghost force 写、无需 reverse force exchange；但必须取得邻居中心 `Fp` |
| `find_partial_force_angular`，`nep.cu:774-861` | 一 thread/partial 中心；一中心循环全部 angular edge | angular `NN/NL`、type/position、center `Fp`、center `sum_fxyz` | directed `f12{x,y,z}[slot*N+center]` float | 否 | 不写 force。要为 owned 的邻居中心也生成/接收反向 directed edge |
| `gpu_find_force_many_body`，`src/force/potential.cu:170-333`，wrapper `Potential::find_properties_many_body()` | 一 thread/最终 force 中心 | angular `NN/NL`、position、own edge `f12(i,j)`；二分查 neighbor row 得 `f12(j,i)` (`227-252`) | center force、center virial | 否 | 要求完整、互反、排序邻居表和邻居中心 partial。无 ghost force 写；邻居 row/partial 是通信对象 |
| `find_force_ZBL`，`nep.cu:863-975` | 一 thread/最终 force 中心；NEP-ZBL 可选 | type/position、ZBL params、传入的 **angular** `NN/NL` (`compute_large_box:1123-1127`) | center PE/force/virial | 否 | center gather，无 reverse exchange；实际候选范围受 angular list 限制。未发现 `rc_outer<=Ra` 校验，需实验/额外校验决策 |

### 4.2 buffer 布局

定义证据：`src/force/nep.cuh:23-37`，构造分配见 `src/force/nep.cu:379-391`。

| buffer | 类型/尺寸 | 布局 | 分布式映射 |
| --- | --- | --- | --- |
| position/force | double `3N` | component-major SoA | 位置覆盖本地槽位，只有负责原子的力可输出。局部域分解模式以 `local_count` 为逻辑步长，capacity 只表示存储容量 |
| virial | double `9N` | component-major SoA，内部顺序 `xx,yy,zz,xy,xz,yz,yx,zx,zy` | owned only |
| PE | double `N` | atom-major scalar | owned only；dependency center 的 PE 可不保存/不通信 |
| `NN_radial/angular` | int `N` | center scalar | owned + dependency centers |
| `NL_radial/angular` | int `N*MN` | ELL ordinal-major | local indices；反向 row 必须存在 |
| `Fp` | float `N*dim` | descriptor-component-major | center-owned intermediate；需要 forward exchange 或 halo 重算 |
| `sum_fxyz` | float `N*(nmaxA+1)*(((Lmax+1)^2)-1)` | angular-channel-major | 只被同一中心的 partial kernel 读；若 owner 直接生成并发送 partial，可不发送它 |
| `f12x/y/z` | float `N*MNangular` each | ELL ordinal-major directed edge | 边的中心拥有；跨 rank 需把边值或足以重算它的中心字段送到 consumer |

### 4.3 GPU device 辅助函数

这些函数没有独立 launch，但属于必须复用的 NEP 数学核心，主要位于 `src/utilities/nep_utilities.cuh`：

| helper | 证据位置 | 调用者/用途 | 建议 |
| --- | --- | --- | --- |
| `apply_mic` | `src/model/box.cuh` | large-box 所有距离 | 保持精确 box 语义；不要另写一套 |
| `find_fc`、`find_fc_and_fcp` | `nep_utilities.cuh:378-425` | cutoff value/derivative | 原样共享 |
| `find_fn`、`find_fn_and_fnp` | `nep_utilities.cuh:479-593` | Chebyshev/basis value/derivative | 原样共享 |
| `accumulate_s` | `nep_utilities.cuh:1644-1727` | angular moment sums | 原样共享，包含按 `L_max` 展开的分支 |
| `find_q` / `find_q_one` | `nep_utilities.cuh:1728-1819` | invariant descriptor | 原样共享 |
| `apply_ann_one_layer`、`apply_ann_one_layer_nep5` | `nep_utilities.cuh:169-311` | energy 与 descriptor gradient | 原样共享；参数布局必须与 loader 同步 |
| `accumulate_f12` 及 `get_f12_*` | `nep_utilities.cuh:594-1643` | angular directed partial | 原样共享；不要重推/重写多体导数 |
| `find_f_and_fp_zbl` 两个 overload | `nep_utilities.cuh:426-478` | universal/flexible ZBL | 原样共享 |

`find_cell_id`、`gpu_sort_neighbor_list` 等邻居辅助函数在 `src/force/neighbor.cu/.cuh`；可提成与 MPI 无关的 local neighbor 库，但其域和索引类型必须参数化。

## 5. halo 深度的代码证明

<a id="51-不是一个-cutoff"></a>
### 两跳依赖从哪里来

要计算本 rank 负责的原子 `i` 的最终力，须沿下列读取关系找到第二层邻居。

1. `find_descriptor(i)` 读取所有 `j`，距离至多 `R(i,j)`。
2. `find_force_radial(i)` 读取 `Fp(j)` (`nep.cu:727-728`)。
3. `Fp(j)` 又由 `j` 的所有邻居 `k` 的 position/type 生成。
4. angular final gather 读取 `f12(j,i)` (`potential.cu:209-252`)；该值依赖 `Fp(j)` 和 `sum_fxyz(j)`，后者同样依赖 `j` 周围的 `k`。

所以仅交换坐标并在本 rank 重算所有依赖中心时，position halo 的保守几何上界是两条 typewise 边之和：

```text
H_position(i) = max over i-j-k [ R_force(i,j) + R_descriptor(j,k) ]
```

当所有类型用同一截断半径时，两跳距离的最坏情形约为 `2*Rmax`。如果跨步复用邻居表，还须计入建表和迁移预留的 skin。精确的类型对半径及 ZBL 约束须从已加载的势参数推导。

<a id="52-两个可行通信方案"></a>
### 当前的位置交换与待实现的中间场交换

局部域分解模式已采用两跳位置边界。rank 先获得所需坐标和类型，再在本地重算依赖中心的描述符与有向部分力，最后只对负责原子求最终力。这样只需小幅修改核函数，但会保存更多边界副本，并重复一部分邻居中心计算。

```text
exchange positions/types to two-hop bound
→ build rows and compute descriptors/partials for owned + dependency centers
→ final force only on owned
```

分阶段中间场交换方案是首选的长期方向，尚未实现。它只交换一跳坐标，每个中心的负责进程计算中间量，再把邻居需要的值发送给相应进程。

```text
one-hop position/type halo
→ owner computes descriptor fields
→ exchange Fp（radial）
→ owner computes directed angular partial
→ exchange required reverse f12/edge data
→ final center-owned gather
```

该方案由中心汇总最终力，无须将副本的力反向传回。但必须定义两次中间量前向交换，并为跨 rank 的有向边定义稳定匹配键。另一种做法是发送 `Fp+sum_fxyz`，让接收方重算部分力。两者的通信量和计算量仍待实验比较。

## 6. small-box 路径

触发条件由 `get_expanded_box()` (`src/force/nep.cu:1269-1354`) 决定：任一周期方向厚度 `<=2.5*(rc_radial_max+1)`。数据结构硬编码每原子最多 2000 个候选 (`nep.cu:1369-1380`)，未见越界保护。

| kernel | launch/读写 | atomic | MPI 后果 |
| --- | --- | --- | --- |
| `find_neighbor_list_small_box`，`src/force/nep_small_box.cuh:56-132` | 每中心扫描 `[N1,N2)` 所有原子和 expanded images；写 radial/angular ELL 及 6 组 float r12 | 否 | 同一 global atom 可因不同 image 多次出现，但 `NL` 只存 atom index，image 由 r12 区分 |
| `find_descriptor_small_box`，`nep_small_box.cuh:134-293` | 读显式 r12、type/list；写 center PE/Fp/sum | 否 | descriptor 语义与 large 相同，无邻居 descriptor 读 |
| `find_force_radial_small_box`，`nep_small_box.cuh:398-494` | 每中心/edge；只读 center `Fp`；写 `±f` 到 n1/n2，virial 到 n2 | **是** double atomic | ghost 会收到 partial force/virial，必须 reverse exchange |
| `find_force_angular_small_box`，`nep_small_box.cuh:496-618` | center Fp/sum + explicit edge r12；写 n1/n2 | **是** | 同上；不使用 separate partial/gather 两 kernel |
| `find_force_ZBL_small_box`，`nep_small_box.cuh:620-703` | angular list + r12；写 n1/n2 force、n2 virial、center PE | **是** | 同上；多 image identity 必须保留 |
| `atomicAdd(double*)` 的后备实现，`nep_small_box.cuh:24-37` | 在旧架构上用 CAS 实现 double 原子加法 | 是 | 累加顺序不确定，rank 数和调度可改变低位 |
| `apply_mic_small_box`，`nep_small_box.cuh:39-54` | 按扩展盒分数坐标计算最小像 | 否 | 须同时验证周期像生成逻辑 |

large/small 的力分解不同，不能只替换邻居构建器后共用同一通信协议。

## 7. `NEP_MULTIGPU` 的 N/M 含义

证据：结构注释 `src/force/nep_multigpu.cuh:21-55`，分区计算 `src/force/nep_multigpu.cu:1416-1549`，pack/scatter kernel `1249-1310`，计算域 `1591-1753`。

cell 宽度是 `rc/2` (`nep_multigpu.cu:1424-1427`)。沿一个 partition direction，每个 GPU 的逻辑窗口为：

```text
local:  0             N4      N1            N2      N5             N3
        | coord-only L | desc L|    owned    | desc R| coord-only R |
global:             M0        M1            M2

坐标可用域:          [0, N3)       （owned 两侧各 4 cells ≈ 2rc）
descriptor/partial: [N4, N5)       （owned 两侧各 2 cells ≈ rc）
最终 force:          [N1, N2)       （owned）
```

具体含义：

- `N1`：local packed array 中 owned 段起点。
- `N2`：local packed array 中 owned 段终点。
- `N3`：local coordinate/type 可寻址终点；含左右各 4 cell 层。
- `N4`：需要构建邻居、descriptor 和 angular partial 的 local 起点；比 owned 左扩 2 cells。
- `N5`：同一计算域终点；比 owned 右扩 2 cells。
- `M1`：GPU owned slab 在 GPU0 `cell_contents` 全局排序数组中的起点。
- `M2`：owned slab 的全局终点/右段起点；最后 GPU 用 `0` 表示周期 wrap 到数组开头。
- `M0`：左侧 4-cell 坐标窗口在全局排序数组的起点；第一 GPU 指向数组末端以实现周期 wrap。

此处 `M0`/`M1`/`M2` 为参考代码的数组边界，不是运行模式名称。`distribute_position` 用 `M0/M1/M2` 从 GPU0 的全局 type/position 和全局 `cell_contents` 打包 local `[0,N3)` (`1249-1280`)；`collect_properties` 用 `M1` 把 local owned 结果散回 GPU0 的全局数组 (`1283-1310`)。

可映射到 MPI 的仅是概念：

| 概念 | MPI 映射 |
| --- | --- |
| `[N1,N2)` | owned atoms |
| `[0,N1)∪[N2,N3)` | 坐标 ghost，深度约 2rc |
| `[N4,N1)∪[N2,N5)` | 为 owned force 提供 descriptor/partial 的 dependency centers |
| 三个不同计算域 | owned、intermediate-compute halo、coordinate halo 的明确区分 |

不能映射/必须删除的实现：

- GPU0 持有全局 `N` type/position/force/PE/virial；
- GPU0 建全局 cell list并把 prefix D2H；
- 一个进程枚举和切换所有 GPU；
- GPU0 串行打包、D2D 分发、同步所有 GPU、回收全局输出；
- 只沿一个方向切 slab、固定 2/4 cell halo，并假定 `rc/2` cell；
- 最多 16 GPU 的固定数组/单节点设备可见性假设。

`NEP_MULTIGPU` 的两个边界层为区分坐标域和中间量计算域提供了代码依据。MPI 实现自行管理每个 rank 的设备和数据。

## 8. 同步依赖

上游单 GPU 的默认 stream 按以下顺序执行。

```text
PBC
→ clear force/PE/virial
→ [distance check]
→ [cell count → scan → contents → global NL → sort → update reference]
→ filter radial/angular
→ descriptor/PE/Fp/sum
→ radial force (reads neighbor Fp)
→ angular partial
→ many-body force (reads reverse partial)
→ optional ZBL
→ VV second half
→ thermo reduction
→ optional dump reduction/copies
```

位移检查决定是否重建。方括号里的 cell 计数、扫描、填表、排序和参考坐标更新只在重建时执行。每 1000 次 large-box 计算，程序还把所有邻居计数复制到主机并追加写入 `neighbor.out`（`nep.cu:1007-1025`），产生同步和隐式输出。DMG-MD 保留该输出。局部域分解模式在本次类型对过滤后归约最大值，仅由 rank 0 写出。[调用计数和字节数](../standards/mpi-runtime.md)

跨 GPU 时须显式保证数据可用。当前局部实现先完成所需迁移和位置 halo 刷新，再从本地依赖中心生成描述符与部分力，最后计算负责原子的力。启用重叠时，独立中心可提前计算，边界中心仍须等待其输入。需要热力学量时，最终力计算完成后才能归约并交给恒温器或输出。未来若采用分阶段中间场交换方案，还要确保 `Fp` 和所需反向边的部分力送达后才计算依赖它们的最终力。默认 stream 或碰巧阻塞的设备到主机复制均不能替代这些依赖关系。

## 9. 当前复现与后续边界

兼容层已复现 NEP 数学辅助函数和大盒核函数。局部域分解模式分别指定中心域和候选域，按全局 ID 排序邻居行，用两跳位置边界取得求力所需坐标。小盒仍走单 rank 或全量复制模式。DMG-MD 未采用 `NEP_MULTIGPU` 的 GPU0 分发和回收方式。

可选的掩码分区保留 L 步长及每个中心的运算顺序，默认关闭（O2）。显式启用计算与通信重叠时，可以在开始和完成 halo 交换之间执行独立计算（O3/O4）。这些调度属于 DMG-MD。分阶段中间场交换方案仍待实现。[分区](../standards/domain-partition.md)、[halo 生命周期](../standards/halo-lifecycle.md)、[扩展计划](../plans/domain-decomposition.md)、[O4 验收](../status/validation/communication-computation-overlap.md#overlap-o4-20261016)、[O5 重叠计划](../plans/communication-computation-overlap.md)
