# O11：限定范围采用复验（2026-10-30 完成）

类别：独立性能复验与采用建议。测算状态：**COMPLETE_SCOPED**；判定：**SCOPED_MANUAL_USE**。
2026-10-29 批次于 2026-10-30 02:43:57（Asia/Shanghai）完成封存及离线复算：
31,293 项文件哈希通过，五份派生结果的 SHA-256 与封存值一致。
原始封存报告为产物目录中的 `report.md`；本页补充最终核验状态与结果解读。

**采用建议仅限 carbon_200k/P2 的 HostStaged 或 CudaAware B-wait**，相对当前
HostStaged/full 分别为 1.0210×、1.0199×。water/P8/HostStaged 点估计 1.0508×，
但校正区间 [0.9183, 1.2261] 跨过 1，独立 O8/full 桥接也未通过，故本轮不建议采用；
这不改写 O9 的历史结果。water/P8/CudaAware 未胜默认路径；water/P4 仅为探索支持。
核心范围没有确认 poll 相对 wait 的额外收益，几何 C 也没有确认额外收益。

生产默认保持 **HostStaged/full、overlap=off、schedule=split**。本轮未改生产代码、
NEP 数学、布局、所有权、通信协议或自动启用阈值，未提交或推送。

## 身份与正确性

起始 HEAD：`4457c3c1ff56e3c326508bf3075fa4ffcd7cf96c`。独立目录：`dmgmd-overlap-o11-20261029/`。
候选 SHA-256：`46e6b9f341a44aea67de79679e2e6e2bb75421a3f2be210357bae35b0cdf2e1d`；
独立 O8/full SHA-256：`dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32`。
本机 n4、8×RTX 4090，K=4 物理核/rank，固定 CPU/NUMA 与 rank→GPU UUID；
Open MPI/UCX 环境由 `source ../env/md-mpi.sh` 加载，精确版本见目录中的版本文件。
未独占、未锁频；物理内存驻留 UNKNOWN。实际 trial 均通过独立绑定核验。

两份二进制各通过新的 P8 环境预检与四个单 rank golden；80 个新真实输入短程科学
对照通过，正式同窗口的各版本/模式科学输出哈希一致。未声称本轮重新运行完整
nightly：与 O9 相同二进制的完整 MPI/调度/nightly/GPU 验收经历史封存核验后复用。
O6.5/O8.5/O8.6/O9 分别核验 9886/15788/33416/17415 项历史文件；旧样本不进入新统计。

首次 freeze 在采样前为减少共享输入重复哈希而终止；保留首次日志和 amendment，
优化后完整重跑核验。新工具三项测试覆盖矩阵、缺实验拒绝及共享输入变更检测。

## 预先冻结的范围与统计

本轮按计划允许的范围收缩，仅对 carbon_200k/P2 和 water_400k/P8 的双后端作
采用确认。water/P4、carbon_1m/P2、高重建/P2、薄 slab 空 early/P4 为探索或退化对照。
不声称覆盖原始三体系 × P1/2/4/8 全矩阵；缺少的 rank/体系组合不作采用建议。
O10 条件性后端实验暂缓，不是本轮前提。

预选 B-wait；核心保留 O8/full、candidate full、完整 split-serial、A/B/C serial/wait
及 B-poll，扩展范围保留 O8/full、candidate full、B-serial/wait/poll。
共 800 个新正式 trial、80 个 pilot、48 个独立 CPU 诊断；全部尝试保留，
不按性能删除慢样本。正式窗口范围 23.532378–142.983535 秒。
初始 400 个 trial 采集于 10 月 29 日 16:47:55–21:18:19，追加 400 个采集于
10 月 29 日 21:53:59 至 10 月 30 日 02:40:42（Asia/Shanghai）。

统计为配对块中位数之比、40000 次 bootstrap，种子 2026102915。
八项核心同后端/default 净收益使用 Bonferroni **99.375%** 区间；其余为探索性 95%。
初始五块，若主区间相对宽度超过 10%，按冻结规则追加一次五个完整全局块。
本轮追加块：是，最终每配置十个独立配对块。追加后 water 的部分区间仍宽；
冻结规则只允许一次追加，因此以剩余不确定性结束该范围，不继续扩样。
`analysis.json` 的 `extension_required=true` 记录当前宽度条件仍触发，
不表示还有第二次追加待执行；已有 `extension-plan.json` 且全部完成。
只在核心两项校正区间下界均大于 1，且独立 O8/default 的 95% 下界大于 1 时建议
手动 B-wait。poll、A/C 或扩展范围的新赢家不能用本轮数据同时选择并确认。

## 核心整步结果

单位 ms/step，中位数。A 为原 producer，B 为同 E/H 紧凑 producer，C 为几何内缩。

| 体系 / 后端 | O8 full | candidate full | split serial | A serial / wait | B serial / wait / poll | C serial / wait |
| --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 15.520793 | 15.537200 | 20.255268 | 16.173585 / 16.104595 | 15.280079 / 15.217966 / 15.221647 | 15.357731 / 15.279533 |
| carbon_200k/P2/CudaAware | 16.359752 | 16.332677 | 20.992142 | 16.890829 / 16.118973 | 16.111788 / 15.233333 / 15.231068 | 16.093916 / 15.332856 |
| water_400k/P8/HostStaged | 5.466128 | 5.522507 | 6.246812 | 5.472805 / 5.348445 | 5.478615 / 5.255480 / 5.268675 | 5.462321 / 5.256262 |
| water_400k/P8/CudaAware | 6.475053 | 6.472754 | 7.303465 | 6.586693 / 5.950187 | 6.499263 / 5.882400 / 5.791120 | 6.465824 / 5.885546 |

比值大于 1 表示 B-wait 更快；独立 O8 列为探索性 95% 桥接区间。

| 体系 / 后端 | 同后端 full / B | HostStaged full / B | O8 HostStaged full / B | 采用判定 |
| --- | --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 1.0210 [1.0136, 1.0282] | 1.0210 [1.0136, 1.0282] | 1.0199 [1.0153, 1.0232] | 限定范围手动使用 |
| carbon_200k/P2/CudaAware | 1.0722 [1.0580, 1.1070] | 1.0199 [1.0069, 1.0269] | 1.0189 [1.0092, 1.0230] | 限定范围手动使用 |
| water_400k/P8/HostStaged | 1.0508 [0.9183, 1.2261] | 1.0508 [0.9183, 1.2261] | 1.0401 [0.9807, 1.0427] | 不建议采用 |
| water_400k/P8/CudaAware | 1.1004 [1.0947, 1.1131] | 0.9388 [0.9168, 1.0953] | 0.9292 [0.9234, 0.9378] | 不建议采用 |

### 首轮不确定性与追加依据

首轮 400 个 trial 中，HostStaged/full 的少数慢样本使以下校正区间相对宽度超过 10%，
因此按冻结规则追加五个全局配对块。初始统计另存 initial-analysis/，全部慢样本
保留在最终统计中，未替换或删除；慢样本的具体硬件原因未确认。

| 体系 / 后端 / 对比 | 初始估计 | 初始校正区间 | 相对宽度 |
| --- | --- | --- | --- |
| carbon_200k/P2/HostStaged/same_backend_net | 1.0256 | [1.0165658206826913, 2.190564076391724] | 114.47% |
| carbon_200k/P2/HostStaged/default_net | 1.0256 | [1.0165658206826913, 2.190564076391724] | 114.47% |
| carbon_200k/P2/CudaAware/default_net | 1.0231 | [1.0068237799480284, 2.210446985986741] | 117.65% |
| water_400k/P8/HostStaged/same_backend_net | 1.0414 | [1.0336106112526826, 1.250505369264724] | 20.83% |
| water_400k/P8/HostStaged/default_net | 1.0414 | [1.0336106112526826, 1.250505369264724] | 20.83% |
| water_400k/P8/CudaAware/default_net | 0.9319 | [0.9262450206883035, 1.1190080564025773] | 20.68% |

追加块 5 的 water/P8/HostStaged full/B-wait 为 7.175671/6.126891 ms，
块 6 为 5.626325/7.859596 ms，均保留。这说明本轮不确定性不只来自 full 慢样本；
不能挑出较快窗口来恢复 O9 的采用范围，也不能据此断言算法退化或硬件原因。

## 分区、重叠与 poll 对照

以下为探索性 95% 区间，包含维护、执行和同步，不能当作纯网络隐藏时间。

| 体系 / 后端 | full / B-serial | B-serial / B-wait | B-wait / B-poll | A-wait / B-wait | B-wait / C-wait |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 1.0168 [1.0090, 1.0246] | 1.0041 [0.9974, 1.0120] | 0.9998 [0.9958, 1.0051] | 1.0583 [1.0531, 1.0611] | 0.9960 [0.9911, 1.0029] |
| carbon_200k/P2/CudaAware | 1.0137 [1.0079, 1.0290] | 1.0577 [1.0492, 1.0614] | 1.0001 [0.9150, 1.0120] | 1.0581 [1.0501, 1.0889] | 0.9935 [0.9886, 1.0057] |
| water_400k/P8/HostStaged | 1.0080 [0.9627, 1.0818] | 1.0425 [1.0191, 1.0492] | 0.9975 [0.9939, 1.0687] | 1.0177 [1.0030, 1.0203] | 0.9999 [0.8088, 1.0072] |
| water_400k/P8/CudaAware | 0.9959 [0.9905, 1.0035] | 1.1049 [1.0999, 1.1107] | 1.0158 [0.9923, 1.0371] | 1.0115 [1.0075, 1.0199] | 0.9995 [0.9921, 1.0008] |

## 扩展范围与退化对照

全部为探索性 95% 区间，不直接扩大推荐范围。

| 体系 / 后端 | 同后端 full / B | HostStaged full / B | B-serial / B-wait | B-wait / B-poll |
| --- | --- | --- | --- | --- |
| water_400k/P4/HostStaged | 1.0164 [1.0051, 1.0302] | 1.0164 [1.0051, 1.0302] | 1.0172 [1.0127, 1.0222] | 1.0012 [0.9949, 1.0097] |
| water_400k/P4/CudaAware | 1.1672 [1.1602, 1.1749] | 1.0324 [1.0221, 1.0455] | 1.1713 [1.1624, 1.1776] | 0.9979 [0.9897, 1.0038] |
| carbon_1m/P2/HostStaged | 0.9942 [0.9914, 1.0480] | 0.9942 [0.9914, 1.0480] | 1.0037 [1.0018, 1.0073] | 1.0011 [0.9977, 1.0050] |
| carbon_1m/P2/CudaAware | 1.0270 [1.0252, 1.0296] | 0.9963 [0.9935, 1.0507] | 1.0424 [1.0400, 1.0448] | 0.9996 [0.9973, 1.0009] |
| carbon_200k_drift/P2/HostStaged | 1.0000 [0.9887, 1.0093] | 1.0000 [0.9887, 1.0093] | 0.9996 [0.9954, 1.0102] | 0.9946 [0.8837, 1.0081] |
| carbon_200k_drift/P2/CudaAware | 0.9999 [0.9975, 1.0000] | 0.9932 [0.9806, 1.0032] | 1.0159 [1.0101, 1.0192] | 1.0013 [0.9987, 1.0091] |
| carbon_thin_empty/P4/HostStaged | 1.0051 [0.9925, 1.0170] | 1.0051 [0.9925, 1.0170] | 1.0082 [0.9899, 1.0132] | 0.9964 [0.9834, 1.0096] |
| carbon_thin_empty/P4/CudaAware | 1.0016 [0.9907, 1.0097] | 0.9568 [0.9453, 0.9675] | 1.0072 [0.9963, 1.0158] | 0.9974 [0.9924, 1.0066] |

薄 slab 的空 early/full fallback 不计作独立 overlap 成功证据；不从 E/D 比例推导通用阈值。

## CPU 与维护成本

| 体系 / 后端 | full CPU | serial CPU | wait CPU | poll CPU |
| --- | --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 1.0047 | 1.0044 | 1.0091 | 1.0047 |
| carbon_200k/P2/CudaAware | 1.0004 | 1.0000 | 1.0045 | 1.0092 |
| water_400k/P8/HostStaged | 1.0087 | 1.0045 | 1.0084 | 1.0048 |
| water_400k/P8/CudaAware | 1.0091 | 1.0089 | 1.0087 | 1.0088 |
| water_400k/P4/HostStaged | 1.0091 | 1.0046 | 1.0047 | 1.0045 |
| water_400k/P4/CudaAware | 1.0047 | 1.0005 | 1.0002 | 1.0000 |
| carbon_1m/P2/HostStaged | 1.0090 | 1.0045 | 1.0046 | 1.0046 |
| carbon_1m/P2/CudaAware | 1.0044 | 1.0049 | 1.0003 | 1.0003 |
| carbon_200k_drift/P2/HostStaged | 1.0048 | 1.0051 | 1.0004 | 1.0046 |
| carbon_200k_drift/P2/CudaAware | 1.0089 | 1.0089 | 1.0089 | 1.0092 |
| carbon_thin_empty/P4/HostStaged | 1.0089 | 1.0045 | 1.0002 | 1.0042 |
| carbon_thin_empty/P4/CudaAware | 1.0000 | 1.0002 | 1.0003 | 1.0047 |

CPU 为每 rank core-equivalent 的中位数，均为独立单次诊断，不能用于显著性判断。
正式 marker 覆盖 97.44%–99.89%，均超过 80% 门槛。
诊断/无诊断整步比值 0.9857–1.1147；
不从主墙钟扣除诊断开销，主样本 formal_cpu=null，分配仍为 K=4。

| 体系 / 后端 | B-wait 分类包络 ms/step | 逻辑 / 容量 bytes 最大 |
| --- | --- | --- |
| carbon_200k/P2/HostStaged | 0.000579 | 2679819 / 3349799 |
| carbon_200k/P2/CudaAware | 0.000582 | 2679819 / 3349799 |
| water_400k/P8/HostStaged | 0.006413 | 1682091 / 2105615 |
| water_400k/P8/CudaAware | 0.006322 | 1682091 / 2105615 |
| water_400k/P4/HostStaged | 0.010282 | 2859903 / 3579155 |
| water_400k/P4/CudaAware | 0.010401 | 2859903 / 3579155 |
| carbon_1m/P2/HostStaged | 0.011289 | 13863947 / 17329959 |
| carbon_1m/P2/CudaAware | 0.011122 | 13863947 / 17329959 |
| carbon_200k_drift/P2/HostStaged | 0.315664 | 2692107 / 3349799 |
| carbon_200k_drift/P2/CudaAware | 0.315851 | 2692107 / 3349799 |
| carbon_thin_empty/P4/HostStaged | 0.000018 | 154379 / 192999 |
| carbon_thin_empty/P4/CudaAware | 0.000017 | 154379 / 192999 |

分类是 cache-miss 主机包络摊销，不是纯 GPU 成本；内存是逻辑空间/容量高水位，
不是物理驻留。B 的索引辅助空间及 CUB 临时空间均计入真实整步；详细逐 rank 数据
见 `report-metrics.json`。本轮未采新的 Nsight trace，不伪造 GPU 子阶段归因。

## 使用边界、失败回退与复算

仅在上表通过范围、相同输入/模型/盒形、GPU、后端与 K=4 绑定下建议手动设置
`DMGMD_DOMAIN_SCHEDULE=producer-compact`、`DMGMD_DOMAIN_PARTITION=serial`、
`DMGMD_DOMAIN_OVERLAP=wait`，并显式选择对应 `DMGMD_COMM_BACKEND`。
推荐针对上述已验收候选二进制及冻结条件；其余范围不建议启用。默认保持 off，不落地自动阈值。

重建或布局失效当步 full，空 early full fallback；闭包/配置错误不可吞掉。
OOM、绑定或科学门槛失败必须停止并保留失败。不同势/盒形、其他 rank 数、GPU、
跨节点、NVT 性能、100000-step release 与跨引擎性能均不外推。
少量独立块、非独占及未锁频限制仍适用。

精确阶段命令、环境、起止时间和返回码见各 `*-dispatch.json`。统一入口后依次运行：

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o11.py freeze --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py gates --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py pilot --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py main --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py cpu --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py analyze --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py extension --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py analyze --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/run_overlap_o11.py seal --root dmgmd-overlap-o11-20261029
python3 tests/benchmark/recompute_overlap_o11.py --root dmgmd-overlap-o11-20261029
```

`raw-inventory.json` 封存原始结果及分析源码。离线复算只加载封存源码并校验哈希，
复用封存的 reference 最终状态；复算通过记录和派生结果哈希比较由 finalization.json 给出。


复算日志位于 `dmgmd-overlap-o11-20261029-finalize/recompute.log`；结果为
`PASS sealed evidence 31293` 和 `SCOPED_MANUAL_USE trials 800 CPU 48`。
`finalization.json` 保存 completion_time、全部派生哈希和推荐范围。
出版后的文档另有 `publication-inventory.json`，不改写实验原始封存清单。
