# DMG-MD Golden Test 标准

类别：现行测试标准。  
状态：ACTIVE。  
锁定参考：GPUMD commit `9d23496e41319b9e2af5221a7df6285387401d1e`。

## 1. 文档边界与事实源

本标准说明当前已实现测试的验收层级和判定原则，不复制所有机器可读参数。发生冲突时按以下
顺序处理：

1. 测试脚本决定实际执行行为；
2. `manifest.json` 决定 case、profile、哈希、容差和统计门槛；
3. 测试目录 README 说明命令行和运维方法；
4. 本文件说明为什么测试、如何判定以及哪些门槛尚未实现；
5. `docs/status/` 只记录某次实际运行结果，不能反向定义标准。

不得把“脚本中已经定义”写成“已经执行通过”。任何通过声明必须给出日期、代码 revision、
环境、精确命令和结果，并写入 `docs/status/`。

## 2. 当前自动化入口

### 2.1 CPU/CTest

`ctest --test-dir build --output-on-failure` 当前注册四项：

| test | 当前覆盖 |
| --- | --- |
| `dmgmd.run_parser` | `run.in` typed IR、支持和拒绝路径、参数校验 |
| `dmgmd.model_parser` | model schema、类型、质量、盒和输入错误 |
| `dmgmd.spatial_ownership` | M1 slab 纯逻辑：s=0/s=1/内部边界及两侧、周期首尾迁移、一步跨多 slab、空 owned list 与 N<P、cube/最长轴 tie（含负向 lattice edge）、owner 唯一覆盖、index 排序、mask/list 一致、malformed global_id，以及非 canonical displacement/local send count/非 permutation scatter plan 拒绝（取代 M0 的 balanced owned range 测试） |
| `dmgmd.long_nve_analysis` | fixture/potential 生成、哈希、统计量、checkpoint、重试和失败分类 |

CTest 不会自动启动 baseline GPU、MPI differential 或长程 GPU 矩阵。

### 2.2 单 GPU committed baseline

入口为 `tests/baseline/run_baselines.py`，由 `tests/baseline/manifest.json` 定义四个 case：

- `single_small_static`；
- `single_large_nve`；
- `multi_nvt_restart`；
- `nep_zbl_boundary`。

reference 模式必须校验 reference repository、commit、executable hash、输入 hash、单 GPU
可见性和 committed golden hash，并要求输出逐字节一致。candidate 模式对结构字段做 exact
比较，对浮点物理量使用 manifest 容差。

### 2.3 Replicated-data MPI differential

入口为 `tests/mpi/run_mpi_differential.py`。默认矩阵为 1/2/4 rank × HostStaged/CudaAware，
每个组合运行完整四组 baseline case，并进一步检查：

- Open MPI+UCX/CUDA 环境预检先于 numerical case；
- 每 rank 唯一 GPU UUID、后端 capability 与主动数值自检；
- M1 空间 ownership：coverage proof 无遗漏、无重叠，per-rank owned counts 覆盖
  global_count（个别 rank 可为 0）；
- rank 0 输出与 committed golden 一致；
- `DMGMD_COMM` 的采样步、collective 次数和分后端字节字段合法；
- 短 NVE excursion/drift 与 reference 的差异不超过由 energy 容差导出的门槛；
- 不同 rank/backend 的输出直接互比。

该矩阵验证 replicated-data + M1 空间所有权的 correctness，不是 halo 域分解或性能测试。

### 2.3a M1 迁移矩阵

入口为 `tests/mpi/run_mpi_migration.py`，fixture（显式初速度，不依赖随机初速度）在
同一环境门槛后以 1/2/4 rank × HostStaged/CudaAware 运行，覆盖：跨内部边界、周期端
回绕、一步跨多 slab、暂时空 slab、N<P、correct_velocity 触发步、多段 run、
velocity/force/potential/virial/unwrapped 输出、dump_restart 后跨 rank 数恢复。
判定包括：每 epoch 的 `owned_sum==N` 与 transitions 记账；用逐步 double 精度
wrapped position 重算期望 owner 并要求日志迁移完全一致；P=1 永不迁移；各 fixture
必须实际产生其声明的内部/周期/多 slab/空 slab/N<P 场景；每步 collective 数量以及
MPI、HostStaged D2H/H2D、CudaAware output-download 字节逐字段精确匹配协议公式；跨
rank/backend 输出对 1-rank 参考的 committed 容差比较（容差不放宽）；作业目录只有
rank 0 输出。同一 runner 还含 unsupported-box 门：triclinic 与非周期方向输入在
P=2 上必须被所有 rank 以完全相同、可诊断的错误拒绝且不产生 MD 输出，同时同一输入
在 P=1 仍被接受（M0 兼容路径不选择分区轴）。机器可读参数以脚本内 fixture 常量为
第一事实源。

### 2.4 长程 NVE/NVT suite

入口为 `tests/long_nve/run_long_nve.py`，机器可读合同位于
`tests/long_nve/manifest.json`。当前包含：

- 三个物理 fixture：4096-atom C、12288-atom water、5000-atom BaTiO3/ZBL；
- 四个只跑静态/短轨迹的兼容分支：NEP5、typewise cutoff、flexible ZBL、typewise ZBL
  cutoff；
- `short`、`long`、`replay`、`restart`、`nvt` 五个 section；
- smoke 100 steps、nightly 10000 steps、release 100000 steps；
- release 使用 5 组显式初态（seed 0–4）、1/2/4/8 rank 和两种通信后端。

精确 profile 列表以 manifest 为准。命令行的 `--cases`、`--seeds`、`--ranks`、`--backends`
和 `--sections` 只用于缩小诊断范围；用子集通过不能宣称完整 profile 通过。

## 3. 比较层级

### 3.1 必须完全一致

- 成功/失败分类；
- 文件集合、文件名、frame 数、输出 step；
- N、species、整数列、原子 global-ID 顺序；
- header、Properties schema、列顺序和 token 数；
- parser IR 中的命令类别和离散参数；
- potential 版本、type/symbol 顺序、参数数量和输入哈希；
- ownership 覆盖（无遗漏/无重叠/owned counts 覆盖）、rank 0 唯一输出等结构不变量。

reference 在锁定环境中还要求与 committed golden byte-exact。不能由此推导 candidate、跨
GPU 架构或跨 MPI reduction tree 必须 byte-exact。

### 3.2 使用确定性数值容差

position、velocity、force、energy、virial、thermo、stress、restart 量化值和短轨迹采用：

```text
abs(test - reference) <= atol + rtol * abs(reference)
```

精确阈值只在 `tests/baseline/manifest.json` 维护。身份、schema、neighbor 集合或所有权错误
不能用浮点容差掩盖；失败后也不得为单个 case 原地放宽门槛。

### 3.3 使用长期或统计判定

长 NVE 不要求混沌分叉后的逐步坐标重合，而比较：

- 真正 `E(0)` 对应的每原子最大 excursion；
- drift slope、detrended RMS、最大相邻采样跳变；
- 每原子总动量变化；
- 元素对距离直方图；
- GPUMD 与 DMG-MD 长期快照的双向静态回放。

NVT 比较温度 mean/std/RMSE、MSD mean/final/slope 和时间平均 partial RDF。多初态汇总使用
median/q95；非劣 margin、absolute floor 和双侧统计门槛只在 long-NVE manifest 维护。
Berendsen 结果只证明与锁定 GPUMD 的实现兼容，不代表 canonical NVT 采样质量。

## 4. fixture 与基线纪律

- reference 必须只看见一张 GPU，禁止自动进入 `NEP_MULTIGPU`；
- 每个 case/stage 在独立空目录执行，避免 append 输出污染；
- 输入、potential、reference executable 和 committed golden 均由 SHA-256 锁定；
- dynamics fixture 使用显式 velocity，随机初始化另设专项测试；
- 更新 golden 必须使用显式 `--update-goldens` 并审查 diff；
- 容差只能根据重复基线实验、格式量化和数值分析更新；
- 长程压力 fixture 不宣称为生产科学用的已平衡体系。

## 5. 长作业恢复、失败证据与 timing

长程 runner 默认每个失败 stage 额外重试一次，可用 `--retries` 调整。每次重试从干净 stage
目录开始，失败尝试保留为 `.failed-attempt-*`；最终失败写
`.dmgmd-stage-failure.json`。

成功 stage 的 `.dmgmd-stage-complete.json` 锁定输入、executable、launcher、关键环境和输出
哈希。`--resume-work` 只复用签名及输出哈希仍匹配的 stage；旧版无 checkpoint 的目录只有显式
`--adopt-existing` 才能采用，并在报告中标记为未验证 provenance。

candidate stage 还必须存在结构合法的 `DMGMD_TIMING phase=run/total` 记录。timing、wall time
和 `global_atom_steps_per_second` 目前只用于诊断，没有 speedup、scaling 或吞吐通过门槛。

## 6. 当前实现状态与未来测试

| 层级 | 当前状态 | 归属 |
| --- | --- | --- |
| GPUMD 单 GPU committed baseline | 已实现 | baseline runner |
| DMG-MD 单 rank differential | 已实现 | baseline runner |
| replicated-data MPI 1/2/4 rank | 已实现 | MPI runner |
| 长 NVE/NVT、回放、跨 rank restart | 已实现 | long-NVE runner |
| domain decomposition、owned/ghost halo | 未实现（M2 计划） | `docs/plans/domain-decomposition.md` |
| M1 空间所有权逻辑迁移 fixtures | 已实现（`run_mpi_migration.py`） | MPI migration runner |
| 分区面精确边界、周期 ghost（M2 边界 fixture） | 未实现 | `docs/plans/domain-decomposition.md` |
| 多节点本地 scratch/故障注入 | 自动化已定义；严格双节点验收 IN PROGRESS | `tests/mpi/run_rank_io_isolation.py`、`docs/plans/multi-node-io.md` |
| 完整 malformed/invalid compatibility corpus | 未完成 | `docs/plans/risk-and-backlog.md` |

未来计划不得提前写入本标准的“当前通过矩阵”。实现并通过验证后，再把生效合同迁入
`docs/standards/`，并在 `docs/status/` 记录实际结果。
