# 19: C5 验收势与六种系综的支持矩阵

Status: ready-for-agent

Blocked by: 03, 18

直接前置工单为[03 验证变盒、输出与跨卡重启](03-box-output-cross-rank-restart.md)、[18 C3 交付单卡 EAM/alloy](18-c3-single-gpu-eam-alloy.md)。

## 交付内容

用户能按势、系综、卡数和盒型查询 V1.0 的已验证组合，按示例启动并从 restart 作为新作业继续。

## 验收条件

- [ ] 分别对单卡 LJ、EAM/alloy 执行六种系综，共 12 个势与系综组合。覆盖 NVE、NVT Berendsen/NHC/BDP、NPT Berendsen/SCR。NPT 使用各向同性正交全周期盒。每格记录输入、退出码、实际输出和新作业重启结果。
- [ ] 对 NEP4、NEP5 和已声明的 ZBL 变体，按支持表逐一核对单卡与局部组合、实际分派、输出列、全局原子顺序及重启。给 LJ/EAM 等缺少局部实现的势发起多卡请求，验证首步和科学输出前集体拒绝与单卡重跑。
- [ ] 确定性量按冻结误差带对照。随机系综按同 seed 重复和独立种子注册统计判断。Berendsen 预平衡单列检查方向、稳定性和耗时。把盒型或势/系综限制记入相应行。
- [ ] 归档可机读或逐行复查的 G-C 支持矩阵、每种支持势的示例与证据位置，汇出失败及预期拒绝，所有声称支持的格均有通过记录。

## 依据

[V1.0 验收规格，用户故事 1、2、4、6、7、13、14、15、16、20](../../v1-release-readiness/spec.md) · [C0–C5 力场验收](../../../docs/plans/dmg-md-v1.0-development-workflow.md#力场开发卡) · [系综与重启合同](../../../docs/standards/ensembles.md) · [冻结 golden 与误差约定](../../../docs/standards/golden-test-standard.md)
