# 20: 恢复四卡 10,000 步 nightly

Status: ready-for-agent

Blocked by: 04

直接前置工单为[04 统一长短程参考并恢复 100 步 smoke](04-long-reference-smoke.md)。

## 交付内容

在现有四卡 L20 上，维护者能从冻结的长期初态重跑 nightly 并判断正确性是否回归。

## 验收条件

- [ ] 使用长程清单的碳、致密水、BaTiO₃-ZBL 三个体系、种子 0 和对应 nightly 几何。核对参考、候选、输入哈希和 MPI/GPU 探针，再分别运行 NVE 与 `nvt_ber` 各 10,000 步。
- [ ] 覆盖 P=1、2、4 和 HostStaged、CudaAware，核对清单所要求的单卡全体系、各多卡局部分派与每个原子唯一归属。记录每项的完成步数、输出和退出码。
- [ ] 按既有清单审核能量漂移、热力学/结构统计、双向同构型回放及相应跨 rank restart，并执行清单中的短程兼容派生例。保留原始检查点和汇总，失败项能按同一冻结输入独立复跑。
- [ ] 交付完整 nightly 覆盖表，列出版本、用卡、后端和阈值。只有该 profile 的要求都通过才作为进入完整 release 的依据。

## 依据

[V1.0 验收规格，用户故事 2、6、7、13、19、20](../../v1-release-readiness/spec.md) · [长程 profile 与矩阵](../../../tests/long_nve/README.md) · [冻结 golden 与误差约定](../../../docs/standards/golden-test-standard.md)
