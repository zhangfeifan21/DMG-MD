# Benchmark CPU 采样合同（schema v1）

类别：当前测试工具合同。实现入口：`tests/benchmark/cpu_sampling.py`、
`analyze_cpu.py`、`run_benchmark.py`；CLI/运行示例见
[benchmark README](../../tests/benchmark/README.md#cpu-采样c1)。不改变 runtime 默认行为。
本工具记录 affinity/cpuset/quota，不设置它们。O1 的 expected-cores/expected-numa
参数核验手工映射；统一设置策略仍为[待实施 P0](../plans/mpi-cpu-binding.md)。

## 支持范围与身份

Python 3.10+、Linux、本机 Open MPI、`--engines dmgmd`。默认关闭，开启后每 trial 一个
独立 collector；在 mpiexec 的 rank executable 位置插入 wrapper，读取
`OMPI_COMM_WORLD_{RANK,LOCAL_RANK,SIZE,LOCAL_SIZE}`，注册后等待 collector ACK 再 `execv`。
不在 rank 内创建采样线程、不初始化 CUDA/MPI；launcher 只出现在 observer scope。

wrapper 必须与 collector 的 hostname、boot ID、PID/mount/time/cgroup namespace 全部相同，
且 world size=local size、world rank=local rank。多节点、宿主跨 namespace 采容器均拒绝；
无共享注册目录的远端启动会失败，不用本机同号 PID 代替远端 rank。
容器内运行 runner/collector/ranks 支持相同 namespace；不得把 `NSpid` 当成已完成的跨 namespace 映射。

身份是 trial + host/namespace + PID + `/proc/PID/stat` starttime(field 22)；线程另加 TID 和
自己的 starttime。collector 验证 exe 路径、argv、`/proc/PID/exe` 内容 SHA-256 与候选一致后才
采 rank，故不把 wrapper/launcher CPU 算入 rank。极短进程可能来不及验证：保留注册、标 partial。
每次读 stat/status/affinity 后复核 starttime，避免读取中重用。进程退出或 zombie 不作为有效端点。

实际 GPU 只从该 trial 的 `DMGMD_MPI` stdout 关联：world/local rank、size、hostname 一致，
CUDA UUID 去前缀/横杠后 32 hex 与请求可见卡匹配且 rank 间唯一。原始 binding 和注册均保留。
缺日志/缺 exec 验证为 unavailable，冲突为 conflict，GPU=null；不从可见卡顺序猜映射。

## 文件与原始 schema

默认 `TRIAL/cpu/`；自定义目录为 `--cpu-output ROOT` 下的 `RUN目录名/TRIAL_ID/`，拒绝覆盖。

| 文件/记录 | 内容 |
|---|---|
| 上层 metadata.json、result.json | 现有 manifest/profile/CLI、顺序、源码与候选/input hashes、MPI/CUDA/CPU/GPU 环境；CPU 开启额外记录工具 hashes、HEAD dirty patch 内容/hash、相关 untracked 文件 hash、CPU 选项与结果路径/status。 |
| config.json | schema_version=1、trial/ranks、host/namespace/clock resolution、CLK_TCK、候选/hash、采样周期、诊断选项、host 可见性声明、runner 身份、逻辑 CPU/core/socket/NUMA 拓扑、trial 与上层 metadata 引用。 |
| rank-N.json、rank_map、exec_verified | hostname、rank/local rank/size、PID/start ticks、主 TID、NSpid、exe/argv、注册/exec 验证 monotonic ns；exec 前后边界左删失。ACK 文件只用于握手。 |
| samples.jsonl: proc_sample/task_sample | sample_id、pid/tid、start_ticks、CLK_TCK、utime_ticks/stime_ticks、state、num_threads、last_cpu、affinity、context switches、stat/status 原文、读取区间及中点；task 带 process_start_ticks。 |
| capacity | 每个 rank/轮次的 cgroup/mountinfo 原文、路径/mount root、capacity_id、可见祖先链、cpu.max/cpu.weight/cpuset.cpus.effective/**cpu.stat 原文**、allowed_cpus、quota_cores、容量上界与 hierarchy_complete、读取区间。cpu.stat 嵌在 capacity 中，不重复发 cgroup_sample。 |
| thread_seen/thread_lost | TID/task start ticks、首次可见和首次不可见时刻/轮次；lost 标 right_censored，不能认作精确出生/退出时刻。 |
| collector/observer_sample | collector 自身 stat/status/affinity、采样轮次耗时/写出 bytes/迟到/漏 deadline；runner、launcher 独立身份与原始计数。诊断进程另存 diagnostic_samples。 |
| diagnostics*、工具-rank-N.txt | 可选 pidstat/perf 路径/version/argv、返回码、原始输出。缺工具、拒绝权限、短命目标等 unavailable；通用汇总 cycles/instructions/migrations=null，原始 perf 数据仅供诊断，不未经 time_enabled/running 校正就发布计数。 |
| stop、collector-exit.json、sampler.stderr | 停止时刻/原因、collector 返回码、异常；截断的 JSONL 仍能解析已有完整行。 |
| summary.json/csv、intervals.json | rank/task 有效窗口、覆盖/缺样、均值与窗口峰值；JSON 另含映射、job subtotal、observer、cgroup、marker 窗、完整性/解析错误。intervals 保存可逐条复核的派生窗口。 |

所有 JSONL 记录的共同信封：`schema_version,type,trial_id,host_id,clock_domain_id,clock_id,
source,status,reason`。host_id=hostname+boot_id；clock_domain_id=boot_id+time namespace；
clock_id=`CLOCK_MONOTONIC`。采集 ns 与 ticks 为整数，工具消费应使用无损整数（JavaScript
不能无条件用 Number 存所有 ns）。不可读样本省略无从取得的原始计数字段并附 unavailable/reason，
派生值用 null，不以 0 替缺失。原始文件不可覆盖修补；重新分析只覆盖派生文件。

CPU 按绝对 deadline 调度；迟到跳过 deadline 并记 missed_deadlines，sample_id 相应跳号，
不补发伪样本。每对象 `t_begin_ns/t_end_ns` 包住读取，以中点 t_ns 表示采样时刻；对象之间并不同时。
GPU telemetry 在原 UTC 时间之外附同 CLOCK_MONOTONIC 读取区间；只在本机相同时钟域对齐。

## 指标与缺失语义

`interval(a,b)` 只接受同身份/clock/CLK_TCK、相邻 sample_id、两端 status=ok、正时间差、
非负计数差。`U=Δutime/CLK_TCK`、`S=Δstime/CLK_TCK`、`W=Δmidpoint_ns/1e9`，
core equivalent=`(U+S)/W`、one-core CPU%=100×core equivalent；不加 child/guest time。
均值是 `ΣCPU/Σ有效W`，峰值是有效单窗口最大值并附窗口宽度。覆盖率为有效 W 总和/首末有效
样本跨度，另记 unavailable 样本数与跳过的 sample_id 数；不是对整个进程寿命的覆盖保证。

进程 stat 是 rank 聚合视图，线程行只用于分解，二者**不相加**。PID/TID 重用不跨生命周期
算差；首样本只建基线，线程退出无末端样本右删失。两次轮询间出生又退出的线程不可见，
`max_sampled_threads` 不是创建线程总数。进程与线程读时刻不等，差额不能直接命名为短命线程 CPU。
`/proc/PID/status` context switches 仅主 task；last_cpu 不是迁移计数。等待类型、第三方线程职责
仍 UNKNOWN；CPU 使用时间不能区分 MPI 忙轮询与有效工作。

job 默认是所有已观测**进程** CPU seconds subtotal，P>1 异步读取的窗口一般不完全重合，
status=partial，job core-equivalent/峰值为空；`sum_rank_mean_core_equivalent` 明确是不同窗口
rank 均值之和。只有各 rank 存在完全相同的实际起止时间才给 common_exact_windows job 均值和带宽度的窗口峰值；
不插值、不将各 rank 峰值相加。O1 已有 rank-local run marker；若要正式的跨 rank
同一窗口 job 指标，仍需同步 marker/计数边界。
C1 不发布跨 trial CPU 性能统计：进程窗口含初始化、预热和退出，尚不能冒充正式 run 段。

observer 汇总与 rank 平行；collector 周期样本包含读取/写入/注册核验的 CPU 成本，但初始化、
收尾、诊断进程及短命 nvidia-smi 的完整开销不能仅靠它求和。cgroup CPU/throttling 按
host+祖先路径+sample_id 去重，缺字段/跳号/负差不算；报告各指标自己的有效 W。祖先与子
cgroup 都是重叠范围，不能相加，更不能加到 rank CPU。需要 C2 A/B 校准实际总扰动。

## Affinity、cpuset 与共享 quota

每个 rank 的 allowed_cpus 为已观测 task affinity 并集与有效 cpuset/online 集合的交集；
线程快照数不一致则 hierarchy_complete=false。逻辑 CPU 是调度容量，不等价于物理核心吞吐。
quota_cores 是可见祖先 quota/period 的最小值；cpu.max=max 不设上限。默认 scope=unknown，
隐藏祖先可能存在，保留 upper_bound，正式容量归一化 null。`--cpu-capacity-scope host` 是
操作者对完整宿主层次可见性的**显式声明**，还要求 mount root=/；容器内 root=/ 不能自行证明。
cgroup v1、不可读/逃逸路径保留原因，CPU ticks 仍可用。

同一 capacity_id 的 rank 对 allowed CPU 集合取并集，共享 quota 只算一次，
`K=min(|union CPUs|,visible quota)`。不同域只支持 CPU 集合互斥、无共同有限祖先 quota；
其他拓扑归一化 null/unsupported_capacity_topology。共享容量不是 rank 独享配额，也不保证
其他 job 不竞争。rank 的 capacity_percent=`100×rank CPU seconds/Σ(Kshared×Wrank)`，
标签为对共享上限的贡献，不人为等分 quota；需要同轮所有 rank 的 capacity。

窗口两端 capacity 签名不同则容量指标 null（变化时刻未知，不猜积分），CPU seconds 仍保留。
签名相等只说明采样时限制相同，不能检测窗口中短暂变化；C2 应固定绑定/配额并保存外部证据。
没有完整 task/祖先可见性就不声明完整归一化。

## Marker 与生命周期

可选 `analyze_cpu.py --markers JSONL`，每行 schema_version=1，包含 rank/pid/
process_start_ticks/clock_domain_id/clock_id/t_ns/sequence/phase/edge(begin|end)，可带
global_step/epoch。按身份+sequence+phase配对；缺端、重复begin、非正窗口、时钟错误记 issue。
只纳入两端读取区间完全在窗内的样本，不按比例切割边界区间。嵌套阶段可以重叠，不能相加。
无 marker 则 phase/sequence/run_cpu_utilization=null。M2a 的 O1 trace 模式现在由
`DMGMD_DOMAIN_TRACE=1` 发射 `CLOCK_MONOTONIC` 整数纳秒；benchmark runner 校验
rank/PID/start ticks/clock domain 后把 raw stdout 转成 `cpu/markers.jsonl`，再传给现有
`analyze_cpu`。它给 run-loop 与逐步 ordinary/rebuild 窗口；短步没有完整采样区间时
CPU 值仍为 null。默认关闭，`DMGMD_TIMING`/`DMGMD_DOMAIN_TIMING` 的相对耗时
本身仍不能替代绝对 marker；具体 trace 范围见 [M2a 合同](mpi-runtime.md)。

正常结束、timeout、KeyboardInterrupt/SIGTERM 均回收本次 launcher 后代与 collector。
launcher 沿已有 ancestry+PID/starttime 清理；collector/诊断工具只通过本次 Popen handle 回收，
不按名称 kill；collector 发现 runner 身份消失也自行退出。SIGKILL 无 finally 保证，已写原始行
可单独重算；collector 非零/缺结束记录使完整性为 false。rank 早退不阻止保存部分数据。
`result.cpu_status` 是采样接线/映射完整性，不能代替数值 status 或有效窗口计数；partial 令 runner
非零退出，原 benchmark 数值结果仍保留原 status。
