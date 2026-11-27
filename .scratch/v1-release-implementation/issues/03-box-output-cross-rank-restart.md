# 03: 验证变盒、输出与跨卡重启

Status: completed-by-agent

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

## Comments

- 2026-11-27，Codex 已认领。先用空闲 GPU 验证单卡和双卡，四卡批次等待 01 释放设备。

- 2026-11-27，维护者确认代码，五项验收完成。单节点 L20 上，双卡和四卡各通过 42 个变盒迁移作业、8 个启动拒绝与积分停止用例。显式局部作业覆盖两种通信后端及 `rebuild`、`affine`。过窄初始盒在写文件前退出，诊断中的单卡命令可直接运行。收缩用例的第 3 步在提交前停止，三份输出与独立两步作业逐字节一致。
- 实际跨界后，每个全局 ID 仍有唯一负责进程。逐帧能量、压力和 virial 通过单卡对照，只有 rank 0 写科学输出。1、2、4 卡间六种增减方向共 64/64 个重启目标通过，NHC、BDP 和 SCR 均按新作业重置。[验收数值与构建身份](../../../docs/status/validation/domain-npt-validation.md#local-box-restart-20261127) · [复跑命令](../../../tests/npt/README.md#local-box-restart)
- L20 的日志、原始输出、冻结清单及差异保存在 `~/DMG-MD-Project/scratch/ticket03-20261127/`。在 `3e8f1c5` 上独立复建后，四卡停止用例及 44/44 项 CTest 通过。实现和结单记录同次提交，可用 `git log -1 --format=%H --grep='^验证局部 NPT 变盒、输出与跨卡重启$'` 查询。
