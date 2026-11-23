# 域分解后续扩展

状态：PROPOSED。更新日期：2026-10-21。

单 rank 基线、全量复制模式和局部域分解模式已经实现。两条路径的每步顺序和通信见[MPI 运行时](../standards/mpi-runtime.md)，计数和槽位见[数据布局](../standards/data-layout.md)，复用条件见[缓存有效性](../standards/cache-validity.md)。

O1–O5 的本机阶段及 DMG-MD CPU 绑定已完成。计算重叠实验由[专题计划](communication-computation-overlap.md)维护，跨引擎绑定和 LAMMPS 验收独立管理。[风险与待办](risk-and-backlog.md)记录排期。分阶段中间场交换和三维分解均未确定为紧接着实施的方案。

<a id="m2b-分阶段中间场交换"></a>
## 分阶段中间场交换方案

该候选希望减少两跳坐标范围和依赖副本上的重复计算。一个可能的流程是：

```text
一跳 position/type halo → owner descriptor
→ forward Fp → directed angular partial
→ forward 所需反向 f12/边数据 → owned force/virial
```

另一候选发送 `Fp` 和 angular sums，由接收端重算 partial。MatPL 式的边界副本力回传采用另一种力汇总方式，须单独比较。

实施前需定义以下内容：

- 每阶段的计算中心、数据使用者、本地数组跨度、所有权和完成事件。[内核依赖证据](../reference/kernel-inventory.md)
- 有向边的稳定键 `(center_gid, neighbor_gid, periodic_image)`。本地邻居行下标不能跨 rank 或重建持久保存；单槽位副本与最小镜像表示不能直接扩展成多镜像。
- 通信载荷、主机暂存、MPI request 和工作区的生命周期，以及缓存失效和错误退出。
- 若用 Newton scatter 写入边界副本的部分力，须定义反向力和 virial 交换；每原子的能量与 virial 仍须有唯一所有者。

带宽按实际描述符维度、边数和布局计量。`Fp` 为 `4*dim` B/center，每条有向边的 partial 含三个 float。减少深层坐标不一定减少总字节。在均匀密度、两侧边界带不重叠且无需去重时，两跳条带每 rank 接收位置量近似为 `24 * 2ρA*d_coord` B/step；实际以 face plan 为准。

验收以现有局部域分解为数值参照。同快照比较逐原子能量、力、全部 virial、邻居、反向边和精确字节；覆盖两跳链、多元素、NEP5、ZBL、P2 两面同 peer、周期、空 rank、迁移、多 run 段和 restart，再运行 baseline、MPI 与 long_nve。

先实现同步版本，再单独评估通信重叠。具体的中间场交换和力汇总方向仍待确认。

## 多节点硬化

节点内连续条带和 rank 放置方式须实测。周期条带环包含首尾跨节点边，不能按非周期链估计通信次数。

[双物理节点 I/O 验收](multi-node-io.md)暂缓，恢复后检查本地 `TMPDIR`、故障处理和清理。还需完成跨节点 restart、自检及强弱扩展，保留网络、拓扑、CPU/GPU 资源和重复分布。计时条件见[性能规程](../status/performance/npt-benchmarks.md)。

## 更远扩展

三维分解、负载均衡、三斜或非周期的局部域分解、小盒多镜像及更多系综尚未实现。各项先给出几何、边界和所有权证明、反例及 golden 设计，再确认方向。

后续改动保留单卡数值基线、内部全量复制对照、rank 0 兼容输出和原容差。
