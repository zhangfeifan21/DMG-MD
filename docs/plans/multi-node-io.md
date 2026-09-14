# 多节点 rank I/O 隔离整改方案（待审批）

类别：待实施计划。

状态：**PROPOSED，尚未修改生产源码**。本方案针对 replicated-data MPI prototype；经维护者
审批后才修改 `src/runtime.cu`。

## 1. 当前缺陷

`RankIoIsolation` 当前由 world rank 0 在自己的
`std::filesystem::temp_directory_path()` 下创建所有非零 rank 的目录，再把该根路径广播给所有
rank（`src/runtime.cu:80-129`）。这隐含“rank 0 的临时目录在所有节点以同一路径可见”。集群上
`/tmp` 通常是节点本地文件系统，因此跨节点 rank 可能无法 `chdir`；更坏的错误路径是在某些
rank 已进入 collective、另一些 rank 抛异常时形成 hang。

隔离的目标不变：只有 rank 0 可以写用户作业目录中的兼容输出；legacy ordinary NEP 在非零
rank 产生的 `neighbor.out` 必须落到一次性本地目录或本地 `/dev/null`，不能发生多 rank append。

## 2. 推荐设计：每 rank 本地 scratch

1. rank 0 生成只用于命名和诊断的 job nonce，并广播；不广播任何文件系统路径。
2. 每个非零 rank 在**本机** `temp_directory_path()` 下创建
   `dmgmd-rank-io-<nonce>-r<world-rank>`。world rank 保证同一作业唯一，nonce 避免并发作业和
   上次异常残留冲突。
3. rank 0 保持原工作目录；非零 rank 在创建成功后进入自己的目录，并在本地创建
   `neighbor.out -> /dev/null`。不再创建 `run.in` symlink：parser 已在隔离发生前完成；potential
   已转为绝对路径、完成 fingerprint/metadata 校验，随后 `NepForce` 也按该绝对路径读取，运行
   阶段不应重新读取 `run.in`。
4. 把“创建目录、创建 symlink、chdir”做成两阶段 collective：每 rank 捕获本地错误并先
   `Allreduce` 成功标志；若任一失败，由 rank 0 汇总可诊断的 rank/host/path/error 后让所有 rank
   走同一个失败出口。任何 rank 都不得在其他 rank 进入 barrier 前直接抛出。
5. 正常结束时，非零 rank 先恢复自己的原工作目录并报告状态；所有 rank 确认恢复后，各非零
   rank 删除**自己的**目录。rank 0 不删除其他节点路径。
6. 析构函数只做本地、best-effort、无 MPI 的恢复/清理；需要 collective 的错误传播和清理由
   显式 `finish()` 完成，避免栈展开期间调用 MPI。
7. 安全边界：删除目标必须同时满足父目录等于本 rank 启动时记录的 temp 根、basename 带完整
   nonce 和 world-rank、且不是 symlink。校验失败时保留目录并报警，不扩大删除范围。

## 3. 为什么暂不改 gpumd_compat 写入策略

长期更干净的方案是给 ordinary NEP 注入 `neighbor.out` sink/enable policy，彻底取消 `chdir`。
但这会修改 `src/gpumd_compat` 的内部 I/O 路径，扩大与锁定 GPUMD 复现代码的差异。本阶段推荐
先在 runtime 边界完成本地 scratch 隔离；后续若决定移除隐式 neighbor I/O，再单独审计并跑
完整 golden/release 矩阵。

“所有 rank 使用共享 scratch 根”也不作为默认方案，因为它要求站点提供共享文件系统，不能
解决无共享 `/tmp` 的通用多节点部署。

## 4. 验证与故障注入

审批实现后必须增加以下门槛：

- 单节点 1/2/4 rank：现有 golden 与 HostStaged/CudaAware matrix 全部不回归；作业目录只有
  rank 0 的兼容输出。
- 至少 2 节点×2 rank：两个节点设置不同且互不可见的 `TMPDIR`，验证全部 rank 启动、计算、
  恢复 cwd 和本地清理成功。
- 在一个非零 rank 分别注入 mkdir、symlink、chdir、restore、cleanup 失败，验证所有 rank
  有界退出且错误包含 world rank、hostname、目标路径和系统错误；不得 hang。
- 成功与失败后检查用户作业目录无非零 rank 输出；成功后无 scratch 泄漏，cleanup 失败时只
  保留精确的本 rank 目录供诊断。
- 用 rank 0 本地 `/tmp` 不存在于第二节点的环境做回归，确保测试能捕获当前实现而通过新实现。

## 5. 请审批的决策点

- 是否接受“每个非零 rank 使用节点本地 scratch、rank 0 保持作业目录”的边界；
- 是否保留非零 rank `neighbor.out -> /dev/null`，还是改为普通本地文件后退出时删除；
- cleanup 失败是否只报警并成功退出（推荐），还是把已经完成的 MD 作业判失败；
- 是否批准实现阶段同步增加上述多节点/故障注入测试。
