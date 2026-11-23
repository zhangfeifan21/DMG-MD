# 多节点 rank I/O 隔离验收计划

状态：PROPOSED，暂缓。

维护者于 2026-10-08 暂缓双物理节点验收。2026-10-09 曾将固定 MPI CPU 绑定、LAMMPS 对照和通信计算重叠列为 P0；当前排期以[风险与待办](risk-and-backlog.md#当前开发优先级)为准。

已有实现规则见 [MPI 运行时](../standards/mpi-runtime.md)的“I/O 与 NEP_MULTIGPU”一节。本文列出尚未完成的双节点验证，R28 在以下关闭条件全部满足前保持打开。

## 待完成验证

1. 在至少 2 个物理节点启动至少 2 个 MPI rank。各节点使用路径字符串相同、内容彼此不可见的本地 `TMPDIR`。使用 [`tests/mpi/run_rank_io_isolation.py`](../../tests/mpi/run_rank_io_isolation.py)，传入的设备数量至少等于 rank 数。站点 host、mapping 等参数通过重复的 `--mpiexec-arg` 传入，参数自身以 `--` 开头时写成 `--mpiexec-arg=--host`。
2. 健康作业完成计算，用户作业目录只出现 rank 0 的兼容输出。测试包装器的逐 rank 状态和作业后的逐节点 probe 均报告零 scratch 泄漏。
3. 分别注入 mkdir、neighbor.out、chdir、restore 和 cleanup 故障。至少 3 个 rank 时，还需同时让一个 rank 的 setup 失败、另一 rank 的 setup-restore 失败。进程可能仍在 scratch 中工作时，清理不得删除该目录；后续逐节点 probe 须能发现并清理测试残留。
4. setup/restore 故障有界退出，诊断包含 world rank、hostname、目标路径和原因。cleanup 故障只报警；保留的目录权限为 0700，`neighbor.out` 为普通文件。
5. 同一 revision 继续通过 CTest，以及 1/2/4 rank × HostStaged/CudaAware 的数值差分，确认 I/O 隔离未改变数值或兼容输出。

## 关闭条件

先在 `docs/status/validation/` 记录执行日期、revision/dirty 身份、双节点环境、精确命令和结果，并从当前状态页及索引链接。然后将[风险登记](risk-and-backlog.md)中的 R28 关闭。

全部条件满足后删除本计划，最终行为保留在标准中，实测证据保留在状态记录中。Git 历史保存实施和审批过程。
