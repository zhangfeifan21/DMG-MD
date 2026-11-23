# 06: 普通 GPUMD 同卡数 NPT 先导

Status: ready-for-agent

Blocked by: None (can start immediately)

## 交付内容

用短的公开碳、水、铜输入，证明普通 GPUMD 在给定的卡数上确实执行所声明的 NEP 和控压路径，并能产出与 DMG-MD 对照的物理量。

先导可从现有短输入开始。引擎来源决策记录本引擎经过实测的身份核验方法及可比条件。

## 验收条件

- [ ] 记录当次普通 GPUMD 的版本、可执行文件、可得的构建配置、GPU 型号与势文件。说明其短程数值 golden 和同卡数性能记录各自的输入与用途。
- [ ] 从实际作业分别记录 P=1、2、4 的设备身份、单进程配置及运行日志。P>1 提供多卡 NEP 求力确实分派到 P 张 GPU 的证据。仅有可见设备清单的记录判为未核实。
- [ ] 对三材料逐项检查 `npt_scr`、`npt_ber`、势头与元素映射、盒资格、温压与阻尼及输出。作同快照静态比较和对应短段，保留模型不支持、显存不足或未执行项的依据。
- [ ] 生成按材料、控压器、P 检索的先导清单，给出可比、不可比或待验证及证据。未启用所选求力后端或实际卡数错误的作业不纳入对照，正式采样可按冻结快照复用这一入口。

## 依据

[V1.0 验收规格，用户故事 8、19、21、22](../../v1-release-readiness/spec.md) · [决策 06 同卡数引擎来源与 NPT 可比性](../../v1-release-readiness/issues/06-three-engine-provenance.md) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [跨引擎来源、物理量与单位约定](../../../docs/standards/cross-engine-reference.md)
