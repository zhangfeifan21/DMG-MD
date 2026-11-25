# 25: 完成三材料四引擎同卡数 NVE 对照

Status: ready-for-agent

Blocked by: 01, 05

直接前置工单为[01 冻结碳、水、铜三套公开输入](01-public-equilibrated-inputs.md)、[05 用短作业验证配对计时](05-paired-job-timing.md)。

## 交付内容

用 01 的三种冻结快照和 05 的区组计量交付独立的 NVE 同卡数表，候选与外部程序均记录实际构建。已有碳短程作业可直接重放。两张前置工单提供新增三材料正式采样所需的输入和计量方法。

## 验收条件

- [x] 用显式可执行文件在 L20 运行 `carbon_smoke`。2026-11-23，普通 GPUMD、LAMMPS/KOKKOS/NEP 和 DMG-MD 完成 P1/P2，WPE 完成 P1，7 个作业通过。版本与设备记录见[实测结果](../../../docs/status/validation/lammps-reference.md#l20-installed-nve-20261123)。
- [ ] 用碳、水、FCC 铜的冻结盒、坐标、速度和势启动 P=1/2/4 的 NVE。P=1 配普通 GPUMD、LAMMPS/KOKKOS/NEP、WPE 与 DMG-MD 四引擎。P=2/4 比较普通 GPUMD、LAMMPS/KOKKOS/NEP 与 DMG-MD，WPE 固定单卡。配对使用相同卡数、物理步长、步数和输出要求。
- [ ] 逐作业登记原势、转换势和二进制哈希，核对普通 GPUMD 的实际多卡 NEP、LAMMPS 的 MatPL/KOKKOS 和逐 rank 绑卡、WPE 宿主与 Wisevolve 单卡标记，以及 DMG-MD 的实际分派。用错误后端或重复 GPU 绑定的负例验证无效作业会排除。
- [ ] 按原子 ID、单位与分量核对静态能量、力和 virial，并从同一初态跑短 NVE 核对守恒相关输出。物理与身份核验通过后再对该材料和卡数做配对计时，容量或结构限制登记依据。
- [ ] 在独占 4 × L20 上按 05 的预热、先导变异系数、随机区组及对数配对 95% t 区间采样。交付有版本、命令、系综、计时边界、两侧绝对时长、误差及 GPU·小时的单独 NVE 表，预算计入总机时。14 按 NPT 试用条件验收，26 另行归档本表。

## 依据

[V1.0 验收规格，用户故事 13、19、21、22](../../v1-release-readiness/spec.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md#answer) · [现有计时入口和 profile](../../../tests/benchmark/README.md) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md) · [L20 的 LAMMPS 参考核验](../../../docs/status/validation/lammps-reference.md) · [决策 04 区组计时与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer)
