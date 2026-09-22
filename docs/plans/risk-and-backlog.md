# DMG-MD 风险与待办登记

类别：待实施计划。状态：IN PROGRESS（持续登记）。本文只登记仍影响未来实现的风险和待决事项。

## 1. 风险评级

参考 GPUMD commit：`9d23496e41319b9e2af5221a7df6285387401d1e`。

- **P0**：会产生物理错误、越界、死锁或重复/遗漏贡献，且普通小测试可能不暴露。
- **P1**：会破坏兼容性、可重复性、restart或扩展性。
- **P2**：性能/工程风险，通常不直接改变物理结果。

## 2. 最危险的五项

### R1 — NEP 是两跳数据依赖，不是单一 cutoff halo（P0）

证据：

- `find_force_radial` 读取 `Fp[n1]` 和 `Fp[n2]`：`src/force/nep.cu:727-728`。
- `gpu_find_force_many_body` 从 neighbor row 查找并读取反向 `f12(n2,n1)`：`src/force/potential.cu:209-252`。
- `Fp(n2)`、`sum_fxyz(n2)` 和反向 partial 又依赖 `n2` cutoff 内的第三原子。
- `NEP_MULTIGPU` 用 `rc/2` cell，坐标域向 owned 两侧扩4 cells，descriptor/partial域扩2 cells (`src/force/nep_multigpu.cu:1487-1543,1591-1728`)。

失败模式：只交换 `rc` 坐标 halo 时，分区边界 owned atom的邻居 descriptor或反向 partial缺失；力可能看似连续但系统性错误。把 `rc+skin` 当完整答案也不成立。

控制：M2a 先实现保守两跳 position halo 作为生产路径和后续 oracle；坐标成员在邻居重建间隔内缓存时，深度按 `max(R_force + R_dep) + 2*skin` 的实际 typewise kernel 消费路径计算。M2b 再实现一跳 position + `Fp` + reverse-partial 分阶段交换，只有与 M2a 逐原子 force/virial 等价后才可替换。

### R2 — 全局 `N`、local stride 和 owned/ghost 权限混用（P0）

证据：

- `Atom::number_of_atoms` 与所有 3N/9N arrays：`src/model/atom.cuh:21-49`。
- NEP ELL/`Fp`/virial的地址均为 `component_or_slot*N+atom`。
- VV用 `mass.size()` launch所有原子：`src/integrate/ensemble.cu:297-398`。
- thermo用 `mass.size()`累加所有原子：`ensemble.cu:636-672`。

失败模式：简单把 `N` 替换为 `local_count` 会积分ghost并重复计算thermo；替换为 `owned_count` 又会令neighbor index无法寻址ghost，或SoA分量错位。capacity变化时旧view/stride也可能悬空。

控制：类型层面分开 `global_count/owned_count/local_count/atom_stride/center_domain`；kernel接口不接收含混的单一 N。force/PE/virial只承认owned前缀有效，所有 reduction/assert检查ghost不参与。

### R3 — 迁移后的稳定身份、周期 image 与反向边匹配（P0）

证据：

- GPUMD没有global ID，input行号只隐含于数组顺序。
- group contents保存数组下标：`src/model/group.cu:25-72`。
- many-body gather假定反向row存在且按原子整数下标排序。
- small-box的同一原子多个周期image共用同一个 `NL` atom index，只靠独立r12区分：`src/force/nep_small_box.cuh:86-123`。

失败模式：原子换rank后local index改变，group/restart/output顺序漂移；directed partial发给错误image；正好跨周期边界的原子重复或丢失；neighbor cache引用失效。

控制：读取时赋不可变64-bit `global_id`；所有迁移携带ID、image/unwrapped状态和group labels。通信边键至少包含 `(center_global_id, neighbor_global_id, periodic_image)`。local reorder后重建neighbor/edge map，输出按global ID排序。

### R4 — 全局归约、温控和随机数随 rank 数变化（P0/P1）

证据：

- thermo kernel把全N在固定GPU树中求和 (`src/integrate/ensemble.cu:434-633`)。
- `nvt_ber` 在 `find_thermo()` 后立即按瞬时全局温度缩放 (`src/integrate/ensemble_ber.cu:195-233`)。
- `Velocity::initialize()` 的随机速度使用CPU `rand()`并以数组下标偏移种子，随后做完整体系CPU动量/角动量修正 (`src/model/velocity.cu:55-308`)。

失败模式：每rank各自温控会得到不同缩放因子；ghost重复PE/virial；MPI求和树改变低位并导致混沌轨迹分叉；随机速度随分区/rank数改变；一个rank先进入collective而另一个rank错误退出造成hang。

控制：local-owned累加后统一all-reduce，再更新thermostat；RNG以 `(user_seed, global_id, component, epoch)` counter-based映射。明确bitwise不是跨rank数默认承诺；为调试提供固定归约顺序或高精度host oracle。所有解析/运行错误先全rank协调再abort。

### R5 — 每 rank 设备所有权会被当前 `NEP_MULTIGPU` 破坏（P0）

证据：

- `Force::parse_potential()` 调 `gpuGetDeviceCount()`；只要可见GPU数>1便实例化 `NEP_MULTIGPU` (`src/force/force.cu:139-160`)。
- `NEP_MULTIGPU::compute()` 自己循环 `gpuSetDevice(gpu)`，GPU0持有全局输入/输出并分发到所有GPU (`src/force/nep_multigpu.cu:1416-1798`)。

失败模式：在一节点启动多个MPI rank且每rank看见所有GPU时，每个rank都尝试占用全部GPU、复制完整系统、竞争内存/stream，甚至结果错误。仅靠用户正确设置 `CUDA_VISIBLE_DEVICES` 太脆弱。

控制：新runtime完全不走 `NEP_MULTIGPU`；启动时根据local rank显式选择一张设备并校验唯一绑定。NEP core构造器接收已选择device/context，不能再次枚举设备决定算法。

当前状态：replicated prototype 已直接构造 ordinary `NEP`，以 shared-communicator local rank
选择 device，并 Allgather CUDA UUID 检查本节点唯一绑定。启动记录明确写出该策略。

## 3. 完整风险矩阵

| ID | 等级 | 风险 | 代码证据 | 失败症状 | 验证/缓解 |
| --- | --- | --- | --- | --- | --- |
| R1 | P0（M2a 已关闭验证路径） | NEP多层halo | `nep.cu:727-728`; `potential.cu:209-252` | 边界力错但内部原子正确 | 已实施：d_coord = max(R_force+R_dep)+2*skin，三原子两跳链 fixture 与 P=1 oracle 逐字节一致（2026-09-18）；M2b 对比待做 |
| R2 | P0（M2a 已关闭验证路径） | global/local N和SoA stride混用 | `atom.cuh`; ELL地址 `slot*N+n` | 越界、分量串线、ghost被积分 | 已实施：logical local_count 与 allocation capacity 分离、domain 接口显式计数、真正 local_count=0 的 1000-step P=4 fixture + ghost 不进积分/thermo/输出断言（2026-09-18） |
| R3 | P0 | 无stable global ID | `read_xyz.cu`, `group.cu` | 输出重排、迁移后group错 | input row ID；跨rank迁移/输出排序测试 |
| R4 | P0/P1 | thermo/thermostat/RNG rank依赖 | `ensemble.cu`, `ensemble_ber.cu`, `velocity.cu` | NVT各rank温度不同、rank数改变初速 | global reduction；counter RNG；rank-count tests |
| R5 | P0 | `NEP_MULTIGPU`抢全部设备 | `force.cu:139-160` | OOM、GPU竞争、每rank完整复制 | 禁用该编排；显式local-rank device binding |
| R6 | P0 | ghost force/PE/virial所有权 | large只写center；small atomic写n1/n2 | 重复总能量/virial或漏reverse force | large中心gather首选；small路径明确reverse exchange |
| R7 | P0 | 邻居表不完整/不互反/未排序 | `neighbor.cu:347`; `potential.cu:227-252` | angular force读取错误/未初始化partial | 每row排序；反向edge invariant检查；边界测试 |
| R8 | P0（M2a 已关闭） | neighbor build候选域错误 | `gpu_find_neighbor_ON1`要求n2也在`[N1,N2)` (`neighbor.cu:144`) | owned看不到ghost | 已实施：`Neighbor::find_neighbor_domain` 分离 center/candidate 域 + 行容量守卫（2026-09-18） |
| R9 | P0 | PBC/triclinic domain归属 | `box.cuh` MIC；`gpu_apply_pbc` fractional转换 | 倾斜盒漏halo、边界重复owner | fractional decomposition；精确边界golden |
| R10 | P0 | PBC wrap只加减一次且 `s==1` 不wrap | `force.cu:434-453` | 大位移/精确上边界行为不同 | 兼容测试锁定；迁移前限制/规范化策略需审批 |
| R11 | P0（M2a 已关闭） | skin重建是全局OR，迁移使reference失效 | `Neighbor::check_atom_distance/find_neighbor_global` | 某rank不重建或用错old position | 已实施：全局 max-OR + 迁移/布局变化强制重建 + `invalidate_rebuild_reference`（2026-09-18） |
| R12 | P0（M2a 半关闭） | typewise cutoff/ZBL范围隐含 | `nep.cu:465-480`; ZBL传angular list | `Ra>Rr`或ZBL outer较大时截断 | M2a 半径推导忠实按消费路径及 kernel float pair-average 舍入取保守上界；`Ra>Rr`/ZBL outer 语义裁决仍属 B2 |
| R13 | P0 | small-box多image和atomic | `nep_small_box.cuh:56-703` | 多计/少计、ghost partial未返回 | 首切口拒绝small-box；后续image-aware edge+reverse |
| R14 | P1 | group使用local array index | `group.cu:25-72` | 迁移后fix/dump group错误 | labels随原子；global size归约；contents临时重建 |
| R15 | P1 | fixed/move ghost影响温度自由度 | `ensemble.cu:646-653` | T/KE分母错误 | 全局owned group counts；只计owned kinetic |
| R16 | P1 | MPI浮点归约非结合 | thermo/virial/XYZ sums | 低位不同、轨迹很快分叉 | 定义容差层级；记录reduction算法/rank数 |
| R17 | P1 | rank0输出顺序/格式 | `dump_xyz.cu`, `dump_restart.cu` | 文件列对但atom行不同；rounding不同 | 按global ID gather/sort；复用formatter；字节结构test |
| R18 | P1 | restart跨rank数 | restart不含ID/time/RNG | 无法相同续跑、重新分区不稳定 | 读取行号重建ID；只承诺保存字段；sidecar决策待定 |
| R19 | P1 | rank0 gather内存扩展性 | GPUMD完整D2H/FILE串行 | 大系统OOM/输出慢 | MVP可gather；后续分块gatherv/parallel I/O且保持顺序 |
| R20 | P1 | 多段run状态不一致 | `Run::perform_a_run/finalize` | rank间命令状态分叉、文件生命周期错 | rank0 typed IR广播；显式state machine；state checksum |
| R21 | P1 | potential文件不一致 | 每rank独立文件系统读取 | 不同rank参数不同、静默物理错 | rank0读并广播bytes，或全rank hash一致性检查 |
| R22 | P1 | 单rank错误导致collective死锁 | GPUMD普遍 `exit(1)` | 其他rank永久等待 | error object + allrank status + `MPI_Abort` only after message |
| R23 | P1 | 默认随机初速不可重现 | `velocity.cu` libc rand/array index | 分区改变初始轨迹 | counter-based global-ID RNG；与GPUMD baseline定义容差 |
| R24 | P1 | `correct_velocity`需要全局COM/惯量 | `velocity.cu:77-308` | 每rank各自去动量，物理改变 | 多阶段global reductions；PBC下角动量定义做golden |
| R25 | P2（M2a 已关闭） | 每1000次隐式 `neighbor.out` D2H/I/O | `nep.cu:1007-1025` | 同步尖峰、多rank文件竞争 | 已实施：在本次 typewise 表生成后采样，各rank local max经MPI_MAX聚合，仅rank0写；1000-step fixture 精确断言记录值/归约字节（2026-09-18） |
| R26 | P2 | CUDA-aware MPI/stream同步不明确 | GPUMD只依赖默认stream和blocking copy | 发送未完成buffer或读未到达halo | 固定 Open MPI+UCX；MPIX query + collective及p2p数值自检；同步；HostStaged fallback |
| R27 | P2（M2a 已关闭） | local capacity变化使device view失效 | `GPU_Vector::resize`式重分配 | 偶发illegal address | 已实施：布局 epoch 全量重建 + workspace 仅随 local_count 重分配 + 强制 neighbor 重建；device 指针每用途现取（2026-09-18） |
| R28 | P0（整改与验证进行中） | rank 0 创建的 node-local `/tmp` 被其他节点 rank 使用 | `src/runtime.cu` `RankIoIsolation`（整改前 `runtime.cu:80-129`） | 非零rank无法chdir，异常路径可能collective hang | 目标合同：每 rank 本机 `mkdtemp` scratch（0700）+ 两阶段错误归约共享出口 + 三阶段 finish；严格关闭条件为 [multi-node-io.md](./multi-node-io.md) 的双物理节点验收，现行合同见 [replicated-mpi.md](../standards/replicated-mpi.md) |

## 4. PBC 与 triclinic 专项

GPUMD内部 box 矩阵按晶格向量列存；MIC 在正交盒快速路径和triclinic fractional路径间切换 (`src/model/box.cuh`)。MPI分解不能在Cartesian轴上简单切triclinic bounding box然后沿同样Cartesian距离交换halo，否则倾斜面邻居可能遗漏。

推荐不变量：

```text
owner = half-open rank cell in fractional coordinates
global periodic wrap = fractional modulo for periodic lattice directions
physical distance/MIC = GPUMD Box helper in Cartesian coordinates
halo selection = physical cutoff relative to triclinic rank faces
```

必须覆盖：

- atom恰好位于rank边界、`s=0`、`s=1`；
- atom跨一个及多个周期；
- 非周期方向靠边（不得制造周期ghost）；
- 高倾斜box中不相邻Cartesian rank却通过triclinic image成为邻居；
- small-box触发条件所用的box thickness `volume/face_area`。

第一实现切口可以只执行正交全周期大盒，但 parser 对triclinic/nonperiodic输入必须明确报unsupported，不能把它投影成正交盒。

## 5. owned/ghost 不变量

每个force step应在debug构建断言：

1. 每个global ID恰好有一个owner；ghost至少有一个明确source owner。
2. 只有owned被VV更新；ghost velocity不会进入thermo。
3. large-box最终force/PE/virial只写owned。
4. 每个owned angular edge需要的reverse edge/partial可用且匹配相同periodic image。
5. local neighbor中的每个index `<local_count`，每行count不超过capacity。
6. global PE/virial是owned local sum的all-reduce，不能包含ghost。
7. migration后旧neighbor、edge map、descriptor和partial全部失效。
8. 每次输出按global ID恰好生成N条全体系record（group dump则恰好为全局group count）。

## 6. 风险关闭顺序

1. 先在单rank引入owned/local显式域但令owned=N、ghost=0，证明数值无变化。
2. 做“复制全局数据”的MPI原型，只测试归约、rank0输出和设备绑定。
3. 实现正交大盒domain decomposition与保守两跳position halo，关闭R1/R2/R3/R6-R8。（M2a 已于 2026-09-18 实施并通过验收矩阵；R6 由"中心 gather + ghost scratch 永不 gather"关闭，R3 由迁移载荷/输出排序关闭，R7 由 global-ID 行排序 + 两跳 halo 关闭。）
4. 实现分阶段intermediate exchange，与两跳oracle逐原子比较。
5. 加migration、跨周期边界、不同rank数restart。
6. 最后考虑triclinic、small-box、fix和随机thermostat。

在R1-R8未由golden tests关闭前，不应把性能优化或通信压缩作为主目标。

## 7. 未决事项

性能实施路线（2026-09-21 批准）按以下顺序推进：第一阶段日志降噪与 GPU capacity 复用
已完成实现与验收；第二阶段减少重建并分别测量普通步/重建步；第三阶段实现并比较 M2b 与
MatPL 式 ghost 力回传。通信计算重叠、3D 分解、动态负载均衡、triclinic/非周期扩展均不在
前三阶段内。第一阶段不改变 M2a 迁移触发、两跳 halo、force assembly 或统计频率。
第二阶段的缓存有效性证明、统一重建判定与计时口径已实施，批准设计见
[cache-validity-and-step-timing.md](./cache-validity-and-step-timing.md)，现行合同见
[replicated-mpi.md](../standards/replicated-mpi.md)，实测与瓶颈见
[stage2-cost-report.md](../status/stage2-cost-report.md)。第三阶段入口仍是 M2b 与 MatPL 式
ghost 力回传对比；局部 cell 网格与按需 thermo 已登记为具体性能待办。

旧未决问题文档中的初始仓库快照、已经确认的第一切口决策和已经实现的功能不再保留在
工作树中；这些历史可从 Git 和 [架构决策](../standards/architecture-decisions.md) 查询。以下只
登记仍会影响当前兼容性或未来实现的事项。

| ID | 未决事项 | 关闭条件 |
| --- | --- | --- |
| B1 | NEP3 是否纳入产品范围 | pinned reference negative test，并明确支持矩阵 |
| B2 | `Ra > Rr`、ZBL outer cutoff 大于 angular cutoff 的实际语义 | 合成 potential 对 reference/candidate 的静态与边界测试 |
| B3 | large/small neighbor capacity overflow 的安全行为 | 高密度 fixture、sanitizer 和明确错误合同 |
| B5 | malformed NEP 文件中宽松解析与安全拒绝的边界 | 建立 malformed corpus，锁定错误类别和关键 message |
| B6 | 负 `time_step`、`run 0`、缺失 ensemble 等边界行为 | reference negative corpus 与 parser/runtime 自动测试 |
| B7 | 多 potential、`potential FILE x|y|z` 的产品语义 | 明确 unsupported 或实现相加/方向语义，并加入 golden |
| B8 | 默认随机初速度与未来随机 thermostat 的跨 rank 可重复性 | 基于 global ID 的 RNG 设计、rank-count 和 restart 测试 |
| B9 | 是否提供跨 rank 数确定性归约模式 | 明确产品承诺；默认仍为结构 exact、数值容差、长程统计 |
| B10 | restart 是否允许保存 time/RNG/thermostat 的 sidecar | 保持主文件兼容的产品决策和跨 rank 测试 |
| B11 | rank 0 gather 的规模上限与分块输出 | 大 N 内存门槛、分块 Gatherv 或保持顺序的并行 I/O 方案 |
| B12 | unwrapped position 跨 migration/restart 的生命周期 | image counter 协议和 reference 对比 |
| B13 | 单 rank 异常的无死锁传播和错误分类 | 故障注入、全 rank 有界退出和唯一诊断记录 |
| B14 | small/large NEP 每原子 virial 归属是否统一 | 同构 fixture 的逐原子与总量比较 |
| B15 | XYZ charge 的单位和普通 NEP 语义 | manual/代码证据与兼容测试 |
| B16 | 自适应 `time_step DT MAX_DISTANCE` 是否支持 | 全局最大速度归约设计和多段 run golden |

域分解的 halo 深度、edge key、排序、迁移和 triclinic 问题统一在
[domain-decomposition.md](./domain-decomposition.md) 中关闭；多节点临时目录问题按
[multi-node-io.md](./multi-node-io.md) 完成严格双节点验收后关闭（R28），避免在三处复制同一计划。
