# 03: 验证变盒、输出与跨卡重启

Status: ready-for-agent

Blocked by: 02

直接前置工单为[02 实现生产多卡分派与启动拒绝](02-production-domain-routing.md)。

## 交付内容

从实际进入局部域分解模式的 NPT 作业核验有效盒、唯一原子、输出和跨卡新作业重启。02 已有通用失格拒绝的验证记录。本单显式指定缓存策略即可开工，13 在选定默认值后补普通多卡命令的回归。

## 验收条件

- [ ] 在 P=2、4 上覆盖 HostStaged、CudaAware 两种后端，分别显式运行 `rebuild` 和 `affine`。合格作业核对逐 rank GPU、`DMGMD_DOMAIN mode=m2a` 和 `DMGMD_DOMAIN_NPT` 策略。失格初始盒在首步和科学输出前集体非零退出，验证诊断给出的单卡重跑命令。
- [ ] 分别构造正常变盒和积分中收缩至失格的候选盒。失格时各 rank 在提交候选盒前共同退出，最后一份 thermo、轨迹或 restart 应对应最后有效状态。
- [ ] 令原子实际跨过分区边界，核对每个全局 ID 恰有一个负责进程。与单卡基线比较能量、压力及 virial 的唯一计数，核对 rank 0 写出的全局 ID 顺序、列、单位、帧数，以及无重复写入。
- [ ] 用冻结的跨 rank 重启用例，在不同卡数组合间读写，包括增加和减少卡数。复核元素、质量、坐标、速度、盒与首个输出，并与从同一物理快照、同一新作业参数启动的对照比较。
- [ ] 核对 NHC 链及 BDP/SCR 随机状态按新作业重新初始化。保存跨分区与变盒的日志、输出差异、容差、通过项和故障案例的退出码，使审阅者能复查停止边界。

## 依据

[V1.0 验收规格，用户故事 2、5、6、7、13](../../v1-release-readiness/spec.md) · [局部域 NPT 合同](../../../docs/standards/domain-npt.md) · [MPI 域分解回归](../../../tests/mpi/README.md) · [恒温器与重启回归](../../../tests/thermostats/README.md) · [决策 04 区组计时与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer)
