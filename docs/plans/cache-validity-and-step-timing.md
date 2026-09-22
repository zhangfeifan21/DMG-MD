# M2a 缓存有效性统一重建与分步计时设计

类别：待实施计划。
状态：**APPROVED**（2026-09-22 已按本设计实施并验证；现行合同已迁入
[`replicated-mpi.md`](../standards/replicated-mpi.md) 与
[`data-layout.md`](../standards/data-layout.md)，实测见
[`stage2-cost-report.md`](../status/stage2-cost-report.md)）。本文保留批准时的证明与风险边界，
不再作为当前行为的唯一事实源。

## 1. 目标、边界与当前证据

本设计是性能路线第二阶段，只改变 M2a local-domain 将来如何决定迁移、halo membership、
通信映射和邻居表重建，并增加普通步/重建步诊断计时。M2a 两跳 position halo、单轴等宽
slab、direct all-rank migration routing、现有 NEP 数学和 force assembly 均保持不变；不引入
3D 分解、动态负载均衡、局部 cell 网格、M2b/MatPL、CUDA Graph 或通信计算重叠。

第一阶段基线可直接复用：修改前 clean revision
`27e07d92b84f0e02d08d99c400c52b795175341f`，Release 配置、修改前二进制 SHA-256、输入与
potential SHA-256、2026-09-18 nightly 报告及第一阶段最终 candidate SHA-256 均已记录在
[current.md](../status/current.md)。本轮源码 HEAD 是
`cfb3b2ec3ac1a4dccd16de43e2d81f3d9ef84432`；开始设计时工作树 clean。本文不新增性能结果，
也不把正确性 nightly 的 wall time 当作无 I/O 基线。

当前实际路径的主要问题是 `run_domain_segment` 每步先以几何 slab 检测 foreign owned；只要
发生微小越界就立即 `do_migration`，否则刷新 ghost 后再由
`NEP::neighbor_needs_rebuild`/`Neighbor::needs_rebuild_domain` 做第二次 skin 判定。迁移和 skin
重建最终都会进入 `exchange_halo_membership`，但由两个判定驱动。每个 `run` 段入口还以
`launch_domain_force(..., true)` 强制邻居重建。目标是用一个可证明的 cache epoch 和一次全局
一致判定替代这些分散触发点。

## 2. 身份、几何位置与五类缓存

### 2.1 管理 owner 不等于当前几何 slab

定义：

- `manager_owner(a,e)`：cache epoch `e` 内对 global atom `a` 唯一拥有积分、thermo、输出和
  持久动态字段写权限的 rank。只在 rebuild transaction 提交时改变。
- `geometric_owner(x_a)`：以当前 wrapped position 和现行半开 fractional slab 规则计算出的
  rank。它只决定下一次 rebuild 的目标 owner，不在普通步改变权限。
- `local_slot(a,r,e)`：`a` 在 rank `r` 的 epoch-local 索引；owned prefix 与 ghost suffix 的
  顺序在 epoch 内不可改变。它不是持久身份。
- `ghost_membership(r,e)`：rank `r` 在 epoch 建立时选出的 dependency/coordinate ghost 键集合。
  键至少为 `(global_id, source_manager, face, image_shift)`；当前唯一-image 布局继续适用。
- `communication_map(r,e)`：两 face 的 `(send owned slot, receive ghost slot, stream index, peer)`
  映射。普通步只按该映射刷新坐标。
- `neighbor_reference(r,e)`：与确定的 `layout_epoch`、logical stride、slot key 顺序、box、
  cutoff/skin 和参考坐标绑定的 Verlet `NN/NL` 及 `x0/y0/z0`。

因此原子可在 epoch 内越过 slab 面，而 `manager_owner != geometric_owner`；旧 manager 仍唯一
积分和统计该原子，邻 rank 仍把它当来自旧 manager 的 ghost。只有统一 rebuild transaction
才迁移权限。任意时刻所有 global ID 的 `manager_owner` 必须恰好覆盖一次；ghost 永不积分、
统计或直接输出。

### 2.2 失效矩阵

| 对象 | 确认复用的必要条件 | 必须失效/重建 |
| --- | --- | --- |
| manager owner | epoch 未提交新 ownership；global ID 覆盖仍唯一 | rebuild 提交；身份覆盖错误为 fatal，不尝试复用 |
| local slot/layout | `layout_epoch`、owned/ghost key 与 logical stride 未变 | owner、membership、分类、slot/order、type/static identity、box/decomposition 改变 |
| ghost membership | §3 的全局参考位移界成立；radii/box/参考 slab 未变 | 位移越界、owner 提交、radii/box 变化或映射结构错误 |
| communication map/send list | sender 保持 manager；slot key/peer/stream 与 membership epoch 一致 | 任一关联 epoch/key/count 改变；收发 fingerprint 不匹配为 fatal |
| neighbor reference/rows | 同一 layout/stride/key/box/cutoff epoch，且全局位移界成立 | 首次使用、位移越界、任何 layout/stride/key/box/cutoff 变化、overflow/error |
| descriptor/Fp/f12/force scratch | 只在本次 force call 的坐标和 neighbor rows 上有效 | 每次 force call 后即视为过期；绝不跨步当缓存复用 |

容量增长本身不改变 logical layout，但若 device pointer 改变，所有已捕获 view 都失效；本阶段
仍在 rebuild transaction 内重新取得 view。allocation capacity 不能充当 layout 或 reference
版本。

## 3. 两跳闭包的充分条件

### 3.1 位移定义

令 `q_a(e)` 为 epoch 提交时 atom `a` 的连续参考位置，`x_a(t)` 为求力时位置。令
`Delta_a(t)` 是从 `q_a` 到 `x_a` 沿实际积分/wrap 历史累计的位移，而不是只用两个 wrapped
坐标做一次 MIC 后得到的可能混叠值。普通步必须已证明

```text
delta(t) = max over all global atoms |Delta_a(t)| <= skin/2.
```

实现上只检查各 atom 的唯一 manager copy，再做 global MAX；不检查 ghost duplicate。每个
owned slot 需要一个 epoch displacement/reference 累加量，VV1 后以 wrap 前的真实增量更新；
rebuild commit 后清零。这样接近周期边界、往返跨面和接近整盒位移都不会被 wrapped MIC
错误抵消。现行“一步最多只允许单次 wrap”的路由守卫仍独立检查；无法证明增量连续或发现
非有限值时不得返回 reuse。

### 3.2 Neighbor Verlet rows

`Neighbor::find_neighbor_domain` 在参考坐标以 `R_build = R_global + skin` 建表。若当前一条
consumer edge 满足 `|x_b-x_a| < R_global`，三角不等式给出

```text
|q_b-q_a| <= |x_b-x_a| + |Delta_a| + |Delta_b|
            < R_global + 2*delta <= R_global + skin.
```

故该 edge 已在 Verlet row；额外旧 edge 会在每次 force call 的
`find_neighbor_list_large_box` 中用当前坐标/typewise cutoff 再过滤，不改变科学语义。这个证明
要求 row center 和 candidate 的 global ID/image 在整个 epoch 与参考槽位相同。仅比较同 local
index 的 `x0`/current position 而不验证 key/epoch 不足以证明这一点。

### 3.3 Dependency membership

NEP 实际读取链为：

- owned force center `i` 的 `find_force_radial` 读取 `i` 和 neighbor `j` 的 `Fp`；
- `gpu_find_force_many_body` 读取 `f12(i,j)`，并在 `j` 的排序 row 中查找反向
  `f12(j,i)`；ZBL 同样消费 angular list；
- 所以所有满足当前 `|x_j-x_i| < R_force(type_i,type_j)` 的 `j` 必须是 dependency center，
  获得 neighbor row、typewise lists、descriptor/Fp 和 angular partial。

因为 epoch 建立时 `q_i` 位于其 manager 的参考 slab，逐 edge 有

```text
distance(q_j, reference_slab(i))
  <= R_force(type_i,type_j) + 2*delta.
```

因此 membership 在参考坐标按
`D_dep >= max_type_chain(R_force + 2*delta_limit)` 建立即完整。取
`delta_limit=skin/2` 后得到现行 `d_dep = max R_force + skin`。这里的结论依赖 manager 在
epoch 内不迁移、send list 以参考位置建立并逐步刷新其固定成员，而不是依赖 `j` 当前仍属于
原 slab。

### 3.4 Coordinate membership（两层关系）

dependency center `j` 的 descriptor/partial 又读取满足
`|x_k-x_j| < R_dep(type_j,type_k)` 的 candidate `k`。分别对 `i-j` 和 `j-k` 的参考 edge 使用
三角不等式，可得保守且可组合的充分条件：

```text
distance(q_k, reference_slab(i))
  <= R_force(type_i,type_j) + R_dep(type_j,type_k) + 4*delta.
```

故 membership 以

```text
D_coord >= max_(ti,tj,tk) [R_force(ti,tj) + R_dep(tj,tk)]
          + 4*delta_limit
```

建立即可闭合 owned -> dependency center -> coordinate candidate。代入
`delta_limit=skin/2` 才得到现行 `+2*skin`。所以 `d_coord` 的数值本身不是安全证明；还必须
同时成立：全局连续位移界、参考 owner/slab 不变、key/slot 映射不变、每步 ghost refresh 完成、
typewise kernel reach 枚举完整。端点直接界有机会更紧，但跨两条 MIC edge 的 image 一致性和
当前单-slot ghost 表示尚未形式化证明，本阶段不据此缩 halo。

### 3.5 Send list 与刷新

rebuild 时每个 manager 用 `q` 选择距相邻 reference slab 小于 `D_coord` 的 send members；
eligibility 继续要求 `slab_width >= d_coord`，因此沿用只与左右 peer 通信和 P=2 去重。普通步
不重新做 band test：只要 manager/slot 不变且 §3.1 成立，所有将来可能需要的成员在 epoch
建立时已经入表；每步只需在求力前把这些 global ID 的当前 wrapped 坐标写入固定 receive
slot。send list、receive stream 或 source owner 任一改变都使整个 cache decision 成为
`MustRebuild`，不能局部修补后仍复用 neighbor reference。

## 4. 统一、全 rank 一致的判定

### 4.1 三态与原因

判定 API 使用三态，不再用多个 bool：

- `Undetermined`：本步尚未获得 device displacement/routeability 结果或尚未完成 global
  reduction；禁止进入 halo refresh/NEP。
- `ReuseConfirmed`：所有 rank 已共同确认 epoch/key/box/radii 不变量和位移界；本步为普通步。
- `MustRebuild(reason_mask)`：任一 rank 报告强制原因；本步为重建步。原因可多选但类别仍只有
  一个。

建议 reason bits：`BOOTSTRAP`、`DISPLACEMENT_LIMIT`、`OWNER_ROUTING_NEEDED`（只作迁移统计，
不单独触发；通常与 displacement 同现）、`LAYOUT_OR_KEY_INVALID`、`BOX_OR_RADII_CHANGED`、
`EXPLICIT_INVALIDATION`、`CAPACITY_OR_OVERFLOW_RETRY`、`CONTINUITY_UNPROVEN`。非有限位置、
单步超出现行 wrap/通用路由能力、fingerprint/coverage 错误是 `Fatal`，不是可恢复 reason。

每步 VV1 后，一个 fused/local decision kernel：

1. 累加每个 owned global ID 的连续 epoch displacement；计算 local max；
2. 验证有限值与现行单步 wrap 可路由范围；
3. 计算当前 geometric owner，仅写“若重建需迁移”的计数/目标，不提交 owner；
4. 合并 host-side epoch/key/box/radii invalidation bits。

随后执行一次 logical OR reason mask + MAX displacement（可用一次固定小结构的
`MPI_Allreduce`，或语义等价的两个 reduction）。所有 rank 得到同一个最终状态。目标不是消灭
Allreduce，而是消灭互相重叠、可能分歧的判定。runtime 得到最终状态后，Neighbor 不得再做
位移检查。

### 4.2 执行伪代码

```text
bootstrap:
  derive geometric owner from input; assign exactly one manager per gid
  decision = MustRebuild(BOOTSTRAP)
  rebuild_transaction(decision)
  compute initial force; force_state = valid

for each run segment:
  preserve cache/layout/reference epochs across the segment boundary
  preserve current GPUMD-compatible force-call behavior until separately proven
  (if a segment-initial force is required, pass the resolved cache state; do not force rows)

  for each step:
    correct_velocity_if_due(current manager ownership)
    dt = adaptive_dt_owned_global_max()
    VV1_owned_and_accumulate_continuous_displacement()
    apply_pinned_single_wrap_to_owned()

    local = inspect_epoch_keys_displacement_routeability()
    global = allreduce_max_displacement_and_or_reasons(local)
    assert global != Undetermined

    if global == ReuseConfirmed:                 // ordinary step
      refresh_ghost_positions(cached communication_map)
      assert refreshed_epoch == layout_epoch
      neighbor_action = ReuseConfirmed
    else:                                         // rebuild step
      download/currently stage owned persistent fields as required
      route every geometric-owner mismatch directly to final rank
      verify global gid coverage exactly once
      rebuild membership from new owner reference positions
      rebuild local layout, slot keys and communication map
      grow/rebind/upload buffers if needed
      reset continuous displacement references
      atomically publish one new cache/layout/reference epoch
      neighbor_action = MustRebuild(global.reason_mask)

    NEP.compute_domain(..., neighbor_action)      // no internal displacement check
    VV2_owned; thermo_owned; thermostat_owned; scientific_outputs_by_gid
    record exactly one ordinary/rebuild timing class for this step

segment end:
  resolve queued CUDA events at the existing final synchronization
  aggregate local counters/histograms once; print bounded summaries
```

“原子当前在别的 slab”只影响 rebuild transaction 的 migration plan，不令
`ReuseConfirmed` 变成 rebuild。transaction 先保持旧 ownership 完整，收齐 direct Alltoallv 后
验证 global ID 覆盖，再一次性发布新 owner/layout epoch；失败时不得留下半提交视图。

## 5. Buffer 与 epoch 生命周期

建议 `DomainState` 持有：

```text
CacheEpoch {
  uint64 layout;       // owner, slots, ghost classes
  uint64 mapping;      // send/recv list and stream indices
  uint64 neighbor;     // NN/NL reference
  uint64 box_radii;    // box, decomposition, cutoff, skin fingerprint
  Decision state;      // only Undetermined between VV1 and global decision
}
OwnedReference { gid, reference_position/image, accumulated_displacement }
SlotKey[local] = { gid, source_manager, face, image_shift, role }
```

- `OwnedReference` 是 correctness metadata，始终存在，不能依赖用户是否请求
  `unwrapped_position` 输出；输出用 unwrapped buffer 的兼容语义不变。
- 普通步只写 owned position/velocity/displacement、ghost position 和每步 NEP scratch；
  owner/layout/mapping/reference buffers只读。
- rebuild transaction 的 host/device staging buffers 活到所有 nonblocking MPI request 完成；旧
  send/recv buffer 和 device view 活到对应 event/request 完成后才可复用或扩容。当前不新增 overlap，
  但生命周期必须显式，不能靠机械 `cudaDeviceSynchronize` 保证。
- transaction 在临时 `next_*` 对象中建 owner/layout/mapping并校验，最后一起递增/发布 epoch。
  `Neighbor` 只接受匹配的 `layout_epoch` 和明确 action；建表成功后才令
  `neighbor_epoch == layout_epoch`。overflow/error 不得发布 reference。
- 普通 halo 完成后设置 `refreshed_epoch == mapping_epoch`；NEP 入口拒绝 stale mapping。
- allocation capacity 可跨 epoch 复用；logical size/stride、pointer generation 和 slot key 另行
  记录。

## 6. 边界与兼容行为

- **PBC/wrap/边界往返**：保持 pinned 单次 `<0/+1`、`>1/-1` 和 `s==1` 归属规则；位移判定
  用连续累计量，halo/NEP 坐标仍 wrapped + MIC。跨 slab 后又返回不会迁移，除非全局缓存因
  位移等原因重建。精确 slab 面只在 rebuild commit 时按半开规则选 owner。
- **一次跨多个 slab**：若超过 cache 位移界，本步求力前进入 rebuild；继续使用现有 direct
  all-rank final-owner routing，不退化成邻接逐跳。单步超过一个盒长或连续增量无法可靠恢复时，
  在任何 halo/force 前全 rank 明确报错；不得让 wrapped MIC 静默返回 reuse。
- **空 rank、N<P**：local max 对空 owned 集贡献 0，reason mask 仍参加 collective；空 rank
  同样获得统一 decision，并可在 rebuild 后变为非空。logical `local_count=0` 不读取 padding。
- **连续 run**：domain/cache/reference epoch 延续，不因 command boundary 迁移或重建。当前每段
  额外 initial force 是否为 GPUMD 可见语义尚未判定；实现前先用连续-run golden 核实。即使保留
  force call，也只允许复用已确认的 NN/NL，不由 Neighbor 二次判定。
- **输出**：current manager 的 owned prefix 仍按 global ID gather；几何 slab 不匹配不影响唯一
  输出、thermo 或 thermostat。诊断不得改变 rank 0 为唯一科学文件 writer。
- **跨 rank restart**：restart 仍不序列化 cache/owner/epoch；新进程从文件行重建 global ID，按
  新 rank 数的几何 slab bootstrap 并强制建新 epoch。不得跨进程复用 send list/neighbor reference。
- **box/potential**：当前 run 内 box/radii 固定；未来任何合法变化必须产生
  `BOX_OR_RADII_CHANGED`。无法证明新半径时沿现行 eligibility fail closed，不复用旧 epoch。

兼容合同中有意变化只有：M2a 的 manager ownership 与当前几何 slab 可暂时不同，migration 日志
只出现在 rebuild step；现有测试“每一步 logged owner 必须等于 trajectory 几何 owner”需改成
“每个 epoch commit 后相等，epoch 内 manager 唯一且缓存证明成立”。M1/P1、fallback、数值精度、
potential、容差、global ID、ghost 权限、rank 0 输出和 restart 文件语义不变。该变化获批并实施、
验证前不得写入 standards。

## 7. 普通步/重建步计时

### 7.1 模式与分类

详细计时使用独立的全 rank 一致开关（建议 `DMGMD_DOMAIN_TIMING=1`），默认关闭。性能比较只在
关闭详细计时、关闭逐步 diagnostics、保持现有 run 前 barrier/同步与
`DMGMD_TIMING phase=run` 的情况下进行；`seconds_max` 和
`global_atom_steps_per_second` 仍是端到端主口径。

每个积分 step 按最终统一 decision 恰好计入一个互斥类别：

- `ordinary`：`ReuseConfirmed`；
- `rebuild`：`MustRebuild`，另累计可多选 reason bits；
- bootstrap/segment setup 不伪装成积分 step，单列 `setup`。

### 7.2 阶段和时钟

阶段标签固定为：`decision`、`migration`、`membership_layout`、
`allocation_upload`、`cell_neighbor`（含现有全局 box cell 网格扫描、Verlet rows、排序和
typewise list）、`halo_pack`、`halo_transfer_wait`、`halo_unpack`、`nep`（descriptor/Fp/partial/
force/ZBL，不含 cell_neighbor）、`integration`（VV1/wrap/VV2/thermostat）、`thermo`、
`scientific_output`、`remaining`。correct_velocity 和 adaptive dt 暂计入 `integration`，同时保留
子计数；若实测显著再新增非重叠子阶段。

不能把不同 clock 的数字相加。每 rank 维护两个 ledger：

1. **host/critical-path wall ledger**：用 `MPI_Wtime`（或单调 host clock，但全实现统一）记录
   不重叠的顶层 host/MPI interval。MPI call wall time记入对应 `*_transfer_wait`/collective 阶段，
   包含到达偏斜与等待。segment 实际 wall 为权威，`remaining_host = segment_wall -` 已标记的
   非重叠 host intervals；若因测量误差为负则报告错误，不截断伪装。
2. **device ledger**：在现有 CUDA stream 上以 events 围住 GPU 阶段，得到实际执行时间；host
   enqueue 时间不冒充 GPU 时间。pack/unpack、cell/neighbor、NEP、integration、thermo kernel
   分别记 event。events 成对且不嵌套；同一 stream 可求和，未来多 stream/overlap 时必须先求
   interval union，不能重复相加。

event 不在每个 kernel 后 synchronize。采用可回收 event ring，在自然存在的 D2H/MPI readiness
点或 segment 末已有 `cudaDeviceSynchronize` 后解析；若 event 尚未完成就延后，不插入新同步。
blocking H2D/D2H 的 host 等待进入 host ledger，相应 copy event 进入 device/copy ledger，两者明确
是嵌套的不同资源视角。`cell_neighbor` 与 `nep` 的边界需要在 `compute_domain` 内各放一对 event，
但不改变 kernel 顺序。

### 7.3 段末汇总

各 rank 本地累计，不逐步输出，也不为每个指标逐步 Allreduce。每个 run segment 末一次性汇总：

- ordinary/rebuild/setup 次数；各 reason 次数（允许和大于 rebuild 次数）；
- 每类 step wall 的 count/sum/min/max、均值和固定桶 histogram（用于近似 p50/p95，不保存长程
  每步数组）；
- 各阶段 host wall 与 device event 的 rank mean、rank max、local imbalance；MPI wait/transfer
  单列；
- segment 的 `seconds_min/mean/max` 与最慢 rank，作为真实关键路径结果。

输出明确命名 `rank_mean`、`rank_max`、`critical_path_segment`。各阶段的 rank max 发生在不同
rank/step，禁止相加并称为总时间；device 子阶段也禁止与包含它的 host wall interval相加。
普通/重建分布用于解释成本，正式吞吐仍只采用详细计时关闭时的 `DMGMD_TIMING seconds_max`。

## 8. 实施时的文件与符号清单

本轮不修改下列代码；获批后的最小预计修改面为：

| 文件/符号 | 计划修改 |
| --- | --- |
| `include/dmgmd/domain_layout.hpp` | 增加 cache key/epoch、reference-band 纯 CPU 规划与校验；保留现有 radii 和 direct migration |
| `src/domain_runtime.cu` `DomainState` | 增加 manager/reference displacement、epoch/fingerprint、三态 decision 和 timing accumulators |
| `count_foreign_owned` | 替换为统一 local decision/连续位移与 routeability kernel；几何越界不单独触发迁移 |
| `run_domain_segment` | 一次 global decision；普通/重建互斥分支；连续段复用 epoch；阶段计时 |
| `do_migration` | 只在 rebuild transaction 内按 current geometric owner direct route，并与 next layout 原子提交 |
| `exchange_halo_membership` / `upload_domain_layout` / `refresh_ghost_positions` | 接受/验证 epoch；拆出可计时的 membership、layout、allocation/upload 边界 |
| `src/gpumd_compat/nep.{cuh,cu}` `neighbor_needs_rebuild`, `compute_domain` | domain API 接受已解析的 `ReuseConfirmed/MustRebuild`；删除 runtime 后的重复判定入口；kernel 数学不改 |
| `src/gpumd_compat/neighbor.{cuh,cu}` `needs_rebuild_domain`, `find_neighbor_domain` | domain path 不再自行检查位移；显式 action + layout/key epoch；legacy P1/M1 API 不动 |
| `include/dmgmd/mpi_runtime.hpp`, `src/mpi_runtime.cu` | 固定小 decision reduction、段末 timing 汇总与机器可读输出；不逐步归约 timing |
| `tests/domain_layout_tests.cpp` | reference-band、epoch/key 失效、边界/P=2/空 rank 的 CPU tests |
| `tests/domain_neighbor_cuda_tests.cu` | reuse 不建表、rebuild 恰建一次、key/stride mismatch fail、event 不改变结果 |
| `tests/mpi/run_mpi_domain.py` | 新 owner/cache epoch oracle、普通/重建原因、通信模型和 timing schema |
| `tests/mpi/run_mpi_migration.py` | 仅 M1 继续逐步几何 owner 合同；M2a 断言移至 domain runner |
| `tests/long_nve/*` | A/B correctness 与详细计时关闭的正式性能采集；不改 manifest 容差 |

## 9. Golden、回归与性能验证设计

实现后按风险分层执行，不在每个小改动后重复全部长程矩阵：

1. **CPU/CUDA 定向测试**：三态只能解析一次；同 epoch key reuse；任一 key/stride/source/image
   改变强制 rebuild；每个 rebuild 只建一次 NN/NL；连续位移不会被周期 wrap/MIC 抵消；CUDA
   timing off/on 的数值输出一致。
2. **合成 domain golden**：原子在 `skin/2` 内跨 slab 并往返，多个普通步不迁移且与 P1 的
   force/PE/virial/trajectory 一致；刚超过阈值在求力前统一 rebuild；恰好阈值覆盖 `>` 判据；
   dependency `j` 和 coordinate-only `k` 分别向最坏方向漂移，保持两跳链闭包。
3. **路由边界**：一次跨多个 slab、周期首尾、接近整盒但仍受支持、超过一盒明确错误；空
   rank、N<P、P=2 同 peer 去重、精确 slab 面和边界来回。验证每次 commit 后 geometric owner
   匹配、epoch 内 manager global ID 恰好一次。
4. **连续 run/restart/output**：连续 run 不因段边界重建 membership/NN；在确认 force-call 兼容
   前保持段首 force 记录。dump/thermo/restart 与 P1 oracle 在现有容差内，restart 用不同 rank
   数 bootstrap 新 epoch；ghost 不参与积分/统计/输出。
5. **现有矩阵**：环境预检后执行 baseline、domain、differential、migration；两种通信后端；
   再做一次 nightly correctness。potential、输入、精度和容差不变，不覆盖 golden。
6. **性能 A/B**：从 §1 的可复用 M2a provenance 派生新 candidate 记录；正式比较关闭
   `DMGMD_DOMAIN_TIMING`/详细日志和科研高频 I/O，明确 timed region。另开诊断 run 比较
   ordinary/rebuild 次数与阶段成本。报告 carbon/water/BaTiO3 的 P1/P2/P4 `seconds_max`、吞吐和
   重复分布，不把 correctness nightly 原始 wall time直接当结论。全局 cell 网格扫描保留并作为
   `cell_neighbor` 待计时项。

## 10. 尚不能证明与审批风险

- 当前 `GhostSlotInfo` 对一个 global ID 只保留一个 wrapped slot，`image_shift` 不进入 kernel；
  在 `slab_width == d_coord`、P=2 重叠带和两条 MIC edge 选择不同 image 时，现有测试支持数值
  正确，但“端点紧界”及所有退化边界的形式证明仍缺失。因此本阶段只能保留保守
  `+2*skin`，不能缩小 coordinate halo。
- 连续 displacement buffer 的具体浮点累计方式尚需 CUDA 边界测试；若无法在现行单次 wrap
  语义下可靠区分大位移与周期 image，必须选择求力前明确错误，而不能退回 wrapped MIC 判定。
- 连续 `run` 的段首额外 force call/`neighbor.out` call index 是否属于必须逐字节保持的 GPUMD
  语义尚未由专门 golden 判定。本阶段只批准 cache row 复用设计，不自动删除该 force call。
- `find_cell_list` 当前按全局 box 建 bins、扫描全部 local candidates；它可能主导重建步，但在
  获得计时前不改成局部网格。
- typewise reach 证明只覆盖当前已支持 NEP/NEP-ZBL 消费链；新增 potential、改变 ZBL/list 语义、
  box 变化或多 image ghost 都必须令旧证明失效并重新审批。
- 允许 manager 与 geometric slab 暂时不同是所有权时序合同变化。它不改变唯一积分/统计 owner，
  但必须经本文测试证明后才能进入 production standards。

本设计的实现门已由本轮明确授权满足。M2b、MatPL 式 ghost 力回传与通信计算重叠仍不在本设计
的实施范围内。
