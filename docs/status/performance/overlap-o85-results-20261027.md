# O8.5：只拆 producer、完整执行力计算（2026-10-28）

类别：独立执行候选与整步对照。状态：**COMPLETE**（2026-10-27 开始，10-28 完成）。
实现、完整正确性门槛、120 个正式 trial、24 份 CUDA 时间线、8 个 CPU 诊断和离线复算齐全。
四组拆分成本下降；相对同后端候选 full 三组有净收益，carbon/HostStaged 仍慢。
相对 HostStaged/full，仅本轮 water/P8/HostStaged 显示 1.0193× [1.0155, 1.0256]。
这是探索结果，交 O9 用新样本独立确认；**未采用**，生产默认仍
HostStaged/full、overlap=off、schedule=split。O9 未在本任务执行。

## 身份与实施范围

起始 HEAD 为 `33c7b7f41b9ea5e2d374e915614fbf5b41410fd7`，保留已有未提交 O6.5
源码和文档。起始源码、dirty patch、三个只读 reference 的 HEAD/status/全部原有 ignored
文件哈希、独立二进制、输入、固定资源与实际命令保存在
`dmgmd-overlap-o85-20261027/`，没有覆盖历史 O1–O9/O6.5 样本。

| 二进制 | SHA-256 | 用途 |
| --- | --- | --- |
| 当前 O7+O8 基线 | `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32` | 原 full / 原 split-serial / 原 overlap-wait |
| O8.5 候选 | `e28534ba121019d9bc1cd8b0506a22fb82e8906fa249b435b4a8bf4fbf955092` | 新 full / producer-serial / producer-wait |

候选构建为 Release、CUDA architecture 89、O6.5 diagnostics OFF。
`DMGMD_DOMAIN_SCHEDULE=producer` 是显式实验选择，原完整 split 仍保留；
partition=off、P1/M1、setup/rebuild/迁移、空 interior/local 与未证明模型维持 full。

`compute_domain` 仅分离 descriptor 与 force consumer 的主机调度；现有 E/H 与 key、
粒子/邻居顺序、L stride、精度/表达式、halo 字节与后端、默认 stream 不变。
schedule 的 rank 一致性检查只在启动时新增指纹归约，不增加逐步通信或 neighbor sink。
`dependency-proof.json` 确认 NEP 原始数值 kernel、small-box 路径、potential/neighbor
及 GPU 分类器相对起始源码逐字节未改。依赖、唯一写入与验证合同见
[分区标准](../../standards/domain-partition.md#o85-producer-调度)。

## 依赖、唯一写入与实际覆盖

`compute_domain` 的 descriptor 只读中心坐标邻域并写本中心 PE/Fp/sums；radial 读取
本中心及邻居 Fp，angular partial 读取中心 Fp/sums，many-body 读取双向 partial。
因此只允许 E 的 descriptor 提前；全部 D 的 descriptor 在 halo 完成、H 完成后就绪，
再执行完整 radial(O) → partial(D) → many-body(O) → ZBL(O)。每中心的邻居顺序、
PE 写入和 radial→many-body→ZBL 累加顺序保持，owned 与 ghost 写权限不变。

```text
prepare → begin halo → clear once → filter(E) → descriptor(E)
→ finish halo / physical unpack → filter(H) → neighbor record if due → descriptor(H)
→ radial(O) → angular_partial(D) → many_body(O) → ZBL(O) if enabled → finalize
```

producer-serial 先 finish halo，再执行同样的两段 producer 和完整 force。E/H 继续互补
覆盖 D，I/B 分类与缓存 key 仍保留，未引入 compact list、第二 stream 或新通信协议。
完整 split 的基础 launch 为 10，producer 为 7，full 为 5；可选 ZBL 在 producer owned
上仅一次。startup schedule 指纹使用两次 Allreduce，各 rank 一致后才启动模拟。

验证使用私有 ghost 坐标 NaN；early 的 filtered rows、PE/Fp/sums 对照同快照 full
**descriptor 后、ZBL 前**的 oracle。所有最终 force、9 个 virial 分量仍为零，partial
仍为 NaN，非 E producer 不写；最终完整 PE/force/virial、有效 partial 及 ghost scratch
逐位一致。原完整 split 的验证独立保留。`coverage-proof.json` 记录 504 条 E\I 非空、
516 条小 I/B 混合 block/尾块、876 条小 E/H 混合 block/尾块和 8696 条 bitwise/poison 记录。
同 shape gid 交换实证中 O=2/D=3/L=4、storage_generation=1 不变，gid 1/3 在 step 3
交换 owner，layout 1→2；step 4 重新分类、oracle/poison 通过，未发生 GPU storage 分配。

## 环境、输入与固定资源

主机 n4：双 AMD EPYC 7K62（每 socket 48 核，SMT2，共 192 OS CPU）、双 NUMA、
8 张 RTX 4090；driver 580.173.02，CUDA 12.9.86，Open MPI 5.0.10，UCX 1.22.0，
Nsight Systems 2025.1.3.140，Python 3.12.3。构建/每个 GPU shell 均先 source
`../env/md-mpi.sh`。沙箱隔离的 driver 失败后在宿主机执行门槛及采样，未更换 MPI/UCX。
完整 linkage、环境入口/输入/source hashes、binding audit 与逐 trial 实际命令保存在产物中。

每 rank 四物理核及相应 SMT，rank 0–3 使用 NUMA0 的物理核 0–15，rank 4–7 使用
NUMA1 的 48–63；每 rank 连续四核，SMT OS CPU 为对应编号加 96。P2 使用 P8 的前缀。
rank→GPU UUID 固定，按下表顺序；完整映射见 `frozen-plan.json` 的 bindings/identity。

| rank | GPU UUID |
| --- | --- |
| 0 | `GPU-a4cdc2a3-d216-af6d-26bb-d6a5c81e2a80` |
| 1 | `GPU-d146f2b8-3d3d-4b60-b4a5-1b9ed07f2900` |
| 2 | `GPU-8264a4fc-58ff-d92c-b45c-1ae435b9574d` |
| 3 | `GPU-411245be-070f-01e4-a956-fbad65a7a003` |
| 4 | `GPU-9ea64601-9658-26c8-f8de-7a5b317bb1ad` |
| 5 | `GPU-ea45f080-5f8c-7636-a559-6aa4715ea7df` |
| 6 | `GPU-e99f226f-8450-fa78-a5bc-bc89221036b7` |
| 7 | `GPU-1c95c731-16e1-b32e-0da6-4cd86bf11262` |

输入延用当前 O8 已验收的真实体系，未换小规模：

| fixture / 原子数 | model.xyz SHA-256 | nep.txt SHA-256 |
| --- | --- | --- |
| carbon_200k / 196608 | `07c87849e5afdea3236fc46c8ba961477a35744451fcd697af7c250d4292df79` | `add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623` |
| water_400k / 393216 | `09236e70f8a0a8ad21cd55ea45a63b2222ba469c5d38a7c6d85c45a235f831c1` | `8638300c8c6ba7eca589fa2fdc111d2e6a1f6ad9c88cc522ea9db220538a66e3` |

未锁频、非独占；NUMA CPU affinity 已核验，物理内存页驻留仍 UNKNOWN。

## 预先冻结的实验

核心范围为真实 196608-atom carbon/P2 与 393216-atom water/P8，双后端、每 rank
四物理核及其 SMT CPU、固定 UUID 与 NUMA 映射。基线与候选共六组合，每配置五个
独立进程配对块，共 120 个正式 trial；原始 O8 输入逐文件核对，没有换小体系。
pilot/诊断/主样本独立，主样本关闭 trace/profiler/verify，正式段 CPU 为 null。

pilot/main/profile/bootstrap seed 分别为 2026102781/82/83/84。正式共同步数按
每 scope 最快 pilot 冻结，目标 25 秒、至少 10 秒；比值为中位数之比，20000 次保留块
配对 bootstrap、点态 95% 区间。任何主比值区间宽度/估计超过 10% 时，预先规定追加
该 scope/backend 五个完整块一次。无效尝试保留，替换必须另行冻结整块，不删慢样本。

独立诊断计划为 24 份六组合 Nsight trace、8 个 producer serial/wait CPU 作业。
Nsight 正式窗口 128 步；CPU 为保证既有 80% marker 覆盖另用 carbon 700 / water 1800
步，采样前修订保存在 `diagnostic-amendment.json`。未锁频、非独占，物理内存驻留 UNKNOWN。

## 已完成的正确性门槛

- 两份二进制分别通过八 rank 环境预检与 committed golden 4/4。
- 最终 GPU CTest 18/18，无 skip；GPU precondition/ticket/workspace/box 检查覆盖两种 schedule。
- producer MPI 主矩阵 272 配置通过，包含 off/serial/verify/verify-wait、P2/P4、双后端，
  加共同 P1/M1 fallback、cache/timing/error 等共 313 次执行。
- 所有 313 次保存的实际 CONFIG 均为 producer；13044 条真实 split 记录的基础 dispatch
  为 2/2/1/1/1，不能把 full fallback 当作候选。完整字节记账和既有 manifest 容差保持。
- 受控延迟/有限 progress/错误退出/非法与 rank 不一致配置共 27/27 通过。
- 完整 nightly 42/42 配置、12/12 跨 rank restart、572 个执行 stage 一次通过；
  三个基础体系执行长程 NVE/NVT/双向回放，四个兼容性变体执行 manifest 规定的静态/短轨迹。

MPI 进程启动后新增的 CONFIG-only runner 断言没有影响已加载脚本或 GPU 二进制；
原加载脚本保存为 `mpi-producer-loaded-runner.py`，对全部原始 CONFIG 与 dispatch 的
独立复核另存 `mpi-execution-proof.json`。最终源脚本与冻结身份一同保存。
采样前另外收紧了 collector 的 all-fallback 拒绝单测和最终门槛日志校验；原冻结计划及
三个 collector 的精确源码均保留，修订见 `predata-amendment.json`，未改变二进制或实验设计。
首次采集器启动在创建 trial 前因遗漏继承基类的 `o4` 身份别名退出；修复为保留继承别名，
增加对应回归，六个 collector 单测通过。`collector-amendments.json` 将原/新源码哈希与
验证日志串联，原 frozen plan、失败日志和修复前源码保留，二进制/资源/统计设计未改。

## 六组合正式墙钟

24 个短科学门槛作业与 24 个 pilot 均通过，六组合相同窗口的 thermo/neighbor 科学文件
逐字节一致。正式步数冻结为 carbon 1700、water 4500（warmup 分别 200/1600）；
120 个正式 trial 全部通过，未替换样本，未触发追加规则。正式段耗时范围
23.215–35.841 s，保留全部慢样本。各正式 trial 的科学输出也按相同窗口逐字节复核。

下表为五个独立配对块的中位数，单位 ms/step；原为基线二进制，新为候选二进制。
逐 trial、min/max、SD、MAD 见 `main-trials.csv` 与 `wall-analysis.json`。

| fixture / P | 后端 | 原 full | 原 serial | 原 wait | 新 full | producer serial | producer wait |
| --- | --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/P2 | HostStaged | 15.545664 | 20.250384 | 20.159028 | 15.509280 | 16.064760 | 16.002162 |
| carbon_200k/P2 | CudaAware | 16.360019 | 20.943085 | 20.146640 | 16.421418 | 16.780349 | 15.910514 |
| water_400k/P8 | HostStaged | 5.280156 | 6.062475 | 5.923607 | 5.265650 | 5.338881 | 5.165965 |
| water_400k/P8 | CudaAware | 6.305090 | 7.112188 | 5.777409 | 6.299123 | 6.390395 | 5.772616 |

所有下表比值为**分子中位数 / 分母中位数**；大于 1 表示分母更快。
区间是预先冻结的 20000 次配对块 bootstrap、点态 95% CI，无多重比较校正，不能代替
独立确认。ms 差为对应两个中位数之差，不等同于单步关键路径拆账。

| fixture / 后端 | 原 full / 新 full | 原 serial / producer serial |
| --- | --- | --- |
| carbon_200k/HostStaged | 1.0023 [0.9945, 1.0055] | 1.2605 [1.2435, 1.2698] |
| carbon_200k/CudaAware | 0.9963 [0.9888, 1.0043] | 1.2481 [1.2337, 1.2564] |
| water_400k/HostStaged | 1.0028 [0.9973, 1.0074] | 1.1355 [1.1303, 1.1434] |
| water_400k/CudaAware | 1.0009 [0.9934, 1.0065] | 1.1129 [1.1091, 1.1210] |

四组新旧 full 区间均跨 1；未检测到稳定差异，不能据此宣称性能等价。原 serial 与
producer serial 的四组区间下界均大于 1，拆分粒度的整步额外成本明显下降：

| fixture / 后端 | 原 serial − 原 full (ms) | producer serial − 新 full (ms) | 原 serial / 原 full | producer serial / 新 full |
| --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 4.704720 | 0.555480 | 1.3026 [1.2960, 1.3157] | 1.0358 [1.0297, 1.0446] |
| carbon_200k/CudaAware | 4.583066 | 0.358931 | 1.2801 [1.2719, 1.2983] | 1.0219 [1.0146, 1.0364] |
| water_400k/HostStaged | 0.782320 | 0.073231 | 1.1482 [1.1467, 1.1546] | 1.0139 [1.0022, 1.0214] |
| water_400k/CudaAware | 0.807098 | 0.091271 | 1.1280 [1.1235, 1.1345] | 1.0145 [1.0088, 1.0196] |

producer 仍有正的拆分成本，不能把旧额外成本的下降写成全部隐藏或零成本。

| fixture / 后端 | 原 serial / 原 wait | 原回收 (ms) | producer serial / wait | producer 回收 (ms) |
| --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 1.0045 [0.9985, 1.0177] | 0.091355 | 1.0039 [1.0000, 1.0159] | 0.062598 |
| carbon_200k/CudaAware | 1.0395 [1.0339, 1.0477] | 0.796444 | 1.0547 [1.0467, 1.0611] | 0.869835 |
| water_400k/HostStaged | 1.0234 [1.0214, 1.0290] | 0.138868 | 1.0335 [1.0269, 1.0394] | 0.172916 |
| water_400k/CudaAware | 1.2310 [1.2279, 1.2357] | 1.334779 | 1.1070 [1.0998, 1.1155] | 0.617779 |

carbon/HostStaged 的原 overlap 回收 INCONCLUSIVE；producer 的区间下界仅
1.000005（上表四位数舍入为 1.0000），属于边缘的点态结果，且最终仍输给 full。
water/CudaAware 的 producer 回收小于原完整 split，但 serial 成本也下降，最终 wait
中位数与原 wait 接近；不能只按“隐藏时间”大小选择候选。

| fixture / 后端 | 新同后端 full / producer wait | 原同后端 full / producer wait | 新 HostStaged/full / producer wait |
| --- | --- | --- | --- |
| carbon_200k/HostStaged | 0.9692 [0.9649, 0.9796] | 0.9715 [0.9703, 0.9766] | 0.9692 [0.9649, 0.9796] |
| carbon_200k/CudaAware | 1.0321 [1.0216, 1.0383] | 1.0283 [1.0206, 1.0318] | 0.9748 [0.9684, 0.9821] |
| water_400k/HostStaged | 1.0193 [1.0155, 1.0256] | 1.0221 [1.0145, 1.0251] | 1.0193 [1.0155, 1.0256] |
| water_400k/CudaAware | 1.0912 [1.0858, 1.0953] | 1.0922 [1.0860, 1.1011] | 0.9122 [0.9105, 0.9222] |

同后端三组净收益成立，carbon/HostStaged 失败。默认竞争路径只有
water/P8/HostStaged 的 producer-wait 更快；carbon/HostStaged 慢 0.492882 ms/step，
carbon/CudaAware 与 water/CudaAware 分别慢于新 HostStaged/full 0.401234/0.506966 ms/step。
使用**原** HostStaged/full 的对照结论一致，所有 44 个对照均保留在 `wall-contrasts.csv`。

## 实际执行、分类与通信诊断

24 份独立 Nsight trace 通过 cache inspection→本步 VV2 对齐正式窗口、CPU launch
correlation 和 stream。每 rank 的 carbon 128 步均普通步；water 为普通 124、重建 4
（3.125%）。普通步分别实际执行 full 5 / 原 split 10 / producer 7 次基础 launch，
producer 的 radial(O)/partial(D)/many-body(O) 均为完整 grid。重建步均 full，未将
fallback 当作候选执行；核心输入没有 ZBL，ZBL 的一次完整执行由 MPI 数值/计数覆盖。

下表为各 rank 普通步 kernel 平均耗时的中位数，单位 ms；原 serial 的 early/late
先在同 rank 相加，再跨 rank 取中位数。诊断独立且有 profiler 开销，不能与正式墙钟
相加或精确扣账；所有五类 kernel、分相和每次实际 launch 见 `kernel-steps.csv`。

| fixture / 后端 | 调度 | radial | partial | many-body | 基础 launch API / 普通步 |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | baseline/split-serial | 8.523220 | 2.808453 | 0.405256 | 0.046262 |
| carbon_200k/HostStaged | candidate/producer-serial | 4.663090 | 2.629424 | 0.312527 | 0.032934 |
| carbon_200k/HostStaged | candidate/producer-wait | 4.629934 | 2.631795 | 0.312391 | 0.033283 |
| carbon_200k/CudaAware | baseline/split-serial | 8.460874 | 2.808201 | 0.405361 | 0.046948 |
| carbon_200k/CudaAware | candidate/producer-serial | 4.651324 | 2.623626 | 0.313579 | 0.033346 |
| carbon_200k/CudaAware | candidate/producer-wait | 4.673826 | 2.622252 | 0.313763 | 0.034271 |
| water_400k/HostStaged | baseline/split-serial | 0.554654 | 0.938145 | 0.174162 | 0.046639 |
| water_400k/HostStaged | candidate/producer-serial | 0.297626 | 0.569602 | 0.120879 | 0.032777 |
| water_400k/HostStaged | candidate/producer-wait | 0.299889 | 0.570039 | 0.121063 | 0.033983 |
| water_400k/CudaAware | baseline/split-serial | 0.541465 | 0.932503 | 0.173514 | 0.046829 |
| water_400k/CudaAware | candidate/producer-serial | 0.300763 | 0.570115 | 0.120760 | 0.033199 |
| water_400k/CudaAware | candidate/producer-wait | 0.298879 | 0.572349 | 0.120478 | 0.034083 |

候选 full 的对应 trace 也独立保存。例如 carbon/HostStaged 的 full radial 为
4.699390 ms，producer serial 为 4.663090 ms，原 serial 两相合计 8.523220 ms；
water/HostStaged full partial 为 0.572497 ms，producer serial 为 0.569602 ms，
原 serial 两相合计 0.938145 ms。该结果支持减少重复掩码 force 执行的归因；
不是对 cache/带宽等微架构原因的硬件计数器证明。本轮未请求 NCU 计数器。

trace 中每 rank 的 carbon 实际分类 1 次、water 4 次；普通步其余命中缓存。
下表分类含这些真实维护成本并在普通步摊销；同步及 Waitall 为每 rank 普通步中位数，
再跨 rank 取中位数，单位 ms。HostStaged 无对应 device-buffer 输入/输出同步事件，
用“—”表示不适用，不把缺失事件当作零耗时。

| fixture / 后端 | 调度 | 分类 / 普通步 | MPI Waitall | input sync | output sync |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | producer-serial | 0.007817 | 0.073392 | — | — |
| carbon_200k/HostStaged | producer-wait | 0.007853 | 0.069586 | — | — |
| carbon_200k/CudaAware | producer-serial | 0.007877 | 1.192182 | 0.004077 | 0.002439 |
| carbon_200k/CudaAware | producer-wait | 0.007934 | 1.277074 | 0.004140 | 4.185870 |
| water_400k/HostStaged | producer-serial | 0.007972 | 0.157535 | — | — |
| water_400k/HostStaged | producer-wait | 0.007993 | 0.167926 | — | — |
| water_400k/CudaAware | producer-serial | 0.007827 | 1.886645 | 0.003945 | 0.002550 |
| water_400k/CudaAware | producer-wait | 0.008073 | 1.904897 | 0.003972 | 0.002738 |

carbon/CudaAware producer-wait 的 output sync 中位数约 4.186 ms，包含等待已排队
GPU 工作；不能把它当作纯通信开销再与 early kernel 相加。生命周期和 readiness 检查未删除。
普通步常规全局 OR 仍一次，128-step 诊断中末次 thermo 另有一次归约（所以范围 1–2）；
neighbor sink 与 halo 消息/字节顺序由 MPI 完整记账校验。原始 trace 的逐 rank
collective、halo bytes、Waitall 与同步范围均在 `kernel-trials.json`。

正式段 carbon 每 rank 0 次重建/迁移，water 每 rank 117 次重建且均迁移
（117/4500 = 2.6%）；water workspace_updates 为 110–116。所有 120 个正式 trial、
全部 rank 的 `gpu_allocations=0`、`capacity_growth_events=0`。这不表示所有未来体系
无需分配；本次容量已在 warmup 建立。

## CPU、内存与测量边界

8 个独立 producer CPU 作业的正式段 marker 完整，逐 rank 覆盖 97.35–99.38%，高于
既有 80% 门槛。下表为 rank 平均 core equivalent 的中位数和范围，1 代表占用一个逻辑
CPU 的运行时间；资源仍固定为四物理核/rank，未增加 progress thread。

| fixture / 后端 | 调度 | rank core equivalent 中位数 [min, max] |
| --- | --- | --- |
| carbon_200k/CudaAware | producer-serial | 1.009054 [1.008621, 1.009486] |
| carbon_200k/CudaAware | producer-wait | 1.009098 [1.009097, 1.009099] |
| carbon_200k/HostStaged | producer-serial | 1.005048 [1.000920, 1.009176] |
| carbon_200k/HostStaged | producer-wait | 1.005465 [1.000924, 1.010005] |
| water_400k/CudaAware | producer-serial | 1.000459 [1.000000, 1.009665] |
| water_400k/CudaAware | producer-wait | 1.005191 [1.000001, 1.009445] |
| water_400k/HostStaged | producer-serial | 1.004576 [0.999970, 1.010174] |
| water_400k/HostStaged | producer-wait | 1.008764 [0.999990, 1.009390] |

各 rank /proc 读窗口不同，`synchronized_job_cpu=null`；不虚构同步 job 总利用率。
主墙钟 trial 无正式 CPU marker，`formal_cpu=null`，CPU 诊断不混入主样本。

producer 复用现有 mask 和 NEP scratch，没有新增 compact list/device 工作数组；verify
专用 oracle/poison 不在正式性能样本启用。mask 逻辑存储为 12L+12 bytes，诊断观察到
每 rank carbon 最大 1394700 bytes、water 最大 924732 bytes；capacity 高水位与
设备全局 `memory.used` 含义不同。下表是 5 个正式作业进程生命期（含启动/warmup）
全部所用设备的观察高水位，单位 MiB，不能归因为本进程分配或正式段独占内存：

| fixture / 后端 | 原 full | 新 full | producer serial | producer wait |
| --- | --- | --- | --- | --- |
| carbon_200k/HostStaged | 5249 | 5249 | 5249 | 5249 |
| carbon_200k/CudaAware | 5761 | 5761 | 5761 | 5761 |
| water_400k/HostStaged | 5299 | 5299 | 5299 | 5299 |
| water_400k/CudaAware | 5813 | 5813 | 5813 | 5813 |

`telemetry-analysis.json/csv` 保留逐 trial/rank 的温度、功率、SM/memory clocks、memory
与 GPU utilization，`report-metrics.json` 给出报告聚合。采样包括启动时空闲频率；
未锁频、非独占与已有其他驻留进程是实测条件，物理内存驻留 UNKNOWN。

结论限于本机、这两份输入、P2/P8、双后端、固定 K=4 绑定和 wait；未测 poll 的主性能、
water/P4、carbon_1m、跨节点、其他 GPU 或长期 release。本轮没有 GPUMD 同资源性能
对照，不能据消除 newmd 拆分回归主张跨引擎算法优势。

## 命令、封存与 O9 交接

所有 GPU 工作逐 shell 使用统一环境。精确构建和门槛命令保存在 `gate-command.json`；
例如本次候选 MPI 与完整 nightly 为：

```bash
source ../env/md-mpi.sh
python3 tests/mpi/run_mpi_domain.py --candidate ./dmgmd-overlap-o85-20261027/dmg-md-candidate --devices 0,1,2,3 --ranks 2,4 --backends HostStaged,CudaAware --partition-modes off,serial,verify,verify-wait --schedule producer --work-root dmgmd-overlap-o85-20261027/mpi-producer
TMPDIR="$PWD/dmgmd-overlap-o85-20261027/nightly-tmp" python3 -u tests/long_nve/run_long_nve.py --candidate ./dmgmd-overlap-o85-20261027/dmg-md-candidate --devices 0,1,2,3 --profile nightly --partition-mode serial --schedule producer --overlap-mode wait --keep-work --ui plain --report dmgmd-overlap-o85-20261027/nightly.json
```

collector 按 freeze → gates → pilot → main → walls → extend → profiles → cpu → all
执行；各 `*-command.json`、`*-run.log`、逐 job `result.json` 保存实际命令、输入/二进制
哈希、绑定及退出码。nightly 572 个 stage 全部首次通过，无重试。首次 collector 的别名
错误发生于创建科学 trial 前，原失败保留；修复后全部采集完成，额度暂停未中断后台作业。
采集/复算方式见[benchmark README](../../../tests/benchmark/README.md#o85-producer-拆分实验)。

产物根目录为 `dmgmd-overlap-o85-20261027/`：

- `initial-source/`、`initial.patch` 和 `initial-source.json` 保留任务开始时的 O6.5 dirty 身份。
- `final.patch` 是相对上述起始源码的 O8.5 patch，排除已有 O6.5 修改；`final-source.json`
  记录最终源码及辅助交接文件的哈希，`analysis-source/` 保存本轮源码、manifest、工具与环境入口。
- `dmg-md-baseline`、`dmg-md-candidate` 固定独立二进制；`handoff.json` 保留 O9 所需
  二进制/patch 身份、全部正负对照和未验证范围。
- `raw-inventory.json` 固定所有原始输入、日志、正确性产物、trace/SQLite、CPU、源码和
  二进制的 SHA-256。最终 reference 核对与起始 HEAD/status/ignored 逐文件哈希完全一致：
  GPUMD 原有 ignored 208 个，两个 LAMMPS 0 个，无新增或改写。
- `recompute.sh` 从封存的 `analysis-source` 无 GPU 重算墙钟、全部 trace、CPU、遥测及
  报告聚合；不依赖未来工作树源码，不启动 GPU/MPI，不重新采集样本。

本次封存与无 GPU 独立复算使用：

```bash
python3 tests/benchmark/recompute_overlap_o85.py --seal --root dmgmd-overlap-o85-20261027
bash dmgmd-overlap-o85-20261027/recompute.sh
```

实现与正确性为 PASS；性能结论为**拆分成本下降、同后端三组净收益、默认路径净收益
限于本轮 water/P8/HostStaged**。该候选与 O6.5 的 compact 方向交 O9 筛选；确认数据须
使用未参与探索的新配对块，保留原 full/完整 split/producer 的独立对照。生产默认不变，
O9/O10/O11、commit 和 push 均未执行。
