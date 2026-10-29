# O6.5：拆分执行的因果对照（2026-10-27；10-26 开始）

类别：独立受控执行诊断与无诊断整步对照。状态：**COMPLETE：实现与正确性 PASS，必要执行对照完整；生产采用 NONE**。
当前 O7+O8 的 carbon radial / water angular partial 拆分损失已用新 trace 和同快照 A–D
对照验证；紧凑映射在两体系均降低真实互补 mask 的目标 kernel 耗时，支持进入独立整步实验；具体微架构原因仍 INCONCLUSIVE。紧凑执行目前处于诊断阶段，生产采用与整步净收益仍待独立验证。
生产默认继续为 HostStaged/full、overlap=off。

## 身份、冻结设计与测量范围

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

## 新基线墙钟与实际 GPU 增量

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

## 真实图、mask 与工作量

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

## 同快照 A–D 对照

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

## 维护成本、假设与候选交接

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

## 正确性、复算与限制

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
