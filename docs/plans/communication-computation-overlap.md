# 通信与计算性能优化：条件性 O10；O11 限定复验已完成

类别：分阶段后续计划。状态：**PROPOSED（仅 O10 条件性暂缓；O1–O9 已完成，O11 限定复验已完成）**。
优先级：P0。更新日期：2026-10-30。

目标是通过可验证的算法/执行结构改进降低真实整步成本，并判断 overlap 是否值得采用。
完成诊断工具或结束权限探测不等于完成优化目标；候选必须经过实际执行对照。生产默认保持
HostStaged/full、`DMGMD_DOMAIN_OVERLAP=off`。以下每个 O 编号对应一个可独立交给
Codex goal 的任务；O6/O6.5 诊断、O7 单次 OR 与 O8 按需 thermo 已完成。
O8.5 producer 拆分与 O8.6 紧凑索引/几何内缩实验均已完成，
O11 已完成 800 个新 trial 的限定复验，仅建议 carbon_200k/P2 两后端手动 B-wait；默认不变。

## 已完成基础与当前判断

| 阶段 | 交付与事实源 |
| --- | --- |
| O0/O2 依赖与分区 | [集合、缓存、唯一写入合同](../standards/domain-partition.md)；[O2 数值与串行成本](../status/validation/communication-computation-overlap.md#overlap-partition-20261015) |
| O1 原串行基线 | [普通/重建时间线、C1 与固定资源窄矩阵](../status/performance/communication-computation-overlap-results.md#o1-20261012-report) |
| O3 halo 生命周期 | [现行合同](../standards/halo-lifecycle.md)；[生命周期与串行成本验收](../status/validation/communication-computation-overlap.md#halo-lifecycle-20261015) |
| O4 实际 overlap | [wait/poll 正确性与正常路径证据](../status/validation/communication-computation-overlap.md#overlap-o4-20261016)；[当时窄成本检查](../status/performance/communication-computation-overlap-results.md#o4-20261016-report) |
| O5 正式实验 | [910 个有效性能 trial、历史归因、诊断和独立采用验证](../status/performance/communication-computation-overlap-results.md#o5-20261021-report) |
| O6 稳态归因 | [45 个正式 trial、27 份稳态 trace 与硬件指标限制](../status/performance/nep-execution-cost.md#o6-results-20261021-report)；carbon 主损失位于 radial，原因验证已由 O6.5 补充 |
| O7 缓存判定归约 | [语义证明、回归、实际调用计数与 60 个配对 trial](../status/performance/cache-reduction-results.md#o7-results-20261023-report)；采用单次 OR，整步收益 INCONCLUSIVE |
| O8 按需 NVE thermo | [消费者合同](../standards/thermo-demand.md)、[292 个调用计数作业、MPI/nightly 与 105 个三版本 trial](../status/performance/thermo-demand-results.md#o8-results-20261026-report)；相对 O7 三组增量确认、一组 INCONCLUSIVE，采用 |
| O6.5 原因验证 | [30 个正式 trial、18 份 trace、真实图/mask 与 50 个 A–D 重放进程](../status/performance/nep-execution-cost.md#o65-results-20261027-report)；两目标 C/D 区间下界均大于 1，支持独立整步试验，未采用 |
| O8.5 producer 候选 | [依赖/验证合同](../standards/domain-partition.md#o85-producer-调度)、[完整门槛、120 个六组合正式 trial、24 份 trace 与 8 个 CPU 诊断](../status/performance/producer-schedule-results.md#o85-results-20261027-report)；拆分成本下降，默认路径净收益限于本轮 water/P8/HostStaged，未采用 |
| O8.6 紧凑/几何候选 | [当前合同](../standards/domain-partition.md#o86-producer-紧凑与几何调度)、[完整门槛、240 个正式 trial、48 份 trace、24 个 CPU 诊断与离线复算](../status/performance/producer-schedule-results.md#o86-results-20261028-report)；B/A 四组确认，C/B wait 增量均未确认，未采用 |
| 首轮 O9 执行候选复核 | [冻结门槛、真实 NCU 重试与安全/反例分析](../status/performance/nep-candidate-validation.md#o9-results-20261026-report)；以 NO_ADOPTION 完成，未测执行候选；历史结论保留，新一轮另见下行 |
| O9 独立确认 | [300 个正式 trial、12 个 CPU 诊断与限定范围确认](../status/performance/nep-candidate-validation.md#o9-results-20261029-report)；预选 B 三个核心 scope 确认，交 O11，默认不变 |
| O11 限定采用复验 | [800 个正式 trial、48 个 CPU 诊断与离线复算](../status/performance/nep-candidate-validation.md#o11-results-20261030-report)；仅 carbon/P2 双后端手动 B-wait，默认不变 |

O5 已回答本机冻结范围的采用问题：多数配置的分区成本大于 overlap 回收的时间；
固定 CudaAware 的精确 water fixture/P4/P8 有独立净收益，HostStaged/full 仍更快。
wait/poll 没有通用的稳定优劣证据。模式定义、4.60 ms 成本的证据与推测边界及优化依据见
[O5 后续成本分析](../status/performance/communication-computation-overlap-results.md#o5-cost-20261021-report)。

O6.5 已完成两体系的单因素执行验证，O8.5/O8.6 已完成拆分粒度、紧凑映射和几何规则的整步实验。
O9/O11 已完成独立确认与限定范围采用复验；后续保留能惠及 full 的执行优化方向。不能把增加 poll、CPU 核数或更复杂异步结构当作已有收益方案。
当前合同由 standards 维护，原始结果由带日期的报告维护；producer 调度仅显式实验启用。

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
  O6.5/O8.5/O8.6 与重新执行的 O9 也各自保留独立目录和对照身份。
- 主墙钟关闭详细 profiler/trace/verify。CPU 的正式段归因只用满足 marker 合同的诊断；
  无 marker 的主样本正式段 CPU 为 null。继续报告未锁频、非独占和内存驻留未知条件。
- 报告实现/正确性/性能三种结论；无收益是有效性能结论，关键实验或门槛缺失则为 PARTIAL。
  更新 status 与索引，完成后从本计划移除重复实施步骤。不提交或推送 Git。
- 生产采用需要证据，探索实验负责产生证据。NCU 权限失败只限制微架构指标，不能单独成为
  O6.5/O8.5/O8.6 或新一轮 O9 的完成理由；必须继续可执行的单因素对照。真正无法运行的必要实验
  记 NOT_RUN/PARTIAL 并说明缺项，不得把未测候选写成无收益。

剩余为**条件性 O10（暂缓，PROPOSED）**；O11 限定复验已完成。原始全矩阵未测范围不作采用建议；扩大范围须另行冻结独立实验。O7/O8 不重做其实现。
新实验统一以当前已验收的 O7+O8 为基线，保留其 full 和原 O4 完整拆分路径；旧 O6 二进制
只作单独标识的历史桥接，不混入当前基线的增量统计。O6.5 的硬件细分原因即使仍有未知，
只要必要执行对照与依赖证明齐全，也不阻止候选独立验证。任何新候选必须保留独立归因和
对照二进制，不合成一个无法归因的大补丁。

<a id="o6"></a>
## O6：已完成的稳态拆分诊断

结果与未知项见 [O6 实测](../status/performance/nep-execution-cost.md#o6-results-20261021-report)，
现行测量约束见[分区标准](../standards/domain-partition.md#诊断与计时)。
carbon 新墙钟拆分成本约 4.630 ms/step，主要新增 GPU 时间位于 radial；water/P8 的
partial 增量更大。NCU 实际受计数器权限拒绝，微架构原因 INCONCLUSIVE，不精确拆账墙钟。
首轮 O9 因没有新增执行证据而记录 NO_ADOPTION；该历史结论不替代以下 O6.5 的原因验证。

<a id="o6-5"></a>
## O6.5：已完成拆分执行的因果对照

状态：**COMPLETE（2026-10-27）**。当前 O7+O8 基线的 carbon/P2、water/P8 新正式
墙钟与三轮稳态 trace、十个普通步真实图/mask 快照、五个独立进程配对块的 A–D 重放、
维护成本与一键复算齐全；完整环境/golden/MPI/nightly 门槛通过，未改变生产默认。

真实静态覆盖否定“两相在每 rank 均覆盖超过半数 warp/block”的统一解释；紧凑映射的
C/D 为 1.4226 [1.4043, 1.4422] / 1.1405 [1.1179, 1.1661]，支持两个目标进入独立
整步试验，但 D 仍慢于 A。微架构原因仍 INCONCLUSIVE，候选整步净收益 NOT_TESTED。
完整范围、负结果、维护/缓存限制及 O8.5/O9 交接见 [O6.5 实测](../status/performance/nep-execution-cost.md#o65-results-20261027-report)；
现行隔离重放与测量合同见[分区标准](../standards/domain-partition.md#o65-隔离快照重放)。

<a id="o7"></a>
## O7：已完成缓存判定归约合并

已采用各 rank 的严格位移超限判定加一次 reason OR。语义证明、阈值/错误/空 rank、
两后端 P2/P4/P8 实际归约计数、golden/MPI/nightly NVE 与 60 个配对 trial 见
[O7 报告](../status/performance/cache-reduction-results.md#o7-results-20261023-report)。六个性能区间均跨过 1，
整步收益 INCONCLUSIVE；不宣称稳定加速。现行合同见[缓存标准](../standards/cache-validity.md)
和 [MPI 标准](../standards/mpi-runtime.md)，后续对照二进制分别保留，默认仍 HostStaged/full、overlap=off。

<a id="o8"></a>
## O8：已完成按需 NVE thermo

M2a NVE 按 thermo/XYZ 消费者的段内步号并集计算，NVT 保留每步反馈；restart、
adaptive timestep、修速和段末同步不引入隐含 thermo。所有权、数学、积分和请求输出不变。
消费者、旧值防护、通信字节与诊断就绪合同见[thermo 标准](../standards/thermo-demand.md)。
golden、292 个实际调用计数作业、MPI 68、nightly NVE/NVT 18 + restart 12、三版本 smoke 48、
105 个正式 trial 和 8 个 CPU 诊断见[O8 实测](../status/performance/thermo-demand-results.md#o8-results-20261026-report)。
采用该实现；相对 O7 的 carbon/HostStaged 增量 INCONCLUSIVE，其余三组确认加速。
不从 `thermo_host` 预测节省时间，不外推范围或改变 overlap 默认。

<a id="o8-5"></a>
## O8.5：已完成 producer 拆分与完整力计算实验

状态：**COMPLETE（2026-10-28，10-27 开始）**。显式 `schedule=producer` 保留原
E/H、stride、数学和 halo 语义，仅拆 filter/descriptor，随后完整执行 radial(O)、
partial(D)、many-body(O) 与可选 ZBL(O)。实际普通步基础 launch 从 10 降为 7；
原 full 与完整 split 保留，生产默认仍 HostStaged/full、overlap=off、schedule=split。

环境/golden、GPU CTest 18、producer MPI 313 次执行、受控调度 27、完整 nightly
42 配置及 12 restart、24 个真实输入科学作业通过。120 个六组合正式 trial、24 份独立
trace、8 个 CPU 诊断及离线复算齐全。四组拆分成本下降；同后端候选 full / producer-wait
在 water/HostStaged、carbon/CudaAware、water/CudaAware 分别为
1.0193 [1.0155, 1.0256]、1.0321 [1.0216, 1.0383]、1.0912 [1.0858, 1.0953]，
carbon/HostStaged 为 0.9692 [0.9649, 0.9796]。相对 HostStaged/full，只有本轮
water/P8/HostStaged 显示净收益；两个 CudaAware 候选仍更慢。

最终合同见[分区标准](../standards/domain-partition.md#o85-producer-调度)，全部正负结果、
依赖/唯一写入证明、固定二进制与 patch、诊断开销、原始日志和一键复算见
[O8.5 实测](../status/performance/producer-schedule-results.md#o85-results-20261027-report)。这些点态区间属于探索，
候选交 O9 用新配对块独立筛选和确认；该阶段未自动采用、未执行 O9/O11；后续 O9 结果另见下文。

<a id="o8-6"></a>
## O8.6：已完成的 producer 紧凑索引与几何内缩

状态：**COMPLETE（2026-10-28）**。B 保留原 E/H、稳定 GPU 索引和原槽位/stride；C 从
实际 Verlet 参考 q 做完整构建半径的几何内缩，验证实际 V(E) 闭包，不构建 S/I/B。
仅 filter/descriptor 紧凑执行，全部 force consumer 在 halo 和 producer 就绪后完整执行。

两路线分别通过完整 MPI、27 个受控调度作业、nightly 42 配置 + 12 restart；两份二进制
环境/golden、GPU CTest 18 + O8.6 专项 1 项、48 个大输入科学对照均通过。
240 个新正式 trial、48 份独立 trace、24 个 CPU 诊断与封存源码离线复算齐全，未触发追加。
B/A 的 serial、wait 四组探索区间均确认收益；C/B 的四组 wait 增量均 INCONCLUSIVE。
相对同候选二进制 HostStaged/full，carbon 双后端及 water/HostStaged 的 B/C 有本轮净收益，
water/CudaAware 仍慢。C 减少相对 B 的辅助空间和分类维护成本，未确认额外 wait 净收益。

最终合同见[分区标准](../standards/domain-partition.md#o86-producer-紧凑与几何调度)，全部
区间、负结果、真实网格/stream/早期 GPU 时间、维护/CPU/内存、独立 patch/binary 与复算入口见
[O8.6 实测](../status/performance/producer-schedule-results.md#o86-results-20261028-report)。生产默认仍为
HostStaged/full、overlap=off、schedule=split；该阶段未采用；后续 O9 独立确认另见下文，该阶段未执行 O10/O11；后续 O11 结果见下文。

<a id="o9"></a>
## O9：已完成候选筛选与独立确认

状态：**COMPLETE（2026-10-29）**。首轮 NO_ADOPTION 历史保留；新一轮预先选择
同 E/H 紧凑 producer B，显式保留 full、完整 split、A/B/C 和独立 O8/full 对照。
300 个新正式 trial、12 个 CPU 诊断、60 个真实输入科学对照及新 MPI/调度门槛通过；
相同二进制的完整 nightly/GPU 验收经原始封存校验后复用，未声称本轮重跑。

八项核心净收益采用冻结的多重比较校正区间。B 在 carbon_200k/P2 双后端和
water_400k/P8/HostStaged 上独立确认胜过同后端 full 与 HostStaged/full；
water/P8/CudaAware 仍慢于默认路径。C/B wait 增量仍未确认。
water/P4 有探索支持；carbon_1m、高重建和空 early 的结果限制外推，全部负结果保留。

完整区间、筛选依据、维护/内存/CPU、失败回退和一键复算见
[O9 独立确认](../status/performance/nep-candidate-validation.md#o9-results-20261029-report)；工具合同见
[分区标准](../standards/domain-partition.md#o9-独立确认工具合同)。
结论为 **CONFIRMED_SCOPED_CANDIDATE**，交 O11 全局采用复验；生产默认仍为
HostStaged/full、overlap=off、schedule=split。该 O9 阶段未执行 O10/O11；后续 O11 结果见下文，未自动采用。

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
## O11：限定范围采用复验已完成

状态：**COMPLETE_SCOPED（2026-10-30）**。按采样前冻结的范围收缩规则完成
800 个正式 trial（初始五块及一次追加五块）、80 个真实输入科学对照与 48 个 CPU
诊断。31,293 项封存文件及离线复算通过；原始慢样本全部保留。

仅建议 carbon_200k/P2 两后端手动 B-wait，相对当前 HostStaged/full 为
1.0210×/1.0199×；water/P8/HostStaged 区间仍跨 1，不建议采用；CudaAware 未胜
默认路径。water/P4 仍为探索支持，carbon_1m、高重建与空 early 不支持扩大范围。
未覆盖的 P1 及其他体系/rank/硬件组合不作性能采用建议，不声称完成原始全矩阵。

完整对照、CPU/维护代价、剩余不确定性及回退条件见
[O11 实测](../status/performance/nep-candidate-validation.md#o11-results-20261030-report)；已生效工具合同见
[分区标准](../standards/domain-partition.md#o11-限定范围采用复验工具合同)。
生产默认保持 HostStaged/full、overlap=off、schedule=split。默认变更、自动阈值
或范围外推广均不在本轮结论内。O10 暂缓，不作为本轮完成的前提。

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
