# GPUMD NEP MD differential/golden baseline 结果

类别：进度与实测备忘。  
结果日期：2026-09-09。  
状态：历史结果有效，但不能代替对当前 HEAD 的重新执行。

## 1. 结论

锁定 GPUMD reference 的四组短程 baseline 已生成 committed golden，并在单张 NVIDIA
GeForce RTX 4090 上完成独立全量复跑和 30 次重复校准。随后 DMG-MD candidate 也通过相同
四组 differential case。本文只记录这次已执行结果；当前验收规则见
[golden-test-standard.md](../standards/golden-test-standard.md)，实际参数以测试脚本和 manifest
为准。

锁定 reference：

- GPUMD commit：`9d23496e41319b9e2af5221a7df6285387401d1e`；
- executable SHA-256：
  `3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`；
- GPU：NVIDIA GeForce RTX 4090，compute capability 8.9；
- driver：580.173.02；CUDA compiler：12.9 / nvcc 12.9.86；
- reference 子进程固定只看见一张 GPU，确保走 ordinary `NEP`，不进入
  `NEP_MULTIGPU`。

原记录没有单独保存与本结果严格对应的 DMG-MD Git commit，因此不得把本文结果直接解释为
当前任意 commit 已通过。当前进度声明见 [current.md](./current.md)。

## 2. 已执行矩阵

| case | 已执行覆盖 | 主要输出 |
| --- | --- | --- |
| `single_small_static` | 单元素 NEP4、small-box、多周期 image、静态 force/PE/virial | `initial.xyz`、`thermo.out`、`neighbor.out` |
| `single_large_nve` | 单元素 NEP4、large-box、两个连续 run、6 步 NVE、restart | 静态帧、短轨迹、thermo、restart |
| `multi_nvt_restart` | 双元素 NEP4、`nvt_ber`、三个 run、restart 后新进程续跑 | initial/resume 两阶段输出 |
| `nep_zbl_boundary` | 三元素 universal NEP4-ZBL、跨周期边界 0.5 Å 近邻 | 静态逐原子量和 neighbor |

所有动力学输入均提供显式 velocity，因此没有把默认随机初速度混入数值 baseline。短程 harness
只逐原子比较 6 步以内的轨迹；长期守恒与统计由 `tests/long_nve/` 独立承担。

## 3. 实际运行命令

在 DMG-MD 仓库根目录执行：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py \
  --reference ../gpumd-reference/src/gpumd --device 0
python3 tests/baseline/run_baselines.py \
  --candidate ./build/dmg-md --device 0
python3 tests/baseline/run_baselines.py \
  --reference ../gpumd-reference/src/gpumd --device 0 --calibrate 30
```

当时结果为：

- pinned GPUMD reference 的所有输出与 committed golden 逐字节一致；
- 30 次 repetition 的全部结构字段一致，已比较数值的最大绝对差和相对差均为 0；
- DMG-MD candidate 的四组 case 全部通过 manifest 中的结构和数值门槛。

一次完整 repetition 包含 4 个 case、5 个独立 GPUMD 进程；30 次校准共执行 150 个进程。
校准覆盖 12,528 个 force 数、42,021 个 virial 数、5,655 个 energy 数和 12,528 个
position 数，以及 thermo/restart 字段。

## 4. 本结果证明和不证明的内容

本结果证明：

- 固定 reference binary、固定输入和固定单卡选择可重建 committed golden；
- 四组 case 的文件集合、schema、原子顺序、时间点和数值字段得到自动比较；
- 当前混合精度路径在记录环境中具有稳定的短程输出。

本结果不证明：

- 换 GPU 架构、编译器或 CUDA 版本后仍逐字节一致；
- MPI reduction tree 或不同 rank 数保持 bitwise；
- 10000/100000-step nightly/release 已完整执行；
- domain decomposition、ghost、halo 或 migration 已实现或通过。

## 5. 权威产物和更新纪律

| 内容 | 权威来源 |
| --- | --- |
| runner 行为 | `tests/baseline/run_baselines.py` |
| case、输出结构、容差、输入哈希 | `tests/baseline/manifest.json` |
| committed reference 输出 | `tests/baseline/goldens/` |
| 30 次校准原始汇总 | `tests/baseline/calibration.json` |
| 使用方法 | `tests/baseline/README.md` |

只有在明确审查 reference 升级或 baseline 意图变化后，才可运行 `--update-goldens`。更新必须
同时审查 reference commit/二进制哈希、输入哈希、golden diff、重复校准和本文结果记录；比较
失败本身不是更新 golden 或放宽容差的理由。
