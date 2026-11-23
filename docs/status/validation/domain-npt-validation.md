# 局部 NPT 验证

<a id="dn4-final-20261116"></a>
## 2026-11-16 DN4 统计验收

这次验收覆盖单节点、5,760 原子 NEP4 金刚石碳、300 K、0 GPa 和各向同性正交全周期盒。152 条完整链完成一次最终分析，151 个注册区间按预定规则通过。检查包括并行和步长比较、独立体积响应，以及温度和压力。跨种子聚合均值的最低有效样本量为 1,035.814。[冻结的统计设计](../../../tests/npt/domain-statistics-registration.json)

| 步长配置 | 覆盖的 rank、后端和域模式 |
| --- | --- |
| 基准 | P1/HostStaged/off；P2/P4 的 HostStaged、CudaAware 各含 off、rebuild、affine |
| 半步长 | P1/HostStaged/off、P2/HostStaged/affine、P4/CudaAware/affine |

`off` 为全量复制对照，局部样本须确认 `m2a` 路径。216 原子碳 G-B、含 ZBL 的短程检查和其他材料分别有自己的证据范围。[局部 NPT 标准](../../standards/domain-npt.md) · [G-B](npt-validation.md#npt-scr-confirmation-20261104)

### 候选与审核身份

原报告记录的代码基线为 `d1b933c05c857e536ffb0e71e2212bc02939f5bc` 加未提交修改。环境为单节点 RTX 4090、CUDA 12.8、Open MPI 5.0.10 和 UCX 1.22.0。冻结候选 SHA-256 为 `5a460ad845597f62623e33657abd7e1a81af6b94e908b98a5768ae5e5b88158e`。

原最终审核 `final-audit.json` 的 SHA-256 为 `e52a56979a6d0afad747814ea23a12ee5e59f8fb1eb8278482233ced8eb7a909`，分析结果 `analysis.json` 的 SHA-256 为 `0db29a9e5d176744fe844625705c937d920d3ab080971724fc08f5ab6617c36e`。这些是原报告登记的身份，原始链和审核日志未随仓库分发。精确分析参数记在原 `analysis-start.json`，审核命令与环境记在原执行收据；当前公开页不具备这些执行记录。

原报告分别登记最终只读审核退出 0、最终分析子进程退出 0。未观察的是分析启动器的外层 OS 退出码。旧 wrapper 退出码和部分经授权删除的失败输出也有留存限制。审核未重新计算估计量、有效样本量或 bootstrap。完整历史文字可在提交 `0e8360f` 的本文件中查询。

<a id="l20-domain-npt-20261120"></a>
## 2026-11-20 单节点短程复验

另一次复验使用 4 × L20，以及 `0e8360f` 加工作树修改的 Release/sm_89 构建。条件为 5,760 原子碳、300 K、0 GPa、0.25 fs × 20 步。

P2/P4 和两种通信后端各测全量复制及两个局部模式。Berendsen 与 SCR 各通过 12/12 项，比较实际路径、thermo、坐标、速度、力和 virial。这是短程复验，不能替代上述独立统计验收。逐作业命令和结果保存在当次站点记录，未随本页分发。

同日另以 0.1 fs × 20 步检查碳、水和铜的单卡与四卡。输入与结论见[性能方法与结果](../performance/npt-benchmarks.md#目前有哪些实测)。
