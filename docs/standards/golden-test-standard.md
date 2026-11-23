# Golden Test 与验收原则

Golden 是参考程序在固定输入上生成的输出。短程比较需要先确认用的是哪份代码、哪个可执行文件，再比较文件结构和物理量。本文记录截至 2026-11-20 生效的验收原则。

## 事实源与边界

短程基线的 GPUMD 提交和可执行文件 SHA-256 以 [baseline manifest](../../tests/baseline/manifest.json) 为准。长程测试要求其参考锁与短程基线相符，性能测试按自己的 manifest 检查参考提交。[L20 参考身份与当前限制](cross-engine-reference.md)

运行哪些检查由测试脚本决定。算例、运行规模（profile）、哈希和容差以本套 manifest 为准。测试目录的 README 给出命令行用法。已运行的结果见[当前状态](../status/current.md)和带日期的验收记录。未执行、跳过和子集通过各自单列，均不能计为完整矩阵通过。

## 自动化入口

| 入口 | 覆盖 / 必须断言 |
| --- | --- |
| CTest | 运行下表中的已注册测试。baseline、MPI 与 long_nve GPU 矩阵各有独立入口 |
| `tests/baseline/run_baselines.py` | 四个单 GPU case。校验输入和 golden 哈希，候选结构精确比较，数值按 manifest 比较。reference 自检还校验参考 checkout 和二进制身份，并逐字节比较输出 |
| `tests/mpi/check_environment.py` | 指定 executable/linkage、Open MPI/UCX CUDA transport、唯一 GPU、device collective 与 p2p 主动自检；环境变化先过此门 |
| `tests/mpi/run_mpi_differential.py` | P1/2/4、两后端，committed baseline、owned 覆盖、输出/通信及跨 rank/backend 比较；确认回退到全量复制模式 |
| `tests/mpi/run_mpi_migration.py` | 全量复制逐步几何 owner、内部/周期/多 slab/N<P/空 slab、修速、多 run、跨 rank restart；精确字节与 P1 oracle；P>1 unsupported-box 门 |
| `tests/mpi/run_mpi_domain.py` | P2/4 两后端、同输入 P1 oracle，要求日志出现 `mode=m2a`；两跳依赖、空域 1000 步、NEP5/typewise/ZBL、缓存/迁移/多 run/restart、计时 A/B、精确通信与 allocation |
| `tests/mpi/run_halo_lifecycle.py` | 独立 GPU 作业：P1/2/4、两后端、收发独立完成、unpack/复用、零 count/增长/幂等和单 rank 故障有界退出 |
| `tests/mpi/run_rank_io_isolation.py` | 本地 scratch、健康输出/清理与 mkdir/file/chdir/restore/cleanup 故障；物理双节点验收暂缓 |
| `tests/long_nve/run_long_nve.py` | NVE/NVT 长程、双向静态回放、跨 rank restart 及兼容势静态/短轨迹 |
| `tests/thermostats/` | NHC/SVR 逐位 oracle、NHC GPUMD 短轨迹、BDP seed 合同、两路径/后端重复、root 因子协议、预注册 KE/速度分量 CI 与新作业 restart；入口见[说明](../../tests/thermostats/README.md) |
| `tests/npt/` | B2 `npt_ber` GPUMD golden、P1/P2/P4 全量复制路径、压力方向/输入安全/restart 分类；B3 `npt_scr` 统计条件冻结为同条件 GPUMD reference；总状态见[NPT/G-B 矩阵](../status/validation/npt-validation.md#npt-matrix-20261104) |
| `tests/lammps_reference/compare_static.py` | 三引擎物理量等价；阈值在该目录 manifest，不能套用跨软件字节一致 |

### CTest 注册项

`tests/CMakeLists.txt` 当前注册 44 项。列表可用 `ctest --test-dir build -N` 查看。
`dmgmd.domain_neighbor_cuda`、`dmgmd.box_control` 和 `dmgmd.owned_box_control`
需要可用 GPU。三项的退出码 `77` 由 CTest 记为跳过。

| test | 覆盖 |
| --- | --- |
| `dmgmd.run_parser` / `dmgmd.model_parser` | typed IR、schema、参数、支持/拒绝路径 |
| `dmgmd.ensemble_control` | target ramp、Berendsen 原表达式、NHC/BDP 标量状态与 guard、seed/cache/无分配合同 |
| `dmgmd.nhc_fixture` / `dmgmd.svr_fixture` | 与只读上游独立提取的固定 fixture 比较 double 位模式 |
| `dmgmd.thermostat_statistics` | 自相关/ESS、块与 seed 间 Student-t CI、预注册误差带判定 |
| `dmgmd.spatial_ownership` | 全量复制边界/tie、槽位/ID/plan、空 slab/N<P、owner 覆盖 |
| `dmgmd.domain_layout` | typewise 半径/float 上界、eligibility、P2 去重、ghost/迁移/空域计划 |
| `dmgmd.domain_partition` | 独立图闭包、producer 唯一性与 key/ticket |
| `dmgmd.domain_neighbor_cuda` | CUDA 容器、非零 center offset、分类 oracle、key 失效与非法 row/token/反边拒绝 |
| `dmgmd.npt_manifest` / `dmgmd.npt_statistics` / `dmgmd.npt_paired_confirmation_math` / `dmgmd.npt_paired_confirmation_integration` | 全量复制 NPT 注册、统计分析与独立确认 |
| `dmgmd.pressure_oracle_cpu` / `dmgmd.pressure_writer` | 主机压力 oracle、应力输出列序与单位 |
| `dmgmd.box_control` / `dmgmd.owned_box_control` / `dmgmd.box_control_host` / `dmgmd.m2a_npt_guard` | 盒缩放、owned 原子写入限制、主机几何与局部 NPT 守卫 |
| `dmgmd.domain_npt_geometry_oracle` / `dmgmd.domain_npt_rounding_cell_oracle` / `dmgmd.domain_npt_pressure_oracle` | 局部 NPT 几何、舍入边界和压力 oracle |
| `dmgmd.domain_npt_runner` / `dmgmd.domain_npt_contracts_runner` / `dmgmd.domain_npt_migration_runner` / `dmgmd.domain_npt_affine_cache_runner` | 局部 NPT 驱动器的输入、合同、迁移和形变缓存检查 |
| `dmgmd.domain_npt_statistics` / `dmgmd.domain_npt_statistics_integration` / `dmgmd.domain_npt_statistics_proofs` / `dmgmd.domain_npt_statistics_batch` | 局部 NPT 统计、证据约束与批量作业驱动 |
| `dmgmd.long_nve_analysis` | 几何/势/hash、统计量、checkpoint、重试/失败分类 |
| `dmgmd.benchmark_analysis` | 三引擎日志、统计、报告/超时与实验配置 |
| `dmgmd.cpu_sampling` | 原始计数、窗口、身份/容量/缺失与采样生命周期 |
| `dmgmd.cpu_binding` | 每 rank K 核、NUMA/rankfile 策略、绑定审计与拒绝路径 |
| `dmgmd.overlap_o5_analysis` | 正式矩阵、历史归因、采用验证与封存一致性 |
| `dmgmd.overlap_o6_analysis` / `dmgmd.overlap_o65_analysis` / `dmgmd.overlap_o7_analysis` / `dmgmd.overlap_o8_analysis` | 稳态成本、快照重放、缓存判定与按需 thermo 的证据/统计合同 |
| `dmgmd.overlap_o85_analysis` / `dmgmd.overlap_o86_analysis` / `dmgmd.overlap_o9_analysis` | producer 调度、紧凑/几何调度与 O9 独立确认的矩阵/证据合同 |
| `dmgmd.lammps_reference` | 等价势格式、单位、物理量比较器 |

O11 的契约测试 `tests/benchmark/test_overlap_o11.py` 目前未注册到 CTest（单独运行 3/3
通过），是否纳入待测试矩阵变更审批。

### O2/O3 额外门槛

局部域分解验收显式设置 `--partition-modes off,serial,verify`。同一 rank 数及后端下，三种模式的科学输出必须逐字节相同，还要通过原 P1 manifest 的差分检查。测试必须实际走到分区计算，不能全部回退。

`verify` 用同一快照做一次完整求力作对照，比较 CPU/GPU mask，再用特殊值填充 scratch 和 ghost 来暴露误读。检查范围包括有效的 R/A、Fp、sums、partial 和全部 9 个 virial 分量。重放不得推进逻辑调用计数、MPI 或 I/O。缓存 key、反向边、辅助集合 E\I、skin、新旧布局和空域都须实际触发。

当前生产默认在开始 halo 交换后立即等待完成（O3）。须保留三种模式的完整矩阵，并与锁定的 O2 输出核对。生命周期测试是单独的 GPU 作业，未注册为自动 CTest。修改兼容层还须运行 baseline/long_nve。因没有 GPU 而跳过只能记 PARTIAL。[分区](domain-partition.md)、[halo 生命周期](halo-lifecycle.md)

### 长程 profile

长程运行分 smoke（100 步）、nightly（10,000 步）和 release（100,000 步）。准确的几何、seed、rank 数、后端、测试项目及统计门槛以 long_nve manifest 为准。

nightly/release 的多 rank 长轴体系必须进入局部域分解模式，最大 P 时分片宽度也须满足 `slab>2*d_coord`。三种物理势执行适用的动力学、回放和 restart 检查，另四种兼容势只做静态和短轨迹检查。缩减算例、seed、rank 或测试项目时，只能报告该子集。100,000 步 release 尚无完整通过记录。[smoke 和 nightly 历次结果](../status/current.md)

## 比较层级

| 比较对象 | 如何判定 |
| --- | --- |
| 身份与文件结构 | 成功或失败分类、文件集合和名称、schema、header、列、帧、step、整数、species、gid 行序和输入版本/类型/哈希必须完全匹配。每个负责原子只计一次。身份错、邻居错或越界直接失败。 |
| 确定性数值 | 位置、速度、力、能量、virial、thermo 和 restart 按 `abs(test-ref) <= atol + rtol*abs(ref)` 比较。阈值以对应 manifest 为准。跨 GPU 或 MPI 归约可能改变低位。同一二进制且明确要求逐字节一致的 A/B 检查，仍须逐字节相同。 |
| 长轨迹与采样 | 混沌轨迹比较长期物理量。NVE 以实际初始能量 E(0) 为准，测偏移幅度、漂移、去趋势 RMS 和相邻步跳变，还看动量、距离直方图及双向静态回放。NVT 比温度、MSD 和时间平均的分波径向分布函数（partial RDF）。多初态的中位数、q95 和非劣效判据的 margin/floor 由 manifest 定义。Berendsen 兼容性不能证明正则采样质量。 |

NHC/BDP 的正则矩另由 thermostat manifest 冻结的独立初速、seed、burn、块长度、预算、误差带
和最低 ESS 判定，CI 必须完整落入误差带。Berendsen 的涨落不构成正则 oracle；总 K 通过不能
替代速度分量/有效自由度检查。减半步长须保持物理耦合时间。完整方法及有限预算的
INCONCLUSIVE 见[系综合同](ensembles.md#统计证据的适用范围)。

NPT 采用维护者批准的跨引擎相容性判据。须固定相同的模型、势、`run.in`、物理单位和运行条件，再比较候选与 GPUMD 或 LAMMPS 参考的结果。双方共同偏离理论目标时，另列诊断。未经验证的理论近似不参与通过判定，参考相容也不足以证明普遍严格采样。[NPT 统计范围与指标](../status/validation/npt-validation.md#npt-matrix-20261104)

G-B 还要求直接验证安全行为。非法参数或几何、非有限状态及无效缩放因子应使作业失败。所有权或邻居缓存错误、MPI 挂起、错误的输出文件或输出时点须由专项测试发现。

[三引擎标准](cross-engine-reference.md)允许有证明的格式/单位转换；不同软件输入/输出哈希
不必相同，转换前后哈希用于追溯。物理正确性先于正式效率排名。

## 基线与运行纪律

- 每次运行参考程序只让它看到一张 GPU，避免自动选择 `NEP_MULTIGPU`。每个阶段使用独立空目录，以免追加写入与旧输出混在一起。
- 固定源码版本、可执行文件、输入、势文件和 golden 的哈希。动力学对照使用显式初速，随机初始化单独测试。
- 只有显式指定 `--update-goldens` 才能更新 golden，更新后须审查差异。失败不构成放宽容差的依据。压力测试构型只用于对应检查，不能当作生产平衡体系。
- long_nve 允许按命令行说明重试失败的阶段，须保留 failed-attempt 和失败文件。checkpoint 锁定身份与输出。resume 只复用匹配结果；adopt-existing 必须明确标记来源未经验证。分区模式也计入候选程序的身份。
- 候选作业须输出有效的计时记录。墙钟和吞吐供诊断或独立 benchmark 使用，正确性测试不设速度门槛。同次作业内各 rank 的最小、最大值描述进程间差别。重复实验的误差须从多次独立运行估算。

操作见[baseline](../../tests/baseline/README.md)、[MPI](../../tests/mpi/README.md)、
[long_nve](../../tests/long_nve/README.md)、[benchmark](../../tests/benchmark/README.md)。
