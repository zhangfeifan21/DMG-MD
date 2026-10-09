# CPU 调度与使用率研究计划

类别：待实施计划。状态：**PROPOSED**。优先级：P0（与通信计算重叠并列）。

## 目标

为导师提供可复核的三类证据：CPU 在代码里承担什么工作、各工作出现在运行的什么阶段、进程/线程实际消耗多少 CPU。区分“线程数”“CPU 使用率”“CPU 时间”与“CPU 占用核数”，避免仅凭 `top` 截图下结论。

## 当前代码路径（静态证据）

- `src/dmgmd_main.cpp: main` 是单进程主控；初始化 MPI、解析输入和 potential/model，然后进入 runtime。MPI rank 是进程；源码搜索未发现 OpenMP parallel region、`std::thread`、`pthread_create` 或项目自建 CPU worker pool。`src/mpi_runtime.cu` 以 `MPI_Init_thread(..., MPI_THREAD_FUNNELED, ...)` 初始化并检查提供级别，表示应用只从初始化线程调用 MPI；仍需运行时测量 MPI/UCX/CUDA driver 是否另外创建内部线程。
- 每 rank 选择绑定一张 GPU（`MpiRuntime::initialize_device`）；CUDA kernel 由 CPU 发起，主要 NEP、邻居表、积分、thermo 工作在 GPU。
- CPU 负责输入解析、文件系统与 rank-0 输出/格式化、MPI 调用与通信协调、部分 host-side 管理/路由决策、GPU buffer 分配/launch。`correct_velocity` 触发时由 root CPU 做全局速度修正；M1 经 Allgatherv/Bcast，M2a 经 gather/scatter。
- M2a halo 路径在 `src/mpi_runtime.cu` 使用 `MPI_Isend/Irecv` 后立即 `MPI_Waitall`；HostStaged 会在 host/device 间搬运，CudaAware直接交给 MPI。异步 MPI API 本身不证明通信有后台进展或与 kernel 重叠。
- `DMGMD_DOMAIN_TIMING=1` 已给阶段墙钟、CUDA event、MPI wait 等分项计时，但它不是 CPU 利用率采样；耗时也不能推导 CPU 周期或有效忙碌程度。

上述为源码阅读结论，不是操作系统线程数或某台机器的 CPU 利用率实测。

## 测量设计

### A. 静态调度清点

1. 搜索 `std::thread`、pthread、OpenMP、TBB、task runtime、MPI 初始化线程级别与 CUDA callback；记录创建点、线程职责、调用 MPI 的线程约束。
2. 对入口到 step loop 列出 CPU/GPU/MPI 边界：输入初始化、普通步、rebuild/迁移步、thermo、输出、restart、最终清理。
3. 记录每个 rank 的 CPU affinity、MPI local rank、GPU UUID 与 rank 对应关系；核对容器 CPU quota/cpuset 是否限制可用核数。

### B. 动态采样

在专用节点上固定算例、rank/GPU 绑定、CPU affinity 与 Docker CPU 限额，分阶段采样：初始化、稳定普通步、重建步、输出步、结束。每组至少重复 5 次。至少选 carbon_1m 与 water_400k，并测 1/2/4/8 rank。

- `pidstat -h -u -t -p <rank-pid> 1`：采样线程级 `%CPU`、user/system CPU 时间；用 MPI launcher wrapper 或 `/proc` 父子关系把 rank PID 与 local rank/GPU 对应。
- `/proc/<pid>/stat`、`/proc/<pid>/task/*/stat`：累积 user/system ticks 与线程数，低开销计算区间 CPU time；同时保存 `/proc/<pid>/status` 的 voluntary/nonvoluntary context switches。
- `perf stat -p <pid> -e task-clock,context-switches,cpu-migrations,cycles,instructions`：仅在内核权限允许时取得 CPU 时间/周期；权限不足则不把它作为必需门槛。
- `docker stats --no-stream` 或 cgroup v2 `cpu.stat`、`cpu.max`、`cpuset.cpus.effective`：容器级 CPU 使用与限额。Docker stats 的 CPU% 口径需与可分配核数共同报告。
- 记录进程/线程 CPU affinity、NUMA 节点、系统 CPU 型号、核心/SMT、内存、OS/kernel、Docker limits、MPI/UCX/CUDA 版本；GPU 同时采样以区分 CPU 饱和与 GPU 等待。
- 先审计 `DMGMD_DOMAIN_TIMING=1` 的输出粒度：当前段级汇总/直方图不能直接提供逐步时间边界。须先有可靠 begin/end 标记才能归因到 run 时间窗；普通步、重建步、输出步的细粒度归因需要可关闭的时间戳/trace 标记及同机时钟对齐。单独报告采样、标记与 profiler 的额外开销，不能把详细 timing 的结果与未开 profiler 的绝对性能混作一组。

### C. 必报指标

每 rank 和全 job 分别给出：进程/线程数量及线程职责、CPU affinity、CPU time (user/system)、平均/峰值 CPU core-equivalent、按可用 CPU 核数归一化的利用率、context switches、CPU migrations、阶段墙钟、CPU/GPU/MPI wait时间、CPU/GPU使用曲线。明确百分比按单核 100% 还是整机 100% 计。报告中给中位数和离散度，并关联精确输入/二进制哈希。

## 验收产物

- `docs/status/cpu-usage-results-YYYYMMDD.md`：静态线程图、测量主机与容器限制、精确命令、原始数据位置、CPU利用率按 rank/阶段图表及重复性。
- 可复跑采样脚本和解析器放入 `tests/benchmark/` 或 `scripts/`；CPU采样须可关闭，默认不改变 runtime。
- 首轮先测量与建立可视化，不因 CPU 利用率低直接增加 CPU 线程。只有 profile 证明热点后再设计优化和 correctness/performance A/B。

## 注意事项

多 MPI rank 的每个进程通常有一个主线程，但 MPI、CUDA driver、UCX 可能创建内部线程；必须实测并识别。主线程在同步 MPI/CUDA 调用中阻塞会显示低 CPU，这不等同于 CPU 调度失败。宿主机 `top`、容器统计、进程统计的分母不同，不能直接比较。CPU profiling 结果只代表记录的节点、容器配额和 workload。

## 细化目标与完成边界

本计划回答四个问题：谁使用 CPU、在哪个可识别时间窗使用、消耗多少 CPU 时间、
这些消耗如何随 rank 数/后端变化。首轮交付是可复跑证据链，不以增加线程或降低 CPU% 为目标。
静态职责、OS 实测、热点归因必须分别标注；不能仅从线程名或低 CPU% 推断阻塞原因。

| 阶段 | 具体目标 | 完成判据 | 后续依赖 |
|---|---|---|---|
| C0 | CPU 职责审计与指标合同 | 调用链、已知/未知、采样 schema、阶段可观测性明确 | C1；可与 O0 独立开展 |
| C1 | 低扰动采样与解析工具 | CPU-only 测试、端到端 smoke、异常退出保留数据 | C2；O1 复用 |
| C2 | pilot 与串行基线实测 | rank 映射可靠、采样开销已量化、原始数据可重算 | O1、C3 |
| C3 | 扩展实验与结论 | 正式重复实验、按 rank/阶段图表、限制与热点证据 | O5 复用同一采样器 |

执行顺序建议：C0 → C1 → C2 → C3；与 overlap 的联合顺序见
[通信计算重叠计划](./communication-computation-overlap.md#联合执行顺序)。
资源不足允许提交部分矩阵，但必须标为 PARTIAL，不得将未跑的 8 rank 或多节点写成通过。

### 指标合同（由 C0/C1 落实到机器可读输出）

- 每条记录带 schema version、trial ID、hostname、rank/local rank、PID、进程 starttime、
  TID（线程记录）、单调时钟时间戳、采样间隔、GPU UUID、run segment。PID 要与 starttime
  联合识别，处理线程出生/退出及 PID 重用；原始计数保留，不只保存百分比。
- `CPU_seconds = Δ(utime + stime) / CLK_TCK`，`core_equivalent = CPU_seconds / Δwall`，
  单核口径 `CPU% = 100 × core_equivalent`。进程总量与线程明细是两种视图，不可再次相加。
  分别报告 user/system；采样峰值必须同时报告窗口宽度，不称为瞬时峰值。
- 有效 CPU 容量考虑 affinity、cpuset 和 cgroup quota；quota 可能与其他 rank 共享，不能为每个
  rank 各自分配整个容器额度后求和。单独报告逻辑 CPU 数、SMT、quota core-equivalent、
  cgroup throttling；跨节点 job 总量按各节点有效容量汇总，不能直接合并不同时钟的时间戳。
- task 级 context switches 从相应 task/status 获取；进程级与线程级统计注明范围。
  perf 不可用时 cycles/instructions/migrations 标记 unavailable，绝不填零。
  单靠 `/proc`、pidstat 无法准确拆出 GPU wait/MPI wait；这类归因另需 trace/栈证据。
- 1 秒采样不能区分毫秒级 MD 步。没有可靠 run 标记时只给进程观测窗口利用率；有段标记而无可对齐细粒度 trace 时只给段级，细粒度阶段写 UNKNOWN。
  profiler 组与低开销组独立运行；短命线程遗漏和采样误差需进入报告。

## C0 审计与 C1 设计交接（2026-10-08）

**C0 COMPLETE（静态审计/设计）；C1–C3 未执行。** 总计划保持 PROPOSED，以下接口为
待实现合同，不表示已有采样工具、阶段 trace 或 CPU 利用率实测。本轮只修改本文件。

### 审计基线、范围与证据定位

- 仓库 HEAD：`1d704ef6a5ad65570b4ac781bb381a06d224cc95`；参考 HEAD：
  `9d23496e41319b9e2af5221a7df6285387401d1e`。下文行号相对此工作树，符号名为长期定位锚点。
- 开始时本仓库已有修改：`README.md`、`docs/README.md`、`docs/plans/multi-node-io.md`、
  `docs/plans/risk-and-backlog.md`、`docs/status/current.md`；未跟踪文件为本计划、
  `docs/plans/communication-computation-overlap.md`、
  `docs/status/benchmark-multigpu-results-20260926.md`。全部保留；参考仓库 `git status --short`
  为空。未提交、未推送、未构建、未运行 MD/pilot/正式矩阵。
- 已读 `AGENTS.md`、本计划、通信重叠计划、`docs/standards/` 全部八份标准以及
  `tests/benchmark/README.md`、`manifest.json`、实际 runner。旧标准部分引用的是参考树行号：
  例如 gpumd-runtime-audit 的建议 rank-0 解析不能代替当前每 rank 解析的源码事实；
  kernel-inventory 对 neighbor.out 的旧 UNKNOWN 由本次当前实现证据补足，不在 C0 改标准。
- 复核入口（只读）：`rg -n 'std::thread|std::jthread|pthread_|#pragma[[:space:]]+omp|omp_|tbb::|std::async|thrd_create|clone\(|fork\(|cudaLaunchHostFunc|cudaStreamAddCallback|MPI_Init_thread|MPI_Query_thread|MPI_Is_thread_main' src include CMakeLists.txt scripts tests/benchmark`；
  用 `rg -n 'run_replicated|run_segment|run_domain_segment|correct.*velocity|recompute_and_commit|do_migration|domain_timing_marker|begin_mpi_wait' src` 沿调用核对。
  搜索无命中是该源码范围的负证据，不是 OS 线程数的证明；CUDA `threadIdx` 不是 CPU 线程。

### 主线程职责与 CPU/GPU/MPI 边界

| 位置、符号与调用点 | 静态事实、所有权和需要测量的部分 |
|---|---|
| `src/dmgmd_main.cpp:29 main`，37、52、70–73 | 每 rank 主线程构造 MpiRuntime，CPU 解析完整 run/model/potential metadata，再调用 `run_replicated`。输入解析不是只在 root；文件读取、分配和解析 CPU 开销在正式 run 前。 |
| `src/runtime.cu:1245 run_replicated`，1265–1287、1321 | 文件 fingerprint 和 MPI 一致性检查 → `initialize_device` → 每 rank ordinary `NEP` 参数加载 → eligibility → `run_local_domain` 或 `run_m1_replicated`。P=1 恒为 M1；M2a 初始化仍保留每 rank 完整 host identity，不能称为完全分布式输入。 |
| `src/mpi_runtime.cu:375 Impl::bind_cuda_device`；805 `initialize_device`；603 `log_startup` | 可见一张时取 device 0，否则取 local rank；UUID 在 shared communicator 内 allgather 去重。启动记录含 rank/local rank/hostname/device/UUID/backend/self-test，**不含 PID**。GPU ordinal 不是宿主物理卡身份。 |
| `src/runtime.cu:783 run_m1_replicated`，865–875 → `run_segment:637`，687–776 | 段首一次 force；每步修速/可选 adaptive → owned VV1 → indexed position Allgatherv → 全 N NEP → 所有权切换 → owned VV2、thermo Allreduce、可选 GPU 缩速 → 输出。CPU 发射 kernel、管理 buffer、执行 MPI 和 formatter，NEP scratch 全 rank 重复计算。 |
| `src/runtime.cu:163 RuntimeOwnership::recompute_and_commit`，调用716；`compute_owner_map:213` | P>1 每步全坐标 D2H，CPU 重算 slab map、hash Allreduce；变化时旧 owner velocity/unwrapped Allgatherv 后 adopt 新 map。P=1 提前返回。M1 是逻辑所有权迁移，不是本地粒子压缩。 |
| `src/domain_runtime.cu:1843 run_local_domain`，1889–1999、2144 → `run_domain_segment:1403` | CPU 从完整输入挑 owned、排序；bootstrap GPU wrap、下载、root 随机速度初始化（输入无 velocity 时）与 Bcast，建立 halo/layout。段首 force 在1484–1491，每段延续缓存；初始化不等于普通 step。 |
| `src/domain_runtime.cu:1500–1637 run_domain_segment` | 修速、adaptive → owned VV1/wrap → `inspect_domain_cache` GPU 位移/reason 检查、标量 D2H → host MAX/OR collective。只有 displacement 超阈值触发 rebuild；几何跨 slab 本身不立即迁移。普通步1627调用 `refresh_ghost_positions:880`，随后1637 `launch_domain_force`。 |
| `src/mpi_runtime.cu:1288 exchange_p2p_indexed_device_soa`，1333–1420；`Impl::p2p_exchange_bytes` 722–735 | GPU pack → HostStaged pinned D2H 或 CudaAware device-ready synchronize → 两方向 Isend/Irecv 后立即 Waitall → H2D 或 output synchronize → GPU unpack。请求发起非 CPU worker，也不证明后台进展/通信计算重叠。 |
| `src/domain_runtime.cu:1606–1622` → `do_migration:936`、`exchange_halo_membership:763` | rebuild 有 routing 时：owned dynamics D2H、CPU fractional/路由/序列化、Alltoall counts + Alltoallv records、CPU 排序/校验；随后重建 membership。无 routing 时也下载 owned 并重建 membership。membership 两轮 host p2p（count/record）、CPU layout/plan → upload、workspace 更新、epoch 发布；GPU neighbor 重建随 force。 |
| `src/domain_runtime.cu:1108 launch_domain_force` → `src/gpumd_compat/nep.cu:1230 NEP::compute_domain` | local stride；dependency centers 的邻居/descriptor/partial，owned centers 的 radial/many-body/ZBL；CPU 检查/launch，主要算术在 GPU，不能把 ghost scratch 计入物理输出。`src/gpumd_compat/neighbor.cu:346` 是 `thrust::exclusive_scan(thrust::device, ...)`。 |
| `src/domain_runtime.cu:1644–1699` → `compute_domain_thermo:1169` | owned VV2，GPU 8项 local sums → `allreduce_sum_device` → GPU normalize → thermo D2H；CPU 计算温控 factor、GPU 缩速。thermo 每步计算，不受 dump 间隔控制。 |
| `src/runtime.cu:551 adaptive_time_step`；`src/domain_runtime.cu:1207 adaptive_domain_time_step`，调用700/1514 | 启用 maximum_distance 才 D2H velocity，**CPU 扫描 owned 最大速度**并 MPI MAX。不能把自适应步长归为纯 GPU reduction；默认 benchmark 不启用它。 |
| `src/runtime.cu:577 correct_device_velocity`，调用695；`src/domain_runtime.cu:1246 correct_domain_velocity`，调用1509 | M1：先 velocity Allgatherv，root D2H/CPU 修正/H2D，再 device Bcast。M2a：counts、gid、owned position/velocity gather，root 按 ID 恢复、CPU 修正、按 owner scatter。共同调用 `runtime.cu:930 correct_velocity_subset`（线/角动量、惯性矩阵、逐原子修正，group 模式有 CPU 子集扫描）。触发条件 `step % interval == 0`，包括段内 step 0；与 dump 的 step+1 不同。 |
| `src/domain_runtime.cu:1341 gather_domain_snapshot`，调用1736；`src/runtime.cu:1039 output_order`、1106 `write_xyz`、1208 `write_restart`，调用两条 loop | owned 数据收集至 root、稳定 ID/input-slot 顺序、CPU 格式化/文件 I/O；thermo/XYZ/restart 只由 root 写用户目录。非 root 仍参与 gather，等待时间可受 root I/O 影响，但程度 UNKNOWN。 |
| `src/gpumd_compat/nep.cu:1262、1318–1351`；`src/domain_runtime.cu:1982 neighbor_record_sink`；`src/runtime_internal.hpp:198 RankIoIsolation` | 隐式 neighbor.out 每1000次 force（含首次），M2a 下载 count、CPU 最大值、两次 MPI MAX、root 写文件；它在 NEP 内，不在 scientific-output 区间。M1 非 root ordinary NEP 的文件进入本机 scratch。`finish`/析构负责恢复 cwd/清理，不能据“用户输出只 root”推断其他 rank 无文件系统工作。 |
| `src/domain_runtime.cu:2141–2173`；`src/runtime.cu:865–897`；`src/mpi_runtime.cu:356 Impl::~Impl` | run 前 CUDA sync+MPI barrier，run 后 CUDA sync 与 timing reduce；结束有 rank-I/O finish、buffer 析构、communicator free/MPI finalize。`phase=total` 从所选 runtime 开始，既不覆盖 main 解析/MPI init，也不覆盖全部析构/finalize；不是进程全生命周期。 |

只读参考核对：`../gpumd-reference/src/main_gpumd/main.cu:29 main` →
`run.cu:211 Run::perform_a_run`，250 修速；修速实现在 **`src/main_gpumd/velocity.cu:210、273`**
（不是旧审计中部分引用的 `src/model/velocity.cu`）。
`src/force/nep_multigpu.cu:1416 NEP_MULTIGPU::compute`、1588 device 切换、1758–1762 各 GPU
同步与回到 GPU0；这是单进程多设备参考路径。当前 `CMakeLists.txt:134–180` 明确列举本地
compat/runtime 与 CUDA/MPI 链接，不编译/链接参考树，不能把参考多卡 stream 当作本项目 CPU 线程。

### 线程分类及 MPI 调用约束

| 分类 | 证据与结论 | 运行时待证 |
|---|---|---|
| 应用自建线程 | 上述搜索范围未发现 C++/pthread/OpenMP/TBB/task worker 创建；main → runtime 为同一 host 调用链。`domain_timing_marker`、`neighbor_record_sink` 是同步调用的 lambda（nep.cu:1244、1338），不是 CUDA host callback/另一个线程。 | 实际主线程 TID、是否有动态加载组件另建线程仍需 `/proc/task`；不能写“进程恰有1线程”。 |
| 第三方可能线程 | `MpiRuntime::Impl` 的 MPI_Init_thread、Open MPI/UCX 组件加载；`bind_cuda_device` 的 CUDA 初始化、GPU_Vector 分配/copy；device-policy Thrust scan 均是第三方入口。构建只证明链接与调用，不证明库内部 worker 数。 | MPI/UCX/PMIx/PRRTE/CUDA driver 的线程数量、出生时刻、progress 模式、忙等/阻塞和亲和性 UNKNOWN。栈或库映射才能归属；线程名只能线索。 |
| 外部进程/工具 | `tests/benchmark/run_benchmark.py:205 execute` 的 Popen 启动 mpiexec，循环同步调用 `snapshot:152`/nvidia-smi；`OMP_NUM_THREADS=1` 在410–413设置。 | launcher/daemon、sampler、nvidia-smi/perf/pidstat 分开记账，不充当 rank、不并入 rank CPU；OMP 环境不能限制所有库线程。 |

`src/mpi_runtime.cu:282–292` 在尚未初始化时请求并检查 **MPI_THREAD_FUNNELED**：应用 MPI
调用应限于初始化线程。正常 main 满足此结构；当前没有每次 MPI 调用的 thread-ID guard。
若 MPI 已初始化，构造器跳过 init，**未调用 MPI_Query_thread/MPI_Is_thread_main**，不能声称
嵌入场景也验证了线程级别。C1 sampler 用独立进程读 `/proc`，不得从采样线程调用应用 MPI，
不得趁测量增加 progress thread；FUNNELED 也不禁止 MPI 实现内部创建线程。

### DMGMD_DOMAIN_TIMING 可观测性与扰动

开关解析：`domain_runtime.cu:381 domain_timing_enabled`，1869启用、1872一致性检查；仅 M2a
消费。host 计时是 `steady_clock`（398–407），event 读取为 `cudaEventElapsedTime`（410–415）。

| 采集区间 | 真实口径与局限 |
|---|---|
| setup（1484–1498）、decision（1553–1602） | setup 只包段首 force host 调用及其设备区间，不是 MPI/CUDA/输入/bootstrap 初始化。decision 包 GPU 发射、D2H 和 MAX/OR 的 host 墙钟。 |
| migration（945–1098）、membership_layout（773–853）、allocation_upload（855–870） | 都是混合 host 墙钟，含对应通信/copy/同步，不是 CPU time。无路由 rebuild 的 `download_owned_dynamics` 在这些子计时之外，但从 accounted_host 扣入整个 rebuild，故不可仅把打印的 host 分项加 remaining 当严格完备分解。 |
| halo（mpi_runtime.cu:1333–1420） | pack/unpack 用事件；transfer_wait 从 pack-end 发射后到 unpack-begin 前，HostStaged 含 pinned reserve/D2H/p2p/H2D，CudaAware 含前后 device synchronize。没有独立 D2H/H2D 时间；MPI 子区间包含 Isend/Irecv 的发起及 Waitall，不是单独 Waitall。 |
| cell_neighbor / nep（nep.cu:1244、1315、1351、1463） | 事件0→1为 cell/neighbor/typewise filter（普通步也有 filter），2→3为整段 descriptor/force；周期 neighbor count 下载/CPU/MAX/I/O 位于1→2，非空域不计入这两个设备字段，空域分支略异。未分 interior/boundary。CUDA 事件是 stream 时间区间，可能含 host 提交间隙，不等于 kernel 活跃时间。 |
| integration / thermo（domain_runtime.cu:1506–1713） | integration_host 含修速、adaptive、VV launch/温控等，修速没有独立字段。thermo_device 累加0→1和2→3，排除中间MPI；thermo_host 包发射、通信和D2H等待。两种口径不可相加。 |
| scientific_output / remaining / mpi_wait（1725–1786） | scientific_output含snapshot gather、formatter/I/O，也含无输出步的分支检查；不分XYZ/restart。remaining是host墙钟余项，不是CPU忙碌。mpi_wait来自 `mpi_runtime.cu:50–65 begin/end_mpi_wait` 包围的调用（如1170、1359、1432、1454、1576、1637、1689）；与decision/thermo/output等重叠，非全部作业MPI、更不是OS off-CPU或网络传输时间。 |

`run_domain_segment:1459–1478` 创建22个 CUDA events，后续 record/elapsed/host clock 和直方图
更新有额外成本。event_seconds 本身不显式 synchronize；按1669–1676利用下一步 thermo D2H
读取前一步 thermostat，最终1793显式 `cudaDeviceSynchronize` 收尾。调用者2147仍再同步；
因此不是逐kernel新增同步，但开启计时确有段末同步调用、事件管理和归约/打印扰动。
`log_segment_timing:430–565` 经 `mpi_runtime.cu:1269 reduce_sum_min_max_doubles` 做三次 Reduce，
只 root 输出 sequence × ordinary/rebuild 的 count、rank mean per-step、rank min/max total、
step极值/合并直方图，另有 setup、step_loop summary；没有逐rank原始时间或绝对单调时间戳。
step_loop_wall 在1802取得，含最终event收尾但不含随后汇总/销毁；外围 phase=run 包含这些开销。

**对齐边界**：现有 `DMGMD_TIMING`（mpi_runtime.cu:1759）只有时长；stdout接收时间还含缓冲、
I/O forwarding和收集延迟，不能倒推出准确 run 起止。无新标记时只能可靠报告 wrapper/进程
观测窗口 CPU；run段仅保存独立墙钟，CPU归因标 UNKNOWN。即使开详细 timing，也无法把
CPU样本可靠分给初始化、warmup/正式段、普通/rebuild/迁移/修速/输出或finalize；step编号/直方图
不是时间线。需要默认关闭、同机单调时钟的begin/end标记后才能段级归因，毫秒级细分还需trace。
CPU busy/poll/sleep、GPU wait/MPI wait、真实网络进展均另需栈/调度/设备trace；本轮未测。

### C1 最小接口（拟定，当前 CLI 尚无这些能力）

复用 `tests/benchmark/run_benchmark.py:285 main` 的解析配置、trial顺序、输入哈希、
metadata/result保存；在 `execute:205` 接入采样生命周期。拟新增 `--cpu-sampling off|proc`
（默认off）、`--cpu-sample-interval SECONDS`（与现有GPU `--sample-interval` 分离）、
`--cpu-output PATH`（默认trial内cpu/）；均要 help/dry-run 展示。分析入口拟为
`python3 tests/benchmark/analyze_cpu.py TRIAL_DIR --output OUTPUT_DIR`。这些名称是C1交付目标，
不是本轮可执行命令；C1可按项目风格改名，但必须同步本节、README和测试。

1. runner在mpiexec的rank executable位置插入wrapper；wrapper从OMPI_COMM_WORLD_RANK/
   LOCAL_RANK读取身份，写rank注册记录后 **exec候选程序**，保持PID/starttime。
   不预先初始化CUDA/MPI、不在rank中新增CPU线程。采样器独立进程；注册/握手写本机绝对路径，
   不依赖rank之后chdir，输出文件不混入MD科学文件。exec确认用`/proc/PID/exe`匹配候选与哈希；
   exec前wrapper CPU单独标boundary，不当candidate初始化。读取不到exec边界则初始化首尾不完整。
2. 注册rank与现有DMGMD_MPI按trial+hostname+world rank关联，local rank/size一致、UUID唯一；
   保存请求可见卡、实际device/UUID、后端、自检和原始日志。将32 hex CUDA UUID与nvidia-smi
   UUID规范化比较，保留原串。映射状态verified/conflict/unavailable；无日志的早退保留PID记录，
   GPU=null，不能靠CUDA_VISIBLE_DEVICES猜实卡。CPU采样可先开始，GPU映射后补。
3. sampler以绝对deadline采样，逐对象记录read begin/end而非假设同时读取；超期计missed，
   不连续补采伪造时间序列。结束flush并原子写summary；超时/中断保留jsonl与退出原因。
   只回收本trial注册且PID+starttime核验的sampler/wrapper；参考现有stop_process_tree:175，
   不按进程名kill、不杀无关任务；wrapper exec不得破坏现有rank后代回收逻辑。
4. C1先交付进程窗口采样与marker解析接口；生产标记由C2/O1另行接入。拟marker记录
   `{rank, pid, process_start_ticks, clock_id, t_ns, sequence, phase, edge, global_step, epoch}`，
   edge=begin/end，phase至少run，扩展setup/step/correction/migration/output；用嵌套关系保留重叠。
   必须在调用位置产生时间戳、不得用收日志时间替代；缺一端标incomplete。先用合成marker测试。
   `steady_clock`标准并不保证与Python clock epoch相同，后续标记显式用CLOCK_MONOTONIC，
   或记录经验证的时钟配对/误差；CUDA event时长不能直接转换为host绝对时间。
5. 详细诊断实验还需显式白名单开关接口：runner:408–416清除全部继承DMGMD_*并置timing=0；
   外部export无效。新增诊断选项必须在dry-run、launch_environment、实际日志三处一致，
   默认off，不开放任意未知变量。C1可以先保留详细trace不支持状态，不虚构现有入口。

### 最小机器可读 schema 与可重算指标

schema_version=1；UTF-8 JSONL原始记录（整数计数/ns，不只百分比），JSON/CSV派生汇总。
所有记录含type、trial_id、schema_version、host_id（hostname+boot_id）、clock_domain_id
（含time namespace身份）、source、status与reason；不适用/无权限/退出/解析错误用null+原因，
合法0与缺失严格区别。元数据改变以version引用，不悄悄覆盖。

| record type | 必需字段/单位与口径 |
|---|---|
| trial | resolved manifest/profile/CLI、case/geometry/steps/warmup/repeat/order_seed、engine、requested/actual backend/mode、完整argv与测量相关环境、source HEAD+dirty diff hash及内容、untracked相关输入hash、binary/input/manifest/generator hash、运行方式、工具版本、开始/结束状态。沿用metadata而非另造输入参数表。 |
| rank_map | world/local rank与size，hostname、PID、process_start_ticks（stat field22）、CLK_TCK、主TID、exe、注册/exec验证时刻、pid/mount/time namespace inode、NSpid链、GPU原始/规范UUID及device，mapping证据/状态。主键trial+host/namespace+PID+starttime。 |
| proc_sample / task_sample | sample_id，t_begin_ns/t_end_ns与midpoint，CLOCK_MONOTONIC/分辨率、utime_ticks/stime_ticks（stat14/15）、num_threads、state；task额外TID+task_start_ticks、comm；进程/线程各自stat/status原文或无损解析字段，Cpus_allowed_list/affinity，voluntary/nonvoluntary context switches，last_cpu（stat processor，**不是migration次数**）。 |
| capacity | capacity_id，cgroup路径/挂载root/namespace和可见祖先链、每层cpu.max的quota_us/period_us或max、cpu.weight、cpuset.cpus.effective、逐task affinity集合、逻辑CPU/core/socket/NUMA/SMT拓扑，read区间，限制是否完整可见、其他进程共享/独占状态。 |
| cgroup_sample | capacity_id、read区间、cpu.stat原始usage_usec/user_usec/system_usec/nr_periods/nr_throttled/throttled_usec等实际存在字段。是整个cgroup（可含launcher/sampler/其他job），与rank总量平行展示，不能相加。 |
| collector / lifecycle / marker | sampler PID/starttime/CPU ticks、轮次read耗时/写出字节/延迟/漏采数、thread首次/末次可见/退出区间、exec/exit/timeout信号与数据完整性；marker按上节。可选perf原始计数、time_enabled/running/事件scope、失败原因；GPU样本增加同clock begin/end和UUID。 |

计算合同（Linux /proc为C1目标平台，解析含空格/括号comm必须按stat结构处理）：

- 同身份两端：`U=Δutime/CLK_TCK`、`S=Δstime/CLK_TCK`，不加cutime/cstime（子进程），
  不再加guest_time（避免重复）；`W=Δmidpoint_ns/1e9`，`C=(U+S)/W`，单核CPU%=100C。
  PID/TID starttime变化、负差、W<=0、缺任一端则该区间invalid；不补0、不跨缺样洞插值。
  实际采样间隔不是目标间隔；保留read跨度以显示读取误差。原始整数用无损表示。
- 进程stat是rank聚合视图，task是分解视图，不再相加；`/proc/PID/status` context switches
  只代表主task，不能标签为整个进程。task/status各自取差；已观测task差之和标签为
  observed-task总量/下界（出生退出可漏），不强行与进程CPU相等。
- rank窗口平均`sum(valid CPU seconds)/sum(valid W)`，峰值为有效采样窗口max(C)，附该窗口宽度、
  count、coverage和缺口。首个样本只建立基线；新task不将首次累积值分摊到前一窗口；
  已退出task无末端样本标right-censored。两次轮询间出生又退出的线程不可见，
  num_threads也是采样值、不是总创建数；进程stat通常仍包含已退出线程CPU，保留差额但不直接
  命名“短命线程CPU”（不同读取时刻也会产生差额）。若需完整线程生命周期另用可选调度trace。
- 阶段样本只纳入两端及读取区间完全位于同一可靠marker窗内的区间；跨边界标boundary，
  不按时间比例假分配CPU；同时输出marker墙钟、observed_wall、覆盖率和observed CPU。
  没marker时sequence/phase=null，scope=process_observed，run利用率=null。
- 单机job取共同有效窗口的各rank CPU总和除共同W；rank缺失时job写PARTIAL/observed subtotal，
  不把缺失rank当0，不把各rank不同窗口的峰值相加称job峰值。独立trial给median、min/max、
  IQR、relative_range、有效/计划重复数；失败和过短trial保留。跨机monotonic不可直接拼接，
  先每机相对marker对齐；无跨机校准不提供同步全job瞬时峰值。跨机平均可报告
  各节点同逻辑阶段CPU/各自W的和，标明非同一绝对窗口，并同时列节点时长。

**affinity/quota分母合同**：有效CPU集合用每个task affinity与有效cpuset/online CPU交集，
进程potential capacity取已观测task集合并集，漏线程则完整性UNKNOWN。逻辑CPU按调度容量计，
不把SMT两个逻辑CPU解释为两个物理核心吞吐。单个独占共享资源域的简单情形：
`K=min(|union(allowed CPUs)|, quota/period及可见祖先约束)`；quota=max不限制，不能用
宿主os.cpu_count直接作容器分母。窗口容量变化时按`∫K(t)dt`归一化；若变化时刻不明，
该窗口capacity利用率null，CPU seconds仍可报告。

rank报告100C及affinity容量；若共享quota，**不人为等分quota**。可另报
`100 * rank_CPU_seconds / shared_capacity_seconds`，明确是对共同额度的贡献而非rank独享利用率。
job分母对重叠CPU集合取并集、共享quota只计一次。多个子cgroup/重叠affinity/共享父quota
不能直接Σmin：C1最小实现支持单一可见共同资源域与CPU集合互斥且无额外共享上限的资源域；
其他拓扑保留原始树并令有效容量/归一化=null、reason=unsupported_capacity_topology，
不得猜分母。祖先限额被容器隐藏时标upper_bound/incomplete、正式quota归一化null。
被其他job共享的容量是上限不是独占保证，throttling与竞争单独报告。

### 原生/容器、开销与 C1 验收

- 原生每个执行shell source `../env/md-mpi.sh`；容器用entrypoint加载
  `/opt/dmgmd/env/md-mpi.sh`（`docker/md-mpi.sh`）。采样器与rank运行在相同PID/mount/time
  namespace，读取本机/proc；优先容器内采样，完整父目录挂载布局沿用benchmark README。
  宿主采容器需显式NSpid/namespace/cgroup映射证据，否则不支持，不能按同数字PID关联。
- C1最低支持单节点Linux、原生/cgroup v2容器；v1/不可读cgroup先提供CPU ticks与affinity，
  quota归一化unavailable。容器内层root显示max不证明宿主无quota，需宿主限制元数据核验。
  多节点必须每节点本地collector/注册与原始文件回收；当前runner是单节点suite，C1可明确
  fail fast为unsupported_multi_node，远端/proc不可见不能静默变成全job统计。
- sampler/launcher/nvidia-smi等observer用独立scope记CPU/affinity/cgroup，尽量固定独立可用CPU，
  但不自动改应用绑定或quota。共享cgroup时承认observer占额度；cgroup CPU不通过减法冒充rank CPU。
  现有GPU telemetry:152–156只有UTC time，C1要附单调读取区间，不能直接与CPU ns连接。
- 校准三组：A=CPU sampler off、详细timing off；B=proc sampler on、详细timing off；
  C=详细timing/trace诊断组（明确其sampler状态）。三组保留相同既有GPU telemetry与输入/绑定，
  因此A称现有benchmark基线，不能称完全无观测。先试CPU周期1s，再按pilot时长评估0.2s；
  短smoke无两个有效样本只验接线、不报利用率。比较run seconds_max和进程wall分开，
  `overhead_B=median(T_B)/median(T_A)-1`、C同理，附波动与sampler CPU/read成本；至少5次
  独立重复且随机化组序。C2据实测设可接受扰动门槛后锁配置，C0不凭空宣称低扰动通过。
- C1 CPU-only测试应覆盖：tick/不同W、comm括号、PID/TID重用、缺失/null、短命task、
  主task context switch范围、共享quota不重复求和、容量变化/隐藏祖先、marker边界、异常退出；
  load/sleep子进程只验证趋势，mock rank日志验证映射冲突；wrapper与原runner默认off无回归。
  GPU smoke须再走环境门槛；本轮不执行。C1不应为凑阶段图直接修改生产runtime。

### 最小 pilot 与完整矩阵配置设计（均未运行）

输入/profile/几何/seed继续引用 `tests/benchmark/manifest.json` 和
`run_benchmark.py:70 geometry、93 run_input、285 main`；不另存一份数值defaults。
CPU研究只选dmgmd（现有`--engines dmgmd`），保留P=1两后端但标mode=m1-fallback、halo=N/A，
不能从P=1推导M2a。实际backend回退由parse_timing:114等拒绝纳入请求后端组。

| 组 | 配置与完成条件 |
|---|---|
| C1 smoke / C2接线 | 现有profile=smoke，case默认，P=1/2，两后端、1次；验证exec PID映射、输出与中断保留。不是CPU统计或正式性能验收。 |
| C2最小pilot | profile=pilot，显式cases=carbon_1m,water_400k，P=1/2，两后端，先继承profile重复/步数，8个配置；有资源再补P=4。先A/B，诊断组在显式接口实现后单跑。按最快配置校准时长与采样频率；大case OOM先用smoke诊断，原大case保留FAILED/PARTIAL。 |
| C2开销校准 | 选pilot中最快（样本最少）和采样成本最高的实际配置作A/B/C，至少5次；same steps/warmup/binary/affinity/quota，组序随机种子落盘。所有配置必须至少获得两个有效样本，正式目标≥10个完整窗口；根据pilot统一延长steps，不能为不同P各选不同步数。 |
| C3核心完整矩阵 | carbon_1m/water_400k × P=1/2/4/8 × HostStaged/CudaAware × 至少5次，最低80个CPU采样trial；引用standard profile并显式--repeats 5，steps/warmup由C2校准覆盖且保留resolved config。8卡不足或失败保留PARTIAL；不把缺项当通过。timing-off主组与诊断组分别汇总。 |
| 路径定向诊断（不混入核心强扩展） | 核心run_input只NVE、末步thermo，不含correct_velocity/adaptive/XYZ/restart/NVT。C2复用 `tests/mpi/run_mpi_domain.py`/`run_mpi_migration.py` 的现有fixture、CLI与触发条件，单独保存生成run.in/hash，覆盖修速、输出、实际rebuild/迁移、M1 fallback与多段run。先读对应--help，不杜撰benchmark --correct-velocity等不存在选项；未触发路径标NOT_OBSERVED。 |

现有CLI可预览的模板（仅表示计算矩阵，**没有CPU采样功能**；设备列表按实际资源提供）：

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile pilot --engines dmgmd \
  --cases carbon_1m,water_400k --devices 0,1 --ranks 1,2 \
  --backends HostStaged,CudaAware --dry-run
python3 tests/benchmark/run_benchmark.py --profile standard --engines dmgmd \
  --cases carbon_1m,water_400k --devices 0,1,2,3,4,5,6,7 --ranks 1,2,4,8 \
  --backends HostStaged,CudaAware --repeats 5 --dry-run
```

CLI已有 `--steps/--warmup/--min-seconds/--timeout` 与重复的`--mpiexec-arg`用于覆盖；
正式命令由C2将校准值写入，不复制manifest defaults。绑核策略用现有launcher参数，记录实际
task affinity核验；不要把命令中的`--bind-to core`当成生效证据。C2前环境与正确性门槛照
AGENTS执行，CPU研究不代替数值正确性，GPUMD对照也不纳入rank线程总量。

### 本轮验证

仅执行上面两条现有CLI的 `--dry-run`（加载同一环境入口，Python禁写bytecode），分别解析为
8和80个计划trial；未访问GPU、未生成模型、未创建benchmark结果目录。
文档空白检查与工作树内容哈希对比确认本轮仅本文件变化；参考仓库仍干净。
这验证配置与改动范围，不是采样器测试或任何性能/数值通过声明。

### 未决项与下一阶段入口

运行时线程数/库归属、MPI轮询/后台进展、实际affinity/quota与祖先可见性、CPU使用率、
observer扰动、适宜采样周期、8卡/多节点资源均 **UNKNOWN（未测）**。阶段trace/时钟配对、
显式诊断开关尚未实现；当前只能形成进程观测窗口，不能宣称已能逐run/逐step归因。
下一轮执行下方 **C1 prompt**，实现本节schema/映射/采样/解析及CPU-only测试与受控smoke；
C2再校准pilot，O1复用同一接口补时间线。本轮C0到此停止，不自动推进C1/C2/O0。

## 可直接交给 Codex CLI 的阶段 prompts

以下每个代码块可单独粘贴。路径均相对包含 `AGENTS.md` 的 **newmd 仓库根目录**。
每轮只执行指定阶段，交接完成后再派下一轮，不让多个 agent 同时修改同一工作树的公共文件。

### C0：职责审计与测量合同

```text
请执行 docs/plans/cpu-usage-study.md 的 C0，仅做静态审计和测量设计，不修改生产代码或启动正式矩阵。
先读 AGENTS.md，检查本仓库与 ../gpumd-reference 的 git status，保留已有修改，不提交、不推送。
阅读本计划、通信重叠计划、docs/standards、tests/benchmark/README.md 与 manifest.json。
从 main 沿 runtime/step loop 追踪 CPU、GPU、MPI、输出、correct_velocity、rebuild/迁移路径；
搜索项目线程创建、第三方线程入口、MPI_Init_thread 及其调用约束。输出文件+符号+调用位置证据，
区分应用自建线程、第三方可能线程、运行时待测线程；不要把源码未发现当成运行时不存在。
审计 DMGMD_DOMAIN_TIMING 的采集点、同步开销及输出粒度，指出哪些阶段无法用现有日志对齐。
在本计划内记录审计结果和 C1 的最小 schema/接口设计：rank-PID-GPU 映射、单调时钟、CPU 指标、
affinity/quota 共享分母、缺失数据、短命线程、采样器自身开销、容器/原生运行方式。
给出最小 pilot 与完整矩阵的配置设计，参数继续以现有 manifest/CLI 为事实源。
验收：每项结论可定位源码，未知项显式标注，C1 无需猜指标口径；不声称已测得 CPU 利用率。
最后报告改动文件、证据、未决问题与下一阶段入口；本阶段完成即停。
```

### C1：实现采样器与分析器

```text
请执行 docs/plans/cpu-usage-study.md 的 C1。先读 AGENTS.md、本计划及 C0 产物，检查两个仓库状态，
保留用户修改，不提交、不推送。若 C0 未完成，先补齐必要设计，不猜测其结论。
在 tests/benchmark/ 或 scripts/ 实现可关闭的 rank/线程 CPU 采样器与解析器，复用现有 benchmark
编排和 metadata 机制，不改变 runtime 默认行为。新增选项必须有 --help、dry-run 与 README。
用可靠 launcher wrapper/进程映射关联 hostname、rank/local rank、PID+starttime、GPU UUID；
容器 PID namespace、多节点远端 /proc 不可见时必须明确支持边界，不把 launcher 当 rank。
按本计划指标合同保留 /proc 原始计数及时间戳，采集 affinity/cpuset/quota/throttling；
pidstat/perf 作为可选诊断，缺失工具或权限记录 unavailable。输出 schema 化数据及汇总 CSV/JSON。
处理线程出生退出、进程提前退出、超时/中断、部分结果；退出时回收本次采样器，不杀其他任务。
添加有意义的 CPU-only 测试：tick 换算、不同窗口、PID 重用、缺失样本、共享 quota、不重复求和、
异常退出。使用 CPU 负载/睡眠子进程检查趋势，不以脆弱的精确 CPU% 做门槛。
GPU smoke 每个新 shell source ../env/md-mpi.sh，按 AGENTS.md 先环境预检；无权限时记录真实阻塞。
新增文档从 docs/README.md 链接。验收：数据可重算、采样可关闭、原有 benchmark 行为无回归。
最后交付实际 CLI 命令、测试结果、原始数据路径、支持限制和 C2 入口；不要直接跑完整矩阵。
```

### C2：pilot、开销校准与串行基线

```text
请执行 docs/plans/cpu-usage-study.md 的 C2。读 AGENTS.md、本计划及 C0/C1 交接，检查两个仓库状态，
保留已有修改，不提交、不推送。使用 C1 实际实现的 CLI，先查看 --help/dry-run，不能杜撰选项。
每个新 shell source ../env/md-mpi.sh，按 AGENTS.md 完成环境门槛；先 smoke，再 pilot。
先用可用的 1/2/4 rank、HostStaged/CudaAware 跑 carbon_1m 与 water_400k，内存不足从 smoke 降级，
记录 mode、实际后端、自检状态、CPU affinity、GPU UUID、quota 与独占条件。P=1 为基线，
不能据其推断 M2a halo 行为；不支持的后端组合明确标 N/A。
同输入、二进制、绑定、步数下分开测无采样、低开销采样、详细 trace 三组，随机化顺序，
正式开销比较至少 5 次重复；pilot 只用于选择时长/采样频率，不发表最终扩展性结论。
优先复用已有阶段标记；若需新增，做默认关闭的最小诊断修改并验证关闭路径。
没有可靠细粒度时间戳时只给 run 段归因，不能拿 timing 汇总或 1 秒采样生成逐步利用率。
保存命令、revision/dirty diff、二进制/输入哈希、环境、原始数据、采样扰动和缺失项。
写 docs/status/cpu-usage-results-YYYYMMDD.md 并从 docs/README.md 链接，注明本轮为 pilot/部分结果。
验收：rank-PID-GPU 可核对，CPU 总量可从原始 ticks 重算，给出 C3 固定配置与开销可接受性依据。
最后交接路径、实际矩阵、失败原因和重跑命令；缺少资源不等于通过，不自动进入 C3。
```

### C3：正式 CPU 研究报告

```text
请执行 docs/plans/cpu-usage-study.md 的 C3。读 AGENTS.md、C0-C2 产物，检查两个仓库状态，
保留已有修改，不提交、不推送。C2 映射和采样开销未验收时先解决问题，不扩大矩阵。
按已校准配置测 carbon_1m/water_400k，1/2/4/8 rank，两通信后端，至少 5 次独立重复；
使用现有 manifest 与显式 CLI 覆盖，记录最终解析配置。每 shell source ../env/md-mpi.sh。
不足 8 张 GPU、OOM、后端自检失败等保留日志并标 PARTIAL/BLOCKED，不补造数据。
分别给每 rank、rank 0/非 root、全 job 的 CPU 时间、core-equivalent、采样峰值、线程数、
容量归一化利用率、context switches 和可用 perf 数据，报告中位数、离散度及采样窗口。
输出可复跑分析命令和静态图：CPU/GPU 时间曲线、rank 分布、CPU 时间与阶段墙钟关系；
只有可靠标记支持的阶段才细分，其他写 UNKNOWN。跨节点先分节点对齐，不能直接拼接时钟。
结合栈/trace 区分有效 host 工作、轮询、阻塞；没有证据就不作热点归因。
更新实测 status 和索引，列出对 O1/O5 有用的基线；如发现 CPU 热点只提出带证据的下一步，
本阶段不增加 CPU worker/progress thread、不顺手优化 runtime。
验收：每张图可追溯原始 trial/哈希，结论限定在实测主机和负载，不从低 CPU% 推断需要更多线程。
最后报告结论、复跑入口、未完成矩阵与可交接产物。
```
