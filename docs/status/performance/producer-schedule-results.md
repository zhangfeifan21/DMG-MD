# Producer 调度方案对照实测

目标：并列记录 O8.5 producer-only split 与 O8.6 紧凑索引/几何内缩候选的正确性、正式整步对照、实际执行诊断和未确认边界。

| 日期 | 阶段 | 结论 | 适用范围与限制 |
| --- | --- | --- | --- |
| 2026-10-27 开始、10-28 完成 | [O8.5 只拆 producer、力计算完整执行](#o85-results-20261027-report) | 完成 120 个正式 trial、CUDA 时间线、CPU 诊断和复算；多数组合拆分成本下降，候选相对 full 的净收益按组不同。 | 探索结果，未采用；生产默认保持 HostStaged/full、overlap=off、schedule=split。注意源文件名日期为 10-27，正文执行完成日期为 10-28。 |
| 2026-10-28 | [O8.6 producer 紧凑索引与几何内缩](#o86-results-20261028-report) | 完成候选 A/B/C 的正确性与正式整步配对，记录 GPU 执行、分区维护成本和资源边界。 | COMPLETE 仅指本轮实验完成；未自动采用，O9 独立确认仍是后续门槛；结论限冻结输入、P2/P8、双后端、K=4 和 wait。 |

以下各节按来源报告记录执行身份、环境、历史命令、统计数据、失败与未覆盖边界；标题层级仅为嵌入整合文档而调整。

> **保留与适用边界（2026-11-05）：** 本文保存公共共享服务器历史测量的结果、结论和适用限制；这些数据属于历史探索，不作为专用性能服务器的基准参考。按授权清理了对应的忽略目录 `dmgmd-overlap-*`，逐 trial 输出、日志、trace、原始输入副本及重算工具均不再保留。下文残留的旧文件名、路径和命令仅记载当时执行过程，不能用来访问产物或复算。后续专用服务器 benchmark 将单独记录为性能参考；本报告的数字、结论和生产默认不变。



<a id="o85-results-20261027-report"></a>

<a id="o85-results-20261027-o85只拆-producer完整执行力计算2026-10-28"></a>

## O8.5：只拆 producer、完整执行力计算（2026-10-28）

类别：独立执行候选与整步对照。状态：**COMPLETE**（2026-10-27 开始，10-28 完成）。
实现、完整正确性门槛、120 个正式 trial、24 份 CUDA 时间线、8 个 CPU 诊断和离线复算齐全。
四组拆分成本下降；相对同后端候选 full 三组有净收益，carbon/HostStaged 仍慢。
相对 HostStaged/full，仅本轮 water/P8/HostStaged 显示 1.0193× [1.0155, 1.0256]。
这是探索结果，交 O9 用新样本独立确认；**未采用**，生产默认仍
HostStaged/full、overlap=off、schedule=split。O9 未在本任务执行。

<a id="o85-results-20261027-身份与实施范围"></a>

### 身份与实施范围

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
partition=off、P1/全量复制模式、setup/rebuild/迁移、空 interior/local 与未证明模型维持 full。

`compute_domain` 仅分离 descriptor 与 force consumer 的主机调度；现有 E/H 与 key、
粒子/邻居顺序、L stride、精度/表达式、halo 字节与后端、默认 stream 不变。
schedule 的 rank 一致性检查只在启动时新增指纹归约，不增加逐步通信或 neighbor sink。
`dependency-proof.json` 确认 NEP 原始数值 kernel、small-box 路径、potential/neighbor
及 GPU 分类器相对起始源码逐字节未改。依赖、唯一写入与验证合同见
[分区标准](../../standards/domain-partition.md#o85-producer-调度)。

<a id="o85-results-20261027-依赖唯一写入与实际覆盖"></a>

### 依赖、唯一写入与实际覆盖

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

<a id="o85-results-20261027-环境输入与固定资源"></a>

### 环境、输入与固定资源

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

<a id="o85-results-20261027-预先冻结的实验"></a>

### 预先冻结的实验

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

<a id="o85-results-20261027-已完成的正确性门槛"></a>

### 已完成的正确性门槛

- 两份二进制分别通过八 rank 环境预检与 committed golden 4/4。
- 最终 GPU CTest 18/18，无 skip；GPU precondition/ticket/workspace/box 检查覆盖两种 schedule。
- producer MPI 主矩阵 272 配置通过，包含 off/serial/verify/verify-wait、P2/P4、双后端，
  加共同 P1/全量复制回退路径、cache/timing/error 等共 313 次执行。
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

<a id="o85-results-20261027-六组合正式墙钟"></a>

### 六组合正式墙钟

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

<a id="o85-results-20261027-实际执行分类与通信诊断"></a>

### 实际执行、分类与通信诊断

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

<a id="o85-results-20261027-cpu内存与测量边界"></a>

### CPU、内存与测量边界

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

<a id="o85-results-20261027-命令封存与-o9-交接"></a>

### 命令、封存与 O9 交接

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

O8.5 执行期间的产物根目录为 `dmgmd-overlap-o85-20261027/`，已于 2026-11-05 按授权清理。下列条目描述当时归档内容，当前不可访问：

- `initial-source/`、`initial.patch` 和 `initial-source.json` 当时记录任务开始时的 O6.5 dirty 身份。
- `final.patch` 是相对上述起始源码的 O8.5 patch，排除已有 O6.5 修改；`final-source.json`
  当时记录最终源码及辅助交接文件的哈希，`analysis-source/` 保存本轮源码、manifest、工具与环境入口；这些文件均已清理。
- `dmg-md-baseline`、`dmg-md-candidate` 当时固定独立二进制；`handoff.json` 记录 O9 所需
  二进制/patch 身份、全部正负对照和未验证范围。
- `raw-inventory.json` 当时记录原始输入、日志、正确性产物、trace/SQLite、CPU、源码和
  二进制的 SHA-256。最终 reference 核对与起始 HEAD/status/ignored 逐文件哈希完全一致：
  GPUMD 原有 ignored 208 个，两个 LAMMPS 0 个，无新增或改写。
- `recompute.sh` 从封存的 `analysis-source` 无 GPU 重算墙钟、全部 trace、CPU、遥测及
  报告聚合；不依赖未来工作树源码，不启动 GPU/MPI，不重新采集样本。

以下是当时封存与无 GPU 独立复算使用的历史命令，现已不可运行：

```bash
python3 tests/benchmark/recompute_overlap_o85.py --seal --root dmgmd-overlap-o85-20261027
bash dmgmd-overlap-o85-20261027/recompute.sh
```

实现与正确性为 PASS；性能结论为**拆分成本下降、同后端三组净收益、默认路径净收益
限于本轮 water/P8/HostStaged**。该候选与 O6.5 的 compact 方向交 O9 筛选；确认数据须
使用未参与探索的新配对块，保留原 full/完整 split/producer 的独立对照。生产默认不变，
O9/O10/O11、commit 和 push 均未执行。

---

<a id="o86-results-20261028-report"></a>

<a id="o86-results-20261028-o86producer-紧凑索引与几何内缩2026-10-28"></a>

## O8.6：producer 紧凑索引与几何内缩（2026-10-28）

类别：独立执行候选与整步对照。状态：**COMPLETE**。B 保留原 E/H，C 从实际 Verlet 参考 q
做几何内缩；两者只紧凑执行 filter/descriptor，全部 force consumer 继续完整执行。
数值门槛、正式配对、独立 trace/CPU 和离线复算齐全。**未自动采用**，生产默认仍为
HostStaged/full、overlap=off、schedule=split；本报告数据属于探索，O9 必须独立确认。

<a id="o86-results-20261028-身份执行范围与依赖"></a>

### 身份、执行范围与依赖

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

<a id="o86-results-20261028-正确性与实际覆盖"></a>

### 正确性与实际覆盖

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

<a id="o86-results-20261028-正式整步配对"></a>

### 正式整步配对

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

<a id="o86-results-20261028-实际-gpu-执行分区与维护"></a>

### 实际 GPU 执行、分区与维护

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

<a id="o86-results-20261028-cpu内存与边界"></a>

### CPU、内存与边界

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
复算当时仅对双方退出码为 0 的这一已证明元数据差异做适配；原始文件和首次严格比较失败日志已随实验目录清理；
全部计数/窗口/状态/身份及其他字段仍必须精确一致，见 `raw-recheck-amendment.json` 和
`raw-recheck.json`。未修改冻结采集器、原始样本、正式统计或数值容差。

设备级 memory.used 包括其他驻留进程、启动/warmup，不等于本进程分配或正式段驻留。
实际 GPUVector allocation/capacity、workspace/rebuild/migration 与遥测见逐 trial 原始 summary
和 `report-metrics.json`；保持未锁频、非独占、物理内存驻留 UNKNOWN 的边界。

结论限本机、两份冻结输入、P2/P8、双后端、固定 K=4 绑定和 wait；未外推 poll、water/P4、
carbon_1m、跨节点、其他设备或长期 release。本轮没有同资源 GPUMD 性能对照，不能据此
主张跨引擎优势或将消除拆分成本等同于新物理算法收益。

<a id="o86-results-20261028-命令封存与-o9-交接"></a>

### 命令、封存与 O9 交接

精确构建与 gate 命令当时记录在 `build-command.json`、`gate-command.json` 和构建日志中，正式采集命令与退出码记录在
`performance-command.json`；每 trial 的 launch/audit/input/binary hashes 和 raw logs 已随目录清理。
流程为 freeze → 环境/golden/GPU/MPI/schedule/nightly → P8 环境与实际覆盖 → 科学对照
→ pilot → main → 必要追加 → 独立 trace → CPU → 离线分析。历史工具说明见
[benchmark README](../../../tests/benchmark/README.md#o86-producer-紧凑索引与几何内缩)；原实验数据和复算入口已清理。

```bash
source ../env/md-mpi.sh
python3 tests/mpi/run_mpi_domain.py --candidate ./dmgmd-overlap-o86-20261028/dmg-md-candidate --devices 0,1,2,3 --ranks 2,4 --backends HostStaged,CudaAware --partition-modes off,serial,verify,verify-wait,verify-poll --schedule producer-compact --work-root dmgmd-overlap-o86-20261028/mpi-compact
# 几何路线同样单独运行，schedule=producer-geometry，使用独立目录。
python3 tests/benchmark/recompute_overlap_o86.py --seal --root dmgmd-overlap-o86-20261028
bash dmgmd-overlap-o86-20261028/recompute.sh
```

`initial-source/`、起始 dirty patch、reference 清单、`final.patch`、`final-source.json`、
`analysis-source/`、`raw-inventory.json` 与 `recompute.sh` 当时归档了 O8.6 身份、源码、环境、
墙钟、trace、CPU、遥测和报告聚合；上述原始记录已随实验目录清理，不能再次复算。三个只读 reference 的最终状态、
全部原有 ignored 文件哈希与起始一致：GPUMD 原有 208 个，两个 LAMMPS 均为 0。

`handoff.json` 当时交付 A/B/C 的独立身份、正负区间与未验证范围。O9 必须用未参与选择的新
配对块筛选并确认；本次只完成 O8.6，没有执行 O9/O10/O11、生产采用、commit 或 push。
