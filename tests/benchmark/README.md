# 性能与诊断工具

`run_benchmark.py` 生成 NVE 输入，用于计时、资源采样和容量检查。实验室试用另有 NPT/WPE 对比，测量对象、科学门槛和已取得的结果见[性能方法与结果](../../docs/status/performance/npt-benchmarks.md)。正式 NPT/WPE 测量需接入对应引擎，并核对实际局部路由。进展见[开发进度](../../docs/plans/dmg-md-v1.0-progress.md)。

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

引擎之间的比较还要检查参考 checkout 的版本和可执行文件身份。`--reference` 和 `--lammps-bin` 只指定可执行文件，参考身份仍按[三引擎对照标准](../../docs/standards/cross-engine-reference.md)检查。L20 当前短程 golden 参考与三引擎运行器要求的参考身份不同，详见[当前状态](../../docs/status/current.md#l20-v1-audit-20261120)。

## 诊断入口

- 缓存归约用 `run_overlap_o7.py`。按需计算热力学量用 `run_overlap_o8.py`。重叠执行与生产者调度分别见 `run_overlap_o5.py` 和 `run_overlap_o85.py`。先用各程序的 `--help` 核对可选材料、后端、进程数和输出目录。运行时默认行为见[多卡实现导读](../../docs/reference/multi-gpu-implementation.md)。
- 独立核验 CPU 采样和核绑定。采样作业单独运行，正式对照使用相同的 CPU 和 NUMA 资源。详见 [CPU 采样](../../docs/standards/cpu-sampling.md)与[核绑定](../../docs/standards/cpu-binding.md)。
- 用 `python3 tests/benchmark/test_benchmark.py` 检查 NVE 驱动的 CPU 逻辑。科学正确性另按[短程基线](../baseline/README.md)、[长程 NVE](../long_nve/README.md)和 [NPT 验收](../npt/README.md)检查。

O9/O11 已结束。存档脚本按当时的文件名读取报告和实验产物。回看这些 NVE 结果时使用对应源码版本（例如 `0e8360f`）及该批次数据。
