# CPU 使用率后续研究

类别：辅助诊断计划。状态：**IN PROGRESS**（C0/C1 完成；C2/C3 待执行）。
更新日期：2026-10-16。

已有[CPU 采样合同](../standards/cpu-sampling.md)、[C1 验收](../status/validation/cpu-sampling-c1-20261009.md)
与[O1 固定资源窄基线](../status/performance/overlap-baseline-20261012.md)。采样不执行绑定；
正式 C2/C3 使用[MPI CPU/NUMA 入口](mpi-cpu-binding.md)交付的策略。O1–O3 已通过手工映射
与 C1 核验推进，C2/C3 不阻塞其正确性工作。

## 已知职责与限制

主调用链在初始化线程执行，没有项目自建 CPU worker/progress thread。CPU 负责解析、
GPU launch、MPI、layout/迁移和 root formatter；修速在 root CPU，自适应步长在 CPU 扫 owned
速度。MPI/UCX/CUDA 内部线程职责须实测。O3 检查 FUNNELED/main-thread，生产立即 finish。

工具可读 rank/task ticks、affinity、可见 cgroup 限制和 marker；指标/缺失语义只在合同维护。
CPU 时间无法区分 MPI 忙轮询与有效工作；墙钟、CUDA event、MPI call wall 不可相加还原 CPU
利用率。只有完整落在同机 marker 内的采样窗可归因；短步缺窗保留 null。无栈/trace 不推断等待原因。

## C2：pilot 与扰动校准

1. 固定 GPU UUID、每 rank 物理核心、NUMA、cpuset/quota 与独占条件，保存实际 affinity；
   统一绑定接口前的试验注明临时配置。
2. 复用 manifest，以 carbon_1m/water_400k、P1/2、两后端 pilot，资源允许再补 P4。
   P1 为 M1；OOM/缺卡/后端回退保留 FAILED/PARTIAL。
3. 同输入、binary、步数和资源下，随机化无采样、proc、详细 trace 三组，至少五次重复。
   校准周期/步数，正式目标每 trial 至少十个完整有效窗口；各 P 使用同一步数。
4. 主结果采用关闭 profiler 的正式 run 墙钟，另报进程墙钟和采样/marker/存储开销；
   observer ticks 只覆盖部分生命周期，不能当完整扰动。
5. 修速、输出、重建/迁移和多 run 另用 MPI fixtures，未触发路径标 NOT_OBSERVED。

无 GPU 的矩阵预览（仓库根目录）：

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile pilot --engines dmgmd \
  --cases carbon_1m,water_400k --devices 0,1 --ranks 1,2 \
  --backends HostStaged,CudaAware --dry-run
```

实际执行先通过环境/科学门槛，加入已验收的绑定及 CPU 参数；操作见
[benchmark README](../../tests/benchmark/README.md#cpu-采样c1)。交付原始 ticks、身份/环境、
随机序列、重复分布、有效窗口与 C3 配置。O1 三次窄矩阵有限校准不等于完整 C2。

## C3：正式扩展实验

- carbon_1m/water_400k × P1/2/4/8 × 两后端 × 至少五次重复，最少 80 个采样 trial；
  沿用 C2 周期、时长和统一资源策略，资源不足标 PARTIAL。
- 每 rank 报 user/system CPU-s、core-equivalent、带窗口宽度的峰值、观测线程数、context
  switches、可取得的 perf 数据和容量可见性。归一化满足合同，缺项不填零。
- 进程总量与线程分解、各 rank 峰值均不相加。非同步读取通常只能报 CPU-s subtotal，
  不能发布同窗 job 峰值；跨节点还需时钟对齐设计。
- 图与结论追溯到 trial、哈希和重算命令。缺 marker 的阶段标 UNKNOWN；无栈/trace 不归因热点，
  不因 CPU% 低直接增加线程。

实测写入 `docs/status/performance/cpu-usage-results-YYYYMMDD.md` 并链接索引，区分 pilot
与正式结果。本研究不改变 NEP、MPI 线程模型或 runtime 默认策略。
