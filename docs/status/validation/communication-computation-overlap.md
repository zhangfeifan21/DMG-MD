# 通信计算重叠验收

## 目标与阶段索引

本文件按同一验收目标整合日期阶段报告。日期按原记录的执行/复核口径列示；若实施和执行跨日，单独注明。各阶段原始身份、命令、结果、失败尝试、边界与产物证据均保留在对应章节。
| 阶段 / 目标 | 执行日期或阶段 | 结论与边界 | 章节 |
|---|---|---|---|
| O2 / 普通步串行分区 | 2026-10-15 | PASS；无 overlap 收益结论 | [报告](#overlap-partition-20261015) |
| O3 / halo 生命周期 | 2026-10-15 | PASS；begin→立即 finish | [报告](#halo-lifecycle-20261015) |
| O4 / 普通步通信与 NEP 重叠 | 2026-10-16 | PASS；默认关闭，范围不含 O5 | [报告](#overlap-o4-20261016) |



<a id="overlap-partition-20261015"></a>

<a id="overlap-partition-20261015--o2-普通步串行分区验收2026-10-15"></a>

## 2026-10-15：O2 普通步串行分区


类别：实现/实测备忘。结果：**PASS（O2 完成）**。本记录只覆盖 O2：默认关闭、halo 后串行分区。
O3 后续已完成，见[当前状态](../current.md)；本报告无 overlap 收益。

<a id="overlap-partition-20261015--方向代码与只读边界"></a>
### 方向、代码与只读边界

维护者于 2026-10-15 确认 O2 方向：仅普通步 mask 串行分区，不改变数学、布局、stride、
半径或 owned 权限。现行合同见[分区标准](../../standards/domain-partition.md)。

仓库根目录实际是工作区内的 `newmd/` 子目录。起始 HEAD
`8a709385eb4730c4977181525001d7f6f0c48443`，主仓库工作树干净；本次为未提交 dirty 修改，
没有提交、推送或清理已有文件。交付二进制 `build/dmg-md` SHA-256：
`43629bed12fb9b5e9bea93ea677d9c2512e28b9f17a5d5f3cd1195df461151b6`。
完整源码身份见 [source-state.json](../../../dmgmd-overlap-o2-20261015/source-state.json)，
tracked patch 与新文件内容哈希、原始数据均保存在
[原始产物目录](../../../dmgmd-overlap-o2-20261015)。仅 HEAD 不能标识本实现。

三个严格只读 reference HEAD：GPUMD `9d23496e41319b9e2af5221a7df6285387401d1e`、
LAMMPS `611ca3b7f8525ba802373b04f9e3d632d515f7e3`、MatPL
`21fabf34eadc57a717f4c7fbce9bb80aad5fd991`。起始 GPUMD 有 208 个既有 ignored 文件，
另两个没有；以路径/大小/mtime 核对，不将旧构建物误记为本轮新增。前后 Git 与 ignored 路径/大小/mtime 完全一致，见
[末次快照](../../../dmgmd-overlap-o2-20261015/reference-end-state.json)。起始快照是
[start-state.json](../../../dmgmd-overlap-o2-20261015/start-state.json)。GPUMD 仅用于
golden/reference；long_nve 的参考运行目录位于临时目录，没有原位构建或源码链接。

<a id="overlap-partition-20261015--实现范围"></a>
### 实现范围

DMGMD_DOMAIN_PARTITION=off|serial|verify，默认 off。NEP 用完整旧 Verlet 图分配辅助 producer
与最终中心，clear 一次、call/副作用一次；verify 以同快照 full + CPU 图 oracle/poison 核对。
合同、缓存键与写入规则只在[分区标准](../../standards/domain-partition.md)维护。
本次通信位置、字节与 collective 数未变，benchmark/long_nve 显式传递并记录实验模式。

<a id="overlap-partition-20261015--环境与验收结果"></a>
### 环境与验收结果

所有 shell 从本仓库 source `../env/md-mpi.sh`。外层包装目录第一次 source 找不到脚本，
定位到实际仓库后才开展构建；没有回退 system MPI。沙箱预检的 `cuda_copy` 不可见，
按 AGENTS 到宿主加载同一脚本后四 GPU 门槛通过。Release，CUDA architectures
`75;80;86;89;90`，`-O3 -DNDEBUG`，未启用 STRONG_DEBUG 或新 stream。
| 验收 | 当前结果与原始证据 |
|---|---|
| 环境门槛 | PASS：Open MPI 5.0.10、UCX 1.22.0、cuda_copy/cuda_ipc、四 rank CudaAware probe；[日志](../../../dmgmd-overlap-o2-20261015/preflight-delivery.log) |
| 单 rank golden | PASS，4 cases，原 manifest 容差；[日志](../../../dmgmd-overlap-o2-20261015/golden-delivery.log) |
| CTest | PASS，10/10，CUDA 实际运行，非 skip；[日志](../../../dmgmd-overlap-o2-20261015/ctest-delivery.log) |
| 主 domain 矩阵 | PASS，14 cases × P2/4 × 两后端 × off/serial/verify = 168 组；[日志](../../../dmgmd-overlap-o2-20261015/domain-delivery.log)、[输入/环境/输出](../../../dmgmd-overlap-o2-20261015/domain-delivery) |
| 普通 water/统一 ZBL 补充 | PASS，2 cases × P2/4 × 两后端 × 三模式 = 24 组；[日志](../../../dmgmd-overlap-o2-20261015/domain-variants.log)、[原始产物](../../../dmgmd-overlap-o2-20261015/domain-variants) |
| compat long_nve smoke | PASS，7/7 配置，P1/HostStaged，按各 case manifest 执行适用 smoke sections（含 short/long/replay/restart/NVT），70 个实际执行 checkpoint；[日志](../../../dmgmd-overlap-o2-20261015/long-nve-delivery.log)、[报告](../../../dmgmd-overlap-o2-20261015/long-nve-report.json)、[原始工作目录副本](../../../dmgmd-overlap-o2-20261015/long-nve-work) |
| 关闭详细 profiler 的分区成本 | PASS（测量通路），12/12 配对 trial status=ok/cpu_status=ok；两个后端串行分区均变慢，见下表及 [analysis.json](../../../dmgmd-overlap-o2-20261015/analysis.json) |

主轮使用构建身份中记录的 14-case runner，普通 water/统一 ZBL 随后的增量补测
只新增 fixture/筛选参数，二进制始终是上述同一 SHA。两轮合计 **192 组**；最终 runner
默认包含全部 16 cases，直接重跑不指定 `--cases` 会执行完整 192 组。两版 runner 身份
分别由 [构建哈希](../../../dmgmd-overlap-o2-20261015/delivery-build-sha256.json)与
[最终源码身份](../../../dmgmd-overlap-o2-20261015/source-state.json)锁定。

实际 verify 记录有 4,252 次 split rank-force call，另有 setup 208、rebuild 192、
empty-interior 6,240、empty-local 2,020 次 full 记录；计数是两轮 verify 的 rank/call，
不是不同物理 case 数。[analysis.json](../../../dmgmd-overlap-o2-20261015/analysis.json)
保存辅助 E\I、skin、rebuild/migration 及空 rank 的逐次证据。

六个原有数值 kernel 的函数体在排除新增 selection guard 与空白后与 HEAD 完全相同：
[算术审计](../../../dmgmd-overlap-o2-20261015/kernel-arithmetic-audit.json)。原数学没有复制第二份。

CPU 图 oracle 穷举 5 节点全部无向图及 6 种 owned prefix，共 6,144 组合；GPU 分类逐 bit
对照。CUDA 单测还实际拒绝非法 row count、过期 halo token、未重建 box、缺失 angular
反边，并验证同 epoch 新 V generation、同 shape 新 epoch、storage/box key 失效和空域。

主矩阵保持原 P1 manifest 差分，同时对同 rank/backend 的三模式科学输出逐字节比较；
verify 比较有效 R/A 顺序、Fp/sums/partial、逐原子 energy/force/全部 9 virial。已实际触发
非空 I 与辅助 E\I、空 interior/owned/local、P2 两面同 peer、周期链、skin 带 ghost 进入
cutoff、微小跨 slab 不迁移、多 run、无迁移 rebuild、先 split→迁移→重新分类、restart、
NVT/修速/输出、force call 0/1000 和精确通信记账；没有用全 fallback 代替 split 通过。

<a id="overlap-partition-20261015--成本与解释边界"></a>
### 成本与解释边界

逻辑新增设备空间为 3 个 L 长 int mask + 3 int scratch，即 `12L+12` bytes，capacity
可以保留高水位。cache miss 有 3 个分类 kernel、mask/scratch 初始化及两次小 D2H（共
24 bytes；属于分类成本，不是 MPI/staging 通信字节）。每次 split 比 full 多 5 个常规
launch，启用 ZBL 时多 6 个；中心生产集合总计仍为 D/O，各恰一次。

`classify_host_seconds` 是含 cache miss GPU/D2H 完成等待的 host steady_clock；cache hit
仅 key 检查。旧 split `nep_device` 是 pipeline 包围区间，可能含周期 neighbor host/MPI
空隙；verify 还执行双计算与下载，不能用这些数值冒充纯 kernel 时间、overlap 或性能收益。
成本比较针对同二进制 off/serial，包含共同的选择 guard，不是与 O1 旧二进制的完整回归成本。
成本实验计划、逐 trial 命令和数据由
[measure_cost.py](../../../dmgmd-overlap-o2-20261015/measure_cost.py)与现有 benchmark/C1 承载。

成本实际资源为 n4、CUDA 12.9、driver 580.173.02、RTX 4090 GPU0/1（四 GPU
正确性矩阵用 GPU0–3）。CPU 为 AMD EPYC 7K62；每 trial 的 C1 实际核验 rank0
逻辑 CPU 0–3/96–99、rank1 4–7/100–103，各 4 物理核、CPU affinity 均为 NUMA0，与 O1 映射相同；没有由 affinity 推断内存页驻留。
GPU UUID、编译器/CPU/MPI/UCX 详情、遥测、输入哈希与实际 launch_environment 均在
每个 `paired-cost-*/metadata.json` 和 trial 的 result/CPU 原始文件中。

carbon_200k 实际 N=196608，P=2，20 步 warmup + 100 步测量，无详细 profiler。
三组配对顺序按 seed=20261015 为 serial/off、serial/off、off/serial；每次各跑两个后端。
每后端/模式各 3 个独立 trial，下表是测量段秒数/100 的中位数，含段首兼容 force，
不把进程启动时间当逐步成本：
| 后端 | off ms/step | serial ms/step | 额外成本 |
|---|---:|---:|---:|
| HostStaged | 15.6508 | 20.3905 | +30.28% |
| CudaAware | 16.3820 | 21.1373 | +29.03% |

每个 serial trial 累计 240 个真实 split rank-call、4 次分类（两个 run 段各 rank 一次），
不是全 fallback。12 个 trial 的 thermo.out SHA-256 全同：
`3a7070599ccf516a6ff37e9b1d407e5decbca366d9cf95446106b5d99095406f`。绑定均 verified；C1 关闭 profiler 时没有 phase marker，
CPU 原始观测包含启动/两段 run，rank 采样窗不完全同步，故不发布逐步 CPU 或同步 job
core-equivalent。这里没有锁频/排他调度或正式置信区间，三组只记录 O2 的本机额外成本，
不作为 O5 收益结论。**分区显著增加本次串行墙钟，保持默认关闭。**

成本正式组的预定顺序与命令见[paired-cost-plan.json](../../../dmgmd-overlap-o2-20261015/paired-cost-plan.json)
和[运行脚本](../../../dmgmd-overlap-o2-20261015/measure_cost-paired.py)；较早 cost-* 探索样本
仍保留，不并入本表。分类 host 时间可能等待同 stream 前序 unpack/clear，不外推为纯分类 kernel 成本。

<a id="overlap-partition-20261015--精确命令与交接"></a>
### 精确命令与交接

下列 GPU 命令均在宿主执行；沙箱中先做 CPU/静态检查。每个新 shell 都重新 source：

```bash
cd /data/home/zhangyifei/fdata/zhangyifei/newmd/newmd
source ../env/md-mpi.sh
cmake --build build -j 8
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
ctest --test-dir build --output-on-failure
python3 -u tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2,4 \
  --backends HostStaged,CudaAware --devices 0,1,2,3 --partition-modes off,serial,verify \
  --work-root dmgmd-overlap-o2-20261015/domain-delivery
python3 -u tests/long_nve/run_long_nve.py --candidate ./build/dmg-md --profile smoke \
  --devices 0,1,2,3 --partition-mode off \
  --report dmgmd-overlap-o2-20261015/long-nve-report.json --ui plain --keep-work
python3 dmgmd-overlap-o2-20261015/measure_cost.py
python3 -u tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2,4 \
  --backends HostStaged,CudaAware --devices 0,1,2,3 --partition-modes off,serial,verify \
  --cases water,zbl --work-root dmgmd-overlap-o2-20261015/domain-variants
python3 dmgmd-overlap-o2-20261015/measure_cost-paired.py
```

`--work-root` 必须是新目录，重跑时换输出路径；原始证据不能覆盖。新增参数的 help、
benchmark dry-run/CPU parser/checkpoint 测试已执行，产物在同目录。baseline/long_nve/
benchmark manifest 和 golden 未修改；smoke 不代表 nightly 10000 步或 release 100000 步通过。

本报告是当时 O2 的交付快照；O3 后续验收见[报告](communication-computation-overlap.md#halo-lifecycle-20261015)，
O4/O5 尚未实施，见[剩余计划](../../plans/communication-computation-overlap.md)。


<a id="halo-lifecycle-20261015"></a>

<a id="halo-lifecycle-20261015--o3-halo-生命周期验收2026-10-15"></a>

## 2026-10-15：O3 halo 生命周期


类别：实现/实测备忘。状态：**PASS（O3 完成）**。
本轮仅 O3：原位置 begin→立即 finish，不执行 O4、不新增 progress thread。

<a id="halo-lifecycle-20261015--身份授权与边界"></a>
### 身份、授权与边界

维护者本轮明确要求执行 O3，检查 O2 正确性后实施。仓库实际根目录是工作区内
`newmd/` 子目录；起始 HEAD `bcedb72bdc57c2fe83202e3b423292d0bc3562fe`，工作树干净。
执行时改动尚未提交；当前已收入 9c608a1，测量身份仍以下列快照/哈希为准。逐文件核对 O2 source-state.json 的全部源码哈希与当前起始源码一致，
原二进制 SHA-256 `43629bed12fb9b5e9bea93ea677d9c2512e28b9f17a5d5f3cd1195df461151b6`
也一致，复制为 [dmg-md-o2](../../../dmgmd-overlap-o3-20261015/dmg-md-o2) 保留。
O2 已通过完整 192 组数值矩阵、golden、CUDA 单测与 long_nve smoke，见
[O2 报告](communication-computation-overlap.md#overlap-partition-20261015)；原始源码/产物已真实存在，未以设计完成代替通过。

O3 交付二进制 SHA-256：`d1abb2deb8e54d1552a54c1030a33dc89a12795fe946b890671d93d77989e646`。
测试二进制/源码哈希见 [delivery-build-sha256.json](../../../dmgmd-overlap-o3-20261015/delivery-build-sha256.json)。
完整 tracked diff、新文件与身份见 [source-state.json](../../../dmgmd-overlap-o3-20261015/source-state.json)
与同目录 source-final.patch/source-files。仅 HEAD 无法标识本轮 dirty 实现。

起始与结束检查主仓库、GPUMD、LAMMPS、MatPL 四个 checkout。reference HEAD 分别为
`9d23496e41319b9e2af5221a7df6285387401d1e`、
`611ca3b7f8525ba802373b04f9e3d632d515f7e3`、
`21fabf34eadc57a717f4c7fbce9bb80aad5fd991`；起始 Git 无修改，GPUMD 有既有 ignored
构建物，其他两个无 ignored 产物。快照包含路径、lstat 大小/mtime（不追随旧的失效符号链接），
见 [start-state.json](../../../dmgmd-overlap-o3-20261015/start-state.json)。所有运行/产物在主仓库
或临时目录，reference 未原位构建、修改或清理。末次路径/大小/mtime 与起始完全相同：
[reference-end-state.json](../../../dmgmd-overlap-o3-20261015/reference-end-state.json)。

<a id="halo-lifecycle-20261015--实现范围"></a>
### 实现范围

生效状态、同步依赖、lease 和错误合同统一见[halo 生命周期](../../standards/halo-lifecycle.md)。
本次在原位置 begin 后立即 finish，等待四请求和物理 unpack；HostStaged 用 pinned async
copy/event，CudaAware 保留输入/输出同步。单槽位，无新线程/stream/双份 payload。
修复部分 post/Waitall 自检与 host face 错误时的立即 abort，正常完成但数值不符仍回退。
没有提前 NEP，也没有删 CudaAware 同步。

<a id="halo-lifecycle-20261015--环境与验收"></a>
### 环境与验收

所有有效 shell 从实际仓库 source `../env/md-mpi.sh`，没有回退 system MPI/UCX。
沙箱 [环境预检](../../../dmgmd-overlap-o3-20261015/preflight-sandbox.log) 的 cuda_copy 不可见，
按 AGENTS.md 到宿主执行同一脚本，O2 基线和 O3 最终构建四 GPU 门槛均通过。
宿主 n4，RTX 4090 GPU0–3，driver 580.173.02、CUDA 12.9；完整 CPU/GPU UUID/
MPI/UCX/compiler 信息见 [environment.json](../../../dmgmd-overlap-o3-20261015/environment.json)。
| 验收 | 当前结果/原始证据 |
| --- | --- |
| O3 最终构建 | PASS，[build-final.log](../../../dmgmd-overlap-o3-20261015/build-final.log) |
| 四 GPU 环境门槛 | PASS，Open MPI 5.0.10、UCX 1.22.0、cuda_copy/cuda_ipc、四 rank 实际 device-pointer probe；[preflight-final.log](../../../dmgmd-overlap-o3-20261015/preflight-final.log) |
| 单 rank golden | PASS，4/4，原 manifest 容差；[golden-final.log](../../../dmgmd-overlap-o3-20261015/golden-final.log) |
| CTest | PASS，10/10，CUDA 实际运行未 skip；[ctest-final.log](../../../dmgmd-overlap-o3-20261015/ctest-final.log) |
| 生命周期/单边错误 | PASS，68/68 作业；[lifecycle-final.log](../../../dmgmd-overlap-o3-20261015/lifecycle-final.log)、[results.json](../../../dmgmd-overlap-o3-20261015/lifecycle-final/results.json) |
| O2 domain 数值矩阵 | PASS，16 cases × P2/4 × 两后端 × off/serial/verify = 192 组；[domain-final.log](../../../dmgmd-overlap-o3-20261015/domain-final.log)、[原始产物](../../../dmgmd-overlap-o3-20261015/domain-final) |
| 锁定 O2 科学输出逐字节核对 | PASS，808 次比较，覆盖 707 个不同 O3 科学文件；[comparison.json](../../../dmgmd-overlap-o3-20261015/o2-output-comparison.json) |
| long_nve smoke | PASS，7/7 配置、70 executed checkpoints，P1/HostStaged；[日志](../../../dmgmd-overlap-o3-20261015/long-nve-final.log)、[报告](../../../dmgmd-overlap-o3-20261015/long-nve-report.json)、[原始工作目录副本](../../../dmgmd-overlap-o3-20261015/long-nve-work) |
| O2/O3 固定资源串行成本 | PASS（测量通路），24/24 trial status=ok/cpu_status=ok；[cost-analysis.json](../../../dmgmd-overlap-o3-20261015/cost-analysis.json) |

生命周期实际覆盖两后端 P=1/2/4、count 1/0/17/2/1、方向独立数据、普通立即 finish、
progress-only、重复 finish、完成后 free/下一代增长，以及 P=2 同 peer 的正确方向。
受控 wrapper 分别暂缓发送/接收 Testall 的完成观察，核对另一侧不会解锁槽位；这是
完成状态分离证据，不是网络完成顺序或真正 overlap 实测。代表性
[receive-first](../../../dmgmd-overlap-o3-20261015/lifecycle-final/CudaAware-p2-receive-first/stdout.txt)
包含 `sends=0 receives=1 gpu=1 unpack=1 reusable=0`，随后五代 `HALO_REUSE` 全部完成。

错误矩阵只在 rank0 注入，其他 rank 正常执行；每例必须整作业非零退出、包含指定原因，
且未超时。覆盖非法 count、第二 send post（已有一条请求）、Waitall/Testall、unpack
事件、重复 begin、在途 host face、非法 token、活动槽位析构、自检第二 post，以及
HostStaged D2H/H2D 失败。故障 wrapper 只链接进测试程序，生产没有测试开关。

<a id="halo-lifecycle-20261015--字节调用与额外内存"></a>
### 字节、调用与额外内存

两后端普通位置 halo 的 MPI 字节均与 O2 相同：每 face 每原子 24 B，2 Isend + 2 Irecv
与 tag 1101/1102 不变，无新 collective。HostStaged 的 D2H=本地 send bytes、H2D=本地
recv bytes；CudaAware 均为零 staging。所有 byte/call 只在完成时加一次，重复 finish
不加。既有 p2p/collective 分栏口径不变；MPI 计数是应用缓冲字节，不是物理链路流量。

payload 额外内存为 **0 B**：原有 device send/recv 和 HostStaged pinned send/recv
高水位复用。新增四个 timing-disabled CUDA event、持久 request/状态、thread id 和
buffer lease 标志。x86-64 编译 sizeof 实测 Impl 从 **472 B→736 B（+264 B/rank）**，
DeviceBuffer/PinnedBuffer 各 16→24 B；CUDA event/driver 内部存储大小未知且不在该
sizeof 中。测量源码仅在产物目录放宽 C++ private 可见性来读取 sizeof，不构造 MPI/CUDA
对象、不改生产源；[测量结果](../../../dmgmd-overlap-o3-20261015/memory-audit/result.txt)、
[精确测量命令](../../../dmgmd-overlap-o3-20261015/memory-audit/run.sh)。

begin 的 HostStaged event wait 替换原 blocking D2H；H2D 与 unpack 用原 stream 排序，
finish 新增物理 unpack event host wait。CudaAware 原 device sync 均保留。四个生命周期
事件在关闭 profiler 时也存在，常规每交换 HostStaged 记录四个、CudaAware 三个；后者
send-source 由原 input device sync 证明，第四个 handle 保留。计时字段 H2D 现在表示
enqueue，transfer-wait 不包含末端 unpack host wait；总 halo host 与 step wall 含它。

<a id="halo-lifecycle-20261015--串行成本实测"></a>
### 串行成本实测

复用 benchmark/C1，宿主 n4 GPU0/1；carbon_200k 实际 N=196608，P=2，每 rank
固定 4 物理核，CPU affinity 为 NUMA0，GPU UUID 固定。24 个 trial 均由 C1 核验
rank0 为逻辑 CPU 0–3/96–99、rank1 为 4–7/100–103；不由 CPU affinity 推断内存页
驻留。每个 partition 模式各 3 组 O2/O3 配对，seed=20261015 决定组内先后顺序；
每次跑两后端，20 步 warmup + 100 步正式段，详细 profiler 关闭。
| 分区 | 后端 | O2 ms/step 中位数 | O3 ms/step 中位数 | 相对变化 |
| --- | --- | ---: | ---: | ---: |
| off | HostStaged | 15.7202 | 15.7426 | +0.14% |
| off | CudaAware | 16.4089 | 16.4358 | +0.16% |
| serial | HostStaged | 20.4775 | 20.4015 | -0.37% |
| serial | CudaAware | 21.2081 | 21.2842 | +0.36% |

时长为正式 run 的 seconds_max/100，含双方共有的段首 force；不包括进程启动、首次
staging/event 分配成本。每格仅三个样本，逐 trial 时间/离散范围保存在 cost-analysis；
变化与样本波动同量级，没有锁频/排他调度/正式置信区间，不作显著性或收益结论。
这里的比较仅记录 O3 生命周期新增等待/事件与 staging 调整的本机串行成本，不是 O5。

所有 trial 的 thermo.out SHA-256 均为
`3a7070599ccf516a6ff37e9b1d407e5decbca366d9cf95446106b5d99095406f`；
serial 均有真实 split，默认 off 与 backend 由实际日志核验。每个 trial 保存二进制/输入
哈希、launch_environment、C1 原始样本/绑定和 telemetry。两个二进制分别为上方锁定
O2 与交付 O3；不能以性能 trial 当时的 dirty patch 误认 O2 二进制来源。

<a id="halo-lifecycle-20261015--精确命令"></a>
### 精确命令

所有 GPU 命令在宿主执行，按以下顺序通过前门槛才继续；每个新 shell 重新 source。
输出路径重跑需换新目录，不能覆盖现存证据。

```bash
cd /data/home/zhangyifei/fdata/zhangyifei/newmd/newmd
source ../env/md-mpi.sh
cmake --build build -j 8
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
ctest --test-dir build --output-on-failure
python3 -u tests/mpi/run_halo_lifecycle.py --candidate ./build/dmg-md --devices 0,1,2,3 \
  --work-root dmgmd-overlap-o3-20261015/lifecycle-final
python3 -u tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2,4 \
  --backends HostStaged,CudaAware --devices 0,1,2,3 --partition-modes off,serial,verify \
  --work-root dmgmd-overlap-o3-20261015/domain-final
python3 -u tests/long_nve/run_long_nve.py --candidate ./build/dmg-md --profile smoke \
  --devices 0,1,2,3 --partition-mode off \
  --report dmgmd-overlap-o3-20261015/long-nve-report.json --ui plain --keep-work
python3 dmgmd-overlap-o3-20261015/analyze.py
python3 dmgmd-overlap-o3-20261015/measure_cost.py
python3 dmgmd-overlap-o3-20261015/analyze.py
```

固定资源成本编排脚本先写预定 [cost-plan.json](../../../dmgmd-overlap-o3-20261015/cost-plan.json)，
复用现有 benchmark/C1，详细命令及实际环境由每 trial metadata/launch_environment 保存。

<a id="halo-lifecycle-20261015--未解决项与阶段边界"></a>
### 未解决项与阶段边界

本轮没有删除 CudaAware 全局同步，没有证明 stream-aware 或异步网络后台进展；opaque CUDA event 内部内存仍 UNKNOWN。目标栈
改变须重跑自检与门槛。progress 保留可能阻塞的 MPI/CUDA 行为，O4 需要单独设计调度、
重新证明计算依赖/竞态并取得授权。本轮不移动 compute_domain/force ticket 到在途 halo 前，
默认分区 off，仍等待 halo 后计算。无实际 overlap、跨节点或 O5 收益声明。

O3 验收已完成，无剩余 O3 正确性阻塞项；nightly/release 长程矩阵与跨节点验收未新增执行，
不把 smoke 解释为其通过。O4 的接口与依赖设计仍按[剩余计划](../../plans/communication-computation-overlap.md)推进。


<a id="overlap-o4-20261016"></a>

<a id="overlap-o4-20261016--o4-普通步通信与-nep-重叠验收2026-10-16"></a>

## 2026-10-16：O4 普通步通信与 NEP 重叠


类别：实现与实测记录。状态：**PASS（O4 完成，默认关闭）**。范围限本报告实际执行项，不含 O5。

本轮明确授权 O4 接口、实现、测试和文档；未执行 O5 正式矩阵，未提交或推送 Git。
起始 HEAD `246a765d7f4f9f19849ccb295a24f38531c4c08d`，主仓库干净。起始状态页仍写
`9c608a1`，源码确为已完成 O1–O3、生产 begin 后立即 finish 的预期基线。
三个 reference 的锁定 revision 与 ignored 路径/大小/mtime 保存在
[start-state.json](../../../dmgmd-overlap-o4-20261016/start-state.json)。

源码依赖审计与先行验证设计见
[audit-design.md](../../../dmgmd-overlap-o4-20261016/audit-design.md)。实际入口为
`run_domain_segment`→`launch_domain_force`→`NEP::compute_domain`，通信为
`begin_ghost_positions`→`MpiRuntime::begin_p2p_indexed_device_soa/progress_halo/finish_halo`。
原 kernel 的公式与 launch/行顺序不变，完整旧 Verlet 图的 S/I/E/H/B、L stride 与唯一写入
合同见[分区](../../standards/domain-partition.md)，lease/收发/物理 unpack 合同见
[halo](../../standards/halo-lifecycle.md)。

<a id="overlap-o4-20261016--实现与有效范围"></a>
### 实现与有效范围

默认 `DMGMD_DOMAIN_OVERLAP=off`。wait/poll 要求 partition=serial/verify，所有 rank
必须模式一致。普通 M2a、全局 confirmed-reuse、已证明 NEP4/5、I 非空时实际执行
prepare→begin/post→clear once→early→finish/unpack→late。prepare 的分类/必要分配在
post 前，不按陈旧 ghost 或本步 R/A 扩大 interior。empty-owned neighbor reference 也不
在 post 前读取 ghost。P1/M1、段首、rebuild/迁移、空 I/owned/local 和未证明模型同步 full，
保留原因；不同 rank 可以混合 full/overlap。启动时新增四次标量 Allreduce 核对 overlap/trace
一致性；逐步 halo 请求/字节和 force sink collective 不增加。

相同默认 stream，owned x 只读，pack 写独立 send，early 不读 ghost，unpack 只写 ghost。
late 的 stream 顺序和 matching physical halo-ready 双重约束保留 early 结果；mask/Verlet/
map/view/workspace/key 在 force 内冻结。每次逻辑 force 只增加一次 call，call 0/1000
neighbor sink 与两次 MPI_MAX 在所有 rank 的 finish 后执行一次，verify 无额外 MPI/I/O。
verify 在 finish 后保存/恢复安全 early 快照，再进行 full replay；ghost poison 是 prepare
时生成的独立 position 副本，不并发修改生产数据。

wait 直接 finish；poll 在 early enqueue 后最多 8 次 progress，看到 reusable 或收发均完成
就停止，再 finish。CPU 线程仍为初始化线程，无新增 stream/thread。CudaAware input sync
在 early 之前，output sync 保留；HostStaged 沿用 O3 D2H/H2D/event。

<a id="overlap-o4-20261016--验收方法与产物"></a>
### 验收方法与产物

通过阶段的精确命令汇编保存在 [commands-accepted.sh](../../../dmgmd-overlap-o4-20261016/commands-accepted.sh)，
实际逐作业 command/launch_environment 与 stdout/stderr 保存在各测试目录；
每个新 shell source 同一 `../env/md-mpi.sh`。受限沙箱 cuda_copy 不可见，已按 AGENTS
到宿主执行同一入口，不回退 system MPI。最终 candidate SHA-256 为 `860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6`。
最终构建的源码/测试/二进制哈希保存在
[delivery-build-sha256.json](../../../dmgmd-overlap-o4-20261016/delivery-build-sha256.json)。
数值矩阵、long_nve smoke、小规模成本与正常路径 profiler 均完成；未将 smoke 宣称为 nightly/release。
| 验收 | 当前结果与原始产物 |
|---|---|
| 最终构建 | PASS；[build-final.log](../../../dmgmd-overlap-o4-20261016/build-final.log) |
| 四 GPU 环境门槛 | PASS；[preflight-delivery.log](../../../dmgmd-overlap-o4-20261016/preflight-delivery.log) |
| 单 rank golden | PASS 4/4，原 manifest 容差；[golden-delivery.log](../../../dmgmd-overlap-o4-20261016/golden-delivery.log) |
| CTest | PASS 10/10，CUDA 实际执行无 skip；[ctest-delivery.log](../../../dmgmd-overlap-o4-20261016/ctest-delivery.log) |
| O3 生命周期/单边错误 | PASS 68/68；[results.json](../../../dmgmd-overlap-o4-20261016/lifecycle-delivery/results.json) |
| O4 受控延迟/配置/单边错误 | PASS 25/25，32 条受控 pending receive + early-complete；[results.json](../../../dmgmd-overlap-o4-20261016/schedule-accepted/results.json) |
| 完整 domain 七模式 | PASS 476/476（17 cases × P2/4 × 两后端 × 七模式）；[日志](../../../dmgmd-overlap-o4-20261016/domain-accepted.log) |
| long_nve smoke | PASS 7/7，全部适用 smoke sections；[报告](../../../dmgmd-overlap-o4-20261016/long-nve-report.json)，不代表 nightly/release |
| 固定资源/正常路径 profiler | PASS：24/24 cost trial 与 C1；24/24 profiler rank-step 有交叠；[成本与证据](../performance/communication-computation-overlap-results.md#o4-20261016-report)，未执行 O5 |

主矩阵的 off/serial/verify/overlap-wait/overlap-poll/verify-wait/verify-poll 保持原
manifest 容差，另重算 1,368 次科学文件逐字节比较，以及与锁定 O3 off 产物的 212 次
比较；结果见 [numerical-analysis.json](../../../dmgmd-overlap-o4-20261016/numerical-analysis.json)，
重算执行 `python3 dmgmd-overlap-o4-20261016/analyze.py`。
每个 wait/poll/verify-wait/verify-poll 配置各有 4,348 个真实 overlap rank-force call，
168 条非空 E\I 记录、4 条 call1000 overlap 记录；不是全 fallback。各配置另外记录
setup 220、rebuild 192、empty-interior 6,288、empty-local 2,020 个 M2a full rank-call。
计数是本矩阵的诊断记录，不是独立物理 case 数，也不包含 M1 全局 fallback 行。

全部覆盖两跳、周期链、skin ghost 进入 cutoff、P2 同 peer、空 owned/local/interior、
混合 full/overlap、cache generation/无迁移 rebuild/迁移后重分类、多 run、NEP4/5、
typewise cutoff、普通/flexible/typewise ZBL、修速/NVT/输出/restart、call 0/1000。
verify 对有效 R/A、Fp/sums/partial、owned energy/force/全部 9 virial 与 ghost 权限逐 bit
核验；原 halo 字节、四请求及 tag 门槛沿用。CPU ticket/图穷举与 CUDA 单测覆盖过期
step/key、错误 halo generation、early/late 顺序/重复、未完成 ready 和 force 期间 resize。

独立延迟 wrapper 使用未完成 generalized receive request，直到 early CUDA event 完成才
实际 post receive；它证明在途状态、受控延迟和 stream 依赖，不证明物理网络活动。P4
rank0 的 I 为空而 rank1/3 可 overlap：首版故障脚本错误假设 rank0 有 early，故障未触发，
保留 [schedule-final.log](../../../dmgmd-overlap-o4-20261016/schedule-final.log)；修正为在
rank0 的 full fallback 仍在途时注入，使混合调度的单边错误同样受检。此探索失败不计 PASS。

配置不一致测试的首个 MPMD 启动方式在候选程序执行前触发 Open MPI launcher SIGSEGV
（returncode=-11、无程序输出），保留 [recheck.json](../../../dmgmd-overlap-o4-20261016/schedule-delivery/rank-mismatch/recheck.json)。
改用单一 MPI app 的测试 rank 包装器后，生产程序的 fingerprint 拒绝实际通过。
新增 NVT fixture 的首次 P1 输入用 `correct_velocity 1`，按既有 >=10 语法被拒绝；
已改为 interval=10/run=12，保留 [原日志](../../../dmgmd-overlap-o4-20261016/domain-delivery.log)，
不改变任何数值门槛。探索失败不计入最终通过数。

<a id="overlap-o4-20261016--正常路径证据成本与限制"></a>
### 正常路径证据、成本与限制

真实生产 binary 的 HostStaged/CudaAware × wait/poll 四份 Nsight CUDA/MPI 采集均有
6/6 个 rank-step 的实际 early kernel 与保守请求在途区间交集，合计 24/24；同 rank/step
按 kernel 区间并集重算，没有用 enqueue 包围区间、跨 rank 相加或网络流量假设。
主线程 MPI API 内也观测到 GPU early 执行。时钟关联、误差限制、SQL/重算与原始
trace 在[实测](../performance/communication-computation-overlap-results.md#o4-20261016-report)维护。
HostStaged 单 rank-step 交集约 0.096–0.227 ms，CudaAware 约 1.536–2.369 ms；
这是请求状态交叠，不是 NIC 传输时间。未发布 overlap ratio。

小规模固定资源检查：carbon_200k、N=196608、seed0、P2、GPU0/1、每 rank 4 物理核/
NUMA0、20 warmup+100 steps、每后端/模式三重复、主墙钟关闭详细诊断。wait 比 serial
约快 1.68%/3.91%，仍比 full 慢 28.04%/22.61%（HostStaged/CudaAware）。poll 未显示
更佳中位数；finish thread CPU 接近 wall。真实启用和 CPU/C1 原始样本全部保存，保持
默认 off。没有锁频、排他调度或正式统计区间，不外推普遍收益。

最终源码身份、dirty patch 与所有新增源文件副本：
[source-state.json](../../../dmgmd-overlap-o4-20261016/source-state.json)、
[source-final.patch](../../../dmgmd-overlap-o4-20261016/source-final.patch)、同目录 source-files/。
最终 candidate 副本位于 candidate-dmg-md；long_nve 临时工作目录已复制到
[long-nve-work](../../../dmgmd-overlap-o4-20261016/long-nve-work)，包括 70 个实际 checkpoint。
模型/势/run 固定哈希、48 个实际 rank 绑定核验记录见
[input-binding-verification.json](../../../dmgmd-overlap-o4-20261016/input-binding-verification.json)。
三个 reference 前后 HEAD/Git/ignored 路径、lstat 大小和 mtime 完全相同，GPUMD 的 208 个
既有 ignored 文件没有新增或变更；见
[reference-end-state.json](../../../dmgmd-overlap-o4-20261016/reference-end-state.json)。
没有修改 reference、golden、manifest 容差、粒子布局/stride/精度/所有权/半径或数学 kernel，
没有提交或推送 Git。

未执行：完整 nightly/release、跨节点/更多 GPU/不同 NUMA 的 O4 正确性与性能、NIC 传输
trace、独立最大时钟误差校准、O5 正式矩阵/至少五重复/采用阈值与采样扰动研究。
M2b、中间场交换、3D、progress thread、新 stream/双缓冲/跨步流水均未实施。
可选展示图尝试在 import 处失败（环境缺少 matplotlib），图未生成、未纳入验收；原始 trace 和 JSON 重算不依赖它。

Nsight 初轮提示 event completion tracing 可能引入额外 stream 依赖；最终以
`--cuda-event-trace=false` 复采四组合，仍为 24/24，验收仅引用 profile-noevent-*。
原始带警告采集保留，未当作成本样本或最终证据。
