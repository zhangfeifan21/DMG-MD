# 07: LAMMPS/KOKKOS/NEP 同卡数 NPT 先导

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

用同一份物理初态运行已登记的 MatPL NEP 后端，确定三个材料在 LAMMPS 与 DMG-MD 之间哪些 NPT 组合可配对。

先导可从现有短输入开始。引擎来源决策登记已验证的 LAMMPS/MatPL 来源、转换与逐格控压条件。

## 验收条件

- [ ] 随结果记录实际 LAMMPS、MatPL 与 KOKKOS 的版本及精度，从日志确认 KOKKOS CUDA 和 `matpl/nep/kk`。以 P=1、2、4 个 MPI rank 运行短作业，验证每个 rank 绑定一张唯一 GPU，并保留进程、CPU/NUMA 和设备对应记录。
- [ ] 从同一快照转换 LAMMPS 输入，按原子 ID 校准元素、质量、位置、速度、实际物理步长和边界。归档原势及等价转换的哈希，比较逐原子能量、力、virial 的单位、符号和分量顺序。缺少等价转换的材料记录具体原因。
- [ ] 当前 `npt/kk` 使用 Nosé–Hoover，在三材料 SCR 格标记算法不等价并保留依据。只有实际验证等价的 SCR 实现才能改变对应格的判定。
- [ ] 用支持的积分器配合 `temp/berendsen`、`press/berendsen` 作短段。对照温控自由度、温压耦合时间、阻尼单位、体模量和盒响应方向、幅度及稳定性。依据物理检查逐材料登记 Berendsen 的可比状态。
- [ ] 交付三材料 × 两种控压 × 1/2/4 卡的先导清单，记录绑定、后端失配及潜在容量限制。选错 pair style、重复绑卡或转换失败的作业不得进入配对结果。

## 依据

[V1.0 验收规格，用户故事 9、19、21、22](../../v1-release-readiness/spec.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md) · [L20 的 LAMMPS 参考核验](../../../docs/status/validation/lammps-reference.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)
