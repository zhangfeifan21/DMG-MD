# O6：稳态拆分成本定位（2026-10-21）

类别：独立受控诊断与无诊断复验。状态：**成本位置已定位；微架构原因 INCONCLUSIVE**。
O6 的工具、正确性与墙钟实验已完成；不把计数器受限解释成已经找到精确硬件原因。
生产 NEP、通信协议与默认 HostStaged/full、overlap=off 均保持原状。

carbon_200k/P2/CudaAware 的新五重复墙钟确认拆分增加 **4.630 ms/step**。
三轮稳态 CUPTI 将主要损失定位到 **radial kernel**：full 约 4.589 ms，
early+late 约 8.479 ms，增加 3.890 ms。五类 kernel 的总增量约 4.728 ms；
radial 约占这个**独立诊断增量**的 82%，不能据此精确拆账正式墙钟的 4.630 ms。
water/P4/P8 的拆分也增加 GPU 成本，但 wait 的独立无诊断净加速仍约为 1.060×/1.089×。

## 身份、资源与采集方案

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
CPU/NUMA 分配沿用 [O5 映射](communication-computation-overlap-o5-results-20261021.md#冻结设计身份与公平性)，
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

## 正确性与采集有效性

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
  生产二进制未变，沿用 [O4 同 SHA 的 long_nve smoke](../validation/overlap-o4-20261016.md)，
  本轮没有声称重跑 nightly 或 100000-step release。

两项 NCU 尝试因计数器权限为 INCONCLUSIVE；其余相同 GPU0 的 17 项按冻结规则
记 NOT_RUN。它们是诊断能力缺项，不计入性能或生产科学失败样本。

## 无诊断整步结果

单位 ms/step，中位数。最后一列 >1 表示 wait 比同后端 full 更快。

| 配置 | full | serial | wait | serial/full，95% CI | full/wait，95% CI |
| --- | ---: | ---: | ---: | --- | --- |
| carbon/P2 | 16.381870 | 21.011742 | 20.167935 | 1.282622 [1.279865, 1.290919] | 0.812273 [0.804688, 0.816327] |
| water/P4 | 8.522567 | 9.306070 | 8.042997 | 1.091933 [1.090222, 1.094452] | 1.059626 [1.057122, 1.061913] |
| water/P8 | 6.421340 | 7.224365 | 5.896438 | 1.125056 [1.119021, 1.127338] | 1.089020 [1.087680, 1.089020] |

carbon 的 serial→wait 中位数差为 0.844 ms；相对 full 仍多 3.786 ms。
这复现了 [O5 的 4.60 ms 代表配置](overlap-o5-cost-analysis-20261021.md)的方向。
water 的拆分 GPU 工作同样变慢，净收益来自整个调度；不能用 kernel 总和预测通信隐藏量。
本轮没有重新比较 HostStaged，不能替代 O5 的默认后端采用结论。

## 稳态实际 kernel 对照

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

## 匹配短控制与诊断扰动

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

## 分类、launch、同步与通信分别记账

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

## 硬件原因、限制与 O9 决策

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

## 原始产物与复算

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
