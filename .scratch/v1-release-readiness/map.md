# DMG-MD V1.0 发布前的开发与验收

Label: wayfinder:map

## Destination

确定 DMG-MD V1.0 的开发顺序与验收证据。单节点试用优先三种公开材料的 NPT，验收 SCR 科学结果并报告 DMG-MD 的单卡能力、同卡数跨软件表现与自身强扩展。正式发布还要通过单卡 LJ、EAM/alloy 及发布检查。

## Notes

- 实施与验收的规格见[DMG-MD V1.0 单节点试用与正式发布验收规格](spec.md)。
- 2026-11-23，维护者选定平衡并放大的公开金刚石碳、致密水和 FCC 铜。同日修订性能范围，普通 GPUMD、LAMMPS/KOKKOS/NEP 与 DMG-MD 按相同卡数比较。GPUMD-WPE 固定单卡，分别衡量单卡性能和迁移到多卡的耗时。三项必须报告，可比组合实测，科学门槛独立，性能不设固定胜负或倍率。组内真实输入后续做外部验证。
- 按 [正式版范围](../../docs/plans/dmg-md-v1.0-development-workflow.md)、[当前进度](../../docs/plans/dmg-md-v1.0-progress.md)、[生产多卡分派决策](../../docs/standards/architecture-decisions.md#production-domain-routing) 和 [NPT 计时规程](../../docs/status/performance/npt-benchmarks.md) 规划。2026-11-23 查核的源码提交为 `b103010`。
- 后续会话使用 Wayfinder、grilling 和 domain-modeling。工单的认领、依赖和答复遵循[仓库工单约定](../../docs/agents/issue-tracker.md#wayfinder-探索工单)。实施与正式采样按仓库开发计划另行执行。

## Decisions so far

- [三种公开算例按什么规模和快照冻结](issues/01-public-material-inputs.md): 单档 100-120万原子，长宽比2.75，GPUMD预平衡（10万步Berendsen+2万步SCR），四引擎静态验证后冻结，10万步正式benchmark

- [长程回归怎样衔接 GPUMD 5.9.1 短程基线](issues/02-long-regression-reference.md): 长短程数值参考同锁 5.9.1 `11ba6fb`，性能计时只记版本；新校准后依次过 smoke、nightly、P4 release，容差不变；旧结果归档到被忽略的 archive/

- [三种 SCR 工况各用什么统计标准验收](issues/03-scr-science-protocol.md) 每种材料对 103 个区间作 95% 同时验收，按 90% 的整组通过概率目标选择采样预算，三种材料合计至少 408 条正式独立链。

- [三项对标怎样采样并据此选择局部 NPT 缓存策略](issues/04-dn5-timing-and-adoption.md): 进程墙钟为主指标，每格 3 个随机区组（变异系数超过 2% 增至 5），用对数配对 t 区间；affine 须科学全过且有可分辨收益才作多卡 NPT 默认，否则 rebuild；内部对照先行，未达检查点先调试迭代，再做外部对标。

## Not yet specified

扩大后的水或铜若出现长自相关、盒形不再满足分区条件，或局部计算节省的时间被通信与输出抵消，需要先导数据才能定位原因，再定义应补的实验。待清楚到可以表述成具体问题时，新增工单。

三项对标、内部对照和调试轮的完整 GPU·小时预算待先导实测步时后确定。若超出可用的独占时段，再决定缩减哪些格。

## Out of scope

- 本轮范围采用正式计划的六种系综及势范围。Langevin/BAOAB、Tersoff 1989、各向异性或三斜 NPT、三维分解和非 NEP 的局部多卡属于后续开发。
- 跨物理节点的正确性、重启、I/O 和性能另立实测，需要至少两台物理节点。首版试用按单节点三种材料验收。长程发布矩阵的八卡要求仍保留。
