# M2a 缓存判定归约实测

目标：保存 O7 将缓存判定归约合并为单次 OR 的实现语义、验收、配对性能结果和采用结论。

| 日期 | 阶段 | 结论 | 适用范围与限制 |
| --- | --- | --- | --- |
| 2026-10-23 | [O7 单次 OR 归约](#o7-results-20261023-report) | 语义等价实现减少一次实际 collective，经门槛验收后采用。 | 整步收益 INCONCLUSIVE；减少调用次数不等于已证明墙钟收益，范围限本次输入/后端/资源。 |

以下各节按来源报告分别保留原始身份、环境、实际命令、统计数据、失败与未覆盖边界及原始产物路径；标题层级仅为嵌入整合文档而调整。

<a id="o7-results-20261023-report"></a>

<a id="o7-results-20261023-o7合并-m2a-缓存判定归约2026-10-23"></a>

## O7：合并 M2a 缓存判定归约（2026-10-23）

类别：实现、数值回归与独立配对实测。状态：**完成；采用单次 OR，整步收益 INCONCLUSIVE**。
本页仅记录 O7；O8–O11 没有实施。

<a id="o7-results-20261023-实现与语义证明"></a>

### 实现与语义证明

改动位于 `src/domain_runtime.cu::run_domain_segment` 的 VV1/wrap 后、halo/force 前。
旧路径对每 rank 的最大位移平方先做 double MAX，再由全局阈值判定设置原因位，
最后做 uint32 reason OR。新路径在本地以相同的严格 `>` 设置位移超限位，然后直接
与 owner-routing、continuity-invalid 原因做一次 OR。GPU 检查、位移累计、阈值表达式、
rebuild/migration transaction 和 NEP 调用均未改动。

所有消费者已沿调用核对：旧 `global_max_squared` 只用于该阈值比较；
`local_reasons` 只传给 OR，后续决策只读取 `global_reasons`。
`allreduce_max_host` 的自适应时间步消费者、周期 `neighbor.out` 占用最大值消费者及
M1 的相应调用保留。

- `inspect_domain_cache` 仅对 routeable 原子调用 `atomicMax`。非有限位移平方（包括
  NaN/Inf）、非有限 fractional step、单步超过一个盒长或 wrap 后仍无法路由时，只置
  continuity-invalid 位并 return，不把非有限值送入最大值。
- 局部最大值由零初始化，仅积累有限非负平方。因此对各 rank 的 `m_r` 与相同有限
  阈值 `h = 0.25*skin*skin`，`max_r(m_r)>h` 当且仅当 `OR_r(m_r>h)`。
  比较没有新算术或舍入，等号继续复用；空 owned rank 的 `m_r=0`、reason=0，仍参与 OR。
- 非有限/不可路由位独立归约；OR 后先检查 fatal，再考虑 rebuild。一个 rank 无效而
  另一个 rank 超阈值时，错误优先级保持一致。其它 rank 无需知道远端最大值的具体数值。
- 各 rank 无条件完成同一处 OR 后才分支；删除的 MAX 对所有 rank 同时删除。
  不新增条件性 collective，也不提前进入 halo/force。底层 MPI 失败仍走现有 fatal 路径。
- periodic crossing 使用原 VV1 在 wrap 前累计的连续位移；多 run 不清零 epoch，
  restart 仍 bootstrap。M1 的源文件与调用链没有改动。

普通 M2a 步的固定 collective 从 3 次变为 2 次（decision OR + 原每步 thermo）；
MPI input/output 逻辑字节分别从 `76P` 降为 `68P`，不表示物理链路字节。
自适应时间步、周期 neighbor 记录、重建/迁移、输出的额外通信仍另计。

<a id="o7-results-20261023-身份资源与冻结设计"></a>

### 身份、资源与冻结设计

起始 HEAD：`cf6d098e3f12b25fddafb62cc3e95365e8f87425`。
起始工作树含 O6 工具与文档的未提交修改；完整状态、patch 和未跟踪文件副本保存在
独立目录 `dmgmd-overlap-o7-20261023/`，没有覆盖 O5/O6 样本。
三个只读 reference 的 tracked/untracked 状态及 ignored 文件清单单独核对。

| 二进制 | SHA-256 |
| --- | --- |
| 原 full（已验收 O4/O5/O6） | `860c1ba319a3bae55f1f3cb52eaf05dc1d3524d6a71766790b78fc0b1d68ceb6` |
| O7 | `61b12e93eac137538e75b2e6c5cfcbccbab11ca75084ee7fc025f48b86bf2d17` |

唯一环境入口为 `source ../env/md-mpi.sh`。本机 n4、八张 RTX 4090、driver
580.173.02；CPU/NUMA、CUDA/Open MPI/UCX 版本与库哈希随机器记录保存。
沿用 O6 的固定 GPU UUID 前缀和每 rank 四物理核、含 SMT 的八个 OS CPU、互不共享物理核；
P2/P4/P8 使用相同 rank 前缀。每 trial 独立 audit 实际 CPU affinity 与 rank→GPU UUID。
没有锁频、独占预约或内存物理页驻留证明；正式段 CPU 为 null。

实施前的 `frozen-design.json` 固定两个版本、两后端、三个 scope、五个独立配对块及
全部随机顺序（seed=2026102307）。`frozen-plan.json` 补充新二进制、输入、工具和绑定身份。
每个新进程先预热，再测独立 run segment；正式墙钟关闭 profiler/trace/verify/详细计时。

| 输入 / ranks | 原子数 | warmup | 正式步数 |
| --- | ---: | ---: | ---: |
| carbon_200k / P2 | 196608 | 200 | 1500 |
| water_400k / P4 | 393216 | 1600 | 4300 |
| water_400k / P8 | 393216 | 1600 | 4300 |

共 60 个预定正式 trial，每个正式段至少 10 s，目标 20–30 s。
估计量为 `median(before)/median(after)`；95% 区间按独立配对块 bootstrap 20000 次，
seed=2026102308，不把 rank 或步当作重复。若区间宽度/估计值超过 10%，仅对该
scope/backend 追加五个完整配对块，最多一次。不按速度删样本，失败/无效记录原样保留。
本轮只测 O7 的 full 增量，不重新评价 overlap 相对新 full 的采用问题。

<a id="o7-results-20261023-验收与实际归约次数"></a>

### 验收与实际归约次数

八 rank 环境预检、committed golden 4/4、CUDA neighbor 检查及 CPU CTest 12/12 已通过。
P2/P4/P8 × 两后端的 108 个边界/错误对照作业及 4 个 P1/M1 作业通过，成功路径的
thermo/trajectory/neighbor 等科学文件逐字节一致。错误作业中所有 rank 均观测到
完成后的 invalid OR，随后按原错误文本退出，没有进入本步 halo/force。
18 对错误作业的退出码及观测到的 `DMGMD_ERROR` 类别/消息逐项一致；MPI_Abort 的
异步终止不保证每个 rank 的错误行都抵达 launcher，因此不比较错误日志条数。
完整 MPI 主矩阵 68/68 通过，含 NEP5、多元素/typewise cutoff、ZBL、NVT、自适应步长/
修速、重建/迁移、restart 与空 rank；另通过 M1 fallback、多 run、timing A/B 与 fatal
focused checks。所有主配置均检查逐原子数值、owner/layout 及逐步精确通信量。
nightly NVE 子矩阵的三个基础体系、seed 0、P1/P2/P4、两后端共 **18/18** 配置通过，
包含每配置 100 步严格短轨迹和 10000 步守恒检查；跨 rank P1↔P4 restart **12/12** 通过。
本轮选择 `short,long,restart`，没有声称重跑完整 nightly 的 replay/NVT/派生 case 或
100000-step release。新增 O7 资源身份测试的 3 项用例及单独 CTest 也已通过。
三个实际性能 scope × 两后端 × 原/新二进制 × full/serial/wait 的 **36/36** smoke
通过，thermo/neighbor 科学文件逐字节一致。三个 reference 的 Git 状态与 ignored 文件
清单在最后一次参考作业后和起始记录完全一致。
PMPI probe 只用于独立诊断，不链接或预加载到正式性能运行。
它直接记录真实的 `MPI_DOUBLE/MPI_MAX` 与 `MPI_UINT32_T/MPI_BOR` 调用，
用相同 probe 比较原/新二进制，不以 runtime 自报的通信账本代替实际调用证据。

以下为每个 rank 的真实调用数，两后端与 P2/P4/P8 均一致。MAX 包括未删除的初始/
周期 neighbor 占用统计；表中差值正好等于执行步数。

| fixture | 完成步数 | reason OR（原 / O7） | scalar MAX（原 / O7） | 全局超限 / invalid 次数 |
| --- | ---: | ---: | ---: | ---: |
| below / equal | 4 | 4 / 4 | 6 / 2 | 0 / 0 |
| above | 4 | 4 / 4 | 6 / 2 | 1 / 0 |
| empty-local | 1000 | 1000 / 1000 | 1004 / 4 | 0 / 0 |
| mixed-invalid-limit | 首步 fatal | 1 / 1 | 3 / 2 | 1 / 1 |

测试准备中的两次拒绝也保留：单原子输入被现有 `N>=2` 校验拒绝；64 Å 盒的 P8
因 slab 过薄走 M1，不能充作 M2a 证据。前者改为两个互不作用原子，后者仅将 P8
盒长改为 128 Å 并平移原子以保持 16 Å face/PBC witness，单步 multi-box fixture
同步缩放速度。通过的 P2/P4 样本保留，受影响的 P8 单独重跑。

首轮 science 在任何 trial 启动前被采集器的身份检查误拦截：完整 binding snapshot
包含动态 `cpu.stat` 与采样时间。只读逐字段核对确认二进制、输入、环境与映射一致，
差异仅为这些观测字段。修正后保留原冻结记录，在第一条样本前重新冻结相同设计；
quota/cpuset、allowed CPUs、拓扑与映射仍严格检查，实际 affinity audit 未放宽。
回归覆盖观测变化可接受、映射/资源约束变化必须拒绝；没有删除科学或性能样本。

<a id="o7-results-20261023-配对性能与采用结论"></a>

### 配对性能与采用结论

**60/60** 正式 trial 通过退出、模式/绑定、时长及科学文件检查；同配置同配对块的
thermo/neighbor 文件逐字节一致。正式段为 **23.396–37.801 s**，均超过 10 s。
没有无效性能样本、按速度删样本或触发区间宽度的追加规则。全部正式段 CPU 为 null。

单位 ms/step，为五个独立进程的中位数；比值 >1 表示 O7 更快，区间按块配对。

| 配置 | 原 full | O7 full | 原/O7，95% CI |
| --- | ---: | ---: | --- |
| carbon/P2/HostStaged | 15.709546 | 15.674100 | 1.002261 [0.989047, 1.009210] |
| carbon/P2/CudaAware | 16.402254 | 16.470661 | 0.995847 [0.993353, 1.005692] |
| water/P4/HostStaged | 7.694553 | 7.691280 | 1.000426 [0.992224, 1.014353] |
| water/P4/CudaAware | 8.718616 | 8.746148 | 0.996852 [0.989396, 1.007080] |
| water/P8/HostStaged | 5.562994 | 5.545631 | 1.003131 [0.997933, 1.007605] |
| water/P8/CudaAware | 6.490378 | 6.471721 | 1.002883 [0.988864, 1.018215] |

六个区间全部跨过 1，不能宣称稳定整步加速，也没有观察到统计显著退化。
按实施前冻结的采用条件，**采用**这项语义等价、实际少一次 collective 的最小实现；
性能结论为 **INCONCLUSIVE**，不把减少调用次数等同于已实现墙钟收益。
生产仍使用 HostStaged/full、overlap=off；本轮没有重做 overlap 的采用决策。

host decision 墙钟包含前序 GPU/D2H 等待与进程到达差，不能全部计作网络时间；
本轮没有做这种拆账。未锁频、非独占和内存驻留未知的限制保留。

<a id="o7-results-20261023-重算入口"></a>

### 重算入口

```text
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o7.py analyze --root dmgmd-overlap-o7-20261023
```

精确门槛命令见独立目录的 `gate-commands.sh`、`gate-continuation-1.sh` 和
`remaining-gates-and-main.sh` 及修正采集器后的 `continuation-main.sh`；正式 trial 的 `launch.json` 包含实际命令与环境，
`result.json`、独立 rank stdout/stderr、绑定 audit、遥测、输入及科学输出哈希均保留。
最终报告不代表未运行的 100000-step release、跨节点或其它硬件/输入。
