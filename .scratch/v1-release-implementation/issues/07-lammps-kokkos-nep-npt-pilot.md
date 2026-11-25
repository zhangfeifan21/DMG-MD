# 07: LAMMPS/KOKKOS/NEP 同卡数 NPT 先导

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

从现有公开短输入开始，逐材料核验 LAMMPS/KOKKOS/MatPL 的实际求力路径和设备，交付 11 可用的 Berendsen 配对清单及 SCR 不适用的依据。

## 验收条件

- [ ] 记录 LAMMPS、MatPL、KOKKOS 版本、精度与二进制 SHA-256。P=1、2、4 均须在日志核到 KOKKOS CUDA、`matpl/nep/kk`、势加载成功和实际 fix 名称。核对 `Loop time` 的进程数、原子数和步数，逐 rank 记录 CPU/NUMA 与 `LAMMPS_GPU_BIND` 的互异 GPU UUID。
- [ ] 从同快照按原子 ID 核对元素、质量、坐标、速度、盒和逐原子能量、力、virial 的单位、符号与分量。逐作业记录原势、转换势 SHA-256 和格式转换规则，正式输入对照 01 的哈希。三种势均使用统一 cutoff，铜的 `pair_coeff` 必须将类型 1 映射到 16 元素势中的 Cu。
- [ ] 该构建没有 SCR。碳、水、铜在 P=1、2、4 的 SCR 格均填不适用并列明依据。`npt/kk` 是 Nosé–Hoover，`press/langevin` 使用另一算法。相应格的倍率留空。
- [ ] 用 `nve/kk`、`temp/berendsen/kk` 和主机端 `press/berendsen` 对照普通 GPUMD 的 Berendsen。核对自由度、`t_period = 40 × dt`、`p_period = 1000 × dt`、各向同性控压及同一体模量（换算成 bar）。碳、水、铜的步长分别为 1、0.5、1.5 fs。记录两者控压缩放一阶相同、二阶不同。
- [ ] 先用普通 GPUMD 的重复试跑登记各材料平台段均温、均压、均体积的容差。再从两引擎共同的冻结快照核对弛豫方向与平台差值，全部符合才允许该材料配对。未通过的材料记录原因，允许配对的格注明主机控压及其耗时。
- [ ] 交付三材料 × 两种控压 × P=1/2/4 的逐格清单、原始日志和退出码。后端、哈希或绑定错误的作业作废后修正重跑。结构性容量限制和可比待测项分开登记。

## 依据

[V1.0 验收规格，用户故事 9、19、21、22](../../v1-release-readiness/spec.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md) · [L20 的 LAMMPS 参考核验](../../../docs/status/validation/lammps-reference.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)
