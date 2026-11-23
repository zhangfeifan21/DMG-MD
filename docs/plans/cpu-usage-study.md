# CPU 使用率后续研究

状态：IN PROGRESS。更新日期：2026-10-21。

CPU 采样的 C0/C1 已完成；O5 范围内的 C2 小规模校准已测，完整 C2/C3 待执行。指标和缺失值规则见[CPU 采样标准](../standards/cpu-sampling.md)，已执行范围见[C1 验收](../status/validation/cpu-sampling.md#cpu-sampling-c1-20261009)。

采样工具只观测资源使用，不设置绑定。DMG-MD 已有[固定 CPU/NUMA 入口](../standards/cpu-binding.md)；跨引擎和其他调度器的验收由[绑定计划](mpi-cpu-binding.md)维护。O1–O3 已通过手工映射和 C1 核验，继续正确性工作无需等待完整 C2/C3。

## 已知职责与限制

主调用过程在初始化线程执行，项目没有自建 CPU worker 或 progress thread。CPU 负责解析、启动 GPU kernel、调用 MPI、处理布局和迁移，以及根进程输出。修正速度在根进程 CPU 执行，自适应步长在 CPU 扫描负责原子的速度。MPI、UCX 和 CUDA 内部线程的实际工作须测量确认。O3 检查 FUNNELED 和主线程约束，生产路径在发起通信后立即调用 `finish`。

正式 NPT 计时关闭采样。CPU 耗时细分和采样扰动另跑。工具可读取进程或线程 ticks、实际 affinity、可见的 cgroup 限制和阶段 marker。

CPU 时间无法区分 MPI 忙轮询和有效计算。墙钟、CUDA event 时长与 MPI 调用墙钟也不能相加还原 CPU 使用率。只有完整落在同机阶段 marker 内的采样窗口，才能归属到该阶段。短阶段没有完整窗口时保留 null；缺少栈或 trace 时，不推断等待原因。

## C2：pilot 与扰动校准

1. 用已验收入口固定 GPU UUID、每 rank 物理核心、NUMA 和 cpuset/quota，保存实际 affinity 与独占条件。其他调度器环境先完成对应验收。
2. 复用 manifest，对 `carbon_1m` 和 `water_400k` 做 P1/P2、两后端 pilot，资源允许再补 P4。P1 为全量复制模式，OOM、缺卡或后端回退保留 FAILED/PARTIAL。
3. 在相同输入、二进制、步数和资源下，随机安排无采样、proc 采样和详细 trace 三组，各至少重复五次。校准周期和步数，目标为每个 trial 至少十个完整有效窗口，各 P 使用同一步数。
4. 主结果取关闭 profiler 的正式作业墙钟，另报进程墙钟，以及采样、marker 和存储开销。observer ticks 只覆盖部分生命周期，不能当作完整扰动。
5. 修速、输出、重建、迁移和多个 run 段，另用 MPI 测试输入检查。未触发的路径标 NOT_OBSERVED。

以下命令从仓库根目录预览矩阵，不启动 GPU 计算：

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile pilot --engines dmgmd \
  --cases carbon_1m,water_400k --devices 0,1 --ranks 1,2 \
  --backends HostStaged,CudaAware --dry-run
```

实际执行前通过环境和科学检查，再加入已验收的绑定及 CPU 参数。[诊断工具入口](../../tests/benchmark/README.md#诊断入口)

交付原始 ticks、程序身份、环境、随机顺序、重复分布、有效窗口和 C3 配置。O1 的三次小规模矩阵只提供有限校准，不能替代完整 C2。

## C3：正式扩展实验

矩阵为 `carbon_1m`、`water_400k` × P1/P2/P4/P8 × 两后端 × 至少五次重复，共至少 80 个采样 trial。沿用 C2 的周期、时长和统一资源策略，资源不足标 PARTIAL。

逐 rank 报告 user/system CPU-s、平均使用核心数（core-equivalent）、注明窗口宽度的峰值、观测线程数、上下文切换、可取得的 perf 数据和容量可见性。归一化遵守采样标准，缺项留空。

进程总量与线程分解不能相加，各 rank 峰值也不能相加。非同步读取通常只能报告 CPU-s 小计，不能发布同窗作业峰值；跨节点还需设计时钟同步。图和结论须能追溯到 trial、哈希及重算命令。缺 marker 的阶段标 UNKNOWN，缺栈或 trace 时不归因热点，也不因 CPU 使用率低直接增加线程。

正式性能结果按[性能规程](../status/performance/npt-benchmarks.md)区分 pilot 和正式采样。本研究不改变 NEP、MPI 线程模型或运行时默认策略。
