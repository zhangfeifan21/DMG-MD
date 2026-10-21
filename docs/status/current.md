# DMG-MD 当前状态

类别：实现状态索引。核对日期：2026-10-21；O5 起始 HEAD：`6b8efc5`（主工作树原先干净）。

当前已实现 M1/M2a、统一缓存重建、CPU 采样、三引擎 benchmark 编排及 O1–O4。
O4 默认关闭的普通步实际重叠已实现并通过本轮验收；默认仍同步 full。
O4 身份见 [O4 报告](validation/overlap-o4-20261016.md)。
O5 的本机正式实验于 2026-10-21 完成：主矩阵 600、历史归因 150、退化场景 120，
另有独立采用验证 40 个有效 trial；诊断 88 组、Nsight 20 组。默认仍 HostStaged/full、overlap=off。
固定 CudaAware 的精确 water/P4/P8 独立验证净加速约 1.0627×/1.0927×；
本机 HostStaged/full 仍更快。全部范围、CPU 成本、不确定性与重算入口见
[O5 报告](performance/communication-computation-overlap-o5-results-20261021.md)。
中断和失败尝试原样保留，不混入有效统计。

补充成本分析确认：carbon_200k/P2/CudaAware 的 split-serial 比 full 多
4.601 ms/step，wait 回收 0.926 ms/step，仍净慢 3.675 ms/step。
独立诊断将损失定位在 GPU 过滤加 NEP 阶段；分类使用缓存，不能解释这项逐步成本。
完整 grid 加掩码的执行效率是候选原因，具体微架构归因尚未证实。
模式解释、证据与限制见[O5 后续成本分析](performance/overlap-o5-cost-analysis-20261021.md)。

## 已实现能力

| 能力 | 当前行为 / 合同 |
| --- | --- |
| 输入与 MD | GPUMD 格式 model/run/NEP4/5、NEP-ZBL，NVE、`nvt_ber`、兼容输出及跨 rank restart；详见[支持矩阵](../standards/compatibility-matrix.md) |
| M1 replicated-full | P=1 固定使用；P>1 不满足 M2a eligibility 时回退。每 rank 完整 NEP scratch，积分/thermo/输出由 slab owner 唯一负责 |
| M2a local-domain | 正交全周期大盒且 slab 足够宽时，使用 owned/ghost 本地 device 数据、两跳位置 halo、p2p 通信和直接迁移；详见[MPI 合同](../standards/mpi-runtime.md) |
| 缓存与分配 | epoch 内 manager/layout/map 固定；全局连续位移超过 skin/2 才统一重建和迁移；capacity 可复用，host identity 仍每 rank 完整保存 |
| O1 诊断 | 默认关闭的 M2a 时间线、benchmark A/B 编排与 C1 marker 对齐；整段计时不等于 CPU 利用率 |
| O2 串行分区 | `DMGMD_DOMAIN_PARTITION=off|serial|verify`，默认 off；普通步按完整 Verlet 图分区，halo 后串行执行；[合同](../standards/domain-partition.md) |
| O3 halo 生命周期 | begin/progress/finish、单槽位与请求/事件完成检查；默认立即 finish，O4 可插入 early；无新 stream/progress thread；[合同](../standards/halo-lifecycle.md) |
| O4 实验调度 | overlap=off/wait/poll；prepare→begin→early→finish→late，默认 off；本轮 476 组数值矩阵与 profiler 通过 |
| CPU 采样/绑定 | C1 仍只观测；新增共用 core/numa/rankfile 启动策略和独立 affinity audit，C1 关闭也核验；[绑定合同](../standards/cpu-binding.md) |
| 三引擎对照 | 输入、静态比较和 benchmark 编排已实现；DMG-MD 绑定已接线；跨引擎绑定验收与正式效率矩阵未完成 |

P>1 的 triclinic/非周期输入仍拒绝。默认后端为 HostStaged；CudaAware 必须通过 capability
query、device collective 与 p2p 数值自检。支持范围、字节公式和容差分别在标准、测试脚本
及 manifest 维护，本页不复制。

## 验收证据

| 日期 / 实现 | 已记录结果 | 范围与证据 |
| --- | --- | --- |
| 2026-10-21 O5 | 870/870 主/历史/退化 trial；40/40 独立验证；C1 88/88、GPU 映射 384/384、Nsight 20/20；工具 CPU 单测 53/53 | [O5 正式报告](performance/communication-computation-overlap-o5-results-20261021.md)；未改生产二进制或默认；R=N/A、历史小接口成本部分 INCONCLUSIVE |
| 2026-10-16 O4 | domain 七模式 476 组；生命周期 68/68、调度/配置 25/25；golden 4/4、CTest 10/10、long_nve smoke 7/7；24 cost trial、24 profiler rank-step 证据 | [O4](validation/overlap-o4-20261016.md)、[固定资源成本](performance/communication-computation-overlap-results-20261016.md)；candidate SHA-256 `860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6`；仍慢于 full，默认关闭 |
| 2026-10-15 O3 | 生命周期/错误作业 68/68；domain 三模式 192 组；O2/O3 科学文件 808 次字节比较；CTest 10/10；golden 4/4；long_nve smoke 7/7 | [O3](validation/halo-lifecycle-20261015.md)；candidate SHA-256 `d1abb2deb8e54d1552a54c1030a33dc89a12795fe946b890671d93d77989e646` |
| 2026-10-15 O2 | domain 三模式、CPU/GPU oracle、golden、CTest 与 long_nve smoke | [O2](validation/overlap-partition-20261015.md)；本机串行分区额外成本约 29–30%，保持默认关闭 |
| 2026-10-12 O1 | P2 双后端普通/重建时间线、C1 样本、固定资源窄矩阵 | [O1](performance/overlap-baseline-20261012.md)；无 overlap/强扩展结论 |
| 2026-10-09 工具/外部参考 | C1 smoke；GPUMD/LAMMPS 静态 11 case；三引擎单卡及 LAMMPS 双卡 smoke | [C1](validation/cpu-sampling-c1-20261009.md)、[LAMMPS](validation/lammps-reference-20261009.md) |
| 2026-09-26 单机八卡 | 3 算例 × 3 实现 × P1/2/4/8，五次有效重复；DMG-MD 8 卡自身加速 5.17–7.66× | [Benchmark](performance/benchmark-multigpu-results-20260926.md)；另一台服务器，CPU 信息不足，无 LAMMPS |
| 2026-09-22 统一重建 | baseline、M1/M2a、I/O 故障与 smoke 通过；三体系 long 子集通过 | [第二阶段](performance/stage2-cost-report.md)；保留当时 binary/dirty 身份 |

2026-09-18 曾手动通过完整 nightly：7 cases、seed 0、P1/2/4、两后端，共 42 配置，
其中 M2a 28 配置；报告为本机生成物 `dmgmd-nightly-20260918-171122/report.json`，
candidate SHA-256 `07c8f0a0d4b9305cfd3f506ad7172960dbe2b72f02edf5589b73eae4fb2506dc`，
基线 revision `27e07d92b84f0e02d08d99c400c52b795175341f`。此历史结果不代表 O3
已重跑 nightly；O3 最新长程验证范围为 smoke。

## 剩余工作

- P0：[O6–O11 分步性能优化](../plans/communication-computation-overlap.md)：O6 拆分成本诊断，
  O7 合并缓存判定归约，O8 按需 NVE thermo，O9 单一 NEP 执行候选，
  条件性 O10 分消息后端选择，O11 相对新 full 的受控复验与采用决策。均为 PROPOSED，未执行。
- 独立 P0：[跨引擎/调度器绑定剩余验收](../plans/mpi-cpu-binding.md)、
  [三引擎动力学与正式性能矩阵](../plans/lammps-multigpu-benchmark.md)。
  DMG-MD 本机统一启动与独立核验已完成，不再列为未实现。
- 后续：100000-step release、M2b 中间场交换、3D/triclinic/非周期 local-domain 扩展。
- 暂缓：[双物理节点 I/O 验收](../plans/multi-node-io.md)；跨节点 scaling 尚无实测。
- 规模/兼容性缺口见[待办](../plans/risk-and-backlog.md)。host identity 为 O(N)/rank，
  root 输出需全局 gather；全表 debug 每步不变量断言未实现，不宣称端到端内存 scaling。

完整 revision/dirty 身份、环境和命令留在各报告；旧报告中的“下一阶段”表示当时交接，
当前优先级以本页与剩余计划为准。
