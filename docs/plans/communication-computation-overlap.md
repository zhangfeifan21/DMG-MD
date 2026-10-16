# 通信与计算重叠

类别：分阶段实施计划。状态：**IN PROGRESS**（O1–O3 完成；O4/O5 未执行）。
优先级：P0。更新日期：2026-10-16。

目标是只在 M2a 普通步将 halo 与严格独立的 interior NEP 计算重叠。当前生产仍为
begin→立即 finish→NEP；非阻塞 API 本身不构成 overlap。

## 已完成基础

| 阶段 | 交付与事实源 |
| --- | --- |
| O0 依赖证明 | 已由 O2 落地；集合、唯一写入、缓存和反例见[分区合同](../standards/domain-partition.md)及[kernel 证据](../reference/kernel-inventory.md) |
| O1 串行基线 | [普通/重建时间线、C1 与固定资源窄矩阵](../status/performance/overlap-baseline-20261012.md) |
| O2 串行分区 | [192 组数值矩阵、oracle 与约 29–30% 本机额外成本](../status/validation/overlap-partition-20261015.md)；默认 off |
| O3 halo 生命周期 | [单槽位、收发独立完成、物理 unpack 与错误合同](../standards/halo-lifecycle.md)；[68 作业验收与串行成本](../status/validation/halo-lifecycle-20261015.md) |

O2 方向于 2026-10-15 获维护者确认，O3 随后单独授权；这不自动授权 O4、M2b、半径或数学修改。
已完成阶段的旧接口草案、逐轮提示词与审计过程由 Git 保存，现行合同不在计划重复维护。

## O4：普通步实际 overlap

在默认关闭的实验开关下，扩展当前只接受 halo-ready 的 force 接口，使以下日程有明确状态证明：

```text
VV1/wrap + 全局 confirmed_reuse
→ begin halo（pack/发送源就绪后 post）
→ clear 输出一次；early(E producers, I final force)
→ finish halo / unpack 完成
→ late(H producers, B final force)
→ force 完成 → VV2 / thermo / output
```

I/E/H/B 沿用分区标准的完整旧 Verlet 图闭包，不用几何单 cutoff 或本步 R/A 扩大 interior。
E 可含最终 boundary 的辅助中心；late 不得重算其 descriptor PE 或清掉 early 结果。
重建/迁移、段首 force、空 interior、P1/M1 与未证明模型保持同步 full 路径，记录原因。

首轮依赖约束：

- owned position 在 VV1/wrap 后只读，pack 写独立 send；early 不读 ghost，unpack 只写 ghost。
- MPI 完成前不复用 send/recv，物理 unpack 完成前不消费 ghost，early 完成前不执行 late。
  mask、Verlet、map、workspace/view 与 step token 在整个 force 生命周期冻结。
- CudaAware input device sync 在 early 提交前完成；output sync 不得在无可见性证明时删除。
  HostStaged 的 D2H/H2D 事件沿用 O3。默认 stream 排序对交叠程度的限制须实测。
- MPI 仍由初始化线程 FUNNELED 调用。比较有限的 Waitall/Testall 调度，记录轮询次数与 CPU
  成本；不增加 progress thread、不假设后台进度。若引入新 stream，另列 event wait 与读写证明。
- 实验开关须由 runner 显式白名单传递、保存 launch_environment，并由程序实际模式核验；
  不依赖父 shell export。request 出错按 O3 终止作业，不做不对称 collective 回退。

验收保留未分区 full 与 split-serial oracle，覆盖 P2/4、两后端、P2 同 peer、两跳/skin/周期
链、E\I、空 rank/local/interior、重建/迁移、多 run、NEP5/typewise/ZBL、修速/NVT/输出/restart
及 force call 0/1000。核对逐原子 energy/force/9 virial、有效中间量、字节和唯一写入，
再执行 baseline、MPI 与适用 long_nve；不改 golden 或放宽容差。延迟/progress 调度须实际
触发，不能用全 fallback 代替启用路径。环境门槛按 AGENTS 和测试 README 顺序执行。

trace 至少给出 rank/step/key、owned-ready、pack-ready、post、send/recv completion、
unpack-done、early/late、force-done，并明确 host 时钟与 device event 如何关联。
需可复核交叠证据；没有收益也如实交付，不默认启用。

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
O4 前先准备具体接口/依赖与 golden 方案，按 AGENTS 的适用边界确认方向；不重复申请已获
O2/O3 授权。多 GPU 正式实验串行调度，保存 PASS/FAIL/PARTIAL 与未跑项。
