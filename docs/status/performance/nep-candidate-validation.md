# NEP 执行候选确认与限定采用复验

目标：按时间顺序区分 O9 首轮候选门槛复核、独立确认和 O11 限定采用复验，避免把阶段性结论合并为单一当前结论。

| 日期 | 阶段 | 结论 | 适用范围与限制 |
| --- | --- | --- | --- |
| 2026-10-26 | [O9 候选门槛复核](#o9-results-20261026-report) | NO_ADOPTION：无可信候选，未实施 NEP 执行改动；硬件原因 INCONCLUSIVE。 | 不是“优化无收益”的证明；新候选性能未测。 |
| 2026-10-29 | [O9 独立候选确认](#o9-results-20261029-report) | CONFIRMED_SCOPED_CANDIDATE：预选 B 在 carbon/P2 双后端及 water/P8 HostStaged 通过独立核心净收益确认。 | 结论限已确认的三个 scope；water/P8 CudaAware 仍慢于默认，最终采用建议见 O11 复验。 |
| 2026-10-30 完成（批次 10-29） | [O11 限定范围采用复验](#o11-results-20261030-report) | SCOPED_MANUAL_USE：建议仅限 carbon_200k/P2 的 HostStaged 或 CudaAware B-wait。 | 生产默认仍 HostStaged/full、overlap=off、schedule=split；water/P8 未通过采用条件，poll/几何无额外确认。 |

以下各节按来源报告分别保留原始身份、环境、实际命令、统计数据、失败与未覆盖边界及原始产物路径；标题层级仅为嵌入整合文档而调整。

<a id="o9-results-20261026-report"></a>

<a id="o9-results-20261026-o9nep-执行候选证据复核2026-10-26"></a>

## O9：NEP 执行候选证据复核（2026-10-26）

类别：候选门槛复核与分支结论。状态：**COMPLETE / NO_ADOPTION**。
按原 O9 的“没有可信候选则以 NO_ADOPTION 记录终止该分支”完成。
本轮实际重试仍无法采集硬件计数器，也没有新的单因素执行证据支持选择一个候选，
因此没有实施 NEP 执行改动。微架构原因仍为 **INCONCLUSIVE**。
这不表示某个优化已被证明无收益；新候选的性能为 **NOT_MEASURED_NO_CANDIDATE**。

<a id="o9-results-20261026-身份与冻结范围"></a>

### 身份与冻结范围

起始 HEAD：`cf6d098e3f12b25fddafb62cc3e95365e8f87425`，已有 O6–O8 未提交修改。
起始 dirty、全部已有修改快照、源码 patch 与生产文件 SHA-256 保存在独立目录
`dmgmd-overlap-o9-20261026/`；三个只读 reference 的 HEAD、Git 状态和 ignored 清单
均在前后核对。O6 的 18 个 `src/gpumd_compat/` 源文件与当前源码逐文件哈希一致。
本轮修改限于 O9 复核工具、证据判定测试和文档，没有重新编译生产二进制。

| 对照 | SHA-256 | 用途 |
| --- | --- | --- |
| O6 已验收的 O4 二进制 | `860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6` | 重用原稳态窗口实际重试 NCU |
| O8 当前二进制 | `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32` | 当前基线环境/golden 回归；与 `build/dmg-md` 相同 |

两份对照及 carbon_200k/water_400k 的原输入复制到 O9 目录，哈希与已验收阶段核对。
没有把 O6 的 kernel 时长或 O8 的整步结果当作新的 O9 性能样本。

环境为 n4、AMD EPYC 7K62、八张 RTX 4090、driver 580.173.02、CUDA 12.9、
Open MPI 5.0.10、UCX 1.22.0、Nsight Compute 2025.2.1.0。
所有 shell 从 `source ../env/md-mpi.sh` 进入。
沿用 O6 的完整 GPU UUID 顺序、每 rank 四物理核与 NUMA 映射；P2 使用前两张 GPU，
rank0 的 OS CPU 为 0–3/96–99，rank1 为 4–7/100–103。
没有锁频或独占预约，内存页驻留 UNKNOWN；本轮无正式墙钟样本，正式段 CPU 为 null。

`frozen-plan.json` 在采集前冻结二进制、输入、源码、工具、库、环境、绑定、窗口和停止规则。
这是一个硬件指标可用性探测，不是性能试验：只有一个预先确定的窗口，无随机顺序、
bootstrap、配对区间或正式重复块；诊断时长不参与任何加速比。
若指标可用，工具返回 `CANDIDATE_REVIEW_REQUIRED / PARTIAL`，要求重新冻结单个候选，
不会自动采用。缺实验、超时、绑定失败或其他错误均为 PARTIAL。

<a id="o9-results-20261026-实际复核与结果"></a>

### 实际复核与结果

- CPU CTest 15/15 通过，其中 O9 的六项证据判定测试覆盖缺实验、其他错误、超时、
  无效绑定、可用指标与缺失/同大小篡改的原始证据。
- 两份既有二进制分别通过八 rank 环境预检，包含 CUDA-aware collective/p2p 和 GPU 唯一映射。
- 两份既有二进制分别通过 committed golden 4/4，共 8 个 case；沿用原容差。
- 实际重试 `carbon_200k/P2/CudaAware/full`：warmup=200、正式诊断窗口=128 步，
  rank0 的全局第 264 步为原 Nsight 已匹配的 ordinary 窗口。
  过滤五类 pipeline kernel 后 `launch-skip=1325`、`launch-count=5`。
  采用 kernel replay、cache-control=none、clock-control=none，请求 LaunchStats、
  Occupancy、SchedulerStats、WarpStateStats、MemoryWorkloadAnalysis、SourceCounters。
- 新尝试返回 `ERR_NVGPUCTRPERM`；绑定核验通过，未超时。
  当日驱动仍为 `RmProfilingAdminOnly=1`。按冻结规则停止，没有重复相同权限失败或修改驱动。
  此尝试为 **INCONCLUSIVE**，不能列为性能样本或生产科学失败。

本轮核验 O6 的 3873 个原始文件（约 18.14 GB）的证据哈希，仅只读访问旧目录；结果保存在
`inherited-raw-validation.json`。O9 工具、实际 argv/环境、门槛日志、NCU 原始 rank
日志、绑定观测、GPU 遥测及输入/二进制均独立保存。没有覆盖 O1–O8 样本。

本轮没有 launch、数学、布局或所有权改动，所以不重跑候选专属的 MPI/long_nve
矩阵，也不声明这些矩阵在本轮通过。当前 O8 的 MPI/nightly 验收仍引用
[O8 记录](thermo-demand-results.md#o8-results-20261026-report)，原 O4 的分区/overlap oracle 仍引用
[O4 记录](../validation/communication-computation-overlap.md#overlap-o4-20261016)。

<a id="o9-results-20261026-候选筛选依赖与反例"></a>

### 候选筛选、依赖与反例

[O6 原始结果](nep-execution-cost.md#o6-results-20261021-report)支持 carbon 的主要拆分增量位于 radial，
但不能确定活跃 warp、访存、缓存、spill 或 scheduler 的因果贡献。
`NEP::compute_domain` 的 `products` 仍以完整 owned grid 调用 `find_force_radial`，
kernel 在读取物理/workspace 数据前按 interior mask/complement 返回；
early 和 late 的 block/thread/register 属性相同。源码与时间位置不能证明缩小 grid 的实际收益。
water/P8 的 angular partial 增量大于 radial，也构成将 carbon 结论推广到所有输入的反例。

唯一被筛选的方向是“保持数学不变，减少 radial 的非活动中心调度”；没有选择其实现形式：

| 形式 | 必须计入的成本与安全条件 | 本轮不能采用的原因 |
| --- | --- | --- |
| 活动 block 列表 | 两相 block ID 表最多约 `8*ceil(O/64)` bytes（仅表体，不含计数、scratch 和 capacity）；分类/列表构建、空列表及失效维护；原 thread→slot 映射与 mask guard 保留 | 混合 block 可能两相都活跃，无法从 interior 比例推导能删掉多少 block；无直接单因素对照 |
| 中心索引列表 | I/B 总共 O 个 int，表体约 `4O` bytes，另有构建 scratch/计数和高水位 capacity；只映射执行中心，全部物理数组和 L stride 保持原值；额外索引读取与合并访存变化 | 压缩中心不等于降低实际 kernel 成本，列表维护和调度/访存变化没有新测量依据 |

任何后续候选都必须沿用 `DomainPartitionKey` 的 layout/neighbor/workspace generation、
O/D/L、box/PBC 失效合同，在 halo begin 前完成维护；不能以 shape 或 pointer 相同复用旧表。
radial(I) 只可消费 early(E) 已生成的 Fp，radial(B) 必须在 halo 完成及 late(H) producer 后运行；
I/B 不重不漏、仅 owned 中心写最终 force/virial，每行浮点表达式和累加顺序不变。
全局 ID 排序不能替换为一次重新排列物理粒子数组。

反例包括同 shape 的 slot/gid 重排、无迁移的 neighbor rebuild、不同 rank 的空/小 interior、
不满 block 的尾部、I/B 混合 block，以及 thin-slab/M1 fallback。若将来有单因素证据选定候选，
需另冻多元素/NEP5/ZBL、普通/重建/迁移、空/小 interior、M1 的 full/serial oracle golden，
连同 long_nve 和原容差验证，再比较候选 full/serial/wait/poll 与各自原二进制；
整步对照必须含列表/分类维护、额外内存和 launch 成本。这些是重新开启条件，不是本轮已实现或通过项。

<a id="o9-results-20261026-结论与复算"></a>

### 结论与复算

- 实现：证据复核工具及保守判定已落地；生产源码与当前二进制逐文件/哈希不变。
- 正确性：两份既有二进制的环境/golden 与本轮工具测试通过。
- 性能：**NO_ADOPTION** 终止本轮无可信候选分支；硬件原因 INCONCLUSIVE，
  新候选性能 NOT_MEASURED_NO_CANDIDATE，不能声称零收益或回归。

生产默认继续 HostStaged/full、overlap=off。O10 条件性后端评估及 O11 相对 O7+O8 full
的采用复验仍单独管理；本结论不能替代它们。

实际阶段命令：

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o9.py freeze --root dmgmd-overlap-o9-20261026
python3 tests/benchmark/run_overlap_o9.py gates --root dmgmd-overlap-o9-20261026
python3 tests/benchmark/run_overlap_o9.py probe --root dmgmd-overlap-o9-20261026
python3 tests/benchmark/run_overlap_o9.py analyze --root dmgmd-overlap-o9-20261026
python3 tests/benchmark/test_overlap_o9.py
cmake -S . -B build
ctest --test-dir build --output-on-failure -E domain_neighbor_cuda
python3 tests/benchmark/run_overlap_o9.py seal --root dmgmd-overlap-o9-20261026
bash dmgmd-overlap-o9-20261026/recompute.sh
```

前三阶段在沙箱外执行同一环境入口。已有原始门槛日志不可覆盖；新采集须用新 `--root`。
`seal` 保存最终源码、patch 与原始证据 SHA-256 清单，离线 `recompute.sh` 校验原始证据后
重新生成 `analysis.json`，不需要 GPU 或 NCU。报告中的状态以实际原始证据为准。

---

<a id="o9-results-20261029-report"></a>

<a id="o9-results-20261029-o9执行候选独立确认2026-10-29"></a>

## O9：执行候选独立确认（2026-10-29）

类别：候选筛选与独立确认。状态：**COMPLETE**。
判定：**CONFIRMED_SCOPED_CANDIDATE**，预选 B（同 E/H 紧凑 producer）在
carbon_200k/P2 的双后端、water_400k/P8 的 HostStaged 上，通过独立核心净收益确认。
交 O11 做全局采用复验；**生产默认保持 HostStaged/full、overlap=off、schedule=split**。
water/P8/CudaAware 仍慢于默认路径，不能因为同后端收益而推荐它替代默认路径。

<a id="o9-results-20261029-身份工具与门槛"></a>

### 身份、工具与门槛

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

<a id="o9-results-20261029-预先筛选与独立设计"></a>

### 预先筛选与独立设计

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

<a id="o9-results-20261029-整步与路线比较"></a>

### 整步与路线比较

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

<a id="o9-results-20261029-适用性与反例"></a>

### 适用性与反例

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

<a id="o9-results-20261029-维护内存与-cpu"></a>

### 维护、内存与 CPU

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

<a id="o9-results-20261029-重算交接与限制"></a>

### 重算、交接与限制

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

---

<a id="o11-results-20261030-report"></a>

<a id="o11-results-20261030-o11限定范围采用复验2026-10-30-完成"></a>

## O11：限定范围采用复验（2026-10-30 完成）

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

<a id="o11-results-20261030-身份与正确性"></a>

### 身份与正确性

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

<a id="o11-results-20261030-预先冻结的范围与统计"></a>

### 预先冻结的范围与统计

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

<a id="o11-results-20261030-核心整步结果"></a>

### 核心整步结果

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

<a id="o11-results-20261030-首轮不确定性与追加依据"></a>

#### 首轮不确定性与追加依据

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

<a id="o11-results-20261030-分区重叠与-poll-对照"></a>

### 分区、重叠与 poll 对照

以下为探索性 95% 区间，包含维护、执行和同步，不能当作纯网络隐藏时间。

| 体系 / 后端 | full / B-serial | B-serial / B-wait | B-wait / B-poll | A-wait / B-wait | B-wait / C-wait |
| --- | --- | --- | --- | --- | --- |
| carbon_200k/P2/HostStaged | 1.0168 [1.0090, 1.0246] | 1.0041 [0.9974, 1.0120] | 0.9998 [0.9958, 1.0051] | 1.0583 [1.0531, 1.0611] | 0.9960 [0.9911, 1.0029] |
| carbon_200k/P2/CudaAware | 1.0137 [1.0079, 1.0290] | 1.0577 [1.0492, 1.0614] | 1.0001 [0.9150, 1.0120] | 1.0581 [1.0501, 1.0889] | 0.9935 [0.9886, 1.0057] |
| water_400k/P8/HostStaged | 1.0080 [0.9627, 1.0818] | 1.0425 [1.0191, 1.0492] | 0.9975 [0.9939, 1.0687] | 1.0177 [1.0030, 1.0203] | 0.9999 [0.8088, 1.0072] |
| water_400k/P8/CudaAware | 0.9959 [0.9905, 1.0035] | 1.1049 [1.0999, 1.1107] | 1.0158 [0.9923, 1.0371] | 1.0115 [1.0075, 1.0199] | 0.9995 [0.9921, 1.0008] |

<a id="o11-results-20261030-扩展范围与退化对照"></a>

### 扩展范围与退化对照

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

<a id="o11-results-20261030-cpu-与维护成本"></a>

### CPU 与维护成本

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

<a id="o11-results-20261030-使用边界失败回退与复算"></a>

### 使用边界、失败回退与复算

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
