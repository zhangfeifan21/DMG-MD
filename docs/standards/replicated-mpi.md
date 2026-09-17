# Replicated-data MPI runtime with M1 spatial slab ownership

类别：现行标准。本文描述当前已实现的 M1 runtime 合同：数据面 replicated-full，
所有权为空间 slab，迁移为 global_id 上的逻辑移交。

## 数据所有权

每个 rank 的 device arrays 仍以全局 `N` 为 SoA stride，持有完整
position/type/mass/global_id 等输入（`global_count == storage stride == N`，
`ghost_count == 0`）。`AtomCounts::owned_count` 保持 N（replicated stride），
**不是**本 rank 的空间 owned 数量。

MPI 权威所有权由 `include/dmgmd/spatial_ownership.hpp` 的 `SpatialOwnership`
表示（M0 的连续 `OwnedRange` 已退役）：

- `owner_by_slot[N]` 是单一事实源：每个 replicated 槽位唯一 owner rank；
- `owned_indices`（按 `global_id` 升序）、`owned_mask` 与
  `slot_of_global_id` 全部由 owner map 派生，不允许出现可分叉的第二份状态；
- `global_id` 是持久身份，构造时验证为 `[0,N)` 的唯一 permutation；
- 空 `owned_indices`（空 slab）是合法状态；`N < P` 被明确支持，且即使 `N >= P`
  也不保证每个 slab 都有原子；
- P=1 使用常量平凡映射（全部归 rank 0），不选择分区轴、不限制 box、
  每步不重算所有权，输出与 M0 单 rank 逐字节一致；
- P>1 仅支持正交、三方向全周期 box（否则所有 rank 一致报 unsupported，不静默
  投影），沿最长边切 P 个等宽 fractional half-open slab。分区轴 tie 规则与
  锁定参考分区器一致（y 胜 x/y、y/z 平手，x 胜 x/z 平手，立方盒选 y）。
  slab 语义：内部边界属右侧 slab，`s=0` 属第一个 slab，精确 `s=1` 归最后
  一个 slab，周期越界与 `wrap_positions` 的 `<0/+1`、`>1/-1` 单次调整一致。

**M1 的“迁移”是所有权的逻辑移交**：authoritative 状态从旧 owner 转给新 owner，
数组槽位不删除/插入/重排。真正的本地数组压缩是 M2 内容。

## 每步顺序（M1 迁移时序）

记 `current_ownership` 为本步开始时的 owner，`next_ownership` 为本步位置更新、
复制并 wrap 后按空间重新计算的 owner。M0 删除了普通步的 velocity 复制，因此
非 owner rank 的 velocity 可能陈旧；迁移时序保证跨 slab 原子由旧 owner 完成
first half、新 owner 在拿到最新 velocity 后完成 second half，不漏积分、不重复
积分、不读陈旧副本：

1. （correct_velocity 触发步）先以 `current_ownership` 做 indexed velocity
   `MPI_Allgatherv` 恢复复制态，root 在 CPU 上对全体系修正后 `MPI_Bcast`
   完整 velocity；
2. adaptive timestep 只遍历 current `owned_indices`；
3. velocity-Verlet first half、position 更新、unwrapped 更新只遍历 current
   `owned_indices`（kernel 按 device index list 启动）；
4. 以 `current_ownership` 做 indexed position `MPI_Allgatherv`，恢复每张 GPU
   的完整 position；
5. 每张 GPU 用 ordinary `NEP`（`N1=0, N2=N`）对完整 replicated coordinates
   计算 scratch output；wrap 与 force/PE/virial scratch 保持全量；
6. 根据已 wrap 的 replicated position 在每 rank 上重新计算 `next_ownership`
   （目标 owner 由最终位置直接决定，一步跨多个 slab 或跨周期端都是普通情形）；
7. 在任何依赖 `next_ownership` 的 kernel 之前：先做一次固定尺寸的 ownership
   map hash `MPI_Allreduce` 验证所有 rank 得到完全相同的 map（本地 map 构造
   失败也经由同一握手对称失败，不会有的 rank 抛异常、其余进入 collective）；
   若 map 发生变化，先用**旧的** `current_ownership` 做 indexed velocity
   Allgatherv（如启用 unwrapped 则连同 unwrapped）把旧 owner 的最新 half-step
   状态复制到所有 rank；position 已在步骤 4 同步，type/mass/species/group/
   global_id 是静态 replicated 数据，M1 不迁移这些字段；随后原子切换到
   `next_ownership`（epoch++，重建 pack/unpack plan；epoch 不变时复用 plan）；
8. velocity-Verlet second half、thermo local sum、Berendsen 缩放和输出 gather
   都使用 `next_ownership`；
9. 下一步开始时 `next_ownership` 成为 `current_ownership`；多段 run 之间
   ownership 状态持续存在，不回到均衡区间。

所有 rank 以相同顺序进入 collective。velocity 在非迁移、非 correct_velocity 的
普通步不再复制；唯一读非 owned velocity 的消费者是 correct_velocity（触发步
步首恢复）和迁移本身（切换前恢复）。

non-owned NEP output 只是 scratch，不参加积分、thermo 或输出。它没有 ghost 身份；本阶段
`ghost_count` 必须为 0，也没有 halo、点对点通信或 neighbor cache 跨步所有权。

## ownership 一致性与记录

- 启动时先用与逐步 epoch 相同的固定尺寸 hash Allreduce 要求所有 rank 的完整初始
  owner map（含 global-ID/slot permutation）一致；该握手发生在任何使用 per-rank
  variable count 的 indexed collective 之前，属于 control plane、不计入逐步通信记录；
- 初始 hash 门通过后，再对 N 个整数做 `MPI_Allreduce(SUM)` 的 owned mask 覆盖证明：
  每个槽位恰好一个 owner，否则运行失败（`DMGMD_CENTER_PARTITION ... missing=0
  overlapping=0`；P>1 时含 `partition=spatial-slab axis=...`，P=1 不含分区字段）。
  rank 0 同时记录每个 rank 的 `owned_count`（`DMGMD_CENTER_OWNERSHIP`）；
- 每步（P>1）用一次 16 字节的 hash Allreduce 验证所有 rank 的 map 一致：
  identical map + 本地 map 是全函数 ⟹ 每个 epoch 的 missing=0、overlapping=0
  与 `sum(owned_count) == N` 成立；
- 每次 epoch 变化时 rank 0 输出
  `DMGMD_OWNERSHIP_EPOCH step=... epoch=... changed_atoms=... owned_sum=N
  transitions="<gid:old->new,...>"`（最多列 64 条并标注 truncated），供迁移
  fixture 断言确切的 owner 转移。

## NEP 中心分片完整性证明

启动时每个 rank 建立 owner map，先以固定尺寸 hash Allreduce 证明各 rank 的完整 map 一致，
再对 N 个整数做 owned-mask `MPI_Allreduce(SUM)`；每个元素必须严格为 1，否则运行失败。
rank 0 同时记录每个 rank 的 owned count、missing 数和 overlapping 数。两道门共同证明
**authoritative owned output partition** 在所有 rank 上一致、完整且无重叠。mask 覆盖和恰为 1
本身不能在 P>=3 时排除非 owner rank 之间互相分叉，因此不能替代前置 map-hash 握手。

它不证明把 GPUMD `NEP::N1/N2` 直接设为 owned 槽位集合后数学仍完整。代码证据表明该做法当前
不完整：

- radial force 读取中心邻居的 `Fp[n2]`（锁定 GPUMD `src/force/nep.cu:727-728`，复现于 `src/gpumd_compat/nep.cu`）；
- many-body force 读取反向 directed partial `f12(n2,n1)`
  （`src/force/potential.cu:209-250`）；
- 这些分片外 intermediate 不会由仅覆盖 `[N1,N2)` 的 descriptor/partial kernels 生成。

因此启动记录明确包含：

```text
owned_output_coverage=complete
nep_kernel_centers=replicated-full
nep_N1_N2_shard_complete=false
```

本阶段选择完整 NEP scratch 以保持数值正确性，然后仅承认 owned 槽位。未来若要真正减少 NEP
中心计算，必须先暴露 descriptor、`Fp`、directed partial 的 phase boundary 并交换这些中间量；
不能直接放宽 `N1/N2`。M1 的所有权迁移是 global_id 上的逻辑移交，不是 ghost/halo 实现。

## 通信后端

唯一受支持的运行栈是由仓库同级 `env/md-mpi.sh` 选择的 Open MPI+UCX。脚本固定 UCX PML、
Open MPI/PMIx component path，并排除 HCOLL；测试不得回退到 system MPI/UCX。

`HostStaged` 是默认及必需后端。每次 device collective 都严格执行：

```text
CUDA source --pack--> CUDA send
CUDA send --D2H--> pinned host send
pinned host send --Open MPI/UCX collective--> pinned host receive
pinned host receive --H2D--> CUDA receive --unpack--> CUDA destination
```

`CudaAware` 使用相同的 pack/unpack，把中间 send/receive device pointer 交给 Open MPI。
启动先调用 `MPIX_Query_cuda_support()`；只有所有 rank 都报告 supported，才允许进入
device-buffer `MPI_Allreduce(MPI_IN_PLACE)`、`MPI_Allgatherv`、`MPI_Gatherv` 和 `MPI_Bcast` 数值
自检。四类 collective、CUDA 同步和结果校验全部通过后才选择 CudaAware，否则回退
HostStaged。因此 capability query 只是安全前置条件，不会替代数值自检。

## 每步通信量

rank 0 默认每步输出一行 `DMGMD_COMM`。设置正整数 `DMGMD_COMM_LOG_INTERVAL=K` 后只记录
step 为 K 的倍数的采样行；每一行仍是该单步的量，不是 K 步聚合值。所有 rank 必须看到相同
设置，否则启动失败。短 MPI differential 使用默认 K=1；长 NVE suite 使用 K=10 或 100
控制 100000-step stdout 规模。记录的是 collective API 的全局 input/output buffer 字节，而
不是 MPI 私有算法的物理 wire traffic。

M1 的 owned 集合是槽位的任意子集，因此所有 owned collective 都是 **indexed** 语义：CUDA
pack kernel 按 `device_owned_indices` 从 replicated SoA 打包为 AoS，`MPI_Allgatherv`/
`MPI_Gatherv` 使用各 rank 不同的 count（含 0），接收端按
`global_id/slot scatter plan` 还原 replicated SoA——绝不按 rank 拼接顺序直接消费。
pack/unpack plan 在 ownership epoch 不变时逐字复用；每步不额外传输 global_id
（plan 建立时已验证）。HostStaged 与 CudaAware 共用同一 pack/unpack 布局与
ownership 语义，只差传输介质。

令 `P=world_size`、`N=global_count`。P>1 时没有 adaptive timestep、dump snapshot 和
ownership 变化的普通一步：

| collective | 全局 MPI input bytes | 全局 MPI output bytes |
| --- | ---: | ---: |
| indexed position Allgatherv (3 doubles/atom) | `24N` | `24NP` |
| ownership map hash Allreduce (2 uint64/rank) | `16P` | `16P` |
| thermo Allreduce (8 doubles/rank) | `64P` | `64P` |
| 合计 | `24N + 80P` | `24NP + 80P` |

P=1 没有 hash Allreduce（平凡 map 永不变化），合计与 M0 相同：`24N + 64P` /
`24NP + 64P`。HostStaged 同一步的 D2H/H2D 与 MPI input/output 同量（hash Allreduce 是
host-only collective，不产生 D2H/H2D）。adaptive timestep 另加一个 host double max
Allreduce，即 MPI input/output 各 `8P`。

**ownership 迁移步**（owner map 发生变化，P>1）：先用旧 ownership 做 indexed velocity
Allgatherv（MPI input `24N`、output `24NP`），如启用 unwrapped 再做一次（同量）；随后
epoch 切换本身无通信。迁移步另加的通信即 `24N/24NP`（velocity）或 `48N/48NP`
（velocity+unwrapped）。

若本步触发 `correct_velocity`，步首先以 current ownership 恢复复制态：一次 indexed
velocity Allgatherv（MPI input `24N`、output `24NP`；HostStaged 增加同量 D2H/H2D），
随后既有修正路径的 3N-double velocity Bcast（MPI input `24N`、output `24NP`；HostStaged
同时增加 D2H `24N` 和 H2D `24NP`）。

输出步 gather 的基础 snapshot 包含 position(3)、velocity(3)、force(3)、PE(1)、virial(9)，
额外 MPI input/output 各 `19 * 8N = 152N` bytes；unwrapped position 再加 `24N`；root 侧
按 scatter plan 还原成 N-sized 全局 SoA 后才交给 formatter。HostStaged 增加同量 D2H；
CudaAware 在 rank 0 增加同量 output download。启动日志、输入 fingerprint、中心 coverage
与初始 ownership 建立属于一次性 control-plane 通信，不计入 step 行。

## 时间记录

rank 0 为每个 `run` command 输出一行 `DMGMD_TIMING phase=run`，并在作业结束输出一行
`phase=total`。run segment 在计时前执行 CUDA synchronize 和 MPI barrier，结束后再次同步
CUDA；记录各 rank wall time 的 min/mean/max。`global_atom_steps_per_second` 使用
`global_count * steps / seconds_max`，表示用户问题规模的吞吐，不把 replicated-full NEP 在各
rank 的重复计算量累计为额外工作。`phase=run` 包含该 segment 的 MPI collective 与输出，
`phase=total` 还包含 replicated runtime 初始化和清理。

这些数据是诊断记录，不是当前正确性 suite 的性能验收门槛；当前 replicated-data 原型也不据此
发布 speedup 或 scaling 结论。

## 异常日志

进入 `MPI_Abort` 前，发生异常的 rank 向 stderr 写入并立即 flush 一条
`DMGMD_ERROR rank=... local_rank=... hostname=... category=... message="..."`。异常路径不调用
`MPI_Gather` 或其他 collective：某些 peer 可能仍阻塞在 CUDA/MPI 调用中，此时为了聚合日志而
执行 collective 会死锁并掩盖原始失败。Open MPI/PRRTE 的 I/O forwarding 负责把多节点 rank
的 stderr 转发到 `mpirun` 启动端（本项目部署约定中为 rank 0 所在节点）；测试运行器捕获这个
合并后的 stderr 并保存为 `execution.stderr`。记录中的 world rank 与 hostname 用于区分来源，
记录顺序不作为执行顺序证据。rank I/O 隔离的共享失败出口（见下节）与该规则一致：它在抛出
异常**之前**已完成状态 Allreduce、诊断 Gather 和消息 Bcast，因此任何 rank 都不会带着未完成的
collective 进入异常路径。

## I/O 与 NEP_MULTIGPU

所有 thermo/XYZ/restart formatter 只在 world rank 0 调用。GPUMD ordinary NEP 会周期性 append
`neighbor.out`（每 1000 次调用，含首次）；rank 0 保留这一兼容副作用，直接写作业目录。
每个非零 rank 在**本机** `temp_directory_path()` 下用 `mkdtemp` 原子创建
`dmgmd-rank-io-<nonce>-r<rank>-XXXXXX`：目录 mode 恒为 0700（owner-only，不受 umask 影响），
唯一性由 `mkdtemp` 的六字符随机后缀保证，不依赖 nonce 或时间戳。rank 在该目录中创建普通
`neighbor.out` 文件后 chdir 进入；不创建任何 symlink，也不广播任何文件系统路径，因此各节点
`/tmp` 互不可见亦可运行。

setup 是两阶段 collective：各 rank 捕获本地错误（解析 TMPDIR、mkdtemp、创建 neighbor.out、
chdir）后先 `Allreduce` 成功标志；任一失败时，已 chdir 的 rank **先恢复原 cwd**，随后所有
rank 抛出同一条聚合错误（包含每个失败 rank 的 world rank、hostname、目标路径与系统错误），
经常规 `MPI_Abort` 出口有界退出，不产生 collective hang。若恢复 cwd 本身失败，该 rank 不得
删除仍可能作为进程 cwd 的 scratch；聚合错误明确记录跳过清理并保留精确目录供诊断。

`finish()` 分三阶段：恢复状态归约（任一失败判作业失败，即使 MD 输出已完成）→ 各非零 rank
经安全边界校验后只删除**自己的**目录（父目录必须等于启动时记录的本机 temp 根、basename 必须
是完整前缀加 mkdtemp 六随机字符、且不是 symlink；校验失败保留目录并报警，删除范围不扩大）
→ cleanup 状态汇总。cleanup 失败不判作业失败：rank 0 输出一行
`DMGMD_RANK_IO_CLEANUP status=warning rank=... hostname=... ...` 并保留该 rank 的精确目录供
诊断。析构函数只做本地、best-effort、无 MPI 的恢复/清理；需要 collective 的错误传播与清理
只存在于构造函数与 `finish()`，从不在栈展开期间执行。

`DMGMD_RANK_IO_FAULT="<world_rank>:<op>"`（op ∈
`mkdir/file/chdir/setup_restore/restore/cleanup`）是仅供测试使用的故障注入钩子，使且仅使指定
rank 在指定步骤失败；`setup_restore` 专用于组合测试，不是用户可配置行为。该合同由
`tests/mpi/run_rank_io_isolation.py` 驱动验证（见验证入口）。尚未完成的双节点验收见
[multi-node-io.md](../plans/multi-node-io.md)。

runtime 直接构造 ordinary `NEP`，并在选择 CUDA device 后不再枚举设备决定势实现；没有
构造 `NEP_MULTIGPU`，因此 MPI rank 看见多张本机 GPU 也不会自动占用它们。

## 验证入口

`tests/mpi/check_environment.py` 首先检查指定 executable 的 Open MPI/UCX 链接、CUDA build
capability、UCX `cuda_copy/cuda_ipc`、GPU 唯一绑定及实际 device-pointer collectives。只有该
独立门槛通过，`tests/mpi/run_mpi_differential.py` 才对 1/2/4 rank 运行全部已锁定 case，并检查：

- 初始 per-atom energy/force/9-component virial 对 GPUMD golden；
- 短 NVE trajectory 和由 thermo 得到的 max excursion/drift slope；
- 不同 rank 数 thermo header、列数、segment/row 结构；
- 每 rank 启动记录、唯一 GPU UUID、owned coverage proof（missing=0、overlapping=0、
  owned counts 覆盖 global_count）和每步通信记录；
- 默认对 HostStaged 和 CudaAware 运行同一矩阵并做 cross-backend differential。

`tests/mpi/run_mpi_migration.py` 是 M1 专用迁移门：以显式初速度的 fixture（跨内部边界、
周期端回绕、一步跨多 slab、暂时空 slab、N<P）运行 1/2/4 rank ×
HostStaged/CudaAware，并验证：

- 每次 ownership epoch 的 owned_sum==N、changed_atoms 与 transitions 一致；
- 用逐步 dump 的 double 精度 wrapped position 独立重算每原子的期望 owner，要求日志的
  owner 转移与轨迹推导完全一致（明确观察到预期 global_id 的 owner transition）；
- P=1 永不迁移；4-rank crossing fixture 必须实际出现内部相邻、周期首尾和一步跨多 slab
  三类转移，empty-slab fixture 必须实际出现规定的空置/重新占有状态，N<P fixture 必须
  出现至少 `P-N` 个 zero-count rank；
- 每一步（含 correct_velocity、迁移、unwrapped、snapshot 的组合）的 collective 次数、
  MPI input/output、HostStaged D2H/H2D 与 CudaAware output-download 字节必须与本标准公式
  精确相等，不只检查下限；
- dump_restart 后以不同 rank 数恢复、多段 run、correct_velocity 触发步与
  velocity/force/potential/virial/unwrapped 输出；
- 跨 rank 数/后端输出与 1-rank 无迁移参考在 committed 容差内一致（容差不放宽）；
- 作业目录只有 rank 0 输出。

`tests/long_nve/run_long_nve.py` 在此短矩阵之外提供 4096/12288/5000 原子、五显式初态和
100000-step release 正确性验收，包括真实 `E(0)`、长期守恒统计、确定性 NVT 温度统计、时间
平均 RDF、MSD、GPUMD↔DMG-MD 双向静态构型回放及跨 rank restart。release/nightly 同时覆盖
NEP5、typewise cutoff、flexible ZBL 和 typewise ZBL cutoff 的静态/短轨迹分支，并默认执行
HostStaged 与 CudaAware。它保存 wall time/吞吐诊断但不设置性能通过门槛；replicated-full NEP
阶段不发布多卡 speedup 或 scaling 结论。

`tests/mpi/run_rank_io_isolation.py` 在同一环境门槛后验证上文 I/O 隔离合同：每 rank 独立
TMPDIR 根（单节点模拟 node-local temp）、成功后作业目录只含 rank 0 兼容输出且无 scratch
泄漏、五种本地故障（mkdir/file/chdir/restore/cleanup）单 rank 注入的有界可诊断退出、restore
失败判作业失败而 cleanup 失败仅报警并保留 0700 目录与普通 neighbor.out 供诊断。脚本接受
可重复的 `--mpiexec-arg` 传入跨节点 launcher 参数；每个正常返回的 rank 先报告本地状态，随后
独立 MPI probe 扫描每个已分配节点，因此验证不依赖启动端能看见远端节点的临时文件系统。
三 rank 及以上还覆盖 setup 失败与另一 rank 的 `setup_restore` 失败组合。

## 后续演进

从本协议演进到 owned/ghost 域分解与 halo 通信的设计见
[domain-decomposition.md](../plans/domain-decomposition.md)。其中 M0（删除每步 velocity
Allgatherv，correct_velocity 触发步保留恢复复制态）与 M1（空间 slab 所有权 + indexed
collective + global_id 逻辑迁移）已实施并纳入本标准；其余里程碑（M2 起的本地 owned/ghost
布局、halo、NEP 中心分片和点对点通信）仍为待审批计划，尚未修改 runtime。
