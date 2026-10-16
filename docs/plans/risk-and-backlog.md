# 风险与剩余待办

类别：持续登记。状态：**IN PROGRESS**。核对日期：2026-10-16。

## 当前开发优先级

| 优先级 | 工作 | 当前缺口 |
| --- | --- | --- |
| P0 | [CPU 核数/NUMA 绑定](mpi-cpu-binding.md) | 统一启动策略、冲突/资源检查、无需采样的实际绑定验收 |
| P0 | [LAMMPS 对比](lammps-multigpu-benchmark.md) | 三引擎动力学和固定资源正式矩阵 |
| P0 | [通信计算重叠](communication-computation-overlap.md) | O1–O3 完成；O4 实际 overlap、O5 归因待执行 |
| 辅助 | [CPU 使用率](cpu-usage-study.md) | C2/C3；复用 C1，按需校准 |
| 后续 | [域分解扩展](domain-decomposition.md) | M2b、3D/box 扩展、100000-step release |
| 暂缓 | [双节点 I/O](multi-node-io.md) | R28 物理双节点验收 |

下表 P0/P1/P2 是风险严重度，分别为物理错误/越界/死锁、兼容或规模、性能工程风险，
不等于上表的排期。已由当前合同和测试控制的项目不再写成“尚未实现”。

## 持续约束与扩展风险

| ID / 严重度 | 当前控制 | 剩余边界 |
| --- | --- | --- |
| R1 / P0 两跳依赖 | 两跳 halo、dependency 冗余计算与 P1 oracle | M2b 需重证 Fp/反向 partial 交换 |
| R2、R8、R27 / P0–P2 计数/候选域/view | L stride、中心/候选域分离、空域守卫、epoch/capacity 分离 | 新布局/新异步 view 重新证明；capacity 不是身份 |
| R3、R7、R14 / P0–P1 身份/反边/group | gid 稳定、迁移含 labels、行按 gid 排序；verify 检查反边 | 多 image 和跨 rank edge key 未实现；不能缓存过滤行 ordinal |
| R4、R16、R23 / P0–P1 归约/RNG | owned 全局归约，root 按全局顺序生成速度后广播 | 默认无 seed 不可重现；不承诺跨 rank bitwise；counter RNG 是后续方案 |
| R5 / P0 GPU 所有权 | 直接 ordinary NEP，local-rank binding + UUID 去重 | 新 launcher 仍须唯一 GPU；不能实例化 NEP_MULTIGPU |
| R6、R13 / P0 ghost 最终量 | M2a 中心 gather，ghost scratch 不积分/归约/输出 | small-box 仅 M1 fallback；分布式多 image/Newton scatter 须 reverse exchange |
| R9、R10 / P0 box/wrap | P>1 只支持正交全周期；M2a 多盒位移 force 前拒绝 | P1 原行为保留；triclinic/nonperiodic local-domain 未实现 |
| R11 / P0 cache | 全局连续位移 MAX/reason OR、统一 rebuild | 新动态 box/cutoff/身份变化不得沿用旧证明 |
| R12 / P0 typewise/ZBL | 实际消费路径与 float 舍入保守取界 | Ra>Rr、ZBL outer>angular 的参考截断语义仍需专项 corpus |
| R15 / P1 温度 DOF | 无 fix/move 的 NVE/nvt_ber | fix/move 未来须全局 owned group counts |
| R17、R18、R24 / P1 输出/restart/修速 | root gid 排序、兼容 formatter、跨 rank restart；root 全局修速 | restart 不含 time/RNG/thermostat/unwrapped 历史，不能称 bitwise continuation |
| R19 / P1 规模 | root 全局 gather；每 rank 完整 host identity | 无端到端 host 内存 scaling，分块输出待设计 |
| R20、R21 / P1 输入一致 | 每 rank typed parse，run/model/potential fingerprint 一致性门 | 不是 root-only parse/broadcast；多 potential 明确拒绝 |
| R22 / P1 错误退出 | 本地诊断后 MPI_Abort；I/O setup 有协调出口；O3 单边故障验收 | 其他 compat 错误路径无完整故障 corpus；异常中不为聚合诊断新增 collective |
| R25 / P2 neighbor.out | M2a 本次表生成后 MAX，仅 root 写；call0/1000 精确记账 | 新分区不得重复 call/副作用；周期记录可能影响计时 |
| R26 / P2 CUDA-aware | query + collective/p2p 自检，HostStaged 默认，O3 completion/event lease | stream-aware、后台进度/跨节点 overlap 未证明，不能机械删同步 |
| R28 / P2 本地 scratch | 每 rank 本机 mkdtemp、0700、受限清理与故障测试已实现 | 双物理节点验收暂缓，风险保持打开 |

依赖证据见[kernel 清单](../reference/kernel-inventory.md)，生效规则见
[MPI](../standards/mpi-runtime.md)、[缓存](../standards/cache-validity.md)、
[halo](../standards/halo-lifecycle.md)与[分区](../standards/domain-partition.md)。

## 尚未关闭的具体事项

| ID | 事项 | 关闭所需证据 |
| --- | --- | --- |
| B1 | NEP3 是否纳入范围 | pinned reference negative test 与产品决定；当前拒绝 |
| B2 | Ra>Rr、ZBL 被 angular list 截断 | 合成势静态/边界 reference/candidate 对照 |
| B3 | large/small neighbor 容量极限 | 高密度 fixture、sanitizer 和安全拒绝；M2a 已有行容量守卫 |
| B5 | malformed NEP 宽松解析/拒绝 | 完整 corpus、错误类别与关键 message |
| B6 | 负 timestep、run 0/负步数等边界 | reference negative corpus；当前 parser 接受有限负 DT 和任意 int steps，不等于已验证兼容 |
| B8 | 默认初速度与未来随机 thermostat | seed/rank-count/restart 实验；不为兼容初始化擅换 RNG |
| B9 | 可选确定性归约模式 | 产品承诺与固定顺序/高精度 oracle；默认仍按现有容差 |
| B10 | restart sidecar | time/RNG/thermostat 等产品决定，保持主文件兼容 |
| B11 | host identity/root gather 上限 | 大 N 内存门槛、元数据分片/分块输出方案 |
| B12 | unwrapped 历史连续性 | 迁移已有载荷；restart 不保存历史，sidecar/image counter 另设计 |
| B13 | 完整单 rank 异常传播 | 扩展故障 corpus，有界退出，不对称分支不挂起 |
| B14 | small/large 每原子 virial 归属 | 同构 fixture 的逐原子与总量对照 |
| B15 | 普通 NEP charge 单位 | 参考代码/manual 与兼容测试 |

B7 的多 potential/方向选项已明确 unsupported，不再作为语义待决；B16 的 adaptive timestep
已实现，剩余负值/多段边界归 B6。性能候选还包括 rank-local cell 网格和按需 NVE thermo，
均未实施；NVT 每步全局 thermo 与兼容输出不得改变。

## 后续实现的不变量

每个 gid 唯一 owner；ghost 不积分/归约/直接输出；中心和依赖 producer 恰一次；每个有效
angular 反边可用；slot<L 且 row count≤capacity；布局/迁移后旧 view/row/intermediate 失效；
输出按 gid 恢复恰好 N 条（group 为对应全局数量）。这些由定向 CPU/CUDA/MPI 测试与部分
运行时检查覆盖，**尚未实现为完整 debug 每步 assert 表**。

未来 triclinic 必须以 fractional ownership、物理距离选 halo，不能投影 Cartesian box；
small-box 多 image 必须保留身份并定义 reverse exchange。新增数学、布局、精度、所有权、
半径或兼容承诺按 AGENTS 先给证据与 golden 设计，再确定方向。
