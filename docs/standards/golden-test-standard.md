# Golden Test 与验收原则

类别：现行测试标准。状态：ACTIVE。核对日期：2026-10-16。
GPUMD 锁定 `9d23496e41319b9e2af5221a7df6285387401d1e`。

## 事实源与边界

测试脚本决定实际执行，manifest 决定 case/profile/哈希/容差，测试目录 README 维护 CLI。
本文只定义比较层级；通过结果在[当前状态](../status/current.md)和带日期的 status 报告，
不得把测试已定义、skip 或子集通过写成完整矩阵已通过。

## 自动化入口

| 入口 | 覆盖 / 必须断言 |
| --- | --- |
| CTest | 见下表；不自动启动 baseline/MPI/long_nve GPU 矩阵 |
| `tests/baseline/run_baselines.py` | 四个 committed case；reference 身份/输入/golden hash 与单 GPU，结构 exact、数值按 manifest；reference 复现还须 byte-exact |
| `tests/mpi/check_environment.py` | 指定 executable/linkage、Open MPI/UCX CUDA transport、唯一 GPU、device collective 与 p2p 主动自检；环境变化先过此门 |
| `tests/mpi/run_mpi_differential.py` | P1/2/4、两后端，committed baseline、owned 覆盖、输出/通信及跨 rank/backend 比较；明确 M1 fallback |
| `tests/mpi/run_mpi_migration.py` | M1 逐步几何 owner、内部/周期/多 slab/N<P/空 slab、修速、多 run、跨 rank restart；精确字节与 P1 oracle；P>1 unsupported-box 门 |
| `tests/mpi/run_mpi_domain.py` | P2/4 两后端、同输入 P1 oracle，强制 mode=m2a；两跳/空域1000步、NEP5/typewise/ZBL、缓存/迁移/多 run/restart、计时 A/B、精确通信与 allocation |
| `tests/mpi/run_halo_lifecycle.py` | 独立 GPU 作业：P1/2/4、两后端、收发独立完成、unpack/复用、零 count/增长/幂等和单 rank 故障有界退出 |
| `tests/mpi/run_rank_io_isolation.py` | 本地 scratch、健康输出/清理与 mkdir/file/chdir/restore/cleanup 故障；物理双节点验收暂缓 |
| `tests/long_nve/run_long_nve.py` | NVE/NVT 长程、双向静态回放、跨 rank restart 及兼容势静态/短轨迹 |
| `tests/lammps_reference/compare_static.py` | 三引擎物理量等价；阈值在该目录 manifest，不能套用跨软件字节一致 |

### CTest 注册项

`ctest --test-dir build --output-on-failure` 当前注册十项（以 tests/CMakeLists.txt 为准）：

| test | 覆盖 |
| --- | --- |
| `dmgmd.run_parser` / `dmgmd.model_parser` | typed IR、schema、参数、支持/拒绝路径 |
| `dmgmd.spatial_ownership` | M1 边界/tie、槽位/ID/plan、空 slab/N<P、owner 覆盖 |
| `dmgmd.domain_layout` | typewise 半径/float 上界、eligibility、P2 去重、ghost/迁移/空域计划 |
| `dmgmd.domain_partition` | 独立图闭包、producer 唯一性与 key/ticket |
| `dmgmd.domain_neighbor_cuda` | CUDA 容器、非零 center offset、分类/oracle、key 失效和非法 row/token/反边拒绝；无 GPU 返回 skip |
| `dmgmd.long_nve_analysis` | 几何/势/hash、统计量、checkpoint、重试/失败分类 |
| `dmgmd.benchmark_analysis` | 三引擎日志、统计、报告/超时与实验配置 |
| `dmgmd.cpu_sampling` | 原始计数、窗口、身份/容量/缺失与采样生命周期 |
| `dmgmd.lammps_reference` | 等价势格式、单位、物理量比较器 |

### O2/O3 额外门槛

domain 验收显式 `--partition-modes off,serial,verify`：同 rank/backend 三模式科学文件
byte-exact，另保持原 P1 manifest 差分；不能全 fallback 冒充 split。verify 用同快照 full
oracle、CPU/GPU mask 比较、scratch/ghost poison，检查有效 R/A、Fp/sums/partial 与全部
9 virial，replay 不推进逻辑 call/MPI/I/O。key、反边、辅助 E\I、skin、新旧布局与空域须实际触发。

O3 生产立即 finish，保留完整三模式矩阵，并与锁定 O2 输出核对；生命周期测试不注册为
自动 CTest GPU 作业。接口和错误合同见[分区](domain-partition.md)、[halo](halo-lifecycle.md)。
修改 compat 必须连同 baseline/long_nve 验证；GPU skip 只能记 PARTIAL。

### 长程 profile

精确几何、步数、seed、rank/backend、sections 和统计门槛只在 long_nve manifest 维护。
smoke/nightly/release 分别为 100/10000/100000 步；nightly/release 的 P>1 专属长轴几何
必须命中 M2a、最大 P slab>2*d_coord。三物理势执行适用动力学/回放/restart，四兼容势
只执行静态/短轨迹。缩小 cases/seeds/ranks/sections 只算子集，不等于完整 profile。
100000-step release 尚无完整通过记录；最新 O3 只重跑 smoke。

## 比较层级

1. **完全一致**：成功/失败分类、文件集合/名称、schema/header/列/帧/step、整数、species、
   gid 顺序、输入版本/类型/hash、owned 唯一覆盖。身份/邻居/越界错误不能用浮点容差掩盖。
2. **确定性数值容差**：position/velocity/force/energy/virial/thermo/restart 等按
   `abs(test-ref) <= atol + rtol*abs(ref)`，阈值只在对应 manifest。candidate 跨 GPU/MPI
   reduction 不默认 byte-exact；明确要求的同二进制路径 A/B 除外。
3. **长期/统计**：混沌长轨迹不逐步对坐标；NVE 比真实 E(0) 的 excursion、drift、detrended
   RMS、相邻跳变、动量、距离直方图与双向静态回放；NVT 比温度、MSD、时间平均 partial RDF。
   多初态 median/q95、非劣 margin/floor 以 manifest 为准。Berendsen 兼容不证明 canonical 采样质量。

[三引擎标准](cross-engine-reference.md)允许有证明的格式/单位转换；不同软件输入/输出哈希
不必相同，转换前后哈希用于追溯。物理正确性先于正式效率排名。

## 基线与运行纪律

- reference 仅暴露单 GPU，禁止自动 NEP_MULTIGPU；各 stage 用独立空目录，避免 append 污染。
- 锁定源码、binary、输入/potential/golden hash。动力学用显式速度，随机初始化另做专项。
- 更新 golden 仅显式 `--update-goldens` 并审查；不因失败任意放宽容差。压力 fixture 不当生产平衡体系。
- long_nve 失败 stage 可按 CLI 重试，保留 failed-attempt 与失败文件；checkpoint 锁定身份/输出，
  resume 仅复用匹配结果，adopt-existing 显式标未验证 provenance。分区模式进入 candidate 身份。
- candidate 必须有合法 timing。墙钟与吞吐是诊断或单独 benchmark 的测量口径，正确性 suite
  不设性能通过门；不能将各 rank min/max 当重复实验误差。

操作见[baseline](../../tests/baseline/README.md)、[MPI](../../tests/mpi/README.md)、
[long_nve](../../tests/long_nve/README.md)、[benchmark](../../tests/benchmark/README.md)。
