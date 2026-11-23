# 风险与剩余待办

状态：IN PROGRESS。核对日期：2026-11-20。

## 当前开发优先级

| 优先级 | 工作 | 剩余事项 |
| --- | --- | --- |
| P0 试用 | [生产分派、三材料 NPT 与性能报告](dmg-md-v1.0-progress.md) | 逐材料通过 SCR 科学检查，报告普通 GPUMD 和 LAMMPS/KOKKOS/NEP 同卡数、WPE 固定单卡三项对照，测量自身强扩展 |
| P0 发布 | [V1.0 正式版](dmg-md-v1.0-development-workflow.md) | 完成 C0–C3/C5 和 D1–D5 |
| 外部对照准备 | [CPU 绑定](mpi-cpu-binding.md)、[LAMMPS 对照](lammps-multigpu-benchmark.md) | 核验跨引擎身份、绑定和控压可比性，优先三材料 NPT，NVE 矩阵单列 |
| 后续 | [域分解扩展](domain-decomposition.md)、[双节点 I/O](multi-node-io.md) | 在两台物理节点验证正确性并独立测量性能 |

下表 P0/P1/P2 表示风险严重度：P0 涉及物理错误、越界或死锁，P1 涉及兼容或规模，P2 涉及性能工程。排期以上表为准。已受当前规则和测试控制的风险，仍需在扩展时复核。

## 持续约束与扩展风险

| ID / 严重度 | 现有控制 | 扩展时仍需处理的边界 |
| --- | --- | --- |
| R1 / P0 两跳依赖 | 两跳位置边界、依赖副本重算和 P1 数值参照 | 中间场交换须重新证明 Fp 与反向 partial 的交换 |
| R2、R8、R27 / P0–P2 计数、候选与视图 | 以本地逻辑长度 L 为数组跨度，分开中心和候选范围，检查空域，区分代次与容量 | 新布局或异步视图须重新证明；容量不能充当数据身份 |
| R3、R7、R14 / P0–P1 身份、反边与分组 | gid 稳定，迁移携带 labels，邻居行按 gid 排序，verify 检查反向边 | 多镜像与跨 rank 稳定边键未实现；不能缓存过滤后行下标 |
| R4、R16、R23 / P0–P1 归约与随机数 | 只归约负责原子，根进程按全局顺序生成初速度后广播 | 缺省初速度不承诺跨运行复现；BDP/SCR 使用已验收的显式 seed 规则，仍不承诺跨 rank 逐位一致；计数器随机数为后续方案 |
| R5 / P0 GPU 所有权 | 使用普通 NEP，按 local rank 绑定并核验 GPU UUID 唯一 | 新启动器也须一 rank 一 GPU，不能将 NEP_MULTIGPU 实例化为每 rank 的并行层 |
| R6、R13 / P0 边界副本最终量 | 局部力按中心原子汇总，边界临时量不积分、不归约、不输出 | 当前小盒回退到全量复制；分布式多镜像或 Newton scatter 须定义反向交换 |
| R9、R10 / P0 盒与周期折回 | P>1 只支持正交全周期盒，局部路径在求力前拒绝单步多盒位移 | P1 保留原行为，三斜与非周期局部路径未实现 |
| R11 / P0 缓存 | 全局一致判定和重建；实验局部 NPT 记录盒版本，按参考盒计算形变余量 | 各向异性盒、新 cutoff 或身份变化须验证缓存判据。[局部 NPT](../standards/domain-npt.md) |
| R12 / P0 类型半径与 ZBL | 按内核实际使用路径与 float 舍入保守取界 | Ra>Rr、ZBL outer>angular 的参考截断语义仍需专项输入集 |
| R15 / P1 温度自由度 | 无 fix/move 的 NVE、三种 NVT 和当前 NPT 按各自规则计算自由度 | fix/move 未支持，新增时须计算全局负责原子的分组数量 |
| R17、R18、R24 / P1 输出、重启与修速 | 根进程按 gid 排序并使用兼容 formatter，支持跨 rank 重启及全局修速 | restart 不含时间、随机数、恒温器和展开坐标历史，不能保证轨迹逐位延续 |
| R19 / P1 规模 | 根进程全局收集，每 rank 保留完整主机身份信息 | 主机内存尚无端到端扩展性，分块输出待设计 |
| R20、R21 / P1 输入一致 | 每 rank 解析类型化输入，核对 run/model/potential 指纹 | 当前并非根进程解析后广播，多 potential 明确拒绝 |
| R22 / P1 错误退出 | 本地诊断后 MPI_Abort，I/O setup 有协调出口，O3 验证单边故障 | 其他兼容错误路径缺完整故障输入集；异常路径不能为汇总诊断新增集合通信 |
| R25 / P2 neighbor.out | 局部表生成后做 MAX，仅根进程写，精确记录第 0/1000 次调用 | 新分区须避免重复调用和副作用，周期记录可能影响计时 |
| R26 / P2 CUDA-aware | 查询能力并自检集合及点对点通信，默认 HostStaged；O3 管理完成事件和使用期 | 尚未证明流感知、后台推进或跨节点重叠，不能直接删除同步 |
| R28 / P2 本地 scratch | 每 rank 本机 mkdtemp、0700、受限清理和故障测试已实现 | 双物理节点验收暂缓，风险保持打开 |

依赖证据见[内核清单](../reference/kernel-inventory.md)。现行行为见[MPI](../standards/mpi-runtime.md)、[缓存](../standards/cache-validity.md)、[边界交换](../standards/halo-lifecycle.md)和[计算分区](../standards/domain-partition.md)。

## 尚未关闭的具体事项

| ID | 事项 | 关闭所需证据或决定 |
| --- | --- | --- |
| B1 | NEP3 是否纳入范围 | 锁定参考的负例和产品决定；当前拒绝 |
| B2 | Ra>Rr、ZBL 被 angular list 截断 | 合成势的静态与边界 reference/candidate 对照 |
| B3 | 大小盒邻居容量极限 | 高密度输入、sanitizer 和安全拒绝；局部路径已有行容量检查 |
| B5 | 非法 NEP 的宽松解析或拒绝 | 完整输入集、错误类别和关键诊断 |
| B6 | 负 timestep、run 0 或负步数 | 参考负例集。旧 NVE/`nvt_ber` 对有限负 DT 和任意 int steps 的兼容边界未验证；NHC/BDP/NPT 已在运行前拒绝非正 DT |
| B8 | 缺省初速度、rank 数与重启的复现边界 | 明确缺省初速度 seed 的规则；BDP/SCR 已按各自 seed/RNG 规则验收 |
| B9 | 可选确定性归约 | 产品承诺及固定顺序或高精度数值参照；默认仍按现有容差 |
| B10 | restart sidecar | 确定时间、随机数和恒温器状态的保存方式，保持主文件兼容 |
| B11 | 主机身份信息和根进程收集上限 | 大 N 内存判据、元数据分片和分块输出方案 |
| B12 | 展开坐标历史连续性 | 迁移已有载荷，restart 不保存历史；另行设计 sidecar/image counter |
| B13 | 完整单 rank 异常传播 | 扩展故障集，验证有界退出和不对称分支不挂起 |
| B14 | 大小盒每原子 virial 归属 | 同构输入的逐原子与总量对照 |
| B15 | 普通 NEP charge 单位 | 参考源码、手册和兼容测试 |

B7 的多 potential 和方向选项已明确为 unsupported。B16 的自适应步长已实现，剩余负值与多段边界归 B6。按需计算 NVE 热力学量和合并缓存判定归约，分别对应已采用的 O8、O7；相关后续研究见[优化计划](communication-computation-overlap.md)。NVT 的逐步全局热力学反馈及兼容输出须保留。rank-local cell 网格仍是后备候选，当前没有优先实施依据。

## 后续实现的不变量

每个 gid 只有一个所有者，边界副本不积分、归约或直接输出。每个中心及依赖中间量只计算一次，有效 angular 反向边必须可用。槽位满足 `slot<L`，邻居数量满足 `row count≤capacity`。布局或迁移后，旧视图、邻居行和中间量失效。输出按 gid 恢复恰好 N 条，分组输出恢复对应全局数量。

现有定向 CPU/CUDA/MPI 测试和部分运行时检查覆盖这些条件，尚无完整的逐步 debug assert 表。

未来三斜盒须按分数坐标归属、物理距离选择边界，不能直接投影 Cartesian box。小盒多镜像须保留身份并定义反向交换。涉及数学、布局、精度、所有权、半径或兼容承诺的改动，先按 AGENTS 给出证据和 golden 设计，再确认方向。
