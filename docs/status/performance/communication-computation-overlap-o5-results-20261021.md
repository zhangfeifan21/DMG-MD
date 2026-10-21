# O5 正式性能、成本归因与采用决策（2026-10-21）

类别：本轮受控性能实验。状态：**PASS（本机冻结范围）**；主矩阵、归因、诊断、独立采用验证均实际完成。
采集日期为 2026-10-16、10-19 与 10-21；本页与 [O4 窄检查](communication-computation-overlap-results-20261016.md)分开。

证据支持保持默认 HostStaged/full 与 overlap=`off`。仅对固定使用 CudaAware 的用户，
在本机、精确 water_400k fixture、P4/P8、四物理核/rank 条件下支持人工限定试用 wait。独立验证的净加速为 1.0627×/1.0927×；poll 未表现出
稳定优于 wait 的通用收益。两个 carbon 规模的分区成本抵消 overlap 增量收益，不能据此替代 full。
这两个收益均以同后端 full 为分母；本机 HostStaged/full 的实测墙钟仍更短。
不推出自动阈值，不改生产默认，不把单机结果外推跨节点、其他盒形或势模型。

## 完成范围与样本准入

| 组别 | 实际有效性能 trial | 配置与用途 |
|---|---:|---|
| 主矩阵 | 600 | 三体系 × P1/2/4/8 × 两后端 × full/serial/wait/poll；全部至少五重复，其中 24 配置按冻结规则追加至十重复 |
| 历史归因 | 150 | carbon_200k/P2、carbon_1m/P4、water_400k/P8 × 两后端 × O1 full、O2 full/serial、O3 full/serial × 五重复 |
| 退化场景 | 120 | 漂移 carbon/P2 五重复；薄 slab/空 interior/P4 十重复；两后端、四模式 |
| 独立采用验证 | 40 | water/CudaAware/P4/P8 × 四模式 × 五个新重复块；不并入主矩阵 |
| 合计 | 910 | 均为全新进程，正式段完整结束且 ≥10 s；正式计时组没有 verify 或详细诊断 |

原主计划 710 个 trial，基于初轮配对区间宽度预定追加 160 个（carbon_200k/P4/P8、
water_400k/P2、thin/P4 的所有模式/后端），得到前 870 个。最短正式段 19.9937 s，最长
132.4902 s。没有缺卡、单卡 OOM 或以 P4 替代 P8；三个体系的 P1 都有效。

网络中断前 187 个正式 trial 完整保留；另一个 attempt 虽有 timing，退出码与采集生命周期
NOT_OBSERVED，保留为 PARTIAL 且不准入，以新目录补跑。其后保持冻结顺序和身份；
跨日期 repeat1 的排除敏感性单独保存，n<5 不给区间。不按速度删除异常样本。

原 88 组详细诊断中 15 组因 MPI 长日志交错缺少完整计数而 FAILED；73 组虽完成，部分
字段也发生串流，整体由独立目录补采。原生 PRRTE 分文件 smoke 仍发现交错，保留该失败，
最终采用 rank 在 exec 前直接重定向 stdout/stderr；P8 两后端 smoke 科学结果一致。
这些是测量工具/输出问题，没有发现需要修复的生产正确性问题，O4 二进制始终不变。

诊断 88/88、CPU 正式循环有效窗口 384/384 rank、GPU 映射 384/384 rank、Nsight 20/20
均通过；`analysis-status.json` 无缺失或待追加配置。保留 1502 个带 launch 的原始尝试，
其中 1483 PASS、18 FAILED、1 PARTIAL；18 个失败分别为旧诊断日志 15、原生分文件 smoke 1、
初轮薄 slab poll 校验工具误判 2，全部保留原因与后续独立门槛。
所有阶段（含失败、中断、废弃 pilot）的清单与原始证据哈希在最终 `all-attempts.*` 与
`raw-evidence-inventory.json` 中；状态没有被后续成功覆盖。

## 冻结设计、身份与公平性

起始 HEAD `6b8efc505f19d539d4eaa916fe2c4793bf9b9c79`，工作树原先干净。
本轮只补充 Python 启动、绑定、采样/分析、测试和文档；没有修改 NEP 数学、精度、布局、
stride、原子所有权、半径、通信协议或 stream/thread 模型。没有 Git 提交或推送。

[design.json](../../../dmgmd-overlap-o5-20261016/design.json) 在 pilot 前定义准入、统计和追加规则；
[frozen-plan.json](../../../dmgmd-overlap-o5-20261016/frozen-plan.json) 在正式采集前固定步数、
身份、映射和完整顺序。主顺序 seed=2026101606，追加 seed=2026101609；每个重复块随机化
模式、版本、后端。所有 GPU trial 串行调度，使用 workflow/scheduler 锁支持安全续跑。

| fixture | 实际 N | cells | warmup | 正式 steps |
|---|---:|---|---:|---:|
| carbon_200k | 196608 | 16×96×16 | 200 | 3100 |
| carbon_1m | 1048576 | 32×128×32 | 200 | 900 |
| water_400k | 393216 | 32×128×32 | 1600 | 4300 |

同 case 固定初态、NEP、seed=0、显式初速度、0.1 fs 步长和盒形，强扩展固定 N。
carbon_200k_drift 仅对原输入 y 速度统一加 2 Å/fs；600 正式步、200 次重建/150 次迁移。
thin_empty 为 12288 原子、cells=8×24×8、P4，5500 正式步，M2a slab 合法但 interior 为空。
补充场景的 warmup、输入与完整哈希见 plan 的 degraded_configs 和 inputs。

100 步 water pilot 每窗口仅 2–4 次重建，预热检查没有通过；1000 步独立窗口在全 P/后端
的 warmup800/1600 比较均 <5%，另检查 P8/w3200。据最快 pilot 选择统一 steps，给 ≥10 s
留约 20 s 裕量。解决记录及原始文件哈希在 `formal-config-resolution.json`，pilot 不入统计。

每 trial 保存精确 argv、显式白名单 `launch_environment`、输入/科学输出哈希、退出码、
seconds_max/sequence/steps、实际 backend、M1/M2a、partition/overlap 和真实计数。
清除继承的 DMGMD 调试开关及未列入计划的 CUDA/MPI 环境；full 为 off/off，serial 为
serial/off，wait/poll 为 serial/wait|poll。verify 只出现在科学门槛。
所有正式科学文件与同配置 full oracle 一致，历史组 150 个 trial 的 thermo/neighbor 也逐项
通过（`historical-formal-file-check.json`）；模式、绑定或科学输出不符均不准入。

机器 n4：AMD EPYC 7K62，96 物理核/192 逻辑 CPU、两个 NUMA、八张 RTX 4090；
Open MPI 5.0.10/UCX 1.22、CUDA 12.9、driver 580.173.02。每 rank 固定 K=4 物理核，
含各核 SMT 兄弟，共八个 OS CPU。P1/2/4 取 P8 的前缀；不共享物理核。

| rank | hwloc 逻辑 Core | OS CPU（含 SMT） | socket/NUMA | GPU UUID 前缀 | PCI bus |
|---:|---|---|---|---|---|
| 0 | 0–3 | 0–3,96–99 | 0/0 | a4cdc2a3 | 01:00.0 |
| 1 | 4–7 | 4–7,100–103 | 0/0 | d146f2b8 | 24:00.0 |
| 2 | 8–11 | 8–11,104–107 | 0/0 | 8264a4fc | 41:00.0 |
| 3 | 12–15 | 12–15,108–111 | 0/0 | 411245be | 61:00.0 |
| 4 | 48–51 | 48–51,144–147 | 1/1 | 9ea64601 | 81:00.0 |
| 5 | 52–55 | 52–55,148–151 | 1/1 | ea45f080 | A1:00.0 |
| 6 | 56–59 | 56–59,152–155 | 1/1 | e99f226f | C1:00.0 |
| 7 | 60–63 | 60–63,156–159 | 1/1 | 1c95c731 | E1:00.0 |

策略由统一 `cpu_binding.py` 生成，常规 `launch_dmgmd.py` 与 benchmark 复用；
Core 编号经 hwloc/sysfs 映射到 OS CPU，不直接混用。core/P1、numa/P2、rankfile/P4 的
实际 CPU 验收、P1/2/4/8 两后端固定绑定 GPU smoke 已通过。map/bind/rankfile/MCA 等
冲突和资源不足均拒绝；本机 PMIx 的连字符解析问题通过同内容/同哈希的短 rankfile 别名解决。

每个性能 trial 在 C1 关闭时仍每 0.2 s 核验 exec 后所有可见 task 的实际 affinity、候选路径/
哈希和 PID/start identity，每 2 s 核验可见 quota/cpuset；保存 resolved、rankfile 内容/哈希、
hwloc XML、实际命令和资源原文。870 个主/历史/退化 trial 与 40 个独立验证 trial 的绑定全部 PASS。
GPU 初始化日志核对逐 rank UUID 次序；生产只有初始化时一次 cudaSetDevice，诊断组另用
独立 NVML PID/context 查询核验初始化后及正式 loop 内的唯一 GPU，不把 report-bindings 当证明。

GPU0–3 邻近 NUMA0、GPU4–7 邻近 NUMA1。内存策略继承 default、nodebind/membind=0,1；
页驻留/本地内存 UNKNOWN，CPU affinity 不能证明内存本地性。可见祖先 quota 为 max，
cpuset=0–191；未委派的叶控制器继承祖先，隐藏祖先和资源独占性 UNKNOWN。
没有锁频或排他预约；每张卡已有其他用户服务的常驻显存，保留 GPU 进程、温度、时钟、
功耗、loadavg 和 /proc/stat，不中止其他进程。遥测是整个观察进程范围，不能冒充正式段。
主/历史/退化组所选卡的全进程遥测范围为温度 28–77 °C、SM clock 210–2790 MHz、
功耗 6.01–462.79 W，包含初始化/预热与低负载窗口，不当作正式段恒定频率证明。

### 四阶段版本与正确性

| 阶段 | revision | 正式模式 | 二进制 SHA-256 |
|---|---|---|---|
| O1 原串行 | 8a709385 | full | 532bbe9a93a67b1360925edbbccda01cbb46e56c150dbea6dbdabc71290c7288 |
| O2 selection/分区 | bcedb72b | full、serial | 2e6040616105d450d3304df2ce29f661b216c19363a3a209b5c572b941e8fc4a |
| O3 生命周期/立即 finish | 9c608a16 | full、serial | 4581ad7ffd8c547f47960ce9efae233d7bcce398b44c28575488720da0ad93a9 |
| O4 接口与实际 overlap | 6b8efc50 | full、serial、wait、poll | 860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6 |

历史 O1–O3 在独立 archive/source/build 重建，无需切换工作树；clean revision 的源码 patch
为 null，旧交付 dirty patch 与逐文件身份另保存在 historical-provenance。O4 保留原交付
二进制并核对源码；当前 full 不当成无 selection guard 的旧 O1。
统一 Release、GCC 11、NVCC 12.9.86、`-O3 -DNDEBUG`、架构 75/80/86/89/90，无 fast-math。
CMake cache、compile commands 哈希、源文件 SHA、原始环境与构建日志均保存于
`version-identities.json`、`historical-source-check.json`、各 build 与 provenance 目录。
`version-provenance-resolution.json` 纠正原元数据 O4 描述误提 O2 的文字，原记录不覆盖，
冻结二进制哈希与实验组归属从未变化。
原保留 O2 binary `43629bed…` 虽通过门槛，但 flags 追溯不完整，未混入正式历史组。

新入口按同一 `../env/md-mpi.sh` 通过环境预检、单 rank golden、固定绑定 smoke 和实际 P8
双后端门槛；O4 精确身份的原 P2/P4 验收复用。新实际输入覆盖 full/serial/wait/poll 科学文件，
P8 verify 检验同快照 bitwise/poison。大型 CPU 图 oracle 的每步约 54 秒，初轮缩减部分
重复验证，完整与中断记录及 scope adjustment 保留；接受矩阵为 112 个当前、16 个退化、
18 个 O1/O3 历史和 12 个重建 O2 比较，均通过，不将未跑项计为 PASS。
Oct19 另做 P8 环境预检、golden 4/4 与八个模式/后端补验。完整 CTest 12/12 为 Oct16 记录，
Oct21 工具 CPU 单测 53/53、直接日志 P8 两后端 smoke 2/2；最新记录另存。
golden 和容差均未修改。

## 主结果与不确定性

主性能采用程序 sequence=1 的 `seconds_max`，保留段首 force、真实重建/迁移、归约和规定输出；
进程 wall 另报。详细 timing、O1 trace、overlap trace、verify 和 C1 全关闭；固定 affinity audit
与两秒遥测在所有模式相同。不存在把有诊断的 run 声称为无诊断性能样本的情况。

比值统一为**中位数之比**：分区 serial/full，增量 serial/overlap，净 full/overlap；
poll 对 wait 为 wait/poll。以独立 trial/重复块为单位，保留配对关系进行 20,000 次百分位
bootstrap，逐配置点态 95% 区间；不把 steps、rank 或 kernel 当独立样本，不校正探索矩阵
多重比较。主 seed=2026101606 加对比名的 SHA 派生值，全部样本、min/max/SD/MAD 在 JSON/CSV。

初轮五重复中净收益或 wait/poll 区间相对宽度 >10% 时，对相应 case/P 的全模式和后端
统一追加五块，最多到十重复；不只追加较快配置。追加后 carbon_200k/P4/HostStaged 的
净区间仍较宽，保留不确定。历史若干小成本区间很宽，不能用中位数接近 1 断言零成本。

| 体系 | P | 后端 | n | 分区成本 serial/full | 增量 serial/wait | 净 full/wait（95% CI） | 净 full/poll（95% CI） |
|---|---:|---|---:|---:|---:|---|---|
| carbon_200k | 2 | HostStaged | 5 | 1.2960 | 1.0105 | 0.7797 [0.7745, 0.7832] | 0.7784 [0.7692, 0.7841] |
| carbon_200k | 2 | CudaAware | 5 | 1.2789 | 1.0459 | 0.8179 [0.8066, 0.8247] | 0.8158 [0.8098, 0.8257] |
| carbon_200k | 4 | HostStaged | 10 | 1.3359 | 0.9991 | 0.7479 [0.7372, 0.8492] | 0.7545 [0.7439, 0.8561] |
| carbon_200k | 4 | CudaAware | 10 | 1.3367 | 1.0543 | 0.7887 [0.7795, 0.8362] | 0.7980 [0.7917, 0.8461] |
| carbon_200k | 8 | HostStaged | 10 | 1.5823 | 1.0102 | 0.6384 [0.6268, 0.6457] | 0.6373 [0.6302, 0.6420] |
| carbon_200k | 8 | CudaAware | 10 | 1.5316 | 1.0907 | 0.7121 [0.7055, 0.7166] | 0.7113 [0.6717, 0.7185] |
| carbon_1m | 2 | HostStaged | 5 | 1.0496 | 1.0054 | 0.9579 [0.9551, 0.9612] | 0.9574 [0.9558, 0.9606] |
| carbon_1m | 2 | CudaAware | 5 | 1.0466 | 1.0380 | 0.9918 [0.9415, 1.0267] | 0.9928 [0.9893, 1.0270] |
| carbon_1m | 4 | HostStaged | 5 | 1.1165 | 1.0147 | 0.9088 [0.9073, 0.9491] | 0.9092 [0.9092, 0.9484] |
| carbon_1m | 4 | CudaAware | 5 | 1.1110 | 1.0833 | 0.9751 [0.9693, 0.9776] | 0.9730 [0.9710, 0.9769] |
| carbon_1m | 8 | HostStaged | 5 | 1.2966 | 1.0185 | 0.7855 [0.7812, 0.7904] | 0.7850 [0.7788, 0.7909] |
| carbon_1m | 8 | CudaAware | 5 | 1.2697 | 1.1216 | 0.8834 [0.8803, 0.8846] | 0.8836 [0.8782, 0.8877] |
| water_400k | 2 | HostStaged | 10 | 1.1021 | 1.0169 | 0.9227 [0.9038, 0.9376] | 0.9226 [0.9213, 0.9375] |
| water_400k | 2 | CudaAware | 10 | 1.0908 | 1.1070 | 1.0148 [1.0085, 1.0224] | 1.0073 [1.0035, 1.0119] |
| water_400k | 4 | HostStaged | 5 | 1.0991 | 1.0197 | 0.9278 [0.9251, 0.9333] | 0.9276 [0.9268, 0.9280] |
| water_400k | 4 | CudaAware | 5 | 1.0902 | 1.1545 | 1.0590 [1.0567, 1.0639] | 1.0593 [1.0576, 1.0603] |
| water_400k | 8 | HostStaged | 5 | 1.1472 | 1.0381 | 0.9048 [0.9004, 0.9058] | 0.9035 [0.9001, 0.9071] |
| water_400k | 8 | CudaAware | 5 | 1.1264 | 1.2314 | 1.0932 [1.0898, 1.0949] | 1.0937 [1.0901, 1.0958] |

完整 P1、poll 增量、所有区间/离散度/样本、退化与历史表见
[可重算统计](../../../dmgmd-overlap-o5-20261016/statistics.md)与
[结果表](../../../dmgmd-overlap-o5-20261016/report-tables.md)。
P1 三体系两后端的所有请求模式均实际 full fallback，真实 split/overlap/progress 为零，
比值约 1；这些重复配置只衡量噪声，不构成 overlap 启用证据。

![相对 full 的净加速及配对点态区间](../../../dmgmd-overlap-o5-20261016/net-speedup.png)

carbon_200k 的分区成本随 P 从约 28–30% 增到 53–58%，CudaAware 增量最多约 9%，
仍有明显净损失。carbon_1m 分区成本约 5–30%，CudaAware 增量随 P 增到约 12%，仍不足
抵消分区；P2 的净区间跨 1，只能称无确定净收益。
water 的 CudaAware 增量由 P2 的约 11% 增到 P8 的约 23%，抵消约 9–13% 的分区成本；
HostStaged 增量只有约 2–4%，各 P 的净收益均 <1。不能把“比 serial 快”作为采用依据。

作为调度整体效果，主组 serial→wait 的 ms/step 中位数差为 carbon_200k/P2：
Host 0.2129、CudaAware 0.9264；carbon_1m/P4：Host 0.6440、CudaAware 3.6271；
water/P4：Host 0.1617、CudaAware 1.2531；water/P8：Host 0.2313、CudaAware 1.3678。
这不是纯网络等待隐藏量。再扣除分区等成本后，water/CudaAware 对同后端 full 实际仅减少
0.4788/0.5510 ms/step；carbon 的净墙钟差为负。详细诊断与 Nsight 只解释机制，不替代这些墙钟。

wait/poll 大多数配对区间包含 1；water/P2/CudaAware 的 wait/poll=0.9926，
95% CI=[0.9897,0.9979]，poll 较慢约 0.7%。water/P4/P8 的点估计差异 <0.05%，区间含 1。
完整 wait/poll 结果保留，不选择某个最佳 trial 代表方法。

### 历史新增成本与强扩展

四阶段对照覆盖上述六个 case/P/backend 条件，全部 150 个历史 trial 有效，并与当前 full、
serial、wait、poll 在同一重复块随机化。O2 full/O1 的中位数约 0.998–1.011；O2 serial/full
约 1.11–1.29；O3 full/O2 full 约 0.995–1.004，O3 serial/O2 serial 约 0.995–1.001；
O4 full/O3 full 约 0.998–1.001，O4 serial/O3 serial 约 1.000–1.007。
主要可分辨成本来自分区。这里是整个 revision 的增量，不能进一步伪装成纯 selection guard、
单个 launch 或纯同步指令成本。

历史 water/P8/CudaAware 的 O2 full/O1 区间 [0.9754,1.9234]、O4 full/O3 full
[0.5089,1.0034]，carbon_1m/P4/CudaAware 的 O3 full/O2 full [1.0011,1.3062]：
小接口成本精度不足，结论为 INCONCLUSIVE，原始较慢样本保留。没有借用 O1–O4 旧机器的
表格数字补齐本轮归因；跨二进制与非独占环境限制适用于全部历史比较。

强扩展 S=T(1)/T(P)、E=S/P，全部有有效同 N 的 T1；P1 为 M1，P>1 为 M2a，
K=4 固定。只配对共同重复块，追加配置的强扩展仍使用五个匹配 P1 的块。
P8 的 full/wait S 分别为 carbon_200k：Host 4.2015/2.7157、CudaAware 3.7777/2.6903；
carbon_1m：Host 6.2006/4.8646、CudaAware 5.4095/4.7742；
water：Host 2.8200/2.5499、CudaAware 2.0853/2.2803。
相应 E 为 S/8；完整 P1/2/4/8、四策略和区间在 `scaling.*`，不借其他条件单卡补齐。

## 诊断、CPU 与真实交叠

88 组补采均 PASS，384 个 rank 的逐步 trace 完整、科学输出与同窗口 full 一致；
C1 384 个正式循环 rank 窗口均有效，每 rank 有 14–80 个完整采样窗，覆盖率 89.30–99.68%
（中位数 95.65%）。core-equivalent 范围 0.9975–1.0125，中位数 1.0071。
这是单 rank 的诊断观察，不是 384 个独立性能重复。所有逐 rank 数据见 `cpu-formal-loop.*`。

独立 NVML 验证 88/88 trial、384/384 rank：初始化有 post-exec context 样本，正式 marker
内每 rank 至少两个完整查询窗，PID/start/时钟一致且唯一 UUID 与计划一致，query failure=0。
CPU affinity 独立核验也通过；内存驻留仍 UNKNOWN。

| water/CudaAware | 模式 | rank | 有效窗 | 观测秒 | user CPU-s | system CPU-s | core-equivalent |
|---:|---|---:|---:|---:|---:|---:|---:|
| P4 | full | 0 | 21 | 4.1996 | 4.20 | 0.03 | 1.0072 |
| P4 | split-serial | 0 | 23 | 4.6000 | 4.56 | 0.04 | 1.0000 |
| P4 | overlap-wait | 0 | 20 | 4.0002 | 3.96 | 0.04 | 1.0000 |
| P4 | overlap-poll | 0 | 20 | 4.0000 | 4.00 | 0.03 | 1.0075 |
| P8 | full | 0 | 22 | 4.3999 | 4.40 | 0.04 | 1.0091 |
| P8 | split-serial | 0 | 25 | 5.0001 | 5.01 | 0.04 | 1.0100 |
| P8 | overlap-wait | 0 | 20 | 4.0000 | 3.97 | 0.03 | 1.0000 |
| P8 | overlap-poll | 0 | 20 | 4.0000 | 3.99 | 0.04 | 1.0075 |

分类/比例按各 rank 在该诊断窗口的中位数报告，下表范围体现 rank 差异；不是跨 rank
合并的全局 interior 比率，也不是独立样本置信区间。

| fixture/P（CudaAware/wait） | interior/owned 的 rank 范围 | early/dependency 的 rank 范围 | rank0 hit/miss |
|---|---|---|---|
| carbon_200k/P2 | 0.8333–0.8333 | 0.8421–0.8421 | 199/1 |
| carbon_200k/P4 | 0.6667–0.6667 | 0.7080–0.7080 | 399/1 |
| carbon_200k/P8 | 0.3191–0.3469 | 0.4769–0.5077 | 499/1 |
| carbon_1m/P2 | 0.8750–0.8750 | 0.8791–0.8791 | 199/1 |
| carbon_1m/P4 | 0.7500–0.7500 | 0.7724–0.7724 | 199/1 |
| carbon_1m/P8 | 0.5000–0.5000 | 0.5926–0.5926 | 199/1 |
| water_400k/P2 | 0.8749–0.8751 | 0.8785–0.8785 | 376/12 |
| water_400k/P4 | 0.7498–0.7505 | 0.7710–0.7714 | 470/15 |
| water_400k/P8 | 0.4984–0.5000 | 0.5887–0.5894 | 660/20 |
| carbon_200k_drift/P2 | 0.8333–0.8333 | 0.8381–0.8381 | 66/67 |
| carbon_thin_empty/P4 | 0.0000–0.0000 | 0.0000–0.0000 | 899/1 |

诊断中的 MPI_Waitall 包围时间不能直接解释为被隐藏量：CUDA-aware wait 可能保留相近的
MPI 调用墙钟，同时让 GPU early 实际执行；之后的输出同步围栏等待剩余 early。
下表是 rank0 的逐步中位数（ms），这些区间有包围/交叠，不能相加。

| fixture/P/CudaAware | 模式 | MPI_Waitall host | output sync host | early GPU | late GPU |
|---|---|---:|---:|---:|---:|
| carbon_1m/P4 | split-serial | 3.3269 | 0.0013 | N/A | N/A |
| carbon_1m/P4 | overlap-wait | 3.4389 | 26.4123 | 29.8532 | 12.9153 |
| carbon_1m/P4 | overlap-poll | 0.9316 | 26.3195 | 29.7606 | 12.8348 |
| water_400k/P4 | split-serial | 1.2932 | 0.0014 | N/A | N/A |
| water_400k/P4 | overlap-wait | 1.3423 | 1.4360 | 2.7945 | 1.5954 |
| water_400k/P4 | overlap-poll | 0.2375 | 1.4450 | 2.7935 | 1.6107 |
| water_400k/P8 | split-serial | 1.3764 | 0.0014 | N/A | N/A |
| water_400k/P8 | overlap-wait | 1.4217 | 0.0649 | 1.5053 | 1.4029 |
| water_400k/P8 | overlap-poll | 0.6567 | 0.0747 | 1.5053 | 1.4049 |

HostStaged 的 carbon_200k/P2/wait，rank0 的 pack GPU 约 0.0074 ms，D2H host 0.0332 ms、
H2D host 0.0050 ms、unpack GPU 0.0081 ms；CudaAware 对应 staging 未执行，字段保留 N/A。
pack、同步、分类、launch 与真实重建/迁移全部保留在正式墙钟，不用删工作换取加速。

正式计数已给出：carbon 两个静态 fixture 在该窗口没有重建；water 每 4300 步重建/迁移
112 次（2.6047%）、真实 split/overlap 4188 次，每 rank 分类 112 次；漂移场景每 600 步
重建 200 次（33.3%）、迁移 150 次，split 400 次且分类 200 次。
薄 slab 是 M2a full fallback，split/overlap=0，仍可能执行普通 halo 的 poll；不计 overlap 证据。

每个真实 split 多一组五个产品 kernel launch，分类 miss 另有条件 kernel 与 fill；
carbon_1m 与 water/P8 的 interior/owned 都约 0.5，但净收益方向相反，不能单用比例
拟合采用阈值。缓存 hit/miss、interior/owned、early/dependency、rank 差异、pack、D2H/H2D、MPI call、
unpack、early/late、同步围栏和通信字节单独保存在 `diagnostic-analysis.json`。
各阶段计时存在重叠和同步包围，不相加还原纯网络或整步；通信字节也不是物理 wire 字节。

正式段 summary 自带的 finish 回调 wall/thread CPU（包括 poll）可跨独立重复比较。
例如 water/CudaAware/wait 的 rank0 回调 CPU：P4 约 2.9795 ms，P8 约 1.5819 ms；
P8 poll/wait CPU 成本比 0.9990，95% CI=[0.9983,1.0081]，未显示确定节省。
CudaAware 普通回调 poll 常达到八次，wait 为零；完整 per-rank 计数和区间在
`formal-cost-summary.*`、`finish-cost-ratios.*`。这些回调大量等待 early、可见性与 unpack，
CPU 时间不能区分 MPI 忙轮询和有效计算，finish wall 不是纯网络传输时间。

最小 C2 校准为 carbon_200k/P2/CudaAware/wait，off/proc/详细诊断各五次随机重复。
正式段 proc/off=0.9979 [0.9962,1.0044]、trace/off=1.0026 [0.9966,1.0124]；
进程 wall 分别约 1.0029/1.0053，启动+采样生命周期约 1.0093/1.0116。
后者不含离线分析和输入准备，不能当完整分析流水线总开销。它衡量原始输出路径，
补采直接日志路径未另做端到端扰动估计，补采数据只用于诊断。主性能冻结为 C1 off；
没有因 observer ticks 很小便假设零扰动，也没有扩展为完整 C2/C3。

主性能组没有 marker，正式段总进程 CPU 保持 null；process_observed 含启动、预热和退出，
不替代正式段。C1 只对 PID/start、时钟一致且完整落在 marker 内的窗口归因；诊断 loop
marker 又排除段首 force/末端同步，不能等同 `seconds_max`。短 step 没有完整窗口时为 null。
进程/线程 CPU 不相加，rank 峰值不相加，异步 rank 窗口不当同窗 job 利用率，容量比例未知保留 null。

Nsight Systems 2025.1.3.140，使用 `--trace=cuda,mpi --mpi-impl=openmpi --sample=none
--cpuctxsw=none --cuda-event-trace=false`，20 份独立三步记录覆盖三个主条件及两退化条件、
两后端和 wait/poll。关闭 device-side CUDA Event completion tracing；保存精确命令、
工具版本、原始 .nsys-rep/SQLite 和日志；这些墙钟不入主性能。20 份日志未报告 warning/dropped；工具未提供可独立核实的
dropped-event 计数，保留 NOT_OBSERVED，不将“未报告”填成零。

在 Nsight 自身同一时间轴，以 MPI rank/globalTid→GPU globalPid、实际四个 halo post、
真实五个 early kernel 和 unpack 为锚点，按同 rank/step 的区间并集计算交集。
P4/P8 的双方向 pack 与 callback 数分开；初始化/重建的同 tag 消息不误配普通 halo。
16 个非空场景记录中 184 个 rank-step 均观察到 GPU early 与请求状态区间交集；
四份空 interior 记录为 NOT_APPLICABLE_FULL_FALLBACK。

例：rank0/step1，carbon_200k/P2/HostStaged/wait 的 O 为 0.1632 ms，
carbon_1m/P4/CudaAware/wait 为 7.0776 ms，water/P8/CudaAware/wait 为 1.4241 ms；
将请求区间两端各缩 10 μs 后分别为 0.1532/7.0676/1.4241 ms。这是敏感性分析，
10 μs 不是经过证明的最大误差。请求区间止于同 handle 的首次 Test/Wait 返回，
完成观察不等于实际传输完成，host enqueue 不当作 GPU 执行。

独立的最大时钟/完成边界误差 UNKNOWN，所以 R 全部 N/A；空分母也 N/A。
这些数值不是物理网络隐藏量或关键路径节省，不跨 rank 求和，不把阶段理想比例叫整步上界。
空 interior 的一份 HostStaged/poll 中部分 request 完成观察缺失，显式 NOT_OBSERVED，
不需要且不制造该 fallback 的 overlap ratio。正常 split 的请求/五 kernel 映射均完整。

## 独立验证与采用建议

[adoption-validation-plan.json](../../../dmgmd-overlap-o5-20261016/adoption-validation-plan.json)
于 Oct19 在新样本采集前冻结：从探索组选择两个 wait/poll 净区间下界均 >1.05 的
water/CudaAware/P4/P8，保留 full/serial 两控制，五新重复块；order_seed=2026101912，
bootstrap_seed=2026101913。Oct21 实际执行，二进制、输入、GPU/CPU 映射、warmup/steps
与主矩阵相同，不将新数据并回探索组。

四个预定净对比使用 20,000 次配对 bootstrap，同时报告 95% 和每对比 98.75% 区间
（Bonferroni 名义家族 95%）。通过条件在采集前固定为中位数比 ≥1.05 且校正下界 >1；
不因结果继续采到获益。n=5 的 bootstrap 名义覆盖并非严格保证，跨日期和非独占限制保留。

| P | 模式 | n | 净 full/overlap | 95% CI | 98.75% CI（四比较家族） | 冻结门槛 |
|---:|---|---:|---:|---|---|---|
| 4 | overlap-wait | 5 | 1.0627 | [1.0610, 1.0756] | [1.0610, 1.0756] | 通过 |
| 4 | overlap-poll | 5 | 1.0628 | [1.0596, 1.0844] | [1.0596, 1.0844] | 通过 |
| 8 | overlap-wait | 5 | 1.0927 | [1.0519, 1.1268] | [1.0519, 1.1268] | 通过 |
| 8 | overlap-poll | 5 | 1.0923 | [1.0573, 1.1262] | [1.0573, 1.1262] | 通过 |

四项均通过独立验证，支持**固定 CudaAware 时在精确已测条件下人工限定使用**，推荐先用 wait。
poll 没有可重复的普遍净优势，增加 progress 调用也不能据此证明进程总 CPU 显著增加或下降。
采用范围仅本机硬件/软件、该输入 N/盒形/NEP、K=4、CudaAware 和 P4/P8。
water/P2 虽有约 1.5% 的探索净收益，未达到预定采用门槛，未作为确认条件。

默认后端另有必要的范围限制：P4 的 HostStaged/full 为 7.6276 ms/step，CudaAware/wait 为
8.1093；P8 分别 5.4970/5.9109。事后探索的配对 Host-full/Cuda-wait 比为
0.9406 [0.9376,0.9437]、0.9300 [0.9255,0.9312]。因此不建议为 overlap 切换到 CudaAware；
后端可选时，本轮默认 HostStaged/full 仍更快。跨后端比较没有独立 holdout，保留探索性质，
完整四项结果见 `default-backend-comparison.*`，不把它冒充预先冻结的主要对比。
carbon、HostStaged、P1、空 interior 和高重建场景继续使用 full。

使用前保持同资源 full 对照；科学输出、绑定、backend 或执行模式异常时立即停止实验并回 full。
体系/盒形/重建比例/设备环境变化或净收益不能稳定复现时，保持 full，并重新独立验证。
现有 rebuild/M1/empty 等 full fallback 保留。没有据本轮 interior 或 rank 数拟合自动阈值；
自动采用策略与默认变更属于后续独立生产决策。本次交付保持 `DMGMD_DOMAIN_OVERLAP=off`。

## 复核入口与剩余范围

原始数据位于 [独立 O5 目录](../../../dmgmd-overlap-o5-20261016/)。主要入口：

- `frozen-plan.json`、`continuation-plan.json`、`extension-plan.json`：冻结身份、顺序与续跑。
- `main/`、`main-20261019/`、`extension/`、`adoption-validation/`：全部性能原始输出/命令/环境。
- `diagnostic/` 保留旧记录；`diagnostic-20261021/` 为直接 rank 日志补采；`profiles/` 为 Nsight。
- `trial-analysis.json`、`trials.csv`、`summary.*`、`ratios.*`、`scaling.*`：全部样本与统计。
- `analysis-first5/`、`analysis-first5-recomputed/`、`interruption-sensitivity.*`：预定追加依据与续跑敏感性。
- `cpu-formal-loop.*`、`diagnostic-stages.*`、`gpu-mapping-audit-20261021-v2/`：采样窗口、阶段与映射。
- `source-final.json`/patch、`frozen-identity-final-check.json`、`reference-final-check.json`：源码/二进制及三个只读 reference 末次审计。
- `raw-evidence.sha256`、`raw-evidence-inventory.json`、`all-attempts.*`：原始证据完整性、失败及未完成项。

在仓库根目录一键离线重算（不启动 GPU）：

```bash
bash dmgmd-overlap-o5-20261016/recompute.sh
```

该入口加载唯一受支持环境，校验分析工具/原始证据哈希，重算初轮五重复并断言与冻结统计
相同，再重算最终配对区间、CPU marker、诊断、Nsight、独立采用验证和图表。
详细命令也在 [benchmark README](../../../tests/benchmark/README.md#o5-统一绑定与正式实验)。

尚未验收的范围保留在计划：GPUMD 合计 P×K 绑定、LAMMPS 固定绑定科学门槛/三引擎正式矩阵、
任意 scheduler/cpuset 宿主集成、跨节点、内存页驻留、完整 C2/C3、其他盒形/势及自动策略。
三个 reference 的 HEAD、Git 状态与既有 ignored 文件逐项未变；源码/运行库/输入/二进制
冻结身份末次核验全通过。原始证据哈希及初轮统计一致性也通过。
历史小接口成本精度与 R 的时钟/完成误差仍 UNKNOWN/INCONCLUSIVE，不伪装成完整可分解
指令成本或精确网络 ratio；这些限制不以脚本已实现、正确性通过或获得正加速替代。
