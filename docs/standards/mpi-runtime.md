<a id="mpi-runtimem1-与-m2a"></a>
<a id="mpi-runtime-m1-与-m2a"></a>
# MPI runtime：全量复制与局部域分解

类别：现行标准。核对日期：2026-11-05。

模式名称：**全量复制模式**（英语 `replicated-full`；普通路径日志为 `m1-fallback`，NPT 路由日志为 `m1`）、**局部域分解模式**（英语 `local-domain`；路径日志为 `m2a`）、**分阶段中间场交换方案**（英语 `staged-field exchange`，尚未实现）以及**单 rank 基线**（英语 `single-rank baseline`）。日志和测试数据中的机器字段为合同内容，正文使用上述可读名称。

`run_replicated` 每 rank 只加载一次 NEP 参数，deferred workspace 在选定路径后分配。
NPT 始终使用全量复制模式。对于 NVE/NVT，P1 使用全量复制模式；P>1 满足正交全周期、各方向厚度>2.5*(rc_radial_max+1)、slab_width≥d_coord 且半径可证明时采用局部域分解模式，否则回退到全量复制模式。
P>1 triclinic/非周期直接 unsupported。非 NPT 路由时，root 输出：

```text
DMGMD_DOMAIN mode=m2a|m1-fallback axis=x|y|z|none d_dep=... d_coord=... reason=...
```

布局由[data-layout](data-layout.md)定义，NEP 依赖证据在[kernel 清单](../reference/kernel-inventory.md)。
O2 分区与 O4 overlap 均默认 off；显式 O4 普通步在 O3 begin/finish 之间提交独立 early。

<a id="m1-空间所有权"></a>
## 全量复制模式的空间所有权

reader 的 global=owned=local=N、ghost=0，但 MPI 权威 owner 是 SpatialOwnership：
owner_by_slot 单一事实源，派生 owned_indices/mask 与 gid→slot，gid 为 [0,N) permutation。
owned_indices 按 gid 排序，空 slab/N<P 合法。P1 全归 rank0，不选轴/重算 owner。
P>1 沿最长边切等宽 fractional 半开 slab；tie 为 y 胜 x/y 或 y/z，x 胜 x/z。
内部面属右侧，s=0 首 slab，s=1 最后；wrap 保留单次 <0/+1、>1/-1。

全量复制迁移是 gid 逻辑移交，不压缩/重排数组。旧 owner 做 VV1，新 owner 做 VV2：

1. 修速触发时，先用 current ownership indexed velocity Allgatherv，root CPU 修正后 Bcast。
2. adaptive 只扫 owned；owned VV1/position/unwrapped 更新。
3. current ownership indexed position Allgatherv 恢复每张卡完整坐标。
4. 全 N wrap/清零/ordinary NEP scratch。
5. 从 wrapped position 重算 next owner，固定 16 B map-hash Allreduce 保证各 rank 一致；
   本地构造失败也走同一握手。变化时先按旧 owner Allgather 最新 half-step velocity 和
   可选 unwrapped，再 adopt next owner、epoch++ 并重建 indexed plan；不变则复用。
6. next owner 执行 VV2、thermo、控温和输出，下一步沿用该 owner。

普通步不复制 velocity；其非 owned 消费者只有上述修速/迁移。type/mass/group/gid 静态复制。
NEP 读取邻居 Fp/反向 partial，故 full scratch 不能简单改为 owned N1/N2。

启动先校验完整 map hash，再 Allreduce N 个 owned mask，要求每项=1。hash 在 variable-count
通信前执行；mask 不能替代全 map 一致性。记录 CENTER_PARTITION/OWNERSHIP；每次 owner
变化输出 OWNERSHIP_EPOCH（最多64条 transitions）。这证明物理输出覆盖，不证明 NEP 数学分片。

<a id="m1-isotropic-box-change-contract-b1"></a>
### 全量复制模式的各向同性盒变更

The host box-control primitive validates a candidate orthogonal, fully periodic
box, its double inverse, volume and float copy before changing live state. A GPU
preflight scans only `global_count` positions using `local_count` as the SoA
stride and copies back one invalid flag; only a clean preflight launches the
scale kernel. Padding, velocity and `unwrapped` remain untouched. The operation
then refreshes `RuntimeOwnership`'s inverse/partition axis and sends a one-shot
notification to the replicated-full/global NEP neighbor cache. P=1 retains
`partition_axis=-1`; P>1 ownership map/hash must remain unchanged under an
isotropic scale. The next global Verlet build consumes the notification and
clears it only after rebuilding. Neighbor setup recomputes `thickness_*` through
the existing `get_num_bins` path. This primitive is now used by the supported
isotropic NPT routes: all NPT runs use the full-replication path, while the
local-domain entry rejects NPT before dynamics or halo updates. Detailed B1
acceptance evidence remains in the validation report.

For either supported NPT ensemble, this operation runs after VV2, thermo and
thermostat and before snapshot/thermo/XYZ/restart output. The same-step thermo stress remains
the pre-scale sample while box columns and snapshot wrapped positions use the
scaled state. `unwrapped` follows the pinned v1 behavior and is not affinely
scaled. Targeted results are recorded in
[box contract validation](../status/validation/npt-validation.md#box-contract-20261103).

### NPT 路由与控制状态（B2–B4/G-B）

`npt_ber` 与已通过 G-B 的 `npt_scr` 只能使用全量复制模式的全局盒变更原语；P>1 无论几何是否满足局部域分解资格条件均由 dispatcher 强制全量复制模式，并输出 `DMGMD_DOMAIN mode=m1 reason=npt-m1-only`。局部域分解路径入口必须在任何动力学/halo 更新前拒绝 NPT，不能复制缩放逻辑到局部域分解模式。SCR 的统计支持限于 216 原子 NEP-C、300 K/0 GPa、单节点冻结矩阵：base 为 P1/HostStaged 与 P2/P4 两后端，half-dt 仅 P1/HostStaged；原18-look总体 INCONCLUSIVE 历史仍保留。详见[n3最终确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)。

盒缩放因子是 rank 0 单一状态，成功后向所有 rank 广播同一个标量；每 rank 对完整位置数组做相同仿射缩放，再刷新盒逆、partition 轴与 global neighbor cache。不得每 rank 独立算压力随机数。压力采样与 thermostat/box 更新的时点见[控压器标准](barostat.md)。SCR 的 root RNG/BDP→pressure-noise 调用序列按已验收 B3 实现与 B4 记录执行；该验收不表示支持 RDMA、跨节点或局部域分解模式下的 NPT。

## 通信后端与部署

唯一支持栈为 Open MPI+UCX。原生 source ../env/md-mpi.sh；Docker 自动加载
/opt/dmgmd/env/md-mpi.sh。固定 UCX PML/MCA path 并排除 HCOLL；内置 PMIx 可用编译时目录。
默认 HostStaged：device pack→pinned D2H→MPI→H2D→device unpack。
CudaAware 传 device buffer，先经 MPIX query、Allreduce(IN_PLACE)/Allgatherv/Gatherv/Bcast
主动数值自检及 p2p 自检；数值不符回退 HostStaged，MPI 错误按失败合同 abort。
一 rank 一 GPU，local rank 选择与 UUID 去重；不构造 NEP_MULTIGPU。

<a id="m1-每步通信量"></a>
## 全量复制模式的每步通信量

collective API 计全局 input/output buffer 字节，不代表物理 wire traffic。indexed pack 按
owned slot 转 AoS，接收按 gid/slot plan 恢复 SoA；不能按 rank 拼接顺序消费。plan 建立时
验证身份，普通步不重复传 gid。令 P=rank 数、N=global_count，无 adaptive/输出/迁移：

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

含 BDP 的整个输入在执行前校验 `DMGMD_ENSEMBLE_SEED` 的 presence 和原始字符串：root
用长度+内容两次 Bcast 分发，再以一次 Allreduce 比较各 rank；之后解析 uint32 十进制。
局部域分解模式的独立入口再次执行同一预检，避免绕过 dispatcher 时漏检；这些启动控制通信不计逐步行。
没有逐步 RNG 状态传输。选项限制、分段重置和非连续 restart 见[系综合同](ensembles.md)。

## 日志与整体计时

DMGMD_COMM_LOG_INTERVAL 默认1000，各 rank 须一致；行是被采样的单步量，不是 K 步聚合。
逐步精确测试显式 K=1。局部域分解另有逐 rank 类别字段，见下文。

每 run 输出 DMGMD_TIMING phase=run，CUDA sync+MPI barrier 后起算，结束同步完成；
含段首 force、步循环、通信/输出和启用的诊断收尾。seconds_min/mean/max 是同作业 rank 范围，
吞吐=global_count*steps/seconds_max，不把 duplicated NEP 计为额外物理工作。
phase=total 从所选 runtime 入口到 finish 后起止，包含部分初始化/清理；不含 main 解析、
MPI 初始化、dispatcher 势加载和全部析构/finalize，不是进程生命周期。
正式端到端比较用关闭详细 profiler 的 run 墙钟。正确性 suite 不设速度门槛，历史单机
benchmark 见[状态](../status/current.md)，跨节点效率无实测。

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

## 局部域分解路径 路径（现行合同）

### 布局与身份

局部域分解模式的 device 数据面下每 rank 只保存自己的 owned 原子与 ghost；完整 host identity
当前仍按 `data-layout.md` 保留用于静态元数据/兼容输出：

- 槽位顺序统一为 `[0, owned)` owned（按 global_id 升序）、随后 dependency
  ghosts、最后 coordinate-only ghosts；ghost 段按 `(source face, source rank,
  global_id)` 确定性排序；`local_count` 是所有 SoA 与 NEP workspace 的唯一
  stride（`global_count` 只是元数据）；
- dependency ghosts 是与本 rank slab 的 MIC 距离 <= `d_dep` 的相邻 rank owned
  原子（获得 Verlet 行、typewise 表、descriptor/Fp/partial）；coordinate-only
  ghosts 是距离在 `(d_dep, d_coord]` 的原子（仅作邻居候选）；
- 每个槽位携带 global_id、owner/source rank、face 与周期 image shift 元数据；
  发送端按 MIC 带去重，接收端拒绝重复 gid。当前每 gid 只有一个 wrapped slot，
  image shift 不再叠加到数值坐标，不表示已支持多 image ghost；
- `manager_owner` 在一个 cache epoch 内保持不变，可以暂时不同于当前位置算出的
  `geometric_owner`；只有统一 rebuild transaction 才提交 owner 迁移。ghost 位置在普通步
  通过缓存 face 计划刷新（24 B/原子），成员关系只在统一 rebuild 时重估（40 B membership
  记录 + 4 B/face count handshake）。

缓存充分条件见[cache-validity](cache-validity.md)。typewise 半径按 pinned kernel 的实际消费路径枚举：radial list 成员需要
`d < (rc_radial[t1]+rc_radial[t2])/2`，angular list（radial-first filter）需要
`d < min(Rr, Ra)`，ZBL 消费 angular list，因此 `R_force = R_dep = Rr`；
`d_dep = max R_force + skin`，`d_coord = max chain (R_force + R_dep) + 2*skin`。
类型对平均严格复现 kernel 的 float 表达式 `(a+b)*0.5f`，并取其与 loaded-float
操作数精确平均的较大者；kernel 向上舍入时再向 `+inf` 扩一个 double ULP，禁止 host
double 平均低估实际消费 cutoff。
无法证明上界（非有限/非正 cutoff）时 fail closed。

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
5. 唯一 cache decision：VV1 在 wrap 前把 owned 原子的真实位移累加到 epoch
   displacement；只检查每个 global ID 的 manager copy，将局部最大位移平方的严格超限
   判定合入一次 uint32 reason-mask OR。空 owned rank 仍参加 OR，不做缓存位移 MAX。
   非有限位移、无法证明的连续性或单步超过一个盒长在 halo/force 前全 rank
   报错；全局位移 `> skin/2` 才进入 rebuild，恰好阈值仍复用。Neighbor 接受明确的
   `confirmed_reuse` / `must_rebuild` action，不再执行第二次位移检查；
6. 普通步保持 manager、slot/layout、membership、communication map 与 neighbor rows 不变，
   只按缓存 face 计划刷新 ghost 坐标（24 B/原子），并验证 mapping/refreshed epoch；微小跨
   slab 或周期端只改变 `geometric_owner`，不单独迁移；
7. rebuild transaction 中，若 manager 与最终 geometric owner 不同，则沿现有 direct
   all-rank 路由执行 Alltoall count handshake + Alltoallv，一步直达最终 slab；载荷含 gid、
   type、mass、charge、group labels、wrapped position、half-step velocity、unwrapped（启用
   时），force/PE/virial 不迁移。随后统一重建 membership/layout/map，复用或扩展 capacity，
   清零 epoch displacement 并重建 neighbor rows；无 owner 变化的 skin rebuild 也走同一
   membership/layout/neighbor transaction；
8. 域分片 NEP：dependency-center 邻居表/descriptor/partial
   （`[0, owned+dep)`），owned radial force/many-body/ZBL（`[0, owned)`），
   候选 `[0, local_count)`；
9. owned VV second half；
10. 按[消费者需求](thermo-demand.md)执行 owned thermo 求和 + 8-double Allreduce
    （NVE 只在 thermo/XYZ 输出步需要；NVT 每步保留，ghost 的 PE/virial scratch 不参与）；
11. owned thermostat（NHC 后半步或 BDP/Berendsen 末步缩放；NHC 前半步在第 3 项 VV1 前
    另做 owned KE 归约、root 链更新与因子广播）；
12. 输出/measurement：local-owned-prefix gather 携带 global ID，root 恢复恰好
    N 条按 global ID 排序的记录后复用既有 formatter（ghost 永不直接输出）。

第一个 run 前的初始力先完成 local owned 初始化、bootstrap 设备 wrap（与
pinned wrap kernel 逐位一致）、halo 与 NEP workspace。多段 run 延续同一 domain state，
不在段间恢复 replicated device arrays，也不因段首兼容 force call 强制重建已确认的
membership/neighbor cache。restart 不序列化 cache/owner epoch；新进程按新 rank 数从输入行
bootstrap 新 epoch。

### 通信后端与字节口径

O3 的 begin/progress/finish、单槽位、FUNNELED、请求/事件完成与错误合同见
[halo 生命周期](halo-lifecycle.md)。默认立即 finish；O4 可显式在二者间提交 early。两后端 payload
与请求数量不变；finish 现在等待物理 unpack 完成才释放槽位。

`HostStaged` 与 `CudaAware` 都为 p2p 扩展了同一 tag 方案（左右方向独立 tag 与
buffer，P=2 同 peer 安全；P=1 无操作；零 count 合法）。CudaAware 启用门新增
真实 device-buffer p2p Send/Recv 自检（自检失败回退 HostStaged），由
`tests/mpi/check_environment.py` 断言 `cuda_aware_p2p_self_test=passed`。

`DMGMD_COMM` 的既有六个字段保持 global-aggregate 语义（全量复制与 局部域分解一致）；新增
字段口径如下，均记录**发送 rank 自己的 local 值**，每 rank 另有
`DMGMD_DOMAIN_COMM rank=... step=...` 行供逐 rank 精确断言：

| 字段 | 口径 | 内容 |
| --- | --- | --- |
| `p2p_calls` | local | 已完成的 Isend/Irecv 请求个数（每轮左右两面合计 4 个） |
| `halo_send/recv_bytes_local` | local | 位置刷新（24 B/原子/步）+ membership 记录（40 B/原子，仅重建步） |
| `migration_send/recv_bytes_local` | local | Alltoallv 迁移记录（无 group 时 80 B/原子，unwrapped 启用时 104 B） |
| `control_send/recv_bytes_local` | local | face count handshake（8 B/轮）、Alltoall/Allgather of counts |

默认 `DMGMD_COMM_LOG_INTERVAL=1000`，因此普通短 run 不再逐步输出通信记录；需要逐步
精确记账的测试显式设置为 `1`。局部域分解模式的逐次 `DMGMD_DOMAIN_LAYOUT` 与
`DMGMD_DOMAIN_MIGRATION` 默认关闭，设置 `DMGMD_DOMAIN_DIAGNOSTICS=1` 后恢复，字段与
rank 语义不变。无论详细诊断是否开启，每个 run 段末每 rank 输出一条
`DMGMD_DOMAIN_SUMMARY`，记录该段的 migration/rebuild 步数、layout upload、workspace
逻辑更新、capacity growth event、GPU allocation 与 thermo 实际计算/跳过次数；这些计数为 rank-local，不新增
逐步 collective。详细 layout 记录中的 `gpu_allocations` 是本次 Atom+NEP+Neighbor layout
更新实际触发的 `GPU_Vector` 分配次数。

普通 局部域分解步不含任何 N-scaled collective（position Allgatherv 不存在于此协议）；
输出步增加 gid gather（8N）+ 各字段 gather（8 B/atom/component，root 侧恢复
input-slot 顺序）；correct_velocity 触发步增加 80N 的 gather/scatter。除下述周期记录外，
每步固定执行 decision reason OR（4P）；有 thermo 消费者时增加 64P 与一次 SUM，所以
input/output 各为 `4P + 64P * needs_thermo` B、`1 + needs_thermo` 次 collective；
NVT 的 `needs_thermo` 恒为 1。rebuild 若发生 owner 变化再增加 Alltoall count
和 migration payload，所有 rebuild 增加 face-count/membership p2p。统一 decision 取代旧的
migration-OR + neighbor-rebuild-OR 双判定，不得由 Neighbor 再发起位移 collective。
O7 将原缓存 displacement MAX 的阈值判定移到各 rank 后合入 OR；自适应时间步的
MAX 及下述 neighbor 周期 MAX 保留。证明、独立实际调用计数与配对实测见
[O7 报告](../status/performance/cache-reduction-results.md#o7-results-20261023-report)。
`neighbor.out` 每 1000 次 force 调用（含首次）由各 rank dependency-center local
max 经 `MPI_MAX` 聚合后仅 rank 0 写单条既有格式记录。统计必须在**本次** Verlet/typewise
邻居表生成后读取；真正 `local_count=0` 的 rank 贡献零但仍参加 collective。初始力或段首力
的聚合属一次性 control plane，不计入 step 行；若记录落在 step force，则该 step 精确增加
2 次 collective、`mpi_input_bytes_global += 16P`、`mpi_output_bytes_global += 16P`。

设置 `DMGMD_DOMAIN_TIMING=1` 后，每个积分步恰好分类为 `ordinary` 或 `rebuild`，并用固定
CUDA event 与 host monotonic clock 记录 decision、migration、membership/layout、
allocation/upload、halo pack/transfer-wait/unpack、cell/neighbor、NEP、integration、thermo、
scientific output 和 remaining。默认关闭时不创建计时 event（O3 生命周期 event 始终存在）、不输出逐步记录、也不增加计时专用
同步；开启时为每步保留 event，在既有段末 device synchronization 后读取，不依赖 NVE thermo D2H；
诊断资源为 O(段步数)，并在每个 run 段末
各做一次 SUM/MIN/MAX 归约，输出有界 summary/histogram。device interval、host critical-path
interval 与 MPI-call wall interval可能重叠，报告不得相加；端到端性能仍以详细计时关闭时的
`DMGMD_TIMING phase=run seconds_max` 为准。

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
分阶段中间场交换方案、3D 与多节点硬化见[扩展计划](../plans/domain-decomposition.md)；O5 正式性能矩阵已完成，见[报告](../status/performance/communication-computation-overlap-results.md#o5-20261021-report)，O10 等剩余工作见[计划](../plans/communication-computation-overlap.md)。

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
