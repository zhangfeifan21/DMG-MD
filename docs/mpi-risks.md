# DMG-MD MPI 改造风险登记

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

控制：首原型同时实现/比较两种 oracle：保守两跳 position halo重算，和一跳 position + `Fp` + reverse-partial 分阶段交换。只有逐原子 force/virial等价后才选择生产协议。

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
| R1 | P0 | NEP多层halo | `nep.cu:727-728`; `potential.cu:209-252` | 边界力错但内部原子正确 | 两跳/分阶段双实现对比；分区边界三原子构型 |
| R2 | P0 | global/local N和SoA stride混用 | `atom.cuh`; ELL地址 `slot*N+n` | 越界、分量串线、ghost被积分 | typed views；canary；owned/ghost单元测试 |
| R3 | P0 | 无stable global ID | `read_xyz.cu`, `group.cu` | 输出重排、迁移后group错 | input row ID；跨rank迁移/输出排序测试 |
| R4 | P0/P1 | thermo/thermostat/RNG rank依赖 | `ensemble.cu`, `ensemble_ber.cu`, `velocity.cu` | NVT各rank温度不同、rank数改变初速 | global reduction；counter RNG；rank-count tests |
| R5 | P0 | `NEP_MULTIGPU`抢全部设备 | `force.cu:139-160` | OOM、GPU竞争、每rank完整复制 | 禁用该编排；显式local-rank device binding |
| R6 | P0 | ghost force/PE/virial所有权 | large只写center；small atomic写n1/n2 | 重复总能量/virial或漏reverse force | large中心gather首选；small路径明确reverse exchange |
| R7 | P0 | 邻居表不完整/不互反/未排序 | `neighbor.cu:347`; `potential.cu:227-252` | angular force读取错误/未初始化partial | 每row排序；反向edge invariant检查；边界测试 |
| R8 | P0 | neighbor build候选域错误 | `gpu_find_neighbor_ON1`要求n2也在`[N1,N2)` (`neighbor.cu:144`) | owned看不到ghost | 新接口分center域和candidate域 |
| R9 | P0 | PBC/triclinic domain归属 | `box.cuh` MIC；`gpu_apply_pbc` fractional转换 | 倾斜盒漏halo、边界重复owner | fractional decomposition；精确边界golden |
| R10 | P0 | PBC wrap只加减一次且 `s==1` 不wrap | `force.cu:434-453` | 大位移/精确上边界行为不同 | 兼容测试锁定；迁移前限制/规范化策略需审批 |
| R11 | P0 | skin重建是全局OR，迁移使reference失效 | `Neighbor::check_atom_distance/find_neighbor_global` | 某rank不重建或用错old position | 所有rank OR；迁移/容量变化强制rebuild |
| R12 | P0 | typewise cutoff/ZBL范围隐含 | `nep.cu:465-480`; ZBL传angular list | `Ra>Rr`或ZBL outer较大时截断 | loader明确检查或忠实复现；合成potential实验 |
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
| R25 | P2 | 每1000次隐式 `neighbor.out` D2H/I/O | `nep.cu:1007-1025` | 同步尖峰、多rank文件竞争 | rank0 aggregate或明确不支持；不能所有rankappend |
| R26 | P2 | CUDA-aware MPI/stream同步不明确 | GPUMD只依赖默认stream和blocking copy | 发送未完成buffer或读未到达halo | 固定 Open MPI+UCX；MPIX query + 四类数值自检；同步；HostStaged fallback |
| R27 | P2 | local capacity变化使device view失效 | `GPU_Vector::resize`式重分配 | 偶发illegal address | epoch/versioned views；迁移后统一capacity growth和重建 |
| R28 | P0 | rank 0 创建的 node-local `/tmp` 被其他节点 rank 使用 | `runtime.cu:80-129` | 非零rank无法chdir，异常路径可能collective hang | 待审批的每rank本地scratch和两阶段错误归约；见 [multi-node-io-plan.md](./multi-node-io-plan.md) |

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
3. 实现正交大盒domain decomposition与保守两跳position halo，关闭R1/R2/R3/R6-R8。
4. 实现分阶段intermediate exchange，与两跳oracle逐原子比较。
5. 加migration、跨周期边界、不同rank数restart。
6. 最后考虑triclinic、small-box、fix和随机thermostat。

在R1-R8未由golden tests关闭前，不应把性能优化或通信压缩作为主目标。
