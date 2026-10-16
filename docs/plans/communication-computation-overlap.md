# 通信与计算重叠

类别：分阶段实施计划。状态：**IN PROGRESS**（O1–O4 完成；O5 未执行）。
优先级：P0。更新日期：2026-10-16。

目标是只在 M2a 普通步将 halo 与严格独立的 interior NEP 计算重叠。默认生产仍为 begin→立即 finish→NEP；O4 已接入独立 early，
验收状态见[报告](../status/validation/overlap-o4-20261016.md)。非阻塞 API 本身不构成 overlap。

## 已完成基础

| 阶段 | 交付与事实源 |
| --- | --- |
| O0 依赖证明 | 已由 O2 落地；集合、唯一写入、缓存和反例见[分区合同](../standards/domain-partition.md)及[kernel 证据](../reference/kernel-inventory.md) |
| O1 串行基线 | [普通/重建时间线、C1 与固定资源窄矩阵](../status/performance/overlap-baseline-20261012.md) |
| O2 串行分区 | [192 组数值矩阵、oracle 与约 29–30% 本机额外成本](../status/validation/overlap-partition-20261015.md)；默认 off |
| O3 halo 生命周期 | [单槽位、收发独立完成、物理 unpack 与错误合同](../standards/halo-lifecycle.md)；[68 作业验收与串行成本](../status/validation/halo-lifecycle-20261015.md) |

O2 方向于 2026-10-15 获维护者确认，O3 随后单独授权；维护者于 2026-10-16 另行明确授权 O4 的接口、实现、测试和文档；不含 M2b、半径或数学修改。
已完成阶段的旧接口草案、逐轮提示词与审计过程由 Git 保存，现行合同不在计划重复维护。

## O4：已完成

2026-10-16 在本轮授权范围内完成默认关闭的普通步实际 overlap：prepare→begin→early
→finish→late；wait 与最多 8 次 progress 的 poll 均已验证。现行接口/缓存/顺序见
[分区标准](../standards/domain-partition.md)和[halo 标准](../standards/halo-lifecycle.md)，
正确性、受控延迟、正常路径 profiler 与具体身份见
[O4 验收](../status/validation/overlap-o4-20261016.md)。
固定资源小样本表明仍慢于 full，保持默认关闭；本次窄成本检查见
[实测](../status/performance/communication-computation-overlap-results-20261016.md)，不等于 O5。
已完成实施步骤迁入标准与状态，以下只保留 O5。

## O5：成本归因与采用决策

先通过 O4 正确性，再复用 benchmark/C1 做 pilot 和正式 A/B：

- 同输入/seed/binary、GPU UUID、K 物理核/rank、NUMA、warmup/steps，随机化组序。
  O1 已用手工映射+C1 核验；O5 扩展矩阵复用[统一绑定入口](mpi-cpu-binding.md)。
- 至少 carbon_200k/carbon_1m/water_400k × P1/2/4/8 × 两后端，至少五次重复；
  报中位数、离散度与预先确定的区间估计，样本不足就说明不确定或增加重复。
- 四组归因：O1 原串行、O2 分区串行、O3 立即 finish、O4 overlap。跨二进制比较须锁定
  源码/编译身份；同二进制 off/serial 不等于无 selection guard 的旧版本。
- 主结果用关闭详细 profiler 的 run 墙钟；诊断另报分类/额外 launch、pack/staging/unpack、
  等待、interior 比例、通信字节与 CPU-s/core-equivalent。P1/M1 与 M2a 分开。
- 单节点不外推跨节点；空/小 interior、薄 slab、重建占比高和退化场景全部保留。
  CPU C2/C3 按需校准扰动；LAMMPS 是外部参照，不是内部 overlap 数值 oracle。

定义 `speedup=T_serial/T_overlap`；强扩展 `S(P)=T(1)/T(P)`、`E(P)=S(P)/P`。
缺单卡基线则不报后两者。对同 rank/同 step/同一时间轴上的请求在途区间并集 C 与
独立 GPU 计算区间并集 I，报 `O=duration(C∩I)`、`R=O/min(duration(C),duration(I))`，
分母为零记 N/A。这只证明请求与计算交叠，网络实际传输还需 progress/transport 证据。
不把包围盒当区间并集，不跨 rank 相加当关键路径隐藏时间。

host enqueue、MPI call wall、CUDA event 为不同口径；未校准时钟不能造精确 GPU/CPU 时间线。
`min(T_comm,T_interior)/(T_comm+T_interior)` 只是两阶段理想节省比例，不是整步加速上界。

交付 `docs/status/performance/communication-computation-overlap-results-YYYYMMDD.md`、原始
trial/trace/CPU 数据、revision/dirty patch、binary/input hashes 和重算命令；提出保留实验、
补实验或默认策略建议。阈值须独立验证，正确性通过不自动证明通用收益或方法新颖性。

## 范围与推进边界

首轮不做 M2b、3D、halo 半径变化、NEP 数学重写、progress thread 或跨步流水。
O4 接口/依赖与 golden 方案已完成并验收；后续突破边界仍按 AGENTS 的适用规则确认方向。多 GPU 正式实验串行调度，保存 PASS/FAIL/PARTIAL 与未跑项。
