# 04: 统一长短程参考并恢复 100 步 smoke

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

长短程数值参考采用普通 GPUMD 5.9.1 的同一提交和二进制身份。维护者可从原始校准、重启边界和首份 smoke 结果复核这次迁移。

## 验收条件

- [ ] 记录旧锁 `9d23496e`、目标 `11ba6fb` 和两者登记的二进制 SHA-256。引用 2026-11-20 的短程逐字节对照，审阅改读 17 位有效数字 `restart.xyz` 的边界差异。90 个确定性模型沿用现有哈希，旧二进制的历史输出可追溯。
- [ ] 在迁移候选清单中使长短程同时指向 `11ba6fb` 及短程清单的完整二进制哈希。以提交或哈希不符的负例验证身份拒绝。保留原有数值容差、重启边界比较方式和旧锁、旧产物的可追溯记录。
- [ ] 在 L20 用 `11ba6fb` 重跑 30 次短程校准并交付新的 `calibration.json`。记录参考重复误差，若未远小于冻结容差，停止并报告原因。核对新参考及候选的短程 golden 后才进入 smoke。
- [ ] 按清单跑碳、水、BaTiO₃-ZBL 的 NVE 和 NVT Berendsen，各 100 步，种子 0、P=1、HostStaged。核对 smoke 的派生短例、输出和退出码。在干净作业目录可按记录的命令、候选与参考哈希重放。
- [ ] 将校准和 smoke 证据交给 20。旧参考归档安排在新版校准、smoke、nightly 均通过之后。将今后升级的短程逐字节比较、长短清单同次更新和逐级复验步骤写入长程说明。

## 依据

[V1.0 验收规格，用户故事 13、19、20](../../v1-release-readiness/spec.md) · [决策 02 长短程 GPUMD 参考迁移](../../v1-release-readiness/issues/02-long-regression-reference.md#answer) · [长程 profile 与矩阵](../../../tests/long_nve/README.md) · [短程 golden 与参考身份](../../../tests/baseline/README.md)
