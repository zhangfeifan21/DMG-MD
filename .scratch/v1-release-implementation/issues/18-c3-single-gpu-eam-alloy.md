# 18: C3 交付单卡 EAM/alloy

Status: ready-for-agent

Blocked by: 17

直接前置工单为[17 C2 交付单卡 LJ](17-c2-single-gpu-lj.md)。

## 交付内容

用户能用 GPUMD 三行包装头与 setfl 势表在单卡模拟纯 Cu 与 CuNi，并在数据损坏或邻居不能完整存储时取得明确诊断。按既定 C2→C3 次序集成。

## 验收条件

- [ ] 核对三行包装头、setfl 网格与元素映射，用纯 Cu 和 CuNi 检查密度、嵌入能、对势、Hermite 插值及表边界。遵循锁定参考的精度与累加顺序。
- [ ] 两个材料分别完成静态逐原子能量、力、virial 和短轨迹对照，覆盖元素顺序、不同局部环境与变盒后邻居变化。附参考和候选身份、表哈希与冻结容差。
- [ ] 正反例覆盖缺失或截断的表、非法长度、容量与分配大小、邻居恰满及多出一个邻居。恰满结果须与参考相容，溢出作业须在使用残缺邻居求力前给出可识别的错误。
- [ ] 提供可单独重跑的单卡示例和科学输出。分开标识 Cu/CuNi 的 EAM 验证与 FCC 铜 NEP4-ZBL 性能工况。

## 依据

[V1.0 验收规格，用户故事 1、15、16、19](../../v1-release-readiness/spec.md) · [C0–C5 力场验收](../../../docs/plans/dmg-md-v1.0-development-workflow.md#力场开发卡) · [冻结 golden 与误差约定](../../../docs/standards/golden-test-standard.md)
