# M2a 缓存有效性与统一重建

类别：现行标准及充分条件证明。核对日期：2026-10-16。

实现入口：`src/domain_runtime.cu` 的 `DomainState`、`inspect_domain_cache` 与
`run_domain_segment`。Neighbor 接受已决议的 reuse/rebuild action。每步协议/计时见
[MPI runtime](mpi-runtime.md)，布局见[data-layout](data-layout.md)，实施实测见
[第二阶段报告](../status/performance/stage2-cost-report.md)。

## 身份与失效

`manager_owner` 为 epoch 内唯一积分、thermo 与输出 owner；`geometric_owner` 由当前位置
决定，只用于下一次 rebuild 路由。微小跨 slab/周期面不立即迁移。

| 对象 | 复用条件 | 失效条件 |
| --- | --- | --- |
| manager / local slots | ID 唯一覆盖，owned/ghost key、顺序和 L stride 固定 | owner/membership/type/box/decomposition 改变 |
| membership / face map | 参考 slab、半径、source/peer/收发槽位固定，全局位移界成立 | epoch/key/count/route 改变；损坏为 fatal |
| Verlet rows / reference | 同布局、身份、box、cutoff/skin，位移界成立 | 首次、位移超限、显式失效或布局变化；overflow 不发布 reference |
| descriptor/Fp/partial/force | 仅本次 force call 有效 | 每步重算，不跨步复用 |
| device views | pointer 与 logical stride 有效 | resize/扩容后重新取 view；capacity 不是版本 |

local slot 不是持久身份。map/layout epoch 必须匹配；同 epoch 的不同时间步还需本步 halo
完成证明。O3 物理 unpack 和 O2 step token 见[halo](halo-lifecycle.md)及[分区](domain-partition.md)。

## 连续位移与两跳闭包

令 q 为 epoch 参考位置，Δ 为沿真实积分/wrap 历史累计的位移，δ=max|Δ|，只检查唯一
manager copy。VV1 在 wrap 前累计，rebuild 提交后清零；不得用两个 wrapped 坐标的一次 MIC
差代替。普通步要求 δ≤skin/2，等号复用；非有限、连续性无法证明或超出现行单次 wrap
能力的单步位移在 halo/force 前全 rank 报错。

对当前作用边 i–j：

```text
|q_j-q_i| <= |x_j-x_i| + |Δ_i| + |Δ_j| < R_force(i,j) + 2δ
```

以 R_global+skin 建立的 Verlet 行因此包含当前 cutoff 内所有边；每步 typewise filter 去掉
旧边。推导要求 identity/image 与参考行一致；浮点临界行为仍由原 kernel/golden 锁定。

owned i 读取 j 的 Fp/反向 partial，j 的 descriptor 又读取 k。epoch 建立时 i 位于其 manager
参考 slab，逐边三角界给出：

```text
distance(q_j, slab_i) <= R_force(i,j) + 2δ
distance(q_k, slab_i) <= R_force(i,j) + R_dep(j,k) + 4δ
d_dep   = max R_force + skin
d_coord = max_(ti,tj,tk)(R_force(ti,tj) + R_dep(tj,tk)) + 2*skin
```

这是保守逐边充分界。跨 MIC image 的更紧端点界尚无当前单-slot ghost 表示下的证明，不能
据此缩 halo。半径须按 radial-first/angular/ZBL 实际消费路径及 float pair-average 舍入取上界，
无法证明时 dispatcher 回退 M1。

rebuild 用参考位置建立左右 send members，eligibility 要求 slab_width≥d_coord。
普通步刷新固定成员，不重新 band-test；P2 同 peer 按现有 plan 去重。位移界、manager/key
不变和每步刷新共同保证安全，单有半径公式不够。

## 全 rank 决议与提交

VV1/wrap 后检查 owned 位移与 reason，经全局 MAX 和 reason OR 得到一致 action。
未决议禁止 halo/NEP，Neighbor 不再做第二次位移检查。

- reuse：manager/layout/membership/map/Verlet 固定，只刷新 ghost position。
- rebuild：下载 dynamics，direct Alltoall count/Alltoallv 路由至最终 geometric owner；
  无 owner 变化时仍重建 membership/layout/map/neighbor。校验后上传、重置位移并一致发布
  epoch，失败不消费半提交缓存。
- 迁移含 gid、type、mass、charge、group、wrapped position、half-step velocity 和可选 unwrapped，
  不传旧 force/PE/virial。

连续 run 保留 epoch/rows，段首兼容 force 仍执行，不因段界强制重建。restart 不保存缓存，
新进程按输入行号和新 rank 数 bootstrap。

## 验证边界

CPU layout、CUDA neighbor 与 MPI domain runner 覆盖阈值、micro-crossings、无迁移 rebuild、
多 slab/PBC、空 rank/N<P、多 run/restart、timing A/B 科学输出与精确通信量。
O2 另用 Verlet/workspace generation 防同 epoch 新 rows、同 shape 新身份误复用。
全表 debug 每步不变量断言未实现；剩余边界见[风险登记](../plans/risk-and-backlog.md)。
