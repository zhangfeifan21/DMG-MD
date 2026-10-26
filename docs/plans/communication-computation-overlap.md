# 通信与计算性能优化：O6.5、O8.5 与 O9–O11

类别：分阶段后续计划。状态：**PROPOSED**（O1–O8 本机阶段完成；首轮 O9 以 NO_ADOPTION 完成；
新增 O6.5/O8.5、O9 重新执行及 O10–O11 尚未执行）。
优先级：P0。更新日期：2026-10-26。

目标是通过可验证的算法/执行结构改进降低真实整步成本，并判断 overlap 是否值得采用。
完成诊断工具或结束权限探测不等于完成优化目标；候选必须经过实际执行对照。生产默认保持
HostStaged/full、`DMGMD_DOMAIN_OVERLAP=off`。以下每个 O 编号对应一个可独立交给
Codex goal 的任务；O6 稳态诊断、O7 单次 OR 与 O8 按需 thermo 已完成。
接下来先补 O6.5 原因验证，再做 O8.5 producer 拆分实验，依据新证据重新执行 O9。

## 已完成基础与当前判断

| 阶段 | 交付与事实源 |
| --- | --- |
| O0/O2 依赖与分区 | [集合、缓存、唯一写入合同](../standards/domain-partition.md)；[O2 数值与串行成本](../status/validation/overlap-partition-20261015.md) |
| O1 原串行基线 | [普通/重建时间线、C1 与固定资源窄矩阵](../status/performance/overlap-baseline-20261012.md) |
| O3 halo 生命周期 | [现行合同](../standards/halo-lifecycle.md)；[生命周期与串行成本验收](../status/validation/halo-lifecycle-20261015.md) |
| O4 实际 overlap | [wait/poll 正确性与正常路径证据](../status/validation/overlap-o4-20261016.md)；[当时窄成本检查](../status/performance/communication-computation-overlap-results-20261016.md) |
| O5 正式实验 | [910 个有效性能 trial、历史归因、诊断和独立采用验证](../status/performance/communication-computation-overlap-o5-results-20261021.md) |
| O6 稳态归因 | [45 个正式 trial、27 份稳态 trace 与硬件指标限制](../status/performance/overlap-o6-results-20261021.md)；carbon 主损失位于 radial，原因验证缺口由 O6.5 补充 |
| O7 缓存判定归约 | [语义证明、回归、实际调用计数与 60 个配对 trial](../status/performance/overlap-o7-results-20261023.md)；采用单次 OR，整步收益 INCONCLUSIVE |
| O8 按需 NVE thermo | [消费者合同](../standards/thermo-demand.md)、[292 个调用计数作业、MPI/nightly 与 105 个三版本 trial](../status/performance/overlap-o8-results-20261026.md)；相对 O7 三组增量确认、一组 INCONCLUSIVE，采用 |
| 首轮 O9 执行候选复核 | [冻结门槛、真实 NCU 重试与安全/反例分析](../status/performance/overlap-o9-results-20261026.md)；以 NO_ADOPTION 完成，未测执行候选；历史结论保留，新一轮以 O6.5/O8.5 为输入 |

O5 已回答本机冻结范围的采用问题：多数配置的分区成本大于 overlap 回收的时间；
固定 CudaAware 的精确 water fixture/P4/P8 有独立净收益，HostStaged/full 仍更快。
wait/poll 没有通用的稳定优劣证据。模式定义、4.60 ms 成本的证据与推测边界及优化依据见
[O5 后续成本分析](../status/performance/overlap-o5-cost-analysis-20261021.md)。

O6 已定位 carbon 的主要增加位置，但没有完成单因素执行验证。当前优先验证拆分粒度是否合理，
同时保留能惠及 full 的执行优化方向。不能把增加 poll、CPU 核数或更复杂异步结构当作已有收益方案。
当前合同由 standards 维护，原始结果由带日期的报告维护；本计划中的新调度尚未生效。

## 每个 goal 的共同交付与边界

- 开始时核对当前 HEAD/dirty、输入、工具链和二进制 SHA-256；保留现有修改及三个只读
  reference。沿用统一环境、已验收的 K=4 物理核/rank 与固定 rank→GPU UUID 映射。
- 实施前冻结本阶段机器可读 plan：对照、资源、步数、随机顺序/种子、独立重复数、
  统计量、配对区间、无效样本与追加规则。pilot/诊断不并入主性能。
- 每个新二进制先过受影响的环境、golden、MPI/科学门槛，再实测。
  无诊断正式段每 trial 至少 10 s，目标 20–30 s，至少五个新配对重复块；
  比值统一用中位数之比及保留块配对的区间。资源不足保留缺项，不换更小 N 补造结果。
- 每阶段独立产物目录，保存源码 patch、binary/input hashes、实际命令和环境、
  绑定/遥测、原始日志、trial CSV/JSON 与一键重算入口。禁止覆盖或混合既有 O1–O9 样本，
  O6.5/O8.5 与重新执行的 O9 也各自保留独立目录和对照身份。
- 主墙钟关闭详细 profiler/trace/verify。CPU 的正式段归因只用满足 marker 合同的诊断；
  无 marker 的主样本正式段 CPU 为 null。继续报告未锁频、非独占和内存驻留未知条件。
- 报告实现/正确性/性能三种结论；无收益是有效性能结论，关键实验或门槛缺失则为 PARTIAL。
  更新 status 与索引，完成后从本计划移除重复实施步骤。不提交或推送 Git。
- 生产采用需要证据，探索实验负责产生证据。NCU 权限失败只限制微架构指标，不能单独成为
  O6.5/O8.5 或新一轮 O9 的完成理由；必须继续可执行的单因素对照。真正无法运行的必要实验
  记 NOT_RUN/PARTIAL 并说明缺项，不得把未测候选写成无收益。

执行顺序：**O6.5 → O8.5 → O9（重新执行）→ 条件性 O10 → O11**。O7/O8 已完成，不重做其实现。
新实验统一以当前已验收的 O7+O8 为基线，保留其 full 和原 O4 完整拆分路径；旧 O6 二进制
只作单独标识的历史桥接，不混入当前基线的增量统计。O6.5 的硬件细分原因即使仍有未知，
只要必要执行对照与依赖证明齐全，也不阻止 O8.5 实验。任何新候选必须保留独立归因和
对照二进制，不合成一个无法归因的大补丁。

<a id="o6"></a>
## O6：已完成的稳态拆分诊断

结果与未知项见 [O6 实测](../status/performance/overlap-o6-results-20261021.md)，
现行测量约束见[分区标准](../standards/domain-partition.md#诊断与计时)。
carbon 新墙钟拆分成本约 4.630 ms/step，主要新增 GPU 时间位于 radial；water/P8 的
partial 增量更大。NCU 实际受计数器权限拒绝，微架构原因 INCONCLUSIVE，不精确拆账墙钟。
首轮 O9 因没有新增执行证据而记录 NO_ADOPTION；该历史结论不替代以下 O6.5 的原因验证。

<a id="o6-5"></a>
## O6.5：补齐拆分成本的因果实验与候选证据

状态：**PROPOSED**。

**Goal 目标：**把“carbon radial 是主要损失位置”推进为可检验的执行原因结论：
实测完整 grid 加 mask、活动中心分布与执行映射变化的成本，给 O8.5 和重新执行的 O9
提供候选依据。允许具体微架构原因保留 UNKNOWN，但不能只重复 O6 的热点计时或权限失败。

必须补齐的三个缺口：

| 缺口 | 必须执行的工作 | 必须交付的证据 |
| --- | --- | --- |
| 1. 热点定位后缺少单因素执行对照 | 导出真实普通步的 I/B、E/H mask 和邻居工作量；比较 full、掩码与紧凑执行 | 由真实 mask 推导的 lane/warp/block 覆盖、同快照数值对照和配对计时，区分中心比例与执行覆盖 |
| 2. 计数器受限后没有替代验证路径 | 以快照重放、CUDA event/Nsight activity 和无诊断墙钟为主线；NCU 仅作可用时的辅助 | 不依赖硬件计数器的可重算执行证据；指标不可用时仍报告哪些假设已支持、被否定或仍 INCONCLUSIVE |
| 3. “尚无采用证据”被用成不测试候选的理由 | 为待测执行改动建立实验分支，完成最小受控对照后再决定采用；向 O9 交接实际候选结果 | 候选、唯一改变因素、正确性、成本及未知项清单；新 O9 不再将一次权限拒绝直接映射为研究任务 COMPLETE |

**执行步骤与范围：**

1. 以当前 O7+O8 二进制新采集 `carbon_200k/P2` 的 full/原 split-serial/原 wait，
   核心诊断使用与 O6 对应的 CudaAware；记录当前拆分墙钟与五类 kernel 的稳态增量。
   必须增加 `water_400k/P8` 的同类对照，检查其 angular partial 主损失反例。
   按新 trace 冻结普通步窗口、step/rank、输入/二进制和各 generation，不硬编码旧 launch-skip，
   不要求新墙钟恰好复现历史 4.630 ms；差异及环境影响原样记录。
2. 诊断性保存同一步的 mask、gid/slot、O/D/L、实际 filtered 邻居行、坐标与 Fp/sums；
   标明快照的 layout/neighbor/workspace generation。按真实 32-lane warp、64-thread block
   统计两相非空/全空/混合数量、有效 lane 数直方图及邻居数/边数分布。只作读取和独立副本分析，
   不重排生产粒子数组；静态分布不能冒充 achieved occupancy 或内存事务指标。
3. 对同一已完成 halo、全部 producer 就绪的快照，完成下列 radial 重放矩阵；water/P8
   对 angular partial 做对应控制。保持原每中心浮点表达式、邻居行与累加顺序、L stride 和输出槽位。

   | 对照 | 唯一执行变化 / 要回答的问题 |
   | --- | --- |
   | A：full、selection=null | 原始完整中心执行，作为数值与时间参照 |
   | B：全选 mask、完整 grid | 仅增加 mask 读取/判断，观测 guard 路径成本 |
   | C：真实互补 mask、两次完整 grid | 重现实际 early+late 的选择与执行覆盖 |
   | D：同一互补中心的紧凑索引执行 | 只改变 thread→center 映射和相应 grid，验证该执行改动能否消除 C 的部分成本 |

   C/D 每个中心各算一次；索引只映射执行中心，不压缩物理数组或改变 stride。
   重放前恢复同一输出初值，校验有效中间量和 force/全部 virial；重放准备、输出恢复和
   计时边界必须显式保存。D 的差值属于映射/执行改动的组合效果，不能全归为空 block 或缓存。
   紧凑索引的构建、缓存失效、额外内存及维护另行计量；重放收益不能代替整步净收益。
   重放的 producer/缓存上下文与真实流水线不同，必须与步骤 1 的稳态 trace 对照说明，
   不能把重放差值当作原流水线损失的精确份额。
4. 冻结快照、重放预热/次数和顺序，至少五个独立进程配对块，按 trial 聚合，
   不把反复重放、rank 或普通步当作独立重复。短重放明确属于诊断，不适用正式段 10 s 门槛，
   不并入正式墙钟统计；新整步对照仍遵守共同性能门槛。若 NCU 可用，补充指标验证；
   若仍权限拒绝，保存一次有效探测结果后继续上述实验，不反复尝试相同失败。

**完成条件与交接：**两体系的新稳态对照、mask/工作量分布、A–D 数值与执行对照、
候选维护成本及一键复算齐全；逐个假设写 SUPPORTED/REJECTED/INCONCLUSIVE 与证据范围。
至少回答“真实 mask 是否使两相都覆盖大量 warp/block”“紧凑执行是否降低目标 kernel 成本”
以及“收益是否足以支持进入整步实验”，不要求精确分解历史墙钟或穷尽微架构原因。
诊断工具/实验代码完成受影响的环境、golden、MPI 与 long_nve 验证，采用原 manifest 容差。
必要对照缺失为 PARTIAL；全部对照完成但某项区间不确定时允许保留有边界的 INCONCLUSIVE。
输出独立报告及候选清单供 O8.5/O9 使用，生产路径与默认配置不自动改变。

<a id="o7"></a>
## O7：已完成缓存判定归约合并

已采用各 rank 的严格位移超限判定加一次 reason OR。语义证明、阈值/错误/空 rank、
两后端 P2/P4/P8 实际归约计数、golden/MPI/nightly NVE 与 60 个配对 trial 见
[O7 报告](../status/performance/overlap-o7-results-20261023.md)。六个性能区间均跨过 1，
整步收益 INCONCLUSIVE；不宣称稳定加速。现行合同见[缓存标准](../standards/cache-validity.md)
和 [MPI 标准](../standards/mpi-runtime.md)，后续对照二进制分别保留，默认仍 HostStaged/full、overlap=off。

<a id="o8"></a>
## O8：已完成按需 NVE thermo

M2a NVE 按 thermo/XYZ 消费者的段内步号并集计算，NVT 保留每步反馈；restart、
adaptive timestep、修速和段末同步不引入隐含 thermo。所有权、数学、积分和请求输出不变。
消费者、旧值防护、通信字节与诊断就绪合同见[thermo 标准](../standards/thermo-demand.md)。
golden、292 个实际调用计数作业、MPI 68、nightly NVE/NVT 18 + restart 12、三版本 smoke 48、
105 个正式 trial 和 8 个 CPU 诊断见[O8 实测](../status/performance/overlap-o8-results-20261026.md)。
采用该实现；相对 O7 的 carbon/HostStaged 增量 INCONCLUSIVE，其余三组确认加速。
不从 `thermo_host` 预测节省时间，不外推范围或改变 overlap 默认。

<a id="o8-5"></a>
## O8.5：只拆 filter/descriptor，完整执行力计算的候选实验

状态：**PROPOSED**。依赖：O6.5 必要执行对照及依赖审计完成，O7/O8 基线已验收；
不以取得硬件计数器或证明紧凑列表有收益为前提。

**Goal 目标：**验证 overlap 是否只需提前计算安全中心的 descriptor，而不必把整个力流水线
都拆成 early/late。通过改变拆分粒度，消除 radial、angular partial 和 many-body 的两次
掩码执行，同时实测提前生成 Fp 的工作能否带来整步净收益。结果可以无收益，但必须实际测试。

**候选调度与依赖证明：**沿用当前 O/D/L、I/B、E/H 定义和 `DomainPartitionKey`，
E/H 互补覆盖 D，E 及其 Verlet 邻居属于 owned。第一版不扩大 E、不修改分类规则，
不同时加入紧凑列表或其他优化。普通步的 wait 候选为：

```text
prepare（分类/失效/分配，均在 halo begin 前）
→ begin halo → clear outputs once
→ filter(E) → descriptor(E)
→ finish halo（保持原 readiness、物理 unpack 与默认 stream 顺序）
→ filter(H) → neighbor record if due → descriptor(H)
→ radial(O) → angular_partial(D) → many_body(O) → ZBL(O) if enabled
→ finalize → 原 VV2/thermo/output
```

serial 候选先完成 halo，再按相同 producer 分段及完整 force 顺序执行，单独测量拆分成本。
五类基础 kernel 在 eligible 普通步从原 split 的 10 次 launch 变为 7 次；ZBL 启用时
另在 owned 上执行一次。必须用实际 launch/中心覆盖证明，不能把 fallback 算为候选执行。

从 `NEP::compute_domain::products` 分离 descriptor producer 与 force consumer 调度：
descriptor 只读本中心坐标邻域并生成 PE/Fp/sums；radial 读取自身及邻居 Fp，
angular partial 读取中心 Fp/sums，many-body 读取双向 partial。因此完整 force 必须等
D 的 descriptor 全部完成，many-body 必须等 D 的 partial 全部完成。每中心继续保持
descriptor 的 PE 写入及 radial→many-body→ZBL 的 force/virial 累加顺序，owned force
与 ZBL 不写 ghost。保持 L stride、原粒子/邻居顺序、数学/精度、halo 字节、通信后端、
单一默认 stream、一次清零及一次逻辑 force call；不提前 partial、不引入第二个改动因素。

**正确性与生命周期验收：**

- 提供显式实验选择与实际配置/计数记录，保留当前 full 和完整 split 为独立 oracle；
  P1/M1、setup/rebuild/迁移、空 interior/local、未证明模型维持原 fallback。
  各 rank 的不同 fallback 不得改变 neighbor record、collective 次数与调用顺序。
- 将 early 验证调整为检查 E 的 filtered rows、PE/Fp/sums，验证 early 不写最终 force/virial；
  不能直接沿用“early 已完成 I 的力”的旧断言。以 ghost 坐标 poison 和同快照 full replay
  证明 early 不消费 ghost；finalize 前比较完整 PE/force/全部 virial、有效 partial 和输出。
- 覆盖 NEP4/5、多元素/typewise cutoff、ZBL、NVE/NVT、`E\I` 非空、I/B 混合 block、
  空/小 interior、尾 block、P2 同 peer/PBC、同 shape gid 重排、无迁移 rebuild、workspace
  失效、多 run/restart 及周期 neighbor 记录。先通过环境、baseline、MPI 与 long_nve，
  容差沿用现有 manifest；实施时同步更新分区/验证合同，不能只修改调度而复用旧通过声明。

**正式性能实验：**以当前 O7+O8 为共同基线，冻结两套二进制：原基线保留
full/原 serial/原 wait，新二进制保留 full/producer-serial/producer-wait。
先核对新旧 full 的数值与配对成本，防止调度重构影响 full 后仍使用旧 full 掩盖回归。
核心矩阵为 `carbon_200k/P2`、`water_400k/P8` × HostStaged/CudaAware：
每配置至少五个独立配对块，六个版本/调度组合均纳入随机顺序，初始共 120 个正式 trial。
步数由 pilot 冻结，正式段遵守至少 10 s、目标 20–30 s；pilot/诊断独立。
预先固定配对区间和追加规则，不按结果删慢样本。P4、carbon_1m 和 poll 留给 O9/O11 扩展验证，
不能据核心矩阵外推。必须分别报告：

| 问题 | 对照 |
| --- | --- |
| 重构是否影响 full | 原基线 full / 新二进制 full |
| 拆分粒度是否降低额外成本 | 各自 serial − 各自 full，以及原 serial / producer-serial |
| overlap 回收多少调度时间 | 各自 serial / wait，同时报告 ms/step 差值 |
| 是否胜过同后端完整计算 | 新 full / producer-wait，并保留原 full 对照 |
| 是否胜过默认竞争路径 | 同资源 HostStaged/full / producer-wait，不能只比较 CudaAware 内部 |

独立稳态 trace 核对七次基础 launch、完整 radial/partial/many-body、普通/重建占比及实际
通信/分类次数，记录分类维护、内存高水位、同步与有效 CPU marker 成本。
既有 kernel 增量只能用作候选依据，不能直接预测新调度的缓存行为、隐藏时间或整步加速。

**完成条件与交接：**候选实现、依赖/唯一写入证明、完整正确性门槛、六组合配对墙钟、
实际执行 trace、CPU/内存代价及复算入口齐全。分别判定“拆分成本下降”“同后端净收益”
和“相对 HostStaged/full 净收益”；只有第一个成立不能写成 overlap 已值得采用。
必要门槛缺失为 PARTIAL；完成实测但无收益是有效结论。将候选 patch、二进制及全部正负
结果交给 O9 筛选，不自动改变生产默认或声称已经形成相对 GPUMD 的算法优势。

<a id="o9"></a>
## O9：基于 O6.5/O8.5 新证据重新执行候选筛选与验证

状态：**PROPOSED（重新执行）**。首轮 2026-10-26 的 NO_ADOPTION、权限限制与未测候选
结论仍保存在 [O9 历史报告](../status/performance/overlap-o9-results-20261026.md)，不覆盖原产物。

**Goal 目标：**使用 O6.5 的执行原因证据和 O8.5 的拆分粒度结果，选择并验证一个有依据的
执行候选，判断能否在明确范围降低当前 O7+O8 的整步成本。O8.5 已测的 producer 调度
本身可以成为候选；若 O6.5 支持紧凑执行，则单独验证它，组合改动须保留各单项对照。
不要求为重新执行 O9 再发明一个不同候选，也不能因尚无采用证据而跳过候选实验。

**执行要求：**

- 开始前逐项审查 O6.5/O8.5 的必要证据、数值门槛、独立二进制与负结果。
  缺失先补实验；硬件细分原因 UNKNOWN 不阻止已有单因素证据支持的候选。
- 重写/扩展现有 `tests/benchmark/run_overlap_o9.py` 的冻结和判定流程，显式接收新报告、
  候选 patch/binary 和实际实验结果。旧脚本的 `main_trials=0`、权限拒绝即 COMPLETE/NO_ADOPTION
  只描述首轮探测，不能直接重跑后当作本轮算法评估。增加缺候选实验不得完成的工具判定验证。
- 用新目录冻结一个候选及原 full、原完整 split、O8.5 调度的必要对照；新候选先过受影响的
  环境、baseline、MPI/long_nve 门槛，再按共同规则运行独立配对性能。
  若候选来自探索数据筛选，必须用未参与选择的新配对块确认，不能只复用 O6.5/O8.5 样本。
  若探索结果均未显示净收益，选择证据最充分的候选做独立排除验证，再给出有边界的负结论。
- 核心验证沿用 carbon/P2、water/P8 与双后端，分别比较同后端 full 和 HostStaged/full。
  增加 water/P4、carbon_1m 的适用性验证及空/小 interior、高重建反例；若冻结时有依据缩小
  范围，必须明确适用条件与未验证项。报告净收益区间、维护/额外内存、CPU 和失败回退，
  选择准则与多候选比较规则须在确认数据采集前冻结。

**完成条件：**至少对 O8.5 的实测候选和 O6.5 支持的执行方向给出有数据依据的筛选结论，
对最终选中的单个候选完成独立确认。收益成立时明确采用范围并交 O11 全局复验；
必要实验齐全且候选均无收益时可记 NO_ADOPTION；必要实验未执行或数值门槛失败为 PARTIAL。
NCU 权限拒绝、只有工具测试通过或没有新性能样本均不能单独使本轮 COMPLETE。
不保证一定得到正收益，也不把消除 newmd 拆分回归等同于超越 GPUMD；算法创新或跨引擎
性能主张必须另有相应对照。生产默认仍 HostStaged/full、overlap=off。

<a id="o10"></a>
## O10：条件性验证小归约与 halo 的后端选择

**Goal 目标：**仅在前述优化后小消息归约/同步仍有足够成本时，验证为小归约和 halo
分别选择 HostStaged/CudaAware 是否比统一后端更快。

O5 已表明 CudaAware 不保证端到端更快。先量化剩余调用次数、消息量与同步，
再决定是否值得实现显式、可审计的选择；各类消息的实际后端须分别记录。
不把小归约结论直接应用于 halo，不机械删除 CudaAware 输入/输出同步。
任何 readiness/completion 改动须先给生命周期证明与故障/数值门槛。

**完成条件：**独立后端对照、正确性、CPU 代价及净收益完整；若 O7/O8 已消除主要机会，
记录有数据依据的 NO_ADOPTION，保留现有后端。此阶段不引入新 stream、progress thread
或新通信协议；它不是完成 O11 的强制前提。

<a id="o11"></a>
## O11：相对优化后的 full 复验与采用决策

**Goal 目标：**对实际验收的候选重做受控对照，判断是否应在明确范围手动使用 overlap，
交付新的采用建议，生产默认保持 off。

保留各单独改动和组合的 full 对照，比较相同资源上的 HostStaged/full、CudaAware/full、
serial、wait、poll；不能拿优化前 O5 full 代表新的默认竞争路径。
先冻结范围与统计规则：主矩阵原则上沿用 O5 三体系 × P1/2/4/8 × 两后端 × 四模式，
每配置至少五次独立重复；候选适用范围若需缩小，须预先写出理由和不能外推的缺项。
保留空/小 interior、薄 slab 和高重建场景，fallback 不算独立 overlap 证据。
任何从探索数据得到的阈值或新条件必须使用独立数据验证；保留全部配置与慢样本。

**完成条件：**分区成本、overlap 增量、相对新 full 的净收益、CPU 与退化场景、
不确定性和独立确认齐全。缺矩阵或关键归因标 PARTIAL，不因脚本可运行称完成。
只建议明确硬件/输入/后端/绑定条件及失败回退规则；默认变更、自动阈值生产落地另行确认。

## 保留的证据缺口与不推进的方向

- 历史细小接口成本部分区间宽，仍为 INCONCLUSIVE；需要更精细结论时另增稳定资源下的
  配对块，不删除 O5 慢样本，也不将现有结果改成零成本。
- 独立时钟/请求完成边界误差仍 UNKNOWN，精确 R=N/A。实际 GPU 与请求状态区间交集
  不等于物理网络传输隐藏量，更不等于关键路径节省。
- interior/重建比例不能直接作为通用阈值。新盒形、势、体系、设备或跨节点须单独验证。
- CUDA Graphs 暂列后备：它针对提交开销，不能据此消除已观察到的 GPU 拆分执行损失。
  未有证据支持增加 poll 次数、CPU 核数或后台线程为优先方案。
- M2b、3D、halo 半径、NEP 数学/精度/布局/stride/所有权、新 stream、progress thread、
  双缓冲和跨步流水不纳入上述任务。跨引擎绑定、三引擎矩阵及完整 CPU C2/C3 独立管理。
