# 局部域分解缓存有效性与统一重建

类别：现行标准及充分条件证明。核对日期：2026-10-23。

固定盒局部域分解在普通步保留原子槽位、边界成员和 Verlet 邻居表，仅刷新边界副本的位置。每个 rank 检查本地原子的累计位移，全体 rank 共同决定何时重建。变盒时有额外约束，适用于默认关闭的局部 NPT 实验路径。[变盒与参考盒度量](domain-npt.md) · [局部 NPT 验证](../status/validation/domain-npt-validation.md)

`src/domain_runtime.cu` 的 `DomainState` 保存状态。`inspect_domain_cache` 检查位移，`run_domain_segment` 汇总判定。`Neighbor` 按已经确定的复用或重建动作执行。[每步顺序与计时](mpi-runtime.md) · [数据布局](data-layout.md)

## 身份与失效

两次重建之间称为一个缓存周期（cache epoch）。每个原子的管理归属（manager owner，`manager_owner`）在整个周期内负责积分、归约热力学量和提供输出记录。当前位置所在的条带给出几何归属（geometric owner，`geometric_owner`）。原子稍微跨过条带或周期面时，原 rank 仍负责积分。到下一次重建才使用几何归属确定迁移目的地。

| 对象 | 复用条件 | 失效条件 |
| --- | --- | --- |
| manager / local slots | ID 唯一覆盖，owned/ghost key、顺序和 L stride 固定 | owner/membership/type/box/decomposition 改变 |
| membership / face map | 参考 slab、半径、source/peer/收发槽位固定，全局位移界成立 | epoch/key/count/route 改变；损坏为 fatal |
| Verlet rows / reference | 同布局、身份、box、cutoff/skin，位移界成立 | 首次、位移超限、显式失效或布局变化；overflow 不发布 reference |
| descriptor/Fp/partial/force | 仅本次 force call 有效 | 每步重算，不跨步复用 |
| device views | pointer 与 logical stride 有效 | resize/扩容后重新取 view；capacity 不是版本 |

原子迁移后，本地槽位可以改变，原子全局 ID 保持稳定。通信映射和布局必须属于同一次重建。每一步还要确认本步收到的位置已写入 ghost 槽位。通信接口（O3）记录解包完成，重叠调度（O4）使用 `owned-ready`、`halo-ready` 和求力票据分别约束不同阶段能读的数据。[通信生命周期](halo-lifecycle.md) · [分区与重叠](domain-partition.md)

## 连续位移与两跳闭包

位移要沿真实运动路径累计，才能记录原子跨过周期边界的距离。令 q 为缓存周期的参考位置，Δ 为累计位移，`δ=max|Δ|`。每个原子只用管理归属上的副本参与计算。VV1 在折回坐标前累计位移，重建提交后清零。用两个折回盒内的坐标取一次最小镜像（MIC）会丢失部分历史。

固定盒普通步要求 `δ≤skin/2`，恰好等于阈值时仍复用。若位移非有限、无法证明连续性，或单步越界超出一次折回的能力，全体 rank 在交换边界和求力前报错。

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

上述公式给两跳路径的每条边分别取保守上界。当前每个全局 ID 只有一个副本槽位。若要用更紧的端点界缩小边界范围，还需证明它能覆盖跨最小镜像的路径。半径上界按径向表先筛选、角向表和 ZBL 的实际读入路径确定，并计入 float 类型对平均的舍入。无法证明上界时，当前分派回退到全量复制模式。

重建时，程序用参考位置为左右两面选出要发送的副本，局部域分解要求 `slab_width≥d_coord`。普通步只更新已有成员的坐标。P2 两面可能面对同一 rank，沿现有计划去重。安全复用还要求满足位移界、管理归属和缓存键保持一致，以及每步完成边界刷新。

## 全 rank 决议与提交

速度 Verlet 的第一半步更新位置。位置折回盒内后，各 rank 检查负责原子的位移与本地失效原因。对有限非负的局部最大位移平方做
`m_rank > 0.25*skin*skin` 的严格比较，将超限位与 routing/continuity-invalid 位合并，
只经一次 uint32 reason OR 得到一致 action；不再对缓存位移做 double MAX。
空 owned rank 贡献零，仍参加 OR；等号复用。非有限平方不进入局部最大值，只置
invalid 位，OR 后优先 fatal，再判断 rebuild。未决议禁止 halo/NEP，Neighbor 不再做
第二次位移检查。有限值的 `max(m_rank)>h ⇔ OR(m_rank>h)` 保证各进程得到同一决议。

判定复用时，沿已保存的映射刷新 ghost 坐标。负责 rank、槽位、ghost 成员及 Verlet 邻居表保持原样。

判定重建时，先下载动力学字段。如有原子迁移，用 `Alltoall` 交换计数，再用 `Alltoallv` 直接把原子记录发给最终的几何归属 rank。记录包含 gid、类型、质量、电荷、组、折回盒内的位置、半步速度和可选的展开坐标（unwrapped）。旧力、势能（PE）和 virial 不随迁移发送。

即使没有原子迁移，也要重建边界成员、布局、通信映射和邻居表。新数据通过校验后才上传。各 rank 清零累计位移，统一发布重建版本（epoch）。校验失败时终止，避免后续使用只完成了一部分的缓存。

连续 run 保留 epoch/rows，段首兼容 force 仍执行，不因段界强制重建。restart 不保存缓存，
新进程按输入行号和新 rank 数 bootstrap。

## 验证边界

CPU layout、CUDA neighbor 与 MPI domain runner 覆盖阈值、micro-crossings、无迁移 rebuild、
多 slab/PBC、空 rank/N<P、多 run/restart、timing A/B 科学输出与精确通信量。
O2 另用 Verlet/workspace generation 防同 epoch 新 rows、同 shape 新身份误复用。
全表 debug 每步不变量断言未实现；剩余边界见[风险登记](../plans/risk-and-backlog.md)。

重叠求力从准备邻居图到结束本次求力期间冻结所用数据。缓存未命中时，掩码分配、分类和 D2H 都在发起交换前完成。本地原子就绪（`owned-ready`）只授权第一批 E/I 计算。边界交换版本与步号和缓存键匹配，且解包完成后，才可计算 H/B。此期间拒绝工作区 resize 或重新进入求力。

普通步始终用含 skin 的完整旧 Verlet 邻接图和连续位移判据证明内部中心的依赖闭合。上一步的边界坐标不能用来重新分类。
