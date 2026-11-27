<a id="mpi-runtimem1-与-m2a"></a>
<a id="mpi-runtime-m1-与-m2a"></a>
# MPI runtime：全量复制与局部域分解

类别：现行标准。核对日期：2026-11-20。

## 作业如何选模式

DMG-MD 每个 MPI 进程（rank）使用一张 GPU。全量复制模式下，每张 GPU 保存整盒原子、计算整盒 NEP 力。局部域分解模式下，程序沿一个轴把盒分成条带。每张 GPU 计算本地负责原子的最终力，只交换求力所需的边界数据。

| 名称 | 代码与日志 |
| --- | --- |
| 全量复制模式 | `replicated-full`。普通分派记录 `m1-fallback`，默认 NPT 路由记录 `m1`。 |
| 局部域分解模式 | `local-domain`。分派日志记录 `m2a`。 |
| 分阶段中间场交换方案 | `staged-field exchange`，尚未实现。 |
| 单 rank 基线 | `single-rank baseline`，单 rank 的运行对照。 |

程序在启动时为整份作业选择模式。`run_replicated` 每 rank 只加载一次 NEP 参数，选好模式后再分配工作区（deferred workspace）。

单 rank 始终使用全量复制模式。多 rank 拒绝三斜盒和带非周期方向的盒。正交全周期盒的各方向厚度须大于 `2.5*(rc_radial_max+1)`，并满足 `slab_width≥d_coord` 和半径证明，才具备局部域分解资格。多卡作业需要局部域分解但初始局部资格不足时，各 rank 在写科学输出前集体退出。内部对照可在所有 rank 一致设置 `DMGMD_FORCE_M1_REPLICATED=1`，允许失格作业使用全量复制。

任一 NPT 声明默认使整份作业使用全量复制。显式 `DMGMD_DOMAIN_NPT=rebuild|affine`
可以按[实验合同](domain-npt.md)进入局部域分解。普通分派或实验 NPT 的初始资格判定输出
下列日志。若实际进入局部 NPT，还会输出 `DMGMD_DOMAIN_NPT mode=rebuild|affine`。

```text
DMGMD_DOMAIN mode=m2a|m1-fallback axis=x|y|z|none d_dep=... d_coord=... reason=...
```

失格诊断之前也会输出 `mode=m1-fallback`。这行只记录资格判定，需结合退出码确认作业是否运行。显式局部 NPT 的单卡恢复和积分中停止已在双卡、四卡验证，普通多卡 NPT 的生产默认策略由工单 13 复验。[生产路由决策](architecture-decisions.md#production-domain-routing) · [实测记录](../status/validation/domain-npt-validation.md#local-box-restart-20261127)

局部域分解可以在交换边界坐标时，先提交只依赖本地原子的求力工作。分区（O2）识别这部分工作，通信接口（O3）提供发起和完成两个调用，重叠调度（O4）在两者之间提交计算。分区和重叠默认关闭。[数据布局](data-layout.md) · [NEP 内核依赖](../reference/kernel-inventory.md) · [分区与重叠](domain-partition.md)

<a id="m1-空间所有权"></a>
## 全量复制模式的空间所有权

读取器给每个 rank 建立一份完整原子数组，计数为 `global=owned=local=N` 和 `ghost=0`。运行时另给每个原子分配唯一的负责 rank，由它积分并提供热力学量和输出数据。`SpatialOwnership` 以 `owner_by_slot` 保存归属，派生 `owned_indices`、掩码和从原子全局 ID（gid）到槽位的映射。gid 是 `[0,N)` 的排列，`owned_indices` 按 gid 排序。允许空条带和 `N<P`。

单 rank 的原子都归 rank 0，跳过选轴和归属重算。多 rank 沿盒的最长边切成等宽条带，用分数坐标划分半开区间。x 与 y 或 y 与 z 等长时选 y，x 与 z 等长时选 x。内部边界属于右侧，`s=0` 属于第一条带，`s=1` 属于最后一条带。坐标越界时只折回一次（`<0/+1` 或 `>1/-1`）。

全量复制模式下的迁移只改变 gid 对应的负责 rank，各 rank 的数组槽位及顺序保持原样。旧 rank 完成速度 Verlet 的第一半步（VV1），新 rank 完成第二半步（VV2）。

1. 触发修速时，按当前归属索引通过 `Allgatherv` 汇集速度，在根 rank 的 CPU 上修正后用 `Bcast` 广播。
2. 自适应时间步只检查负责原子。当前负责 rank 执行 VV1，更新位置和已启用的展开坐标。
3. 按当前归属索引用 `Allgatherv` 汇集位置，恢复每张 GPU 的完整坐标。
4. 对全部 N 个原子折回坐标、清零并执行普通 NEP 求力。
5. 根据折回盒内的坐标计算下一归属，并用固定 16 B 的归属表哈希做 `Allreduce`。本地构造失败也参加同一次握手。归属改变时，按旧归属用 `Allgather` 汇集最新半步速度和可选的展开坐标，再提交新归属、递增 epoch 并重建索引计划。归属未变时继续用原计划。
6. 新负责 rank 执行 VV2、热力学量计算、控温和输出。下一步沿用此归属。

速度只在修速和迁移时复制给非负责 rank。类型、质量、组和 gid 保留静态副本。普通 NEP 读取邻居 `Fp` 和反向部分力，完整工作区必须保留这些依赖，单改 `N1/N2` 只计算负责中心会遗漏所需中间量。

启动时先检查完整归属表哈希，再用 `Allreduce` 对 N 个负责标记求和，每项必须等于 1。检查哈希的步骤位于变长通信前，兼顾归属表一致性与原子唯一覆盖。程序记录 `CENTER_PARTITION` 和 `OWNERSHIP`，归属变化时还记录 `OWNERSHIP_EPOCH`，最多记录 64 次转换。上述检查证明物理输出覆盖。NEP 计算是否分片要另看求力路径。

<a id="m1-isotropic-box-change-contract-b1"></a>
### 全量复制模式的各向同性盒变更

提交前检查候选盒的正交全周期性、double 逆矩阵、体积和 float 副本。GPU 以
`local_count` 为 SoA stride，仅预检 `global_count` 个位置，并返回一个异常标志。
预检通过后才缩放位置。padding、速度及 `unwrapped` 保持原值。

提交会刷新 `RuntimeOwnership` 的盒逆与分区轴，并标记全局 NEP 邻居缓存失效。P1 保留
`partition_axis=-1`。P>1 的各向同性缩放要求 owner map/hash 不变。下次全局 Verlet
重建后清除失效标记。`get_num_bins` 重新计算 `thickness_*`。

更新位于 VV2、thermo 和 thermostat 之后，所有输出之前。同一步 thermo stress 使用缩盒前样本。
box 列与快照的 wrapped position 使用缩盒后状态。
证据见 [B1 盒合同验收](../status/validation/npt-validation.md#box-contract-20261103)。

### NPT 路由与控制状态（B2–B4/G-B）

默认 `DMGMD_DOMAIN_NPT=off` 时，`npt_ber` 和已通过 G-B 的 `npt_scr` 使用全量复制模式的盒变更接口。多 rank 即使满足局部域分解的几何条件，也会走全量复制，并输出 `DMGMD_DOMAIN mode=m1 reason=npt-m1-only`。如果把 NPT 交给关闭实验开关的局部入口，入口会在积分或更新边界前拒绝。显式开启局部实验模式后，只对负责原子缩放坐标，先预检再提交。[局部 NPT](domain-npt.md)

全量复制模式的 SCR 统计验收取单节点 216 原子 NEP-C，在 300 K、0 GPa 下运行。基准步长包括 P1/HostStaged 以及 P2、P4 的两个后端，半步长只包括 P1/HostStaged。最初 18 次分析的总体判定为 `INCONCLUSIVE`，后续独立确认通过上述范围。[G-B 独立确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)

rank 0 计算盒缩放因子并广播状态和因子。全量复制模式的每个 rank 对完整位置数组应用
同一因子。SCR 按 B3/B4 验收的顺序，由 root 先取恒温随机数，再取压力随机数。
压力与更新的采样时点见[控压器标准](barostat.md)。局部 NPT 的验收单独记录在[实验合同](domain-npt.md)。

## 通信后端与部署

目前只支持 Open MPI 和 UCX 组成的运行栈。原生运行时加载 `../env/md-mpi.sh`，Docker 自动加载 `/opt/dmgmd/env/md-mpi.sh`。环境固定 UCX PML 和 MCA 路径，排除 HCOLL。内置 PMIx 可使用编译时目录。

默认后端 `HostStaged` 先在设备上打包，再经固定页主机缓冲区完成 D2H、MPI 和 H2D，最后在设备上解包。`CudaAware` 直接向 MPI 传设备缓冲区。启用前执行 MPIX 查询，并对 `Allreduce(IN_PLACE)`、`Allgatherv`、`Gatherv`、`Bcast` 及点对点收发做数值自检。自检数值不符时回退到 `HostStaged`。MPI 出错时按失败规则终止作业。

每 rank 根据 local rank 选择一张 GPU，运行时用 UUID 检查重复绑定。MPI 路径不构造 `NEP_MULTIGPU`。

<a id="m1-每步通信量"></a>
## 全量复制模式的每步通信量

集合通信按传给 MPI 的逻辑输入和输出缓冲区计字节，实际链路流量另算。发送时按负责槽位打包成逐原子记录（AoS），接收后按 gid 和槽位的映射恢复为逐分量数组（SoA）。接收端必须使用映射还原槽位。程序建立映射时就核对身份，因此普通步无需重复发送 gid。

下表取 P 为 rank 数、N 为 `global_count`。自适应时间步、输出和迁移的额外通信在下一表单列。

| collective | MPI input | MPI output |
| --- | ---: | ---: |
| position Allgatherv | 24N | 24NP |
| map hash Allreduce（仅 P>1） | 16P | 16P |
| thermo Allreduce | 64P | 64P |
| P>1 合计 | 24N+80P | 24NP+80P |

P1 无 map hash，合计 24N+64P / 24NP+64P。HostStaged device collective 的 D2H/H2D
等于相应 input/output，host-only hash 不计 staging；不能把合计 80P 全当 D2H/H2D。

| 额外触发 | 增量 |
| --- | --- |
| adaptive | host max-Allreduce，MPI input/output 各8P，无 device staging |
| owner 变化 | velocity Allgatherv：24N/24NP；启用 unwrapped 再加同量 |
| correct_velocity | velocity Allgatherv + Bcast，各24N/24NP；HostStaged 各有对应 staging |
| snapshot | position3+velocity3+force3+PE1+virial9，MPI input/output 各152N；unwrapped 另24N |

snapshot 的 HostStaged 增同量 D2H；CudaAware root 仍需 output download。启动 hash、覆盖和
输入一致性属于一次性 control plane，不计 step 行。

### NHC / BDP 的控制通信

新 NVT 的逐步增量相对于上表每步反馈 thermo 的 `nvt_ber`，全量复制模式与局部域分解相同。
链或 RNG 只在 root 存在，成功时广播两 double `[status=1, factor]`：

| 系综 | 额外 collective calls / step | MPI input bytes | MPI output bytes |
| --- | ---: | ---: | ---: |
| `nvt_nhc` | 3：一次 1-double KE Allreduce、两次状态/因子 Bcast | `8P+32` | `40P` |
| `nvt_bdp` | 1：一次状态/因子 Bcast | `16` | `16P` |

NHC 在 VV1 前用当前 owned 速度求和 `Σmv²`；全量复制用 owned 索引，局部域分解用 owned prefix。
它复用原 thermo kernel 的 quantity-0 block，只有一个 double 进入归约。
HostStaged 为此额外记 D2H/H2D 各 `8P`，CudaAware device collective staging 增量为零。
两路径随后都将归约标量读到 host（每 rank 8 B）；这与已有 thermo 控制量下载一样不属于
MPI staging / snapshot output-download 字段，不能把账本当全部 CUDA memcpy 清单。
host 状态/因子 Bcast 不计 device staging。NHC 两半步、BDP 一次末步缩放均只写 owned 速度。
NVE/Berendsen 不进入此协议，不增加链/RNG 分配或通信。

root 更新失败时先将 `status=0` 广播，再广播错误字符串，所有 rank 随后抛出并走既有 abort
出口；失败路径的错误文字通信不属于成功步预算。逐 rank 因子位模式和上述成功步增量的实测见
[恒温器矩阵](../status/validation/thermostat-validation.md#thermostat-matrix-20261102)。

含 BDP 或 SCR 的整个输入在执行前校验 `DMGMD_ENSEMBLE_SEED` 的设置状态和原始字符串。root
用长度+内容两次 Bcast 分发，再以一次 Allreduce 比较各 rank；之后解析 uint32 十进制。
局部域分解模式的独立入口再次执行同一预检，避免绕过 dispatcher 时漏检；这些启动控制通信不计逐步行。
没有逐步 RNG 状态传输。选项限制、分段重置和非连续 restart 见[系综合同](ensembles.md)。

## 日志与整体计时

`DMGMD_COMM_LOG_INTERVAL` 默认为 1000，各 rank 须一致。每行只统计被采样的一个步。需要检查每一步时设为 `1`。局部域分解还输出逐 rank 字段。

每个运行段输出 `DMGMD_TIMING phase=run`。CUDA 同步和 MPI barrier 完成后开始计时，段末同步完成时停止。
含段首 force、步循环、通信/输出和启用的诊断收尾。seconds_min/mean/max 是同作业 rank 范围，
吞吐=global_count*steps/seconds_max，不把 duplicated NEP 计为额外物理工作。
`phase=total` 从选定的运行入口计至 `finish` 返回，包括部分初始化和清理。这个区间从分派加载势之后开始，范围也排除了 `main` 解析、MPI 初始化和完整的析构及 finalize。

正式端到端比较使用关闭详细分析后的 `run` 墙钟。正确性测试没有速度门槛。当前只有单机记录，跨节点效率尚待实测。[验证状态](../status/current.md)

## I/O 与 NEP_MULTIGPU

thermo/XYZ/restart 仅 root formatter 写。全量复制ordinary NEP 的非 root neighbor.out 写本机
私有 scratch：temp_directory_path 下 mkdtemp 原子创建 dmgmd-rank-io-<nonce>-r<rank>-XXXXXX，
0700、普通 neighbor.out、无 symlink、不广播路径，跨节点 /tmp 无需共享。

setup 各 rank 捕获 TMPDIR/mkdir/file/chdir 错误后 Allreduce 状态，失败者信息 Gather/Bcast，
已 chdir 的先恢复 cwd，再所有 rank 走同一失败出口。恢复失败不删除仍可能作为 cwd 的目录。
finish 先归约恢复状态（失败判作业失败），再校验父 temp 根、完整前缀/随机后缀和非 symlink，
各 rank 只删自己目录，最后汇总 cleanup。cleanup 失败仅 root 警告并保留精确目录；
析构只本地 best-effort，不在栈展开中执行 MPI。

测试钩子 DMGMD_RANK_IO_FAULT=<rank>:<op> 支持 mkdir/file/chdir/setup_restore/restore/cleanup，
不属用户产品参数。测试见[Golden Test](golden-test-standard.md)，[双节点验收](../plans/multi-node-io.md)暂缓。

异常 rank 写并 flush DMGMD_ERROR（rank/local rank/host/category/message）后 abort；异常诊断
不新加 collective，避免 peer 已阻塞时挂起。I/O setup 的协调在抛出前完成，不能推广为所有
runtime 错误都先归约；Open MPI 转发 stderr，记录顺序不等于执行顺序。

## 局部域分解路径（现行合同）

### 布局与身份

局部域分解模式下，每张 GPU 保存本 rank 的负责原子（owned atom）和其他 rank 的边界副本（ghost atom）。依赖副本（dependency ghost）需要计算中间量，仅坐标副本（coordinate-only ghost）只提供邻居候选。主机仍保留完整的原子身份，用于静态元数据和按输入顺序输出。[数据布局](data-layout.md)

数组前段 `[0, owned)` 保存负责原子，按 `global_id` 升序排列。后面依次放依赖副本和仅坐标副本。两段副本各按 `(source face, source rank, global_id)` 排序。`local_count` 是所有 SoA 数组和 NEP 工作区的唯一跨度（stride），`global_count` 只作元数据。

以最小镜像约定（MIC）算原子到本 rank 条带的距离。距条带不超过 `d_dep` 的副本归入依赖层，程序为这些中心构建 Verlet 行和按类型对截断的邻居表，并计算描述符、`Fp` 和部分力。距离在 `(d_dep, d_coord]` 内的副本只提供坐标。

每个槽位保存 `global_id`、管理 rank、来源 rank、边界面和周期镜像偏移。发送端按 MIC 带去重，接收端拒绝重复 gid。每个 gid 只有一个折回盒内的槽位，周期镜像偏移只作元数据，坐标保持原值。多镜像副本尚未支持。

管理归属（manager owner，`manager_owner`）是当前负责积分的 rank，在一个缓存周期内保持不变。按当前位置计算的几何归属（geometric owner，`geometric_owner`）可以先改变，迁移在统一重建时才提交。普通步沿已有条带边界通信计划刷新副本坐标（24 B/原子）。统一重建时重新选取边界成员，每条成员记录为 40 B，每个面另交换 4 B 的计数。

两跳位置边界须覆盖固定版本内核访问的所有邻居。径向表要求 `d < (rc_radial[t1]+rc_radial[t2])/2`。角向表从径向表再筛选，要求 `d < min(Rr, Ra)`。ZBL 使用角向表，因此 `R_force = R_dep = Rr`。据此得到：

- `d_dep = max R_force + skin`
- `d_coord = max chain (R_force + R_dep) + 2*skin`

用内核的 float 表达式 `(a+b)*0.5f` 算类型对平均，同时求输入 float 操作数的精确平均，取两者较大值。内核结果向上舍入时再向 `+inf` 扩一个 double ULP，避免主机计算低估 cutoff。遇到非有限或非正 cutoff 等无法证明上界的情况，拒绝局部域分解资格。[缓存有效性证明](cache-validity.md)

<a id="每步顺序m2a"></a>
### 局部域分解路径的每步顺序

1. （correct_velocity 触发步）gather `(global_id, owned position, owned
   velocity)` 到 root，root 按 global ID 恢复全局顺序并复用现有 CPU 修正，再按
   current owner scatter 回 owned local 槽位（禁止把 3N 数据写入 local_count
   stride 的 device 数组）；
2. 自适应时间步（owned max |v| host max-Allreduce）；
3. owned VV first half（+ unwrapped 跟踪）；
4. owned 位置按 pinned 单次 `<0/+1`、`>1/-1` 语义 wrap；若单步位移导致坐标一次 wrap 后
   仍在盒外，则统一 decision 在任何 halo/force 前报 unsupported multi-box displacement；
5. VV1 在折回坐标前累计负责原子的真实位移。每个 rank 只检查自己负责的全局 ID，将局部最大位移平方是否严格超限合入一次 uint32 原因按位或归约（reason-mask OR）。空 rank 也参加。缓存判定只用这次 OR。非有限位移、连续性无法证明或单步超过一个盒长时，全体 rank 在交换边界或求力前报错。固定盒的全局位移 `> skin/2` 时重建，恰好等于阈值时仍复用。局部 NPT 另有变盒判据。邻居表按 `confirmed_reuse` 或 `must_rebuild` 执行。[局部 NPT](domain-npt.md)
6. 普通步验证通信映射和刷新对应的缓存版本，只沿已有边界计划刷新副本坐标（24 B/原子）。管理归属、槽位、成员、通信映射和邻居表保持不变。小幅跨条带或跨周期边界只改变几何归属。
7. 统一重建时，若当前负责 rank 与几何归属不同，先用 `Alltoall` 交换待迁移原子数，再用 `Alltoallv` 把记录直接送到最终条带的 rank。记录包括 gid、类型、质量、电荷、组标签、折回盒内的位置、半步速度及已启用的展开坐标（unwrapped）。力、势能（PE）和 virial 在此处不传。随后统一重建边界成员、布局和映射，复用或扩展容量，清零累计位移并重建邻居表。即使负责 rank 未变化，超过 skin 位移界时也走同一套重建过程。
8. 域分片 NEP：dependency-center 邻居表/descriptor/partial
   （`[0, owned+dep)`），owned radial force/many-body/ZBL（`[0, owned)`），
   候选 `[0, local_count)`；
9. owned VV second half；
10. 按[消费者需求](thermo-demand.md)执行 owned thermo 求和 + 8-double Allreduce
    （NVE 只在 thermo/XYZ 输出步需要，NVT 和实验 NPT 每步均需。ghost 的 PE/virial scratch 不参与）；
11. owned thermostat（NHC 后半步或 BDP/Berendsen 末步缩放；NHC 前半步在第 3 项 VV1 前
    另做 owned KE 归约、root 链更新与因子广播）；
12. 实验局部 NPT 在输出前还执行控压。若盒因子变化，先验证候选盒并在所有 rank 上达成一致，
    才提交盒和 owned 位置。下次求力前按[局部 NPT 合同](domain-npt.md)刷新或重建。
13. 输出/measurement：local-owned-prefix gather 携带 global ID，root 恢复恰好
    N 条按 global ID 排序的记录后复用既有 formatter（ghost 永不直接输出）。

第一个 run 前的初始力先完成 local owned 初始化、bootstrap 设备 wrap（与
pinned wrap kernel 逐位一致）、halo 与 NEP workspace。多段 run 延续同一 domain state，
无须在段间恢复 replicated device arrays。若有未处理的盒变化或布局重新上传，段首先完成
求力准备。仍有效的 membership/neighbor cache 可以复用。restart 新进程按新的 rank 数
从输入行重建 cache 和 owner epoch。

### 通信后端与字节口径

普通步发起点对点交换后默认立即等待结果。显式开启重叠时，可在发起和等待之间提交只依赖负责原子的计算。通信接口在 GPU 完成解包、写入边界副本后才释放槽位。两种后端沿用同一载荷与请求数量。[边界交换接口与线程限制](halo-lifecycle.md)

`HostStaged` 与 `CudaAware` 都用独立 tag 和缓冲区区分左右两面。P=2 时两面可发给同一个 rank，P=1 时跳过点对点交换。允许 count 为零。`CudaAware` 启用前执行真实设备缓冲区点对点 Send/Recv 自检，数值失败回退到 `HostStaged`。`tests/mpi/check_environment.py` 断言成功记录 `cuda_aware_p2p_self_test=passed`。

`DMGMD_COMM` 的既有六个字段保持 global-aggregate 语义（全量复制与 局部域分解一致）；新增
字段口径如下，均记录**发送 rank 自己的 local 值**，每 rank 另有
`DMGMD_DOMAIN_COMM rank=... step=...` 行供逐 rank 精确断言：

| 字段 | 口径 | 内容 |
| --- | --- | --- |
| `p2p_calls` | local | 已完成的 Isend/Irecv 请求个数（每轮左右两面合计 4 个） |
| `halo_send/recv_bytes_local` | local | 位置刷新（24 B/原子/步）+ membership 记录（40 B/原子，仅重建步） |
| `migration_send/recv_bytes_local` | local | Alltoallv 迁移记录（无 group 时 80 B/原子，unwrapped 启用时 104 B） |
| `control_send/recv_bytes_local` | local | face count handshake（8 B/轮）、Alltoall/Allgather of counts |

测试逐步通信时显式设 `DMGMD_COMM_LOG_INTERVAL=1`。设置 `DMGMD_DOMAIN_DIAGNOSTICS=1` 后，才逐次记录 `DMGMD_DOMAIN_LAYOUT` 和 `DMGMD_DOMAIN_MIGRATION`，沿用现有字段与 rank 含义。

每个运行段结束时，每 rank 都输出一条 `DMGMD_DOMAIN_SUMMARY`。它记录本 rank 的迁移与重建步数、布局上传、工作区逻辑大小更新、扩容、GPU 分配，以及实际计算或跳过热力学量的次数。这些计数不增加逐步集合通信。详细布局记录中的 `gpu_allocations` 指这次更新 Atom、NEP 和 Neighbor 布局时实际发生的 `GPU_Vector` 分配次数。

普通 NVE/NVT 局部域分解步省去按 N 扩张的 position Allgatherv。
实验 NPT 的附加控制通信见[局部 NPT 合同](domain-npt.md)。
输出步增加 gid gather（8N）及各字段 gather（8 B/atom/component，root 侧恢复
input-slot 顺序）；correct_velocity 触发步增加 80N 的 gather/scatter。除下述周期记录外，
每步固定执行 decision reason OR（4P）；有 thermo 消费者时增加 64P 与一次 SUM，所以
input/output 各为 `4P + 64P * needs_thermo` B、`1 + needs_thermo` 次 collective；
NVT 的 `needs_thermo` 恒为 1。rebuild 若发生 owner 变化再增加 Alltoall count
和 migration payload，所有 rebuild 增加 face-count/membership p2p。统一 decision 取代旧的
migration-OR + neighbor-rebuild-OR 双判定，不得由 Neighbor 再发起位移 collective。
O7 将原缓存 displacement MAX 的阈值判定移到各 rank 后合入 OR；自适应时间步的
MAX 及下述 neighbor 周期 MAX 保留。证明、独立实际调用计数与配对实测见
[缓存判定](cache-validity.md)。
`neighbor.out` 每 1000 次 force 调用（含首次）由各 rank dependency-center local
max 经 `MPI_MAX` 聚合后仅 rank 0 写单条既有格式记录。统计必须在**本次** Verlet/typewise
邻居表生成后读取；真正 `local_count=0` 的 rank 贡献零但仍参加 collective。初始力或段首力
的聚合属一次性 control plane，不计入 step 行；若记录落在 step force，则该 step 精确增加
2 次 collective、`mpi_input_bytes_global += 16P`、`mpi_output_bytes_global += 16P`。

设置 `DMGMD_DOMAIN_TIMING=1` 后，每个积分步恰好归类为 `ordinary` 或 `rebuild`。程序用 CUDA 事件和主机单调时钟记录决策、迁移、成员与布局、分配与上传、边界打包与等待及解包、网格邻居、NEP、积分、热力学量、科学输出和剩余阶段。

默认关闭时，程序只保留 O3 生命周期所需的事件，跳过详细计时事件、逐步记录和专用同步。开启时为每步保留事件，在已有的段末设备同步后读取，因此 NVE 跳过热力学量的 D2H 时也能计时。诊断资源为 O(段步数)。每个段末各做一次 SUM、MIN 与 MAX 归约，输出有界汇总和直方图。

设备区间、主机关键路径区间和 MPI 调用墙钟可以重叠。报告应分别列出。端到端性能以关闭详细计时后的 `DMGMD_TIMING phase=run seconds_max` 为准。

`DMGMD_DOMAIN_TRACE=1` 仅在同时设置 `DMGMD_DOMAIN_TIMING=1` 的 局部域分解路径有效，默认关闭。
`DMGMD_DOMAIN_DIAGNOSTICS` 启动记录中的 `timing`/`trace` 是程序实际模式。每 rank/步
输出 `DMGMD_DOMAIN_TRACE`：`CLOCK_MONOTONIC` 的整数纳秒记录 step、decision、rebuild、
ordinary halo、NEP host enqueue 和可选 thermo 的 host 结束边界；跳过 thermo 时该边界不证明 GPU readiness。
ordinary halo 还记录 pack/unpack
enqueue、HostStaged D2H（含完成等待）/H2D enqueue、CudaAware 输入/输出同步、MPI post 与 `MPI_Waitall` 调用边界。
rebuild 的同类内部阶段目前只在整体 rebuild host 区间内，未单独拆出。缺席阶段用零时间戳，
不得解释为耗时零。每行带 rank/PID、sequence/global step、当前步各类通信字节和整段 NEP
CUDA event 耗时；`DMGMD_DOMAIN_TRACE_SEGMENT` 给出可与同一 clock namespace 的 C1 样本关联
的 run loop 边界。trace 的逐行输出本身有开销，不能用作无诊断性能基线。

`halo_transfer_wait` 从 pack enqueue 后至 unpack enqueue 前计 host wall，含 pinned staging、
CudaAware 的既有 `cudaDeviceSynchronize` 和 MPI；`mpi_wait` 是所有被计量 MPI 调用的 host
耗时总和，不是纯网络传输时间。trace 的 `mpi_waitall_*` 只括住 `MPI_Waitall` 调用；
post 至完成是请求在途窗口，不证明网卡正在传输。pack/unpack/NEP 的 CUDA event 给出同一
stream 上的设备耗时，不携带 `CLOCK_MONOTONIC` 绝对时间。host enqueue 时间与既有 thermo
D2H/段末同步提供先后及就绪边界，无法从这些字段给设备事件构造精确主机时间戳；跨 CPU/GPU
重叠只能在更强的时钟校准或外部 profiler 证据下声称。诊断没有新增逐步 barrier 或同步；
段末只执行原有一次 device synchronization，随后读取保存的各步 event 并输出 trace。

## 验证入口与后续

完整 runner 覆盖和比较原则只在[Golden Test](golden-test-standard.md)维护，CLI 见
[MPI README](../../tests/mpi/README.md)；已执行结果见[当前状态](../status/current.md)。
[O2 分区](domain-partition.md)保持 L stride、半径和每中心顺序；先完成 halo 后串行执行。
分阶段中间场交换方案、3D 与多节点硬化见[扩展计划](../plans/domain-decomposition.md)，O10 等剩余工作见[计划](../plans/communication-computation-overlap.md)。

## O4 默认关闭的普通步重叠

`DMGMD_DOMAIN_OVERLAP=off|wait|poll` 默认为 off；启用要求
`DMGMD_DOMAIN_PARTITION=serial|verify`。模式与 `DMGMD_OVERLAP_TRACE=0|1` 全 rank 一致，
非法配置整作业拒绝。启动时为 overlap/trace 一致性各执行 MIN/MAX fingerprint，
共新增四次标量 Allreduce；不进入逐步通信量账本，force/halo 不增加 collective。只在已证明模型、局部域分解普通 confirmed-reuse 且 I 非空时，将 E/I
计算放在 begin 与 finish 之间。其他路径同步 full，诊断记录 reason。
同一默认 stream、FUNNELED、原 halo/tag/字节及独立收发完成合同不变；所有 rank 在
neighbor sink、thermo/output collective 前 finish，混合 full/overlap 顺序一致。
接口/缓存/诊断细则见[分区](domain-partition.md)与[halo](halo-lifecycle.md)，实测范围见
[O4 验收](../status/validation/communication-computation-overlap.md#overlap-o4-20261016)。不含分阶段中间场交换方案、progress thread 或 O5。
