# MPI 验收入口

## 检查设备绑定与 CUDA 缓冲通信

从仓库根目录执行。`GPU_IDS` 填 4 张已分配的 GPU，按测试使用的顺序排列。`GPU_ID` 填其中一张，供单卡 golden 使用。`WORK_ROOT` 指向可写位置，下面各个 `--work-root` 都应是尚不存在的目录。

```bash
source ../env/md-mpi.sh
GPU_IDS=0,1,2,3  # 示例，换成分配的设备
GPU_ID=0
WORK_ROOT=../scratch/mpi-checks-new
cmake --build build -j 8
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices "$GPU_IDS" --ranks 4
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device "$GPU_ID"
```

启动数值矩阵前，确认预检通过，并核对每个 rank 的 GPU 身份和实际后端。权限问题按 [AGENTS.md](../../AGENTS.md) 诊断。环境仍须使用同一套 Open MPI 与 UCX。全量复制、迁移和局部域分解驱动分别提供 `--help`。

NHC 和 BDP 的双路径、双后端、根进程链与随机数状态，以及新作业重启，使用[恒温器测试](../thermostats/README.md)。目前这两个系综只支持默认分区和重叠选项。下文 O 系列用于原路径与实验开关回归。支持组合见[系综标准](../../docs/standards/ensembles.md)。

<a id="o3-halo-生命周期"></a>

## 检查 halo 生命周期（O3）

```bash
python3 -u tests/mpi/run_halo_lifecycle.py --candidate ./build/dmg-md \
  --devices "$GPU_IDS" --work-root "$WORK_ROOT/halo-lifecycle"
python3 -u tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2,4 \
  --backends HostStaged,CudaAware --devices "$GPU_IDS" --partition-modes off,serial,verify \
  --work-root "$WORK_ROOT/halo-domain"
```

生命周期驱动重做四卡预检，调用独立测试程序 `build/tests/dmgmd_mpi_halo_lifecycle_tests`。用 `--test-executable` 可指定其他构建的路径。此驱动需单独启动。保存精确命令、环境、标准输出、标准错误与 `results.json`。

正常矩阵覆盖 HostStaged 和 CudaAware，分别用 P1/P2/P4 测试。检查计数 `1/0/17/2/1` 变化时的容量增长和复用、两个分量、各方向数据、立即完成和只推进模式、重复完成及精确字节记账。

`receive-first` 和 `send-first` 让测试包装器暂缓观察一组 `Testall` 完成。接收侧验证 GPU 与 unpack 完成，发送侧验证源缓冲区何时可复用。该测试验证状态条件。实际网络完成顺序和计算通信重叠另用实测判断。

故障矩阵只让 rank 0 注入错误。覆盖非法 count、第二次 send post、`Waitall`、`Testall`、unpack event、重复 begin、在途 host face 操作、非法 token、未 finish 的析构、启动自检时第二次 post，以及 HostStaged 的 D2H/H2D。每个负例须使整个作业非零退出并打印指定的 halo 原因。超时判失败。GNU 链接包装器仅编入独立测试程序。

数值驱动用 P1 参考比较 `full`、`serial`、`verify` 的逐原子能量、力、virial、轨迹与重启。还核对实际分片、回退、空 rank、多段运行、skin、重建、迁移和字节计数。原 O3 验收另与冻结的 O2 产物逐字节比较。证据见 [O3 验收](../../docs/status/validation/communication-computation-overlap.md#halo-lifecycle-20261015)，接口见 [halo 生命周期标准](../../docs/standards/halo-lifecycle.md)。

<a id="o4-默认关闭的实际重叠"></a>

## 检查实验重叠路径（O4）

先完成环境预检、单卡 golden、CTest 和 O3 测试，再显式启用各重叠组合。

```bash
python3 -u tests/mpi/run_overlap_schedule.py --candidate ./build/dmg-md \
  --devices "$GPU_IDS" --work-root "$WORK_ROOT/o4-schedule"
python3 -u tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2,4 \
  --backends HostStaged,CudaAware --devices "$GPU_IDS" \
  --partition-modes off,serial,verify,overlap-wait,overlap-poll,verify-wait,verify-poll \
  --work-root "$WORK_ROOT/o4-domain"
```

驱动清除父 shell 的 DMGMD 变量，按白名单设置分区、重叠和追踪开关。核对 `launch_environment`、程序的 CONFIG、实际分片及进度次数。`overlap-wait` 和 `overlap-poll` 使用 `partition=serial`。`verify-wait` 和 `verify-poll` 检查有效 R/A、中间量、逐原子全量及 边界副本的污染值。每种 rank 和后端的 7 种模式须产生逐字节相同的科学文件。沿用 manifest 容差、通信字节和请求/tag 判据。

`build/tests/dmgmd_overlap_schedule_tests` 链接生产 runtime 和主程序，只在测试链接时包装 MPI/CUDA API。它暂缓发布接收请求，直至 early event 完成，并记录未完请求。`poll` 路径须实际触发 8 次上限。负例检查 rank 0 单边 `Waitall` 错误、非法或不兼容开关、rank 间配置不一致。负例必须整作业非零退出，超时判失败。

在正常作业中判断 GPU kernel 与请求在途是否重叠时，另用 profiler 观察设备执行。`DMGMD_OVERLAP_TRACE=1` 默认关闭，分别记录主机单调时钟与 CUDA event 耗时。两种时间源经过校准才可合并。证据及重算方法见 [O4 验收](../../docs/status/validation/communication-computation-overlap.md#overlap-o4-20261016)。

<a id="o8-thermo-消费与实际调用次数"></a>

## 核对热力学量的实际调用（O8）

预检通过后，用相同输入比较 O7 的可执行文件与候选。`BEFORE` 指向 O7 二进制的绝对路径。探针通过预加载注入，只用于检查消费者、调用步号和原子所有权。正式墙钟测试使用无探针运行。

```bash
python3 tests/mpi/run_thermo_demand.py \
  --before "$BEFORE" --after ./build/dmg-md \
  --probe ./build/tests/libdmgmd_thermo_demand_probe.so \
  --devices "$GPU_IDS" --work-root "$WORK_ROOT/thermo-demand"
```

脚本覆盖 P2/P4 两种后端，保存 `results.json`。具体判断见[热力学量按需计算标准](../../docs/standards/thermo-demand.md)。

## 核对生产者调度（O8.6）

显式传 `--schedule producer-compact` 选择原 E/H 紧凑布局，传 `--schedule producer-geometry` 选择按 Verlet 参考坐标 q 做几何内缩。两者支持 `off,serial,verify,verify-wait,verify-poll`。紧凑布局的 late 列表为空时跳过两次生产者调用。几何条件允许原 I 为空，窄条带可记录 `empty-early` 并回退到完整计算。

受控测试 `run_overlap_schedule.py` 用 128 Å、16 原子的测试体系验证几何路线在 P2/P4 实际执行 early。原窄条带由域分解矩阵验证数值与回退。紧凑网格、列表覆盖、污染值和参考判据见[分区标准](../../docs/standards/domain-partition.md#o86-producer-紧凑与几何调度)。
