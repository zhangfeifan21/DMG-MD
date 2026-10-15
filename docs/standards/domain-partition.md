# M2a 普通步串行 NEP 分区

类别：现行实验接口合同。默认关闭；O2 只改变中心调度。halo 在 O3 的 begin 后立即
finish（Waitall + unpack event 完成），unpack 与全部计算依次排入同一默认 stream，没有通信/计算 overlap。
方向由维护者于 2026-10-15 在 O2 会话明确确认；证明与反例来源为
[O0](../plans/communication-computation-overlap.md#o0-证据闭包与串行分区设计2026-10-08)。
本轮实测与验证范围见 [O2 状态](../status/overlap-partition-20261015.md)。

## 选择与生命周期

`DMGMD_DOMAIN_PARTITION=off|serial|verify`，默认 `off`；非法值或各 rank 配置不一致拒绝。
`serial` 仅在 M2a 普通步、全局 confirmed_reuse、本步 halo 已完成通信且 unpack 对默认
stream 后续计算有序可见时生效。P=1/M1、段首 force、rebuild/迁移、空 interior/空 rank、
未证明模型走原 full 同步路径。`verify` 使用相同分区，另执行同快照未分区 oracle 和 poison
诊断，不能用于性能测量。支持范围为现有普通 NEP4/5、NEP-ZBL，`model_type=0`、无 B projection。

`NEP::compute_domain` 保留未选择的完整 M2a 路径；可选 `DomainForceContext` 传入模式、
ordinary 标志、本步编号和 halo_ready_step。runtime 在 `refresh_ghost_positions` 返回后
发布本步 ready token；这是 MPI completion + O3 unpack event 物理完成的证明，不是单独用
`refreshed_epoch` 推断本步完成。O2 的 `DomainForceTicket` 约束 early/late 恰一次及顺序，
仅存在于一次 compute 内；NEP force ticket 不提供异步计算接口；通信接口见 [halo 生命周期](./halo-lifecycle.md)。

owned 为 O=[0,owned)，dependency 为 D=[0,owned+dep)，L=local_count。owned=0 时 D 为空。
V 是未按 typewise cutoff 过滤、含 skin 带的实际完整 Verlet 行：

```
S = {c in O | V(c) subset O}
I = {i in S | V(i) subset S}       B = O \ I
E = union({i} union V(i), i in I)  H = D \ E
```

分类只读 V/global ID，不读 ghost 坐标。GPU 先检查 count/capacity、slot 范围、自边和 gid
严格行序，再生成 mask；非法结构失败退出，不回退到仍会越界的 full kernel。
E 是 I 的辅助 producer 并集，属于 O；E\I 可以是最终 boundary 中心。

## 读写、布局与唯一性

保持全部原槽位/global ID、原 grid/block、每行顺序及每线程数学/累加顺序。mask guard 位于
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

逻辑 call counter、1000-call neighbor 记录和两个 MPI_MAX 跨 full/split 共用；verify replay
不增加 force call、neighbor build、MPI 或 I/O。不同 rank 可以各自 full/split，但 sink 次数
与顺序一致，包括空 rank。通信 tags、半径、消息与应用字节合同保持原样。

## 缓存有效性

`DomainPartitionKey` 包含 layout epoch（runtime 同时验证 mapping_epoch 相等）、独立 Verlet
generation、workspace generation、O/D/L、CPU/float box 矩阵和 PBC。势参数及 cutoff/skin
在一个 NEP 实例中不可变；type/gid 的 slot 身份在 layout epoch 内不可变。变更任何一项必须
重建原 M2a cache/eligibility 或创建新实例，不能只替换 pointer 或绕过 runtime 的全局判定。
缓存不是靠地址、capacity 或 shape 判断身份。

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
`DMGMD_PARTITION_CONFIG` 是程序实际解析的模式；M1 另记录 `reason=m1-fallback`。
verify 逐 bit 核对 GPU mask 与独立 CPU 图 oracle，覆盖/交集、gid/反边可用性、有效 R/A 行、
Fp/sums/partial、逐原子 PE/force/全部 9 virial，并检查 ghost 最终量与 full 一致。
过滤后的 count/slot/Fp producer 与反边在消费者之前检查；缺失反边安全拒绝。
它恢复同一零输出快照、poison 全部中间 scratch，并让 early 使用 ghost 坐标为 NaN 的独立
position 副本；early 和 complete split 都必须与 full 一致。无效 row 尾部不比较。

开关 off 不分配 mask、不生成分区 CUDA event 或诊断；原 full 的 timing marker 语义保留。
split 的旧 `cell_neighbor_device` 区间包含缓存分类；旧 `nep_device` 是两段 filter/products
的 **split pipeline 包围区间**，可能包含周期 neighbor host/MPI 空隙，不能当纯 NEP kernel
时间或 T_interior/T_boundary。verify 的分类区间还包含 oracle replay，不能用于性能结论。
`classify_host_seconds` 是 steady_clock 墙钟，cache miss 含 GPU 分类、D2H 完成及同 stream 前序 unpack/clear 的潜在等待，cache
hit 只是 key 检查。分类占用三个 L 长 int mask（S/I/E）及三个 int scratch，逻辑额外设备空间为 12L+12 bytes，
物理 capacity 可保留高水位；cache miss 使用三次分类 kernel、mask/scratch fill 和两次小型 D2H 检查。
分区额外成本由同输入、固定 CPU/GPU 的 off/serial 无详细诊断 A/B 另测。

## 测试入口

`dmgmd.domain_partition` 穷举小图的独立两跳闭包、辅助生产/唯一性及 key/ticket；
`dmgmd.domain_neighbor_cuda` 对 GPU 分类与 CPU oracle 逐 bit 比较，实际触发 key 失效、
非法 count 拒绝和空域，并注入过期 halo token、未重建的 box 变化与缺失 angular 反边，验证消费前拒绝。GPU 不可访问时 skip 不构成验收通过。

`tests/mpi/run_mpi_domain.py --partition-modes off,serial,verify --work-root PATH` 对每个
rank/backend 执行同输入三模式，要求科学输出逐字节一致，同时保留原 P1 oracle 的 manifest
容差和逐字段通信记账。新增专用辅助/周期链、skin ghost 进入 cutoff、无迁移 rebuild/多 run，
并让 NEP5/typewise/flexible/typewise ZBL 实际产生非空 split，禁止全 fallback 冒充通过。
P1/M1 关闭路径、空 rank/interior、迁移/restart/输出及 call 1000 继续受检。
`--cases water,zbl` 可只筛选主矩阵；共同 P1/oracle、fallback 和 timing 门仍执行。
各 runner 显式传递并保存实验模式；不得依赖父 shell export。修改 compat 必须重验 baseline
及 long_nve，容差与 golden 仍以原 manifest 为准。
