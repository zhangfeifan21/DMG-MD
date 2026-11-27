# 性能与诊断工具

`run_benchmark.py` 生成 NVE 输入，用于普通 GPUMD、LAMMPS/KOKKOS/NEP 与 DMG-MD 的同卡数计时，也可单列 GPUMD-WPE 的单卡样本。V1.0 优先公开碳、水、铜的 NPT，现有配对入口可接收各引擎已核验的作业输入，正式对照仍需通过各材料的科学验收和引擎先导。逐项核验等价控压、实际引擎、GPU 绑定和局部路径，可比性及状态见[性能方法与结果](../../docs/status/performance/npt-benchmarks.md#performance-matrix)和[开发进度](../../docs/plans/dmg-md-v1.0-progress.md)。

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

## 按区组运行与复算配对作业

`paired_timing.py` 处理准备好的作业输入。在已分配的单节点四卡窗口内，按材料和冻结顺序串行运行。所有配对输出使用同一服务器挂载，作业记录保存实际挂载、文件系统及可取得的设备信息。设置 `RESERVATION` 为真实的独占分配记录，`OUT` 为服务器上的输出目录。各次 `run` 需要新的子目录。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/paired_timing.py freeze smoke "$CONFIG" "$OUT/smoke-plan.json"
python3 tests/benchmark/paired_timing.py run "$OUT/smoke-plan.json" "$OUT/smoke" --reservation "$RESERVATION"
python3 tests/benchmark/paired_timing.py replay "$OUT/smoke"

python3 tests/benchmark/paired_timing.py freeze pilot "$CONFIG" "$OUT/pilot-plan.json"
python3 tests/benchmark/paired_timing.py run "$OUT/pilot-plan.json" "$OUT/pilot" --reservation "$RESERVATION"
python3 tests/benchmark/paired_timing.py freeze sample "$CONFIG" "$OUT/sample-plan.json" --pilot-run "$OUT/pilot"
python3 tests/benchmark/paired_timing.py run "$OUT/sample-plan.json" "$OUT/sample" --reservation "$RESERVATION"
```

配置 JSON 顶层填写 `cpu_budget`（整项作业的物理核数）、`cells` 和 `comparisons`。每个格子填写 `id`、`material`、`engine`、`devices`、`physics` 和 `variants`。物理参数至少包括 `ensemble`、`model_sha256` 和 `potential_sha256`。每项对比填写 `id`、`numerator` 和 `denominator`。普通 GPUMD 和 LAMMPS 对 DMG-MD 须同卡数，WPE 固定单卡。自身强扩展可另填 `divisor=P` 计算效率。

每个阶段的配置给出 `engine`、`input_dir`、`command`（绝对可执行路径及参数列表）、`inputs` 和 `binaries`（文件名与 SHA-256 对照）、`steps`、`thermo_interval`、`atoms` 和 `ranks`。绑核由启动命令实施，驱动在运行中核验。DMG-MD 另填 `expected_backend` 和 `expected_domain_mode`。LAMMPS 格子用 `input_equivalence` 登记共同快照、原势及已验证的转换证据。无法关闭的额外输出在 `extra_outputs` 按文件登记原因。可运行的测试配置见 [CPU 端到端用例](test_paired_timing.py)。

短作业使用 `variants.smoke`。准备正式采样时，在同一配置中登记 `pilot`、`warmup` 和 `sample`。先导每格 10,000 步 × 5 次。进程墙钟的样本 CV 大于 2% 时正式运行 5 个区组，其余运行 3 个区组。每个正式区组开始前各作业单独预热 1,000 步，正式轨迹各 100,000 步。两侧重复数不同时，配对区间使用共同区组，各侧绝对时间计入全部有效重复。

每项作业的 `audit.json` 记录区组、执行顺序和实际资源。`work/paired-job.json` 记录输入及程序身份、启动阶段、引擎用时、thermo 时间窗口、退出状态和输出检查。`replay` 校验冻结清单与原始记录后重算 `summary.json`。第 1,000 步和末步给出稳态步时。尾段上界超过自身墙钟 1% 时同时给出扣尾区间，并标明阈值判定是否受观察精度影响。

按实际工况填写[预算模板](paired_budget_template.json)，用 `python3 tests/benchmark/paired_statistics.py budget "$BUDGET"` 分别复算各类机时及独占节点小时。[L20 短作业验收](../../docs/status/performance/npt-benchmarks.md#paired-timing-smoke-20261127)

## 诊断入口

- 缓存归约用 `run_overlap_o7.py`。按需计算热力学量用 `run_overlap_o8.py`。重叠执行与生产者调度分别见 `run_overlap_o5.py` 和 `run_overlap_o85.py`。先用各程序的 `--help` 核对可选材料、后端、进程数和输出目录。运行时默认行为见[多卡实现导读](../../docs/reference/multi-gpu-implementation.md)。
- 独立核验 CPU 采样和核绑定。采样作业单独运行，正式对照使用相同的 CPU 和 NUMA 资源。详见 [CPU 采样](../../docs/standards/cpu-sampling.md)与[核绑定](../../docs/standards/cpu-binding.md)。
- 用 `python3 tests/benchmark/test_benchmark.py` 检查 NVE 驱动的 CPU 逻辑。科学正确性另按[短程基线](../baseline/README.md)、[长程 NVE](../long_nve/README.md)和 [NPT 验收](../npt/README.md)检查。

O9/O11 已结束。DMG-MD 内部计时直接使用各实验的候选和对照。离线复算早期归档时，脚本从当批数据中读取封存的证据。回看这些 NVE 结果时使用对应源码版本（例如 `0e8360f`）及该批次数据。
