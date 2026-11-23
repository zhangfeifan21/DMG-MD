# 15: C0 统一求力接口并保持 NEP 兼容

Status: ready-for-agent

Blocked by: None (can start immediately)

结单所需的长程回归依赖 [04 统一长短程参考并恢复 100 步 smoke](04-long-reference-smoke.md) 的已验收参考。

## 交付内容

用户沿原有入口运行 NEP 作业时，全系统求力通过统一接口，短程结果、局部布局、变盒与重启行为保持已验收的行为。

## 验收条件

- [ ] 让已支持的 NEP 变体沿原有输入经过统一全系统求力接口，保留周期折回、清零、求力次序及工作区所有权。局部数据布局和变盒后的缓存失效可用新旧盒的物理输出核对。
- [ ] 运行单卡冻结 golden、关键 NHC/BDP 作业和 P=2、4 的 NEP 局部回归。核对静态与短程能量、力、virial、thermo、逐原子输出顺序及 restart，按既有容差归档结果。
- [ ] 用变盒和段切换案例验证失效后的下一次求力、跨区迁移和工作区生命周期。既有 CTest、标量及 MPI 回归的结果可追到本候选。
- [ ] 结单前，在本工单候选上复跑工单 04 解锁的 100 步长程 smoke。附候选、短程/长程参考身份、命令和差异表。

## 依据

[V1.0 验收规格，用户故事 1、6、7、13、19](../../v1-release-readiness/spec.md) · [C0–C5 力场验收](../../../docs/plans/dmg-md-v1.0-development-workflow.md#力场开发卡) · [短程 golden 与参考身份](../../../tests/baseline/README.md) · [恒温器与重启回归](../../../tests/thermostats/README.md) · [长程 profile 与矩阵](../../../tests/long_nve/README.md)
