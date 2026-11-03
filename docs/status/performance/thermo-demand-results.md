# 按需执行 NVE thermo 实测

目标：保存 O8 按需执行 M2a NVE thermo 的消费者证据、调用计数、MPI/nightly 验收、配对性能和边界。

| 日期 | 阶段 | 结论 | 适用范围与限制 |
| --- | --- | --- | --- |
| 2026-10-23 至 2026-10-26 | [O8 按需 NVE thermo](#o8-results-20261026-report) | 实际调用计数和科学文件 differential 292/292 通过；完成 MPI/nightly 与 105 个正式 trial。 | 增量结果按四组分别为 INCONCLUSIVE 或 SPEEDUP；只适用于所列输入、输出节奏、绑定、GPU、后端和本机环境。 |

以下各节按来源报告分别保留原始身份、环境、实际命令、统计数据、失败与未覆盖边界及原始产物路径；标题层级仅为嵌入整合文档而调整。

<a id="o8-results-20261026-report"></a>

<a id="o8-results-20261026-o8按需执行-m2a-nve-thermo2026-10-2326"></a>

## O8：按需执行 M2a NVE thermo（2026-10-23–26）

类别：实现、数值回归与独立三版本配对实测。状态：**完成；采用按需 NVE thermo，收益限于冻结范围**。
本页仅记录 O8；O9–O11 未实施。生产仍为 HostStaged/full、overlap=off。

<a id="o8-results-20261026-实现与消费者证据"></a>

### 实现与消费者证据

`src/domain_runtime.cu::run_domain_segment` 在 owned VV2 后，仅当 NVE 的本步
`dump_thermo` 或任一 `dump_xyz` 到期时调用原 `compute_domain_thermo`。
NVT 继续每步计算并保持缩放前 thermo 的原有反馈/输出时序。
消费者、步号、多 run/restart 及就绪证明见[现行合同](../../standards/thermo-demand.md)。

审计从 `run_local_domain` 的命令分发、measurement 生命周期及步循环追至
`write_thermo_row`、`write_xyz`、`write_restart`、`gather_domain_snapshot`、
`adaptive_domain_time_step` 和 `correct_domain_velocity`。XYZ 无条件输出头部
energy/stress，不能只按是否请求逐原子 energy 决定；restart/snapshot 本身没有 thermo 读者。
每步使用新的 optional，消费者用 `value()` 读取，不复用前步、前段或默认零值。

所有 rank 根据同一输入决定执行相同 collective，不按 owned 数量或 root 身份分支。
有需求时 sums/SUM/归一化/D2H 及数值顺序不变；无需求时完整删除这些辅助工作。
NEP 数学、launch 参数、SoA 布局、所有权、halo、重建/迁移、积分和用户输出均保持原合同。
M1/P1 保持原每步 thermo 路径。

详细计时原先靠每步 thermo D2H 使 event 就绪。O8 将诊断 event 按步保存，到原有段末
device synchronization 后读取，不引入新的逐步等待。该诊断使用 O(段步数) 资源，
且旧/新 trace 输出时序不同，其 CPU 差值包含插桩影响，不作为纯 thermo 成本。
正式性能关闭这些诊断。`thermo_host` 含前序 GPU 等待，不能预测可节省时间。

<a id="o8-results-20261026-身份资源与冻结设计"></a>

### 身份、资源与冻结设计

起始 HEAD：`cf6d098e3f12b25fddafb62cc3e95365e8f87425`。
起始工作树含 O6/O7 的未提交代码、工具和文档；完整 patch、状态和逐文件副本保存在
独立目录 `dmgmd-overlap-o8-20261023/initial-files/` 与 `initial.patch`。
O8 单独的 runtime 差异保存在 `o8-only.patch`，没有覆盖 O5–O7 样本或提交 Git。

| 二进制 | SHA-256 |
| --- | --- |
| 原 full | `860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6` |
| O7 full | `61b12e93eac137538e75b2e6c5cfcbccbab11ca75084ee7fc025f48b86bf2d17` |
| O8 full | `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32` |

环境唯一入口为 `source ../env/md-mpi.sh`。沿用本机八张 RTX 4090、固定 GPU UUID
前缀与 K=4 物理核/rank、含 SMT 的八个 OS CPU；各 rank 不共享物理核。
实际 CPU affinity 和 rank→GPU UUID 每 trial 独立 audit；库、输入和环境哈希保存于
`frozen-plan.json`，机器和遥测另存。不具备锁频、独占预约或内存物理页驻留证明。

实施前 `frozen-design.json` 固定三个版本、两个 scope、两后端、五个独立三版本块及
全部随机顺序，seed=2026102308。选择 carbon/P2 覆盖 radial 主损失配置，water/P8
覆盖最高 rank 的 water；本轮不外推 P4、其它输入或 overlap 的采用决策。

| 输入 / ranks | 原子数 | warmup | 正式步数 |
| --- | ---: | ---: | ---: |
| carbon_200k / P2 | 196608 | 200 | 1500 |
| water_400k / P8 | 393216 | 1600 | 4300 |

预定 60 个正式 trial，实际按规则追加 45 个，共 105 个；每段至少 10 s，目标 20–30 s。
三个对比均使用中位数之比：
原/O7 分离 O7，O7/O8 分离 O8，原/O8 给组合效果；95% 区间按独立三版本块
bootstrap 20000 次，seed=2026102309。rank 与步不作为独立重复。
任一对比区间宽度/估计值超过 10% 时，仅对该 scope/backend 追加五个完整三版本块，
最多一次。失败、无效与慢样本原样保留，不混入 pilot、科学或诊断样本。

<a id="o8-results-20261026-验收与实际调用次数"></a>

### 验收与实际调用次数

八 rank 环境预检、committed golden 4/4、CPU CTest 14/14 已通过。
实际调用计数与科学文件 differential **292/292** 作业通过；MPI 主矩阵 **68/68**、
共同 P1/fallback、多 run、timing A/B、重建 schema 与 multi-box fatal focused checks 通过。
所有主配置均检查逐原子数值、owner/layout、迁移与逐步精确通信量。
nightly 子矩阵 **18/18** 与跨 rank restart **12/12** 通过：三个基础体系、seed 0、
P1/P2/P4、两后端，包含 100-step 严格短轨迹、10000-step NVE 和 NVT。
本轮没有运行 replay、其余派生 case 或 100000-step release。
三版本 × 两实际输入 scope × 两后端 × full/serial/wait/poll 的 **48/48** smoke
通过，thermo/neighbor 科学文件逐字节一致。

独立 PMPI 计数与 runtime 的逐段计数一致，各成功对照的科学文件逐字节相同。
示例为每 rank 的实际 8-double SUM 次数，不把 rank 当独立重复：

| 用例 | O7 | O8 |
| --- | ---: | ---: |
| 无输出 NVE / 7 步 | 7 | 0 |
| thermo 周期 3 / 8 步 | 8 | 2 |
| XYZ-only 周期 2 / 7 步 | 7 | 3 |
| thermo/两个 XYZ 互素周期 / 13 步 | 13 | 8 |
| 多 run 与 NVE/NVT 切换 / 23 步 | 23 | 9 |
| NVT 稀疏输出 / 7 步 | 7 | 7 |
| restart-only / 5 步 | 5 | 0 |
| adaptive timestep/修速 / 13 步 | 13 | 6 |
| 静默前段及重建/迁移 / 11 步 | 11 | 4 |
| 空 local rank / 1000 步 | 1000 | 0 |
| P1/M1 fallback / 7 步 | 7 | 7 |

P2/P4 覆盖两后端、full/serial/wait/poll 和独立详细计时；P8 另覆盖静默、互素输出、
多 run、空 rank、NVT。cache reason OR 仍恰好每 M2a 步一次，空 rank 也不跳过；
1000-step empty-local 继续覆盖周期 neighbor 输出。跨 rank restart 读取的模型与输出逐字节一致。

第一次 differential 的 `correct_velocity 2` 被原 O7 正确拒绝：合同最小间隔为 10。
该失败目录和原日志保存在 `thermo-demand.failed-input*`；改为 10 后从新目录重跑。
迁移 fixture 使用两颗保持间距的无力原子，确保静默前段真实发生 skin rebuild 与
slab/周期迁移，而不依赖长时间演化下不受约束的大初速碰撞。

网络中断前已保存 224 个成功记录，未进入正式性能。恢复时校验这些科学文件哈希，
从独立 `thermo-demand-continuation/` 补齐 68 个作业；旧 partial input 及记录保留。
恢复过程还发现小盒 fixture 仅缩盒而未调整粒子分布导致原子重合，原 O7 因 ownership
错误退出；改用两颗不重合的孤立原子后通过。失败目录与日志保存在
`thermo-demand-continuation.failed-small-box*`，没有修改生产二进制或放宽门槛。

<a id="o8-results-20261026-配对性能与-cpu-诊断"></a>

### 配对性能与 CPU 诊断

首批 **60/60** 正式 trial 已完成；第五块保留了高主机负载时的慢样本。carbon/CudaAware、
water/HostStaged 与 water/CudaAware 触发预定的区间宽度追加规则，2026-10-26 已冻结
45 个新 trial（各五个完整三版本块，seed=2026102608）；不删除原慢样本。
追加 **45/45** 通过，合计 **105/105** 有效，正式段 **22.574–70.225 s**，CPU 全部为 null。
没有按速度删样本或更换输入/资源；原 60 个样本未覆盖。最终全部区间宽度满足冻结规则。

单位 ms/step，数值为独立进程的中位数。carbon/HostStaged 为五个三版本块，另外三组为
十个块；区间是按块配对的 pointwise 95% bootstrap，未作多比较的 familywise 校正。

| 配置 | 原 full | O7 full | O8 full | O7/O8，95% CI | 原/O8，95% CI |
| --- | ---: | ---: | ---: | --- | --- |
| carbon/P2/HostStaged | 15.741051 | 15.673729 | 15.535244 | 1.008914 [0.997161, 1.012303] | 1.013248 [1.005161, 1.018863] |
| carbon/P2/CudaAware | 16.449213 | 16.520948 | 16.208842 | 1.019255 [1.007947, 1.021941] | 1.014830 [1.003567, 1.023246] |
| water/P8/HostStaged | 5.524865 | 5.492866 | 5.343124 | 1.028025 [1.021921, 1.036003] | 1.034014 [1.028695, 1.038989] |
| water/P8/CudaAware | 6.436507 | 6.431452 | 6.290882 | 1.022345 [1.016248, 1.027546] | 1.023149 [1.021090, 1.024853] |

同一批数据的原/O7 对照分别为：

| 配置 | 原/O7，95% CI |
| --- | --- |
| carbon/P2/HostStaged | 1.004295 [1.000248, 1.008646] |
| carbon/P2/CudaAware | 0.995658 [0.988013, 1.006295] |
| water/P8/HostStaged | 1.005826 [1.000063, 1.013092] |
| water/P8/CudaAware | 1.000786 [0.996652, 1.006802] |

O8 相对 O7 的增量：carbon/HostStaged 为 **INCONCLUSIVE**，另三组为 **SPEEDUP**，
加速比约 1.019–1.028；没有统计显著退化。O7+O8 相对原 full 的四个 pointwise 区间
均高于 1。按冻结采用条件采用该最小实现；不把本轮 O7 对照替代或混入旧 O7 的独立实验。
该结论只适用于表中精确输入、输出节奏、K=4 绑定、GPU/后端与本机环境。
不外推频繁 XYZ/每步 thermo、P4、其它势/盒形、设备或跨节点，也不重新决定 overlap 的采用。
默认保持 HostStaged/full、overlap=off。

独立 CPU 诊断 **8/8** 通过 collector、PID/start/clock marker、绑定与科学文件检查。
每配置每版本一个独立诊断 trial，正式 run marker 的逐 rank 覆盖率至少 **97.06%**。
下表为各 rank 自己 marker 窗内观测 CPU 秒数之和除以步数，单位 CPU ms/step；
不是主性能样本 CPU，也不是同步峰值或纯 thermo 成本。

| 配置 | O7 | O8 |
| --- | ---: | ---: |
| carbon/P2/HostStaged | 31.114286 | 31.000000 |
| carbon/P2/CudaAware | 32.728571 | 32.600000 |
| water/P8/HostStaged | 43.011111 | 42.072222 |
| water/P8/CudaAware | 51.916667 | 50.416667 |

各 rank 平均 core-equivalent 的和，P2 约 2.00–2.02，P8 约 8.04–8.06；不同 rank 的
观测窗口不完全重合。单次诊断、采样尾部及旧/新 event/trace 插桩差异限制了 CPU 差值解释，
不宣称稳定 CPU 降幅，不把这些值相加来拆账主墙钟或推导网络传输成本。

首个 CPU 诊断遇到 MPI 合并 stdout 交错，PID/clock marker 校验正确拒绝该日志。
失败原始数据保存在 `diagnostic-failed-interleaving/`；该旧 result 的退出/科学 PASS
不构成 CPU 验收，其 error 与缺失 CPU status 保留。采集器修正为仅在 diagnostics
阶段使用既有逐 rank 文件适配器，并明确拒绝带 error 或缺 CPU status 的结果。
修正前源码和 frozen plan 独立保留，AST 检查证明仅 `freeze`（新增适配器哈希）与
`diagnostics` 两个函数改变；主 trial、输入、绑定、二进制、统计函数及已有 60 个样本不变。
修正证据见 `diagnostic-collector-amendment.json`，追加计划见 `extension-plan.json`。
修正后八个诊断均使用完整 marker；未修复、拼接猜测或复用交错日志中的时间戳。

<a id="o8-results-20261026-重算与原始证据"></a>

### 重算与原始证据

```text
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o8.py analyze --root dmgmd-overlap-o8-20261023
python3 dmgmd-overlap-o8-20261023/summarize-diagnostics.py
```

精确门槛和运行顺序保存在独立目录的 `gate-commands.sh`、
`remaining-gates-and-main.sh`、`continue-after-network.sh` 与 `extension-and-diagnostics.sh`。
各 trial 的 `launch.json`、原始 stdout/stderr、输入/科学文件哈希、CPU 绑定 audit、
GPU/CPU 遥测与结果 JSON/CSV 均保留；主 trial 保存 launcher 合并输出，修正后的 CPU
诊断另有逐 rank 文件与派生拼接 provenance。`run-frozen-command.py` 在 source 同一
环境入口后复用冻结的受控环境，保留旧 PATH 中的非工具链目录而不切换 MPI/CUDA 库。
最终源码、相对起始 dirty tree 的 O8 patch、完整工作树 patch、逐文件哈希及三个只读
reference 的前后清单由 `snapshot-final.py` 保存；原始门槛、慢样本及失败尝试均保留。
此记录不代表 100000-step release、跨节点、其它设备或输入范围的验收。
