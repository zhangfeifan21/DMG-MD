# 04: 统一长短程参考并恢复 100 步 smoke

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

维护者能解释长短程普通 GPUMD 的参考差异，并用获认可的同一参考身份从实际候选作业得到首份完整长程 smoke。

可立即复现错误和准备参考侧差异证据。更新长程参考锁之前，需要对已有参考迁移决策作出可追溯的处置。

## 验收条件

- [ ] 复现长短版本锁的冲突。记录旧锁 `9d23496e`、短程 GPUMD 5.9.1 `11ba6fb`、对应二进制 SHA-256、输入及参考产物。解释 restart 精度与续跑差异，并明确历史结果所属版本。
- [ ] 先用参考侧核对差异并取得参考迁移决策，再同步被批准的身份和必要的清单。用参考提交或二进制不符的作业验证身份检查仍会拒绝启动，沿用冻结的数值容差。
- [ ] 用锁定参考和候选重新核对短程 golden。随后在碳、水、BaTiO₃-ZBL 的锁定 smoke 初态上，以种子 0、P=1、HostStaged 分别跑 NVE 与 NVT Berendsen 各 100 步。
- [ ] 归档精确命令、候选和参考版本与哈希、通过的清单和可追溯的科学输出。复现步骤可从干净的作业输出目录执行。

## 依据

[V1.0 验收规格，用户故事 13、19、20](../../v1-release-readiness/spec.md) · [决策 02 长短程 GPUMD 参考迁移](../../v1-release-readiness/issues/02-long-regression-reference.md) · [长程 profile 与矩阵](../../../tests/long_nve/README.md) · [短程 golden 与参考身份](../../../tests/baseline/README.md)
