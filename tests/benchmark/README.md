# 单节点、多 GPU 性能测试

类别：测试操作说明。参数以本目录 `manifest.json` 为准；不改变 `long_nve` 正确性验收。

## 测量对象与公平性

- DMG-MD：`mpiexec -n P dmg-md`，每 rank 一张 GPU；分别测 HostStaged / CudaAware。
- GPUMD：一个 `gpumd` 进程，只暴露 P 张 GPU。P=1 自动 ordinary NEP，P>1 自动
  `NEP_MULTIGPU`，`potential nep.txt y` 固定长轴。**不能用 mpiexec 启动 P 份 GPUMD**。
- 两者读取字节相同的 `model.xyz`、锁定势、显式速度，NVE、0.1 fs、seed 0；只有 GPUMD
  多卡输入增加分解轴参数。复用 long_nve 的确定性碳晶体/水初态生成器，按几何放大，保留密度。
  这是性能压力样本，不是已平衡的生产科学体系。碳晶体与水分别观察稳定邻域和迁移/重建成本。
- 每次重复都是全新进程、相同初态；同进程先 `run warmup`，再 `run steps`，最后一步输出一次
  thermo，不输出轨迹/restart。速度初始化、读文件、MPI 启动不计入正式段。正式段保留实际
  积分、邻域重建、通信和全局归约。预热不等于热力学平衡。
- DMG-MD 用第二条 `DMGMD_TIMING phase=run seconds_max`（所有 rank 中的最大同步耗时）；
  GPUMD 用第二条 `Time used for this run`。两者计时边界并非逐条指令完全相同：DMG-MD 的
  run 段包含自己的段内准备，GPUMD 在循环前做部分准备；这是各自实际 MD 段性能，不能解释为
  NEP kernel 微基准。最终 thermo 会读回 GPU 结果，避免把异步 kernel 发射时间当完成时间。
  同时记录整个子进程 wall time，不能拿它替代正式段。
- 默认关闭 DMGMD_DOMAIN_TIMING/DIAGNOSTICS，通信日志间隔大于段长；不修改 runtime/NEP。
  清除继承的全部 `DMGMD_*` 开关后显式设置性能环境，避免调试模式混入正式测量。
- 每轮随机化配置顺序，固定 order_seed；重复次数、步数、GPU 顺序与二进制必须统一。
  同一行两种引擎使用同一 GPU UUID 前缀，不偷偷选择各自最快的卡组。

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

# 正式矩阵，216 个串行 trial，建议 tmux + 独占节点
python3 tests/benchmark/run_benchmark.py --profile standard --output dmgmd-bench-standard

# 单独的大内存容量/近立方盒实验，不和主矩阵混跑
python3 tests/benchmark/run_benchmark.py --profile capacity --output dmgmd-bench-capacity
```

若用快速部署脚本构建，每条运行命令加上：

```text
--candidate ./build-benchmark/dmg-md --reference ./build-benchmark-gpumd/gpumd
```

默认 reference 是旧测试使用的 `../gpumd-reference/src/gpumd`。正式比较建议重新构建两者，
使用相同 CUDA/GCC/架构与 Release 选项，保留构建日志。运行器检查参考 checkout commit、
保存二进制 SHA-256，但不能仅凭旧二进制推断其一定来自该 checkout。其他 reference 路径也必须
保留同级锁定 `gpumd-reference` 源码用于审计。

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

`--cases` 覆盖 profile 的 case 列表。`--engines gpumd` 或 `dmgmd` 可诊断单方；
`--mpiexec-arg=--bind-to --mpiexec-arg=core` 可指定统一 MPI 绑核策略。
`--timeout` 是每次启动的总时限（包括读入、预热），默认 7200 s。
已有结果目录拒绝覆盖；结果逐 trial 原子写入，Ctrl-C 会终止当前启动进程及其后代（包括独立进程组的 MPI rank）并保留已完成项。
当前没有自动续跑/合并：按 case 分批可缩小重跑范围；不要跨不同环境/步数手工拼接比值。

## 共享服务器与可审计输出

启动前与每 trial 前检查所选 GPU 的计算进程、已用显存、利用率；有进程即停止，不终止他人任务。
默认允许 ≤256 MiB 显存、≤10% 利用率的无计算进程背景；阈值可调，但进程检查不能绕过。
采用 nvidia-smi 物理索引解析成完整 UUID，再写入 CUDA_VISIBLE_DEVICES，避免 CUDA 顺序歧义。

每 2 s 采样温度、功耗、SM/显存时钟、利用率、显存和计算 PID，并追踪启动进程的父子关系排除自身（MPI rank 可能自建进程组）；检测到
外来 GPU 进程的 trial 标记 contaminated，保留但不纳入汇总。采样不能证明没有短暂干扰，也
不能排除 CPU/内存/PCIe/磁盘竞争，因此正式结果仍需调度器独占节点/预留时段。脚本不改 GPU
功率或锁频，不把 CUDA-aware 等同于 GPUDirect P2P 实际可用；4090 的传输能力以实测拓扑和
UCX 路径为准。若通过其他工具锁频/设置功率，双方保持一致并附命令和恢复方案。

- `metadata.json`：命令、profile、随机顺序、GPU UUID、源码 revision/dirty 状态、二进制与
  生成器哈希、MPI/UCX/CUDA、CPU、拓扑、完整 GPU 配置和相关环境变量。
- `preflight.txt`：现有 MPI/CUDA 门槛，实际 device-buffer collective/p2p 自检；失败不启动矩阵。
- `inputs/`：可复现初态和势；每 trial 有 `run.in`、输入哈希、stdout/stderr、telemetry、结果 JSON。
- `results.json`：包括失败、OOM（查看原始 stderr）、超时、受干扰和过短 trial。
- `summary.{json,csv,md}`：只汇总有效样本，提供 min/median/max、相对范围、样本数和完成标记。
  只有重复次数完整的行与基线才能计算性能比。

记 `tP` 为固定步数正式段的 median：

- 吞吐 = `N × steps / tP`，ms/step = `1000 × tP / steps`。
- 强扩展 speedup = `t1/tP`；效率 = `t1/(P×tP)`，只用同引擎同后端同 N 的单卡基线。
- 弱扩展效率 = `t1(N1)/tP(P×N1)`；弱扩展不输出混淆含义的 speedup。
- 同卡数 `DMGMD/GPUMD = t_GPUMD/t_DMGMD`，>1 表示 DMG-MD 更快。
- 缺失/失败/OOM 的 P=1 基线，其扩展指标留空；绝不改用 P=2 伪装单卡基线。

此 suite 只检查最终 thermo 有限、模式和计时完整，不代替能量漂移/力误差验收。对生产代码
改动必须先跑已有 golden/nightly；正式发表时同时附正确性 revision 和结果。

## 测试脚本本身

```bash
python3 tests/benchmark/test_benchmark.py
```

CPU 测试覆盖几何门槛、预热排除、后端回退/错误绑定拒绝、受干扰数据排除、强弱扩展公式和
缺失基线，不启动 GPU。亦注册为 CTest `dmgmd.benchmark_analysis`。
