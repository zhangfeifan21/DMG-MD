# DMG-MD 长程 NVE/NVT 正确性测试

本目录实现独立于短程 committed golden 的长期 NVE 与确定性 NVT 统计正确性验收。它不生成
或宣称性能数据，也不要求混沌轨迹在 100000 步后逐原子重合。GPUMD ordinary NEP 单 GPU仍是
参考；DMG-MD 在相同初态上接受短程严格比较、长期守恒、温度/RDF/MSD统计、双向构型回放和
跨 rank restart 检查。

## 固定体系

`manifest.json` 定义并锁定 10 组显式速度初态。模型由
`long_nve_common.py` 确定性生成，每一组完整 `model.xyz` 的 SHA-256 都保存在 manifest；运行器
在启动任何 GPU 作业前重新生成并校验全部 30 个哈希。

| case | 原子数 | 盒与构造 | 势 | 固定 dt |
| --- | ---: | --- | --- | ---: |
| `carbon_crystal` | 4096 | 8×8×8 diamond conventional cells，28.56 Å 立方盒 | NEP4 C | 0.1 fs |
| `dense_water` | 12288 | 16³ 个带确定性取向/位置扰动的 H₂O，49.6 Å 立方盒 | 双元素 NEP4 | 0.1 fs |
| `batio3_zbl` | 5000 | 10³ 个 BaTiO₃ 晶胞；中心 Ti/Ba 距离 1.0 Å，进入 ZBL 插值区 | NEP4-ZBL | 0.01 fs |

这些是数值压力 fixture，不宣称是生产研究用的已平衡热力学样本。选定的 dt 是保守验收值；若
未来改成经 GPUMD 预平衡的科学体系，必须先做仅参考程序参与的 dt 收敛实验，再审查生成器、
模型哈希和 manifest，不能根据 DMG-MD 的失败反向修改输入或门槛。

同一 manifest 还包含 `carbon_nep5`、`dense_water_typewise_cutoff`、
`batio3_flexible_zbl` 和 `batio3_typewise_zbl_cutoff`。它们从上述锁定 potential 确定性生成，
生成后的完整文件也锁定 SHA-256；这些 compatibility case 只执行静态与短轨迹，用来封住此前
没有覆盖的 NEP5、typewise cutoff、flexible ZBL 和 typewise ZBL cutoff 分支。

## 三个 profile

| profile | NVE/NVT采样步数 | 初态 | ranks | backends | 用途 |
| --- | ---: | --- | --- | --- | --- |
| `smoke` | 100/100 | 三体系 seed 0 | 1 | HostStaged | 验证完整编排和全部语法变体 |
| `nightly` | 10000/10000 | 三体系 seed 0 | 1/2/4 | HostStaged + CudaAware | 定期回归 |
| `release` | 100000/100000 | 三体系 seeds 0–9 | 1/2/4/8 | HostStaged + CudaAware | 发布/阶段门槛 |

profile 已固定默认后端：smoke 为 HostStaged，nightly/release 为 HostStaged 与 CudaAware。
`--backends` 可用于缩小诊断范围，但发布门槛不得据此删去后端。无论 profile 如何，MPI 环境
预检都先于数值作业执行。

## 执行

从仓库根目录：

```text
source ../env/md-mpi.sh
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build/dmg-md \
  --devices 0 --profile smoke \
  --report /tmp/dmgmd-long-nve-smoke.json
```

nightly：

```text
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build/dmg-md \
  --devices 0,1,2,3 --profile nightly \
  --report /tmp/dmgmd-long-nve-nightly.json
```

完整 8 卡、10 初态、100000-step release：

```text
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build/dmg-md \
  --devices 0,1,2,3,4,5,6,7 --profile release \
  --report /tmp/dmgmd-long-nve-release.json
```

CudaAware 三初态扩展矩阵：

```text
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build/dmg-md \
  --devices 0,1,2,3,4,5,6,7 --profile release \
  --seeds 0,1,2 --backends HostStaged,CudaAware \
  --report /tmp/dmgmd-long-nve-cuda-aware.json
```

`--cases`、`--seeds`、`--ranks` 和 `--sections short,long,replay,restart,nvt` 可缩小诊断范围。
每个 MD stage 默认在失败后用相同参数从干净目录重试一次（最多两次尝试）；`--retries N` 可调整
额外尝试次数，`--retries 0` 可关闭重试。失败时临时工作目录总是保留；成功时只有显式
`--report` 指定的 JSON 报告会保留。

运行器为每个成功 stage 原子写入 `.dmgmd-stage-complete.json`，记录输入、可执行文件、launcher、
关键环境和输出哈希；失败 stage 写入 `.dmgmd-stage-failure.json`。从失败处续跑时，使用日志末尾
给出的 retained work directory，并保持原 profile/selection/candidate 不变：

```text
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build/dmg-md \
  --devices 0,1,2,3,4,5,6,7 --profile release \
  --report /persistent/path/dmgmd-long-nve-release.json \
  --keep-work \
  --resume-work /persistent/path/work/dmgmd-long-nve-release-XXXXXXXX
```

续跑会重新核验 checkpoint 和输出哈希、重做 Python 数值比较，但不会重新启动已经完成的 MD
stage；失败或不匹配的旧 stage 会保留为 `.failed-<UTC>` 后再执行。一次 stage 内触发重试时，
失败尝试保留为 `.failed-attempt-<N>-<UTC>`，其中包含该次的 stdout、stderr 和 failure JSON；
新尝试重新创建原 stage 目录，避免半成品输出污染重试。checkpoint 功能加入之前的
工作目录没有可验证的可执行文件身份，只能显式增加 `--adopt-existing` 做一次迁移；报告会标记
这种 provenance 为 unverified，不应在生产代码发生物理变化后采用。

终端以 `LONG_NVE_PLAN`、`LONG_NVE_CONFIG` 和 `LONG_NVE_STAGE` 记录总配置数、已完成/待完成数、
当前 stage、复用和失败状态。DMG-MD 的每个 `execution.stdout` 还包含机器可解析的
`DMGMD_TIMING`：`phase=run` 是同步后的每个 run segment（包含通信和该段输出），`phase=total`
是 replicated runtime 总耗时；`global_atom_steps_per_second` 使用全局原子数和所有 rank 中的
最大耗时计算，不把 replicated-full NEP 的重复计算量算成额外吞吐。

## 验收内容

### 短程严格比较

对静态构型和短轨迹按 global ID 比较 position、velocity、force、per-atom energy、
9-virial、thermo 和文件结构。`nightly`/`release` 比较前 100 步；只验证编排的
`smoke` 比较前 10 步。使用 `tests/baseline/manifest.json` 中已经校准的字段容差。

### 长期守恒

静态独立作业提供真正的 `E(0)`；长期作业只提供完成相应步数后的采样。计算并报告：

- 每原子最大能量偏移；
- 每原子能量线性漂移斜率；
- 去趋势 RMS；
- 相邻采样最大能量跳变；
- 每原子总线性动量最大变化；
- 最终 MSD、采样最小原子间距和按元素对分组的距离直方图。距离分析以按 global index
  确定性抽取的最多 512 个中心为样本，避免 Python 后处理退化为全体系 O(N²)。

manifest 在查看 candidate 长程结果前固定 25% 非劣 margin 和每项 absolute floor。运行器同时
执行同批 GPUMD reference，分别比较十初态的 median 与 q95。最终 MSD 和最小距离作为诊断量；
构型分布由元素对距离直方图的最大 L1 距离检查。

### NVT统计正确性

三个物理 case 以 300 K、`nvt_ber` coupling 100 先平衡后采样。运行器对 GPUMD 与 DMG-MD
分别计算温度 mean/std/RMSE、以采样首帧为原点的 MSD mean/final/max/slope，以及按元素有向对
归一化的时间平均 partial RDF。温度和 MSD 的 median/q95 做双侧 GPUMD 等价检查；RDF 使用
manifest 固定的最大 mean-absolute-bin-difference 门槛。这个结果证明实现兼容，不把 Berendsen
弱耦合 thermostat 或这些压力 fixture 宣称为 canonical NVT 科学采样。

### 双向构型回放

每个长期 trajectory 快照执行两条回放：GPUMD 快照交给 DMG-MD 静态计算，DMG-MD 快照交给
GPUMD 静态计算。在完全相同坐标上逐原子比较 force、energy 和 virial，因此长期轨迹的正常
混沌分叉不能掩盖力计算错误。

### Restart

每个体系的首个所选 seed 在半程写出兼容 `restart.xyz`，随后以最小→最大 rank 和最大→最小
rank 继续后半程。检查 restart schema/顺序/字段、读回边界静态量及后半程 NVE 统计。因为 GPUMD
restart 不保存 global time 且使用文本量化，本测试不要求它与未中断轨迹逐步重合。

## 非性能测试

运行器没有 wall-time、atom-steps/s、speedup 或 scaling 判据。当前 replicated prototype 每张
GPU仍执行完整 NEP，长测只验证正确性。`DMGMD_COMM_LOG_INTERVAL` 由 profile 设置为 10 或
100，减少长程 stdout；默认 runtime 行为仍是每步记录，现有短 MPI differential 不受影响。
