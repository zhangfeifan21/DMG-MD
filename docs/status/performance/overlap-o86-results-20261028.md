# O8.6：producer 紧凑索引与几何内缩（2026-10-28）

类别：独立执行候选与整步对照。状态：**COMPLETE**。B 保留原 E/H，C 从实际 Verlet 参考 q
做几何内缩；两者只紧凑执行 filter/descriptor，全部 force consumer 继续完整执行。
数值门槛、正式配对、独立 trace/CPU 和离线复算齐全。**未自动采用**，生产默认仍为
HostStaged/full、overlap=off、schedule=split；本报告数据属于探索，O9 必须独立确认。

## 身份、执行范围与依赖

起始 HEAD 为 `33c7b7f41b9ea5e2d374e915614fbf5b41410fd7`，保留已有 O6.5/O8.5 未提交修改。
原始状态、源码、patch 和三个只读 reference 身份位于 `dmgmd-overlap-o86-20261028/`。
`baseline-source-proof.json` 确认任务开始时与已验收 O8.5 对照的 33 个生产源码哈希一致。

| 二进制 | SHA-256 | 用途 |
| --- | --- | --- |
| baseline | e28534ba121019d9bc1cd8b0506a22fb82e8906fa249b435b4a8bf4fbf955092 | O8.5 A、原 full/split |
| candidate | 46e6b9f341a44aea67de79679e2e6e2bb75421a3f2be210357bae35b0cdf2e1d | 同二进制 A/B/C、原 full/split |

构建为 Release、CUDA architecture 89、O6.5 diagnostics OFF；统一入口为 `source ../env/md-mpi.sh`。
本机 n4、8×RTX 4090，driver 580.173.02、Open MPI 5.0.10、UCX 1.22.0。性能作业固定
K=4 物理核/rank、既有 CPU/NUMA 绑定和 GPU UUID 映射；本任务 GPU 作业串行。
未锁频、非独占，物理内存驻留 UNKNOWN。输入沿用已验收 carbon_200k/P2 和 water_400k/P8，
输入/库/工具链哈希、实际绑定、遥测和精确命令见 frozen-plan、launch 与 audit 产物。

B 使用原图规则 E/H，以 GPU exclusive scan 和 stable scatter 建立两个原 local slot
升序列表。C 不计算 S/I/B，直接使用实际 V 的重建参考坐标 q：
`R_V=nextafter((rc+skin)*(1+32*FLT_EPSILON)+32*FLT_EPSILON*axis_length,+inf)`，
选择 owned slab 两端距离严格大于 R_V 的中心。R_V 包含 skin，未使用两跳 coordinate halo。
每次 miss 都检查实际完整 `V(E_geo) ⊆ O`；规则/轴/slab/内缩宽度与完整分区代次一起绑定。
普通步固定 q、V 和 slot 身份；小位移跨边界仍受既有位移界保护，rebuild/迁移当步 full。
空 early 是明确 full fallback，空 late 跳过两次 producer launch。

`arithmetic-proof.json` 确认 filter/descriptor 的逐中心计算部分与起始源码逐字节相同，
radial、angular partial、ZBL kernel 原样保留。只有 `n1=center_idx[k]` 的中心映射改变；
原粒子数组、L stride、行序、浮点表达式/精度、所有权、halo 半径/字节/协议和默认 stream 不变。
E/H 互补覆盖 D，所有 descriptor 完成后才执行 radial(O)、partial(D)、many-body(O)、ZBL(O)。
因此每中心 producer 与最终 owned 写入仍唯一，early 不读 ghost、不写最终 force/virial/partial。
完整合同见[分区标准](../../standards/domain-partition.md#o86-producer-紧凑与几何调度)。

## 正确性与实际覆盖

| 门槛 | 实际完成 |
| --- | --- |
| 环境 | 两份二进制 P4 基础门槛及 P8 显式 device-pointer collective/p2p、唯一 GPU 绑定 |
| golden | 两份二进制均通过全部 4 个锁定 case；容差与 goldens 未修改 |
| GPU / CPU 工具 | GPU CTest 18 项 + O8.6 CTest 专项 1 项；O8.6 分析工具 7 个验证 |
| 紧凑 MPI | 两条路线分别完成 P2/P4 × 双后端 × off/serial/verify/verify-wait/verify-poll |
| 受控调度 | 两条路线各 27 个延迟/有界 progress/故障与配置不一致作业 |
| nightly | 两条路线各 42 配置 + 12 跨 rank restart，三组 physics 模型的长 NVE/NVT/回放；四组等价变体的 compatibility 静态/短程 |
| 真实大输入 | 12 对照 × 两体系 × 双后端 = 48 个短程作业，科学输出哈希逐组一致 |

GPU 列表测试覆盖 0/1/63/64/65/129/255 长度、空 early/late、稳定顺序/唯一覆盖、实际 V
闭包失败、新 Verlet、同 shape layout/gid、workspace 和分类规则变化。MPI verify 使用独立
CPU 图/几何 oracle、真实 ghost 坐标 NaN、ZBL 前 descriptor oracle，检查 force/全部九个
virial 仍零、partial 和非 E producer 未写，最终同快照 full 逐 bit 一致。
`coverage-proof.json` 要求两条紧凑路线在 NEP5、多组分/typewise、ZBL/flexible/typewise ZBL、
NVE/NVT、双后端的 P2 verify-wait/poll 上确有非空候选执行，禁止全 fallback 冒充通过。

nightly 的七个 case 按既有 manifest 区分 physics 与 compatibility；不能把四组等价势
短程结果写作其各自长 NVE/NVT 已通过。覆盖范围与参数仍以 manifest 为第一事实源。

首次 MPI 验收因旧工具固定要求七次 launch，在 E=D 的正确空 late 路径上失败。
已修正为按实际 E/D 检查一次或两次 producer launch，完整重跑通过；原失败目录/日志保留。
几何 P4 窄 slab 可能完全无 early，仍验证 full 字节与失效；受控延迟另用明确标识的
128 Å/16 原子宽 slab 证明实际 early。未改候选二进制或数值容差。
工具修订与冻结哈希链均保存；cache-miss 累计主机计时与 hit 检查分开解释。

## 正式整步配对

正式初始矩阵 240 个 trial，最终 240 个有效正式 trial。每组至少五个新配对块，
按冻结种子随机顺序独立进程执行；正式步数 {'carbon_200k': 1700, 'water_400k': 4600}，warmup 为 carbon 200 / water 1600。
pilot/诊断不并入主样本，每个主 trial 至少 10 s、目标 25 s。全部原始尝试保留，区间为
中位数之比的 20,000 次配对块 bootstrap 95% 点态区间；宽度超 10% 的追加规则事前冻结。
数据未与旧 O6.5/O8.5/O9 混合，未从探索结果直接作生产采用判断。

实际正式段为 23.369101–35.904136 s，全部超过最低时长。80 项区间的
最大相对宽度为 2.91%，本轮未触发 10% 的追加门槛，未追加或替换配对块。


下表单位 ms/step；A 是原 producer，B 是同 E/H 紧凑，C 是几何紧凑，均为新候选二进制。
原完整 split 和两份二进制的 full/A 桥接仍作为正式对照保留在 CSV/JSON。

| fixture / 后端 | full | 原 split serial | A serial / wait | B serial / wait | C serial / wait |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 15.591607 | 20.298350 | 16.212366 / 16.015477 | 15.351319 / 15.239358 | 15.383214 / 15.254980 |
| carbon_200k/CudaAware | 16.335985 | 21.006567 | 16.841991 / 15.990357 | 16.090292 / 15.326238 | 16.013859 / 15.234204 |
| water_400k/HostStaged | 5.277157 | 6.074785 | 5.335985 / 5.170301 | 5.290645 / 5.112917 | 5.263013 / 5.093000 |
| water_400k/CudaAware | 6.290193 | 7.106964 | 6.362803 / 5.732599 | 6.292139 / 5.679072 | 6.284041 / 5.689792 |

下表比值大于 1 表示分母更快。B/A 分离同集合映射及其列表维护的增量；C/B 分离几何规则
及分类维护的增量。相对默认 full 始终使用同一配对块的 HostStaged/full。

| fixture / 后端 | B/A serial | B/A wait | C/B serial | C/B wait |
| --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 1.0561 [1.0492, 1.0609] | 1.0509 [1.0397, 1.0556] | 0.9979 [0.9887, 1.0111] | 0.9990 [0.9945, 1.0091] |
| carbon_200k/CudaAware | 1.0467 [1.0422, 1.0616] | 1.0433 [1.0332, 1.0525] | 1.0048 [0.9908, 1.0119] | 1.0060 [0.9947, 1.0167] |
| water_400k/HostStaged | 1.0086 [1.0059, 1.0208] | 1.0112 [1.0098, 1.0148] | 1.0053 [1.0022, 1.0096] | 1.0039 [0.9963, 1.0080] |
| water_400k/CudaAware | 1.0112 [1.0095, 1.0125] | 1.0094 [1.0061, 1.0105] | 1.0013 [0.9989, 1.0026] | 0.9981 [0.9935, 1.0070] |

| fixture / 后端 | B 同后端净收益 | C 同后端净收益 | B 默认净收益 | C 默认净收益 |
| --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 1.0231 [1.0127, 1.0274] | 1.0221 [1.0188, 1.0290] | 1.0231 [1.0127, 1.0274] | 1.0221 [1.0188, 1.0290] |
| carbon_200k/CudaAware | 1.0659 [1.0532, 1.0789] | 1.0723 [1.0569, 1.0820] | 1.0173 [1.0137, 1.0253] | 1.0235 [1.0198, 1.0312] |
| water_400k/HostStaged | 1.0321 [1.0264, 1.0354] | 1.0362 [1.0315, 1.0362] | 1.0321 [1.0264, 1.0354] | 1.0362 [1.0315, 1.0362] |
| water_400k/CudaAware | 1.1076 [1.0954, 1.1086] | 1.1055 [1.0989, 1.1089] | 0.9292 [0.9184, 0.9304] | 0.9275 [0.9208, 0.9309] |

| fixture / 后端 | A serial-full ms | B serial-full ms | C serial-full ms | A serial-wait ms | B serial-wait ms | C serial-wait ms |
| --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 0.620759 | -0.240288 | -0.208393 | 0.196888 | 0.111962 | 0.128234 |
| carbon_200k/CudaAware | 0.506006 | -0.245693 | -0.322126 | 0.851634 | 0.764054 | 0.779655 |
| water_400k/HostStaged | 0.058828 | 0.013489 | -0.014143 | 0.165684 | 0.177728 | 0.170013 |
| water_400k/CudaAware | 0.072610 | 0.001946 | -0.006152 | 0.630204 | 0.613067 | 0.594250 |

本轮 B/A 的 serial 和 wait 四组区间均在 1 以上；wait 增量为 carbon 的约 4.33%–5.09%、
water 的约 0.94%–1.12%。C/B 的四组 wait 区间均跨 1，不能确认几何规则额外提高整步速度；
serial 只有 water/HostStaged 的区间确认约 0.53% 增量。两者的比较均包含实际列表/分类维护。

B/C 相对同后端候选 full 的四组净收益均获本轮探索区间支持。相对 HostStaged/full，
carbon 的两个后端及 water/HostStaged 的 B/C 均有正净收益；water/CudaAware 的 B/C 比值
分别为 0.9292 [0.9184, 0.9304]、0.9275 [0.9208, 0.9309]，仍慢于默认路径。

carbon 的 B/C serial-full 已为负值，说明本轮净收益不全来自通信重叠。HostStaged 的
B/C serial→wait 回收比值分别为 1.0073 [0.9927, 1.0123]、1.0084 [0.9982, 1.0215]，均未确认额外回收；
其余三个 scope/backend 的 B/C 回收区间均在 1 以上。不能把这些整步差值当作纯网络隐藏时间。


| fixture / 后端 | full 桥接 | 原 split 桥接 | A serial 桥接 | A wait 桥接 |
| --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 0.9950 [0.9875, 1.0013] | 0.9978 [0.9895, 1.0004] | 0.9943 [0.9913, 1.0054] | 0.9990 [0.9933, 1.0027] |
| carbon_200k/CudaAware | 0.9952 [0.9813, 1.0072] | 0.9966 [0.9854, 1.0023] | 1.0002 [0.9852, 1.0134] | 0.9952 [0.9910, 1.0054] |
| water_400k/HostStaged | 0.9976 [0.9966, 1.0017] | 1.0035 [0.9952, 1.0098] | 1.0025 [0.9905, 1.0084] | 0.9996 [0.9986, 1.0046] |
| water_400k/CudaAware | 0.9973 [0.9971, 0.9995] | 0.9991 [0.9972, 1.0015] | 1.0018 [0.9977, 1.0055] | 1.0018 [1.0004, 1.0018] |

桥接比值是 baseline/candidate；16 项中 14 项区间跨 1，这不构成等价性证明。
water/CudaAware 的候选 full 比基线略慢、候选 A wait 略快，幅度约 0.27%/0.18%。
B/A、C/B 和净收益主对比均使用同一候选二进制及同块控制，桥接差异仍完整保留。


完整 80 项比值/区间、桥接、全部单次 trial 和 telemetry 见 `wall-analysis.json`、
`wall-contrasts.csv`、`main-trials.csv`。绝对差是两个中位数的差，不能把差值逐项相加解释
关键路径或物理网络隐藏量。所有正负结果保留，点态探索区间不替代独立确认。

## 实际 GPU 执行、分区与维护

48 个独立 Nsight 进程按实际 cache inspection 到 VV2 对齐普通/重建步，逐步核对 5/10/7
基础流水线、紧凑 specialization、真实 grid/block 和原默认 integration stream；全部 consumer
仍是完整 O/D 网格。A/B 的逐 rank、逐步中心数一致性另有工具断言。
下表来自独立 wait trace，比例为每 rank 步中位数再取 rank 中位数；early GPU 是每 rank
步均值再取 rank 中位数，单位 ms。不用原子比例替代可隐藏时间，也不将诊断时间代替主墙钟。

| fixture / 后端 | 路线 | I/O（图规则） | E/D | H/D | E/O | early GPU ms | partition bytes 最大 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | producer-wait | 0.833333 | 0.842105 | 0.157895 | 0.916667 | 5.486729 | 1394700 |
| carbon_200k/HostStaged | compact-wait | 0.833333 | 0.842105 | 0.157895 | 0.916667 | 5.444059 | 2679819 |
| carbon_200k/HostStaged | geometry-wait | — | 0.837321 | 0.162679 | 0.911458 | 5.446281 | 1750027 |
| carbon_200k/CudaAware | producer-wait | 0.833333 | 0.842105 | 0.157895 | 0.916667 | 5.488945 | 1394700 |
| carbon_200k/CudaAware | compact-wait | 0.833333 | 0.842105 | 0.157895 | 0.916667 | 5.477085 | 2679819 |
| carbon_200k/CudaAware | geometry-wait | — | 0.837321 | 0.162679 | 0.911458 | 5.455350 | 1750027 |
| water_400k/HostStaged | producer-wait | 0.500900 | 0.591487 | 0.408513 | 0.747988 | 0.587501 | 924732 |
| water_400k/HostStaged | compact-wait | 0.500900 | 0.591487 | 0.408513 | 0.747988 | 0.584618 | 1672143 |
| water_400k/HostStaged | geometry-wait | — | 0.581360 | 0.418640 | 0.735277 | 0.573431 | 1055951 |
| water_400k/CudaAware | producer-wait | 0.500900 | 0.591487 | 0.408513 | 0.747988 | 0.584452 | 924732 |
| water_400k/CudaAware | compact-wait | 0.500900 | 0.591487 | 0.408513 | 0.747988 | 0.580173 | 1672143 |
| water_400k/CudaAware | geometry-wait | — | 0.581360 | 0.418640 | 0.735277 | 0.575875 | 1055951 |

下表把独立 wait trace 的 producer 两相和完整 consumer 分开，均为 rank 步均值再取
rank 中位数，单位 ms；CPU launch API 包含本步全部基础 NEP launch。单份诊断没有配对
显著性区间，其时间不替代无诊断主墙钟。

| fixture / 后端 | 路线 | early producer | late producer | 完整 force consumers | CPU launch API |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | producer-wait | 5.486729 | 2.536085 | 7.594842 | 0.033720 |
| carbon_200k/HostStaged | compact-wait | 5.444059 | 1.757122 | 7.607040 | 0.034223 |
| carbon_200k/HostStaged | geometry-wait | 5.446281 | 1.800414 | 7.783784 | 0.033788 |
| carbon_200k/CudaAware | producer-wait | 5.488945 | 2.549243 | 7.812301 | 0.033882 |
| carbon_200k/CudaAware | compact-wait | 5.477085 | 1.790108 | 7.695914 | 0.033569 |
| carbon_200k/CudaAware | geometry-wait | 5.455350 | 1.793041 | 7.689948 | 0.033822 |
| water_400k/HostStaged | producer-wait | 0.587501 | 0.536578 | 0.984401 | 0.033641 |
| water_400k/HostStaged | compact-wait | 0.584618 | 0.506922 | 0.991228 | 0.033294 |
| water_400k/HostStaged | geometry-wait | 0.573431 | 0.510489 | 0.990624 | 0.033237 |
| water_400k/CudaAware | producer-wait | 0.584452 | 0.539390 | 0.988156 | 0.033670 |
| water_400k/CudaAware | compact-wait | 0.580173 | 0.507938 | 0.998217 | 0.033124 |
| water_400k/CudaAware | geometry-wait | 0.575875 | 0.510756 | 0.998654 | 0.033218 |

A/B 的 E/D 完全相同。独立 trace 中 carbon 的 early 时间接近，late producer 从约
2.54 ms 降到 1.76–1.79 ms；water 的 late 从约 0.54 ms 降到约 0.51 ms。
这些实际执行差异支持紧凑映射方向，尚未单独证明寄存器、cache 或带宽层面的原因。
C 的 E/D 略低于 B，额外 wait 整步收益仍未确认。


`partition-steps.csv` 保留每 rank 的完整 E/H/O/D/L、I（仅图规则）、分类/复用及真实 grid。
C 的 `interior=0` 表示没有构建 I，不能作原图规则 I/O。`kernel-steps.csv` 保留每次实际
filter/descriptor 与完整 consumer 的 GPU/CPU launch 时间；`kernel-trials.json` 记录分类、
scan/scatter、hit/miss、halo/collective/Waitall/sync 和实际 stream。

B 的逻辑辅助空间为 `12L+12D+12+T` bytes，C 为 `4L+12D+12+T`，T 是 CUB 临时空间；
物理容量保留高水位。计数/错误回传复用三 int scratch（B 两次、C 一次）；列表在普通步复用，miss 的分配/扫描/计数同步与
容量增长都在真实整步里。`maintenance-trials.json` 是正式段 cache-miss host 包络摊销；
hit 成本另见 trace，完整 wall 对照包含它。serial 的包络可能包含前序默认 stream 工作，
不能当纯 GPU 分类成本。cold/warmup 首次与后续列表构建、临时/容量 bytes 另见
`report-metrics.json`，不能将稳态零重建解读为构建免费。

下表同样取 wait 诊断：cold 首次和后续 warmup miss 是 rank 中位数；hit 是普通步主机
检查时间的 rank 中位数。摊销来自无详细诊断的全部正式 trial/rank cache-miss 包络，
容量和 CUB 空间列出实际最大值，不能将它们等同于硬件物理驻留。

| fixture / 后端 | 路线 | cold 首次 ms | 后续 warmup miss ms | hit μs | 正式 miss 摊销 ms/step | capacity bytes | CUB temp bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | producer-wait | 1.087280 | — | 0.200500 | 0.000574 | 1743388 | 0 |
| carbon_200k/HostStaged | compact-wait | 1.100535 | — | 0.195500 | 0.000579 | 3349799 | 1023 |
| carbon_200k/HostStaged | geometry-wait | 0.311620 | — | 0.180000 | 0.000144 | 2187551 | 1023 |
| carbon_200k/CudaAware | producer-wait | 1.090140 | — | 0.190500 | 0.000577 | 1743388 | 0 |
| carbon_200k/CudaAware | compact-wait | 1.097720 | — | 0.195500 | 0.000581 | 3349799 | 1023 |
| carbon_200k/CudaAware | geometry-wait | 0.312542 | — | 0.195500 | 0.000145 | 2187551 | 1023 |
| water_400k/HostStaged | producer-wait | 0.322380 | 0.237686 | 0.182500 | 0.006221 | 1171672 | 0 |
| water_400k/HostStaged | compact-wait | 1.542690 | 0.239695 | 0.175750 | 0.006336 | 2105615 | 1023 |
| water_400k/HostStaged | geometry-wait | 0.248030 | 0.126903 | 0.190500 | 0.003170 | 1324519 | 1023 |
| water_400k/CudaAware | producer-wait | 0.329137 | 0.242415 | 0.205000 | 0.006185 | 1171672 | 0 |
| water_400k/CudaAware | compact-wait | 1.670295 | 0.246247 | 0.190250 | 0.006311 | 2105615 | 1023 |
| water_400k/CudaAware | geometry-wait | 0.215569 | 0.127845 | 0.205500 | 0.003173 | 1324519 | 1023 |

实际 trace 也保留 startup/warmup 的 safe/closed/geometry、scan/scatter/count kernel。
下表为这些选定 kernel 的累计 GPU 时间（rank 中位数，ms），包含全部 warmup miss；
不包含填零、分配、同步和 CPU 等待，因此不等于上表首次构建的完整主机包络。

| fixture / 后端 | A warmup 选定 GPU kernels | B warmup 选定 GPU kernels | C warmup 选定 GPU kernels |
| --- | --- | --- | --- |
| carbon_200k/HostStaged | 0.959955 | 0.968485 | 0.231289 |
| carbon_200k/CudaAware | 0.964292 | 0.968132 | 0.232440 |
| water_400k/HostStaged | 8.422058 | 8.569230 | 4.468199 |
| water_400k/CudaAware | 8.719293 | 8.892303 | 4.487947 |

serial 与 wait 的所有 rank 原值、miss/hit 分布及辅助 GPU kernel 时间均在
`kernel-trials.json` / `report-metrics.json`，正式 miss 次数与摊销在 `maintenance-trials.json`。
C 相对 B 的辅助逻辑空间峰值在 carbon/water 下降约 35%/37%，仍高于 A；正式 wait miss
包络摊销在 carbon 约为 B 的四分之一、water 约为一半。B 增加冷构建开销，C 减少分类维护，
两者成本均保留在表中；这些维护收益没有使 C/B wait 的整步增量得到本轮区间确认。

## CPU、内存与边界

24 个独立 CPU 诊断使用正式段 marker 和 /proc 样本，逐 rank 覆盖至少 80%。
主样本关闭详细诊断，`formal_cpu=null`。下表为 rank core equivalent 的中位数及范围；
1 表示一个逻辑 CPU 的运行时间，分配资源仍为每 rank 四物理核。

| fixture / 后端 | 模式 | rank CPU median [min,max] | coverage min–max |
| --- | --- | --- | --- |
| water_400k/HostStaged | geometry-wait | 1.001043 [0.998962, 1.009375] | 98.99%–99.00% |
| water_400k/HostStaged | producer-wait | 1.000008 [0.998960, 1.008174] | 97.88%–99.88% |
| water_400k/HostStaged | geometry-serial | 1.008681 [0.999982, 1.009348] | 96.37%–98.37% |
| water_400k/HostStaged | compact-wait | 1.000746 [1.000369, 1.008674] | 98.21%–99.30% |
| water_400k/HostStaged | compact-serial | 1.004585 [0.998965, 1.010182] | 97.89%–97.89% |
| water_400k/HostStaged | producer-serial | 1.000693 [0.999375, 1.009389] | 98.38%–98.42% |
| water_400k/CudaAware | compact-wait | 1.000554 [0.999307, 1.010453] | 97.30%–99.08% |
| water_400k/CudaAware | producer-wait | 1.000892 [0.999993, 1.008919] | 97.13%–98.90% |
| water_400k/CudaAware | geometry-wait | 1.005957 [0.997844, 1.008287] | 94.30%–96.08% |
| water_400k/CudaAware | compact-serial | 1.000413 [0.999974, 1.010151] | 97.04%–97.05% |
| water_400k/CudaAware | geometry-serial | 1.008910 [1.000009, 1.009353] | 98.28%–98.28% |
| water_400k/CudaAware | producer-serial | 1.008763 [0.999178, 1.009188] | 98.17%–98.18% |
| carbon_200k/HostStaged | geometry-wait | 1.000481 [1.000007, 1.000955] | 97.57%–97.58% |
| carbon_200k/HostStaged | geometry-serial | 1.009435 [1.009434, 1.009437] | 97.74%–97.74% |
| carbon_200k/HostStaged | producer-wait | 1.004915 [1.000000, 1.009830] | 98.50%–98.50% |
| carbon_200k/HostStaged | compact-serial | 1.009132 [1.008654, 1.009610] | 97.19%–97.20% |
| carbon_200k/HostStaged | compact-wait | 0.999522 [0.999038, 1.000007] | 97.59%–97.60% |
| carbon_200k/HostStaged | producer-serial | 1.008184 [1.008182, 1.008185] | 97.68%–97.68% |
| carbon_200k/CudaAware | producer-wait | 0.999745 [0.999667, 0.999824] | 97.91%–97.92% |
| carbon_200k/CudaAware | compact-wait | 1.004327 [1.000000, 1.008653] | 97.54%–97.54% |
| carbon_200k/CudaAware | producer-serial | 1.004314 [1.000006, 1.008621] | 98.07%–98.07% |
| carbon_200k/CudaAware | geometry-serial | 1.005002 [1.000912, 1.009091] | 96.93%–96.93% |
| carbon_200k/CudaAware | geometry-wait | 1.004721 [1.000001, 1.009441] | 98.21%–98.21% |
| carbon_200k/CudaAware | compact-serial | 1.008633 [1.008181, 1.009085] | 97.57%–97.57% |

各 rank /proc 读窗口不同，`synchronized_job_cpu=null`，不虚构同步 job 总利用率。
各模式约使用一个逻辑 CPU equivalent/rank，本轮未证明 CPU 使用量下降；诊断不构成 CPU
成本的配对显著性检验。原始计数、marker、映射、进程身份、采样区间和全部 CPU 汇总已离线
逐字段重建一致，正式段最小覆盖为 94.30%。旧采集器在 summary 前以 `trial_finished` 关闭，
又在 finally 中以 `trial_finally` 二次关闭，只改写 `collector-exit.json` 的原因字符串。
复算仅对双方退出码为 0 的这一已证明元数据差异做适配，保留原始文件和首次严格比较失败日志；
全部计数/窗口/状态/身份及其他字段仍必须精确一致，见 `raw-recheck-amendment.json` 和
`raw-recheck.json`。未修改冻结采集器、原始样本、正式统计或数值容差。

设备级 memory.used 包括其他驻留进程、启动/warmup，不等于本进程分配或正式段驻留。
实际 GPUVector allocation/capacity、workspace/rebuild/migration 与遥测见逐 trial 原始 summary
和 `report-metrics.json`；保持未锁频、非独占、物理内存驻留 UNKNOWN 的边界。

结论限本机、两份冻结输入、P2/P8、双后端、固定 K=4 绑定和 wait；未外推 poll、water/P4、
carbon_1m、跨节点、其他设备或长期 release。本轮没有同资源 GPUMD 性能对照，不能据此
主张跨引擎优势或将消除拆分成本等同于新物理算法收益。

## 命令、封存与 O9 交接

精确构建与 gate 命令在 `build-command.json`、`gate-command.json` 和构建日志中，正式采集命令与退出码在
`performance-command.json`，每 trial 的 launch/audit/input/binary hashes 和 raw logs 均保留。
流程为 freeze → 环境/golden/GPU/MPI/schedule/nightly → P8 环境与实际覆盖 → 科学对照
→ pilot → main → 必要追加 → 独立 trace → CPU → 离线分析。工具与复算入口见
[benchmark README](../../../tests/benchmark/README.md#o86-producer-紧凑索引与几何内缩)。

```bash
source ../env/md-mpi.sh
python3 tests/mpi/run_mpi_domain.py --candidate ./dmgmd-overlap-o86-20261028/dmg-md-candidate --devices 0,1,2,3 --ranks 2,4 --backends HostStaged,CudaAware --partition-modes off,serial,verify,verify-wait,verify-poll --schedule producer-compact --work-root dmgmd-overlap-o86-20261028/mpi-compact
# 几何路线同样单独运行，schedule=producer-geometry，使用独立目录。
python3 tests/benchmark/recompute_overlap_o86.py --seal --root dmgmd-overlap-o86-20261028
bash dmgmd-overlap-o86-20261028/recompute.sh
```

`initial-source/`、起始 dirty patch 与 reference 清单保留任务开始身份；`final.patch` 相对起始
源码隔离 O8.6 修改，`final-source.json` 与 `analysis-source/` 封存源码、工具、manifest 和环境。
`raw-inventory.json` 固定原始产物 SHA-256，`recompute.sh` 用封存源码无 GPU/MPI 重算所有
墙钟、trace、CPU、遥测和报告聚合，不依赖未来工作树。三个只读 reference 的最终状态、
全部原有 ignored 文件哈希与起始一致：GPUMD 原有 208 个，两个 LAMMPS 均为 0。

`handoff.json` 交付 A/B/C 的独立身份、全部正负区间与未验证范围。O9 必须用未参与选择的新
配对块筛选并确认；本次只完成 O8.6，没有执行 O9/O10/O11、生产采用、commit 或 push。
