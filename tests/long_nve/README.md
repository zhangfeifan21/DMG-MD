# DMG-MD 长程 NVE/NVT 正确性测试

用同一 NEP 初态启动 DMG-MD 和单卡 GPUMD，检查短程逐原子物理量、长期守恒、温度和结构统计、双向构型回放及跨进程数重启。长程轨迹会混沌分叉，100,000 步后的正确性按统计与同构型回放判断。`nvt_ber` 是本文唯一的 NVT 系综。`nvt_nhc` 和 `nvt_bdp` 有自己的统计、MPI 和重启测试，见[恒温器验收](../thermostats/README.md)。

2026-11-20 的 L20 工作树中，短程 golden 已切换到 GPUMD 5.9.1，长程 manifest 仍锁定旧版。实跑 `smoke` 在创建轨迹前报 `long-NVE and short golden reference locks differ`。下列命令需要长短参考身份一致。矩阵的实测状态见[短程基线核查](../../docs/status/validation/baseline-results.md#l20-baseline-20261120)和[当前状态](../../docs/status/current.md)。

<a id="三个-profile"></a>

## 选择测试规模

| 配置 | NVE/NVT 步数 | 初态 | MPI 进程数 | 后端 | 用途 |
| --- | ---: | --- | --- | --- | --- |
| `smoke` | 100 / 100 | 三体系，种子 0 | 1 | HostStaged | 检查编排和语法变体 |
| `nightly` | 10,000 / 10,000 | 三体系，种子 0 | 1、2、4 | HostStaged、CudaAware | 定期回归 |
| `release` | 100,000 / 100,000 | 三体系，种子 0～4 | 1、2、4、8 | HostStaged、CudaAware | 发布验收 |

三套配置都先做 MPI 环境预检。`--backends` 可缩小诊断范围，发布验收须覆盖表中的两种后端。完整 `release` 需要 8 张已分配的 GPU。四卡环境可执行 `nightly` 并报告对应范围。

每种体系、配置和进程数的预期路径由 [manifest](manifest.json) 的 `domain_mode_by_rank` 锁定。单 rank 均为全量复制模式。`nightly` 的 P2/P4 与 `release` 的 P2/P4/P8 均为局部域分解。兼容性派生用例沿用其基础体系的路由。

驱动按后端分别跑所有 rank 数。全量复制应报告 `replicated-full`。局部域分解应报告 `local-domain-force-centers`，并核对第 0 步布局中每个原子恰有一个负责进程。`LONG_NVE_CONFIG`、checkpoint 和最终 JSON 记录 `domain_mode`。意外回退会在该阶段失败。

## 固定体系

`manifest.json` 锁定 10 组显式速度初态。`long_nve_common.py` 确定性生成模型，驱动在启动 GPU 作业前核对 base、nightly 和 release 三套几何共 90 个基础模型的 SHA-256。初始速度用 `math.fsum` 去除质心速度。Ubuntu 22.04 的 Python 3.10 和 Python 3.12 应生成相同字节。

`base` 几何供 `smoke` 使用。

| 用例 | 原子数 | 盒与构造 | 势 | 固定步长 |
| --- | ---: | --- | --- | ---: |
| `carbon_crystal` | 4,096 | 8 × 8 × 8 个金刚石常规晶胞，28.56 Å 立方盒 | NEP4 C | 0.1 fs |
| `dense_water` | 12,288 | 16³ 个 H₂O，取向和位置有确定性扰动，49.6 Å 立方盒 | 双元素 NEP4 | 0.1 fs |
| `batio3_zbl` | 5,000 | 10³ 个 BaTiO₃ 晶胞，中心 Ti/Ba 距离 1.0 Å，进入 ZBL 插值区 | NEP4-ZBL | 0.01 fs |

`nightly` / `release` 主要沿分解轴 `y` 拉长盒子。最大 rank 数下，条带宽度大于 `2*d_coord`，左右 halo 不会覆盖整块相邻条带。`release` 沿长轴加倍，使 8 rank 时的条带宽度与 `nightly` 的 4 rank 一致。

| 配置 | 用例 | 晶胞数 | 原子数 | `Ly` | 最大 rank 数时的条带宽度 | `2*d_coord` |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| nightly | `carbon_crystal` | 8 × 48 × 8 | 24,576 | 171.36 Å | 42.84 Å | 32 Å |
| nightly | `dense_water` | 8 × 128 × 8 | 24,576 | 396.8 Å | 99.2 Å | 28 Å |
| nightly | `batio3_zbl` | 10 × 40 × 10 | 20,000 | 160 Å | 40 Å | 28 Å |
| release | `carbon_crystal` | 8 × 96 × 8 | 49,152 | 342.72 Å | 42.84 Å | 32 Å |
| release | `dense_water` | 8 × 256 × 8 | 49,152 | 793.6 Å | 99.2 Å | 28 Å |
| release | `batio3_zbl` | 10 × 80 × 10 | 40,000 | 320 Å | 40 Å | 28 Å |

水体系的横截面调整到 8 × 8 个构造单元，减少扩散原子跨条带触发全局布局和邻居重建的频率。密度、分子构造、势、速度分布和步长保持不变。这些体系用于数值压力测试。若换成经 GPUMD 预平衡的科学体系，先仅用参考程序验证步长收敛，再审查生成器、哈希和 manifest。候选失败时沿用已冻结的输入和门槛。

`carbon_nep5`、`dense_water_typewise_cutoff`、`batio3_flexible_zbl` 和 `batio3_typewise_zbl_cutoff` 分别覆盖 NEP5、按类型截断、灵活 ZBL 和按类型 ZBL 截断。它们沿用基础体系的几何和模型哈希，从锁定势确定性生成，并另外锁定生成后势的 SHA-256。每个派生用例只检查静态量和短轨迹。

需要用同一初态做 LAMMPS 静态或短 NVE 时，由[输入转换器](../lammps_reference/README.md)生成。三引擎允许可证明等价的格式与单位转换，按物理量比较。已完成的静态检查和后续门槛见[对照标准](../../docs/standards/cross-engine-reference.md)与 [LAMMPS 计划](../../docs/plans/lammps-multigpu-benchmark.md)。

<a id="执行"></a>

## 运行测试

在仓库根目录设置设备和输出位置。`GPU_ID`、`GPU_IDS_4` 和 `GPU_IDS_8` 分别使用 1 张、4 张和 8 张分配给该作业的设备。`RESULT_ROOT` 指向可写位置，下例的各运行目录应互不相同。只运行与当前分配资源相符的命令。

### 单卡冒烟测试

```bash
source ../env/md-mpi.sh
GPU_ID=0  # 示例，换成分配的设备
RESULT_ROOT="$PWD/../scratch/long-nve"
mkdir -p "$RESULT_ROOT"
python3 tests/long_nve/run_long_nve.py --candidate ./build/dmg-md \
  --devices "$GPU_ID" --profile smoke --report "$RESULT_ROOT/smoke.json"
```

### 定期回归和发布矩阵

包装脚本自动加载实验室环境，保留工作目录并追加 `run.log`。结果目录锁避免重复启动同一作业。首次运行时先构建所需候选，整个验收期间使用同一二进制。用 `--dry-run` 查看最后的命令，完整选项见 `--help`。

```bash
scripts/run_long_nve_nightly.sh --devices "$GPU_IDS_4" --result-root "$RESULT_ROOT/nightly-new"
scripts/run_long_nve_release.sh --devices "$GPU_IDS_8" --result-root "$RESULT_ROOT/release-new"
```

`--ui auto` 在交互终端显示总进度、用例、种子、后端、rank、阶段、尝试次数和耗时。非交互终端采用逐行输出。`--ui plain` 与 `--ui dashboard` 可显式指定。`LONG_NVE_*` 事件保存在无终端控制符的 `run.log`。新作业还创建 `provenance/`，记录源码提交、工作树状态、补丁、文件和二进制哈希，以及可用的 `CMakeCache.txt`。

单项诊断可用 `--cases`、`--seeds`、`--ranks` 和 `--sections short,long,replay,restart,nvt`。例如，需用首 3 个初态比较两种后端的 8 卡矩阵时，执行以下命令。结果只覆盖所选初态。

```bash
python3 tests/long_nve/run_long_nve.py --candidate ./build/dmg-md \
  --devices "$GPU_IDS_8" --profile release --seeds 0,1,2 \
  --backends HostStaged,CudaAware --report "$RESULT_ROOT/three-seeds.json"
```

### 查看报告与恢复中断

日志中的 `LONG_NVE_PLAN`、`LONG_NVE_CONFIG` 和 `LONG_NVE_STAGE` 分别报告计划、配置进度和阶段状态。Python 驱动默认对失败阶段重试一次，最多 2 次尝试。用 `--retries N` 调整额外尝试数，`--retries 0` 关闭重试。默认保留失败工作目录。成功时，用 `--keep-work` 保留完整目录，用 `--report` 保存 JSON。

每个完成阶段以原子写入方式保存 `.dmgmd-stage-complete.json`，包含输入、可执行文件、启动器、关键环境和输出哈希。失败阶段保存 `.dmgmd-stage-failure.json`。包装脚本可用原命令加相同 `--result-root` 续跑。它从唯一的 checkpoint 找到工作目录。若有多个，显式传 `--work-root`。续跑沿用配置、筛选、设备和候选，保留首次的源码记录。

直接调用 Python 驱动时，`RETAINED_WORK` 填日志结尾列出的残留工作目录。

```bash
python3 tests/long_nve/run_long_nve.py --candidate ./build/dmg-md \
  --devices "$GPU_IDS_8" --profile release \
  --report "$RESULT_ROOT/release-resumed.json" \
  --keep-work --resume-work "$RETAINED_WORK"
```

续跑重新核对 checkpoint 与产物哈希，并重做 Python 数值比较。已验证完成的 MD 阶段可以复用。失败或身份不符的阶段先移到 `.failed-<UTC>`，然后重跑。单个阶段的失败尝试另存为 `.failed-attempt-<N>-<UTC>`，内含 stdout、stderr 和失败 JSON。新尝试以空目录启动。

旧版工作目录缺可核验的候选身份。若需要迁移，必须显式加 `--adopt-existing`，报告会标记 `unverified`。生产代码已有物理变化时，应从新目录执行。

## 验收内容

### 短程严格比较

按全局原子 ID 比较静态构型和短轨迹的坐标、速度、力、逐原子能量、9 个 virial 分量、thermo 与文件结构。`nightly`/`release` 比较前 100 步，`smoke` 比较前 10 步。各字段使用[短程基线容差](../baseline/manifest.json)。

### 长期守恒

初始能量 `E(0)` 由独立静态作业提供。长程阶段从完成相应步数后开始取样，报告每原子最大能量偏移、线性漂移斜率、去趋势均方根、相邻采样最大能量跳变及每原子总线性动量最大变化。还记录最终均方位移（MSD）、采样最小原子间距及各元素对距离直方图。距离统计按全局索引确定性抽取最多 512 个中心，限制 Python 后处理开销。

manifest 在读取候选结果前冻结 25% 非劣效余量和各项绝对下限。参考与候选在同一批次运行，分别比较五初态的中位数与 95% 分位数。最终 MSD 和最小距离供诊断，构型分布以元素对直方图的最大 L1 距离判断。

### `nvt_ber` 统计正确性

三个物理体系在 300 K、`nvt_ber` 耦合参数 100 下平衡后采样。比较器分别计算温度的均值、标准差和均方根误差。MSD 以首个采样帧为原点，计算均值、末值、最大值和斜率。分元素的时间平均径向分布函数（RDF）按有向元素对归一化。

温度和 MSD 的中位数及 95% 分位数使用双侧等价检查。RDF 使用 manifest 固定的最大平均绝对箱差门槛。结果说明实现与 GPUMD 的兼容性。正则 NVT 采样需要针对可产生正确涨落的恒温器独立验收。

### 双向构型回放

把每个长期 GPUMD 快照交给 DMG-MD 做静态计算，再把同组 DMG-MD 快照交给 GPUMD 回放。两次比较都在完全相同的坐标上核对逐原子力、能量和 virial，避免把长程轨迹分叉误判为求力差异。

<a id="restart"></a>

### 跨进程数重启

每种体系的首个所选种子在半程保存兼容的 `restart.xyz`。分别用最少→最多、最多→最少 rank 跑完后半程。核对文件格式、顺序、字段、读回的边界静态量与后半程 NVE 统计。GPUMD 重启不保存全局时间，文本精度也会影响读回值，因此用重启边界和统计指标判断恢复结果。

## 运行模式与性能口径

默认正确性运行检查全量复制和局部域分解的数值与路由。验收依据为数值门槛。局部路径以负责原子和依赖中心计算 NEP，全量复制在每 GPU 重复整体系求力。`global_atom_steps_per_second` 用全局原子数与步数的乘积除以最慢 rank 的耗时，重复力计算不计入产出。

每个 candidate 的 `execution.stdout` 保留 `DMGMD_TIMING`。`phase=run` 是同步后的各运行段，含 MPI 与该段输出。`phase=total` 是全量复制 runtime 总耗时。默认正确性和 `--domain-timing` 显式启用 `DMGMD_DOMAIN_DIAGNOSTICS=1`，通过各 rank 的第 0 步 `DMGMD_DOMAIN_LAYOUT` 检查布局。`DMGMD_COMM_LOG_INTERVAL` 在配置中为 10 或 100，runtime 默认 1,000，短 MPI 测试显式设 1。`--performance` 关闭详细布局日志，把通信汇总降为每段末一次，仍核对启动、路由、唯一归属、通信字节、数值与计时。参考程序的环境不传 DMGMD 专用变量。

需要区分总体耗时与内部阶段时，在两个目录分别采集。性能运行使用 `DMGMD_TIMING`，诊断运行使用 `DMGMD_DOMAIN_TIMING*` 查看普通步、重建步和子阶段。两种模式都保留各次 `execution.stdout`、输入和 checkpoint。诊断开关写入 checkpoint 与报告，因此两类结果各自复现。

```bash
scripts/run_long_nve_nightly.sh --devices "$GPU_IDS_4" \
  --result-root "$RESULT_ROOT/cost-total" \
  --cases carbon_crystal,dense_water,batio3_zbl --ranks 1,2,4 \
  --backends HostStaged,CudaAware --sections long --performance
scripts/run_long_nve_nightly.sh --devices "$GPU_IDS_4" \
  --result-root "$RESULT_ROOT/cost-detail" \
  --cases carbon_crystal,dense_water,batio3_zbl --ranks 2,4 \
  --backends HostStaged,CudaAware --sections long --domain-timing
```

## O2 分区模式

`--partition-mode off|serial|verify` 默认 `off`。显式选择会写入候选环境与 checkpoint。父 shell 的旧 mask 开关无法替代，GPUMD 参考不接收此设置。修改兼容层后重做对应规模检查，沿用原容差。分区的逐原子、中间量及污染值矩阵见[分区标准](../../docs/standards/domain-partition.md)。

O8.6 使用完整 nightly 的主矩阵、回放和跨 rank 重启。两次分别选 `--schedule producer-compact` 和 `--schedule producer-geometry`，配合 `--partition-mode serial --overlap-mode wait`，各用单独的工作目录和报告。几何方案用固定 Verlet 参考坐标，窄条带允许回退全量计算。非空候选、空列表、失效和污染值由 MPI/GPU 专项检查。详见[生产者调度合同](../../docs/standards/domain-partition.md#o86-producer-紧凑与几何调度)。
