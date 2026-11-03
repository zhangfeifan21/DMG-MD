# 单节点 MPI CPU/NUMA 启动与核验

类别：启动工具合同。入口为 `scripts/launch_dmgmd.py` 与 benchmark 的一等 CPU 参数，
共用 `scripts/cpu_binding.py`。不改变 MD 二进制、数学、通信或 `run.in`。

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

工具从 libhwloc XML 的 Core/PU 顺序取得逻辑 Core→OS CPU 集合，并从 sysfs 保存
core/socket/NUMA。实际启动用 Open MPI rankfile；rank 内 wrapper 只检查，不重绑。
本机系统 PMIx 的 `pmix_check_cli_option` 会把参数值内的连字符也当成分隔符，因此
launcher 为保存的 rankfile 创建 `/tmp/dmgmd_binding_*/rankfile` 短路径别名，内容/哈希
不变，并记录实际使用的路径。这不是降级映射；失败探测保留在 O5 产物。

启用固定策略时，拒绝另一个 map/bind/rankfile/CPU-list/oversubscribe/rank-order、host、
rank 数、工作目录或相关 MCA 覆盖，包括继承的策略变量。普通 `--mpiexec-arg` 仍可传
与绑定无关的参数；不设置 K 时保留旧入口。K 必须为正数。

可用资源取当前 affinity、online CPU 与可见 cgroup cpuset 的交集；可见祖先 quota 的
最小值必须 ≥P×K。controller 未委派、文件不存在时继承祖先限制；存在而不可读则拒绝。
调度器须为单节点并有受限 CPU envelope，声明 CPU 预算不足或 allocation 模糊时拒绝。
隐藏祖先与共享 quota 的独占性仍 UNKNOWN，不把容量上限当作保留资源。

## 核验与证据

每 rank 在 exec 前注册 PID/start ticks、rank/local rank、namespace 和内存策略原始输出；
exec 后独立观察器核对候选路径/SHA-256，并每 0.2 s 记录全部可观测 task 的 affinity。
所有观测都必须等于 resolved plan；记录覆盖初始化至运行/退出期间的采样，不声称可排除
两次观测间短暂变化。独立于 C1，CPU sampler=off 仍执行；短命进程少于两个 exec 后
观测不作为绑定验收通过。实际 `DMGMD_MPI` 的 rank/size/host 和 GPU UUID 必须完整且唯一。
O5 另核验 UUID 的逐 rank 顺序，不能只比较 GPU 集合。

另每 2 s 保存可见祖先的 quota/cpuset 与 cpu.stat 原文，资源限制变化使该 trial 无效。
当前单机验收要求 rank 与解析计划处于同一 cgroup；不同的 scheduler step cgroup 尚未
验收，会明确拒绝。共享 cgroup 的 cpu.stat 不用作该 trial 的正式段 CPU。

保存 resolved.json、hwloc XML、cgroup/scheduler、rankfile 内容/哈希/启动别名、精确命令、
注册、observations.jsonl、resources.jsonl 和 verification.json。benchmark 还保存 GPU PCI/NUMA 拓扑与遥测。
`numactl --show` 是策略观测；CPU affinity 不证明内存页已绑定或本地驻留，页驻留 UNKNOWN。

## 当前边界

原生 Linux、单机同 namespace、Open MPI 5；DMG-MD 启动与 benchmark 已接线。纯 CPU
策略测试及实际 GPU 验收范围见 [O5 报告](../status/performance/communication-computation-overlap-results.md#o5-20261021-report)。
策略生成和观察器可以复用到其他 MPI 程序；LAMMPS 接线未在本轮验收，GPUMD 的合计
P×K CPU 集合入口尚未实施，benchmark 对其固定绑定请求明确拒绝。不把这些未验收面
或三引擎正式矩阵写成完成。跨节点、任意调度器和内存页放置另行验收。

Open MPI 的逻辑 Core rankfile 语义以[5.0.10 官方手册](https://docs.open-mpi.org/en/v5.0.10/man-openmpi/man1/mpirun.1.html#rankfiles)为依据，
实际映射以 `/proc` 与 sysfs 的证据为准。
