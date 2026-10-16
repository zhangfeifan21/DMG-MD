# MPI runtime：M1 与 M2a

类别：现行标准。核对日期：2026-10-16。

`run_replicated` 每 rank 只加载一次 NEP 参数，deferred workspace 在选定模式后分配。
P1 恒 M1 replicated-full；P>1 在正交全周期、各方向厚度>2.5*(rc_radial_max+1)、
slab_width≥d_coord 且半径可证明时选 M2a local-domain。其余已支持输入回退 M1；
P>1 triclinic/非周期直接 unsupported。root 输出：

```text
DMGMD_DOMAIN mode=m2a|m1-fallback axis=x|y|z|none d_dep=... d_coord=... reason=...
```

布局由[data-layout](data-layout.md)定义，NEP 依赖证据在[kernel 清单](../reference/kernel-inventory.md)。
当前没有实际 overlap；O2 串行分区默认 off，O3 halo 原位置立即 finish。

## M1 空间所有权

reader 的 global=owned=local=N、ghost=0，但 MPI 权威 owner 是 SpatialOwnership：
owner_by_slot 单一事实源，派生 owned_indices/mask 与 gid→slot，gid 为 [0,N) permutation。
owned_indices 按 gid 排序，空 slab/N<P 合法。P1 全归 rank0，不选轴/重算 owner。
P>1 沿最长边切等宽 fractional 半开 slab；tie 为 y 胜 x/y 或 y/z，x 胜 x/z。
内部面属右侧，s=0 首 slab，s=1 最后；wrap 保留单次 <0/+1、>1/-1。

M1 迁移是 gid 逻辑移交，不压缩/重排数组。旧 owner 做 VV1，新 owner 做 VV2：

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

## 通信后端与部署

唯一支持栈为 Open MPI+UCX。原生 source ../env/md-mpi.sh；Docker 自动加载
/opt/dmgmd/env/md-mpi.sh。固定 UCX PML/MCA path 并排除 HCOLL；内置 PMIx 可用编译时目录。
默认 HostStaged：device pack→pinned D2H→MPI→H2D→device unpack。
CudaAware 传 device buffer，先经 MPIX query、Allreduce(IN_PLACE)/Allgatherv/Gatherv/Bcast
主动数值自检及 p2p 自检；数值不符回退 HostStaged，MPI 错误按失败合同 abort。
一 rank 一 GPU，local rank 选择与 UUID 去重；不构造 NEP_MULTIGPU。

## M1 每步通信量

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

## 日志与整体计时

DMGMD_COMM_LOG_INTERVAL 默认1000，各 rank 须一致；行是被采样的单步量，不是 K 步聚合。
逐步精确测试显式 K=1。M2a 另有逐 rank 类别字段，见下文。

每 run 输出 DMGMD_TIMING phase=run，CUDA sync+MPI barrier 后起算，结束同步完成；
含段首 force、步循环、通信/输出和启用的诊断收尾。seconds_min/mean/max 是同作业 rank 范围，
吞吐=global_count*steps/seconds_max，不把 duplicated NEP 计为额外物理工作。
phase=total 从所选 runtime 入口到 finish 后起止，包含部分初始化/清理；不含 main 解析、
MPI 初始化、dispatcher 势加载和全部析构/finalize，不是进程生命周期。
正式端到端比较用关闭详细 profiler 的 run 墙钟。正确性 suite 不设速度门槛，历史单机
benchmark 见[状态](../status/current.md)，跨节点效率无实测。

## I/O 与 NEP_MULTIGPU

thermo/XYZ/restart 仅 root formatter 写。M1 ordinary NEP 的非 root neighbor.out 写本机
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

## M2a local-domain 路径（现行合同）

### 布局与身份

M2a 的 device 数据面下每 rank 只保存自己的 owned 原子与 ghost；完整 host identity
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

### 每步顺序（M2a）

1. （correct_velocity 触发步）gather `(global_id, owned position, owned
   velocity)` 到 root，root 按 global ID 恢复全局顺序并复用现有 CPU 修正，再按
   current owner scatter 回 owned local 槽位（禁止把 3N 数据写入 local_count
   stride 的 device 数组）；
2. 自适应时间步（owned max |v| host max-Allreduce）；
3. owned VV first half（+ unwrapped 跟踪）；
4. owned 位置按 pinned 单次 `<0/+1`、`>1/-1` 语义 wrap；若单步位移导致坐标一次 wrap 后
   仍在盒外，则统一 decision 在任何 halo/force 前报 unsupported multi-box displacement；
5. 唯一 cache decision：VV1 在 wrap 前把 owned 原子的真实位移累加到 epoch
   displacement；只检查每个 global ID 的 manager copy，做一次全局最大位移归约与一次
   reason-mask OR。非有限位移、无法证明的连续性或单步超过一个盒长在 halo/force 前全 rank
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
10. owned thermo 求和 + 8-double Allreduce（ghost 的 PE/virial scratch 不参与）；
11. owned thermostat；
12. 输出/measurement：local-owned-prefix gather 携带 global ID，root 恢复恰好
    N 条按 global ID 排序的记录后复用既有 formatter（ghost 永不直接输出）。

第一个 run 前的初始力先完成 local owned 初始化、bootstrap 设备 wrap（与
pinned wrap kernel 逐位一致）、halo 与 NEP workspace。多段 run 延续同一 domain state，
不在段间恢复 replicated device arrays，也不因段首兼容 force call 强制重建已确认的
membership/neighbor cache。restart 不序列化 cache/owner epoch；新进程按新 rank 数从输入行
bootstrap 新 epoch。

### 通信后端与字节口径

O3 的 begin/progress/finish、单槽位、FUNNELED、请求/事件完成与错误合同见
[halo 生命周期](halo-lifecycle.md)。生产仍立即 finish，无实际 overlap。两后端 payload
与请求数量不变；finish 现在等待物理 unpack 完成才释放槽位。

`HostStaged` 与 `CudaAware` 都为 p2p 扩展了同一 tag 方案（左右方向独立 tag 与
buffer，P=2 同 peer 安全；P=1 无操作；零 count 合法）。CudaAware 启用门新增
真实 device-buffer p2p Send/Recv 自检（自检失败回退 HostStaged），由
`tests/mpi/check_environment.py` 断言 `cuda_aware_p2p_self_test=passed`。

`DMGMD_COMM` 的既有六个字段保持 global-aggregate 语义（M1 与 M2a 一致）；新增
字段口径如下，均记录**发送 rank 自己的 local 值**，每 rank 另有
`DMGMD_DOMAIN_COMM rank=... step=...` 行供逐 rank 精确断言：

| 字段 | 口径 | 内容 |
| --- | --- | --- |
| `p2p_calls` | local | 已完成的 Isend/Irecv 请求个数（每轮左右两面合计 4 个） |
| `halo_send/recv_bytes_local` | local | 位置刷新（24 B/原子/步）+ membership 记录（40 B/原子，仅重建步） |
| `migration_send/recv_bytes_local` | local | Alltoallv 迁移记录（无 group 时 80 B/原子，unwrapped 启用时 104 B） |
| `control_send/recv_bytes_local` | local | face count handshake（8 B/轮）、Alltoall/Allgather of counts |

默认 `DMGMD_COMM_LOG_INTERVAL=1000`，因此普通短 run 不再逐步输出通信记录；需要逐步
精确记账的测试显式设置为 `1`。M2a 的逐次 `DMGMD_DOMAIN_LAYOUT` 与
`DMGMD_DOMAIN_MIGRATION` 默认关闭，设置 `DMGMD_DOMAIN_DIAGNOSTICS=1` 后恢复，字段与
rank 语义不变。无论详细诊断是否开启，每个 run 段末每 rank 输出一条
`DMGMD_DOMAIN_SUMMARY`，记录该段的 migration/rebuild 步数、layout upload、workspace
逻辑更新、capacity growth event、GPU allocation 次数；这些计数为 rank-local，不新增
逐步 collective。详细 layout 记录中的 `gpu_allocations` 是本次 Atom+NEP+Neighbor layout
更新实际触发的 `GPU_Vector` 分配次数。

普通 M2a 步不含任何 N-scaled collective（position Allgatherv 不存在于此协议）；
输出步增加 gid gather（8N）+ 各字段 gather（8 B/atom/component，root 侧恢复
input-slot 顺序）；correct_velocity 触发步增加 80N 的 gather/scatter。除下述周期记录外，
每步固定执行 displacement MAX（8P）、decision reason OR（4P）与 thermo（64P），所以
input/output 各为 `76P` B、3 次 collective；rebuild 若发生 owner 变化再增加 Alltoall count
和 migration payload，所有 rebuild 增加 face-count/membership p2p。统一 decision 取代旧的
migration-OR + neighbor-rebuild-OR 双判定，不得由 Neighbor 再发起位移 collective。
`neighbor.out` 每 1000 次 force 调用（含首次）由各 rank dependency-center local
max 经 `MPI_MAX` 聚合后仅 rank 0 写单条既有格式记录。统计必须在**本次** Verlet/typewise
邻居表生成后读取；真正 `local_count=0` 的 rank 贡献零但仍参加 collective。初始力或段首力
的聚合属一次性 control plane，不计入 step 行；若记录落在 step force，则该 step 精确增加
2 次 collective、`mpi_input_bytes_global += 16P`、`mpi_output_bytes_global += 16P`。

设置 `DMGMD_DOMAIN_TIMING=1` 后，每个积分步恰好分类为 `ordinary` 或 `rebuild`，并用固定
CUDA event 集与 host monotonic clock 记录 decision、migration、membership/layout、
allocation/upload、halo pack/transfer-wait/unpack、cell/neighbor、NEP、integration、thermo、
scientific output 和 remaining。默认关闭时不创建计时 event（O3 生命周期 event 始终存在）、不输出逐步记录、也不增加计时专用
同步；开启时只在既有 thermo D2H/段末 device synchronization 读取 event，并在每个 run 段末
各做一次 SUM/MIN/MAX 归约，输出有界 summary/histogram。device interval、host critical-path
interval 与 MPI-call wall interval可能重叠，报告不得相加；端到端性能仍以详细计时关闭时的
`DMGMD_TIMING phase=run seconds_max` 为准。

`DMGMD_DOMAIN_TRACE=1` 仅在同时设置 `DMGMD_DOMAIN_TIMING=1` 的 M2a 路径有效，默认关闭。
`DMGMD_DOMAIN_DIAGNOSTICS` 启动记录中的 `timing`/`trace` 是程序实际模式。每 rank/步
输出 `DMGMD_DOMAIN_TRACE`：`CLOCK_MONOTONIC` 的整数纳秒记录 step、decision、rebuild、
ordinary halo、NEP host enqueue 和 thermo readiness 边界；ordinary halo 还记录 pack/unpack
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
段末只执行原有一次 device synchronization，随后读取末个 event。

## 验证入口与后续

完整 runner 覆盖和比较原则只在[Golden Test](golden-test-standard.md)维护，CLI 见
[MPI README](../../tests/mpi/README.md)；已执行结果见[当前状态](../status/current.md)。
[O2 分区](domain-partition.md)保持 L stride、半径和每中心顺序；先完成 halo 后串行执行。
M2b、3D 与多节点硬化见[扩展计划](../plans/domain-decomposition.md)，实际 overlap 见[O4/O5](../plans/communication-computation-overlap.md)。
