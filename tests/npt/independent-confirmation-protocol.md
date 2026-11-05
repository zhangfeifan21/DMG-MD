# NPT SCR 独立确认注册

**状态：APPROVED_INDEPENDENT_CONFIRMATION。** 本独立确认方案已由 root 于 2026-11-03 约 07:33 UTC 批准，适用于原 B3 矩阵完成且保留原结论为 INCONCLUSIVE 后的确认。新采样须严格使用本文件与唯一 manifest；GPU 作业需在 b0 生成正式 plan/ledger、b1 生成正式 execution freeze，并由 root 核验该 freeze 后启动。该注册批准不替代 execution freeze 核验。

机器参数在 [independent-confirmation-manifest.json](independent-confirmation-manifest.json)；SHA-256：`aa9d1f9ae2cabbb174fa8fb30b0798edb5f9035541763d2c7f7f6b44d3fc1580`。该 manifest 标记 `APPROVED_INDEPENDENT_CONFIRMATION` 且 `sampling_authorized=true`；具体 GPU 作业仍须等待正式 execution freeze 经 root 核验。

## 研究设计

确认样本使用预先固定的 seed `201–232`，每个 seed 生成相同规则的 216 原子 diamond 初态，candidate 与 GPUMD 对应相同 `model.xyz` 字节。将 32 个初态 seed cluster 视为从同一生成总体抽取的独立、可交换 cluster；统计外推仅限这一生成总体和锁定模型/系综参数，不代表普遍遍历性。bridge 使用事先指定子集 `201–208`；它与 primary 的 P1 数据共享，因此**不假设组件统计独立**，只依赖 Bonferroni 联合覆盖界。

| Cohort | 采样 | 配对用途 |
| --- | --- | --- |
| `primary_base` | P1 HostStaged 与 GPUMD，各 seed 201–232 单段 160,000 步，`dt=0.25 fs`、`Tdamp=40`、`tau_p=200` | 直接估计 P1−GPUMD；也是 base MPI bridge 的 P1 anchor |
| `primary_half` | P1 HostStaged 与 GPUMD，各 seed 201–232 单段 320,000 步，`dt=0.125 fs`、`Tdamp=80`、`tau_p=400` | 独立半步长直接估计 P1−GPUMD；保持相同物理耦合时间 |
| `bridge_base` | P2/P4 × HostStaged/CudaAware 各 seed 201–208 单段 160,000 步，base dt | 配对 `primary_base` 同 seed 的 P1 HostStaged 轨迹；不再跑 P1 anchor，不设 half-dt MPI bridge |

所有作业保持 300 K、0 GPa、输入控制模量 425 GPa、NEP4 C 势、thermo 每步、无 per-step XYZ。P1、P2、P4 使用同一候选 binary；candidate 随机数环境变量为 `DMGMD_ENSEMBLE_SEED=<seed>`，每 rank 字符串相同。每 profile 每 seed 均从完整输入单段启动；不续跑、不拼接、不重用原 B3 轨迹。候选、参考输入/二进制/thermo 的路径与 SHA、步数、时间、PID、设备、rank、backend、units、列合同、成功状态都写入严格 schema 的 records。

GPUMD reference 每个 seed/profile 是独立 release SCR 作业，使用与 candidate 相同的显式初态、run.in、potential 字节。release 内部 RNG 来自 `system_clock`，不声称被 model seed 控制或已观测其真实 seed；GPUMD 和 candidate 之间不作逐步 RNG 配对。对同一 dt 的 GPUMD 32 条链分别供该 profile 的 primary 和所有 bridge 结果共用，避免每种 rank/backend 重复生成 reference。

## MPI–P1 bridge 的使用前提

bridge 只有在采样前源审计证明以下事实时才有效：候选二进制完全相同；同 seed 的 P1/MPI 初态及 run/potential SHA 相同；每一步调用顺序和时间轴一致；SCR 随机状态由 rank 0 以相同 seed 创建，每步 Gaussian draw 次数/顺序相同，计算出的 cell/thermostat 因子按相同阶段广播给各 rank。代码审计入口为 `src/ensemble_control.cpp::EnsembleController::barostat_scale`、`src/runtime_internal.hpp::ensemble_box_factor` 以及 P1/M1 replicated step loop；M2a local-domain 路径明确拒绝 NPT。bridge 是**共享随机流和同初态的配对差**；若这些条件不能证明，bridge 不可用于通过结论，必须另行预注册直接 MPI–GPUMD 确认，本方案不自动回退。

已有旧 pilot 的同 seed 80k 路径诊断显示，所核对 MPI/P1 配对最大派生差为 T 0.00473933 K、KE 0.000132323 eV、PE 0.0003596 eV、P 0.0001469791 GPa、V 0.0000308661 Å³（见修正列映射报告 `mpi-vs-p1-mapped-diagnostic-current.json`，SHA `8e84b52c…4534ca`）。这只是有限旧 seed 的数值路径诊断，不构成样本总体的 PASS，也不能替代 bridge 置信区间。

## 估计量、覆盖与门槛

冻结 primary analyzer `tests/npt/analyze_statistics.py` 只在独立 manifest 副本下运行；阈值、点估计、统计 helper、20,000 次 variance bootstrap、bootstrap seed、burn、blocks 和 ESS 规则沿原注册不变。primary 与 paired bridge 的每个组件区间分别使用 `z=2.64`（two-sided `alpha=0.008290602722072082`）。原 `z=2.394` 对应 `alpha=0.016665748791226766`；Bonferroni 对两组件求和为 `0.016581205444144164`；若两个近似 Student-t/bootstrap 组件区间达到其标称覆盖，则复合比较的标称覆盖至少 `98.341879%`，略强于原单组件的标称 `98.333425%`。该组合不要求组件独立，但不构成有限样本实际覆盖率保证。该保证针对每个指标/注册对比；不宣称全矩阵 joint 95%。

- 均值目标总体相同：`E[MPI−GPUMD] = E[P1−GPUMD] + E[MPI−P1]`，其中期望均针对同一 seed 初态分布。primary P1−GPUMD 与 bridge MPI−P1 各自产生合法区间后，按端点相加。P1 的 32-seed primary 与 8-seed bridge subset 重叠，不做独立方差合成；Bonferroni 仍适用。
- 方差目标总体也相同：`Var(MPI)/Var(GPUMD) = [Var(P1)/Var(GPUMD)] × [Var(MPI)/Var(P1)]`。primary/bridge 两组正值 ratio CI 的最终区间为 `[Lprimary × Lbridge, Uprimary × Ubridge]`。primary 沿用冻结的 ratio-of-global-unbiased-variances 与分 engine block bootstrap；bridge 对 seed clusters 重采样后，在每个重复 seed 内以同一 chronological block index 成对重采样 MPI/P1 blocks，再合成每个 engine 的全局无偏方差比。不得平均局部 block 方差或比值。
- 体积容差基于 primary 的 GPUMD full-32 pooled mean，保持 `±0.25%`，不由 bridge 子集重定义。
- V 分布门用确定的经验三角不等式：`D(MPI8,P1_same8) + D(P1_same8,P1_full32) + D(P1_full32,GPUMD_full32) <= 0.15`。中间 subset→full32 项必须保留；所有 ECDF 均由相同 burn 后注册序列 pooled 计算。这是有限观测 ECDF 的精确上界，不是 population CDF 的置信区间。
- T/P/V 均值复合 CI 必须完整落入原界：T `[-6,+6] K`、P `[-0.1,+0.1] GPa`、V `±0.25%` GPUMD full-32 均值。复合 variance CI 必须完整落入 `[0.85,1.15]`；V empirical CDF triangle sum `<=0.15` 时通过该有限样本上界门；sum 大于 0.15 只表示上界未能证明门槛，应判 INCONCLUSIVE，不能据此宣称真实 MPI–GPUMD ECDF sup 超界。均值/方差 CI 窄且与界分离时判 FAIL；跨界或精度不足判 INCONCLUSIVE。任何一项 FAIL/INCONCLUSIVE 不得写成 PASS。
- 每 profile、engine、metric pooled ESS（mean 与 squared deviation）至少 1000；每 seed 至少 32 个完整 125 fs blocks；禁止不完整 block tail。ESS 是 across-seed pooled 门，不是每条 seed chain 各自 1000。
- 每个输入 profile 只有单段单最终 look；base/half 各自独立参考链。原研究所有失败、INCONCLUSIVE 和执行偏差保留，confirmation 只形成独立证据，不合并样本或覆盖旧状态。理论 target 偏差继续单列；不宣称普遍严格采样。

## Pilot-only 功效估算

可复跑脚本 `/tmp/dmgmd-b0-b4-20261103/npt-b3/pilot_power_simulation.py`，SHA `fb2afa470ebbbbfb0c5cd055b9627e39a956ca1369e899e7d12b100d4698b86f`；机器结果 `/tmp/dmgmd-b0-b4-20261103/npt-b3/pilot-power-simulation.json` SHA `bdea5d0f1990f03d3c17527b6c05507674366a86789c6c5349a05692a4594a53`，stdout log 同目录同名 `.log` 且 SHA 相同。脚本只读取并校验已完成旧 pilot 的原始记录。输入为 base P1 80k records SHA `bca58c05…f16187d` 和 half P1 160k records SHA `1d8de57d…42fada`，未使用任何新确认数据，也不按结果挑 seed。重采样方案和 PRNG seed `2026110303` 固定：每 profile 的八个 seed-level candidate−GPUMD 均值差先中心化到真差为 0，再对八个 seed clusters 有放回抽 32 个，保守保留旧 seed dispersion；block-SE 按有效总块数 8 倍投影，seed t 区间用df=31的t临界值，block部分用相应block自由度t临界值，并取较宽者。Variance宽度另按`2.64/2.394`调整组件置信度。50,000 次模拟得到：

| Profile | 指标 | 真差为 0 时模拟均值 CI PASS 比例 |
| --- | --- | ---: |
| base 32×160k | T / P / V | 100.0% / 95.798% / 100.0% |
| half 32×320k | T / P / V | 100.0% / 93.828% / 100.0% |

在等 variance ratio=1 附近，将旧z=2.394的pilot log-ratio区间按新组件z=2.64/2.394放大，再按总有效block exposure 8倍缩放，预计压力 variance log-CI 半宽 base/half 约 `0.04176/0.04584`，体积约 `0.04148/0.04524`，均显著小于原 ratio 界附近的 log margin；这是近似投影，不是完整 bootstrap power guarantee。Pilot 的 pooled V CDF sup base/half 为 `0.04810/0.00646`，但**没有**模拟新 32-seed bridge 的三段 triangle pass 概率。

这些功效数字条件于仅 8 个旧 seed 能代表新 seed/block dispersion；50,000次模拟的二项Monte Carlo标准误约0.09个百分点（base压力）和0.11个百分点（half压力）。T/V的50,000次零失败只表示在零失败场景下，按单侧95%“三除以样本数”近似，失败率上界约0.006%；这不是对真实pass率的保证。小pilot下seed/block dispersion估计仍不确定。更关键地，已观测 base 80k pressure 点估计 `+0.13563 GPa`，CI `[+0.00068,+0.27058]` 并不接近零。上表通过率是假定真实差为零的设计功效，不是现有数据的预测或 PASS 证据。若新确认仍跨界或偏离，停止并保留 INCONCLUSIVE/FAIL，调查原因，不无限追加预算。

## 采样前注册门

1. 原 B3 八 seed 直接 GPUMD 矩阵完整结束并报告；原 20/40/80k与half looks保留，包含20k rerun prefix不逐字节一致的已知执行偏差，不把rerun链冒充同一条旧链。
2. 主审批准本 manifest、协议、配对 analyzer 与语义测试；分析实现及 `tests/thermostats/sample_statistics.py`、native ACF helper 的所有传递依赖均记 SHA。现有 primary analyzer SHA `da0b26270c5c9330deec1b30c96179d516a5b0c2f95b973e6431cd2e77520496`，统计 helper SHA `0b85ecee1b7ec7aee19d5b350ec5bdc7a5e23d635874843e68a50aa5faee6b67`，ACF helper SHA `ffa85c62195c6886c40510024ffd60ecea30531fc0851242208f0fc796db9761`；paired CLI与integration实现已root验收，14项CPU语义/集成测试通过（log `/tmp/paired-confirmation-cpu-tests.log`，SHA `fbde680e1301f93910ee988745a12a7f66acab9c1f32404faaf51d188afb6298`）。manifest另列analysis dependency SHA（primary analyzer、sample_statistics helper、native ACF、paired math/test、candidate/reference binary、source digest、bridge RNG source audit与NEP）；最终采样前仍须复核整份冻结身份。
3. 重新确认 candidate `dmg-md` SHA `c3c9cd0b81a3f18ebbd8f4379dfbd3dbf929785eb5aa883fa4730b025d70bcdf`、reference GPUMD SHA `3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`、锁定 source digest SHA `52069b5aca1dc5d3d7b9a674041e814ef3898eb9a6f93d15d6c38653ff5b1c18` 与 NEP SHA `add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623`。任一身份改变须重新审查并重冻注册。
4. 独立输入生成器核对 seed 201–232 的所有模型 SHA 与 manifest，run.in SHA 与 base/half 注册参数一致；程序拒绝漏 seed、重复、多余 run、profile 混杂、step/unit/column/hash/status不符及非有限轨迹。
5. MPI bridge 源审计确认 rank-0 RNG所有权、每步 draw 次数/顺序、所有 rank 环境 seed 一致检查与 factor 广播的调用路径。无法证明则不执行本配对确认方案；须另提直接 reference 设计。
6. 该注册、paired analyzer/tests、reference proof、binary/source identity 与 CPU tests 已获 root 批准；b0/b1 仍须针对唯一 manifest 生成正式 plan/ledger/execution freeze，并由 root 核验所有 hashes 后才可启动 GPU 数据生成。

截至本注册批准时，尚未开始新的研究数据采样；等待正式 execution freeze 经 root 核验后再执行。


## 桥接 RNG 源审计

当前候选审计记录为 [bridge-rng-source-audit.json](bridge-rng-source-audit.json)，SHA-256 `44d80eae0a60cf52ef72ad0f1f396a598129b6d4dd8f6c38ac459025442c8d4e`，已由 root 在 2026-11-03 06:51 UTC 接受静态源审计前提；该认可只覆盖 RNG 配对前提。整份独立确认注册随后于约 07:33 UTC 获批，但 GPU 作业仍须等正式 execution freeze 经 root 核验。它锁定 M1 路径、根进程 SvrState、每步 BDP 后 SCR draw 顺序、MPI seed 一致检查与因子广播。相同初始 SvrState、seed、N、时间步和二进制时，各条件消耗同一伪随机流；rejection sampler 的分支由该流决定，不依赖 thermo/速度值。未直接采集逐步 RNG trace，因此桥接不声称 thermo、box factor 或轨迹逐位相同。P2/P4改变归约舍入可能改变状态和缩放因子，因此桥接只依据共享 RNG 调用流，不声称轨迹、thermo 或因子逐位相同。
