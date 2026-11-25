# 20: 恢复四卡 10,000 步 nightly

Status: ready-for-agent

Blocked by: 04

直接前置工单为[04 统一长短程参考并恢复 100 步 smoke](04-long-reference-smoke.md)。

## 交付内容

交付 L20 四卡长程 nightly 的完整覆盖表。用 04 已校准且通过 smoke 的同锁参考运行，在 nightly 通过后归档旧结果，并另交 L20 截至 P4 的 release 覆盖表。

## 验收条件

- [ ] 使用 04 的普通 GPUMD 5.9.1 `11ba6fb` 和对应二进制 SHA-256。独占 4 × L20，核对候选、模型哈希和通信探针。按 nightly 几何及种子 0 运行碳、水、BaTiO₃-ZBL，NVE 与 `nvt_ber` 各 10,000 步。
- [ ] 覆盖 P=1、2、4 和 HostStaged、CudaAware，核对清单所要求的单卡全体系、各多卡局部分派与每个原子唯一归属。记录每项的完成步数、输出和退出码。
- [ ] 按既有清单审核能量漂移、热力学/结构统计、双向同构型回放及相应跨 rank restart，并执行清单中的短程兼容派生例。保留原始检查点和汇总，失败项能按同一冻结输入独立复跑。
- [ ] 交付完整 nightly 的覆盖表、实际版本、阈值和判定。校准、smoke、nightly 均通过后，将旧 `9d23496e` 校准与产物移入服务器的 `archive/gpumd-9d23496e/`，忽略归档目录并停止跟踪旧产物。当前基线文档登记新参考，兼容源码出处、势来源和独立冻结测试按原身份追溯。
- [ ] nightly 通过后按 release 清单在 L20 单独跑 P=1/2/4、种子 0 至 4 和双后端，记录实际步数、输出和任何缺项。将 nightly 与截至 P4 的 release 两份记录交给 21。完整八卡 release 在同一台八卡机重新覆盖 P=1/2/4/8。

## 依据

[V1.0 验收规格，用户故事 2、6、7、13、19、20](../../v1-release-readiness/spec.md) · [长程 profile 与矩阵](../../../tests/long_nve/README.md) · [冻结 golden 与误差约定](../../../docs/standards/golden-test-standard.md) · [决策 02 长短程参考迁移](../../v1-release-readiness/issues/02-long-regression-reference.md#answer) · [决策 05 八卡发布站点](../../v1-release-readiness/issues/05-eight-gpu-release-site.md#answer)
