# O2 普通步串行分区验收（2026-10-15）

类别：实现/实测备忘。当前状态：**PASS（O2 完成）**。本轮仅 O2；默认关闭，halo 完成后串行
执行两部分。O3–O5 未执行，没有 overlap 或加速收益声明。

## 方向、代码与只读边界

维护者在本次会话明确回复“确认此方向，继续实施 O2”。确认范围及
`AGENTS.md` 关于数学/布局/所有权/半径/兼容承诺的适用边界记录在
[计划确认节](../plans/communication-computation-overlap.md#o2-方向确认与实施入口2026-10-15)。
实现没有改变 NEP 数学、粒子布局、L stride、半径、global ID 或 owned 权限。

仓库根目录实际是工作区内的 `newmd/` 子目录。起始 HEAD
`8a709385eb4730c4977181525001d7f6f0c48443`，主仓库工作树干净；本次为未提交 dirty 修改，
没有提交、推送或清理已有文件。交付二进制 `build/dmg-md` SHA-256：
`43629bed12fb9b5e9bea93ea677d9c2512e28b9f17a5d5f3cd1195df461151b6`。
完整源码身份见 [source-state.json](../../dmgmd-overlap-o2-20261015/source-state.json)，
tracked patch 与新文件内容哈希、原始数据均保存在
[原始产物目录](../../dmgmd-overlap-o2-20261015/)。仅 HEAD 不能标识本实现。

三个严格只读 reference HEAD：GPUMD `9d23496e41319b9e2af5221a7df6285387401d1e`、
LAMMPS `611ca3b7f8525ba802373b04f9e3d632d515f7e3`、MatPL
`21fabf34eadc57a717f4c7fbce9bb80aad5fd991`。起始 GPUMD 有 208 个既有 ignored 文件，
另两个没有；以路径/大小/mtime 核对，不将旧构建物误记为本轮新增。前后 Git 与 ignored 路径/大小/mtime 完全一致，见
[末次快照](../../dmgmd-overlap-o2-20261015/reference-end-state.json)。起始快照是
[start-state.json](../../dmgmd-overlap-o2-20261015/start-state.json)。GPUMD 仅用于
golden/reference；long_nve 的参考运行目录位于临时目录，没有原位构建或源码链接。

## 已生效的接口与证明

规范迁入 [串行分区标准](../standards/domain-partition.md)，并从 data-layout、
replicated-mpi、kernel-inventory、golden-test-standard 及索引链接。

- `DMGMD_DOMAIN_PARTITION=off|serial|verify`，默认 off；`DomainForceContext` 要求普通步、
  confirmed_reuse 和本步 halo_ready token。`DomainForceTicket` 约束 early/late 顺序和恰一次。
- 基于完整旧 V（含 skin）的 S/I/E/H；I/B 分配最终 owned force，E/H 分配辅助
  filter/descriptor/partial。mask guard 在读取前执行，仍用原 slot/grid/block/行序。
- 全 L 输出清零一次；E\I 的 descriptor PE 不在 late 重复累加，最终 PE/force/9 virial
  保持每中心顺序。有效 Fp/sums/partial 必须本步生成；未使用的 row 尾部不比较、不读取。
- layout/mapping、V generation、workspace generation、O/D/L、两种 box 表示/PBC 约束
  缓存；参数/type/gid 在对应实例/epoch 不可变。沿用全局连续位移 ≤ skin/2 的原合同。
  setup/rebuild/迁移失效旧 mask；同 shape 不代表同身份；未证明模型、P1/M1、空域回原 full。
- 原未分区 M2a 为同快照 oracle；verify 另检查 CPU/GPU 集合、闭包及消费者前反边/Fp
  可用性，poison 中间 scratch 和 early 私有副本中的 ghost 坐标。oracle replay 不增加
  logical force call、neighbor 记录、MPI 或 I/O。

runtime 的 MPI halo 调用与立即 Waitall 未变；普通步通信字节合同未变。实验模式在启动时
有一致性 fingerprint；生产普通步没有新增 MPI collective。显式详细日志/verify 输出逐次
`DMGMD_PARTITION`；所有启用模式在每段输出累计 SUMMARY，以证明实际 split/classification。
benchmark 与 long_nve 新参数显式传递模式并记录环境/checkpoint；benchmark 检查实际模式。

## 环境与验收结果

所有 shell 从本仓库 source `../env/md-mpi.sh`。外层包装目录第一次 source 找不到脚本，
定位到实际仓库后才开展构建；没有回退 system MPI。沙箱预检的 `cuda_copy` 不可见，
按 AGENTS 到宿主加载同一脚本后四 GPU 门槛通过。Release，CUDA architectures
`75;80;86;89;90`，`-O3 -DNDEBUG`，未启用 STRONG_DEBUG 或新 stream。

| 验收 | 当前结果与原始证据 |
|---|---|
| 环境门槛 | PASS：Open MPI 5.0.10、UCX 1.22.0、cuda_copy/cuda_ipc、四 rank CudaAware probe；[日志](../../dmgmd-overlap-o2-20261015/preflight-delivery.log) |
| 单 rank golden | PASS，4 cases，原 manifest 容差；[日志](../../dmgmd-overlap-o2-20261015/golden-delivery.log) |
| CTest | PASS，10/10，CUDA 实际运行，非 skip；[日志](../../dmgmd-overlap-o2-20261015/ctest-delivery.log) |
| 主 domain 矩阵 | PASS，14 cases × P2/4 × 两后端 × off/serial/verify = 168 组；[日志](../../dmgmd-overlap-o2-20261015/domain-delivery.log)、[输入/环境/输出](../../dmgmd-overlap-o2-20261015/domain-delivery/) |
| 普通 water/统一 ZBL 补充 | PASS，2 cases × P2/4 × 两后端 × 三模式 = 24 组；[日志](../../dmgmd-overlap-o2-20261015/domain-variants.log)、[原始产物](../../dmgmd-overlap-o2-20261015/domain-variants/) |
| compat long_nve smoke | PASS，7/7 配置，P1/HostStaged，按各 case manifest 执行适用 smoke sections（含 short/long/replay/restart/NVT），70 个实际执行 checkpoint；[日志](../../dmgmd-overlap-o2-20261015/long-nve-delivery.log)、[报告](../../dmgmd-overlap-o2-20261015/long-nve-report.json)、[原始工作目录副本](../../dmgmd-overlap-o2-20261015/long-nve-work/) |
| 关闭详细 profiler 的分区成本 | PASS（测量通路），12/12 配对 trial status=ok/cpu_status=ok；两个后端串行分区均变慢，见下表及 [analysis.json](../../dmgmd-overlap-o2-20261015/analysis.json) |

主轮使用构建身份中记录的 14-case runner，普通 water/统一 ZBL 随后的增量补测
只新增 fixture/筛选参数，二进制始终是上述同一 SHA。两轮合计 **192 组**；最终 runner
默认包含全部 16 cases，直接重跑不指定 `--cases` 会执行完整 192 组。两版 runner 身份
分别由 [构建哈希](../../dmgmd-overlap-o2-20261015/delivery-build-sha256.json)与
[最终源码身份](../../dmgmd-overlap-o2-20261015/source-state.json)锁定。

实际 verify 记录有 4,252 次 split rank-force call，另有 setup 208、rebuild 192、
empty-interior 6,240、empty-local 2,020 次 full 记录；计数是两轮 verify 的 rank/call，
不是不同物理 case 数。[analysis.json](../../dmgmd-overlap-o2-20261015/analysis.json)
保存辅助 E\I、skin、rebuild/migration 及空 rank 的逐次证据。

六个原有数值 kernel 的函数体在排除新增 selection guard 与空白后与 HEAD 完全相同：
[算术审计](../../dmgmd-overlap-o2-20261015/kernel-arithmetic-audit.json)。原数学没有复制第二份。

CPU 图 oracle 穷举 5 节点全部无向图及 6 种 owned prefix，共 6,144 组合；GPU 分类逐 bit
对照。CUDA 单测还实际拒绝非法 row count、过期 halo token、未重建 box、缺失 angular
反边，并验证同 epoch 新 V generation、同 shape 新 epoch、storage/box key 失效和空域。

主矩阵保持原 P1 manifest 差分，同时对同 rank/backend 的三模式科学输出逐字节比较；
verify 比较有效 R/A 顺序、Fp/sums/partial、逐原子 energy/force/全部 9 virial。已实际触发
非空 I 与辅助 E\I、空 interior/owned/local、P2 两面同 peer、周期链、skin 带 ghost 进入
cutoff、微小跨 slab 不迁移、多 run、无迁移 rebuild、先 split→迁移→重新分类、restart、
NVT/修速/输出、force call 0/1000 和精确通信记账；没有用全 fallback 代替 split 通过。

首轮新多 run fixture 错把同名轨迹重开的两段当成四帧，测试停止；改为两个轨迹文件后
重跑通过。最初日志保留为 `domain-host.log`，中间版本为 `domain-final-host.log`，交付
数值结论只引用 `domain-delivery.log` 的二进制。没有因此更改输出语义、golden 或容差。

## 成本与解释边界

逻辑新增设备空间为 3 个 L 长 int mask + 3 int scratch，即 `12L+12` bytes，capacity
可以保留高水位。cache miss 有 3 个分类 kernel、mask/scratch 初始化及两次小 D2H（共
24 bytes；属于分类成本，不是 MPI/staging 通信字节）。每次 split 比 full 多 5 个常规
launch，启用 ZBL 时多 6 个；中心生产集合总计仍为 D/O，各恰一次。

`classify_host_seconds` 是含 cache miss GPU/D2H 完成等待的 host steady_clock；cache hit
仅 key 检查。旧 split `nep_device` 是 pipeline 包围区间，可能含周期 neighbor host/MPI
空隙；verify 还执行双计算与下载，不能用这些数值冒充纯 kernel 时间、overlap 或性能收益。
成本比较针对同二进制 off/serial，包含共同的选择 guard，不是与 O1 旧二进制的完整回归成本。
成本实验计划、逐 trial 命令和数据由
[measure_cost.py](../../dmgmd-overlap-o2-20261015/measure_cost.py)与现有 benchmark/C1 承载。

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

首轮随机序列偶然得到 off/off/off/serial/serial/serial；其 12 个样本仍在 `cost-*`
中保留，随后用上述配对组核对（[预定命令](../../dmgmd-overlap-o2-20261015/paired-cost-plan.json)、
[运行脚本](../../dmgmd-overlap-o2-20261015/measure_cost-paired.py)）。两组都观察到退化；
没有据此优化 kernel、改布局或扩大本轮范围。分类 host 计时原值保留在逐次日志；它还可能
等待默认 stream 前面排队的 unpack/clear，不把混合小 fixture 的中位数外推到 carbon_200k。

## 精确命令与交接

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

下一阶段入口是计划 O3：在现有同步调度位置引入 begin/progress/finish，仍立即 finish，
先定义 request/send/recv/unpack/storage 的生命周期。当前 `compute_domain` 要求 halo
ready，不能直接移到在途通信前；须在单独阶段扩展接口和完成状态证明，复用 off/serial/
verify oracle、缓存失效和两后端矩阵。M2b、stream/progress thread 与真实 overlap 尚无授权
实施或数值证据。本轮到 O2 停止。
