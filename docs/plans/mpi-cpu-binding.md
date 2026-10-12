# MPI 启动 CPU 核数与 NUMA 绑定计划

类别：待实施计划。状态：**PROPOSED**。优先级：**P0**（2026-10-09 调整）。

## 目标与当前边界

导师要求在启动时固定每个 MPI rank 可使用的 CPU 核数，使不同 GPU/rank 数的 benchmark
有相同、可核验的 CPU 资源条件，并能在多 NUMA 节点上指定 rank 的位置。保持一 MPI rank
对应一张 GPU；CPU 绑定是启动配置，不改变 NEP、域分解、MPI 通信协议或 `run.in` 语法。

当前 `tests/benchmark/run_benchmark.py` 只有重复的 `--mpiexec-arg` 透传。它可以把
Open MPI 的 `--map-by`、`--bind-to` 传给 DMG-MD，但没有一等的 CPU 核数/NUMA
参数、配置冲突检查、实际 affinity 验证或跨引擎一致性保证。GPUMD 目前直接启动，
没有应用同一资源策略的入口。因此下述接口与验收均**尚未实现**；C1 CPU 采样器已能
记录 rank 的实际 affinity，可作为后续核验工具，不能替代绑定。

## 拟增加的启动接口

- `--cpu-cores-per-rank K`：必填的正整数（启用固定绑定时），语义为每个 MPI rank
  恰好可运行于 K 个**物理核心**。SMT 兄弟逻辑 CPU 是同一核心，不得将两个线程误报为两核。
  不设置此选项保持旧启动行为；正式新 benchmark 必须显式设置。
- `--cpu-placement core|numa|rankfile`：`core` 在可用资源中分配 K 核/rank；`numa`
  将每 rank 的 K 核限制在一个 NUMA 节点，按节点分布 rank；`rankfile` 用显式
  `--cpu-rankfile FILE` 指定每个 rank 的位置与核集合。显式文件用于 CPU/GPU 拓扑不对称
  或需要固定 rank→NUMA→GPU 映射的机器。具体 CLI 名称在实现时可调整，但三个能力、
  错误行为和结果元数据须保持。
- Open MPI 5 的自动模式基于 `--map-by core:PE=K` 或 `numa:PE=K` 与
  `--bind-to core`；显式模式使用 `--map-by rankfile:file=FILE`。rankfile 使用 hwloc
  逻辑核心编号，必须在运行前解析并映射到操作系统 CPU ID；不能把 rankfile 数字直接
  当 `/proc` 的 CPU ID。只传 `--report-bindings` 是诊断，不是验收。
- 固定绑定模式拒绝与 `--mpiexec-arg` 中另一个 map/bind/rankfile/oversubscribe
  策略冲突的组合，不静默覆盖。无法在 cpuset、cgroup quota、调度器 allocation 内满足
  `P×K` 核或 rankfile 指向不可用核心时，启动前失败并给出原因。

交付面包括 DMG-MD 的常规启动入口（例如 `scripts/launch_dmgmd.py` 或安装后的
`dmg-md-launch`，负责构造并执行 `mpiexec`）和 `run_benchmark.py` 的一等参数；
两者必须复用同一策略生成/验证逻辑。这个入口在 MPI 创建 rank 前设置位置，
`dmg-md` 物理计算二进制不解析 CPU 拓扑参数，`run.in` 也不增加命令。
同一资源策略须可用于新增的
LAMMPS MPI benchmark 与 GPUMD 单进程对照（P 张 GPU 时 GPUMD 进程使用与 MPI 作业
合计 P×K 核相同的 CPU 集合，不假装它有 MPI rank）。保留文档化的原生 `mpiexec`
参数供已有脚本使用；不要在
已经启动的 rank 内偷偷改变全局绑定。所有启动模式都保留原有一 rank 一 GPU UUID 检查。

## 验收与结果合同

1. 在 1/2/4/8 rank 中固定 K，不把 `P` 增大误当成每 rank 自动增加 CPU 额度。
   每 rank 在初始化后和正式段记录 `sched_getaffinity`/`Cpus_allowed_list`、CPU→core→
   socket/NUMA、cgroup cpuset/quota、rank/local rank、GPU UUID；实际独立物理核心数
   必须等于 K。相邻 rank 是否共享核心由策略声明；正式公平对比默认禁止共享。
2. 多 NUMA 节点分别验证同 NUMA 放置和跨 NUMA 分布；记录 GPU PCI/NUMA 邻近性、
   内存策略（例如 `numactl --show`）和 rank→GPU 对应。CPU affinity 不自动保证内存
   也绑定在同一 NUMA，远端内存是否产生由实测判定，不写成已控制。
3. benchmark 的 resolved plan、每 trial 命令、绑定策略、rankfile 内容/哈希、实际
   affinity 与资源限制进入 metadata；`--dry-run` 展示将执行的策略但不宣称已经生效。
   `--cpu-sampling off` 时也必须能验证绑定，不能把采样器作为唯一证据源。
4. 覆盖 cpuset 裁剪、SMT、核心不足、rankfile 重复/缺 rank、NUMA 无足够核心、
   launcher 错误/静默退化。拒绝无效 trial 进入性能汇总；固定 CPU 配置下重跑既有
   MPI/CUDA 环境门槛和科学正确性门槛，证明启动变化未改变数值输出。

实施建议先做 Open MPI 参数生成与纯 CPU 拓扑/冲突测试，再做单节点含多 NUMA 的
smoke、pilot 和固定 K 正式矩阵。CPU 使用率采样及 C2/C3 统计继续作为容量/竞争诊断，
优先级低于可复现的绑定入口；其数据不能决定默认增加 worker/progress 线程。

Open MPI 5 的 `PE=n`、NUMA mapping、rankfile 和绑定报告以
[官方 mpiexec 手册](https://docs.open-mpi.org/en/v5.0.10/man-openmpi/man1/mpirun.1.html)
为实现依据；实际 CPU ID、cgroup 限制以目标节点采集结果为准。
