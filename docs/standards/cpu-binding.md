# 单节点 MPI CPU/NUMA 启动与核验

单节点作业可以指定每个 MPI rank 使用的物理核心数，以及如何在 NUMA 节点之间分配。启动工具先根据可用资源生成分配方案，用 Open MPI 启动，再独立核对实际 CPU 和 GPU 绑定。

直接启动用 `scripts/launch_dmgmd.py`。benchmark 提供同一套 CPU 参数。两处均调用 `scripts/cpu_binding.py`，沿用原 MD 二进制、计算、通信和 `run.in`。

## 策略

`--cpu-cores-per-rank K` 为每 rank 恰好 K 个物理核心，默认不共享。SMT 兄弟属于同一
物理核心；当前 core 绑定要求该核心的完整兄弟集合都在可用 cpuset/affinity 内，不能
满足时拒绝，不向 allocation 外扩张。

- `--cpu-placement core`：按可用 hwloc Core 逻辑顺序分配。
- `--cpu-placement numa`：每 rank 留在一个 NUMA；默认按节点轮转，可用
  `--cpu-numa-nodes 0,0,0,0,1,1,1,1` 明确 rank 顺序。长度可以小于 P，按周期重复。
- `--cpu-placement rankfile --cpu-rankfile FILE`：本机 Open MPI 格式
  `rank N=localhost slot=0,1,2,3`，slot 是 **hwloc 全局逻辑 Core 编号**，不是 OS CPU ID。
  只接受 localhost、本机 hostname 或 +n0；拒绝跨节点、socket:core 简写、缺/重复 rank、
  重复/不可用核心和共享核心。若同时指定 NUMA，必须一致。

工具读取 libhwloc XML 的 Core/PU 顺序，把逻辑 Core 编号换成 OS CPU 集合，并从 sysfs 保存 core、socket 和 NUMA 的关系。Open MPI 按 rankfile 启动。每个 rank 内的 wrapper 检查实际绑定。

本机 PMIx 的 `pmix_check_cli_option` 会把参数值内的连字符也当成分隔符。启动工具用 `/tmp/dmgmd_binding_*/rankfile` 作为短路径别名，保存实际路径、原内容和哈希。首次探测的失败留在 O5 产物里。

启用固定策略时，拒绝另一个 map/bind/rankfile/CPU-list/oversubscribe/rank-order、host、
rank 数、工作目录或相关 MCA 覆盖，包括继承的策略变量。普通 `--mpiexec-arg` 仍可传
与绑定无关的参数；不设置 K 时保留旧入口。K 必须为正数。

工具只在 CPU 亲和性（affinity）、在线 CPU 和可见 cgroup cpuset 的交集内分配核心。可见祖先中最小的 quota 必须 ≥P×K。controller 未委派且文件不存在时，沿用祖先限制。文件存在但不可读时，工具拒绝启动。

调度器须给作业分配单个节点，并明确限制可用 CPU。声明的预算不足或分配范围不明时，工具拒绝启动。隐藏祖先的限制以及共享 quota 是否独占仍为 `UNKNOWN`。

## 核验与证据

每个 rank 在 `exec` 前登记 PID、启动计数、rank/local rank、namespace 和内存策略原始输出。切换到候选程序后，独立观察器核对程序路径与 SHA-256，每 0.2 s 记录所有可见线程（task）的 affinity。每次记录都须与解析出的绑定方案完全一致。观测覆盖初始化、运行和退出期间，采样间隔内发生的短暂更改可能看不到。

即使关闭 C1 CPU 采样，启动器仍执行上述核验。候选进程短到在 `exec` 后少于两次观测时，绑定不计为通过。工具检查实际 `DMGMD_MPI` 记录的 rank、size、host 和 GPU UUID 是否完整，rank 与 UUID 是否唯一。O5 进一步按 rank 核对 UUID 的顺序。

另每 2 s 保存可见祖先的 quota/cpuset 与 cpu.stat 原文，资源限制变化使该 trial 无效。
当前单机验收要求 rank 与解析计划处于同一 cgroup；不同的 scheduler step cgroup 尚未
验收，会明确拒绝。共享 cgroup 的 cpu.stat 不用作该 trial 的正式段 CPU。

保存 resolved.json、hwloc XML、cgroup/scheduler、rankfile 内容/哈希/启动别名、精确命令、
注册、observations.jsonl、resources.jsonl 和 verification.json。benchmark 还保存 GPU PCI/NUMA 拓扑与遥测。
`numactl --show` 是策略观测；CPU affinity 不证明内存页已绑定或本地驻留，页驻留 UNKNOWN。

## 当前边界

当前范围为原生 Linux、单节点、相同 namespace 和 Open MPI 5，支持 DMG-MD 直接启动及 benchmark。其他 MPI 程序可以复用绑定方案生成和观察工具。LAMMPS 接入尚未验收，GPUMD 的合计 P×K CPU 集合入口仍待实现，benchmark 会拒绝对 GPUMD 的固定绑定请求。三引擎正式矩阵、跨节点、其他调度器和内存页放置另行验收。[跨引擎绑定计划](../plans/mpi-cpu-binding.md)

Open MPI 的逻辑 Core rankfile 语义以[5.0.10 官方手册](https://docs.open-mpi.org/en/v5.0.10/man-openmpi/man1/mpirun.1.html#rankfiles)为依据，
实际映射以 `/proc` 与 sysfs 的证据为准。
