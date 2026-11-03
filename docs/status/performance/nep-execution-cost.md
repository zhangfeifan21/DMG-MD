# NEP 拆分执行成本定位与因果对照

目标：并列记录 O6 稳态成本定位与 O6.5 同快照 A–D 因果对照，区分已定位的执行位置、已测试的机制和仍未知的微架构原因。

| 日期 | 阶段 | 结论 | 适用范围与限制 |
| --- | --- | --- | --- |
| 2026-10-21 | [O6 稳态成本定位](#o6-results-20261021-report) | 无诊断整步结果及稳态 kernel 对照将主要新增 GPU 执行位置定位到 carbon radial。 | NCU 计数器权限失败；微架构因果 INCONCLUSIVE；结论限冻结输入、资源和本机。 |
| 2026-10-26 开始、10-27 完成 | [O6.5 拆分执行因果对照](#o65-results-20261027-report) | 真实图/mask 与同快照 A–D 重放显示紧凑映射降低两类目标 kernel 耗时，达到进入独立整步试验的门槛。 | 仍慢于 A；未接入生产流水线，整步净收益 NOT_TESTED/INCONCLUSIVE；微架构原因未知。 |

以下各节按来源报告分别保留原始身份、环境、实际命令、统计数据、失败与未覆盖边界及原始产物路径；标题层级仅为嵌入整合文档而调整。

<a id="o6-results-20261021-report"></a>

<a id="o6-results-20261021-o6稳态拆分成本定位2026-10-21"></a>

## O6：稳态拆分成本定位（2026-10-21）

类别：独立受控诊断与无诊断复验。状态：**成本位置已定位；微架构原因 INCONCLUSIVE**。
O6 的工具、正确性与墙钟实验已完成；不把计数器受限解释成已经找到精确硬件原因。
生产 NEP、通信协议与默认 HostStaged/full、overlap=off 均保持原状。

carbon_200k/P2/CudaAware 的新五重复墙钟确认拆分增加 **4.630 ms/step**。
三轮稳态 CUPTI 将主要损失定位到 **radial kernel**：full 约 4.589 ms，
early+late 约 8.479 ms，增加 3.890 ms。五类 kernel 的总增量约 4.728 ms；
radial 约占这个**独立诊断增量**的 82%，不能据此精确拆账正式墙钟的 4.630 ms。
water/P4/P8 的拆分也增加 GPU 成本，但 wait 的独立无诊断净加速仍约为 1.060×/1.089×。

<a id="o6-results-20261021-身份资源与采集方案"></a>

### 身份、资源与采集方案

起始 HEAD：`cf6d098e3f12b25fddafb62cc3e95365e8f87425`；起始仓库与三个 reference
的 Git 状态干净。修改范围为诊断/统计工具、测试和文档；没有重新编译生产程序。
复用并复制 O4/O5 已验收可执行文件，其 SHA-256 为
`860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6`。
最终 patch、源码文件与哈希随独立目录 `dmgmd-overlap-o6-20261021/` 保存；没有提交或推送。
O1–O5 样本和 reference 均不写入。

环境仍为 n4、AMD EPYC 7K62、八张 RTX 4090、driver 580.173.02、CUDA 12.9、
Open MPI 5.0.10/UCX 1.22.0。唯一环境入口是 `source ../env/md-mpi.sh`。
Nsight Systems 2025.1.3.140，Nsight Compute 2025.2.1.0。
每 rank 固定四物理核、含 SMT 的八个 OS CPU，不共享物理核；GPU UUID 与
CPU/NUMA 分配沿用 [O5 映射](communication-computation-overlap-results.md#o5-20261021-冻结设计身份与公平性)，
P2/P4 取 P8 前缀。完整 UUID、拓扑、实际命令、affinity、资源与遥测在各 trial 中。
没有锁频、独占预约或物理页本地驻留证明；主样本正式段 CPU 为 null。

`frozen-plan.json` 在采集前固定输入/二进制/环境/库/工具身份、完整随机顺序、
资源、统计与失效规则。无诊断顺序 seed=2026102106，诊断 seed=2026102107，
配对 bootstrap seed=2026102108。128 步匹配短控制另以 `matched-control-plan.json`
在采集前冻结，seed=2026102109，不并入主统计。

| 输入 / ranks | N | warmup | 主正式步数 | Nsight 正式窗口 |
| --- | ---: | ---: | ---: | ---: |
| carbon_200k / P2 | 196608 | 200 | 1500 | 128 |
| water_400k / P4 | 393216 | 1600 | 4300 | 128 |
| water_400k / P8 | 393216 | 1600 | 4300 | 128 |

三组都比较 CudaAware 的 full、split-serial、overlap-wait；每组每模式五个新主重复、
三个新 Nsight 重复。主正式段实际为 **24.456–40.033 s**，均达到 ≥10 s 门槛。
比值是中位数之比，95% 区间保留重复块配对、20000 次 bootstrap，属于点估计的探索区间。
没有按速度删样本，区间未触发 >10% 宽度的追加规则。

<a id="o6-results-20261021-正确性与采集有效性"></a>

### 正确性与采集有效性

- 八 rank 环境预检通过，包含 CUDA-aware capability、device collective/p2p 和 GPU 唯一映射；
  单 rank committed golden 四 case 通过。
- 实际输入的三配置 × full/serial/wait/verify-wait 共 **12/12** 科学门槛通过；
  verify 保留逐 bit oracle/poison 检查。
- **45/45** 正式 trial 的退出、绑定、实际执行模式、计数和科学文件检查通过；
  thermo/neighbor 与同重复块的 full 逐字节一致。
- **27/27** Nsight 采集与正式窗口匹配通过，科学文件与同窗口 full 一致。
  carbon 每 rank 每轮取 128 个普通步；water 每 rank 每轮取 124 个普通步，另四步重建排除。
- **27/27** 同输入/预热/128 步的无诊断短控制通过，科学文件与对应 Nsight trial 一致。
  这些短校准不足 10 s，明确不计入主正式性能。
- CPU CTest **12/12** 通过；O6 分析/证据校验的 **11 项**单测通过。
  生产二进制未变，沿用 [O4 同 SHA 的 long_nve smoke](../validation/communication-computation-overlap.md#overlap-o4-20261016)，
  本轮没有声称重跑 nightly 或 100000-step release。

两项 NCU 尝试因计数器权限为 INCONCLUSIVE；其余相同 GPU0 的 17 项按冻结规则
记 NOT_RUN。它们是诊断能力缺项，不计入性能或生产科学失败样本。

<a id="o6-results-20261021-无诊断整步结果"></a>

### 无诊断整步结果

单位 ms/step，中位数。最后一列 >1 表示 wait 比同后端 full 更快。

| 配置 | full | serial | wait | serial/full，95% CI | full/wait，95% CI |
| --- | ---: | ---: | ---: | --- | --- |
| carbon/P2 | 16.381870 | 21.011742 | 20.167935 | 1.282622 [1.279865, 1.290919] | 0.812273 [0.804688, 0.816327] |
| water/P4 | 8.522567 | 9.306070 | 8.042997 | 1.091933 [1.090222, 1.094452] | 1.059626 [1.057122, 1.061913] |
| water/P8 | 6.421340 | 7.224365 | 5.896438 | 1.125056 [1.119021, 1.127338] | 1.089020 [1.087680, 1.089020] |

carbon 的 serial→wait 中位数差为 0.844 ms；相对 full 仍多 3.786 ms。
这复现了 [O5 的 4.60 ms 代表配置](communication-computation-overlap-results.md#o5-cost-20261021-report)的方向。
water 的拆分 GPU 工作同样变慢，净收益来自整个调度；不能用 kernel 总和预测通信隐藏量。
本轮没有重新比较 HostStaged，不能替代 O5 的默认后端采用结论。

<a id="o6-results-20261021-稳态实际-kernel-对照"></a>

### 稳态实际 kernel 对照

采集 `nsys profile --trace=cuda,mpi --mpi-impl=openmpi --sample=none --cpuctxsw=none
--cuda-event-trace=false`。从同一个 report 的 CUDA activity 顺序定位每步：
cache inspection 前的第一半 Verlet 至本步 thermo normalize；匹配逐 rank 的正式步号，
排除每段初始 setup force 和重建 fallback。五类 pipeline 必须完整且有序，full 为五个
launch，普通 split 为 early/late 两组十个 launch。没有拼接 host 与 Nsight 时钟。
未发现缺失目标 launch；这不能证明工具级丢失或时钟误差的独立上界为零。

以下每个数先取 trial 内普通步的 rank 均值，再取三个独立 trial 的中位数。
early+late 先在 trial 内相加；不把步或 rank 当作独立重复。单位 ms/ordinary step。

| kernel | carbon full | carbon serial E+L | carbon wait E+L |
| --- | ---: | ---: | ---: |
| filter | 0.776991 | 1.121725 | 1.122665 |
| descriptor | 6.678235 | 6.862485 | 6.856322 |
| radial | 4.589316 | 8.479222 | 8.506198 |
| angular partial | 2.620110 | 2.799012 | 2.789541 |
| many-body | 0.310925 | 0.408285 | 0.408546 |

五类独占 kernel 时间**之和**的 trial 中位数：full **14.967238**，serial **19.695549**，
wait **19.721941**；三个 trial 的范围分别为 [14.966515,15.224019]、
[19.657267,19.731860]、[19.653151,19.847446]。分项中位数的和不必等于总和的中位数。
这比 O5 冷启动三步 trace 更直接地支持“radial 为主要增加位置”，仍不等于无诊断墙钟拆账。

| kernel | water/P4 full | P4 serial E+L | P4 wait E+L | water/P8 full | P8 serial E+L | P8 wait E+L |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| filter | 0.401328 | 0.526051 | 0.523881 | 0.208298 | 0.260683 | 0.258810 |
| descriptor | 1.348313 | 1.368109 | 1.373811 | 0.866448 | 0.865884 | 0.866653 |
| radial | 0.499008 | 0.828407 | 0.824712 | 0.305661 | 0.548299 | 0.544734 |
| angular partial | 1.032396 | 1.255442 | 1.257484 | 0.573759 | 0.934983 | 0.934778 |
| many-body | 0.275141 | 0.312681 | 0.313168 | 0.120172 | 0.174527 | 0.174653 |

P4 五类总和 full/serial/wait 为 **3.556409/4.289975/4.292340**，
P8 为 **2.075572/2.784803/2.779267**。P8 partial 增加约 0.361 ms，大于 radial
的约 0.243 ms；carbon 的“radial 主导”不能直接推广。现有 water 核对已提供这个反例，
没有再新增 carbon_1m 或重跑 O5 全矩阵。

<a id="o6-results-20261021-匹配短控制与诊断扰动"></a>

### 匹配短控制与诊断扰动

完全匹配 Nsight 输入、预热和 128 步的独立无诊断控制，每模式三个新进程。
单位 ms/step，中位数；每项都逐文件核对与对应 Nsight trial 的科学输出。

| 配置 | 无诊断 full / serial / wait | 诊断 full / serial / wait | 诊断/控制 full / serial / wait |
| --- | --- | --- | --- |
| carbon/P2 | 16.576516 / 21.001662 / 20.325985 | 16.821639 / 21.569265 / 20.526076 | 1.0148 / 1.0270 / 1.0098 |
| water/P4 | 8.846265 / 9.587218 / 8.323073 | 9.670589 / 10.316040 / 8.656069 | 1.0932 / 1.0760 / 1.0400 |
| water/P8 | 6.897351 / 7.571120 / 6.206658 | 7.651036 / 8.360923 / 7.038674 | 1.1093 / 1.1043 / 1.1341 |

观测到约 1%–13% 的诊断扰动；三个配置的 full/serial/wait 快慢方向均未改变。
这是详细 timing/trace 加 profiler 的对照，不是纯 Nsight 因果开销；阶段未交错且频率
未锁定，也不以 n=3 给 95% 区间。短窗口与长正式段的重建/段边界摊销不同，二者不混合。
诊断墙钟会夸大或缩小净加速，采用/收益数字始终使用前述五重复无诊断长正式段。

<a id="o6-results-20261021-分类launch同步与通信分别记账"></a>

### 分类、launch、同步与通信分别记账

carbon 的 interior/owned=81920/98304=83.33%，early/dependency=90112/107008=84.21%。
full、early、late 的 launch 属性一致：

| kernel | blocks × threads | registers/thread |
| --- | --- | ---: |
| filter | 1672 × 64 | 40 |
| descriptor | 1672 × 64 | 165 |
| radial | 1536 × 64 | 64 |
| angular partial | 1672 × 64 | 255 |
| many-body | 1536 × 64 | 62 |

源码证据是 `src/gpumd_compat/nep.cu` 的 `NEP::compute_domain`、`filter/products`
以及各 kernel 的 selection/complement guard；未选中心提前返回，但 grid 不收缩。
这些是实际静态 launch 属性，不能据此断言 achieved occupancy、spill 或缓存瓶颈。

下面是 carbon 三轮诊断的 trial-rank 均值中位数，单位 ms/ordinary step，**各列不相加**：

| 观察 | full | serial | wait |
| --- | ---: | ---: | ---: |
| 五类 kernel 的 CPU launch API 合计 | 0.023822 | 0.049524 | 0.048129 |
| 分类 host（含 cache miss 等待） | N/A | 0.007925 | 0.008003 |
| halo MPI_Waitall host | 1.178219 | 1.171369 | 1.302863 |
| CudaAware output sync host | 0.002454 | 0.002482 | 10.999032 |

每 rank 的 128 普通步只有一次分类、127 次命中，不能解释约 4.6 ms 的逐步增加。
额外五个 CPU launch API 的合计增加约 0.026 ms，也不支持将全部损失归给五次 API 本身。
wait 的 output sync 包含 early GPU 完成等待，MPI_Waitall 也是 host 观察区间，
均不能当成纯网络或额外独占损失。没有把这些区间与 GPU kernel 时间相加。

carbon 三模式的普通步均为每 rank 430080 halo-send bytes、三次 collective；
water/P4、P8 的窗口 rank 均值分别约为 668432、668748 bytes，三模式一致，collective 也均为三次。
water 每 rank 四次分类、120 次命中；分类 host 约 P4 0.012 ms、P8 0.008 ms/ordinary step。
原始逐步通信量、其他 GPU kernel、独立 early/late 和各 rank 的误差范围在 CSV/JSON 中保留。

<a id="o6-results-20261021-硬件原因限制与-o9-决策"></a>

### 硬件原因、限制与 O9 决策

NCU 为 rank0 的正式第 64 步，根据完整 Nsight trace 冻结匹配五类 kernel 的
launch skip/count；预设 kernel replay、cache-control=none/all、clock-control=none，
请求 LaunchStats、Occupancy、SchedulerStats、WarpStateStats、MemoryWorkloadAnalysis、
SourceCounters。首个 carbon/full/cache-none 实际返回 **ERR_NVGPUCTRPERM**；
一次仅 LaunchStats 回退也被拒绝。驱动 `RmProfilingAdminOnly=1`，未修改驱动权限。
后续 17 个相同 GPU0 的项目记 NOT_RUN，**没有形成 cache-control=all/none 的有效对照**。

有效线程/warp、achieved occupancy、访存事务/缓存命中、scheduler issue/stall 均不可用。
即使将来能采集，kernel replay 的内存恢复、同步和缓存控制也会改变运行；none 也不等于
完全无扰动，NCU 时长不能替换无诊断墙钟。本轮微架构因果结论为 **INCONCLUSIVE**：

- 支持：主要新增 GPU 执行位置是 carbon radial；完整 grid/掩码与两次 launch 是实际机制。
- 不支持：每步分类、五个额外 CPU API 本身解释全部增加；统一用 carbon radial 解释 water/P8。
- 未知：活跃 warp 分布、延迟隐藏、缓存/事务或调度中各项的因果份额；精确分解主墙钟增加。

**O9 当前决定：NO_ADOPTION，不选择一个尚无因果证据的 NEP 执行改动。**
radial 的 late 工作少而耗时长值得继续追踪，但“把完整 grid 改为中心列表”能减少多少
实际成本尚未被证明，列表维护和 launch 变化也有代价。静态寄存器数相同不能证明根因，
因此本次不以此为由启动 kernel 改写。O9 若重新开启，应先取得可用的硬件指标或更直接的
单因素执行证据，再冻结一个候选。O7/O8 仍可独立推进；本轮未实现它们。

<a id="o6-results-20261021-原始产物与复算"></a>

### 原始产物与复算

独立目录：`dmgmd-overlap-o6-20261021/`，不与 O1–O5 混合。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o6.py freeze --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/run_overlap_o6.py gates --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/run_overlap_o6.py profiles --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/run_overlap_o6.py main --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/analyze_overlap_o6.py --root dmgmd-overlap-o6-20261021 --profiles-only
python3 tests/benchmark/profile_overlap_o6_ncu.py --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/run_overlap_o6_controls.py --root dmgmd-overlap-o6-20261021
```

以上为实际阶段入口；每个底层 argv/显式环境在 launch、profiler-command、export-command
或 gate command JSON 中保存。新机器必须使用新目录并先核对固定资源与旧二进制身份。
离线复算（含原始证据 SHA-256 校验，不要求 GPU）：

```bash
bash dmgmd-overlap-o6-20261021/recompute.sh
```

关键入口为 `frozen-plan.json`、`gates.json`、`wall-analysis.json`、`kernel-trials.json`、
`kernel-steps.csv`、`kernel-summary.*`、`summary.md`、`matched-control-analysis.json`、
`ncu-plan.json`、`ncu-analysis.json`、`all-attempts.json` 和 `raw-evidence-inventory.json`。
源码快照、patch、输入/二进制、绑定、遥测、rank 原始日志、`.nsys-rep`/SQLite 全部保留。
完整各阶段状态以 `analysis-status.json` 为准；性能无收益和硬件 INCONCLUSIVE 均不改写为 PASS。
全部原始尝试共 113 项：111 PASS、两项 NCU INCONCLUSIVE；没有把未运行的 NCU 项算成有效尝试。

---

<a id="o65-results-20261027-report"></a>

<a id="o65-results-20261027-o65拆分执行的因果对照2026-10-2710-26-开始"></a>

## O6.5：拆分执行的因果对照（2026-10-27；10-26 开始）

类别：独立受控执行诊断与无诊断整步对照。状态：**COMPLETE：实现与正确性 PASS，必要执行对照完整；生产采用 NONE**。
当前 O7+O8 的 carbon radial / water angular partial 拆分损失已用新 trace 和同快照 A–D
对照验证；紧凑映射在两体系均降低真实互补 mask 的目标 kernel 耗时，支持进入独立整步实验；具体微架构原因仍 INCONCLUSIVE。紧凑执行目前处于诊断阶段，生产采用与整步净收益仍待独立验证。
生产默认继续为 HostStaged/full、overlap=off。

<a id="o65-results-20261027-身份冻结设计与测量范围"></a>

### 身份、冻结设计与测量范围

起始 HEAD：`33c7b7f41b9ea5e2d374e915614fbf5b41410fd7`，起始源码工作树干净。
三个只读 reference 的 HEAD、Git 状态及原有 ignored 文件列表在工作前后逐项相同；
未提交或推送。源码、独立二进制、输入与工具/库 SHA-256、完整命令/环境、K=4
CPU/NUMA 映射、GPU UUID、遥测及所有原始尝试保存于本机独立目录
`dmgmd-overlap-o65-20261026/`；`raw-inventory.json` 封存原始证据，`final.patch`
和 `final-source.json` 描述最终工作树。O1–O9 既有样本没有改写或混用。

| 二进制 | SHA-256 | 用途 |
| --- | --- | --- |
| 当前 O7+O8 基线 | `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32` | 新 full / 原 split-serial / 原 wait 正式墙钟及稳态 trace |
| 隔离诊断 runtime | `49116d0f8ab97cb0e41709138e4cf72c1a9200be999ffbed208d26befee173aa` | 同普通步快照导出与受影响数值门槛 |
| 独立重放 | `e92d71f95bc49cbd623318a05ecdc5c36b2008c01a87b9f48cd501d9278f04d7` | A–D 目标 kernel 与最终 consumer 逐 bit 验证 |

基线与已验收 O8 二进制完全相同。复用旧实验驱动的内部版本键 `o4` 及同名 symlink
仅指向上述当前基线，不是历史 O4/O6 二进制。唯一环境入口为
`source ../env/md-mpi.sh`；n4、AMD EPYC 7K62、八张 RTX 4090、driver 580.173.02、
CUDA 12.9、Open MPI 5.0.10 / UCX 1.22.0、Nsight Systems 2025.1.3.140、
Nsight Compute 2025.2.1.0。每 rank 固定四物理核及其八个 SMT OS CPU，P2 使用
P8 的相同 GPU UUID/CPU 前缀。独立重放也核对实际 UUID 与 taskset affinity。
未锁频、非独占；内存页本地驻留 UNKNOWN；正式段 CPU 为 null。
两输入分别为 196608 个 carbon 原子和 393216 个 water 原子，步长均为 0.1 fs；
model/potential 与已验收 O8 的输入哈希一致，没有替换更小体系。

`frozen-design.json` 在采集前冻结 scope、独立重复、随机种子、pilot/诊断/正式区分、
统计及无效/追加规则；pilot 后 `main-plan.json` 冻结共同正式步数。
顺序 seed=2026102664/65/66，配对 bootstrap seed=2026102667，重放 seed=2026102668。
比值采用 trial 中位数之比、保留块配对的 20000 次 bootstrap 点态 95% 区间。

| 范围 | 模式 | 预热步 | 正式步/trial | 独立块 |
| --- | --- | --- | --- | --- |
| carbon_200k/P2 | full / split-serial / overlap-wait | 200 | 1600 | 5 |
| water_400k/P8 | full / split-serial / overlap-wait | 1600 | 4100 | 5 |

共 30 个无诊断正式 trial，每配置至少五个独立进程配对块，正式段
23.672–33.807 秒，均达到 10 秒门槛。六个比值的区间宽度/点估计均不超过 10%，未触发追加。
额度中断的原 block 0 四个完整 trial 和一个未完成尝试全部保留但整块不进入统计；
在恢复前以 seed=2026102766 冻结一个完整替换块，其余四块不变，见 `main-recovery-plan.json`。
18 个独立 Nsight trial 属于三轮诊断，不进入正式墙钟；每轮 carbon 的 128 个普通步、
water 的 124 个普通步参与 kernel 统计，water 的四个重建步与 setup 分开。

<a id="o65-results-20261027-新基线墙钟与实际-gpu-增量"></a>

### 新基线墙钟与实际 GPU 增量

| 范围 | n/配置 | full ms/step | serial | wait | serial−full | wait−full |
| --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/P2 | 5 | 16.370515 | 20.936521 | 20.136519 | 4.566007 | 3.766004 |
| water_400k/P8 | 5 | 6.317208 | 7.159041 | 5.780147 | 0.841833 | -0.537062 |

差值为配置中位数之差；配对比值及区间如下（大于 1 表示分子更慢）：

| 范围 | 比值 | 估计 [95% CI] |
| --- | --- | --- |
| carbon_200k/P2 | split-serial/full | 1.2789 [1.2779, 1.2880] |
| carbon_200k/P2 | split-serial/overlap-wait | 1.0397 [1.0336, 1.0509] |
| carbon_200k/P2 | full/overlap-wait | 0.8130 [0.8088, 0.8160] |
| water_400k/P8 | split-serial/full | 1.1333 [1.1312, 1.1333] |
| water_400k/P8 | split-serial/overlap-wait | 1.2386 [1.2358, 1.2418] |
| water_400k/P8 | full/overlap-wait | 1.0929 [1.0905, 1.0976] |

本轮正式墙钟均为 CudaAware；compact 的 HostStaged/full 竞争对照留给后续整步试验。

下表为 CUDA activity 的独占 kernel 时长：先取 trial 内普通步的 rank 均值，再取
三个独立 trial 的中位数，单位 ms/ordinary step。early+late 在 trial 内先相加；
分项中位数不能直接相加解释墙钟。实际 O8 日程通过 cache inspection 到本步 VV2
匹配，不要求每步 thermo，也没有复用旧 launch-skip 或拼接 host/device 时钟。

| 范围 | kernel | full | serial early+late | wait early+late | serial−full |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/P2 | filter | 0.783887 | 1.121349 | 1.124430 | 0.337462 |
| carbon_200k/P2 | descriptor | 6.689744 | 6.879584 | 6.873490 | 0.189840 |
| carbon_200k/P2 | radial | 4.719340 | 8.436136 | 8.506637 | 3.716796 |
| carbon_200k/P2 | angular partial | 2.625474 | 2.794920 | 2.802871 | 0.169446 |
| carbon_200k/P2 | many-body | 0.312211 | 0.404898 | 0.404105 | 0.092687 |
| water_400k/P8 | filter | 0.208573 | 0.260594 | 0.258712 | 0.052021 |
| water_400k/P8 | descriptor | 0.866320 | 0.865056 | 0.866621 | -0.001263 |
| water_400k/P8 | radial | 0.299776 | 0.546971 | 0.544285 | 0.247195 |
| water_400k/P8 | angular partial | 0.573657 | 0.933851 | 0.934430 | 0.360194 |
| water_400k/P8 | many-body | 0.120393 | 0.174524 | 0.174744 | 0.054130 |

carbon 的主要新增位置仍为 radial，water/P8 的 angular partial 增量更大。
本轮墙钟和 profiler 环境与历史 O6 不同，以上新结果独立解释，不强行对齐历史 4.630 ms。
CPU launch API、分类、同步和 MPI 区间均保存在 `kernel-trials.json`，不与 GPU 时间相加。

<a id="o65-results-20261027-真实图mask-与工作量"></a>

### 真实图、mask 与工作量

按新 trace 冻结 carbon step=216/call=217，water step=1616/call=1617。
carbon rank0 的 layout/Verlet/workspace generation=1/3/1，O/D/L=98304/107008/116224；
water rank0 为 48/144/48、49143/62128/77052。所有十个 rank 的具体值和原始数组见
`capture-plan.json`、`snapshots/`、`snapshot-analysis.json`，不把 rank0 代表所有 rank。

快照只在本步 halo 和完整 force/producer 物理完成后导出。导出器在同一真实 V/gid/key
上使用独立 buffer 生成等价 I/E 副本；生产 mask 只有 prepare 分类器写入，key 内的 V/gid
不变，分类为确定性的布尔闭包。复算从保存的真实 Verlet 图独立推导 CPU 闭包，并核对
生产日志的 I/E 数量、实际 filtered 邻居行、gid 顺序、O/D/L 与三类 generation。
位置、Fp/sums、有效 partial 及 slot→gid 均保存；没有重排生产粒子或压缩物理数组。

| 范围 | 相位 | 中心数 | 中心 % | 非空 warp % | 非空 block % | radial 边数 | angular 边数 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/P2 | I | 81920 | 83.333 | 83.333 | 83.333 | 22446080 | 3768320 |
| carbon_200k/P2 | B | 16384 | 16.667 | 16.667 | 16.667 | 4489216 | 753664 |
| carbon_200k/P2 | E | 90112 | 84.211 | 84.211 | 84.211 | 24690688 | 4145152 |
| carbon_200k/P2 | H | 16896 | 15.789 | 15.789 | 15.789 | 4629504 | 777216 |
| water_400k/P8 | I | 24572–24690 | 49.998–50.237 | 56.836–58.529 | 60.807–63.151 | 2164788–2176064 | 662657–665048 |
| water_400k/P8 | B | 24457–24574 | 49.763–50.002 | 55.404–57.682 | 58.854–62.109 | 2155511–2167837 | 659964–663072 |
| water_400k/P8 | E | 36731–36797 | 59.131–59.210 | 62.738–63.562 | 65.432–66.152 | 3237537–3246530 | 990297–993179 |
| water_400k/P8 | H | 25342–25405 | 40.790–40.869 | 46.372–47.425 | 49.588–50.978 | 2236275–2241734 | 683985–685642 |

warp/block 分别以 32/64 个真实 lane 为一组；尾部 padding 为 inactive。原始 JSON
保存每个相位的全空/非空/全满/混合计数、active-lane 直方图、径向/角向邻居数直方图及
总边数。中心比例与执行覆盖分别列出；静态覆盖不是 achieved occupancy 或访存事务。
carbon 四相均无 mixed warp/block，I/B 的非空覆盖为 83.333%/16.667%，E/H 为 84.211%/15.789%。water 的 E/H 非空 warp 为 62.738–63.562%/46.372–47.425%，block 为 65.432–66.152%/49.588–50.978%。若把“大量”明确为每相在每 rank 都超过原 grid 的一半，两个目标的双相假设均为 REJECTED；这并不否定 water 的混合组与额外覆盖。尤其不能用“两相几乎全部 warp 都做有效工作”统一解释 carbon 的损失。
例如 carbon rank0 的 I/B 非空 warp 为 2560/512（原 grid 3072），block 为 1280/256
（原 grid 1536）；water rank0 的 E/H 为 1230/906 warp（原 grid 1942）、641/489 block
（原 grid 971）。

<a id="o65-results-20261027-同快照-ad-对照"></a>

### 同快照 A–D 对照

所有 producer 就绪后，以相同 L stride、原 slot、原邻居行及每中心表达式/累加顺序执行：
A 为 null selection/完整 grid；B 为全选 mask/同一完整 grid；C 为真实互补 mask/
两次完整 grid；D 为同一互补中心的升序索引/对应 grid。D 只改 thread→center 映射及
相应 launch，C/D 均使每个中心恰好执行一次。D 的 selection=null，由索引列表表达选择；C/D 差异包括该执行映射组合。

十个冻结 rank 快照各做五个独立进程重复，共 50 个进程；每个进程随机排列四变体，
每变体预热 20 次、记录 100 次。每次先在 event 外恢复同一输出初值，event 只包围
目标 kernel(s)；重放准备、索引构建、恢复和 consumer 验证排除。先聚合进程内重放，
再聚合 rank 成一个 block 样本；20000 个 event 读数不是 20000 个独立重复。

| 目标范围 | A ms | B ms | C ms | D ms |
| --- | --- | --- | --- | --- |
| carbon_200k/P2 | 4.645334 | 4.656727 | 8.408700 | 5.910817 |
| water_400k/P8 | 0.594959 | 0.599060 | 0.946916 | 0.830280 |

| 目标范围 | A/B [95% CI] | C/A [95% CI] | C/D [95% CI] | D/A [95% CI] |
| --- | --- | --- | --- | --- |
| carbon_200k | 0.997554 [0.983322, 1.000040] | 1.8101 [1.7987, 1.8190] | 1.4226 [1.4043, 1.4422] | 1.2724 [1.2613, 1.2898] |
| water_400k | 0.9932 [0.9800, 1.0218] | 1.5916 [1.5811, 1.6112] | 1.1405 [1.1179, 1.1661] | 1.3955 [1.3656, 1.4274] |

A–D 均逐 bit 通过目标结果、有效 angular partial、最终 force/全部九个 virial、PE
以及 Fp/sums 只读检查。partial 验证运行原 radial 和 many-body consumer；radial 验证
使用冻结的完整 partial 后运行 many-body。未比较未使用的 ELL 尾部；坐标与输入只读。
该最终 consumer 重放只验收这两个无 ZBL NEP4 fixture，不据生产路径的广泛回归扩大
compact 映射的模型适用范围。

两份独立短 CUPTI 采集核对实际 64-thread block、完整/compact grid、模板实例与 launch
数量；每份有 12 个完整 grid 目标 launch、6 个 compact 目标 launch（包括独立数值
校验及短预热/计时）。这些短采集只验证执行，均不作为性能样本。

<a id="o65-results-20261027-维护成本假设与候选交接"></a>

### 维护成本、假设与候选交接

| 目标范围 | 冷构建主机 ms 中位数 [min,max] | 逻辑设备 bytes | 物理设备 bytes | host 索引 capacity bytes | key 查询均值 μs 范围 |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/P2 | 0.801629 [0.781717, 0.822694] | 393216 | 491528 | 589824 | 0.0520–0.0577 |
| water_400k/P8 | 0.524253 [0.487835, 0.564319] | 248456–248668 | 310572–310840 | 393216 | 0.0513–0.0603 |

构建成本包括阻塞 mask 下载、CPU 升序索引构建、分配和上传，kernel event 不包含它。
上表均为单 rank 的离线原型，冷构建范围汇总 carbon 的 10 次、水的 40 次观测，只作描述统计。
该主机区间的阻塞下载还可能等待此前同 stream 的准备工作（含全选 mask 初始化），不是纯索引指令时长。
物理设备字节只计两个索引的 GPU_Vector capacity，另保留逻辑 payload 和 host index capacity。
离线 mask 副本、完整快照及验证工作区不包含在索引字节内；生产集成的总内存高水位未测。
七类 generation/shape/box 失效检查通过；同 shape gid 重排必须增加 layout generation。
固定快照的 key 查询墙钟包含时钟开销，固定相等 key 也可能受编译优化影响；只是离线原型观测，不代表生产 CPU 归因。
相对现有 split 可以复用其 mask；相对 full 还需要分类及生命周期维护，未实现的生产
集成成本不能据此记零，更不能从 CPU 墙钟减 GPU event 得出净收益。

| 假设 | 结论 | 证据范围 |
| --- | --- | --- |
| 新稳态主要新增位置仍为 carbon radial / water angular partial | SUPPORTED | 三个独立块的 split−full 配对 kernel 增量 |
| 互补两相在每个 rank 都覆盖超过半数原 warp/block | REJECTED | 上表真实图静态覆盖；carbon boundary 仅 16.667%，water H warp 不到一半 |
| 全选 mask guard 单独产生可辨认的正开销 | INCONCLUSIVE | 两体系 A/B 区间均包含 1 |
| 紧凑映射降低 C 的目标 kernel 时间 | SUPPORTED | 两体系 C/D 区间下界均大于 1；按冻结筛选准则进入独立整步实验 |
| 紧凑映射已恢复完整执行 A 的成本 | REJECTED | D/A 为 1.2724 / 1.3955，区间均大于 1 |
| 特定 occupancy、cache、访存事务或 scheduler 原因已确定 | INCONCLUSIVE | 新普通步 NCU rank0/step264、launch-skip1325 的唯一探测被 ERR_NVGPUCTRPERM 拒绝 |
| compact 已有整步净收益 | INCONCLUSIVE（NOT_TESTED） | 本轮未在生产流水线接入 compact；原 full/serial/wait 墙钟不能替代候选墙钟 |
| 重放差值精确分解原流水线/历史墙钟 | REJECTED | producer、缓存、输出恢复、并发和计时边界不同 |

交给 O9 的两个独立方向是 carbon radial 的升序中心索引映射和 water angular partial 的同类映射。相对 C 的重放目标耗时分别下降约 29.71% 和 12.32%；两者通过预冻结的 C/D 区间下界大于 1 门槛。它们仍分别比 A 慢约 27.24% 和 39.55%，仅支持试验资格，不支持采用。接入时须独立验收 mask/index 生命周期、同 shape 重排、空/尾中心、模型和 rebuild fallback、分类/构建/失效的摊销成本、内存高水位及实际 launch 覆盖；不能把两个方向直接合成一个无法归因的补丁。

[O8.5](../../plans/communication-computation-overlap.md#o8-5) 仍按原计划先验证只拆 filter/descriptor、完整执行力计算；它不以 compact 有收益
或取得硬件计数器为前提。O9 必须单独集成有证据的候选，使用未参与选择的新配对块
确认整步成本、维护与失败回退，并比较同后端 full 和 HostStaged/full。
本轮没有改变生产调度、默认后端、overlap 开关、NEP 数学/精度、halo 字节或所有权。

<a id="o65-results-20261027-正确性复算与限制"></a>

### 正确性、复算与限制

- 当前基线：八 rank 环境预检、4/4 committed golden、两真实输入四模式共 8 个科学作业通过输出对照；输入和当前二进制与已验收 O8 的桥接哈希一致。
- 独立诊断 runtime：八 rank 环境、4/4 golden、沙箱外 GPU CTest 17/17（无 skip）通过；MPI P2/P4、两后端、off/verify-wait 主矩阵 136 配置及 focused cache/timing/error 检查通过。
- 原 nightly manifest 完整七 case × P1/2/4 × 两后端共 42/42 配置、12/12 跨 rank restart 通过，包含 NEP5、typewise cutoff、固定/柔性 ZBL、NVE/NVT；572 个执行 stage 均在第一次尝试通过，未放宽容差。
- 新 trace 18/18、普通步快照 10/10、独立重放 50/50、短 CUPTI 2/2 通过；所有有效正式 trial 的同窗口 thermo/neighbor 文件逐字节相同，实际绑定、配置、诊断关闭和正式段时长验证通过。
- 采集器修正后的 8 个 O6.5 Python 单测通过；完整封存后两次离线重算通过，其中一次使用产物内冻结分析依赖。

现行诊断合同见[分区标准](../../standards/domain-partition.md#o65-隔离快照重放)，
工具操作见 [benchmark README](../../../tests/benchmark/README.md#o65-mask-与紧凑执行因果实验)。

`DMGMD_ENABLE_O65_DIAGNOSTICS` 默认为 OFF。条件块去除后，两份 NEP 源文件与原
HEAD 除空白外相同，证据为 `default-source-proof.json`；生产路径没有紧凑映射分支。
构建和启动前失败保留于原始日志；首份短 trace 的 `shortName` 省略模板参数，原模板分类器因此停止；修正为 `mangledName` 的 bool 参数后复用同一原始 SQLite，并保留第一次失败日志。`collector-amendment.json`、源码副本及 patch 绑定该元数据修正和完整配对块恢复，未修改原始 frozen plan、数值源码、二进制或已采集的重放计时。

关键执行命令（各子作业的完整命令/环境及 profiler 参数见对应 launch JSON）：

```bash
source ../env/md-mpi.sh
cmake -S . -B build-o65 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=89 -DDMGMD_ENABLE_O65_DIAGNOSTICS=ON
cmake --build build-o65 -j 6
python3 tests/benchmark/run_overlap_o65.py gates --root dmgmd-overlap-o65-20261026
python3 tests/benchmark/run_overlap_o65.py pilot --root dmgmd-overlap-o65-20261026
python3 tests/benchmark/run_overlap_o65.py profiles --root dmgmd-overlap-o65-20261026
python3 tests/benchmark/run_overlap_o65.py main --root dmgmd-overlap-o65-20261026
python3 tests/benchmark/run_overlap_o65_replays.py all --root dmgmd-overlap-o65-20261026
```

一键离线复算（不要求 GPU）：

```bash
python3 tests/benchmark/recompute_overlap_o65.py --root dmgmd-overlap-o65-20261026
```

复算先验证封存原始证据 SHA-256，再从 Nsight SQLite、日志、快照和 CSV 重建所有
墙钟、kernel、覆盖与 A–D 区间，生成 `candidate-evidence.json`。清单共 9886 个文件、71,764,418,164 字节（含 symlink 目标的逐文件校验）；`analysis-source/` 冻结 59 个 Python/JSON 依赖及其哈希，也可用 `bash dmgmd-overlap-o65-20261026/recompute.sh` 复算。

重放的 producer/缓存、输出恢复与并发 rank 上下文不同于原流水线；D 的差值是映射/
执行组合效果，不全归给空 block、缓存或 occupancy，也不精确拆账历史墙钟。
单机、两种输入、CudaAware 与当前绑定之外的适用性 UNKNOWN；未测 compact 的整步
净收益、HostStaged 竞争路径、跨节点、其他模型/盒形或 100000-step release。
