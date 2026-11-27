# 05: 用短作业验证配对计时

Status: completed-by-agent

Blocked by: None (can start immediately)

## 交付内容

用短作业和可手算的样本验证配对计时器，交付可被三种材料的先导和正式作业复用的区组清单、原始记录与复算结果。

## 验收条件

实施提交为 `ec14e19`，验证日期为 2026-11-27。以下证据路径均相对于 L20 的 `../scratch/paired-timing-20261127/`。勾选记录本工单计时工具的验证结果。2026-11-27，维护者审阅请求送达后，明确授权提交本次工单文档修订。

- [x] 以单调时钟从启动可执行文件或 `mpirun` 计到最后一个约定输出写完且进程退出。进程墙钟为主指标，另记启动与读入、引擎 run/loop 和分段耗时。用 thermo 时间戳计算第 1,000 步之后至结束的稳态 ms/step。
  - 六份作业记录均保存了启动与退出的单调时钟读数、引擎计时和分段读入观测。从第 1,000 步到第 2,000 步，普通 GPUMD 和 DMG-MD 的平均稳态步时分别为 3.5262 和 5.1768 ms/step。见 `completion/serial-2/job-*/work/paired-job.json`。

- [x] 在作业目录按冻结频率写 thermo，并写一帧末态结构。核对实出文件，记录无法关闭的额外输出和 rank 0 汇总成本。记录末行 thermo 到退出的尾段，任一引擎尾段超过自身墙钟 1% 时同时给出扣除尾段后的配对比值。
  - 六个作业均在同一服务器 ext4 云盘写出 20 行 thermo、一帧 5,760 原子的 restart，另有 `neighbor.out`。三个区组均触发扣尾，扣尾比为 0.6707。独立双卡 4 步诊断中，rank 0 汇集与写出的普通步合计 0.011099042 秒。见 `completion/verification.json` 和 `completion/rank0-schema/stdout.log`。

- [x] 验证两条重复数分支。正式 NPT 每格先导 10,000 步、重复 5 次，若轮间变异系数 >2%，选 5 个正式区组，否则选 3 个。正式采样前冻结。每区组各引擎另跑 1,000 步预热并保存日志，完整正式轨迹各跑 100,000 步。
  - 测试用五个先导样本验证两条分支，样本 CV 恰好为 2% 时冻结 3 个区组，超过 2% 时冻结 5 个。测试也核对了每个正式区组的预热顺序与阶段步数。见 [区组测试](../../../tests/benchmark/test_paired_timing.py) 和 `completion/paired-tests-final.log`。本单实机试跑每次 2,000 步，三材料正式采样由后续工单执行。

- [x] 用 `order_seed 20260922` 重放每个区组内按材料随机交错的顺序。独占 4 × L20，配对作业串行，P=1 用 GPU 0，P=2 用同 NUMA 的 0、1，P=4 用 0 至 3。跨 NUMA 的 0、2 只作自身强扩展补充。记录实际绑卡、CPU 总预算、时钟、功率和输出介质。
  - 固定种子重放三个区组，第一组 DMG-MD 先跑，第二、三组普通 GPUMD 先跑。独占四张 L20，本次六个单卡作业均使用 GPU 0 和相同的两个物理 CPU 核。每份 `audit.json` 保存 GPU UUID、CPU 亲和性、时钟、功率及挂载。双卡、四卡放置和跨 NUMA 限制由[资源及区组测试](../../../tests/benchmark/test_paired_resources.py)与[区组规划](../../../tests/benchmark/test_paired_timing.py)覆盖。见 `completion/frozen.json`、`completion/serial-2/job-*/audit.json`。

- [x] 用手算样本复算按区组配对的耗时比，取对数均值、自由度 `n−1` 的双侧 95% t 区间并换算回比值。区间含 1 记为未分辨。逐项报告绝对时间、稳态步时和 GPU·小时（卡数 × 墙钟小时），性能区间逐项计算。
  - 手算比值 1、2、4 得几何均值 2，自由度 2，95% 区间为 [0.357462, 11.189997]，差异未分辨。三个实机区组的普通 GPUMD / DMG-MD 墙钟比为 0.6708，95% 区间为 [0.6285, 0.7159]。两侧平均墙钟分别为 7.5068、11.1890 秒，三次各合计 0.006256、0.009324 GPU·小时。见 `stats/hand-summary-result.json` 和 `completion/verification.json`。

- [x] 用非零退出、缺末帧及身份不符的短作业验证无效样本排除。保留失败日志、区组号、两侧原始时长、快照与二进制哈希、物理参数、后端与分派。由原始记录复算结果，并按试跑规模标识先导数据。
  - 四个 CPU 子进程配对区组中，第 1 组有效，第 2～4 组分别因退出码 7、缺末帧、运行分派不符而整对排除。两侧时长及失败日志保留在 `driver/cpu-invalid-pairs/`。真实 GPU 批次标记为 `smoke`，保留输入和程序哈希、物理参数与实际运行身份。复算的 `completion/replayed.json` 与归档汇总一致。

- [x] 用一组实测步时演示预算计算。按保留与舍弃时长或轨迹步数、实测步时、固定开销、重复数或链数和卡数换算 GPU·小时，独占节点墙钟单列。交付可由 09、13、10–12 和 25 填写的明细，正式样本、预热及调试轮分别记账。先导预算超出独占窗口时，由维护者决定缩减哪些格，登记仍待测的可比项。
  - 百万原子碳单卡准备作业的实测引擎平均步时为 173.951 ms/step。按该步时与每作业约 2.052 秒固定开销，示例分别计入正式、预热及调试，合计 17.0631 GPU·小时、17.0631 独占节点小时，比假设的 16 小时窗口多 1.0631 小时。见 `stats/measured-budget-input.json`、`stats/measured-budget-result.json` 和[后续工单预算模板](../../../tests/benchmark/paired_budget_template.json)。

## 依据

[V1.0 验收规格，用户故事 10、11、12、19、21、22](../../v1-release-readiness/spec.md) · [决策 04 计时协议与 DN5 默认策略](../../v1-release-readiness/issues/04-dn5-timing-and-adoption.md#answer) · [NPT 对标矩阵与采样规程](../../../docs/status/performance/npt-benchmarks.md#performance-matrix) · [现有计时入口和 profile](../../../tests/benchmark/README.md)

## Comments

2026-11-27，Codex 认领，实施基线为 `52266e0`。先验证区组清单、短进程测量、无效样本排除与离线复算。工单 01 使用 GPU 期间，独占四卡的实机计时等待资源协调。

2026-11-27，维护者确认各作业可使用服务器上的存储。配对作业固定同一挂载并记录实际介质。

2026-11-27，完成验证。独占 4 × L20，在 GPU 0 上串行执行三个区组，普通 GPUMD 和 DMG-MD 的 6/6 个碳 Berendsen 短作业均有效。每次 2,000 步，5,760 原子。按原始时长独立复算，普通 GPUMD / DMG-MD 的进程墙钟比为 0.6708，95% 区间为 [0.6285, 0.7159]。三个区组均另报扣尾比。双卡独立验证了 rank 0 的汇集与写出成本。最终 40 项计时专项和完整 45 项 CTest 均通过。

重复数两条分支、随机顺序重放、手算 t 区间、三个故障区组的整对排除及基于实测步时的预算均已核验。交付[配对计时入口](../../../tests/benchmark/README.md#按区组运行与复算配对作业)、[逐工单预算模板](../../../tests/benchmark/paired_budget_template.json)和[实机结果](../../../docs/status/performance/npt-benchmarks.md#paired-timing-smoke-20261127)。L20 原始证据在 `../scratch/paired-timing-20261127/`，最终清单、六份原始记录、独立复算和测试日志在 `completion/`，最终批次为 `serial-2/`。
