# DMG-MD 长程 NVE 正确性测试

本目录实现独立于短程 committed golden 的长期正确性验收。它不生成或宣称性能数据，也不要求
混沌轨迹在 100000 步后逐原子重合。GPUMD ordinary NEP 单 GPU仍是参考；DMG-MD 在相同初态
上接受短程严格比较、长期守恒统计、双向构型回放和跨 rank restart 检查。

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

## 三个 profile

| profile | steps | 初态 | ranks | thermo | trajectory | 用途 |
| --- | ---: | ---: | --- | ---: | ---: | --- |
| `smoke` | 100 | C seed 0 | 1 | 每 10 步 | 每 50 步 | 验证完整编排 |
| `nightly` | 10000 | 三体系 seed 0 | 1/2/4 | 每 100 步 | 每 1000 步 | 定期回归 |
| `release` | 100000 | 三体系 seeds 0–9 | 1/2/4/8 | 每 100 步 | 每 10000 步 | 发布/阶段门槛 |

默认通信后端是 HostStaged。CudaAware 使用同一 suite，但建议先用三组初态运行，再决定是否扩大
到全部十组。无论 profile 如何，MPI 环境预检都先于数值作业执行。

## 执行

从仓库根目录：

```text
source ../env/md-mpi.sh
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build-openmpi-ucx/dmg-md \
  --devices 0 --profile smoke \
  --report /tmp/dmgmd-long-nve-smoke.json
```

nightly：

```text
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build-openmpi-ucx/dmg-md \
  --devices 0,1,2,3 --profile nightly \
  --report /tmp/dmgmd-long-nve-nightly.json
```

完整 8 卡、10 初态、100000-step release：

```text
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build-openmpi-ucx/dmg-md \
  --devices 0,1,2,3,4,5,6,7 --profile release \
  --report /tmp/dmgmd-long-nve-release.json
```

CudaAware 三初态扩展矩阵：

```text
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build-openmpi-ucx/dmg-md \
  --devices 0,1,2,3,4,5,6,7 --profile release \
  --seeds 0,1,2 --backends HostStaged,CudaAware \
  --report /tmp/dmgmd-long-nve-cuda-aware.json
```

`--cases`、`--seeds`、`--ranks` 和 `--sections short,long,replay,restart` 可缩小诊断范围。
失败时临时工作目录总是保留；成功时只有显式 `--report` 指定的 JSON 报告会保留。

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
