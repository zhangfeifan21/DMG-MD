# 通信与计算性能优化：O6–O11

类别：分阶段后续计划。状态：**PROPOSED**（O1–O5 本机阶段已完成；O6–O11 尚未执行）。
优先级：P0。更新日期：2026-10-21。

目标是降低真实整步成本，并判断 overlap 是否值得采用。生产默认保持
HostStaged/full、`DMGMD_DOMAIN_OVERLAP=off`。以下每个 O 编号对应一个可独立交给
Codex goal 的任务；本轮更新文档并清理构建中间文件，O6–O11 的实现与实验尚未开始。

## 已完成基础与当前判断

| 阶段 | 交付与事实源 |
| --- | --- |
| O0/O2 依赖与分区 | [集合、缓存、唯一写入合同](../standards/domain-partition.md)；[O2 数值与串行成本](../status/validation/overlap-partition-20261015.md) |
| O1 原串行基线 | [普通/重建时间线、C1 与固定资源窄矩阵](../status/performance/overlap-baseline-20261012.md) |
| O3 halo 生命周期 | [现行合同](../standards/halo-lifecycle.md)；[生命周期与串行成本验收](../status/validation/halo-lifecycle-20261015.md) |
| O4 实际 overlap | [wait/poll 正确性与正常路径证据](../status/validation/overlap-o4-20261016.md)；[当时窄成本检查](../status/performance/communication-computation-overlap-results-20261016.md) |
| O5 正式实验 | [910 个有效性能 trial、历史归因、诊断和独立采用验证](../status/performance/communication-computation-overlap-o5-results-20261021.md) |

O5 已回答本机冻结范围的采用问题：多数配置的分区成本大于 overlap 回收的时间；
固定 CudaAware 的精确 water fixture/P4/P8 有独立净收益，HostStaged/full 仍更快。
wait/poll 没有通用的稳定优劣证据。模式定义、4.60 ms 成本的证据与推测边界及优化依据见
[O5 后续成本分析](../status/performance/overlap-o5-cost-analysis-20261021.md)。

接下来先定位分区成本，同时优先验证也能惠及 full 的小范围优化。不能把增加 poll、
CPU 核数或更复杂异步结构当作已有收益方案。旧阶段的实现草案和执行待办已移除，
当前合同由 standards 维护，原始结果由带日期的报告维护。

## 每个 goal 的共同交付与边界

- 开始时核对当前 HEAD/dirty、输入、工具链和二进制 SHA-256；保留现有修改及三个只读
  reference。沿用统一环境、已验收的 K=4 物理核/rank 与固定 rank→GPU UUID 映射。
- 实施前冻结本阶段机器可读 plan：对照、资源、步数、随机顺序/种子、独立重复数、
  统计量、配对区间、无效样本与追加规则。pilot/诊断不并入主性能。
- 每个新二进制先过受影响的环境、golden、MPI/科学门槛，再实测。
  无诊断正式段每 trial 至少 10 s，目标 20–30 s，至少五个新配对重复块；
  比值统一用中位数之比及保留块配对的区间。资源不足保留缺项，不换更小 N 补造结果。
- 每阶段独立产物目录，保存源码 patch、binary/input hashes、实际命令和环境、
  绑定/遥测、原始日志、trial CSV/JSON 与一键重算入口。禁止覆盖或混合 O1–O5 的样本。
- 主墙钟关闭详细 profiler/trace/verify。CPU 的正式段归因只用满足 marker 合同的诊断；
  无 marker 的主样本正式段 CPU 为 null。继续报告未锁频、非独占和内存驻留未知条件。
- 报告实现/正确性/性能三种结论；无收益是有效性能结论，关键实验或门槛缺失则为 PARTIAL。
  更新 status 与索引，完成后从本计划移除重复实施步骤。不提交或推送 Git。

依赖顺序：O6 先行；O7、O8 分别形成可单独测量的改动；O9 取决于 O6 的证据；
O10 是条件性候选；O11 汇总实际完成的候选并重新作采用决策。O7/O8/O9 不得合成
一个无法归因的大补丁；可以在前一阶段验收后串行推进，但保留各自对照二进制。

<a id="o6"></a>
## O6：定位拆分增加成本的具体 kernel 和原因

**Goal 目标：**在相同输入、绑定和工具链下，解释 carbon_200k/P2/CudaAware 的
full→split-serial 成本来自哪里，交付可核验的 kernel 对照与下一候选选择。

范围：先做这个 4.60 ms 代表配置，再用 water/P4/P8 的获益配置核对解释是否适用；
需要时选择一个 carbon_1m 或退化配置作反例，不重跑完整 O5 矩阵。
先冻结目标 kernel、稳定采集窗口与最小诊断集合，比较 full、serial、wait 的
filter/descriptor/radial/partial/many-body 实际 GPU 执行；单独报告分类、额外 launch、
同步和通信。以匹配的无诊断墙钟确认诊断没有改变主结论。

采集可用的 Nsight Compute 指标，检查有效线程/warp、寄存器与 occupancy、
访存事务/缓存、调度与同步；明确 replay/cache control 的扰动和不可用指标。
O5 的冷启动三步 trace 只用于选候选，不能代替稳态 full/serial 对照。
不得把 event 阶段包络当作独占 kernel 时间，也不得预设“缓存 miss”就是原因。

**完成条件：**正确性和无诊断对照有效；给出成本位置、支持/不支持的原因、误差与未知项，
并据此选择 O9 的一个候选或说明不值得继续。硬件指标不足时写 INCONCLUSIVE，
不能声称精确分解出每项 4.60 ms。此阶段不改变生产 NEP kernel 或通信协议。

<a id="o7"></a>
## O7：合并缓存有效性判定的冗余全局归约

**Goal 目标：**验证并最小实现 M2a 缓存判定由 MAX→reason OR 两次 collective
收敛为一次 reason OR，测量相对原 full 的收益，保持重建、迁移和错误行为。

当前 `domain_runtime.cu` 中 `global_max_squared` 只用于严格大于阈值的判定。
对有限值有 `max(m_rank) > h ⇔ OR(m_rank > h)`，因此可先在各 rank 设置位移超限位，
再与 continuity-invalid 等原因位合并 OR。实现前核实所有消费者、非有限值处理、
阈值相等、空 rank 和各 rank collective 顺序；不能仅凭这个恒等式跳过错误语义证明。

验证缓存复用、恰好阈值与两侧、周期位移、重建/迁移、空 rank、多 run 与错误退出，
覆盖两后端及适用 rank，并跑对应 golden/MPI/long_nve 门槛。M1 行为保持原样。
先在 full 下对照原二进制，再用 serial/wait smoke 检查共同接口影响；
保存实际 collective 次数、科学输出与正式墙钟，不能将 host decision wall 全算作网络。

**完成条件：**语义证明、回归、实际归约次数及配对性能报告完整；明确采用或撤回。
只有实现验收后才更新缓存/MPI 标准与通信记账，当前标准仍描述 MAX/reason OR。

<a id="o8"></a>
## O8：按消费者需求执行 NVE thermo

**Goal 目标：**审计并消除没有消费者的 NVE 辅助 thermo 计算/全局归约，
验证原请求输出及轨迹语义不变，量化相对 full 的净收益。

当前 M2a 每步调用 `compute_domain_thermo`，包含 owned sums、8 double Allreduce、
归一化及 D2H。先逐一列出消费者与需要的步号：thermo 输出、thermostat、快照、
restart、多 run 及其他实际读者。只允许 NVE 在无消费者时跳过；NVT 的每步温度反馈保留。
不得通过减少用户要求的输出、重建、迁移、积分或 NEP 工作制造收益。

针对输出周期及段边界、多 run/restart、NVE/NVT、adaptive timestep/修速等适用路径，
验证消费者使用的是正确步的值且不存在旧值复用。跑对应 golden、MPI 和长程门槛；
记录实际 thermo/collective 次数、科学文件、配对墙钟与有 marker 的独立 CPU 诊断。
保留单独的原 full 对照和 O7 对照，区分 O8 独立增量及组合效果。

**完成条件：**消费者清单、最小实现、回归及实测齐全，或证明无法在现行兼容合同下安全跳过
并交付 NO_ADOPTION 结论。`thermo_host` 包含前序 GPU 等待，不能用它预测可节省时间。

<a id="o9"></a>
## O9：依据 O6 优化一个 NEP 执行候选

**Goal 目标：**针对 O6 证实的主因，只实施一个可归因的 NEP 执行优化，
先判断 split-serial 能否变便宜，再判断 overlap 是否产生相对 full 的净收益。

若主要损失来自完整 launch 范围与掩码执行，可评估活动 block 或中心索引列表，
保留粒子物理数组、L stride、producer/力中心唯一写入及所有浮点数学/累加顺序。
先说明索引构建/维护、额外内存和缓存失效成本，及与当前相同 grid 合同的区别。
若证据指向 full 也存在的 radial/descriptor 瓶颈，则改为一个适用于 full 的调度候选，
不能同时展开多种 kernel 重写。按 AGENTS 对触及 launch 参数的修改执行 golden/long_nve。

实施前给依赖/索引安全设计、反例与适用 golden 矩阵；覆盖多元素/NEP5/ZBL、
普通/重建/迁移、小/空 interior 与 M1 fallback，保持 full/serial oracle 和原容差。
把候选 full、serial、wait/poll 与各自原二进制比较，包含分类/列表维护和额外 launch 成本。

**完成条件：**一个候选的设计、正确性和实际净收益结论完整；没有可信候选则以
NO_ADOPTION 记录终止该分支，不为追求 overlap 改 NEP 数学、布局、精度或所有权。

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
