# O5 后续分析：拆分成本与下一优化切口（2026-10-21）

类别：已有实验与源码的补充分析。状态：**ANALYZED；微架构原因尚未定论**。
本记录未新增 GPU 实验、未改变生产二进制或默认配置。
证据来自 [O5 正式报告](communication-computation-overlap-o5-results-20261021.md)及其独立产物目录
`dmgmd-overlap-o5-20261016/`；原报告、冻结方案和交付快照保留原样。
当前生产 HEAD 为 `6b8efc505f19d539d4eaa916fe2c4793bf9b9c79`，O4/O5 主二进制 SHA-256
为 `860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6`。

## 模式和当前结论

一个 MPI rank 对应一张 **GPU**；CPU 负责提交 kernel、MPI 和运行时管理。
M2a 的跨 GPU owned/ghost 域分解在以下四模式下始终存在。

| 模式 | partition / overlap | 当前执行含义 |
| --- | --- | --- |
| full | off / off | halo 完成后执行本 rank 全部所需 NEP；不再拆本卡 early/late。不是每卡计算全局所有原子 |
| split-serial | serial / off | halo 完成后串行执行 early 和 late，用于单独测量拆分成本 |
| overlap-wait | serial / wait | 提交 halo，再提交独立 early，主线程直接 finish，随后 late |
| overlap-poll | serial / poll | 同上，但普通 halo 回调至多调用 8 次 progress，再 finish；不保证发生 8 次，也没有后台 progress thread |

early 的 interior 力中心和 late 的边界中心互补；依赖 producer 也互补，
不是把相同原子的力算两次。P1/M1、重建和空 interior 等 fallback 实际执行 full；
请求配置、执行计数和 fallback 原因必须一起读取。

O5 有效性能 trial 为主矩阵 600、历史 150、退化 120、独立确认 40，共 910。
carbon 两规模多数配置仍被分区成本拖慢。固定 CudaAware 的精确 water/P4/P8，
独立确认 wait 相对同后端 full 的净加速分别为 1.0627×、1.0927×；
四个 wait/poll 对比经过多重比较调整后的区间下界均大于 1。
同机 HostStaged/full 仍比这两个 CudaAware/wait 配置更快，故默认保持 HostStaged/full、off。
不能推广为所有 water、任意盒形或跨节点的收益。

## “多出 4.6 ms”到底测到了什么

代表配置为 carbon_200k（实际 N=196608）、P2、CudaAware，同一当前二进制，
每模式五个独立 trial。以下为主正式段、无详细诊断的 **ms/step 中位数**：

| full | split-serial | overlap-wait | overlap-poll |
| --- | --- | --- | --- |
| 16.499144 | 21.100090 | 20.173730 | 20.223550 |

拆分增加 `21.100090−16.499144 = 4.600945 ms/step`；
wait 相对 serial 回收 `0.926360 ms/step`，相对 full 仍多 `3.674585 ms/step`。
分区时间比约 1.279，wait 增量加速约 1.046，wait 净加速约 0.818。
这些是中位数之比/之差，区间及全部样本在 O5 的 `ratios.json`、`summary.json` 中，
不是把同一 trial 的步数或 rank 当作独立重复。

另跑的 200 步详细诊断将成本定位在 GPU 的过滤加 NEP 阶段：

| 诊断阶段，rank 均值 ms/step | full | split-serial |
| --- | --- | --- |
| cell/neighbor device | 0.790760 | 0.006708 |
| NEP device | 14.346914 | 19.740587 |
| 两阶段合计 | 15.137674 | 19.747295 |

两阶段合计相差 4.609621 ms。必须合并两列，因为 filter 在 full/split 中计时归属不同；
只比较 NEP 列会夸大增量。这些 CUDA event 是阶段包络，不是所有 kernel 独占时间之和；
不能与 host/MPI wall 相加，也不能用诊断精确拆账主样本的 4.600945 ms。

分类并非每步从头重新分区。同一 serial 诊断 rank0 为一次分类、199 次缓存命中；
分类约 0.996 ms/次，命中中位数约 0.00014 ms/次，摊入这 200 步约 0.005 ms/step。
主正式段 3100 步只有一次分类。这个量级不支持“每步分类耗掉 4.6 ms”的解释。

## 已知的机制与仍待验证的原因

源码 `src/gpumd_compat/nep.cu` 的 filter/products 在 early 和 late 各调用一次。
五类基础 NEP kernel 从一次执行的五个 launch 变为十个；当前依旧按完整中心域大小
launch，用掩码在未选中心处提前返回，粒子数组没有重排，L stride 不变。
因此数学上的中心工作互补，GPU 实际调度、掩码检查和访存效率却不保证互补。

O5 正常路径 Nsight 中一个 **冷启动三步短采集** 的 rank0/step2 给出如下候选线索。
它不是 full/serial 稳态配对，不进入主性能统计：

| kernel | early 实际 GPU ms | late 实际 GPU ms |
| --- | --- | --- |
| filter | 0.679563 | 0.511025 |
| descriptor | 5.081445 | 2.233435 |
| radial | 4.797709 | 4.243231 |
| angular partial | 2.210109 | 0.737769 |
| many-body | 0.280375 | 0.141980 |

该步 interior/owned=81920/98304=83.33%，early/dependency=90112/107008=84.21%。
early/late 的 radial 都为 1536 blocks × 64 threads；filter/descriptor/partial
都为 1672 blocks × 64 threads。late radial 中心数占比较小却仍执行较久，值得优先追踪，
但中心数不等于 FLOP 比例，也不足以证明因果。
该采集的 descriptor/partial 寄存器为 165/255 个每线程；这些数字本身不能证明
occupancy、spill 或带宽已经成为瓶颈。CUDA launch API 约为数微秒量级，
不能把 CPU API 时长当作全部 GPU launch 成本，更不能把 4.6 ms 全归给五个额外 API 调用。

完整 grid 重复调度、活跃线程效率、寄存器/occupancy、缓存及内存事务变化是
**待检验假设**。尚缺匹配稳态 full/serial/early/late 的硬件指标；
O6 专门负责这项归因。Nsight Compute 的 replay/cache control 会影响测量，
诊断方法参考 [NVIDIA Profiling Guide](https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html)。

若仅作条件算术，假定回收的 0.926 ms 完全不变，要胜过当前 full，拆分额外成本需从
4.601 ms 降到 0.926 ms 以下，即减少约 80%。优化后回收时间也可能改变，
这不是预测收益、严格通信隐藏上界或整步加速上界。

## 优化建议与优先级

| 方向 | 本次证据与建议 | 对应阶段 |
| --- | --- | --- |
| 明确 GPU 拆分主因 | 先解释 radial/descriptor/partial 的实际损失，再选一个执行优化 | O6 → O9 |
| 合并缓存判定归约 | 当前全局 MAX 的值只用于超阈值判断，再做 reason OR；有限值下可先判本地再 OR，需保持异常/阈值/空 rank 语义 | O7 |
| 按需 NVE thermo | 当前 M2a 每步执行 8-double 全局 thermo；审计消费者后才允许跳过无用辅助工作，保留 NVT 和规定输出 | O8 |
| 分别选择小归约与 halo 后端 | CudaAware 不保证更快；先看 O7/O8 后还剩多少机会，不能机械删同步 | 条件性 O10 |
| 相对新 full 复验 | full 变快会改变 overlap 的竞争基线，必须重新比较默认 HostStaged/full | O11 |

活动中心列表可能降低掩码执行损失，也会增加构建/维护成本，尚无收益证明。
CUDA Graphs 主要针对提交开销，现有证据不足以将其列为拆分 GPU 成本的首选解法。
更频繁 poll、增加 CPU 核数和 progress thread 没有本轮收益依据。
M2b/3D 属于不同协议/几何设计，不作为本次结论的自动后续。
各 goal 的范围、依赖、正确性与实测完成条件统一见
[O6–O11 计划](../../plans/communication-computation-overlap.md)。

## CPU、公平性与证据限制

O5 固定 K=4 物理核/rank、无 rank 间物理核共享，P1/2/4/8 使用一致策略，
启动后和运行中均有独立 affinity 核验；910 个有效性能 trial 绑定通过。
诊断 88 组/384 rank 行另有实际 GPU context 映射核验。
未锁频、未承诺机器独占，内存策略观察不证明物理页本地驻留。

主性能 C1 关闭且无 marker，正式段 CPU 为 null；诊断有效窗口的 rank core-equivalent
中位数约 1.007。CPU 时间无法分辨 MPI 忙轮询和有效计算，finish wall 也不是纯网络时间。
已有实际 GPU kernel/请求状态交集证据，但请求完成观察不等于物理传输完成；
精确 R 仍为 N/A。wait/poll 未体现可推广的差异，不据此增加轮询或线程。

## 复核入口

在仓库根目录执行 `bash dmgmd-overlap-o5-20261016/recompute.sh`，会核验工具与原始证据哈希，
重算 O5 全部统计。代表配置的主样本来自 `summary.json`（o4、carbon_200k、P2、CudaAware），
阶段表来自 `diagnostic-stages.json`（同配置、ordinary），短 trace 来自
`profiles/carbon_200k-p2-CudaAware-wait/trace.sqlite` 及 `profile-analysis.json` 的对应 witness。
本记录只是重新解读已有证据，不增加独立重复数或修改 O5 的冻结采用门槛。
