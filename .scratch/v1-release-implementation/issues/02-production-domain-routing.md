# 02: 实现生产多卡分派与启动拒绝

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

组员按常规入口启动作业时，程序根据全份输入决定可用的单卡或局部多卡路径，并在资源不合格时给出可执行的恢复办法。

## 验收条件

- [ ] 用同类真实作业确认 P=1 的全体系求力及合格 P=2、4 的局部求力。每个 rank 使用唯一 GPU，日志和原子所有权证明各 rank 只为负责原子计算最终力。HostStaged、CudaAware 分别留证。
- [ ] 在首步积分与首份科学输出前，集体验证势、盒、全部 run 段和 rank 间设置。覆盖合格的 NPT→NVT、仅后段不合格的输入、初始小盒、条带宽度等于资格边界、各 rank 设置不一致以及没有局部实现的请求。
- [ ] 每个失格多卡作业整体非零退出，记录请求卡数、具体失格条件和可执行的单卡命令。对单卡支持的输入实际重试成功。仅在资格检查确认后给出较少卡数选项。
- [ ] 用显式内部入口重放原 G-B 多卡复制对照。常规入口发出局部模式日志或明确拒绝。归档启动命令、退出码、文件清单和分派证据，检查所有进程都已结束。

## 依据

[V1.0 验收规格，用户故事 1、2、3、4、6](../../v1-release-readiness/spec.md) · [生产分派决策 D-013](../../../docs/standards/architecture-decisions.md#production-domain-routing) · [MPI 域分解回归](../../../tests/mpi/README.md) · [局部域 NPT 合同](../../../docs/standards/domain-npt.md)
