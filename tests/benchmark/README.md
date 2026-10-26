# 单节点、多 GPU 性能测试

类别：测试操作说明。参数以本目录 `manifest.json` 为准；不改变 `long_nve` 正确性验收。

当前 runner 支持 `gpumd,lammps,dmgmd`，并将 GPUMD 与 LAMMPS 一同作为 DMG-MD
对照组。DMG-MD 固定每 rank CPU 核数/NUMA 入口已补充，见下方 O5 小节；跨引擎绑定
验收与 LAMMPS 正式性能矩阵仍为 P0 后续工作，见
[绑定计划](../../docs/plans/mpi-cpu-binding.md)与
[LAMMPS benchmark 计划](../../docs/plans/lammps-multigpu-benchmark.md)。
三引擎按物理量等价而非输入哈希相同判定，规则见
[横向对照标准](../../docs/standards/cross-engine-reference.md)。

## 测量对象与公平性

- DMG-MD：`mpiexec -n P dmg-md`，每 rank 一张 GPU；分别测 HostStaged / CudaAware。
- GPUMD：一个 `gpumd` 进程，只暴露 P 张 GPU。P=1 自动 ordinary NEP，P>1 自动
  `NEP_MULTIGPU`，`potential nep.txt y` 固定长轴。**不能用 mpiexec 启动 P 份 GPUMD**。
- LAMMPS/MatPL：`mpiexec -n P`，启动包装器按本机 rank 给每个进程唯一 GPU UUID，
  每 rank 仅见一张 GPU，再以 `-k on g 1 -sf kk` 运行 `matpl/nep/kk`。runner 核验
  GPU 绑定记录、pair style 的 `kokkos_device` 路径和两段 `Loop time`。
- 三者使用同一 `model.xyz` 物理初态、锁定势、显式速度，NVE、0.1 fs、seed 0；
  LAMMPS 从模型生成 `data.lmp`，把 Å/fs 转为 Å/ps、fs 转为 ps，势格式仅在证明
  等价时归一化并记录前后哈希。GPUMD
  多卡输入增加分解轴参数。复用 long_nve 的确定性碳晶体/水初态生成器，按几何放大，保留密度。
  这是性能压力样本，不是已平衡的生产科学体系。碳晶体与水分别观察稳定邻域和迁移/重建成本。
- 每次重复都是全新进程、相同初态；同进程先 `run warmup`，再 `run steps`，最后一步输出一次
  thermo，不输出轨迹/restart。速度初始化、读文件、MPI 启动不计入正式段。正式段保留实际
  积分、邻域重建、通信和全局归约。预热不等于热力学平衡。
- DMG-MD 用第二条 `DMGMD_TIMING phase=run seconds_max`（所有 rank 中的最大同步耗时）；
  GPUMD 用第二条 `Time used for this run`；LAMMPS 用第二条 `Loop time`。三者计时边界
  并非逐条指令完全相同：DMG-MD 的
  run 段包含自己的段内准备，GPUMD 在循环前做部分准备；这是各自实际 MD 段性能，不能解释为
  NEP kernel 微基准。最终 thermo 会读回 GPU 结果，避免把异步 kernel 发射时间当完成时间。
  同时记录整个子进程 wall time，不能拿它替代正式段。
- 默认关闭 DMGMD_DOMAIN_TIMING/TRACE/DIAGNOSTICS，通信日志间隔大于段长；不修改 NEP 数值路径。
  清除继承的全部 `DMGMD_*` 开关后显式设置性能环境，避免调试模式混入正式测量。
- 每轮随机化配置顺序，固定 order_seed；重复次数、步数、GPU 顺序与二进制必须统一。
  同一行三种引擎使用同一 GPU UUID 前缀，不偷偷选择各自最快的卡组。

目前静态物理量门槛已对 baseline 与 7 个 long-NVE 势型验证；正式性能还需短程/长程
三引擎正确性和固定 CPU/NUMA 绑定验收。smoke/pilot 仅验证编排，不发表效率结论。

## 规模与扩展性

8 张 4090 不代表任意规模都能放进显存。GPUMD 仍在 GPU 0 保留全局体系，DMG-MD 也有
输入与元数据开销。先 pilot，再逐级放大；OOM/超时保留日志并标记失败，**不补造单卡基准**。
CPU 模型生成器会同时保留原子和文本列表；百万级体系建议至少 32 GB 空闲主机内存，capacity
建议 64 GB 以上空闲内存并监测实际使用，不能只检查 GPU 空闲。生成的模型共享软链接，重复运行
不复制大文件；移动完整结果目录后需要修复绝对软链接。

当前预设规模（精确值可用 `--dry-run` 查看）：

| case | 类型 | N 或 N/P | 几何目的 |
|---|---|---:|---|
| carbon_smoke | 强扩展 | 49,152 | 很短的编排检查，仍满足 8 卡长轴限制 |
| carbon_200k | 强扩展 | 196,608 | 观察小规模通信占比 |
| carbon_1m | 强扩展 | 1,048,576 | 主性能样例 |
| carbon_3m | 强扩展 | 2,949,120 | 大规模压力 |
| water_400k | 强扩展 | 393,216 | 多元素、迁移压力 |
| water_1m | 强扩展 | 1,105,920 | 大规模水 |
| carbon_weak | 弱扩展 | 327,680 / GPU | 1→8 卡总 N 增至 2,621,440 |
| carbon_cube_4m | 强扩展/capacity | 4,198,400 | 80×82×80 近立方盒，避免只展示有利长条盒 |
| carbon_weak_large | 弱扩展/capacity | 614,400 / GPU | 8 卡达 4,915,200 |

强扩展保持 N、盒形不变，测 P=1/2/4/8。弱扩展仅沿 y 按 P 拉长，横截面和每卡 slab 厚度
不变，保持 N/P 不变；这是当前一维域分解的弱扩展，不是各向同性三维弱扩展。
不要把不同 N 的强扩展曲线混为一条，也不要把长条盒结果推广为任意几何的扩展性。

源码依据：锁定 GPUMD 的 `force/force.cu` 选择多卡；`force/nep_multigpu.cu::compute`
要求 `floor(Ly/(rc/2))/P >= 10`，碳 rc=7 Å，因此 8 卡长轴至少约 280 Å。
本目录在生成矩阵时校验该条件和 DMG-MD large-box 条件；运行后 P>1 必须报告 M2a/y。
GPUMD 多卡模式、DMG-MD unique UUID 绑定与通信后端也必须匹配，否则不能纳入结果。

## 执行顺序

从 `newmd` 仓库根目录执行。Python 3.10+，只需标准库。

```bash
source ../env/md-mpi.sh
# 查看矩阵：不会访问 GPU、生成模型或创建目录
python3 tests/benchmark/run_benchmark.py --profile standard --dry-run

# 空闲单卡编排检查：把 2 换成实际空闲卡；也接受完整 GPU UUID
python3 tests/benchmark/run_benchmark.py --profile smoke --devices 2 --ranks 1 \
  --output dmgmd-bench-smoke

# 8 卡均空闲时验证真正多卡入口
python3 tests/benchmark/run_benchmark.py --profile smoke --devices 0,1,2,3,4,5,6,7 \
  --ranks 1,2,4,8 --output dmgmd-bench-smoke8

# 性能前须独立通过已有正确性测试；见 long_nve/README.md
# pilot 校准时间和显存，pilot 自身不发表性能结论
python3 tests/benchmark/run_benchmark.py --profile pilot --output dmgmd-bench-pilot

# 默认三引擎标准矩阵为 288 个串行 trial；CPU/NUMA 绑定 P0 完成后再作为正式结果
python3 tests/benchmark/run_benchmark.py --profile standard --output dmgmd-bench-standard

# 单独的大内存容量/近立方盒实验，不和主矩阵混跑
python3 tests/benchmark/run_benchmark.py --profile capacity --output dmgmd-bench-capacity
```

`--output` 指定输出目录前缀，每次运行自动追加运行环境本地时间 `YYYYMMDD-HHMMSS`
（年月日-时分秒）。例如 `--output dmgmd-bench-smoke` 会生成
`dmgmd-bench-smoke-20260924-153045/`；同一秒重名时再追加 `-1`、`-2` 等序号，
不会覆盖之前的结果或因已有结果目录而失败。不指定时使用仓库根目录下的
`dmgmd-benchmark` 前缀。启动时打印实际输出路径；`--dry-run` 不创建目录。
Docker 中的时间以容器时区为准。

若用快速部署脚本构建，每条运行命令加上：

```text
--candidate ./build-benchmark/dmg-md --reference ./build-benchmark-gpumd/gpumd
```

默认 GPUMD reference 是 `../gpumd-reference/src/gpumd`，默认 LAMMPS 可执行文件是
`./build-lammps-nep-kk-preserved/lmp`，也可用 `--lammps-bin` 指定 `newmd` 内的另一构建。
正式比较建议用记录了构建参数的版本，
使用相同 CUDA/GCC/架构与 Release 选项，保留构建日志。运行器检查参考 checkout commit、
保存二进制 SHA-256，但不能仅凭旧二进制推断其一定来自该 checkout。三个 reference
checkout 严格只读；MatPL overlay 与构建物保存在 `newmd`。

### 在 Docker 中构建并运行

仓库镜像 `dmgmd:cuda12.8` 自带编译工具、MPI/CUDA 环境和 DMG-MD 的常规 `build/`，但镜像构建时
没有包含同级的 `gpumd-reference`。要做 GPUMD 对比，需把宿主机上包含 `newmd` 和
`gpumd-reference` 两个目录的父目录挂载到容器；下面的命令假设你当前位于 `newmd` 根目录，
目录布局为 `../newmd` 和 `../gpumd-reference`，且镜像已准备好。

benchmark 构建脚本使用容器内的 `git` 检查参考版本。若运行时报 `git: command not found`，说明
本机镜像早于 Git 依赖更新；在 `newmd` 根目录用当前 Dockerfile 重建镜像（需要构建机可访问镜像
构建所需的下载站点），再运行下面的命令：

```bash
docker build --build-arg BUILD_JOBS=4 \
  --build-arg DMGMD_UID="$(id -u)" --build-arg DMGMD_GID="$(id -g)" \
  -t dmgmd:cuda12.8 .
```

以下示例把宿主机 GPU 0、1 分配给容器。在容器内它们会显示为设备 0、1；benchmark 的
`--devices` 应填写容器内编号。该命令先在容器内重新构建两边的 Release 可执行文件，再运行
两卡 smoke 矩阵，结果写入宿主机的 `newmd/dmgmd-bench-docker-smoke-YYYYMMDD-HHMMSS/`：

```bash
docker run --rm --gpus '"device=0,1"' --shm-size=1g --ulimit memlock=-1:-1 \
  -v "$PWD/..:/workspace" \
  -w /workspace/newmd \
  dmgmd:cuda12.8 bash -c '
    set -e
    MD_ENV_FILE=/opt/dmgmd/env/md-mpi.sh bash scripts/build_benchmark.sh 89 8
    python3 tests/benchmark/run_benchmark.py --profile smoke --engines gpumd,dmgmd \
      --devices 0,1 --ranks 1,2 \
      --candidate ./build-benchmark/dmg-md \
      --reference ./build-benchmark-gpumd/gpumd \
      --output dmgmd-bench-docker-smoke
  '
```

`scripts/build_benchmark.sh` 会检查 `../gpumd-reference` 是否位于锁定 commit
`9d23496e41319b9e2af5221a7df6285387401d1e` 且没有已跟踪文件修改；检查不通过时先切换到干净的
参考 checkout。构建产物分别放在 `build-benchmark/` 和 `build-benchmark-gpumd/`，不会覆盖常规
`build/`。参数 `89 8` 分别表示 CUDA 架构和编译并行度；按目标 GPU/工具链选择架构。

常见 NVIDIA GPU 的 Compute Capability 与 `scripts/build_benchmark.sh` 第一个参数对应如下。
参数写法是把 Compute Capability 的点去掉，例如 `8.9` 写成 `89`，用于生成对应的 `sm_89` 代码：

| GPU 示例 | Compute Capability | 构建参数 / SM |
|---|---:|---:|
| GeForce RTX 5090、RTX PRO Blackwell | 12.0 | `120` / `sm_120` |
| NVIDIA B200、GB200 | 10.0 | `100` / `sm_100` |
| NVIDIA H100、H200、GH200 | 9.0 | `90` / `sm_90` |
| GeForce RTX 4090、RTX 4080；NVIDIA L4、L40、L40S | 8.9 | `89` / `sm_89` |
| NVIDIA A100、A30 | 8.0 | `80` / `sm_80` |
| NVIDIA A10、A40、A16；GeForce RTX 3090、RTX 3080 | 8.6 | `86` / `sm_86` |
| NVIDIA T4；GeForce RTX 2080 系列 | 7.5 | `75` / `sm_75` |
| NVIDIA V100 | 7.0 | `70` / `sm_70` |

例如 RTX 5090 可运行 `bash scripts/build_benchmark.sh 120 8`。构建机上的 CUDA Toolkit 和
NVCC 必须支持目标架构；较旧的 GPU 架构也可能已被当前 Toolkit 移除。以上型号和版本依据
[NVIDIA CUDA GPU Compute Capability 列表](https://developer.nvidia.com/cuda/gpus)及其
[旧款 GPU 列表](https://developer.nvidia.com/cuda/gpus/legacy)，具体型号请按该表核对。

只用一张卡时，将 Docker 的 `--gpus` 改为 `--gpus '"device=0"'`，并将 benchmark 参数改为
`--devices 0 --ranks 1`。确认 smoke 通过、并完成所需正确性验收后，可把 `--profile smoke`
换成 `pilot` 或 `standard`，并相应更改 `--output`；standard 是长时间正式矩阵，建议在独占节点运行。

standard/capacity 默认预热 200、正式 1000 步、3 次重复，单 trial 正式段须 ≥10 s；这些是起点，
不是保证足够的时长。用 pilot 的最快 ms/step，按 `steps >= 10000 / ms_per_step` 选正式步数，
可目标 20–30 s 留裕量；同一组比较都用相同步数。若预热翻倍仍显著改变结果，应增加预热。
正式分析建议 5 次重复，并检查 `(max-min)/median`，波动 >5% 时先查负载/温度/时钟再重跑，
不只删除较慢数据。

```bash
python3 tests/benchmark/run_benchmark.py --profile standard \
  --cases carbon_1m --steps 3000 --warmup 500 --repeats 5 \
  --devices 2,3 --ranks 1,2 --backends HostStaged,CudaAware \
  --output dmgmd-bench-carbon1m
```

`--cases` 覆盖 profile 的 case 列表。`--engines gpumd`、`lammps` 或
`dmgmd` 可诊断单方；`--mpiexec-arg` 透传给 LAMMPS 和 DMG-MD 的
`mpiexec`，不作用于直接启动的 GPUMD，也不
验证实际 affinity。临时实验可用 Open MPI 5 的例如
`--mpiexec-arg=--map-by --mpiexec-arg=core:PE=4 --mpiexec-arg=--bind-to --mpiexec-arg=core --mpiexec-arg=--report-bindings`
指定每 rank 四个物理核心并打印映射；这取决于实际可用拓扑，必须再检查各 rank 的
`Cpus_allowed_list` 与 CPU→core/NUMA 关系。该旧透传不执行统一策略生成，
不能据此宣称 GPUMD/DMG-MD/LAMMPS 已用相同 CPU 资源。
`--timeout` 是每次启动的总时限（包括读入、预热），默认 7200 s。
已有结果目录拒绝覆盖；结果逐 trial 原子写入，Ctrl-C 会终止当前启动进程及其后代（包括独立进程组的 MPI rank）并保留已完成项。
当前没有自动续跑/合并：按 case 分批可缩小重跑范围；不要跨不同环境/步数手工拼接比值。

### O1 串行时间线与 A/B 配置

`--dmgmd-profiling off,trace` 把两个诊断模式作为同一 benchmark 矩阵中的独立配置，沿用
manifest 输入、固定 seed、随机 trial 顺序和结果保存。默认只有 `off`。`trace` 只支持
`--engines dmgmd` 的 M2a（rank 数 >1）；它显式设置 `DMGMD_DOMAIN_TIMING=1`、
`DMGMD_DOMAIN_TRACE=1`、每步通信日志。`off` 显式设置两者为 0。runner 先清除继承的
全部 `DMGMD_*`，再把这三个诊断开关、backend 和通信日志间隔列入每 trial
`launch_environment`；成功判定还检查程序 `DMGMD_DOMAIN_DIAGNOSTICS` 实际模式记录，
trace 缺行、off 意外出行均拒绝。`summary` 按模式分组，trace 的秒数只用于诊断开销，
正式性能使用 off 行。

本机有 4 张 GPU 邻近 NUMA0 时，可选 GPU0/1、同 NUMA 的每 rank 4 个物理核心：

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --help
python3 tests/benchmark/run_benchmark.py --profile smoke --engines dmgmd \
  --devices 0,1 --ranks 2 --backends HostStaged,CudaAware \
  --dmgmd-profiling off,trace --cpu-sampling proc --cpu-sample-interval .05 \
  --expected-cores-per-rank 4 --expected-numa-node 0 \
  --mpiexec-arg=--map-by --mpiexec-arg=ppr:2:numa:PE=4 \
  --mpiexec-arg=--bind-to --mpiexec-arg=core --mpiexec-arg=--report-bindings \
  --dry-run
# 去掉 --dry-run 并指定 --output 后运行；先过 AGENTS.md 环境与正确性门槛。
```

`--expected-cores-per-rank` 与 `--expected-numa-node` 必须一起给，且要求
`--cpu-sampling proc`。它们不负责绑定，只从 C1 的实际 task affinity、SMT core/package ID
及 NUMA cpulist 核验每 rank 的物理核心数、NUMA 和 rank 间不重叠；不符的 trial 记
`invalid_cpu_binding`，不会进入性能汇总。GPU UUID 由现有 `DMGMD_MPI`/C1 映射核验。
这两个选项只适用于所有 rank 固定在同一 NUMA 的配置；跨 NUMA 映射可保留 C1 原始
affinity，在报告中逐 rank 离线核验。`--report-bindings` 只是辅助原始证据，不能代替 C1。

trace 原始行在 trial `stdout.txt`；同时采 CPU 时，runner 将 rank/PID/start ticks 与
trace 单调纳秒关联为 `cpu/markers.jsonl`，C1 `summary.json` 包含 `run`、
`step_ordinary`、`step_rebuild` 阶段的有效采样窗口。短步可能没有完整窗口，CPU 值保持
`null`，不能补算。时间字段的语义、CUDA event 与 host 时钟的关联限制见
[M2a 现行合同](../../docs/standards/mpi-runtime.md)。trace 组的逐行日志和 event 会扰动
性能；性能组保持 profiler off，CPU sampler 开销需另用 off 模式的 proc/off 配对校准。

重建步使用已有 `tests/mpi/run_mpi_domain.py` 的 `CROSSINGS_MODEL/CROSSINGS_RUN` 定向
fixture，通过 `run_overlap_rebuild.py` 调度；它复用本目录的 C1 sampler、trace marker
转换、affinity 核验、环境预检与结果保存，随机化两后端顺序，并要求 8 个重建步均出现。
普通步由上面的 benchmark smoke/pilot 记录。
它是诊断运行，8 个短步的 CPU 阶段窗口可能不可观测。
`--fixture carbon-drift --warmup 10 --steps 100` 从 benchmark 的 `carbon_200k` 输入派生，
给所有原子的 y 速度统一增加 2 Å/fs，在保留相对初态的同时触发持续重建；原始派生
`model.xyz`、`run.in`、势及哈希均保存。这是 CPU 阶段窗口和重建成本诊断，不作为
科学正确性 oracle，也不与无漂移普通步直接比较速度。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_rebuild.py --help
python3 tests/benchmark/run_overlap_rebuild.py --devices 0,1 \
  --expected-cores-per-rank 4 --expected-numa-node 0 \
  --mpiexec-arg=--map-by --mpiexec-arg=ppr:2:numa:PE=4 \
  --mpiexec-arg=--bind-to --mpiexec-arg=core --dry-run
# 去掉 --dry-run 并指定 --output 后运行。
# 需要可采样的重建窗口时加 --fixture carbon-drift --cpu-sample-interval .1。
```

## 共享服务器与可审计输出

由使用者自行选择空闲服务器。脚本不查询 GPU 计算进程，也不根据已用显存或利用率阻止运行，
不再提供 `--idle-memory-mib`、`--idle-util-percent` 参数。
采用 nvidia-smi 物理索引解析成完整 UUID，再写入 CUDA_VISIBLE_DEVICES，避免 CUDA 顺序歧义。

每 2 s 采样温度、功耗、SM/显存时钟、利用率和显存，仅作为遥测记录，不据此判定 GPU 忙碌或
将 trial 标记为 contaminated。正式结果的环境独占性由使用者保证。脚本不改 GPU
功率或锁频，不把 CUDA-aware 等同于 GPUDirect P2P 实际可用；4090 的传输能力以实测拓扑和
UCX 路径为准。若通过其他工具锁频/设置功率，双方保持一致并附命令和恢复方案。

- `metadata.json`：命令、profile、随机顺序、GPU UUID、源码 revision/dirty 状态、二进制与
  生成器哈希、MPI/UCX/CUDA、CPU、拓扑、完整 GPU 配置和相关环境变量。
- `preflight.txt`：现有 MPI/CUDA 门槛，实际 device-buffer collective/p2p 自检；失败不启动矩阵。
- `inputs/`：可复现初态和势；LAMMPS trial 使用 `data.lmp`/`in.lmp`，
  GPUMD/DMG-MD 使用 `run.in`，各自保留输入哈希、stdout/stderr、telemetry 与结果 JSON。
- `results.json`：包括失败、OOM（查看原始 stderr）、超时和过短 trial。
- `summary.{json,csv,md}`：只汇总有效样本，提供 min/median/max、相对范围、样本数和完成标记。
  只有重复次数完整的行与基线才能计算性能比。

记 `tP` 为固定步数正式段的 median：

- 吞吐 = `N × steps / tP`，ms/step = `1000 × tP / steps`。
- 强扩展 speedup = `t1/tP`；效率 = `t1/(P×tP)`，只用同引擎同后端同 N 的单卡基线。
- 弱扩展效率 = `t1(N1)/tP(P×N1)`；弱扩展不输出混淆含义的 speedup。
- 同卡数 `DMGMD/GPUMD = t_GPUMD/t_DMGMD`，>1 表示 DMG-MD 更快。
- 同卡数 `DMGMD/LAMMPS = t_LAMMPS/t_DMGMD`，>1 表示 DMG-MD 更快。
- 缺失/失败/OOM 的 P=1 基线，其扩展指标留空；绝不改用 P=2 伪装单卡基线。

此 suite 只检查最终 thermo 有限、模式和计时完整，不代替能量漂移/力误差验收。对生产代码
改动必须先跑已有 golden/nightly；正式发表时同时附正确性 revision 和结果。

## 测试脚本本身

```bash
python3 tests/benchmark/test_benchmark.py
```

CPU 测试覆盖几何门槛、预热排除、后端回退/错误绑定拒绝、受干扰数据排除、强弱扩展公式和
缺失基线，不启动 GPU。亦注册为 CTest `dmgmd.benchmark_analysis`。

## CPU 采样（C1）

默认 `--cpu-sampling off`，保留原有 benchmark 启动行为。CPU 工具只支持单节点 Linux、
Open MPI 的 DMG-MD rank；GPUMD 不在 rank 采样范围。指标、原始 schema、共享 quota 与
缺失数据语义见 [CPU 采样合同](../../docs/standards/cpu-sampling.md)，
本轮验证见 [C1 记录](../../docs/status/validation/cpu-sampling-c1-20261009.md)。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --help
# 无 GPU、无文件写入；参数仍由现有 manifest/profile/CLI 决定
python3 tests/benchmark/run_benchmark.py --profile smoke --engines dmgmd \
  --devices 0,1 --ranks 1,2 --backends HostStaged,CudaAware \
  --cpu-sampling proc --cpu-sample-interval .2 --dry-run

# 先按 AGENTS.md 预检，smoke 只验接线，不是正式矩阵或开销校准
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1 --ranks 2
python3 tests/benchmark/run_benchmark.py --profile smoke --engines dmgmd \
  --devices 0,1 --ranks 1,2 --backends HostStaged,CudaAware \
  --cpu-sampling proc --cpu-sample-interval .2 --output dmgmd-cpu-smoke

# CPU-only 测试与离线重算；TRIAL_DIR 为上一步某个 trial 目录
python3 tests/benchmark/test_cpu_sampling.py
python3 tests/benchmark/analyze_cpu.py --help
python3 tests/benchmark/analyze_cpu.py TRIAL_DIR --output /tmp/cpu-recomputed --dry-run
python3 tests/benchmark/analyze_cpu.py TRIAL_DIR --output /tmp/cpu-recomputed
```

| 选项 | 行为 |
|---|---|
| `--cpu-sampling off\|proc` | 默认 off。proc 要求 `--engines dmgmd`，一个独立采样进程，不给 rank 新增采样线程。 |
| `--cpu-sample-interval SECONDS` | 默认 1 s，正有限数；与已有 GPU `--sample-interval` 独立。smoke 示例的 .2 s 不是推荐正式频率。 |
| `--cpu-output ROOT` | 默认 trial/cpu；自定义时为 ROOT/本次run目录名/trial-id，拒绝覆盖已有采样目录。 |
| `--cpu-diagnostics none\|pidstat\|perf` | 默认 none。诊断命令/version/原始日志及退出码保存，缺工具或权限记 unavailable，基础 /proc 采样继续。 |
| `--cpu-capacity-scope unknown\|host` | 默认 unknown；host 是对完整宿主 cgroup 层次可见性的人工声明，仅核验后使用；不是“原生模式”的同义词。 |

后三个选项只允许在 proc 模式使用，防止以为诊断已开却静默不采。全部在 `--help` 和
`--dry-run` 展示；dry-run 不验证 GPU/namespace/cgroup 权限。可选分析器 `--markers FILE`
读取 schema v1 的可靠 monotonic marker；默认 phase/sequence 与 run CPU 利用率为空。
O1 的显式 `--dmgmd-profiling trace` 会产生 M2a marker 并自动传给解析器；
详细 domain timing 不能通过继承 `DMGMD_*` 打开。

数据位置：上层 metadata/result 指向 cpu 目录，内含 config.json、samples.jsonl、
rank-N.json、sampler.stderr、collector-exit.json，以及 summary.json/csv、intervals.json。
保留原始数据可用分析器重算；跨目录搬移还需保留原 stdout、trial/input/metadata，
config.metadata.trial_directory 是原绝对路径，若移动后不可见则 GPU 映射 unavailable，不能猜补。
短命线程可能漏采、初始化/退出尾部删失。少于两个有效样本为 insufficient_samples；
`cpu_status=ok` 仅代表采样接线/映射完整，不意味着阶段性能或采样开销达标。
CPU partial 会使 runner 非零退出；数值 trial status 单独保留。Ctrl-C/SIGTERM、超时和异常早退
保留部分结果并清理本次采样器；不要靠 SIGKILL 做常规停止。

Docker 用前文父目录挂载和镜像 entrypoint 环境，在**容器内**执行相同命令；每个原生新 shell
均需 source `../env/md-mpi.sh`。runner、collector 和 rank 必须同机、同 PID/mount/time/cgroup
namespace；多节点远端 /proc、宿主跨 namespace 采容器不支持。cgroup v1/不可读层次仍提供
rank ticks/affinity，quota 归一化为空；容器 root 的 cpu.max=max 不能证明没有宿主祖先上限。

C2 从现有 pilot profile 进入，先核验固定 affinity/quota 与观测开销，再校准时间窗。
A/B 正式研究须先采用[固定 CPU 绑定计划](../../docs/plans/mpi-cpu-binding.md)交付的
每 rank K 核与 NUMA 策略；C1 采样本身不设置 affinity。
A=off、B=proc，同输入、GPU telemetry 与绑定，随机化组序、至少五次；C1 smoke 不代替此项。
未有可靠 marker 前仅分析 process_observed，不能按 DMGMD_TIMING 汇总日志推断逐步 CPU。

## O2 串行分区成本

`--dmgmd-partition off|serial` 默认 off。runner 清除继承的 DMGMD 环境后显式白名单传递
`DMGMD_DOMAIN_PARTITION`，在 dry-run/plan、launch_environment 和 metadata 保存模式，
并用程序的 CONFIG 与累计执行 SUMMARY 检查实际模式，结果写 actual_partition。
serial 仍先完成 halo，再分区串行运行；它没有 overlap。正式成本比较使用相同输入与
CPU/NUMA/GPU、关闭详细 profiler，另用 trace 组诊断，禁止用 verify 的双计算测速度。
O2 原始成本样本与限制见 [状态](../../docs/status/validation/overlap-partition-20261015.md)。

## O4 小规模检查

`--dmgmd-overlap off|wait|poll` 默认 off，wait/poll 要求 `--dmgmd-partition serial`。
runner 显式传递/保存开关，并检查实际 CONFIG 和 SUMMARY，结果包含 actual_overlap。
`--overlap-trace` 单独启用 O4 CUDA event/host 观察；主墙钟比较应关闭此选项及详细 profiler。
wait 是 early 后 finish；poll 最多 8 次 progress 后 finish，无 progress thread。

O4 只做固定 GPU UUID/CPU 物理核/NUMA、同 binary/input/seed 的小样本 full/serial/wait/poll
比较，不等于 O5 正式性能矩阵。程序的 finish wall/线程 CPU 包含剩余 MPI、可见性、early
和 unpack 等待；C1 的 process_observed 还包含启动与退出，不能相互代替。

## O5 统一绑定与正式实验

常规入口与本 runner 共用物理核心策略，详细边界见[绑定合同](../../docs/standards/cpu-binding.md)。
不设置 K 时沿用旧行为；固定绑定时不能再传另一个 map/bind/rankfile/oversubscribe 策略。
默认禁止 rank 间共享物理核心，CPU sampler=off 也持续检查实际 exec 后的各 task affinity。

```bash
source ../env/md-mpi.sh
# 从仓库根目录开始；将 /path/to/input 替换为含 run.in/model.xyz/nep.txt 的目录。
dmgmd_repo="$PWD"
(
  cd /path/to/input
  python3 "$dmgmd_repo/scripts/launch_dmgmd.py" -n 2 --cpu-cores-per-rank 4 \
    --cpu-placement numa --cpu-numa-nodes 0 \
    --audit-directory /tmp/dmgmd_binding_example -- "$dmgmd_repo/build/dmg-md"
)

python3 tests/benchmark/run_benchmark.py --engines dmgmd --profile smoke \
  --ranks 1,2,4,8 --devices 0,1,2,3,4,5,6,7 \
  --cpu-cores-per-rank 4 --cpu-placement numa \
  --cpu-numa-nodes 0,0,0,0,1,1,1,1 --cpu-sampling off \
  --output dmgmd-bound-smoke
python3 tests/benchmark/test_cpu_binding.py
python3 tests/benchmark/test_overlap_o5.py
```

`core|numa|rankfile` 三种策略及 `--cpu-rankfile FILE` 由 `--help` 说明。显式文件使用
hwloc 全局逻辑 Core 编号，工具保存 Core→OS CPU 映射；不能填 OS CPU ID 冒充逻辑核。
例子要求目标机器有足够资源，GPU/NUMA 顺序须按本机拓扑选择。
CPU affinity 不证明内存页绑定/驻留；`numactl --show` 原始输出另存。
GPUMD 合计 P×K 的入口尚未实施，固定绑定请求会明确拒绝；LAMMPS 接线未在 O5 验收。

O5 专用 `run_overlap_o5.py --root DIR PHASE` 使用独立的 design、science、pilot、calibration、
frozen-plan 和完整随机顺序；以文件锁串行调度，独立进程 trial 的输入/输出不覆盖。
已有 result 只有身份/配置签名相符时可跳过，残留半成品目录拒绝复用；改变身份或配置要
新批次。失败保留，不按速度删除，也不把 verify/trace/pilot 混进主统计。

本轮实际目录为 `dmgmd-overlap-o5-20261016/`，冻结基本方案 710 个，按规则追加至
870 个（主矩阵 600、历史 150、退化 120），独立采用验证另有 40 个；实际完成状态以[O5 报告](../../docs/status/performance/communication-computation-overlap-o5-results-20261021.md)
和产物 `analysis-status.json` 为准。实验环境采用显式白名单，继承的 DMGMD 调试开关清除；
基础环境、工具、输入与二进制身份在正式采集前冻结。详细诊断采用相同 warmup，C1 与主性能分组。不将脚本、pilot 或 smoke 通过写成 O5 完成。

```bash
source ../env/md-mpi.sh
# 完成前门槛和 pilot 后，采集命令及实际顺序见对应 workflow/plan；main 支持安全续跑。
python3 tests/benchmark/run_overlap_o5.py --root dmgmd-overlap-o5-20261016 main
# 离线校验工具/原始证据哈希，重算初轮五重复、最终统计、CPU/诊断/Nsight 与图。
bash dmgmd-overlap-o5-20261016/recompute.sh
```

O5 的 `observe_o5_gpu_mapping.py` 可独立核验诊断期 PID→GPU UUID，使用 affinity audit
的 PID/start ticks 与程序 run-loop trace，不依赖 C1 CPU ticks。它在本轮主性能阶段
休眠，只在独立诊断阶段查询 NVML；主 trial 的 UUID 来自 CUDA 初始化实际查询，
固定设备调用链与诊断期观测作交叉核验。输出与缺失情况以 O5 报告为准。

本机 Open MPI/PRRTE 的合并长日志及原生 `--output-filename` 均实测有跨 rank 片段；
详细诊断须使用 `o5_rank_output.py` 的直接 rank 重定向适配器，在 exec 前将 stdout/stderr
写入各自文件，保留 launcher 流与派生拼接/哈希。冻结主 runner 的源码不原位改变；
实际补采调用、两后端 P8 smoke 与 additive plan/resolution 在 O5 目录的
`recover-diagnostics-20261021-v3.py`、`diagnostic-recovery-resolution.json`、launch JSON 中。
`diagnostic/` 原 88 组不覆盖，`diagnostic-20261021/` 的 88 组作为最终诊断；observer
按 resolution 选择独立的新 GPU 证据目录。诊断 I/O 路径的时长不进入主性能结果。

`analyze_overlap_o5.py` 对逐步 trace 完整性、同窗口 full 科学输出、C1 完整窗口、
独立运行时 GPU 映射及采集前冻结的采用验证分别准入。`summarize_overlap_o5.py` 生成
可读结果表；跨默认后端比较明确标为事后探索。`inventory_overlap_o5.py` 保留全部带
launch 的尝试/状态，并生成原始日志、CPU ticks、trace、GPU context 证据的 SHA 清单。

## O6 稳态 kernel 归因

`run_overlap_o6.py` 复用已验收 O4 生产二进制，仅增加独立实验编排；不构建新的 NEP
kernel。`freeze` 在试验前冻结 binary/input/toolchain 哈希、K=4 绑定、GPU UUID、随机
顺序、步数和统计规则。输入从指定的 O5 产物复制，新样本只写入新的 O6 目录。
当前冻结范围为 carbon_200k/P2 与 water_400k/P4/P8、CudaAware、full/serial/wait。
每种配置五个新无诊断重复；Nsight Systems 另做三轮独立重复，二者不混用。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o6.py freeze --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/run_overlap_o6.py gates --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/run_overlap_o6.py profiles --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/analyze_overlap_o6.py --root dmgmd-overlap-o6-20261021 --profiles-only
python3 tests/benchmark/run_overlap_o6.py main --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/profile_overlap_o6_ncu.py --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/run_overlap_o6_controls.py --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/analyze_overlap_o6.py --root dmgmd-overlap-o6-20261021
python3 tests/benchmark/test_overlap_o6.py
```

所有 GPU 采集共用目录锁并串行运行，源码身份变化、半成品目录或不合格 trial 会停止
采集；失败原样保留。重跑时沿用已冻结身份与命令，不覆盖日志。实际完成范围及
硬件指标可用性以 O6 报告与目录内 `analysis-status.json` 为准。

`analyze_overlap_o6.py` 按同一 CUPTI trace 内的实际 CUDA kernel 顺序定位最后 128
正式步，用 cache inspection、Verlet 和 thermo kernel 校验一步边界，排除段首 setup
与重建步；不拼接 CLOCK_MONOTONIC 和 Nsight 时间轴。完整五类 pipeline 为
filter/descriptor/radial/partial/many-body，普通 split 步必须对应 early、late 两组。
launch 数量、grid/block、寄存器、独立 kernel 时长与 host launch API 时长分别保存。
以“每 trial 的逐步 rank 均值”汇总三次诊断；步和 rank 不能充当独立重复。
host 分类/同步/MPI 区间来自程序原有 trace，只单独描述，不与 GPU 时间相加。

`profile_overlap_o6_ncu.py` 从 trace 冻结 rank0 的稳态 launch skip/count，使用 kernel
replay，对比 cache-control=none/all，clock-control=none。replay 的同步、内存恢复与
缓存操作都会改变执行；NCU 时间不能替换无诊断墙钟。计数器权限拒绝后保留精确错误，
同 GPU 的其他采集记 NOT_RUN，并尝试一次 LaunchStats 静态回退。缺少有效线程、
achieved occupancy、访存/缓存或 scheduler 指标时写 INCONCLUSIVE，不能以静态寄存器
数证明瓶颈，也不能把阶段 event 包络或短冷启动 trace 当作独占 kernel 成本。

`run_overlap_o6_controls.py` 另冻三轮同输入、同预热、128 步的无诊断短控制，逐文件
核对与 Nsight 窗口的科学结果，用于诊断扰动校准。它们不足 10 s，不是正式性能样本，
不与五轮长正式试验混用。阶段不交错且频率未锁定，不把短控制比值当作精确因果开销。
`inventory_overlap_o6.py` 保存源码、patch、全部原始尝试及证据 SHA-256；`--verify`
检查原始文件与分析依赖，供产物目录的 `recompute.sh` 在离线重算前使用。

## O7 缓存判定归约

`run_overlap_o7.py` 使用独立产物目录，原/新二进制分别保存为 `dmg-md-before` 和
`dmg-md-after`。实施前的 `frozen-design.json` 固定 scope、完整随机顺序、预热/正式步数、
重复块及统计规则；`freeze` 从前阶段复制锁定输入，并冻结两二进制、工具、环境、
K=4 CPU/NUMA 绑定与 GPU UUID。`science` 检查环境/golden/MPI/边界门槛，另跑两后端
full/serial/wait 的实际输入字节对照；`main` 还要求 nightly long_nve 门槛已经完成。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o7.py freeze --root dmgmd-overlap-o7-20261023
python3 tests/benchmark/run_overlap_o7.py science --root dmgmd-overlap-o7-20261023
python3 tests/benchmark/run_overlap_o7.py main --root dmgmd-overlap-o7-20261023
python3 tests/benchmark/run_overlap_o7.py analyze --root dmgmd-overlap-o7-20261023
```

实际 collective witness 用独立 PMPI probe，对原/新程序同时计数。该 probe 从不进入正式墙钟：

```bash
python3 tests/mpi/run_cache_decision.py \
  --before dmgmd-overlap-o7-20261023/dmg-md-before \
  --after dmgmd-overlap-o7-20261023/dmg-md-after \
  --probe build/tests/libdmgmd_cache_decision_probe.so \
  --devices 0,1,2,3,4,5,6,7 --ranks 2,4,8 --work-root /tmp/o7-cache-new
```

该测试覆盖严格阈值两侧/等号、非 root 贡献者、PBC、多 run、空 rank、非有限平方及
multi-box fatal，逐字节比较科学文件；P8 使用满足 M2a eligibility 的加长盒。
每步通信账本仍由 `tests/mpi/run_mpi_domain.py` 独立按 owned/layout/migration 记录核算。
实测范围、冻结设计与原始目录见 [O7 报告](../../docs/status/performance/overlap-o7-results-20261023.md)。

## O8 按需 NVE thermo

`run_overlap_o8.py` 使用独立目录，保留 `dmg-md-original`（原 full）、`dmg-md-o7`
和 `dmg-md-o8`，三版本按同一配对块比较。先复制已验收的两个对照二进制，再在修改
生产代码前运行 `design` 冻结范围、步数、顺序与统计规则。O8 二进制先通过统一环境、
golden、实际调用计数 differential、MPI、nightly 子矩阵及实际输入科学门槛。
`freeze` 另冻结输入、二进制、采集器、MPI/CUDA/UCX 库、环境与 K=4 核绑定身份。

```text
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o8.py design --root dmgmd-overlap-o8-20261023
python3 tests/benchmark/run_overlap_o8.py freeze --root dmgmd-overlap-o8-20261023
python3 tests/benchmark/run_overlap_o8.py science --root dmgmd-overlap-o8-20261023
python3 tests/benchmark/run_overlap_o8.py main --root dmgmd-overlap-o8-20261023
python3 tests/benchmark/run_overlap_o8.py diagnostics --root dmgmd-overlap-o8-20261023
python3 tests/benchmark/run_overlap_o8.py analyze --root dmgmd-overlap-o8-20261023
```

正式段关闭 trace/verify/详细计时，CPU 为 null。独立 trace/proc 诊断通过 monotonic
marker 提取正式段 CPU，不把整个进程 CPU 误报为正式段。PMPI probe 仅进入
`tests/mpi/run_thermo_demand.py`，不进入正式性能或 CPU 诊断。
CPU 诊断使用 `o5_rank_output.py` 保存独立 rank stdout/stderr 与拼接 provenance，
避免 MPI 合并输出交错破坏 PID/clock marker；有 error 或缺失 CPU status 时拒绝该诊断。
`main`/`analyze` 校验保存的门槛和科学文件；无效样本保留，不按速度删样本。
消费者与 event 就绪边界见[thermo 合同](../../docs/standards/thermo-demand.md)。
冻结设计、追加规则、实际结果及采集器修正见[O8 报告](../../docs/status/performance/overlap-o8-results-20261026.md)。

## O9 执行候选证据门槛

`run_overlap_o9.py` 复核 O6 的因果证据是否足以选定执行候选。默认从已有 O6/O8
独立目录复制已验收二进制和原输入，保留起始 dirty、patch 与 reference 状态；
这些本机证据目录须已存在，新采集须使用新 `--root`。`--previous`/`--accepted`
可指定 O6/O8 证据位置，固定 GPU UUID、K=4 映射与原窗口仍须一致。
工具不实现或自动采用 NEP kernel 改动，也不生成新的正式性能样本。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_o9.py freeze --root dmgmd-overlap-o9-new
python3 tests/benchmark/run_overlap_o9.py gates --root dmgmd-overlap-o9-new
python3 tests/benchmark/run_overlap_o9.py probe --root dmgmd-overlap-o9-new
python3 tests/benchmark/run_overlap_o9.py analyze --root dmgmd-overlap-o9-new
python3 tests/benchmark/test_overlap_o9.py
python3 tests/benchmark/run_overlap_o9.py seal --root dmgmd-overlap-o9-new
```

`freeze` 必须在采集前完成；两份对照都先通过八 rank 环境和 committed golden。
`probe` 在原 O6 `carbon_200k/P2/full` 的 rank0 第 264 步重试一次 NCU；
只有实际 ERR_NVGPUCTRPERM、无超时且绑定有效时才完成无候选 NO_ADOPTION 分支。
指标可用时返回 CANDIDATE_REVIEW_REQUIRED/PARTIAL，须先冻结单因素候选设计再实施；
其他失败或缺实验保持 PARTIAL。NCU 时长不能作为主性能，旧 O6/O8 样本也不变成 O9 样本。

`seal` 保存最终源码和原始证据哈希；此后 `analyze` 先校验清单，支持无 GPU 离线复算。
本轮实际窗口、筛选方向、索引安全/成本与反例见
[O9 记录](../../docs/status/performance/overlap-o9-results-20261026.md)。
