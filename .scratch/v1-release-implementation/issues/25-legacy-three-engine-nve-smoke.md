# 25: 用已安装的引擎完成 NVE 短程物理对照

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

维护者能在旧三引擎 NVE 入口验证普通 GPUMD、LAMMPS/KOKKOS/MatPL 和 DMG-MD 的真实身份，并复现一个可独立解释的短程物理对照。

NVE 直接使用已安装的程序，报告登记实际版本。本单独立验收短程物理对照。

## 验收条件

- [x] 用显式可执行文件在 L20 运行 `carbon_smoke`。2026-11-23，普通 GPUMD、LAMMPS/KOKKOS/NEP 和 DMG-MD 完成 P1/P2，WPE 完成 P1，7 个作业通过。版本与设备记录见[实测结果](../../../docs/status/validation/lammps-reference.md#l20-installed-nve-20261123)。
- [ ] 核对共同初态使用的势、精度和设备分配，并确认错误的后端或重复 GPU 绑定不能产生有效比较结果。
- [ ] 从共同初态完成三引擎排序和单位一致的静态能量、力、virial 对照及短 NVE，核对守恒相关输出和真实后端。物理检查通过后才能记录该 NVE 工况本身的耗时。
- [ ] 交付 NVE 的复跑命令、版本、输入、误差与作业证据，并在报告中注明系综和计时边界。该补充对照独立于 10–12 的 NPT 正式报告。

## 依据

[V1.0 验收规格，用户故事 13、19、21、22](../../v1-release-readiness/spec.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md) · [现有计时入口和 profile](../../../tests/benchmark/README.md) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md) · [L20 的 LAMMPS 参考核验](../../../docs/status/validation/lammps-reference.md)
