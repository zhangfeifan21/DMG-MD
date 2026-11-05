# DMG-MD 当前状态

类别：实现状态索引。核对日期：2026-11-05；当前 HEAD：`80c5768`（工作区含未提交代码与文档更新）。各历史阶段身份见对应报告。

2026-10-30 新增独立产品化计划：[DMG-MDv1.0开发流程](../plans/dmg-md-v1.0-development-workflow.md)（IN PROGRESS）。
维护者已确认 Gate 0，A0–A6 与 G-A 已完成；本轮新增 NHC/BDP，不属于 O 系列优化。
实际范围、统计与额外 harmonic 轴向 INCONCLUSIVE 见[恒温器矩阵](validation/thermostat-validation.md#thermostat-matrix-20261102)。
逐 Step 进度见[执行进度记录](../plans/dmg-md-v1.0-progress.md)。
2026-11-03 [harmonic 跨引擎对照](validation/thermostat-validation.md#thermostat-reference-controls-20261103)完成：
同预算下 GPUMD/LAMMPS 与 DMG-MD 均为 K PASS、轴向 INCONCLUSIVE；完整 harmonic 仍非 PASS，未改生产恒温器。

当前已实现 全量复制模式/局部域分解模式、统一缓存重建、CPU 采样、三引擎 benchmark 编排及 O1–O9 的已验证阶段；
O11 完成限定范围复验。
O4 默认关闭的普通步实际重叠已实现并通过本轮验收；默认仍同步 full。
O4 身份见 [O4 报告](validation/communication-computation-overlap.md#overlap-o4-20261016)。
O5 的本机正式实验于 2026-10-21 完成：主矩阵 600、历史归因 150、退化场景 120，
另有独立采用验证 40 个有效 trial；诊断 88 组、Nsight 20 组。默认仍 HostStaged/full、overlap=off。
固定 CudaAware 的精确 water/P4/P8 独立验证净加速约 1.0627×/1.0927×；
本机 HostStaged/full 仍更快。全部范围、CPU 成本、不确定性与重算入口见
[O5 报告](performance/communication-computation-overlap-results.md#o5-20261021-report)。
中断和失败尝试原样保留，不混入有效统计。

补充成本分析确认：carbon_200k/P2/CudaAware 的 split-serial 比 full 多
4.601 ms/step，wait 回收 0.926 ms/step，仍净慢 3.675 ms/step。
独立诊断将损失定位在 GPU 过滤加 NEP 阶段；分类使用缓存，不能解释这项逐步成本。
完整 grid 加掩码的执行效率是候选原因，具体微架构归因尚未证实。
模式解释、证据与限制见[O5 后续成本分析](performance/communication-computation-overlap-results.md#o5-cost-20261021-report)。

O6 于 2026-10-21 完成稳态诊断与复验：45 个新正式 trial、27 份稳态 trace；carbon
拆分墙钟增加约 4.630 ms/step，主要新增 GPU 成本位于 radial。water/P8 的 partial 增量
更大，不能统一归因为 radial。NCU/LaunchStats 均受计数器权限拒绝，微架构原因仍为
INCONCLUSIVE，单因素执行验证已由 O6.5 补充。见 [O6 报告](performance/nep-execution-cost.md#o6-results-20261021-report)。

O7 于 2026-10-23 完成并采用：缓存判定从 MAX→reason OR 合并为一次 OR；普通步固定
collective 从 3 次降为 2 次。数值、实际调用次数及 60 个正式 trial 均通过；六个性能
区间均跨过 1，整步收益 INCONCLUSIVE。见 [O7 报告](performance/cache-reduction-results.md#o7-results-20261023-report)。

O8 于 2026-10-26 完成并采用：局部域分解模式 NVE 只在 thermo/XYZ 输出步计算辅助 thermo，
NVT 保留每步反馈。292 个实际调用计数作业、MPI 68、nightly 18 + restart 12、
105 个正式 trial 与 8 个 marker CPU 诊断完成；相对 O7 三组确认 1.019–1.028×，
carbon/HostStaged 增量 INCONCLUSIVE。范围、慢样本与采集器修正见
[O8 报告](performance/thermo-demand-results.md#o8-results-20261026-report)，默认仍 HostStaged/full、overlap=off。

O9 于 2026-10-26 完成证据门槛复核：两份既有二进制均通过八 rank 环境预检和四个
golden case，CPU CTest 15/15；实际重试原 O6 稳态窗口仍被 ERR_NVGPUCTRPERM 拒绝。
没有新的单因素证据选定执行候选，按计划以 NO_ADOPTION 结束该分支；生产源码及当前
二进制不变，新候选性能未测，微架构原因仍 INCONCLUSIVE。见 [O9 报告](performance/nep-candidate-validation.md#o9-results-20261026-report)。

O6.5 于 2026-10-27 完成：30 个正式 trial、18 份新稳态 trace、十个真实图/mask 快照、
50 个独立 A–D 重放进程、两份实际 compact launch trace 和完整 nightly 42 配置通过。
carbon radial / water angular partial 的紧凑映射相对 C 分别降低约 29.71%/12.32% 目标
耗时，配对区间确认；D 仍慢于完整 A。真实覆盖否定统一的双相过半解释；NCU 仍受权限
限制。它们仅取得进入独立整步试验的证据，未测 compact 整步净收益、未生产采用。
见 [O6.5 报告](performance/nep-execution-cost.md#o65-results-20261027-report)；默认仍 HostStaged/full、overlap=off。

O8.5 于 2026-10-28 完成（10-27 开始）：显式 producer 调度仅拆分 filter/descriptor，
radial/partial/many-body 在 halo 和全部 descriptor 完成后完整执行，基础 launch 10→7。
完整正确性门槛、120 个六组合正式 trial、24 份时间线和 8 个 CPU 诊断通过。
四组拆分成本下降；相对同后端候选 full，water/HostStaged、carbon/CudaAware、
water/CudaAware 的比值为 1.0193×、1.0321×、1.0912×，探索区间下界均大于 1，
carbon/HostStaged 仍更慢。相对默认 HostStaged/full，只有本轮 water/P8/HostStaged
显示 1.0193× [1.0155, 1.0256]；两个 CudaAware 候选均未胜过默认路径。
见 [O8.5 报告](performance/producer-schedule-results.md#o85-results-20261027-report)。结果交 O9 独立确认，
未采用；默认仍 HostStaged/full、overlap=off、schedule=split。

O8.6 于 2026-10-28 完成：B 对同 E/H 使用稳定 GPU 紧凑索引，C 直接从实际 Verlet q
做几何内缩；只改变 filter/descriptor 的中心映射，完整 force consumer、所有权和 halo 不变。
两路线完整正确性门槛、240 个正式 trial、48 份 trace、24 个 CPU 诊断及离线复算通过。
B/A 的四组 serial/wait 增量获探索区间支持；C/B wait 增量均 INCONCLUSIVE。
相对同候选二进制 HostStaged/full，B/C 在 carbon/HostStaged 为 1.0231×/1.0221×，
carbon/CudaAware 为 1.0173×/1.0235×，water/HostStaged 为 1.0321×/1.0362×；
water/CudaAware 为 0.9292×/0.9275×，仍慢。范围、全部区间、维护/内存与原始证据见
[O8.6 报告](performance/producer-schedule-results.md#o86-results-20261028-report)。未采用，交 O9 用新样本独立确认；默认不变。

## 已实现能力

| 能力 | 当前行为 / 合同 |
| --- | --- |
| 输入与 MD | GPUMD 格式 model/run/NEP4/5、NEP-ZBL，NVE、`nvt_ber`、`nvt_nhc`、`nvt_bdp`、兼容输出及跨 rank 新作业 restart；详见[支持矩阵](../standards/compatibility-matrix.md) |
| 新恒温器 | root 四节 NHC 链或 BDP RNG/cache；固定正步长、每段重置、restart 不连续；未验证 CV/自适应/实验组合明确拒绝；[合同](../standards/ensembles.md) |
| 全量复制模式 | P=1 固定使用；P>1 不满足局部域分解条件 时回退。每 rank 完整 NEP scratch，积分/thermo/输出由 slab owner 唯一负责 |
| 局部域分解模式 | 正交全周期大盒且 slab 足够宽时，使用 owned/ghost 本地 device 数据、两跳位置 halo、p2p 通信和直接迁移；详见[MPI 合同](../standards/mpi-runtime.md) |
| 缓存与分配 | epoch 内 manager/layout/map 固定；局部严格位移超限位经一次全局 OR 决议重建和迁移；capacity 可复用，host identity 仍每 rank 完整保存 |
| O6.5 诊断 | 默认 OFF 的独立快照/目标 kernel 重放；保持物理数组、stride 与数学不变；[合同](../standards/domain-partition.md#o65-隔离快照重放) |
| O8 thermo | NVE 按 thermo/XYZ 消费者步号并集执行，NVT 每步保留；无旧值复用，全量复制模式/P1 不变；[合同](../standards/thermo-demand.md) |
| O8.6 实验调度 | `producer-compact` 同 E/H 紧凑映射、`producer-geometry` 实际 Verlet q 几何规则；默认 split；[合同](../standards/domain-partition.md#o86-producer-紧凑与几何调度) |
| O8.5 实验调度 | `DMGMD_DOMAIN_SCHEDULE=producer` 显式选择 descriptor E/H、完整 force；原 split/full 保留，默认 split；[合同](../standards/domain-partition.md#o85-producer-调度) |
| O1 诊断 | 默认关闭的局部域分解模式时间线、benchmark A/B 编排与 C1 marker 对齐；整段计时不等于 CPU 利用率 |
| O2 串行分区 | `DMGMD_DOMAIN_PARTITION=off|serial|verify`，默认 off；普通步按完整 Verlet 图分区，halo 后串行执行；[合同](../standards/domain-partition.md) |
| O3 halo 生命周期 | begin/progress/finish、单槽位与请求/事件完成检查；默认立即 finish，O4 可插入 early；无新 stream/progress thread；[合同](../standards/halo-lifecycle.md) |
| O4 实验调度 | overlap=off/wait/poll；prepare→begin→early→finish→late，默认 off；本轮 476 组数值矩阵与 profiler 通过 |
| CPU 采样/绑定 | C1 仍只观测；新增共用 core/numa/rankfile 启动策略和独立 affinity audit，C1 关闭也核验；[绑定合同](../standards/cpu-binding.md) |
| 三引擎对照 | 输入、静态比较和 benchmark 编排已实现；DMG-MD 绑定已接线；跨引擎绑定验收与正式效率矩阵未完成 |

P>1 的 triclinic/非周期输入仍拒绝。默认后端为 HostStaged；CudaAware 必须通过 capability
query、device collective 与 p2p 数值自检。支持范围、字节公式和容差分别在标准、测试脚本
及 manifest 维护，本页不复制。

## NPT / G-B（2026-11-04）

- **B0–B4 完成，G-B PASS。** 压力oracle、全量复制模式盒安全合同、Ber golden、SCR独立统计、输出/restart/负例与旧路径回归均有最终验收结果记录，见[NPT矩阵](validation/npt-validation.md#npt-matrix-20261104)及[NPT/G-B最终报告](validation/npt-validation.md#npt-scr-confirmation-20261104)；迁移与逐作业过程材料不作为额外保留项。
- n3 successor共160条全新完整链，唯一冻结分析于19:39（Asia/Shanghai）结束，primary base/half和P2/P4双后端bridge的均值、variance、体积CDF与ESS门全部PASS。身份、独立科学复核与分析后6项CPU测试通过，见[最终结果](validation/npt-validation.md#npt-scr-confirmation-20261104)及[结果索引](validation/npt-scr-n3-confirmation-index.json)。执行freeze SHA `f0774f57112e0aed882e64a59d5c651219532f81de288e213404e9141ad24be6`，报告SHA `ddc4b004888706b2915d60f494a1590869dda6f637b35acc0a67e2aa5c565461`。
- `npt_ber`/`npt_scr`限全量复制模式各向同性正交全周期；P>1强制全量复制模式，局部域分解模式不提供NPT。SCR统计证据仅216原子NEP-C、300K/0GPa，base覆盖P1 HostStaged和P2/P4双后端，half仅P1 HostStaged；NEP5/ZBL是另外的短合同验证。n3证据为单节点，不代表RDMA或跨节点。
- 原18 looks的INCONCLUSIVE保持不变；旧确认的63条completed、6条interrupted、91条planned仅作为历史计数摘要保留，未并入新数据。共同theory-target诊断单列，不替代安全检查或证明普遍严格采样。盒/thermo/restart时点及unwrapped限制见[控压器标准](../standards/barostat.md)；C/D未启动，v1.0未发布。

## 验收证据

| 日期 / 实现 | 已记录结果 | 范围与证据 |
| --- | --- | --- |
| 2026-11-02 A3–A6 | host CTest23/23、NHC/SVR逐位oracle、52短程配置/重复、54restart、11/12组KE及实际NEP分量通过；旧路径50stage精确A/B相同 | [A3](validation/thermostat-validation.md#nhc-20261102)、[A4](validation/thermostat-validation.md#bdp-20261102)、[A5](validation/thermostat-validation.md#thermostat-matrix-20261102)、[A6](validation/thermostat-validation.md#thermostat-docs-20261102)；附加harmonic轴向INCONCLUSIVE单列，性能/跨节点/release未放行 |
| 2026-11-03 B0–B4 子门 | 压力oracle、盒变更、Ber、输出/restart/负例/NEP5-ZBL和冻结旧路径回归通过；当日SCR原18 looks总体INCONCLUSIVE、G-B PENDING | [原统计记录](validation/npt-validation.md#npt-scr-final-20261103)、[B4输出/restart](validation/npt-validation.md#npt-output-20261103)、[旧路径回归](validation/npt-validation.md#npt-regression-20261103) |
| 2026-11-04 B3/B4/G-B 最终验收 | n3全新160条确认与唯一冻结分析PASS；身份/科学审计、分析后6项CPU测试及证据/文档整合完成，G-B PASS | [最终确认](validation/npt-validation.md#npt-scr-confirmation-20261104)、[新索引](validation/npt-scr-n3-confirmation-index.json)、[NPT/G-B矩阵](validation/npt-validation.md#npt-matrix-20261104)；限定NEP/单节点全量复制模式，原18 looks保留 |
| 2026-10-28 O8.6 | 两份环境 P8/golden 4、GPU CTest 18 + 专项 1；两路线各完整 MPI、调度 27、nightly 42 + restart 12；正式 240、trace 48、CPU 24 及离线复算通过 | [O8.6 实测](performance/producer-schedule-results.md#o86-results-20261028-report)；B/A 确认，C/B wait 未确认，water/CudaAware 未胜默认路径；未采用 |
| 2026-10-28 O8.5 | 两份环境 P8/golden 4、GPU CTest 18、producer MPI 313 次执行、调度 27、完整 nightly 42 + restart 12；正式 trial 120、trace 24、CPU 诊断 8 均通过 | [O8.5 实测](performance/producer-schedule-results.md#o85-results-20261027-report)；四组拆分成本下降，默认路径净收益限于本轮 water/P8/HostStaged，待独立确认、未采用 |
| 2026-10-27 O6.5 | 正式 trial 30、稳态 trace 18、快照 10、重放进程 50、CUPTI 2；环境 P8/golden 4、GPU CTest 17、MPI 136、完整 nightly 42 + restart 12 均通过 | [O6.5 实测](performance/nep-execution-cost.md#o65-results-20261027-report)；映射候选有目标计时证据，整步净收益未测、未采用 |
| 2026-10-26 O9 | 两份二进制各环境 P8/golden 4/4，CPU CTest 15/15；一次 NCU 重试 INCONCLUSIVE | [O9 复核](performance/nep-candidate-validation.md#o9-results-20261026-report)；NO_ADOPTION，无新候选性能或 NEP 执行改动 |
| 2026-10-26 O8 | 调用计数 292、MPI 68、nightly NVE/NVT 18 + restart 12、实际输入 smoke 48、正式 trial 105、CPU 诊断 8 全部通过 | [O8 实测](performance/thermo-demand-results.md#o8-results-20261026-report)；采用按需 NVE thermo，增量收益限于冻结范围 |
| 2026-10-23 O7 | 边界/调用计数 108 + 全量复制模式 4、MPI 主矩阵 68、nightly NVE 子矩阵 18 + restart 12、实际输入 smoke 36、正式 trial 60 全部通过 | [O7 实测](performance/cache-reduction-results.md#o7-results-20261023-report)；采用单次 OR，整步收益 INCONCLUSIVE，默认 HostStaged/full、overlap=off |
| 2026-10-21 O6 | 45/45 主 trial、27/27 稳态 trace、27/27 短控制、12/12 实际输入科学门槛；CPU CTest 12/12；NCU 两项 INCONCLUSIVE | [O6 实测](performance/nep-execution-cost.md#o6-results-20261021-report)；生产二进制未变、默认未改，硬件因果未定 |
| 2026-10-21 O5 | 870/870 主/历史/退化 trial；40/40 独立验证；C1 88/88、GPU 映射 384/384、Nsight 20/20；工具 CPU 单测 53/53 | [O5 正式报告](performance/communication-computation-overlap-results.md#o5-20261021-report)；未改生产二进制或默认；R=N/A、历史小接口成本部分 INCONCLUSIVE |
| 2026-10-16 O4 | domain 七模式 476 组；生命周期 68/68、调度/配置 25/25；golden 4/4、CTest 10/10、long_nve smoke 7/7；24 cost trial、24 profiler rank-step 证据 | [O4](validation/communication-computation-overlap.md#overlap-o4-20261016)、[固定资源成本](performance/communication-computation-overlap-results.md#o4-20261016-report)；candidate SHA-256 `860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6`；仍慢于 full，默认关闭 |
| 2026-10-15 O3 | 生命周期/错误作业 68/68；domain 三模式 192 组；O2/O3 科学文件 808 次字节比较；CTest 10/10；golden 4/4；long_nve smoke 7/7 | [O3](validation/communication-computation-overlap.md#halo-lifecycle-20261015)；candidate SHA-256 `d1abb2deb8e54d1552a54c1030a33dc89a12795fe946b890671d93d77989e646` |
| 2026-10-15 O2 | domain 三模式、CPU/GPU oracle、golden、CTest 与 long_nve smoke | [O2](validation/communication-computation-overlap.md#overlap-partition-20261015)；本机串行分区额外成本约 29–30%，保持默认关闭 |
| 2026-10-12 O1 | P2 双后端普通/重建时间线、C1 样本、固定资源窄矩阵 | [O1](performance/communication-computation-overlap-results.md#o1-20261012-report)；无 overlap/强扩展结论 |
| 2026-10-09 工具/外部参考 | C1 smoke；GPUMD/LAMMPS 静态 11 case；三引擎单卡及 LAMMPS 双卡 smoke | [C1](validation/cpu-sampling.md#cpu-sampling-c1-20261009)、[LAMMPS](validation/lammps-reference.md#lammps-reference-20261009) |
| 2026-09-26 单机八卡 | 3 算例 × 3 实现 × P1/2/4/8，五次有效重复；DMG-MD 8 卡自身加速 5.17–7.66× | [Benchmark](performance/benchmark-multigpu-results.md#multigpu-20260926-report)；另一台服务器，CPU 信息不足，无 LAMMPS |
| 2026-09-22 统一重建 | baseline、全量复制模式/局部域分解模式、I/O 故障与 smoke 通过；三体系 long 子集通过 | [第二阶段](performance/stage2-cost-report.md)；保留当时 binary/dirty 身份 |

2026-09-18 曾手动通过完整 nightly：7 cases、seed 0、P1/2/4、两后端，共 42 配置，
其中 局部域分解模式 28 配置；报告为本机生成物 `dmgmd-nightly-20260918-171122/report.json`，
candidate SHA-256 `07c8f0a0d4b9305cfd3f506ad7172960dbe2b72f02edf5589b73eae4fb2506dc`，
基线 revision `27e07d92b84f0e02d08d99c400c52b795175341f`。此历史结果不代表 O3
已重跑 nightly；O3 最新长程验证范围为 smoke。

## O9 独立确认（2026-10-29）

300 个正式 trial、12 个 CPU 诊断、60 个真实输入科学对照通过；新 MPI/27 个调度
检查通过，已验收同二进制的完整 nightly/GPU 门槛经封存哈希验证后复用。
预选 B（同 E/H 紧凑 producer）在 carbon_200k/P2 双后端、water_400k/P8/HostStaged
上通过多重比较校正的独立净收益确认；water/P8/CudaAware 仍慢于默认路径。
water/P4 有探索支持，carbon_1m/高重建/空 early 不支持外推。C/B wait 增量仍未确认。
结论为 CONFIRMED_SCOPED_CANDIDATE，交 O11 全局复验；默认不变。
完整证据与限制见 [O9 报告](performance/nep-candidate-validation.md#o9-results-20261029-report)。

## O11 限定采用复验（2026-10-30）

800 个正式 trial、80 个短程科学对照与 48 个 CPU 诊断完成；因首轮区间较宽按
冻结规则追加一次五块，全部慢样本保留。31,293 项封存文件及离线复算通过。
当时基于公共共享服务器测量，仅建议 carbon_200k/P2 的 HostStaged/CudaAware 手动 B-wait，相对默认 full 为
1.0210×/1.0199×；该建议属于历史探索，不作为专用性能服务器参考。后续性能参考与采用判断以专用服务器 benchmark 为准。water/P8/HostStaged 区间跨 1，当前不建议采用；CudaAware 未胜
默认路径。water/P4 仅探索支持，百万原子/高重建/空 early 不支持外推。
状态为 COMPLETE_SCOPED / SCOPED_MANUAL_USE，不声称完成原始全矩阵。
默认仍 HostStaged/full、overlap=off、schedule=split；O10 条件性分支暂缓。
见 [O11 报告](performance/nep-candidate-validation.md#o11-results-20261030-report)。

## 剩余工作

- 条件性：[小归约/halo 后端选择 O10](../plans/communication-computation-overlap.md#o10)
  为 PROPOSED、暂缓；O11 限定范围复验已完成，扩大未测范围须另行独立验证。
  首轮 O9 的 NO_ADOPTION 及后续 O9 独立确认历史均保留，生产默认不变。
- 独立 P0：[跨引擎/调度器绑定剩余验收](../plans/mpi-cpu-binding.md)、
  [三引擎动力学与正式性能矩阵](../plans/lammps-multigpu-benchmark.md)。
  DMG-MD 本机统一启动与独立核验已完成，不再列为未实现。
- 后续：100000-step release、分阶段中间场交换方案、3D/triclinic/非周期 local-domain 扩展。
- 暂缓：[双物理节点 I/O 验收](../plans/multi-node-io.md)；跨节点 scaling 尚无实测。
- 规模/兼容性缺口见[待办](../plans/risk-and-backlog.md)。host identity 为 O(N)/rank，
  root 输出需全局 gather；全表 debug 每步不变量断言未实现，不宣称端到端内存 scaling。

完整 revision/dirty 身份、环境和命令留在各报告；旧报告中的“下一阶段”表示当时交接，
当前优先级以本页与剩余计划为准。
