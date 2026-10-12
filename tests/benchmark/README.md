# 单节点、多 GPU 性能测试

类别：测试操作说明。参数以本目录 `manifest.json` 为准；不改变 `long_nve` 正确性验收。

当前 runner 支持 `gpumd,lammps,dmgmd`，并将 GPUMD 与 LAMMPS 一同作为 DMG-MD
对照组。固定每 rank CPU 核数/NUMA 绑定与 LAMMPS 正式性能矩阵仍为 P0 后续工作，见
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
- 默认关闭 DMGMD_DOMAIN_TIMING/DIAGNOSTICS，通信日志间隔大于段长；不修改 runtime/NEP。
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
`Cpus_allowed_list` 与 CPU→core/NUMA 关系。该透传不是尚待实现的一等 P0 接口，
不能据此宣称 GPUMD/DMG-MD/LAMMPS 已用相同 CPU 资源。
`--timeout` 是每次启动的总时限（包括读入、预热），默认 7200 s。
已有结果目录拒绝覆盖；结果逐 trial 原子写入，Ctrl-C 会终止当前启动进程及其后代（包括独立进程组的 MPI rank）并保留已完成项。
当前没有自动续跑/合并：按 case 分批可缩小重跑范围；不要跨不同环境/步数手工拼接比值。

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
本轮验证见 [C1 记录](../../docs/status/cpu-sampling-c1-20261009.md)。

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
读取 schema v1 的可靠 monotonic marker；当前 runtime 未发射这种 marker，默认 phase/sequence
与 run CPU 利用率为空。现有详细 domain timing 仍关闭，不能通过继承 DMGMD_* 打开。

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
