# 通信与计算性能优化：O10–O11

类别：分阶段后续计划。状态：**PROPOSED**（O1–O8 本机阶段完成；O9 以 NO_ADOPTION 完成；O10–O11 尚未执行）。
优先级：P0。更新日期：2026-10-26。

目标是降低真实整步成本，并判断 overlap 是否值得采用。生产默认保持
HostStaged/full、`DMGMD_DOMAIN_OVERLAP=off`。以下每个 O 编号对应一个可独立交给
Codex goal 的任务；O6 稳态诊断、O7 单次 OR 与 O8 按需 thermo 已完成，O9 已终止无可信候选分支。

## 已完成基础与当前判断

| 阶段 | 交付与事实源 |
| --- | --- |
| O0/O2 依赖与分区 | [集合、缓存、唯一写入合同](../standards/domain-partition.md)；[O2 数值与串行成本](../status/validation/overlap-partition-20261015.md) |
| O1 原串行基线 | [普通/重建时间线、C1 与固定资源窄矩阵](../status/performance/overlap-baseline-20261012.md) |
| O3 halo 生命周期 | [现行合同](../standards/halo-lifecycle.md)；[生命周期与串行成本验收](../status/validation/halo-lifecycle-20261015.md) |
| O4 实际 overlap | [wait/poll 正确性与正常路径证据](../status/validation/overlap-o4-20261016.md)；[当时窄成本检查](../status/performance/communication-computation-overlap-results-20261016.md) |
| O5 正式实验 | [910 个有效性能 trial、历史归因、诊断和独立采用验证](../status/performance/communication-computation-overlap-o5-results-20261021.md) |
| O6 稳态归因 | [45 个正式 trial、27 份稳态 trace 与硬件指标限制](../status/performance/overlap-o6-results-20261021.md)；carbon 主损失位于 radial，硬件原因 INCONCLUSIVE，O9 当前 NO_ADOPTION |
| O7 缓存判定归约 | [语义证明、回归、实际调用计数与 60 个配对 trial](../status/performance/overlap-o7-results-20261023.md)；采用单次 OR，整步收益 INCONCLUSIVE |
| O8 按需 NVE thermo | [消费者合同](../standards/thermo-demand.md)、[292 个调用计数作业、MPI/nightly 与 105 个三版本 trial](../status/performance/overlap-o8-results-20261026.md)；相对 O7 三组增量确认、一组 INCONCLUSIVE，采用 |
| O9 执行候选复核 | [冻结门槛、真实 NCU 重试与安全/反例分析](../status/performance/overlap-o9-results-20261026.md)；计数器仍受限，无可信候选，以 NO_ADOPTION 完成；未改 NEP 执行 |

O5 已回答本机冻结范围的采用问题：多数配置的分区成本大于 overlap 回收的时间；
固定 CudaAware 的精确 water fixture/P4/P8 有独立净收益，HostStaged/full 仍更快。
wait/poll 没有通用的稳定优劣证据。模式定义、4.60 ms 成本的证据与推测边界及优化依据见
[O5 后续成本分析](../status/performance/overlap-o5-cost-analysis-20261021.md)。

O6 已定位 carbon 的主要增加位置；接下来优先验证也能惠及 full 的小范围优化。不能把增加 poll、
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

依赖顺序：O6–O9 已完成，原 full、O7 与 O7+O8 对照分别保留；O9 未增加执行候选。
O10 是条件性候选；O11 汇总实际验收的改动并重新作采用决策。任何新候选必须保留
独立归因和对照二进制，不合成一个无法归因的大补丁。

<a id="o6"></a>
## O6：已完成的稳态拆分诊断

结果与未知项见 [O6 实测](../status/performance/overlap-o6-results-20261021.md)，
现行测量约束见[分区标准](../standards/domain-partition.md#诊断与计时)。
carbon 新墙钟拆分成本约 4.630 ms/step，主要新增 GPU 时间位于 radial；water/P8 的
partial 增量更大。NCU 实际受计数器权限拒绝，微架构原因 INCONCLUSIVE，不精确拆账墙钟。
当前不值得在因果证据不足时启动 O9 kernel 改写，记录 NO_ADOPTION；生产行为与默认未改。

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

<a id="o9"></a>
## O9：已完成执行候选复核，NO_ADOPTION

2026-10-26 按原完成条件终止无可信候选分支。两份既有二进制的环境/golden 与工具测试通过；
实际重试原 O6 稳态窗口仍返回 ERR_NVGPUCTRPERM，没有新单因素证据选定执行候选。
冻结方案、候选成本/安全与反例、原始证据及复算入口见 [O9 报告](../status/performance/overlap-o9-results-20261026.md)。
生产 NEP 未改；新候选性能未测，不能解释为已证明无收益。重新开启须有新因果证据并另冻一个候选。

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
