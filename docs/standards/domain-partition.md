# 局部域分解普通步 NEP 分区与实验重叠

局部域分解求力要用到其他 rank 的边界坐标。有些原子的两层邻居都由本 rank 负责，可以在收到新边界坐标前开始计算。分区（O2）找出这些原子和所需的辅助中心。串行分区先交换完坐标，再分两批求力。重叠调度（O4）借助 O3 的通信接口，在发起和完成交换之间提交第一批计算。两批计算沿用同一默认 CUDA 流。

分区和重叠默认关闭。维护者于 2026-10-15 确认上述方向。[分区验收](../status/validation/communication-computation-overlap.md#overlap-partition-20261015) · [重叠验收](../status/validation/communication-computation-overlap.md#overlap-o4-20261016) · [后续工作](../plans/communication-computation-overlap.md)

## 选择与生命周期

用 `DMGMD_DOMAIN_PARTITION=off|serial|verify` 选择分区，默认 `off`。程序拒绝非法值或各 rank 配置不一致的作业。关闭重叠时，`serial` 只在采用局部域分解且全体 rank 确认复用缓存（`confirmed_reuse`）的普通步生效。本步通信须已完成，解包后的坐标须按默认流顺序对后续计算可见。

P1、全量复制模式、段首求力、重建或迁移步、没有内部中心或负责原子的 rank，以及未证明适用的模型，均走完整同步路径（`full`）。支持范围为普通 NEP4/5、NEP-ZBL，且 `model_type=0`、无 B projection。`verify` 使用同一分区，另在同快照跑未分区对照并检查故意填入的无效值（poison）。性能测量应关闭 `verify`。

用 `DMGMD_DOMAIN_OVERLAP=off|wait|poll` 启用重叠，默认 `off`。`wait` 和 `poll` 要求分区为 `serial` 或 `verify`。程序在调度前拒绝非法值和各 rank 不一致的配置。P1 和全量复制模式记录同步 `full` 回退。局部域分解的每个 rank 按各自的内部中心集合 I 选择完整计算或重叠。`DMGMD_OVERLAP_TRACE=0|1` 默认 `0`，只有开启重叠才能启用，且各 rank 必须一致。

用 `DMGMD_DOMAIN_SCHEDULE=split|producer|producer-compact|producer-geometry` 选择每批计算的范围。默认 `split` 把中间量与最终力都分两批。`producer`（O8.5）只提前过滤邻居和生成部分中心的描述符。`producer-compact` 与 `producer-geometry`（O8.6）再把要生成中间量的中心收集到紧凑索引中。

非法值或各 rank 配置不同时，程序在开始调度前拒绝运行。关闭分区、P1、全量复制模式、初始化、重建、迁移、内部中心或本地槽位为空，以及未证明模型，都沿用 `full`。日志 `DMGMD_SCHEDULE_CONFIG` 记录实际选择。核验是否真的分批执行还需查看运行诊断。

### 一次求力如何确认数据就绪

未选择分区时，`NEP::compute_domain` 走完整的局部计算。使用分区时，运行时通过 `DomainForceContext` 传入模式、普通步标志、步号和 `halo_ready_step`。在串行路径中，`refresh_ghost_positions` 返回后才发布本步就绪凭证，依据是 MPI 请求和 CUDA 解包操作均已完成。缓存周期的 `refreshed_epoch` 单独不足以证明本步就绪。

在重叠路径中，运行时经 `DomainForceContext` 注入发起、完成和清零的回调。NEP 通过回调推进，无需直接调用 MPI。`DomainForceTicket` 只在本次求力内存在，绑定 step、call、key 和 halo generation。它依次检查 posted、early、匹配的 halo-ready、late 及 finalize。准备阶段的 `owned-ready` 只允许读取负责原子，完成回调才提供读取边界副本所需的证明。串行仍检查本步就绪凭证。

求力中拒绝工作区 resize 或重入。提交第一批计算前和完成通信后，检查缓存键及输入、工作区、掩码视图。运行时同时检查布局、映射和位置。求力凭证留在本次计算内。[halo 生命周期](halo-lifecycle.md)

### 如何划分两批计算

把最终需要计算力的负责原子记作 `O=[0,owned)`，把要计算中间量的负责原子和依赖副本记作 `D=[0,owned+dep)`。当 `owned=0` 时 D 为空。设 `L=local_count`，V 表示实际的完整 Verlet 邻居行。V 包含 skin 余量内的邻居，尚未按类型对截断半径（typewise cutoff）过滤。

先找出邻居全部在 O 内的中心 S。若中心 i 及其邻居都在 S 内，i 的两层依赖都可以从本地原子取到，归入 I。其余最终力中心为 B。计算 I 的最终力还要准备中心 i 及其邻居的中间量，这些中心合称 E。D 中其余中间量到第二批计算，记作 H。

```
S = {c in O | V(c) subset O}
I = {i in S | V(i) subset S}       B = O \ I
E = union({i} union V(i), i in I)  H = D \ E
```

分类只读 V 和全局 ID。GPU 检查邻居数与容量、槽位范围、自边以及 gid 是否严格按行排序，再生成掩码。非法邻居表会导致完整计算越界，因此一旦发现便退出。

E 完全属于 O。E 中不在 I 的中心只提前计算中间量，其最终力仍归到第二批。

## 读写、布局与唯一性

保持全部原槽位/global ID、每行顺序及每线程数学/累加顺序。原 full/split/producer 保持原 grid/block；
O8.6 的两条显式紧凑路径只改变 producer 中心映射，见下节。mask guard 位于
任何输入/workspace 读取之前。所有 position/Fp/sums/ELL/partial 使用 L stride，mask 大小
也为 L；不压缩粒子数组，不用 interior_count 作为 stride 或 launch 中心范围。

runtime 在一次逻辑 force 开头将全 L 的 PE/force/9-component virial 清零一次：

```
halo communication complete -> unpack ordered on default stream -> clear once
filter(E) -> descriptor(E) -> radial(I) -> partial(E) -> many_body(I) -> ZBL(I)
filter(H) -> neighbor record if due -> descriptor(H) -> radial(B)
          -> partial(H) -> many_body(B) -> ZBL(B) -> VV2/thermo/output
```

I/B 与 E/H 分别互斥且完整覆盖 O 与 D，故每个 producer 和最终 owned 中心只执行一次。
descriptor 的 PE += 对辅助 E\I 不会重复；其 ZBL 在 late 执行。每中心仍是 descriptor→ZBL、
radial→many_body→ZBL 的累加顺序。Fp/sums/有效 partial 每步由唯一 producer 覆盖；不依赖
无效 ELL 尾部的旧值，也不额外清零生产 workspace。反边仍逐步按 gid 查找本步 angular ordinal。
ghost PE 只是 scratch，不积分、不归约/输出，ghost force/virial 无最终写入。

重叠调度在发出通信前完成分类和分配。程序打包坐标，等发送源就绪后发出通信请求，清零输出一次并提交第一批计算 E/I。主线程随后进入 MPI，可以直接完成通信，也可以先做有限次数的进度查询再完成。接收的数据实际解包到 ghost 槽位后，才执行第二批计算 H/B 并结束本次求力。

现行调度沿用默认 CUDA 流和 MPI 初始化线程。H2D 和解包在同一流上排在第一批计算之后。`CudaAware` 保留接收后的设备同步，该同步也会等待第一批计算。

### O8.5 producer 调度

保持同一个 E/H 分类和 `DomainPartitionKey`，只拆坐标过滤与 descriptor：

```text
prepare -> begin halo -> clear once -> filter(E) -> descriptor(E)
-> finish halo / physical unpack -> filter(H) -> neighbor record if due
-> descriptor(H) -> radial(O) -> angular_partial(D) -> many_body(O) -> ZBL(O)
-> finalize -> VV2/thermo/output
```

serial 版本先完成 halo，再执行相同 producer/consumer 顺序。普通 eligible 步实际执行
2 filter、2 descriptor、1 radial、1 angular partial、1 many-body，共七次基础 launch；
ZBL 启用时另执行一次 owned launch。完整 split 仍保留十次基础 launch，full 为五次。
全范围 grid、L stride、gid/粒子/行序、每中心浮点表达式和累加顺序不变，不增加紧凑列表。

E 及其完整 Verlet 邻居属于 owned，因此 early filter/descriptor 不需要 ghost 坐标。
descriptor 只生产本中心 PE/Fp/sums；radial 消费自身与邻居 Fp，故等待全部 D descriptor。
angular partial 消费本中心 Fp/sums；many-body 消费双向 partial，故等待全部 D partial。
所有 consumer 均在 halo-ready 后完整执行，early 不写最终 force/virial 或任何 partial。
每个 D 的 descriptor 恰执行一次，owned 每中心仍按 radial→many-body→ZBL 累加，
descriptor PE 在 ZBL 之前，ghost final force/virial 不写。没有新增逐步通信或 stream；
启动时新增 schedule 的指纹一致性检查，发生在数据面与计时段之前。

producer verify 用 ghost 坐标 NaN 的私有快照实际执行 early，在 finish 物理完成后保存
early 状态。oracle 的 descriptor 快照在 force/ZBL 之前取得，避免把 ZBL PE 当作
early descriptor PE；比较 E 的有效 filtered rows、PE/Fp/sums，检查邻居属于 owned、
全部 force/九个 virial 仍为零、partial 仍为 poison、非 E producer 未写。随后恢复 early，
完成 H 和完整 consumer，再与同快照 full 的 PE/force/全部 virial、有效 partial 逐 bit 比较。
serial verify 同样使用 descriptor-only oracle；完整 split 的旧 I force 断言仍独立保留。
oracle 不增加逻辑 force、周期 neighbor sink、MPI 或生产 launch 计数。

逻辑 call counter、1000-call neighbor 记录和两个 MPI_MAX 跨 full/split 共用；verify replay
不增加 force call、neighbor build、MPI 或 I/O。不同 rank 可以各自 full/split，但 sink 次数
与顺序一致，包括空 rank。通信 tags、半径、消息与应用字节合同保持原样。

### O8.6 producer 紧凑与几何调度

`producer-compact`（B）和 `producer-geometry`（C）是显式实验调度，默认仍为 `split`。
两者沿用 O8.5 的 producer/consumer 顺序；radial(O)、partial(D)、many-body(O)、可选
ZBL(O) 在 halo 物理完成且全部 descriptor 就绪后完整执行。full、原 split 和
`producer`（A）继续作为独立对照；partition=off、P1/全量复制模式、setup/rebuild/迁移仍走 full。

B 完整保留图规则 S/I/E。C 直接从实际完整 Verlet 重建时保存的 `Neighbor::x0/y0/z0`
选择 owned slab 内部的中心，不计算 S/I/B：

```text
R_build = rc + Neighbor::skin
ε = 32 * FLT_EPSILON
R_V = nextafter(R_build * (1 + ε) + ε * axis_length, +infinity)
E_geo = {c in O | q[c] > slab_lo, q[c] < slab_hi,
                   min(q[c]-slab_lo, slab_hi-q[c]) > R_V}
H_geo = D \ E_geo
```

R_V 包含完整 Verlet 的 skin，及坐标差转换、float MIC/盒长、距离平方的保守舍入余量；
不使用两跳 coordinate halo 宽度。几何路径要求正交全周期和匹配的参考数组。每次 cache
miss 都在 GPU 上检查完整实际 V 的 count、slot、自边、gid 行序及 `V(E_geo) ⊆ O`，
失败不发布缓存；半径余量本身不能替代这个实际闭包门槛。

两条路径都在 GPU 对 `early[0,D)` 做 CUB exclusive scan，并按原 local slot 顺序
scatter 到 `early_idx`、`late_idx`。两列表互斥、无重复、严格递增、完整覆盖 D；输入和
PE/Fp/sums/邻居等数组仍用原 local slot 和 L stride。filter/descriptor 采用 64 线程 block，
grid 为 `ceil(list_count/64)`，先检查列表尾部，再取 `n1=center_idx[k]`。每中心邻居顺序、
表达式、精度和累加顺序不变。empty late 跳过两次 late producer launch；两个列表非空时
基础 launch 为 7。empty early 在完成 halo 后走明确的 full fallback，不计实际 overlap。
C 的 `interior=0` 是未计算 I 的诊断占位，不能解释为图规则 I 为空。

缓存 key 除原 layout/Verlet/workspace、O/D/L、CPU/float box/PBC 外，还包含实际 schedule、
几何 axis/slab 两端和 R_V；runtime 同时验证 mapping epoch。列表随完整缓存失效，不能只
按 shape/capacity/pointer 复用。普通步用固定 q 和 V；owned/ghost slot 身份不变且连续位移
界保持 V 有效，因此跨 slab/PBC 的小位移不改变 `V(E) ⊆ O` 的坐标所有权证明。重建或
迁移当步同步 full，下一次普通步重新分类。prepare 的扫描、临时空间、计数回传与分配都在
begin halo 之前；early/finish 之间冻结两列表 view 和全部 force workspace。

verify 对 B 使用独立 CPU 图 oracle，对 C 使用参考 q 的 CPU 几何 oracle，再检查两列表的
稳定顺序、覆盖和唯一性。原 ghost NaN、descriptor-only early oracle、未写 force/virial/partial
及最终 full 同快照逐 bit 检查完整保留。GPU 测试另覆盖 0/1/63/64/65/129/255 列表长度、
空 early/late、实际 V 闭包失败，以及同 shape 新 layout、Verlet、workspace 和分类规则。
MPI 的窄 slab 可正常全回退；受控延迟测试另用明确标识的宽 slab 验证真实 early。

详细记录新增实际 early/late grid、late 数、inset、partition 逻辑/容量 bytes 和 scan 临时
bytes；summary 新增实际 compact 调用和累计 cache-miss classify host seconds（hit 另由诊断记录）。逻辑额外空间 B 为
`12L + 12D + 12 + T` bytes，C 为 `4L + 12D + 12 + T` bytes，T 是 CUB 查询返回的临时
空间；物理 capacity 可保留高水位。计数回传复用三 int scratch。维护与容量增长计入整步，
classify host 包络不等于纯 GPU 时间。独立时间线核对实际 specialization/grid/default stream、
E/D、H/D、E/O 和 early GPU 时间，主墙钟关闭详细诊断，不从原子比例推算可隐藏时间。

## 缓存有效性

`DomainPartitionKey` 包含 layout epoch（runtime 同时验证 mapping_epoch 相等）、独立 Verlet
generation、workspace generation、O/D/L、CPU/float box 矩阵和 PBC。势参数及 cutoff/skin
在一个 NEP 实例中不可变；type/gid 的 slot 身份在 layout epoch 内不可变。变更任何一项必须
重建原 局部域分解cache/eligibility 或创建新实例，不能只替换 pointer 或绕过 runtime 的全局判定。
缓存不是靠地址、capacity 或 shape 判断身份。

重叠路径先准备分区，再发出边界交换。确认复用时，`Neighbor` 的非空中心分支无需读取坐标。分类只用完整 V 和 gid。负责原子为空时，邻居参考数据延至交换完成后更新。

缓存未命中时，发起交换前完成 GPU 分类、D2H 等待和掩码分配。命中时只检查缓存键。初始化、重建与迁移仍走同步完整计算，下一次普通步重新分类。本次求力所用数据冻结后，工作区保持原样。

只有全局 confirmed_reuse 且连续位移平方 ≤ skin²/4 才能复用；阈值等号与原代码一致。
不从当前 R/A、陈旧 ghost 坐标或 geometric owner 扩大 I。小位移跨 slab/周期面时 manager
不变，mask 可复用；超过阈值的迁移或无迁移 rebuild 当步走 full，并使旧 mask 失效。
段首 full、workspace resize/invalidation 同样失效；新 V 与 key 就绪后才重新分类。
同 shape 的 gid 重排必须增加 epoch，同 epoch 强制重建必须增加 Verlet generation。
同一 Verlet generation 下 box 改变会在消费前拒绝，不能重分类旧 V 后继续运行。
key 不同先撤销 valid 再生成 mask；生成失败不发布部分结果。无证明的输入/模型不沿用旧 mask。

## 诊断与计时

显式详细诊断或 verify 输出 `DMGMD_PARTITION rank=... step=... call=... mode=split|full`
及 reason、epoch/generation、classified、I/E/O/D/L、classify_host_seconds、oracle/poison。
`DMGMD_PARTITION_SUMMARY` 为每 rank/段累计 split/full/classification 次数，关闭详细日志也保留；
`DMGMD_PARTITION_CONFIG` 是程序实际解析的模式；全量复制另记录 `reason=m1-fallback`。
O8.5 非空调用的每步详细记录实际 schedule 和六类 kernel 的 launch 次数；空 local
以 `full/empty-local` 记录，当前调用不启动 kernel，累计 launch 数保持不变。summary 记录累计 producer
调用和各类实际 dispatch 次数，排除 verify replay。分类覆盖与 oracle 验证中心唯一性，
独立 CUDA activity 验证 launch/order/grid/default-stream；fallback 不计 producer 调用。
verify 逐 bit 核对 GPU mask 与独立 CPU 图 oracle，覆盖/交集、gid/反边可用性、有效 R/A 行、
Fp/sums/partial、逐原子 PE/force/全部 9 virial，并检查 ghost 最终量与 full 一致。
serial verify 在消费者之前检查过滤后的 count/slot/Fp producer 与反边；缺失反边安全拒绝。
O4 verify 在 prepare 检查完整 V 的 producer/反边闭包，early 的有效行/中间量在 finish 后
从安全快照比较；late 仍在消费前检查有效反边，不在异步 early 中插入 D2H 等待。
serial verify 恢复同一零输出快照；overlap verify 在 prepare 时 poison scratch、准备 ghost
为 NaN 的独立 position 副本，实际 early 在请求在途时运行。finish 证明 early 物理完成后，
安全快照保存 early 输出/中间量，执行 full replay，恢复 early 后再运行 late。replay 无
额外 MPI/I/O/计数，不并发修改生产 early 数据；有效 early/完整 split 与 full 逐 bit 对比，
无效 row 尾部不比较。

开关 off 不分配 mask、不生成分区 CUDA event 或诊断；原 full 的 timing marker 语义保留。
split 的旧 `cell_neighbor_device` 区间包含缓存分类；旧 `nep_device` 是两段 filter/products
的 **split pipeline 包围区间**，可能包含周期 neighbor host/MPI 空隙，不能当纯 NEP kernel
时间或 T_interior/T_boundary。verify 的分类区间还包含 oracle replay，不能用于性能结论。
`classify_host_seconds` 是 steady_clock 墙钟；serial cache miss 含 GPU 分类、D2H 完成及
同 stream 前序 unpack/clear 的潜在等待。O4 分类在 begin 前，不包含本步 halo 等待；cache
hit 只是 key 检查。原 split/O8.5 分类占用三个 L 长 int mask（S/I/E）及三个 int scratch，逻辑额外设备空间为 12L+12 bytes，
物理 capacity 可保留高水位；cache miss 使用三次分类 kernel、mask/scratch fill 和两次小型 D2H 检查。
分区额外成本由同输入、固定 CPU/GPU 的 off/serial 无详细诊断 A/B 另测。

逐 kernel 归因须比较同输入、绑定、工具链的稳态 full/serial/wait 实际 CUDA activity；
段首 setup、重建 fallback 和 ordinary split 分开。原 `schedule=split` 对上述五类基础
pipeline 各执行 early/late 一次，grid 仍为完整 owned/dependency 范围，用 mask 选择
中心，不能用中心数比例代替有效线程或访存指标。独立进程是重复单位，步与 rank
只作 trial 内统计。额外 launch API、分类、同步和 MPI 各自报告，不能与 GPU kernel
时间相加来拼出整步墙钟。Nsight Compute 须记录 replay/cache/clock control；计数器
不可用时微架构归因为 INCONCLUSIVE，不据寄存器数或阶段包络推断 cache/occupancy 瓶颈。
采集、逐步匹配和离线重算入口见[benchmark 工具](../../tests/benchmark/README.md#诊断入口)。

### O6.5 隔离快照重放

`DMGMD_ENABLE_O65_DIAGNOSTICS=ON` 是独立构建选项，默认为 OFF。仅该构建包含
`DMGMD_O65_CAPTURE_CALL`、`DMGMD_O65_CAPTURE_ROOT` 和 `dmgmd-o65-replay`。
快照在指定 ordinary call 的完整 force 后等待默认 stream 完成，独立生成分析 mask，
保存 gid/slot、O/D/L、layout/Verlet/workspace generation、坐标、完整实际 filtered rows、
Fp/sums 和有效 partial 的原始数组；不修改生产 mask、粒子顺序或 MPI 日程。

独立单 GPU 进程从同一已完成 halo、producer 全就绪的快照重放 radial 或 angular partial：
A 使用原完整 grid/null selection，B 使用全选 mask/完整 grid，C 使用真实互补 mask/两次完整
grid，D 使用相同互补中心的升序索引与对应 grid。D 只改变 thread→center 映射，每个中心仍
写原槽位，选择由索引表达（`selection=null`），保持 L stride、邻居行与每中心表达式/
累加顺序。当前最终 consumer 验证只支持
O6.5 已验收的两个无 ZBL fixture；ZBL 等未支持的 consumer 明确拒绝，其他模型未验收，
不扩大生产兼容性承诺。

每次重放前在 event 外恢复同一输出初值；event 只包围目标 kernel launch，consumer 验证、
输出恢复与索引构建均在计时外。A–D 比较目标输出、有效 partial、PE、最终 force/全部九个
virial，并确认 Fp/sums 未改写。索引下载/CPU 构建/分配/上传、generation/shape/box 失效
检查、设备内存与 key 查找另记。重放是 producer 缓存上下文不同的短诊断，不能代替实际
整步净收益；D 的差值不单独归给空 block、occupancy 或缓存。

普通步 trace 通过实际 `inspect_domain_cache` 到本步第二半 Verlet 匹配，每步五类
kernel 必须完整有序；不依赖 O8 后已不成立的逐步 thermo delimiter。setup/rebuild 单列。
warp/block 分析以 32/64 个真实硬件 lane 为组，尾部 padding 为 inactive；静态非空/全空/
混合覆盖与邻居边数不等于 achieved occupancy 或内存事务。

## 必须保留的反例

| 场景 | 分类 / 调度要求 |
| --- | --- |
| i–j 为 owned、j–k 接 ghost | i 的直接邻居全 owned 仍不足，必须排除两跳未闭合的 i |
| 当前 cutoff 外、Verlet skin 内 ghost | filter 先读坐标再拒绝，不能按当前 R/A 误判 interior |
| 非空 E\I | 辅助 descriptor/partial 提前生成，late 不重复 PE；最终 force 仍在 boundary |
| 周期链、P2 同 peer | 按 gid/MIC/原 plan，不按裸坐标或把 image shift 再加一次 |
| 同 shape 换 gid / 同 epoch 新 V | 分别用 layout epoch / Verlet generation 失效，不能按地址/capacity 复用 |
| filtered angular ordinal 变化 | 每步按 gid 查反边，不缓存旧 partial ordinal |

## 测试入口

`dmgmd.domain_partition` 穷举小图的独立两跳闭包、辅助生产/唯一性及 key/ticket；
`dmgmd.domain_neighbor_cuda` 对 GPU 分类与 CPU oracle 逐 bit 比较，实际触发 key 失效、
非法 count 拒绝和空域，并注入过期 halo token、未重建的 box 变化与缺失 angular 反边，验证消费前拒绝。GPU 不可访问时 skip 不构成验收通过。

`tests/mpi/run_mpi_domain.py --partition-modes off,serial,verify --work-root PATH` 对每个
rank/backend 执行同输入三模式，要求科学输出逐字节一致，同时保留原 P1 oracle 的 manifest
容差和逐字段通信记账。新增专用辅助/周期链、skin ghost 进入 cutoff、无迁移 rebuild/多 run，
并让 NEP5/typewise/flexible/typewise ZBL 实际产生非空 split，禁止全 fallback 冒充通过。
P1/全量复制关闭路径、空 rank/interior、迁移/restart/输出及 call 1000 继续受检。
`--cases water,zbl` 可只筛选主矩阵；共同 P1/oracle、fallback 和 timing 门仍执行。
`--schedule producer` 显式覆盖 O8.5，要求每个实际 split 的五类 launch 为 2/2/1/1/1，
不能复用旧完整 split 的通过声明。`run_long_nve.py --schedule producer --partition-mode
serial --overlap-mode wait` 在主矩阵、回放与跨 rank restart 中使用相同实验选择；配置与
stage checkpoint 保存 schedule，禁止靠父 shell export 隐式选择。
各 runner 显式传递并保存实验模式；不得依赖父 shell export。修改 compat 必须重验 baseline
及 long_nve，容差与 golden 仍以原 manifest 为准。

## O4 诊断与调度成本

提交第一批计算后，`wait` 直接调用一次 `finish`。`poll` 先调用至多 8 次 `progress`。观察到槽位可复用，或发送与接收均已结束时，提前退出查询，随后仍调用 `finish`。两者都在 MPI 初始化线程执行，且不会忙等 CUDA。

`DMGMD_OVERLAP_CONFIG` 记录实际模式。`DMGMD_OVERLAP_SUMMARY` 每段累计第一批计算实际执行次数、查询次数、`finish` 墙钟和当前线程 CPU 纳秒。`finish` 含剩余通信、可见性同步、第一批计算和解包等待。应按调用成本解释。`nep_device` 覆盖发起交换、边界收发以及两批计算，和旧 halo 区间相交。`halo_transfer_wait` 也可能含第一批计算和可见性等待。

`cumulative_calls` 只计真实 early；finish wall/CPU 则覆盖 overlap 请求模式下所有普通步
的 halo 回调，包括空 interior/local 等 full fallback。poll 的最多 8 次上限是**每次
普通 halo 回调**，不是每次 early。因而 full fallback 可以有非零 progress/finish 成本，
不构成 GPU overlap 证据；平均回调成本必须用普通 halo 回调数作分母。

`DMGMD_OVERLAP_TRACE=1` 独立于旧 O1 诊断，记录 rank/step/call/key/halo generation、
owned-ready、pack-ready、post 起止、early/late enqueue、独立收发观察、unpack/force 物理
完成的 host 观察，另给 early/late CUDA event elapsed。host 为 CLOCK_MONOTONIC，CUDA
event 没有绝对 host 时间校准；elapsed 可含 kernel 间空隙，不能拼接成精确 GPU 时间线。
详细诊断会额外等待 force event，只用于证据。精确 kernel 区间以同一 Nsight trace 的 CUDA
activity 为准；请求 post 到完成观察的区间仅表示 runtime 的在途状态，不代表物理链路传输。

新增矩阵模式 `overlap-wait,overlap-poll,verify-wait,verify-poll` 显式映射为 partition 与
overlap 两个开关，检查 CONFIG、真实 split/progress 与科学字节。独立
`tests/mpi/run_overlap_schedule.py` 延迟 transport receive post，直到 early CUDA event 完成，
验证 pending request、poll 上限与单边错误退出；该受控证据只证明调度/状态合同。

### O9 独立确认工具合同

`tests/benchmark/run_overlap_o9.py` 默认进入独立确认协议，
`--legacy-probe` 只用于重放首轮权限探测，不代表执行候选验收。
确认前冻结候选选择、全部对照、源码/输入/二进制身份、资源和随机配对顺序；
先前探索数据只用于选定候选，不能计入新确认样本。相同已验收二进制可引用经原始
清单校验的 MPI/nightly 门槛，必须明确复用范围；新的二进制必须重新过受影响的门槛。

完成判定要求实际新配对实验、正确性门槛和独立 marker CPU 诊断齐全。
主性能关闭详细诊断，正式 CPU 为 null。空 early 或重建的 full fallback 单独记录，
不能算实际 overlap 证据。确认程序按冻结的多重比较规则判定范围，其他候选的新结果
仍为探索；若据此重新选择候选，需另采独立确认样本。性能结果不能自动改变默认设置。


### O11 限定范围采用复验工具合同

`tests/benchmark/run_overlap_o11.py` 在新的独立目录冻结预选候选、scope/缺项、
O8/full 与候选 full、serial/wait/poll 及路线控制，旧数据只作候选选择和验收证据。
缩小原始全矩阵时须在采样前记录理由与不能外推的范围。
主计时关闭详细诊断，CPU/维护诊断独立；所有正式尝试和慢样本均保留。
采用须同时通过同后端与默认 full 的冻结多重比较门槛，并通过独立 O8/full 桥接。
探索范围、poll 或新候选不能在同一批数据中选择并确认。

冻结协议只允许一次额外的全局配对块组。执行后若区间仍宽，报告剩余不确定性，
不反复追加；`extension_required` 是区间宽度标志，已有完整 extension 时不代表
尚有追加待执行。缺必要配置/门槛/诊断判为 PARTIAL；完整但无合格范围为
NO_ADOPTION，有合格范围为 SCOPED_MANUAL_USE，均不改变生产默认。

`recompute_overlap_o11.py` 校验封存 SHA-256，再仅使用封存源码离线复算，
复用封存 reference 最终状态。相同已验收二进制可复用经原始清单核验的完整门槛，
须明确本轮新执行与历史复用，不把复用描述为重跑。采用建议只针对报告中的具体
硬件、输入、后端、绑定与失败回退条件。
