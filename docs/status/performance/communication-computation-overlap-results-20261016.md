# O4 固定资源检查（2026-10-16）

类别：本机小样本成本与重叠证据。状态：**PASS（测量与证据通路完成）**。本机小样本仍慢于 full，保持默认关闭。
仅 O4 所需窄检查，不是 O5 正式矩阵，不据此默认启用或宣称跨节点收益。
实现与数值验收见[O4 报告](../validation/overlap-o4-20261016.md)。

环境为 n4、AMD EPYC 7K62、RTX 4090、driver 580.173.02、CUDA 12.9、Open MPI
5.0.10、UCX 1.22.0；完整身份见
[environment.json](../../../dmgmd-overlap-o4-20261016/environment.json)。P2 使用 GPU0/1，
UUID 分别 `GPU-a4cdc2a3-d216-af6d-26bb-d6a5c81e2a80`、
`GPU-d146f2b8-3d3d-4b60-b4a5-1b9ed07f2900`。

## 固定输入与测量口径

既有 benchmark 的 carbon_200k，实际 N=196608、seed=0，P2，20 warmup + 100 steps；
同一最终 binary、potential/model/run hash；同二进制 full 也包含共同 selection guard，
不把它当作无 guard 的旧 O1 二进制。full、split-serial、overlap-wait、overlap-poll
每后端各三次重复，按 seed=20261016 在每个重复块中打乱四组顺序。
[measure_cost.py](../../../dmgmd-overlap-o4-20261016/measure_cost.py) 与
[cost-plan.json](../../../dmgmd-overlap-o4-20261016/cost-plan.json) 保存精确命令/组序。

Open MPI 手工映射 `ppr:2:numa:PE=4 --bind-to core --report-bindings`，沿用 C1
`expected-cores-per-rank=4, expected-numa-node=0` 核验实际 affinity。主墙钟详细 profiler
和 O4 trace 关闭；C1 0.05 秒采样、GPU telemetry 开启并记录。使用测量段
`DMGMD_TIMING phase=run sequence=1 seconds_max/100`；包含段首兼容 force，不用进程启动
时间冒充每步成本。CPU 全程 process_observed 含启动/两段 run/退出，不能当每步 CPU。
没有锁频/排他调度，三样本只报告样本、median、min/max/std，不作正式置信区间。

wait 在提交 early 后 finish；poll 同位置最多 8 次 progress，再 finish。程序累计 finish
墙钟/线程 CPU 含 MPI、可见性围栏、early/unpack 等待，不是纯通信时间或完整 CPU 成本。
详细诊断另跑同输入的 3-step 作业，不能混入墙钟主结果。

## 时间线证据与误差

`DMGMD_OVERLAP_TRACE` 的 host 时间为 CLOCK_MONOTONIC 完成观察，CUDA event elapsed 是
另一时间源；未校准为绝对 host 时间，不据此计算 overlap ratio。host enqueue 不是 GPU
执行，MPI 完成观察是实际完成上界，也不是物理链路传输。独立 wrapper 的 32 个 pending
receive/early-complete 证据只证明受控调度状态。

正常生产路径另由 [profile.sh](../../../dmgmd-overlap-o4-20261016/profile.sh) 采集 Nsight
Systems CUDA/MPI trace，精确 kernel 时间和 MPI API 使用该 profiler 的同一时间轴；
最终四份 `.nsys-rep` 和 SQLite 已生成，采集日志无 dropped/error/warning 记录；
最终采集显式设置 `--cuda-event-trace=false`，目录为 profile-noevent-*。CUPTI 将 GPU 采集时间通过插值映射到 CPU 时间，
见 [CUPTI 12.9 timestamp 合同](https://docs.nvidia.com/cupti/12.9/api/group__CUPTI__ACTIVITY__API.html)。
不把应用 CLOCK_MONOTONIC 数字直接与 profiler offset 拼接；本次没有独立校准出的最大
误差界，因此不发布亚微秒精度结论或 overlap ratio。无 NIC/transport trace，不宣称物理网络传输隐藏。

## 实测墙钟与 CPU 成本

24/24 trial status=ok、cpu_status=ok，C1 均核实每 rank 4 个物理核、NUMA0：
rank0 逻辑 CPU 0–3/96–99，rank1 4–7/100–103。这里只核验 CPU affinity，不宣称内存页
驻留或绑定。全部 24 个 thermo.out SHA-256 相同，所有模型/势/run hash 相同；没有靠
fallback 缩减计算。每个 wait/poll trial 有 240 个真实 overlap rank-call（20+100 steps，
2 ranks）；对应完整 split 覆盖，两个 run 的 setup force 仍 full。

| 后端 | 模式 | 三样本 ms/step | 中位数 | min–max | 样本标准差 | C1 两 rank observed CPU-s 中位数 |
|---|---|---|---:|---|---:|---:|
| HostStaged | full | 15.7167, 15.7857, 15.9322 | 15.7857 | 15.7167–15.9322 | 0.1100 | 8.73 |
| HostStaged | serial | 20.5912, 20.5576, 20.4196 | 20.5576 | 20.4196–20.5912 | 0.0909 | 9.98 |
| HostStaged | wait | 20.1999, 20.4516, 20.2120 | 20.2120 | 20.1999–20.4516 | 0.1420 | 9.91 |
| HostStaged | poll | 20.1470, 20.2702, 20.3020 | 20.2702 | 20.1470–20.3020 | 0.0819 | 10.02 |
| CudaAware | full | 16.3287, 16.4738, 16.6340 | 16.4738 | 16.3287–16.6340 | 0.1527 | 9.05 |
| CudaAware | serial | 20.9753, 21.1320, 21.0207 | 21.0207 | 20.9753–21.1320 | 0.0806 | 10.16 |
| CudaAware | wait | 20.3527, 20.1923, 20.1979 | 20.1979 | 20.1923–20.3527 | 0.0911 | 10.00 |
| CudaAware | poll | 20.2265, 20.2579, 20.2504 | 20.2504 | 20.2265–20.2579 | 0.0164 | 10.00 |

C1 CPU-s 是各 rank 自身采样窗内 user+system 的资源用量之和，包含启动、两段 run 与退出；
窗口不完全对齐，不用它除逐步墙钟构造同步 job CPU 利用率。原始 interval/样本/映射在每个
trial 的 cpu/ 下。主线程 finish 的每次 rank-call 成本（各 trial 平均，再取三 trial 中位数）：

| 后端 | 模式 | finish wall ms | finish thread CPU ms | progress 次数/halo |
|---|---|---:|---:|---:|
| HostStaged | wait | 12.3223 | 12.3203 | 0.0000 |
| HostStaged | poll | 12.3835 | 12.3815 | 2.1125 |
| CudaAware | wait | 12.3205 | 12.3191 | 0.0000 |
| CudaAware | poll | 12.2537 | 12.2517 | 8.0000 |

HostStaged wait 相对 split-serial 墙钟减少 1.68%，但相对 full 增加 28.04%。
CudaAware wait 相对 split-serial 墙钟减少 3.91%，但相对 full 增加 22.61%。
poll 未显示比 wait 更好的中位数；finish 的线程 CPU 接近 wall，当前等待仍消耗 CPU。
三样本差异只描述此次窄实验，不声称显著性、普遍加速或 O5 采用结论。

## 可重算的正常路径交叠

每份 trace 对同 rank/step 使用 `MPI_RANKS.globalTid` 的进程部分关联 CUDA `globalPid`；
按 pack→四个 tag=1101/1102 post→unpack 划分本步。I 是实际执行的五个 early kernel
区间并集，**不是 CUDA event 或早晚 enqueue 的包围区间**。C 只取非零 payload request，
从 post API 返回至同 handle 首次 Waitall/Testall 返回即截断，再取区间并集；如果首个
Testall 未完成，这只会保守缩短在途状态区间。每个 step 保存原始 request handle、API
区间、kernel/correlation ID、交集区间。没有把不同 rank 的区间相加当隐藏时间。

| 独立 trace | rank-step 交叠证据 | 同步 MPI API 内也有 GPU early | 单 rank-step C∩I 范围 ms |
|---|---:|---:|---:|
| CudaAware-poll | 6/6 | 6/6 | 1.545–2.334 |
| CudaAware-wait | 6/6 | 6/6 | 1.536–2.369 |
| HostStaged-poll | 6/6 | 6/6 | 0.112–0.227 |
| HostStaged-wait | 6/6 | 6/6 | 0.096–0.217 |

四组合共 24/24 个独立 rank-step 证据；不把这 24 个区间累计为关键路径节省。
代表例 CudaAware/wait rank0 step1：实际 early kernel 并集约 13.130 ms，与保守请求区间
交集约 2.369 ms，其中约 2.333 ms 的 GPU kernel 执行发生在主线程 MPI_Waitall 调用内。
HostStaged/wait rank0 step1 相应交集约 0.217 ms。所有 rank-step 将 C 两端各缩短 10 μs
的敏感性检查仍为正；这不是宣称实际时钟误差上界为 10 μs。未发布 overlap ratio。

每 rank 分区 I/O=81920/98304=83.33%，E/D=90112/107008≈84.21%，E\I=8192。
这只是本 fixture 的分区比例；小/空 interior 仍 fallback。实际 NEP 数学、顺序与 stride
不改变。HostStaged 的 H2D/unpack 以及 CudaAware 保留的 output sync 都在 early 之后，
因此本方案只能隐藏请求等待中可交叠的部分，不隐藏所有 staging/可见性/分区成本。
没有 NIC trace，不能把请求在途/GPU 交叠写成物理网络传输与 GPU 完全并行。

## 原始数据与重算

- [cost-analysis.json](../../../dmgmd-overlap-o4-20261016/cost-analysis.json) 保留每个 trial 的路径、样本、CPU、真实模式与计数；每个目录保存 metadata/results/telemetry/C1。
- [profile-analysis.json](../../../dmgmd-overlap-o4-20261016/profile-analysis.json) 保留逐 rank/step 原始区间与交集；最终四个 profile-noevent-*/ 下保存 `.nsys-rep`、SQLite、launch.json、输入与原始输出。
- [diagnostic.log](../../../dmgmd-overlap-o4-20261016/diagnostic.log) 的两后端单独作业核验 O1 timing/trace 与 O4 trace 同时启用；不纳入墙钟表。

```bash
source ../env/md-mpi.sh
python3 dmgmd-overlap-o4-20261016/cost_analysis.py
python3 dmgmd-overlap-o4-20261016/profile_analysis.py
```

具体采集命令由 measure_cost.py、diagnostic.sh、profile.sh 与 launch.json 保存；重复采集需
更换输出路径，不能覆盖原产物。候选 binary SHA 与源码/dirty 身份、reference 前后状态见
[验收报告](../validation/overlap-o4-20261016.md)。所有 GPU 作业串行调度。

仍未执行 O5 的 carbon_1m/water_400k、P1/2/4/8 正式矩阵、至少五重复/区间估计、统一
绑定策略与采样扰动研究、跨节点性能及采用阈值。当前建议只保留实验模式，默认 off。

首轮 profile-* 目录保留了工具警告：device-side CUDA Event completion tracing 可能增加
开销并引入额外跨 stream 依赖。因此另以 `--cuda-event-trace=false` 复采全部四组合，
本页表格与 profile-analysis.json 仅使用最终 profile-noevent-*。两轮均未改生产 binary，
主墙钟成本样本从未带 Nsight；初轮证据不混入最终统计。
