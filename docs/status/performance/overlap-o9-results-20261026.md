# O9：NEP 执行候选证据复核（2026-10-26）

类别：候选门槛复核与分支结论。状态：**COMPLETE / NO_ADOPTION**。
按原 O9 的“没有可信候选则以 NO_ADOPTION 记录终止该分支”完成。
本轮实际重试仍无法采集硬件计数器，也没有新的单因素执行证据支持选择一个候选，
因此没有实施 NEP 执行改动。微架构原因仍为 **INCONCLUSIVE**。
这不表示某个优化已被证明无收益；新候选的性能为 **NOT_MEASURED_NO_CANDIDATE**。

## 身份与冻结范围

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

## 实际复核与结果

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
[O8 记录](overlap-o8-results-20261026.md)，原 O4 的分区/overlap oracle 仍引用
[O4 记录](../validation/overlap-o4-20261016.md)。

## 候选筛选、依赖与反例

[O6 原始结果](overlap-o6-results-20261021.md)支持 carbon 的主要拆分增量位于 radial，
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

## 结论与复算

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
