# 多节点 rank I/O 隔离验收计划

类别：待实施计划。

状态：**IN PROGRESS**。生产合同位于
[replicated-mpi.md](../standards/replicated-mpi.md) 的“I/O 与 NEP_MULTIGPU”一节；本文件只
保留尚未完成的双物理节点验收，不把工作区实现或已定义测试写成已通过结论。R28 在本计划的
关闭条件全部满足前保持打开。

## 待完成验证

1. 在至少 2 个物理节点上启动至少 2 个 MPI rank，并让各节点使用相同路径字符串但彼此不可见
   的本地 `TMPDIR`；通过重复的 `--mpiexec-arg` 传入站点所需 host、mapping 等 launcher 参数。
2. 健康作业必须完成计算，用户作业目录只能出现 rank 0 的兼容输出；测试包装器的逐 rank
   状态和作业后的逐节点 probe 均须报告零 scratch 泄漏。
3. 分别覆盖 mkdir、neighbor.out、chdir、restore、cleanup 故障；有至少 3 个 rank 时，还须在
   一个 rank 的 setup 失败同时向另一 rank 注入 setup-restore 失败，证明进程可能仍以 scratch
   为 cwd 时不会删除该目录，且后续逐节点 probe 可以发现并清理测试残留。
4. setup/restore 故障必须有界失败并包含 world rank、hostname、目标路径和原因；cleanup 故障
   只报警，且被保留的目录必须是 mode 0700、`neighbor.out` 必须是普通文件。
5. 同一 revision 继续通过 CTest 和 1/2/4 rank × HostStaged/CudaAware numerical differential，
   确认 I/O 隔离没有改变数值或兼容输出。

## 关闭条件

- 在 `docs/status/current.md` 记录日期、最终提交 revision、双节点环境、精确命令和结果；
- 将 [risk-and-backlog.md](./risk-and-backlog.md) 的 R28 改为已关闭；
- 删除本计划；最终合同留在 standards、实测证据留在 status，Git 历史保存实施和审批过程。
