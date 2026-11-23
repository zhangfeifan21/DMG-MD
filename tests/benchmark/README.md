# 性能与诊断工具

`run_benchmark.py` 生成 NVE 输入，用于普通 GPUMD、LAMMPS/KOKKOS/NEP 与 DMG-MD 的同卡数计时，也可单列 GPUMD-WPE 的单卡样本。V1.0 优先公开碳、水、铜的 NPT，须扩展运行器以覆盖两项同卡数对标和固定单卡 WPE 对照，同时报告 DMG-MD 单卡、强扩展和 GPU·小时。逐项核验等价控压、实际引擎、GPU 绑定和局部路径，可比性及状态见[性能方法与结果](../../docs/status/performance/npt-benchmarks.md#performance-matrix)和[开发进度](../../docs/plans/dmg-md-v1.0-progress.md)。

## 使用既有 NVE 运行器

从仓库根目录运行。实验室维护环境用 `../env/md-mpi.sh`，普通账号按[部署手册](../../docs/operations/deployment.md)加载等价环境。先用 `--dry-run` 查看计划中的几何、原子数、引擎和后端。真实的 smoke 例子只选 DMG-MD，使用一张已分配的 GPU。

```bash
source ../env/md-mpi.sh
GPU_ID=0  # 示例，换成分配的设备
python3 tests/benchmark/run_benchmark.py --profile standard --dry-run
python3 tests/benchmark/run_benchmark.py --profile smoke --engines dmgmd \
  --devices "$GPU_ID" --ranks 1 --output ../scratch/dmgmd-bench-smoke-new
```

`smoke` 检查编排，`pilot` 校准预算和规模，`standard` 使用固定规模矩阵，`capacity` 检查显存和可运行规模。`--output` 指定目录前缀，驱动会添加时间戳并创建新目录。`metadata.json` 保存运行参数、引擎身份和计划，计时汇总见 `summary.md`、`summary.csv` 和 `summary.json`。实际选项以 [manifest](manifest.json) 和 `run_benchmark.py --help` 为准。

加载站点安装后，脚本可从 `PATH` 查找所选引擎的 `gpumd`、`gpumd-wpe` 或 `lmp`。也可显式传入下列路径变量。普通 GPUMD 的 `--reference` 与 `--gpumd-bin` 等价。构建辅助脚本 `scripts/build_benchmark.sh` 只构建 DMG-MD，其打印路径可传给 `--candidate`。

```bash
python3 tests/benchmark/run_benchmark.py --profile smoke \
  --engines gpumd,wpe,lammps,dmgmd --devices 0,1 --ranks 1,2 --backends HostStaged \
  --gpumd-bin "$GPUMD_BIN" --wpe-bin "$WPE_BIN" --lammps-bin "$LAMMPS_BIN" \
  --output ../scratch/nve-smoke
```

WPE 仅在 P=1 生成作业。程序从运行日志读取可用的版本标记，写入 `results.json` 和 `summary.md`。可执行文件路径及 SHA-256 记入 `metadata.json`。其他版本可直接使用，作业仍核对计时段、实际后端和设备。L20 已测版本见[性能对照引擎](../../docs/standards/cross-engine-reference.md#benchmark-engines)。

## 诊断入口

- 缓存归约用 `run_overlap_o7.py`。按需计算热力学量用 `run_overlap_o8.py`。重叠执行与生产者调度分别见 `run_overlap_o5.py` 和 `run_overlap_o85.py`。先用各程序的 `--help` 核对可选材料、后端、进程数和输出目录。运行时默认行为见[多卡实现导读](../../docs/reference/multi-gpu-implementation.md)。
- 独立核验 CPU 采样和核绑定。采样作业单独运行，正式对照使用相同的 CPU 和 NUMA 资源。详见 [CPU 采样](../../docs/standards/cpu-sampling.md)与[核绑定](../../docs/standards/cpu-binding.md)。
- 用 `python3 tests/benchmark/test_benchmark.py` 检查 NVE 驱动的 CPU 逻辑。科学正确性另按[短程基线](../baseline/README.md)、[长程 NVE](../long_nve/README.md)和 [NPT 验收](../npt/README.md)检查。

O9/O11 已结束。DMG-MD 内部计时直接使用各实验的候选和对照。离线复算早期归档时，脚本从当批数据中读取封存的证据。回看这些 NVE 结果时使用对应源码版本（例如 `0e8360f`）及该批次数据。
