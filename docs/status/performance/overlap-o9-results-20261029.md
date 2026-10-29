# O9：执行候选独立确认（2026-10-29）

类别：候选筛选与独立确认。状态：**COMPLETE**。
判定：**CONFIRMED_SCOPED_CANDIDATE**，预选 B（同 E/H 紧凑 producer）在
carbon_200k/P2 的双后端、water_400k/P8 的 HostStaged 上，通过独立核心净收益确认。
交 O11 做全局采用复验；**生产默认保持 HostStaged/full、overlap=off、schedule=split**。
water/P8/CudaAware 仍慢于默认路径，不能因为同后端收益而推荐它替代默认路径。

## 身份、工具与门槛

起始 HEAD 为 `33c7b7f41b9ea5e2d374e915614fbf5b41410fd7`，保留原有
O6.5/O8.5/O8.6 未提交修改；O9 未改生产代码、NEP 数学、布局、所有权或通信。
独立产物目录为 `dmgmd-overlap-o9-20261029/`。开始/冻结/结束的三个只读 reference
状态与 ignored 清单一致；未提交或推送 Git。原始 patch、源码、输入、库、命令、
绑定及遥测分别见 initial.patch、source-files、frozen-plan、*-dispatch.json 和各 trial。

| 二进制 | SHA-256 | 用途 |
| --- | --- | --- |
| candidate | `46e6b9f341a44aea67de79679e2e6e2bb75421a3f2be210357bae35b0cdf2e1d` | 已验收 O8.6，同二进制 full/split/A/B/C |
| o8 | `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32` | 独立保留的已验收 O7+O8/full |

n4、8×RTX 4090；统一入口 `source ../env/md-mpi.sh`，Open MPI 5.0.10、UCX 1.22.0。
沿用已验收 K=4 物理核/rank、CPU/NUMA 映射和 rank→GPU UUID，实际 launcher audit 验证。
未锁频、非独占，物理内存驻留 UNKNOWN。

| 验证 | 本轮范围与结果 |
| --- | --- |
| 历史证据 | 逐文件 SHA-256 校验 O6.5 的 9,886、O8.5 的 15,788、O8.6 的 33,416 个原始文件；保存新报告、候选 patch/binary 和独立对照身份 |
| 工具 | 64 项 overlap 工具测试通过；缺新实验、缺诊断、重复 scope 或无有效区间不能完成；旧权限协议仅显式 `--legacy-probe` |
| 新环境/golden | 两份二进制各 P8 绑定环境预检和 4 个 golden case 通过；另有 candidate P4 预检/golden |
| 新 MPI | B 完整 P2/P4 × 两后端 × off/serial/verify/verify-wait/verify-poll 矩阵通过，包括迁移、空 local、ghost poison、布局/字节记账及缓存/错误路径 |
| 新受控调度 | B 的 27 个延迟/progress/故障/配置一致性作业通过 |
| 长程与 GPU 门槛复用 | 二进制 SHA 完全相同，引用已封存 O8.6 的 B/C 各 nightly 42 配置 + 12 restart、GPU CTest 18 + 专项 1；未声称本轮重跑 nightly |
| 新真实输入科学对照 | 60 个短程配置通过；同 case/P/backend/窗口的全部版本与模式输出哈希一致；正式同窗口输出也逐组一致 |

第一次 freeze 因 O6.5 的计划文件名不同而停止，尚无性能样本；修正后完整重新核验。
新鲜 gate wrapper 的 nightly 原拟重复执行，复核相同二进制后在其启动前取消：
暂停 wrapper 不影响正在执行的 MPI 子进程，MPI 完整 PASS 后结束 wrapper，调度测试独立完成。
精确经过保存在 fresh-gates-scheduling-amendment.json、fresh-mpi-completion.json；
wrapper 的终止不是数值失败，也未省略新二进制必需门槛，因为本轮没有新二进制。

## 预先筛选与独立设计

冻结先选 B，再采本轮数据，不从新数据重新挑赢家。
O8.5 A 减少 force 拆分，但历史 carbon/HostStaged 仍慢于 full；保留为独立控制。
O8.6 B/A 的四组 wait 探索区间均支持收益，C/B wait 的四组额外收益均未确认，
故优先保留原 E/H 的 B；C 仍作为显式单因素控制保留。

O6.5 的紧凑 radial/partial 重放 D 改善 C，但 D 仍慢于完整 A；其结果支持紧凑映射方向，
不支持把紧凑 force 拆分直接当作已胜 full 的方案。producer 让 force consumer 完整执行，
避免该拆分成本；本轮验证已有整步依据的 A/B/C，不叠加无整步净收益证据的 force 拆分。
微架构原因仍 INCONCLUSIVE，未以 NCU 权限失败代替执行实验。

核心为 carbon_200k/P2（196608 原子）、water_400k/P8（393216 原子）及双后端，
同候选二进制保留 full、完整 split-serial、A/B/C serial/wait，并保留 O8/full。
新增 water/P4、carbon_1m/P2（1048576 原子）、carbon 均匀漂移高重建/P2、薄 slab/P4，
各保留 full/B-serial/B-wait、双后端。薄 slab 为 12288 原子，不替代核心规模。

300 个新正式 trial = 60 配置 × 5 个独立配对块，全部有效，未追加、替换或删除慢样本。
另有 60 个 pilot 和 12 个独立 marker CPU 诊断，均不进入主统计。
正式采集为 2026-10-29 10:58–14:17（Asia/Shanghai），主窗口实际为
23.402500–35.747991 s，超过冻结的 10 s 下限、目标 25 s。
共同正式步数为 carbon/P2 1700、water/P8 4600、water/P4 3300、carbon_1m/P2 400、
漂移/P2 700、薄 slab/P4 7000；water warmup 1600，其余 200。

统计为中位数之比、40,000 次保留块配对 bootstrap。
核心 B 的四组同后端/default 共八项对比使用 Bonferroni 校正的 **99.375%** 区间
（名义 family 95%，bootstrap 及仅五个独立块的限制仍适用）；其余均为探索性 95% 区间。
预先规定核心相对区间宽度超过 10% 时追加一次五个全局配对块，本轮未触发。
完整种子、随机顺序、输入和步数见 frozen-plan/main-plan；不混入任何历史样本。

## 整步与路线比较

单位 ms/step，中位数；A 为原 producer，B 为同 E/H 紧凑，C 为几何紧凑。

| fixture / backend | O8 full | candidate full | split serial | A serial / wait | B serial / wait | C serial / wait |
| --- | --- | --- | --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 15.612284 | 15.540905 | 20.401235 | 16.174214 / 16.035258 | 15.324078 / 15.318047 | 15.400875 / 15.258067 |
| carbon_200k/P2/CudaAware | 16.263227 | 16.341615 | 21.000043 | 16.888800 / 16.061590 | 16.036522 / 15.182805 | 16.132377 / 15.211776 |
| water_400k/P8/HostStaged | 5.293002 | 5.300456 | 6.102875 | 5.358428 / 5.193712 | 5.296559 / 5.143852 | 5.289685 / 5.135775 |
| water_400k/P8/CudaAware | 6.334884 | 6.342782 | 7.170787 | 6.438322 / 5.814527 | 6.368780 / 5.760770 | 6.368533 / 5.767921 |

以下比值 >1 表示分母 B 更快。核心采用范围要求同后端与默认路径的校正区间下界均 >1。

| fixture / backend | 同后端 full / B | HostStaged full / B | 核心确认 |
| --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 1.0145 [1.0078, 1.0233] | 1.0145 [1.0078, 1.0233] | 通过，交 O11 |
| carbon_200k/P2/CudaAware | 1.0763 [1.0562, 1.0848] | 1.0236 [1.0190, 1.0313] | 通过，交 O11 |
| water_400k/P8/HostStaged | 1.0304 [1.0258, 1.0338] | 1.0304 [1.0258, 1.0338] | 通过，交 O11 |
| water_400k/P8/CudaAware | 1.1010 [1.0990, 1.1060] | 0.9201 [0.9179, 0.9510] | 未胜默认路径 |

独立确认支持上述三个精确 scope 的 B；不自动启用、不生成通用阈值。
与独立 O8/HostStaged full 比较的探索比值依次为
1.0192 [1.0115, 1.0284]、1.0283 [1.0161, 1.0390]、1.0290 [1.0249, 1.0359]、0.9188 [0.9150, 0.9523]，与核心方向一致。

| fixture / backend | A-wait / B-wait（紧凑增量） | B-wait / C-wait（几何增量） | B-serial / B-wait（重叠增量） |
| --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 1.0468 [1.0379, 1.0573] | 1.0039 [0.9864, 1.0148] | 1.0004 [0.9866, 1.0200] |
| carbon_200k/P2/CudaAware | 1.0579 [1.0449, 1.0675] | 0.9981 [0.9870, 1.0040] | 1.0562 [1.0389, 1.0644] |
| water_400k/P8/HostStaged | 1.0097 [1.0079, 1.0140] | 1.0016 [0.9995, 1.0044] | 1.0297 [1.0291, 1.0348] |
| water_400k/P8/CudaAware | 1.0093 [1.0055, 1.0139] | 0.9988 [0.9652, 1.0040] | 1.1055 [1.1046, 1.1120] |

新数据的 B/A 四组探索区间均支持紧凑增量；C/B 四组仍未确认额外 wait 收益，
不据此换选 C。A/C 的全部正式控制、同后端净收益和 O8/full 桥接也保留在 contrasts.csv。
serial/full 与 serial/wait 的差异均含维护、执行和同步，不能当作纯网络隐藏时间。

## 适用性与反例

以下均为探索性 95% 区间，不升级为核心预注册确认范围。

| fixture / backend | 同后端 full / B | HostStaged full / B |
| --- | --- | --- |
| water_400k/P4/HostStaged | 1.0136 [1.0054, 1.0241] | 1.0136 [1.0054, 1.0241] |
| water_400k/P4/CudaAware | 1.1631 [1.1591, 1.1733] | 1.0251 [1.0191, 1.0400] |
| carbon_1m/P2/HostStaged | 0.9942 [0.9904, 0.9983] | 0.9942 [0.9904, 0.9983] |
| carbon_1m/P2/CudaAware | 1.0249 [1.0207, 1.0285] | 0.9943 [0.9902, 1.0008] |
| carbon_200k_drift/P2/HostStaged | 0.9910 [0.9879, 1.0024] | 0.9910 [0.9879, 1.0024] |
| carbon_200k_drift/P2/CudaAware | 0.9980 [0.9969, 1.0053] | 0.9867 [0.9843, 0.9960] |
| carbon_thin_empty/P4/HostStaged | 0.9967 [0.9811, 1.0147] | 0.9967 [0.9811, 1.0147] |
| carbon_thin_empty/P4/CudaAware | 0.9996 [0.9808, 1.0059] | 0.9495 [0.9171, 0.9666] |

water/P4 两后端有本轮探索支持，交 O11 单独复验。
carbon_1m/HostStaged 小幅回退，CudaAware 虽优于同后端 full，但未确认胜默认路径；
不外推 carbon_200k 的采用范围。漂移使正式 MD 步约 33.43% 重建，没有默认净收益，
CudaAware 比默认更慢。薄 slab 实际 E=0、全部 full fallback，不是 overlap 成功样本；
其 CudaAware 路径也慢于默认。空/小 interior 的数值边界另由已封存 GPU/MPI 用例与本轮
新 MPI 覆盖；不从 interior 比例推出自动启用阈值。

## 维护、内存与 CPU

全部列表/分类/分配和同步开销保留在真实整步内。B 保持原 local slot/stride，
逻辑辅助空间为 `12L+12D+12+T` bytes，相对 A 的 `12L+12` 增加 `12D+T`；T 为 CUB
临时空间。本轮详细诊断均为 B-wait，表中 memory 是逻辑空间和容量高水位，不是物理驻留。
分类时间是正式主样本每 rank 的 cache-miss 主机包络摊销再取中位数，不能当纯 GPU 成本。
完整 kernel/grid/stream 的同二进制证明和 A/C 维护对照引用已核验 O8.6 的 48 份独立 trace；
本轮没有新增 Nsight GPU trace，不假造其时间分解。

| fixture / backend | E/D 中位数 | 逻辑 / 容量 bytes 最大 | miss 包络 ms/step | rank CPU core-equivalent 中位数 |
| --- | --- | --- | --- | --- |
| carbon_1m/P2/CudaAware | 0.879121 | 13863947 / 17329959 | 0.011306 | 1.0046 |
| carbon_1m/P2/HostStaged | 0.879121 | 13863947 / 17329959 | 0.011184 | 1.0047 |
| carbon_200k/P2/CudaAware | 0.842105 | 2679819 / 3349799 | 0.000578 | 1.0092 |
| carbon_200k/P2/HostStaged | 0.842105 | 2679819 / 3349799 | 0.000577 | 1.0046 |
| carbon_200k_drift/P2/CudaAware | 0.838095 | 2692107 / 3349799 | 0.315686 | 1.0089 |
| carbon_200k_drift/P2/HostStaged | 0.838095 | 2692107 / 3349799 | 0.315596 | 1.0044 |
| carbon_thin_empty/P4/CudaAware | 0.000000 | 154379 / 192999 | 0.000018 | 1.0089 |
| carbon_thin_empty/P4/HostStaged | 0.000000 | 154379 / 192999 | 0.000018 | 0.9998 |
| water_400k/P4/CudaAware | 0.764087 | 2859903 / 3579155 | 0.010215 | 1.0087 |
| water_400k/P4/HostStaged | 0.764087 | 2859903 / 3579155 | 0.010318 | 1.0046 |
| water_400k/P8/CudaAware | 0.576778 | 1682091 / 2105615 | 0.006358 | 1.0045 |
| water_400k/P8/HostStaged | 0.576778 | 1682091 / 2105615 | 0.006381 | 1.0088 |

12 个 CPU 诊断的逐 rank 正式 marker 覆盖为 98.72%–99.89%，
全部超过 80% 合同。诊断/无诊断主中位数比值范围 0.9945–1.0691；
这些单次诊断不用于估计显著性，不从主墙钟减掉所谓诊断开销。主样本 `formal_cpu=null`。
约一个 core-equivalent 是实测 CPU 时间；分配仍为每 rank 四个物理核，不是资源需求降至一核的证明。
CUB 临时空间、全部 rank 明细、普通/重建计数见 report-metrics.json；分段初始 full 调用
与正式 MD rebuild_steps 口径不同，不能把 summary 中的所有 full 调用都称为重建步。

失败回退仍由既有合同决定：重建/布局失效当步 full、空 early full fallback；
不得因性能慢而吞掉闭包/配置错误。OOM、绑定或科学门槛失败必须保留失败样本并停止，
不能换小 N 冒充原配置通过。本轮没有性能 trial 失败或替换。

## 重算、交接与限制

统一环境加载后，本轮精确阶段命令为：

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o9.py freeze --root dmgmd-overlap-o9-20261029
python3 tests/benchmark/run_overlap_o9.py gates --root dmgmd-overlap-o9-20261029
python3 tests/benchmark/run_overlap_o9.py pilot --root dmgmd-overlap-o9-20261029
python3 tests/benchmark/run_overlap_o9.py main --root dmgmd-overlap-o9-20261029
python3 tests/benchmark/run_overlap_o9.py cpu --root dmgmd-overlap-o9-20261029
python3 tests/benchmark/run_overlap_o9.py analyze --root dmgmd-overlap-o9-20261029
python3 tests/benchmark/run_overlap_o9.py seal --root dmgmd-overlap-o9-20261029
bash dmgmd-overlap-o9-20261029/recompute.sh
```

新 MPI/受控调度的完整参数见 fresh-mpi-command.json、fresh-schedule-dispatch.json；
实验调度、实际耗时和退出状态见 execute-confirmation.py 与各 *-dispatch.json/log。
raw-inventory.json 封存原始结果及分析源码，recompute.sh 先验证全部哈希，再从 source-files
离线复算，无需 GPU；历史 reference 状态从封存的结束观测读取，不用未来 checkout 状态替代。
离线复算已通过：17,415 个封存文件哈希通过，300 个正式 trial、12 个 CPU 诊断及
CONFIRMED_SCOPED_CANDIDATE 判定从保存的源码重现；五份派生结果的 SHA-256 与封存值一致。
首次复算在哈希校验后发现间接依赖 `long_nve_common → run_baselines` 缺失，未进入统计。
补齐四个 baseline Python/JSON 依赖，保留原始清单、失败日志及 bundle-amendment.json；
原分析源码、样本、统计规则未改。当前 seal 工具已补齐该打包依赖，旧实验仍用其保存源码复算。

O9 的实现、正确性、性能结论分别为：**复用已验收 B，工具流程完成；PASS；限定 scope
独立确认**。下一步为条件性 O10 和 O11；O9 不宣称已完成 O11，也不改变默认或自动阈值。
仅限本机、固定输入/模型、P、后端、K=4 绑定和 wait；不外推不同势/盒形、其他 GPU、
跨节点、poll、100000-step release 或 GPUMD/LAMMPS 性能。五块、非独占与未锁频的统计限制保留。
