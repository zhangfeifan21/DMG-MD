# DMG-MD 当前实现进度

类别：进度与实测备忘。

更新日期：2026-09-22。

本轮第一阶段修改前的 M2a 基线为 clean revision
`27e07d92b84f0e02d08d99c400c52b795175341f`。Release 配置为
`CMAKE_BUILD_TYPE=Release`、CUDA architectures `75;80;86;89;90`；修改前
`build/dmg-md` SHA-256 为
`07c8f0a0d4b9305cfd3f506ad7172960dbe2b72f02edf5589b73eae4fb2506dc`，与
2026-09-18 nightly 报告记录的 candidate 相同（已核对报告内
`candidate_sha256` 一致、42/42 配置通过、其中 m2a 28 个）。该报告位于本工作树
gitignored 目录 `dmgmd-nightly-20260918-171122/report.json`。修改前工作树无 dirty diff。

锁定输入身份：`tests/baseline/manifest.json` SHA-256
`da3d7f30bd612a34b8f343bdf9c477f1a45dee8fe3a1ae0bf317e02e57ec27c5`；
`nep_C.txt` / `nep_water.txt` / `nep_BaTiO3_zbl.txt` 分别为
`add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623`、
`8638300c8c6ba7eca589fa2fdc111d2e6a1f6ad9c88cc522ea9db220538a66e3`、
`d9d5801eb267772294deee6eebd2e1e40f83908f867b5bf69467d1826034aa02`。

## 当前结论

`dmg-md` 已完成 single-rank 路径、一 MPI rank 一 GPU 的 replicated-data MPI
runtime（M1）与 M2a local-domain 路径：

- M1：数据面 replicated-full，积分/thermo/输出权威按空间 slab 所有权归属，
  迁移为 global_id 上的逻辑移交（P=1 恒走此路径，输出与 M0 逐字节一致）；
- M2a（2026-09-18 新增）：满足 eligibility（P>1、正交全周期、NEP large-box
  判据、`slab_width >= d_coord`）的输入进入 rank-local owned/ghost 布局 +
  保守两跳位置 halo + p2p halo/迁移 + NEP 中心/依赖域分片；其余 P>1 输入
  自动回退 M1 replicated-full（`DMGMD_DOMAIN mode=m1-fallback`），小盒继续可运行；
- M2b（Fp/partial 分阶段交换）与 M3（多节点硬化）仍未实施。

M2a 的 10000-step nightly profile 已于 2026-09-18 手动执行并通过验证：覆盖 7 个
case、seed 0、1/2/4 rank、HostStaged/CudaAware 共 42 个配置，其中 2/4 rank 的
M2a 配置共 28 个，全部通过。100000-step release 矩阵尚未执行，因此仍不发布
性能或 scaling 结论。

## 第二阶段：统一重建与分步计时（2026-09-22）

M2a 已把即时几何 owner 迁移与 Neighbor 内部 skin 判定合并为唯一、全 rank 一致的 cache
decision：epoch 内 manager owner/layout/membership/map/neighbor rows 保持，普通步只刷新固定
ghost 坐标；位移超过 `skin/2` 时才在同一 rebuild transaction 中迁移最终 geometric owner、
重建 membership/layout/neighbor 并重置连续位移。连续 run 复用已确认 cache；unsupported
multi-box 位移在 force 前失败。

`DMGMD_DOMAIN_TIMING=1` 提供普通/重建互斥分类、阶段 CUDA/host/MPI 计时、rebuild reason、
bounded histogram 与 rank min/mean/max；默认关闭时不创建 event、不新增同步或逐步输出，段末
才归约详细计时。定向 `micro_crossings`、连续 run、PBC/multi-slab、空 rank/N<P、restart、
双后端 exact communication 与 timing on/off 科学输出已通过。最终二进制 SHA-256 为
`13da7a81c9c77f46b53dd45ada81d7a593634800c30b2714c13d2fa3bc93b80d`。该哈希对应收尾审查后
的最终 Release 重建：详细计时关闭时逐步路径不读取 host clock，位移阈值统一由
`kNeighborSkin` 推导；下述完整正确性与成本矩阵均已在该二进制上重跑。

正式 nightly 几何成本采集覆盖 carbon/water/BaTiO3、P=1/2/4 与 HostStaged/CudaAware；
10000-step long stages 全部正确性 PASS。频率、阶段成本、通信/布局/分配与瓶颈见
[第二阶段成本报告](./stage2-cost-report.md)。结论是 HostStaged 已在三体系获得有限正扩展，
CudaAware 的 water/BaTiO3 仍未正扩展；这不改变任何正确性门槛，也不在本轮继续无限优化。
同一最终二进制还通过 smoke 7/7（含三体系 NVE/NVT、双向回放、restart 与四个兼容势短程）
以及 4-rank 单节点 rank-I/O 隔离/故障注入矩阵；结果分别保存在
`dmgmd-stage2-smoke-final-20260922/report.json` 与测试 stdout。

## 第一阶段：日志与 GPU 分配（2026-09-21）

本轮保持 M2a 迁移与保守两跳 halo 算法、逻辑 `local_count` stride、kernel 顺序和数值
输出语义不变，实施了：

- `DMGMD_DOMAIN_LAYOUT` / `DMGMD_DOMAIN_MIGRATION` 改为
  `DMGMD_DOMAIN_DIAGNOSTICS=1` 显式诊断；逐步通信日志默认间隔由 1 调整为 1000，依赖
  详细记录的测试显式设置开关/间隔；
- 每个 run 段末保留 rank-local `DMGMD_DOMAIN_SUMMARY`，分别统计 migration step、
  rebuild step、layout upload、workspace logical update、capacity growth event 与实际
  `GPU_Vector` allocation；计数不新增逐步 collective；
- `GPU_Vector` 保留原 `resize` 行为，新增 logical size 与 allocation capacity 分离的
  `resize_reuse`；M2a Atom/NEP/Neighbor workspace 容量足够时复用，扩容留 25% 余量，
  带 value 的 resize 仍初始化整个逻辑区；force/PE/virial 每次 layout upload 继续清零；
- 测试新增默认安静模式、显式诊断模式、段末计数、相同 Atom/face-index logical shape 的
  重复 upload 零分配及
  CUDA 容器 size/capacity/初始化覆盖。原有空域、迁移、restart、P1/M1 fallback 和通信
  字节解析断言未删除；
- 审计修正（2026-09-21）：`tests/long_nve/run_long_nve.py` 的 candidate 环境补设
  `DMGMD_DOMAIN_DIAGNOSTICS=1`（reference 环境清除该变量）。m2a stage 验证解析 per-rank
  step-0 `DMGMD_DOMAIN_LAYOUT` 记录，而该记录只在诊断开启时输出；此前的 smoke 复验只跑
  1 rank（全部 m1-fallback）未暴露该缺口，nightly 的 2/4-rank m2a stage 会因此失败。
  `tests/long_nve/test_long_nve.py` 新增环境合同单测。

修改后已实际执行（均先 `source ../env/md-mpi.sh`；下表为 2026-09-21 在最终源码重建后
的复验结果，覆盖了最后一次 `neighbor.cu` find_cell_list 容量复用修改之后的状态）：

| 命令 | 当前结果 |
| --- | --- |
| `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j` | PASS |
| `ctest --test-dir build --output-on-failure` | 6 passed / 0 failed（含 `dmgmd.domain_neighbor_cuda` 与 `dmgmd.long_nve_analysis` 23 项单测） |
| `python3 -m py_compile tests/mpi/run_mpi_domain.py tests/mpi/run_mpi_migration.py tests/mpi/run_mpi_differential.py tests/long_nve/run_long_nve.py tests/long_nve/test_long_nve.py` | PASS |
| `python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS（Open MPI 5.0.10、UCX 1.22.0、cuda_copy/cuda_ipc、4-rank CudaAware probe） |
| `./build/tests/dmgmd_domain_neighbor_cuda_tests` | PASS（容量复用/初始化/计数 + 非零 center ELL） |
| `python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0` | 4/4 PASS（无容差变更） |
| `python3 tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS：默认安静探针 + 36 组 2/4-rank 双后端诊断矩阵 |
| `python3 tests/mpi/run_mpi_differential.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS：1/2/4 rank × 双后端 |
| `python3 tests/mpi/run_mpi_migration.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS：unsupported/空域/N<P/迁移/restart 全矩阵 |
| `scripts/run_long_nve_profile.sh --profile smoke` | PASS：7/7（1-rank m1-fallback；报告 `dmgmd-smoke-20260921-104047` 与复跑 `dmgmd-smoke-20260921-105403`） |

修改后最终 candidate SHA-256 为
`34d4a6585aa5b2fb2aced46771276a42f7ef0e808a7216af6efa7fb64ed797ab`（含最后一次
`neighbor.cu` find_cell_list 容量复用修改；早一轮记录的
`9202afe655c88246c73bc1e2462c028592ce04abd82848626eac4593228ec7b4` 对应修改该文件之前
的源码，其矩阵结果已被上表在同一最终源码上的复验取代）。4-rank 诊断矩阵显示：静态
lattice/chain/empty/NEP 变体的 run 段 `layout_uploads_max=0`、
`capacity_growth_events_max=0`；crossings 的 8 次 layout upload 只有 3 次
capacity-growth event（`gpu_allocations_max=25`），resume 的 6 次 upload 只有 3 次 growth
（`gpu_allocations_max=34`）。`gpu_allocations` 还包含段首 Neighbor reference、overflow
flag 与 migration flag 等一次性 `GPU_Vector` 分配，所以静态段为 5–7 而不是 0；layout 专属
是否扩容以 `capacity_growth_events` 和详细 layout 的 `gpu_allocations` 为准。

第一阶段据此完成实现与验收。本节不把 2026-09-18 correctness nightly 计时或本轮 smoke
wall time 当作无 I/O 性能收益结论；第二阶段的正式拆分见本文前述摘要与
[成本报告](./stage2-cost-report.md)。

## 已实现能力（M2a 增量）

- potential 只解析一次：deferred-workspace NEP 构造 + `allocate_workspace`
  （M1 fallback 用 global N、M2a 用 local_count 分配，不保留 global-N GPU scratch）；
- `include/dmgmd/domain_layout.hpp`（纯 CPU、无 MPI/CUDA）：typewise
  `R_force/R_dep -> d_dep/d_coord` 推导（radial-first filter、angular list、
  ZBL 消费 angular list 的语义忠实保留；非有限 cutoff fail closed）、eligibility
  与 fallback reason、local layout（owned | dependency ghosts | coordinate-only
  ghosts，`(face, source, gid)` 排序）、MIC 带发送列表（含 P=2 同 peer 去重）、
  迁移路由（一步跨任意多 slab）、malformed plan 拒绝；
- `MpiRuntime` p2p：`MPI_Isend/Irecv/Waitall` 双后端（左右独立 tag/buffer，
  P=2 同 peer 安全；零 count 合法）、CudaAware 新增真实 device-buffer p2p
  Send/Recv 自检（失败回退 HostStaged）、HostStaged membership/count p2p、
  Alltoall/Alltoallv/Allgather 计数交换、owned-prefix gather（带 gid）与
  correct_velocity scatter；
- `src/domain_runtime.cu`：M2a 每步顺序（correct_velocity -> adaptive dt ->
  VV1 -> wrap -> direct migration -> halo refresh/rebuild -> 全局 rebuild OR ->
  域分片 NEP -> VV2 -> thermo -> thermostat -> 输出）、neighbor.out 经 MPI_MAX
  仅 rank 0 写单记录、多段 run 延续 domain state；
- `gpumd_compat` 参数化：`Potential::ND1/ND2`、`Neighbor` 中心/候选域分离 +
  global-ID 行排序 + 行容量守卫 + `invalidate_rebuild_reference`、gid 键
  many-body 反向边二分、`NEP::compute_domain`（`[0, owned)` 力中心、
  `[0, owned+dep)` 依赖中心、`[0, local_count)` 候选、空区间 launch 前短路）。

## 已记录的验证结果（2026-09-18，M2a 验收与 nightly）

环境：`source ../env/md-mpi.sh`（Open MPI 5.0.10 + UCX 1.22.0 + CUDA），
4× RTX 4090（`nvidia-smi` UUID GPU-a4cdc2a3… / GPU-411245be…），
候选 `./build/dmg-md`（本工作树 Release 构建，`cmake -S . -B build
-DCMAKE_BUILD_TYPE=Release && cmake --build build -j`）。

执行与最终结果（无放宽容差）：

| 命令 | 结果 |
| --- | --- |
| `ctest --test-dir build --output-on-failure` | 5 passed + 1 skipped（沙箱无 GPU；含 `dmgmd.domain_layout`） |
| `./build/tests/dmgmd_domain_neighbor_cuda_tests`（沙箱外） | PASS（非零 `center_begin` ELL 行寻址） |
| `python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS（含 `cuda_aware_p2p_self_test=passed` 新门） |
| `python3 tests/mpi/run_mpi_differential.py --candidate ./build/dmg-md --devices 0,1,2,3` | 1/2/4 rank × HostStaged/CudaAware 六组全 PASS；24 Å fixture 断言 `mode=m1-fallback` |
| `python3 tests/mpi/run_mpi_migration.py --candidate ./build/dmg-md --devices 0,1,2,3` | 全矩阵 PASS（含 unsupported-box 门、字节精确匹配）；断言 `mode=m1-fallback` |
| `python3 tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --devices 0,1,2,3` | 9 cases × HostStaged/CudaAware × 2/4 rank 共 36 组全 PASS |
| `scripts/run_long_nve_nightly.sh` | 手动执行并 PASS；7 cases × seed 0 × 1/2/4 rank × HostStaged/CudaAware 共 42 个配置全通过，其中 M2a 2/4 rank 共 28 个配置 |
| `python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0` | 4/4 committed single-rank golden PASS |

`run_mpi_domain.py` 的覆盖（`tests/mpi/run_mpi_domain.py` 为第一事实源）：

- 64×24×24 大盒（nep_C.txt：rc_radial=7、rc_angular=4 ⇒ d_dep=8、d_coord=16；
  P=2 slab 32 Å、P=4 slab 16 Å），同输入 P=1 为 oracle；
- 断言 `DMGMD_DOMAIN mode=m2a`（fallback 不算覆盖）、三原子两跳链（k 距
  rank0 slab 8.5 Å ∈ (d_dep, d_coord]，证明 coordinate-only ghost 闭包）；
- 两原子静止用例令 P=4 rank 2 连续 1000 步保持逻辑 `local_count=0`；force call
  0/1000 的 `neighbor.out` 与 P=1 逐字节一致，第 1000 步额外两个 MPI_MAX 的
  collective 次数及 input/output `16P` 字节精确匹配；
- NEP5、mixed typewise cutoff、flexible ZBL、typewise ZBL 两步 large-box 用例在
  2/4 rank × HostStaged/CudaAware 下对 P=1 oracle 全通过；
- 迁移 transitions 与逐步 dump 坐标重算完全一致（内部相邻、周期首尾、一步跨
  多 slab、暂时空 slab、N<P）；restart 跨 2/4 rank 恢复；
- per-atom energy/force/virial、thermo、短轨迹对 P=1 oracle 在 committed 容差内
  （实测 576 原子 × 10 帧全部原子行**逐字节一致**，仅 dump_xyz 帧头
  energy/virial/stress 求和存在 R16 归约顺序噪声，最大 1.7e-18 量级）；
- 每步通信记录逐字段精确匹配模型：普通步 collective_calls=3、mpi_in/out=80P，
  无任何 N-scaled collective；per-rank `DMGMD_DOMAIN_COMM` 的
  halo/migration/control 字节与 `DMGMD_DOMAIN_LAYOUT` 推导值精确相等
  （24 B/步/原子刷新 + 40 B membership/重建步 + 4 B/face count + 迁移记录
  80/104 B/原子）。

大盒 M2a smoke/profile（576 原子、500 步、4 rank HostStaged）：
`DMGMD_TIMING phase=run steps=500 atoms=576 ranks=4 seconds_max=0.733
global_atom_steps_per_second=392782`；325 个 layout epoch（迁移+skin 重建），
`DMGMD_DOMAIN mode=m2a axis=x d_dep=8 d_coord=16 slab_width=16`。

M2a nightly（2026-09-18 手动执行）使用 profile 专属长轴大盒，执行 NVE/NVT、短程
严格比较、构型回放与跨 rank restart；7 个 case 的 42 个配置全部通过，M2a 的
2/4-rank HostStaged/CudaAware 配置 28/28 全部命中 `mode=m2a` 并通过验证。结果报告为
`dmgmd-nightly-20260918-171122/report.json`，candidate SHA-256 为
`07c8f0a0d4b9305cfd3f506ad7172960dbe2b72f02edf5589b73eae4fb2506dc`。

CPU 单测（`tests/domain_layout_tests.cpp`，`ctest -R dmgmd.domain_layout`）：
typewise 半径、eligibility/fallback reason、slab 边界（s=0/s=1/恰在边界）、
mixed-cutoff float 舍入上界、P=2 同 peer 去重、真正 `local_count=0`、空 rank/N<P、
确定性槽位序、malformed exchange/migration plan 拒绝、一步跨多 slab 路由、membership
记录布局。独立 CUDA 单测覆盖非零 `center_begin` 的 ELL 行选择与 global-ID 排序。

## 已知缺口

- M2a profile 专属大盒 nightly 已于 2026-09-18 手动执行并通过（7 个 case、42 个配置，
  其中 M2a 2/4 rank 共 28 个配置）；100000-step release 矩阵仍待执行，compatibility
  变体按合同只执行静态/短轨迹；
- R28 多节点 rank I/O 隔离整改尚未形成带最终提交 revision 的验证记录，严格双
  物理（互不可见 TMPDIR）节点验收也尚未执行，见
  [multi-node-io.md](../plans/multi-node-io.md)；
- M2b（Fp/partial 分阶段交换）与 M3（3D 分解、triclinic/非周期 local-domain、
  多节点 rank-slab 布局、scaling 基准）仍是计划；
- M2a 的 debug 构建不变量断言（risk-and-backlog §5 全表）未实现为每步 assert，
  由 CPU 单测 + layout 记录 + 精确字节模型间接覆盖；
- M2a device 数据面为 local，但每 rank 仍保留完整 `HostAtoms` identity/输出元数据，
  因此 host 内存仍为 O(NP)；后续大 N 输出元数据分片前不宣称端到端内存 scaling；
- malformed potential corpus、若干 cutoff/ZBL 边界语义仍待验证，见
  [风险与待办](../plans/risk-and-backlog.md)；
- replicated/M2a 阶段均不发布多卡 speedup 或 scaling 结论。

## 最近变更

2026-09-21 第一阶段日志降噪与 GPU capacity 复用已实施并通过上述验收：默认关闭 M2a
逐次 layout/migration 与 flush，通信日志默认低频；测试通过环境变量显式恢复逐步诊断；
Atom/NEP/Neighbor 使用独立 logical size/capacity，布局与数值合同未变。同日完成收尾审计：
在最后一次 `neighbor.cu` 容量复用修改后的最终源码（candidate
`34d4a658…`）上复跑了全部验收矩阵；修正 `run_long_nve.py` candidate 环境缺失
`DMGMD_DOMAIN_DIAGNOSTICS=1` 的缺口（nightly m2a stage 验证依赖 step-0 layout 记录，
smoke 只跑 1 rank 未暴露）。该记录之后的第二阶段已完成，结果见本文前述摘要；M2b 仍未在
本轮开始。

2026-09-18 M2a 已实施并验收（本工作树，未提交）：

- 新增 `include/dmgmd/domain_layout.hpp`（纯 CPU 布局/半径/eligibility/计划，
  带完整 CPU 单测）、`src/domain_runtime.cu`（M2a 运行时）、
  `src/runtime_internal.hpp`（两路径共享 kernel/formatter/RankIoIsolation）、
  `tests/domain_layout_tests.cpp`、`tests/mpi/run_mpi_domain.py`；
- `src/runtime.cu` 重构为模式调度器 + M1 fallback（行为不变，经 differential/
  migration 全矩阵回归验证）；`src/mpi_runtime.cu` 扩展 p2p/类别记账/自检；
  `src/gpumd_compat/{potential,neighbor,nep}.{cu,cuh}` 增加 ND 域、gid 排序、
  deferred workspace 与 `compute_domain`（legacy 路径与数值未改动）；
- `tests/mpi/check_environment.py` 增加 p2p 自检门；
  `run_mpi_differential.py`/`run_mpi_migration.py` 增加 `mode=m1-fallback` 断言。

同日审计整改：domain compute/rebuild 显式接收逻辑 `local_count` 并与非零 allocation
capacity 分离；修正非零中心 ELL row offset；`neighbor.out` 改为在本次 typewise 表生成
后采样并记账周期 MPI_MAX；typewise 半径复现 float pair-average 舍入并保守取上界；验收
矩阵加入真正空域 1000-step 与 NEP5/typewise/ZBL 大盒用例。

2026-09-15 M0（删除每步 velocity Allgatherv）已实施并验收（详见 Git 历史与
2026-09-15 的记录方式；基线二进制 A/B 逐字节一致）。M1（`10903db`）见 Git 历史。
