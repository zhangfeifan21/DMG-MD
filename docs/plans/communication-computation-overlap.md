# 通信与计算并行研究/实施计划

类别：待实施计划。状态：**PROPOSED**。优先级：P0（与 CPU 使用研究并列，当前主要研发方向）。

## 研究目标

把 M2a 当前的“发起 halo 请求—等待所有请求完成—计算整个本地 NEP”执行链改为有正确依赖证明的通信与独立计算重叠。论文/软著/专利价值必须由明确的新方法、正确性证据和可复现性能收益支撑；单纯将 `MPI_Isend` 改名为异步或增加 stream 不构成技术贡献。

## 当前实现与可并行区域

当前 halo 交换在 `src/mpi_runtime.cu` 的 `p2p_exchange_bytes` 里排入左右 `MPI_Isend/Irecv`，随后马上 `MPI_Waitall`。上层 `MpiRuntime::exchange_p2p_indexed_device_soa` 先打包并完成这次交换，再解包；`src/domain_runtime.cu` 在 `refresh_ghost_positions` 返回后才运行域分片 NEP。CUDA 路径含同步/设备就绪约束，当前默认 stream 与阻塞语义没有跨越通信等待的在途计算。因此这是明确的优化机会，但已有 `Isend/Irecv` 不等于 overlap。

NEP 数据依赖由 `docs/standards/kernel-inventory.md` 与 `docs/plans/domain-decomposition.md` 描述：owned 力中心需要本地坐标 halo；边界 descriptor/partial 还涉及 dependency centers。任何重叠必须证明正在算的 interior 原子不会读取未到达 halo，也不能让 MPI 读写 buffer 时 GPU 改写它。

## 候选切入点（由低风险到高潜力）

### 1. Halo pack / transfer / unpack 管线化（先做基线）

把 pack GPU kernel、device-ready event、MPI 请求、receive completion、unpack GPU kernel 做成显式状态机；用显式生命周期保证 send buffer 在 request 完成前不复用；单个在途交换不强制双缓冲，只有证明需要跨批次在途复用时才引入双缓冲。HostStaged 使用 pinned host buffer 与异步 D2H/H2D；CudaAware使用设备 buffer，但必须针对目标 Open MPI/UCX 确认 device-buffer completion/progress。该项减少主线程等待或 staging 间隙，可单独量化。

### 2. Halo 进行时计算严格 interior NEP（首个科学切口，推荐）

将 owned force centers 依据坐标 halo 依赖划分为 interior 和 boundary。流程：

1. 发起 halo 的 pack/send/recv；
2. GPU 先计算不会访问 ghost 坐标、descriptor/partial 的 interior centers；
3. 主线程通过 `MPI_Testall`/分块测试推动通信进展，或站点 MPI 异步进展成立时在 GPU interior kernel 期间等待；
4. 请求完成后解包并更新 ghost；
5. 计算 boundary centers；
6. 汇合后进入 VV2/thermo。

先仅对 M2a 的深位置 halo 实现；用 dependency proof 生成 interior mask/center list，三原子两跳链及边界 fixtures 作为反例防线。严格验证每个 interior NEP kernel 的所有间接读取仍在本 rank 可用域中；不能只按单一 cutoff 削区。

### 3. descriptor/partial 分阶段交换与边界力计算

与 M2b 呼应：owned/中心 rank 计算 Fp 和 directed partial 后 exchange，缩短深 coordinate halo 与冗余 descriptor 计算。它是内存/通信算法变化，潜在新颖性较强，但依赖 edge/image 匹配、ghost reverse-force 或 forward intermediate 的完整证明；应先以 M2a oracle 做纯正确性版本，再与 interior overlap 结合，避免同时引入两类误差。

### 4. CUDA stream + MPI progress 的分层 overlap

独立 compute stream 运行 interior kernel，通信 stream 排队 pack/unpack/copies；用 CUDA event 建立生产/消费依赖。MPI 调用仍由初始化线程执行，除非显式升级 `MPI_Init_thread` 并确认线程级别。避免默认 stream 的隐式同步；CudaAware MPI 是否真正异步进展必须 benchmark，而不是假设。

## 推荐实施顺序

1. 静态依赖表 + 当前纯串行时间分解，先记录 pack、MPI wait、unpack、整段 NEP；interior/boundary 独立时间须在分区实现后实测，不从现有整段计时臆造。
2. 增加无重叠分段计时和 timeline/profiling baseline；HostStaged、CudaAware各测。
3. 实现 interior-only overlap 的最小原型，先默认关闭并用单一环境开关启用。
4. 验证 P=2/4、两后端、空 rank、周期两端、skin rebuild、迁移和多段 run；per-atom energy/force/virial 对照 M2a oracle，之后跑 NVE/NVT 和 benchmark。
5. 做强扩展重复实验：至少 carbon_200k、carbon_1m、water_400k，1/2/4/8 GPU，warmup与重复按 benchmark manifest；提供同步版/重叠版置信区间、加速比、并行效率、overlap ratio、通信进展策略及 profiler trace。
6. 对非收益负载保留自动回退或关闭开关；只有在有稳定收益且正确性不变时才成为默认策略。

## 量化指标

`T_comm`、`T_pack`、`T_unpack`、`T_interior`、`T_boundary`、`T_wait`，以及每步总时间、总通信字节和 GPU kernel 时间。
计时必须注明时钟、开始/结束事件、是否含 staging/同步。串行路径的 transfer/wait 汇总不能直接当网络时间。

- `min(T_comm,T_interior)/(T_comm+T_interior)` 仅是这两个理想串行阶段可节省的时间比例上界，
  不是整步加速上界。`T_comm` 包含哪些等待/搬运由 O1 明确定义。
- 同一 rank、同一步、同一时间轴上，令 C 为通信请求在途区间的并集，I 为独立 GPU 计算区间的并集，
  报告 `O = duration(C ∩ I)` 和 `R = O / min(duration(C), duration(I))`；分母为零记 N/A。
  这是“请求在途与计算交叠”，不能单独证明网络确实在传输；需结合 progress 实验与可取得的传输 trace。
  不用区间包围盒代替并集，不跨 rank 累加后冒充关键路径隐藏时间。
- 性能主指标用无详细 profiler 的 A/B 正式段墙钟：`speedup = T_serial / T_overlap`，
  强扩展 `S(P)=T(1)/T(P)`、`E(P)=S(P)/P`；单卡缺失则不报告该项，不能补造分母。
- 区分 setup、首次 rebuild、后续 rebuild/迁移、普通步和输出步。同步采集 CPU 时间、core-equivalent
  与轮询次数，避免把忙等 CPU 或 profiler 同步误写成免费通信隐藏。

## 主要风险与研究贡献表述

MPI 非阻塞请求不保证无 CPU 参与的后台进度；若需要周期 `MPI_Test`，其轮询频率与 CPU 成本需测量。CudaAware 的设备指针支持不保证网络传输与 kernel 自动并发。CUDA kernel 划分会改变累加/调度但不能改变数学结果；NEP 间接两跳依赖尤其容易遗漏。M2b、新 stream 与 MPI progress thread 不宜一次性合并实现。

可研究的贡献点是“基于 NEP 依赖闭包的安全 interior/boundary 分区 + GPU halo 数据面 + 可控 MPI progress 策略”，并以真实 overlap 时间线、rank/GPU scaling 与正确性 oracle 证明。能否构成论文、软著或专利取决于文献检索、技术新颖性审查、导师/学校知识产权规则及实际结果，本计划不作可授权性承诺。

## 参考资料

- [NVIDIA CUDA Programming Guide：Asynchronous Execution](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/asynchronous-execution.html)：stream、异步拷贝及 pinned host memory 的重叠条件。
- [MPI Performance Guidelines：Ensuring Progress for MPI Nonblocking Operations](https://mpi-performance-guidelines.github.io/progress.html)：非阻塞操作的 progress 与 `MPI_Test` 参与进展问题。
- [Anderson & Glotzer, Strong scaling of general-purpose molecular dynamics simulations on GPUs](https://www.sciencedirect.com/science/article/pii/S0010465515000867)：GPU 分子动力学强扩展中重叠通信与计算的动机。
- [Redesigning GROMACS Halo Exchange: Improving Strong Scaling with GPU-initiated NVSHMEM](https://arxiv.org/abs/2509.21527)：GPU 发起 halo 的近期研究方向，可用于比较创新点与技术路线。

## 分阶段目标与范围

首轮目标仅为 **M2a 深位置 halo 的普通步 interior overlap**。M2b、3D 分解、通信半径改变、
新势函数、NEP 公式重写和 MPI progress thread 均不包含在首轮实施范围。
先保留 rebuild/迁移步同步路径；是否扩展其重叠由普通步结果另行决定。
P=1、M1 fallback、空 interior 必须有明确且可测试的同步行为。

| 阶段 | 目标 | 验收后允许推进 |
|---|---|---|
| O0 | 逐 kernel 依赖闭包、分区规则、反例、golden 设计 | 维护者确认影响布局/NEP 接口等的实现方向 |
| O1 | 串行时间线、CPU 基线、A/B 编排和指标定义 | 收集到可复核基线后进入 O2 |
| O2 | interior/boundary 分区，但全部串行运行 | 正确性通过；分区覆盖与缓存失效有证据 |
| O3 | begin/progress/finish 生命周期，仍立即 finish | 两后端、请求/buffer 生命周期与回退通过 |
| O4 | 只接入普通步真实 overlap，默认关闭 | 数值矩阵与时间线交叠验收通过 |
| O5 | 正式 A/B、CPU 成本与强扩展报告 | 有证据后再决定是否扩大范围/默认启用 |

### 联合执行顺序

推荐派发顺序：**C0 → O0 → C1 → C2 → O1 → O2 → O3 → O4 → C3 → O5**。
C0/O0 是独立审计，可在隔离工作树分别完成；C3 不依赖 overlap，资源允许时可提前。
CPU 采样器、元数据 schema 和 benchmark 运行器由 CPU 线先交付，O1/O5 复用，避免两条线重复实现。
GPU 正式实验在同一资源上串行调度；多个 agent 不同时修改公共 runtime/benchmark 文件。

每次交接至少包含：实际 revision 与 dirty patch/哈希、改动文件、精确命令、已跑/未跑矩阵、
原始产物位置、PASS/FAIL/PARTIAL、遗留问题和下一阶段可用接口。仅有 HEAD 不足以识别未提交实现。
本计划保持 PROPOSED；细化 prompt 不代表已实施，也不自动批准 M2b 或 NEP 数学修改。
若维护者已经确认具体方向，后续 agent 复用该确认，不重复申请；否则按 AGENTS.md 在证据准备完毕后确认。

## O0 证据、闭包与串行分区设计（2026-10-08）

**O0 COMPLETE（静态审计与设计）；实现方向待维护者确认，O1–O5 未执行。**
本节是 PROPOSED 合同，不是已实现的 overlap、正确性通过或性能收益声明。
只修改本计划；未修改生产代码、未构建、未启动 GPU/MD/正式矩阵、未提交或推送。

### 基线与审计范围

- newmd HEAD `1d704ef6a5ad65570b4ac781bb381a06d224cc95`；只读参考
  `9d23496e41319b9e2af5221a7df6285387401d1e`，参考工作树干净。
- 开始时已修改 `README.md`、`docs/README.md`、`docs/plans/multi-node-io.md`、
  `docs/plans/risk-and-backlog.md`、`docs/status/current.md`；未跟踪本计划、CPU计划、
  `docs/status/benchmark-multigpu-results-20260926.md`。全部保留，CPU计划含已完成C0，
  本轮不重写C0。文档与源码以本次工作树为准，下面路径/行号均指newmd，参考树另行标明。
- 依据：本计划、[CPU计划/C0](./cpu-usage-study.md)、
  [kernel-inventory](../standards/kernel-inventory.md)、[data-layout](../standards/data-layout.md)、
  [replicated-mpi](../standards/replicated-mpi.md)，并逐项核对实际读写，不能以旧标准中参考行号
  或几何示意代替当前kernel证据。构建边界仍为仓库内 `gpumd_compat`，不链接参考源码。

### 1. 现有调用顺序与符号证据

`src/domain_runtime.cu:1500 run_domain_segment` 每步完成修速/adaptive、VV1/wrap、
`inspect_domain_cache:191` 和位移MAX/reason OR（1553–1595）后，才决定ordinary/rebuild。
普通步1627调用 `refresh_ghost_positions:880` →
`src/mpi_runtime.cu:1288 exchange_p2p_indexed_device_soa` → pack、staging/device-ready、
`Impl::p2p_exchange_bytes:722–735` 的两方向Isend/Irecv/立即Waitall、staging、unpack。
然后1637调用 `launch_domain_force:1108`：检查mapping/refreshed epoch与cache decision，
1133清零**全local** force/PE/virial，1140–1145设置force中心`[0,owned)`、dependency中心
`[0,owned+dep)`（owned=0时dependency中心也置空），1146调用 `NEP::compute_domain`。
VV2在1644，thermo/Allreduce在1660，输出在1725以后；首轮不能提前VV2或改变这些物理顺序。

`src/gpumd_compat/nep.cu:1230 NEP::compute_domain` 当前顺序：

```text
Neighbor::find_neighbor_domain → typewise filter(D)
→ 周期neighbor统计（D2H / CPU / MPI MAX / root I/O）
→ descriptor(D) → radial(O) → partial(D) → many_body(O) → ZBL(O)
```

所有这些kernel launch使用两参数 `<<<grid,block>>>`，没有显式stream；属于调用线程默认
stream，不能直接称为已存在独立通信/计算stream。`neighbor.cu:346`的scan使用
`thrust::device`，未传stream。`error.cuh:103–115 GPU_CHECK_KERNEL` 在STRONG_DEBUG下逐次
device synchronize，普通构建只检查错误；未来profile必须记录此编译开关与default-stream设置。

参考核对锚点（只读）：`../gpumd-reference/src/force/nep.cu:436/488/661/774/863` 分别是
filter/descriptor/radial/partial/ZBL；`src/force/potential.cu:170 gpu_find_force_many_body`
为反向partial gather。当前domain版本以global ID排序/查反边，不能照抄参考的local-index排序。

### 2. 逐kernel读写表

统一记号：`L=local_count`；`O=[0,owned)`；`D=[0,owned+dep)`，owned=0时D=∅；
`G=[owned,L)`；`V(c)`是当前cache epoch内**未按typewise cutoff过滤的完整Verlet行**。
`R_t(c)`、`A_t(c)`是本步重新过滤的radial/angular行，`A_t⊆R_t⊆V`。
SoA/ELL/Fp/sums/partial均以**L而非集合大小或capacity**为stride：
位置`axis*L+c`、Fp`d*L+c`、sums`q*L+c`、partial/邻居`edge_ordinal*L+c`。
下表“默认”表示当前默认stream；前置条件也包括相应buffer尺寸/epoch有效。

| kernel/调用位置（当前源码） | 中心、邻居/间接读取 | 写入域与次序 | stream、前置完成、MPI影响 |
|---|---|---|---|
| `velocity_verlet_range`，domain_runtime.cu:56，launch1156 | O；mass、旧force、velocity，VV1还读position/displacement | O velocity；VV1另写position、连续epoch displacement（wrap前累加） | 默认；上步force已完成；VV1在pack/early前完成，VV2必须等全部本步force |
| `update_unwrapped_range`，domain_runtime.cu:92，调用1161 | O的新position和previous position | O的unwrapped += 位移 | 默认；VV1后，独立于halo；本步迁移/输出消费者须等其完成 |
| `wrap_positions`，runtime_internal.hpp:490，调用domain_runtime1539 | O position、Box | 仅O position，单次PBC调整 | 默认；VV1后；ghost不积分、不wrap覆盖 |
| `inspect_domain_cache`，domain_runtime.cu:191，调用1563 | O position/velocity/displacement，box/axis | device max/reason标量atomic | 默认；D2H后host MAX/OR，continuity错误仍按原路径全rank报错；分类不能绕过此决策 |
| `pack_indexed_soa`，mpi_runtime.cu:188，调用1332/1338 | face send_slots ⊆ O，本步position，L stride | 独立AoS send buffer，左右分区 | 默认；owned-ready后；MPI读send须等pack及所需D2H完成 |
| `unpack_indexed_soa`，mpi_runtime.cu:207，调用1396/1402 | 独立recv AoS、face recv_slots ⊆ G | 只写指定G坐标 | 默认；MPI recv completion、H2D/device-ready后；boundary须等unpack，early不得读这些位置 |
| `clear_owned_properties`，runtime_internal.hpp:525，调用domain_runtime1133 | 不读旧输出 | `[0,L)` force(3)、PE(1)、virial(9)赋0，**不清Fp/sums/f12** | 默认；每次逻辑force恰一次、所有贡献之前；不能在两分区之间再全量清零 |
| `find_cell_counts` / `thrust::exclusive_scan` / `find_cell_contents`，neighbor.cu:89/346/112，调用342/346/351 | 全L位置→cell id；scan读所有cell counts | cell counts/prefix/contents（counts有atomic） | 默认/未指定stream的device scan；重建必须halo坐标有效，涉及全L，不允许作为early独立工作 |
| `gpu_find_neighbor_ON1_domain`，neighbor.cu:226，调用678 | 中心D，cell枚举候选全L；MIC坐标差，float距离与`(rc+skin)^2` | NN[c]、NL[e*L+c]；overflow flag | 默认；cell list完成；overflow D2H安全报错，不截断后继续 |
| `gpu_sort_neighbor_list_domain`，neighbor.cuh:164，调用neighbor.cu:719 | D的NN/NL、候选gid | 同行NL按gid排序，存储值仍local slot | 默认；建表后；不能按interior集合重排槽位或ELL行 |
| `gpu_update_xyz0`，neighbor.cu:494，调用724 | 全L位置 | x0/y0/z0 reference | 默认；重建收尾；ordinary confirmed_reuse不重建/不重新判位移 |
| `find_neighbor_list_large_box`，nep.cu:493，调用1297 | 中心D；逐个读取**V(c)全部候选**位置/type，之后才做float MIC与pair cutoff判断 | NN/NL radial/angular的中心行，覆盖当前有效计数/entries | 默认；该中心整条V坐标有效；即使候选最终被cutoff拒绝，也已经读取了坐标 |
| `find_descriptor`，nep.cu:550，调用1355 | D；R/A中的位置/type；ANN参数；不读邻居Fp | **PE[c] += F**，Fp与sum_fxyz中心列赋值；普通model不写virial | 默认；自身R/A当前；同中心必须只执行一次，不能为每个interior重复计算公共辅助中心 |
| `find_force_radial`，nep.cu:726，调用1383 | O；R(c)坐标/type；**Fp(c)和Fp(j)**，两种有向type参数 | force[c]与9 virial[c] +=，无邻居scatter | 默认；自身filter及`{c}∪R(c)`的descriptor完成；无需reverse force MPI |
| `find_partial_force_angular`，nep.cu:843，调用1406 | D；A(c)位置/type；**只读中心**Fp(c)、sum_fxyz(c) | f12x/y/z[e*L+c]赋值，对本中心所有有效angular edges | 默认；自身descriptor/filter完成；这是directed(c→j)，不是最终force，也不读Fp(j) |
| `gpu_find_force_many_body_domain`，potential.cu:218，经383 wrapper，调用nep1427 | O；A(c)坐标；本向f12(c→j)，j行计数/NN/NL及global_id二分查找，读**反向f12(j→c)** | force[c]与9 virial[c] +=；无atomic scatter | 默认；两端A行/partial完成；二分探测可读j行中其他gid，不能只验证一个反边地址 |
| `find_force_ZBL`，nep.cu:935，调用1443 | O；**A(c)**位置/type/原子序数，普通/柔性/typewise ZBL参数；不读Fp/partial | PE[c]、force[c]、virial[c] += | 默认；filter完成，按原顺序在many_body后；ZBL不扩大读取范围到angular行之外 |
| `find_owned_thermo_sums_range`，domain_runtime.cu:109，调用1180；`normalize_global_thermo`，runtime_internal.hpp:546，调用1194 | 只O的PE/virial/velocity/mass | 8标量local sum→MPI Allreduce→normalize | 默认；全部owned force/VV2完成，ghost PE scratch不得纳入 |

`scale_owned_velocity_range`（domain_runtime.cu:160，调用1690）只读写O velocity，位于本步thermo之后；不属于提前force阶段。`gpu_check_atom_distance`（neighbor.cu:445）用于legacy路径，domain confirmed_reuse不重复调用它；domain只有上述全局统一decision。

`nep.cu:1262–1351` 的neighbor统计是host副作用而不是独立NEP kernel：每1000次force调用
（含首次）遍历D counts、调用 `domain_runtime.cu:1982 neighbor_record_sink` 的两次MPI_MAX。
空rank也必须参加。它必须每次逻辑force只推进一次call_index，不能随分区调用次数翻倍。

### 3. 严格interior：以实际Verlet读图作保守闭包

**首轮选择：不以几何距边界或本步radial/angular行分类，使用缓存V图的两跳闭包。**
缓存建立后定义：

```text
S = { c∈O | ∀j∈V(c), j∈O }                    # 自身filter/descriptor/partial可提前
I = { i∈S | ∀j∈V(i), j∈S }                    # 可提前完成最终force的owned中心
B = O \ I
E = I ∪ ⋃(i∈I) V(i)                           # early辅助中心，E⊆S⊆O⊆D
H = D \ E                                    # halo完成后计算的dependency中心
```

这是充分条件，不求最大interior。`V(i)=∅`的owned可进入I；无owned时I/E/D均空；
I为空保持全串行force，但仍履行两方向通信/诊断collective。集合包含关系按manager-owned
槽位判断，不按当前几何slab判断（owner在cache epoch内可暂时跨slab）。

分类器只读取静态NN/NL与owned边界，先验证行count/slot范围，再两遍生成S和I，最后用整数
mask并集生成E；不读未刷新ghost坐标，不读尚未更新R/A/Fp。只读O的V行，不能对coordinate-only
ghost访问不存在的row。O2可用CPU纯图oracle；生产实现宜在device按行生成mask，避免每步
全表D2H；每次V重建后发布分类，ordinary复用，不引入新的MPI集合归约。

**多元素、两跳、skin、ZBL与周期条件：**

- filter实际为`Rr=(float rc_r[a]+rc_r[b])*0.5f`，angular还要求`d²<Rr² && d²<Ra²`
  （nep.cu:532–544），不得拿一个平均cutoff划几何内区。
  `include/dmgmd/domain_layout.hpp:78 compute_domain_radii` 已枚举type-pair及type-chain，
  `d_dep=max R_force+skin`、`d_coord=max(R_force+R_dep)+2skin`；本提案不改变这两个halo半径。
  图规则直接使用现有实际生成的V，不在host复算float cutoff，不收紧typewise halo。
- 最终force依赖邻居Fp/反向partial；它们又依赖邻居的完整descriptor位置集合，所以必须两跳。
  更容易漏的是V中当前cutoff外候选：filter先读再拒绝，所以只用当前R/A闭包仍可能读旧ghost。
  本判据覆盖整个skin带，以及类型较大cutoff让辅助中心读到的第二跳。
- Neighbor::find_neighbor_domain（neighbor.cu:587，655–663）以`rc+skin`建V；
  ordinary沿用它。全局位移判定保证每个manager自epoch参考的位移≤skin/2，任意两端相对漂移
  受skin约束；连续位移在wrap前累计（domain_runtime.cu:79–92），不从MIC后的坐标差猜位移。
  本证明继承现有M2a的neighbor有效性合同，并证明相对**相同V、相同本步坐标的串行实现**等价；
  不额外宣称修复浮点临界邻居完整性。精确阈值/舍入行为继续用原kernel和现有golden验证。
- 当前layout每global ID只允许一个local slot（domain_layout.hpp:425–467），ghost的
  image_shift是元数据，存储位置仍wrapped，由MIC取距离（295–297）。P=2两face同peer时
  `build_exchange_plan:517` 去重send，左右tag仍分开（mpi_runtime.cu:261–263）。
  V跨周期端也按slot/gid保留；不能用裸坐标距离排除ghost、不能把image_shift再加到坐标。
- angular反边按gid查找，但f12地址使用**本步各自row的edge ordinal**，不持久化ordinal映射。
  ordinary的V固定不代表filtered angular edge ordinal不变。O2必须逐步检查gid有序、
  `(gid_i,gid_j)`反边存在且唯一；当前many_body二分没有独立“not found”安全分支，不能把
  该隐含前提忽略。非法反边属于结构错误，诊断失败退出，不能靠宽容差掩盖。
- ZBL即使rc_outer更大也只遍历A；柔性/typewise只改每edge的数值函数，闭包不扩大。
  本设计保持该参考语义，不把ZBL另建邻居表。小盒显式多image/Newton scatter、triclinic、
  非周期以及重复gid image布局不在分区范围，走原支持/拒绝路径，不能推广本证明。

### 4. 提前可用性、无遗漏与无重复写证明

在ordinary的VV1/wrap完成后，O的本步position、type、gid均可用；V、box、参数与layout固定。
对任意`c∈E`，`c∈S`给出`V(c)⊆O`，所以c的filter、descriptor与全部directed partial只读本步
owned坐标，不需要halo。对任意`i∈I`，`{i}∪V(i)⊆E`，所以radial所有Fp与many_body的两向
partial均由E本步生成；反边二分遍历的j行/gid也已生成且静态gid有效。ZBL只读A(i)⊆V(i)。
因此early最终force的**整个实际读集合**已闭合，不是仅“i远离边界”的推断。

`E\I`是可能属于boundary最终force的辅助owned中心：必须提前算它的descriptor/partial，
但不提前算它的最终force。仅按I算descriptor会漏Fp/反向partial；对每个i重算E会使PE重复累加。
每步恰好使用下面日程（O2两段都在halo完成后，O4才移动early到通信在途）：

```text
clear(0..L) once
filter(E) → descriptor(E) → radial(I) → partial(E) → many_body(I) → ZBL(I)
[halo完成且unpack可见；O2在上述clear之前就已完成]
filter(H) → [若需记录，全D counts就绪后执行一次neighbor统计]
descriptor(H) → radial(B) → partial(H) → many_body(B) → ZBL(B)
force_done → VV2 → thermo → output
```

- `I∩B=∅, I∪B=O`：最终radial/many-body/ZBL每个owned恰一次。
- `E∩H=∅, E∪H=D`：filter/descriptor/partial每个dependency中心恰一次。
  descriptor PE是`+=`（nep.cu:715），所以辅助E必须从late descriptor排除；Fp/sums虽为赋值，
  也禁止晚段重算E，以免与早段consumer竞态。H可包含ghost，其PE仍仅scratch。
- I的PE先descriptor后ZBL；E\I的PE早段descriptor、晚段ZBL；H∩O的PE晚段两项。
  每个owned内部浮点累加次序保持descriptor→ZBL、force/virial保持radial→many_body→ZBL。
  partial与radial的相对顺序沿用当前日程；各中心独立累加、large-box无邻居force atomic，
  不重排其邻居行，不改变每线程浮点表达式。
- 全L clear在所有early/late贡献前恰一次；late**不得再次调用完整launch_domain_force**，
  否则会清掉early结果。Fp/sums/f12无全局清零；每个会被消费的有效row/column必须在本步
  被唯一producer覆盖，旧的无效edge尾部不读取。debug可加producer-step标签验证，不能只靠“旧值看起来对”。
- pack只读O position、写独立send；early只读O position、写NEP输出/workspace；unpack只写G
  position，因此地址不冲突。E/H写域按中心列分离；首轮仍让late在early_done后串行执行，
  不试图并发两套NEP kernel。下一步VV1、迁移/resize必须等待本步force和send/recv生命周期结束。

**证明的边界**：这证明提出的选择/日程在前提成立时与原M2a串行数据依赖一致，尚无分区实现
或GPU数值验证，bitwise一致和实际加速均UNKNOWN。若kernel新增读域、extra model或projection
输出，上述证明失效；首轮要求ordinary NEP4/5/NEP-ZBL、model_type=0且need_B_projection=false。

### 5. cache、epoch和回退规则

| 情形 | O2/O4必须采取的动作 |
|---|---|
| 初始force、段首兼容force | 原串行整段；建/复用V并准备分类，call计数只增一次；段首不凭空触发额外rebuild |
| ordinary、全局confirmed_reuse、所有key相同 | 复用基于V的I/E mask，不根据陈旧ghost坐标动态扩大I |
| displacement > skin/2，或routing需迁移 | rebuild当步保持原同步transaction；do_migration:936与exchange_halo_membership:763重排/上传后旧mask失效，等新V建完再生成mask |
| 微小跨slab/周期往返，但未到rebuild阈值 | manager-owned不变，继续按slot O判定，不按geometric owner误删/增加I；周期MIC不改变gid身份 |
| stride、O/D大小、gid排列、mapping/layout epoch变化 | 立即失效；shape恰好相同也不得复用旧mask。key包含layout/mapping epoch、neighbor generation、O/D/L、box/parameter版本；裸指针不是key |
| workspace扩容/resize或V重建但layout epoch未变 | 旧view失效；单独neighbor generation/storage generation防止同epoch地址变化或强制重建漏失效；不得在请求或kernel在途时resize/free |
| box/PBC/cutoff/type/skin变化 | 当前run支持范围无动态变盒，但接口必须失效并重验原eligibility；确认不了新V及半径则原串行路径，unsupported输入仍报错，不默默扩产品范围 |
| P=1、M1、小盒、empty interior、empty owned/local | 原同步force；empty rank仍参与原MPI/neighbor统计。可逐rank选择有无early，但MPI调用顺序/次数与tags保持一致，不引入新collective |
| 缺分类、key不匹配、未证明模型/stream条件 | 在begin/clear前选择同步fallback并记录reason；结构损坏、invalid continuity、越界按原错误路径失败，不把非法数据送入串行kernel“恢复” |

`state.refreshed_epoch`（domain_runtime.cu:304、903）只表示布局世代，不是本步halo完成证明；
同epoch每步值相同。O3/O4必须另有`step_id / owned_ready / halo_unpacked / force_done`状态。
不能为了提前force简单移除 `launch_domain_force:1112` 的检查或谎设refreshed_epoch。
早段验证owned-ready与cache-key，晚段验证本步halo-ready；异常发生在begin之后须受控完成/终止
本次请求，不在rank间随意切换collective分支，不再次推进同一个逻辑force call。

### 6. O2最小接口及预计修改符号（草案，未实施）

优先**mask选择、保持原launch grid/block与原L stride**：`n1`仍为原中心slot，在任何数据读取
前做选择guard；不把I压成新原子数组，不把N1/N2改成I大小，不复制另一个NEP数学实现。
legacy无选择路径保持原调用；可用模板/独立domain wrapper隔离选择guard，让原P1/M1路径不变。

```cpp
// 概念接口：只新增选择/状态，不改变粒子布局与NEP数学。
struct DomainPartitionKey { /* layout_epoch, mapping_epoch, neighbor_generation,
                              storage_generation, O, D, L, box/parameter_version */ };
struct DomainPartition {
  DomainPartitionKey key;
  DeviceMask interior_owned;   // L entries，只有O可能为1
  DeviceMask early_dependency; // L entries，只有E可能为1
  size_t interior_count, early_dependency_count;
};
struct DomainForceTicket { /* logical_call_index, step_id, key,
                              clear_done, early_done, late_done, record_pending */ };
// Neighbor提供不可变V视图（NN, NL, row_capacity, L, center range, generations）；不暴露可改指针。
classify_partition(neighbor_view, owned_count, key) -> DomainPartition;
prepare_domain_force(state, key, step_id) -> DomainForceTicket; // 检查并clear一次
compute_domain_early(ticket, partition); // filter/descriptor/partial E，最终force I
compute_domain_late(ticket, partition, halo_ready); // H + B，neighbor副作用一次
finish_domain_force(ticket); // 完成检查、force_done，不发明额外MPI barrier
```

`DeviceMask`/ticket/函数名是设计占位，不是已有API。分类两遍的S scratch每次分类重用，E并集
可用整数atomic置位；kernel guard必须先检查slot范围再读mask，空域不访问padding。
NEP::neighbor当前为private，新增只读view或把分类留在NEP内部，不能从外部推算其内存地址。

| 预计位置/符号 | 最小职责与禁止事项 |
|---|---|
| domain_runtime.cu `DomainState`、`launch_domain_force`、`run_domain_segment` | 保持原full入口作为oracle/fallback；新增清零一次、ticket及串行early/late编排。O2仍先完整refresh；rebuild/setup走full。 |
| gpumd_compat/nep.cuh/.cu `NEP::compute_domain`及六个large-box consumer | 增加只限domain的selection入口，descriptor/partial选E/H、最终force选I/B；共享原kernel算术；force ticket的call计数跨full/split共用，不能两个static计数器各走各的。 |
| gpumd_compat/potential.cuh/.cu `find_properties_many_body_domain` / kernel | 传owned selection，保持gid反边查找与原邻居累加顺序。 |
| gpumd_compat/neighbor.cuh/.cu `Neighbor::find_neighbor_domain` | 增加只读V视图/generation与分类完成点，维持原overflow检查、gid排序、skin及唯一cache decision；ordinary不重建cell list。 |
| include/dmgmd中新辅助partition逻辑或tests内graph oracle | 集合/失效键/纯图断言，CPU单测先行；不修改DomainRadii或LocalLayout的数据含义。 |
| mpi_runtime.cu/.hpp | O2无需改通信协议；O3另交begin/progress/finish及buffer生命周期；O4才连接异步调度，MPI仍初始化线程FUNNELED。 |
| tests/domain_neighbor_cuda_tests.cu、tests/mpi/run_mpi_domain.py等 | 增加分区/读写域/双oracle/关闭路径验证；不改golden值或放宽manifest容差。 |

**neighbor统计与timing接口**：把逻辑force开头的call_index分配与record_pending统一，
分区后统计在filter(E/H)都完成且本rank halo已finish后执行一次。所有rank必须对相同logical
call进入两次MAX，即使I为空或local_count=0。只改副作用调度不改neighbor.out文字/次数。
现有0/1/2/3 timing marker不可简单包围两次compute并相加；O2应保留原full计时及增加
显式early/late/aux/classify事件或将旧不可等价字段标N/A，不能把跨MPI的包围区间叫NEP kernel时间。
是否能保持详细timing已有schema由O1/O2一起落实并更新测试，默认关闭路径先保持一致。

### 7. 通信在途的前置条件（供O3/O4，不在O2实现）

最小有用重叠不要求pack与compute并行：可先完成pack/D2H，确认send-ready，发起全部p2p，
然后early计算与主线程Waitall/Testall并行；必须在early提交前完成现有CudaAware的input
全设备同步，否则会把early也等完才发通信。当前output synchronize仍可能截断重叠尾部，
不得在无接收可见性证明时删除。独立stream/pinned异步copy是后续可量化项。

| 对象 | 所有权/完成条件 |
|---|---|
| owned position | VV1/wrap完成→pack与early可共同只读；到本步force结束前不再积分/修速/迁移 |
| send buffers | pack/D2H或device-ready事件后MPI可读；send request完成前不可复用/resize/free；一套in-flight槽即可，首轮不跨步流水 |
| recv buffers | MPI独占写至recv complete；HostStaged再H2D，CudaAware须目标栈device完成可见性成立；之后unpack写G |
| ghost position | unpack完成后才可供H/B读取；early全闭包为O，所以即使unpack与early并行也不写读冲突 |
| NEP workspace | early_done→late；输出clear_done→所有producer；mask/V/map/key在ticket生命周期冻结 |
| MPI/stream | 应用MPI均由初始化线程调用（mpi_runtime.cu:285 FUNNELED）；库后台progress、stream-aware MPI/网络重叠均UNKNOWN。O4若显式stream，pack/unpack、consumer和event都传对应stream并建立wait，不依赖默认stream隐式同步 |

普通步halo请求数/方向tag/字节与原实现一致。E/I少也不能跳过发送邻居所需的数据。
trace至少区分owned-ready、pack-ready、request-posted、MPI-complete、unpack-done、early/late、
force-done，并含rank、step、key；复用C0单调时钟/CPU schema，不从1秒CPU采样反推毫秒级step。
`DMGMD_DOMAIN_TIMING`仅段汇总、benchmark会清除继承DMGMD_*，沿C0/O1显式白名单设计。

### 8. 反例矩阵与验收设计（尚未运行）

现有入口：`tests/mpi/run_mpi_domain.py:1131 chain`、1139 micro_crossings、1147 empty_local、
1157 crossings、1166以后NEP5/typewise/flexible_zbl/typewise_zbl，及1343 timing A/B；
`tests/domain_neighbor_cuda_tests.cu` 覆盖neighbor/stride。现有fixtures只证明原路径，不能记为
已覆盖分区。新增fixture参数统一落测试脚本/相应manifest，计划只定义必须触发的反例，不另维护容差。

| 反例/正例 | 构造与必须断言 |
|---|---|
| 两跳三原子 i–j–k | i/j为O、k为G，i-j和j-k在作用范围，i-k在范围外；i的直接邻居全owned仍必须排出I。示意slab `[0,32)`、沿轴i=23,j=29,k=35，carbon cutoff=7时两edge各6；实际fixture以加载cutoff/盒eligibility校验，不硬套任意势。 |
| filter读skin带ghost | i的所有当前R/A都owned，但V(i)包含cutoff外、rc+skin内ghost，必须排出I；poison该ghost可揭示仅按R/A分类的错误。另让pair从skin带进入cutoff而不rebuild，mask应保持安全。 |
| 非空I及辅助E\I | 四原子链owned坐标17/23/29、ghost35（同上slab/cutoff）；17可为I、23为E中的boundary辅助中心、29因ghost不能early。逐字段证明23的PE只有一次descriptor贡献，17读取到本步23的Fp/partial。 |
| 周期两端/image | 将链跨全局周期端，平移整数盒长后按支持的wrap路径构造等价输入；V gid边与分类一致，不按裸坐标误认interior；验证image元数据不重复加位移。 |
| P=2左右同peer | 同时两face非零及一face零；验证send去重、独立tag、recv slot唯一、相同gid只一个local槽位、pack/unpack不写owned。不能只测一个方向。 |
| 空I、全I、O=0、L=0 | 全boundary走原串行；局部完全闭合cluster允许全I；O=0但有ghost与真正L=0分别测，无zero-block launch/padding读取，仍参与邻居统计/thermo与对应零计数通信。 |
| 多元素/typewise | 加载混合radial/angular cutoff，交换中心/邻居类型使第二跳范围变大；含float pair平均舍入边界、angular>radial情形的现有radial-first语义。分类依据V而非单类型半径，R/A与原实现逐行相同。 |
| NEP4/NEP5/ZBL | 普通/柔性/typewise ZBL，cutoff内有非零ZBL且跨边界，含rc_outer大于angular reach；比PE/force/全部9分量virial，不仅thermo。保持angular-list截断行为。 |
| skin阈值/owner与epoch | displacement等于/刚小于/刚大于skin/2；micro_crossings小位移跨slab不重建；超过阈值+迁移、无迁移rebuild、同shape不同gid顺序、强制同epoch新V generation均使旧key无效；多盒/NaN沿原路径报错。 |
| filtered edge ordinal变化 | V不变，但新angular边进入/旧边退出，反边ordinal移动；每步按gid重新查反边，不缓存旧partial index。比较排序、NN、有效NL及有效partial。 |
| 启停/副作用 | 连续run、初始/段首force、force call0/1000（包含空rank）、修速/adaptive/NVT/XYZ/restart；neighbor.out次数和内容、step计数、通信账本与原路径相同，开关off不增加分区数据/事件。 |
| key/模型/box失配 | CPU或定向CUDA单元测试注入过期epoch/stride、box/cutoff版本变化、unsupported model；在访问旧view前拒绝/同步回退。非法row/反边不“回退后继续错算”。 |
| buffer读写隔离 | 先完整刷新后保存快照，测试工具暂poison G坐标和不应提前可用的workspace，执行early再恢复G/执行late；I及E有效产物与oracle一致且ghost坐标未被early写。guard/canary与producer-step计数验证每个中心、edge恰一次；测试专用，不污染生产数值路径。 |

**双oracle与执行顺序（O2）：**

1. CPU纯图oracle枚举小图验证S/I/E/H覆盖、辅助中心不重复、空集、过期key；分类device结果与之
   逐bit比较。邻居列表非法索引/count先拒绝，不能靠CUDA越界测试得出“fallback成功”。
2. 在完全相同输入、rank数、backend、快照/epoch/V下，对比原full M2a与**halo先完成后**的
   split-serial；test harness分别保存/恢复输出/workspace以免两个算法串接污染状态。
   先比原子global ID/row顺序/计数精确相同，再比本步R/A、有效Fp/sums/partial、逐原子
   PE/force/virial。未写的无效row尾部/coordinate-only scratch不纳入数值等价判定。
   同GPU/同二进制路径目标bitwise一致，出现差异须解释算术/编译来源，不预先降低门槛。
3. 再对同输入P=1 ordinary M1 oracle与P=2/4 split比较，沿现有domain脚本与baseline manifest的
   字段精确/容差合同（不因MPI全局归约顺序强求所有汇总bitwise）。两后端分别通过、自检回退
   不冒充CudaAware通过；测试必须断言至少一个真实普通步I非空、另有B与E\I覆盖，防止全fallback假通过。
4. 原有baseline/golden、M1 differential/migration、domain、long_nve对应验收都保留；修改compat
   必须连同 `tests/baseline`、`tests/long_nve` 重验。修速/输出/restart、空rank、连续run的原测试
   必须无回归。GPU环境门槛、source入口与精确命令按AGENTS及各runner实际help执行。
5. O3在立即finish模式重复同矩阵，验证request/buffer生命周期；O4才在延迟/不同progress调度下
   复验数据竞争与正确性，并给真实trace。O2性能只用于记录分类/双launch额外成本，不声称overlap收益。

C0已规定CPU利用率分母/缺失数据和采样扰动；O1须等C1/C2工具与baseline，不能另造CPU采样器。
本轮只做源码/文档核对，以上所有新测试与性能数据均为**NOT RUN**。

### 本轮静态校验

逐符号核对表中的kernel声明、launch、读写与参考对应关系；检查Markdown空白/代码围栏及本节本地文档链接。文件内容哈希对比确认本轮仅本计划变化，C0与其余已有修改均保留，参考仓库仍干净。没有执行新测试、编译、GPU探测或MD运行；测试矩阵只是方案。

### 9. 确认点、UNKNOWN与交接

待维护者确认的具体方向：**只在M2a ordinary步新增基于旧Verlet图的两跳mask分区，增加
NEP domain early/late选择接口与force ticket；保留原粒子布局、L stride、半径、owned语义、
每中心浮点公式/累加顺序、默认关闭及full串行oracle；先做O2串行验证，后续O3/O4另阶段执行。**
这涉及compat NEP的中心选择/接口、workspace producer调度及neighbor诊断时序，不能把本O0
审计授权解释为生产实施授权。依据 `AGENTS.md` 的要求：
“若任务会改变 NEP 数学、数据布局、精度、原子所有权、通信半径或兼容性承诺，应先完成证据和
 golden test 设计，再请求维护者确认实现方向。”
本提案不改变数学/物理布局/半径，但需验证并维持NEP数值与输出兼容承诺，故按用户本轮要求
将上述可审阅接口方向交给维护者确认；本阶段审计和方案已完成，不因待确认而少做O0内容。

UNKNOWN：实际I/E比例和收益、mask/额外launch成本、CUDA-aware接收可见性与异步progress的
目标栈细节、编译后bitwise表现、stream分离的实际重叠程度、诊断计时schema最终兼容方式。
C0没有CPU实测，O0也没有；不由源码Isend或闭包证明推断运行时并行。

下一入口按联合顺序为 **C1 → C2 → O1**；维护者确认本方向、O1基线及所需前置验收齐备后，
再执行本计划O2。O2可直接以本节集合/读写表/日程/反例为实现合同；不确定依赖回退full，
不得擅自扩大I、改变halo或跳过辅助producer。本轮不自动进入任何下一阶段。

## 可直接交给 Codex CLI 的阶段 prompts

以下每个代码块可独立粘贴，路径相对包含 `AGENTS.md` 的 **newmd 仓库根目录**。
后续阶段只在前置产物与验收真实存在时执行；缺失时完成可做的检查并指出缺口，不自称通过。

### O0：NEP 依赖证明与实现提案

```text
请执行 docs/plans/communication-computation-overlap.md 的 O0，仅做证据审计、设计与测试方案。
先读 AGENTS.md，检查本仓库和 ../gpumd-reference 的状态，保留已有修改，不提交、不推送。
阅读本计划、CPU 计划及 C0 产物（若有）、kernel-inventory、data-layout、replicated-mpi，
沿 domain_runtime、mpi_runtime、gpumd_compat NEP 的实际调用与 buffer 读写追踪，不直接引用参考仓库构建。
输出逐 kernel 依赖表：中心集合、邻居集合、间接读取、Fp/angular sums/directed partial、
写入范围、stride、global ID/edge/image、所在 stream、前置完成条件、MPI 影响。
定义严格 interior 判据，覆盖两跳/多元素 cutoff/skin/周期 image/NEP-ZBL；不能只按单 cutoff 削区。
证明 interior 的整个依赖闭包在 halo 到达前可用，列出辅助 dependency centers 的计算需求，
证明 interior 和 boundary 不漏算、不重复累计、没有 workspace 清零/覆盖冲突。
说明旧邻居表、位移界、epoch、迁移/重建和 box 变化对分类有效性的影响；不确定时回退同步。
设计三原子两跳链、跨周期边界、空 interior/空 rank、P=2 左右同 peer、多元素和 ZBL 反例。
在本计划内给出最小接口草案、预计修改符号、串行分区 oracle 与 golden 验收方案，标记 UNKNOWN。
验收：O2 可以按明确读写集合实施，不能以几何直觉替代证明。
如方案触及 NEP 数学/布局/所有权/半径或兼容承诺，依 AGENTS.md 先完成证据与 golden 设计，
再把具体待确认方向交给维护者；本阶段不改生产代码、不启动 M2b。
最后交付设计、反例矩阵、确认点与下一阶段入口。
```

### O1：串行 baseline 与实验编排

```text
请执行 docs/plans/communication-computation-overlap.md 的 O1。读 AGENTS.md、本计划、O0 和 CPU C0-C2
交接，检查两个仓库状态，保留用户修改，不提交、不推送。缺少 CPU 工具时先明确接口缺口，不另造一套。
审计现有 DMGMD_DOMAIN_TIMING：pack、staging、MPI 等待、unpack、NEP、同步调用的真实计时范围。
补齐必要且默认关闭的 trace/时间戳；明确 CUDA event 与 host 单调时钟如何关联。
不加 profiler barrier/强制同步来制造假 baseline；分别跑详细 trace 与关闭 profiler 的性能组。
建立 A/B 运行配置与 metadata，复用 tests/benchmark 的输入/随机顺序/结果保存和 CPU 采样器。
特别检查运行器清除 DMGMD_* 的逻辑，实验开关必须显式白名单传递、写入 launch_environment，
并从程序实际模式日志核验，不能只在外部 export。新增选项要 --help/dry-run/测试/README。
每个新 shell source ../env/md-mpi.sh；按 AGENTS.md 先环境预检和已有正确性门槛，先 smoke 再 pilot。
采集 HostStaged/CudaAware 的串行普通步与重建步时间线、通信字节和 CPU 成本。
未分区前只记录整段 NEP，不编造 T_interior/T_boundary；MPI 请求在途不等于网络活动。
写 docs/status/overlap-baseline-YYYYMMDD.md，包含 revision/dirty diff、环境、哈希、原始 trace、
精确命令、实际模式、开销与不可观测项，并从 docs/README.md 链接。
验收：基线可复跑、关闭诊断不改默认行为、A/B 确实能区分配置。到此停止，不接入异步计算。
```

### O2：串行分区与数值 oracle

```text
请执行 docs/plans/communication-computation-overlap.md 的 O2。读 AGENTS.md、本计划、O0/O1 产物，
检查两个仓库状态，保留已有修改，不提交、不推送。先核对 O0 涉及的方向已获确认；未确认时
只补充可审阅设计和测试方案，明确引用 AGENTS.md 的适用边界，不擅自改变布局/数学。
实现最小可关闭的 interior/boundary 分区，并在 halo 完成后串行执行两部分，暂不引入 overlap。
首轮只对普通步生效；rebuild/迁移、P=1/M1 fallback 等按设计回到原同步路径。
沿用 O0 的闭包证明，保持 global ID、local_count stride、原邻居行顺序与每中心累加顺序；
处理辅助 descriptor/partial 中间产物、workspace 清零、集合重叠和力/能量/virial 的唯一写入。
给出分类缓存的 epoch/位移有效性规则；旧 mask 不能跨不满足证明的状态继续使用。
保留原始未分区 M2a 为 oracle；新增诊断检查集合覆盖/交集、闭包可用性及未到达 halo 读取。
扩展现有测试承载 O0 反例，覆盖 P=2/4、两后端、空 rank、空 interior、周期边界、多段 run、
skin rebuild/迁移及项目已支持的 NEP 变体。核对 per-atom energy/force/virial，不只看总能量。
每 shell source ../env/md-mpi.sh，按环境门槛、单 rank golden、MPI domain 顺序验收；
修改 gpumd_compat 时同时履行 baseline/long_nve 验证，不放宽容差。
新增生效接口/布局合同同步更新 standards，实测结果写 status 并链接索引；记录分区额外成本。
验收：原始串行与分区串行正确性通过，分类失效/回退被实际触发。缺 GPU 验证标 PARTIAL。
最后交接接口、证据、精确命令和下一阶段入口；不要自动进入 O3。
```

### O3：异步通信生命周期，保持同步调度

```text
请执行 docs/plans/communication-computation-overlap.md 的 O3。读 AGENTS.md、O0-O2 设计与验收，
检查两个仓库状态，保留用户修改，不提交、不推送；O2 正确性未通过时不推进。
把 halo 操作拆成 begin/progress/finish（名称可依项目风格），先在原位置 begin 后立即 finish，
保持同步执行结果。所有 MPI 调用仍在初始化线程，遵守 MPI_THREAD_FUNNELED，不新增进度线程。
显式定义状态与事件：pack 就绪、发送源可交 MPI、收发完成、接收数据可供 GPU、unpack 完成、可复用。
HostStaged 处理 pinned buffer 和 D2H/H2D 事件；CudaAware 遵循目标 MPI/UCX completion 契约与自检，
不能直接假定 stream-aware，不得先删全局同步再补证明。每项同步替换都要标明依赖依据。
请求在途时禁止 buffer reserve/resize/free/覆盖；说明单在途槽位是否足够，不机械引入双缓冲。
接收就绪和发送 buffer 可复用是不同条件；处理零计数、P=2 同 peer 的方向 tag、重复 finish、
多段 run、退出和失败路径，错误时避免其他 rank 永久等待；禁止在途请求静默丢弃。
复用 O2 数值矩阵并新增生命周期/错误路径测试；每 shell source ../env/md-mpi.sh，按 GPU 门槛验证。
记录两后端的字节数/调用契约变化、额外内存与串行性能成本；更新实际变更的 standards 和 status。
验收：立即 finish 模式与 O2 数值一致，请求/缓冲区完成和复用有证据，无默认策略改变。
最后交接状态机、测试、命令和未解决项；不要启动 O4 或 MPI progress thread。
```

### O4：普通步真实 overlap 与完整正确性验证

```text
请执行 docs/plans/communication-computation-overlap.md 的 O4。读 AGENTS.md、O0-O3 与 CPU 基线，
检查两个仓库状态，保留已有修改，不提交、不推送。前置验收不足先解决，不合并 M2b。
在单一、默认关闭的实验开关下接入普通步 begin halo → 独立 interior GPU 计算 → 通信完成/
unpack → boundary → 汇合 → VV2/thermo，具体 event/stream 顺序必须满足已证明的读写依赖。
保证 interior 在本步真正读取最新 owned 数据；pack/unpack 与计算的 workspace 不竞态。
主线程比较有限种 progress 策略，如 kernel 发起后的 MPI 等待与周期 Test；记录调用频率/CPU 成本。
不升级 MPI 线程级别，不加入自建 progress thread，不假定 device-pointer 支持即后台进展。
rebuild/迁移、空 interior、P=1/M1 等回退有机器可读原因；开关关闭走已验证原路径。
核对 benchmark 显式传递并记录开关，日志证明启用模式。保留原始串行、分区串行、立即 finish、
真实 overlap 四种可复跑比较方式，定位分区、状态机、调度各自成本。
扩展并执行 P=2/4 两后端、空 rank、P=2 同 peer、两跳链、周期边界、NEP 变体、skin rebuild、
迁移、多段 run、输出/restart 和 P1/M1 回归；比较逐原子能量/力/virial，再按现有 manifest 做 NVE/NVT。
每 shell source ../env/md-mpi.sh；先环境预检/单 rank golden，再 MPI。不得放宽容差或只测总能量。
采集至少一组可解释 trace：按本计划区分请求在途交叠和实际传输进度，不以 Isend 调用作为成功证据。
按当前实际变化同步更新 standards，status 记录全部精确命令与 PASS/FAIL/PARTIAL。
验收：正确性无回归，启用/回退均被覆盖，存在可复核交叠证据；无收益也如实报告，不默认启用。
最后交接 O5 所需二进制/dirty diff/哈希、配置、trace 和未完成验证。
```

### O5：收益归因、强扩展与采用决策

```text
请执行 docs/plans/communication-computation-overlap.md 的 O5。读 AGENTS.md、O0-O4 和 CPU C2/C3 产物，
检查两个仓库状态，保留已有修改，不提交、不推送。O4 正确性门槛未过时不发表性能结论。
复用 benchmark 与 CPU 采样工具，先 pilot，再在同输入/初态/二进制/绑定/步数下随机化 A/B 顺序。
矩阵至少 carbon_200k/carbon_1m/water_400k、1/2/4/8 GPU、两后端；P=1 标为单卡基线，
M1 fallback 不混入 M2a 收益。资源不足保留缺口，单节点结果不能写成跨节点结论。
warmup/steps 使用 manifest 和显式覆盖，正式重复至少 5 次，保存解析配置和预先确定的统计方法。
主结果使用无详细 profiler 的墙钟；分别给 median/离散度、置信区间、加速比和并行效率，
说明独立重复还是配对设计。5 次重复不保证显著性，证据不足增加重复或写结论不确定。
做四组归因：原始串行、分区串行、异步接口立即 finish、真实 overlap；同时报告 pack/staging/
unpack、NEP 分区额外开销、通信字节、interior 比例、等待、CPU_seconds/core-equivalent 与轮询次数。
按计划定义计算 trace 交叠比例，不将请求在途当网络带宽证据，不把不同 rank 时间简单相加。
报告无收益/退化场景，特别是空或很小 interior、强扩展薄 slab、重建占比高的水样例。
写 docs/status/communication-computation-overlap-results-YYYYMMDD.md 与可复跑分析图表，链接索引。
给出采用建议：保留实验开关、需补实验、或有依据的默认策略提案；自动回退阈值需独立验证，
不能在同一测试集调参后直接宣布普适。此阶段不自动默认启用、不扩展 M2b、不宣称新颖性已证明。
验收：收益/无收益都有原始数据，CPU 代价可见，结论与实际矩阵一致。最后给维护者可审阅决策材料。
```
