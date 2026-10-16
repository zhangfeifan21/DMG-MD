# 域分解后续扩展

类别：后续计划。状态：**PROPOSED**。更新日期：2026-10-16。

M0、M1、M2a 已实施；双路径、两跳 halo、迁移、缓存与字节合同分别由
[MPI runtime](../standards/mpi-runtime.md)、[布局](../standards/data-layout.md)和
[缓存有效性](../standards/cache-validity.md)定义。近期 P0 为 CPU 绑定、LAMMPS 对照
和 O4 overlap，见[待办](risk-and-backlog.md)。

## M2b：分阶段中间场交换

目标是减少两跳坐标带和 dependency-center 冗余计算。候选流程：

```text
一跳 position/type halo → owner descriptor
→ forward Fp → directed angular partial
→ forward 所需反向 f12/边数据 → owned force/virial
```

另一候选为发送 Fp + angular sums，在接收端重算 partial。MatPL 式 ghost 力回传是另一种
force assembly，须单独比较，不能与中间场 forward exchange 混为一个协议。

实施前必须明确：

- producer/consumer 中心域、local stride、所有权和完成事件；数学证据见[kernel 清单](../reference/kernel-inventory.md)。
- directed edge 稳定键 `(center_gid, neighbor_gid, periodic_image)`；local row ordinal
  不能跨 rank/重建持久化，单-slot ghost/MIC 表示不能直接推广到多 image。
- payload、staging、request、workspace 生命周期、缓存失效和错误退出。
- Newton scatter 必须定义 reverse force/virial exchange；能量和 virial 的 owner 唯一。

带宽须按实际 descriptor dim、边数与布局计量：Fp 为 `4*dim` B/center，partial 为每条
有向边三个 float。省掉深层坐标不保证总字节减少。均匀密度且两侧带不重叠、无需去重时，
两跳 slab 每 rank 接收位置量近似为 `24 * 2ρA*d_coord` B/step；实际值以 face plan 为准。

验收以 M2a 为 oracle：同快照比较逐原子 energy/force/全部 virial、邻居/反边及精确字节；
覆盖两跳链、多元素/NEP5/ZBL、P2 同 peer、周期、空 rank、迁移、多 run/restart，再执行
baseline、MPI 与 long_nve。先做同步版本，随后另评估 overlap；M2b/force assembly 方向待确认。

## 多节点硬化

- 节点内连续 slab/rank 放置须实测；周期 slab 环含首尾跨节点边，不能按非周期链计数。
- [双物理节点 I/O 验收](multi-node-io.md)暂缓；恢复后验证本地 TMPDIR、故障与清理。
- 补跨节点 restart、自检及强/弱扩展，保留网络、拓扑、CPU/GPU 资源与重复分布。
- [2026-09-26 单机 benchmark](../status/performance/benchmark-multigpu-results-20260926.md)
  已有结果；单机实测不等于跨节点效率。

## 更远扩展

3D 分解、负载均衡、triclinic/非周期 local-domain、small-box 多 image 和更多 ensemble
未实施。每项先给几何/halo/所有权证明、反例和 golden 设计，再确定方向。
P=1、M1 fallback、rank-0 兼容输出与原容差始终保留。
