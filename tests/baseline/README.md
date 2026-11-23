<a id="gpumd-nep-md-baseline-harness"></a>

# 单卡 GPUMD 短程基线

本目录用锁定的普通 GPUMD 做单 GPU 数值参考，用相同输入检查 DMG-MD 的 NEP 计算、短轨迹、输出格式和重启。当前用例覆盖 8 或 9 原子的体系，其中连续 NVE 最多运行 6 步。用例、字段、单位、容差和参考身份统一从 [manifest](manifest.json) 读取。

## 核验参考输出与候选程序

从仓库根目录执行。`GPU_ID` 填一张已分配的 GPU 的编号或 UUID。第一条命令用参考程序逐字节核对 golden，第二条命令按 manifest 比较 DMG-MD 输出。

```bash
source ../env/md-mpi.sh
GPU_ID=0  # 示例，换成分配的设备
python3 tests/baseline/run_baselines.py --device "$GPU_ID"
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device "$GPU_ID"
```

参考程序默认为 `../gpumd-reference/src/gpumd`，可用 `--reference` 显式指定。脚本从可执行文件的解析路径向上两级核对 Git 提交和工作树状态，并检查参考二进制和输入的 SHA-256。L20 使用的普通 GPUMD 5.9.1 与旧参考的关系见[三引擎对照标准](../../docs/standards/cross-engine-reference.md)。

每次运行只向子进程暴露一张设备。脚本打印通过用例数与各类别的最大差异，失败时保留工作目录。需要检查成功作业的原始输出时加 `--keep-work`。CUDA 初始化失败会使测试失败，应先检查设备是否可用。

## 更新或校准参考

先审查参考版本以及新旧输出的差异，再执行对应的维护动作。更新 golden 和测量参考的重复运行差异是两个独立步骤。

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py --device "$GPU_ID" --update-goldens
python3 tests/baseline/run_baselines.py --device "$GPU_ID" --calibrate 30
```

更新要求参考仓库干净，提交和二进制哈希与 manifest 一致。校准例子重复完整矩阵 30 次并覆盖 `calibration.json`，容差仍由 manifest 管理。2026-09-09 的校准使用 GPUMD `9d23496e`。后续 L20 复验见[短程基线记录](../../docs/status/validation/baseline-results.md)。

## 输入及版本

三份 NEP 势文件最初逐字节复制自 GPUMD `9d23496e41319b9e2af5221a7df6285387401d1e`。右列路径相对于当时的 GPUMD 仓库根目录。

| 本目录路径 | GPUMD 源路径 |
| --- | --- |
| `inputs/potentials/nep_C.txt` | `potentials/nep/C_2024_NEP4.txt` |
| `inputs/potentials/nep_water.txt` | `tests_pytest/fixtures/models/nep_water.txt` |
| `inputs/potentials/nep_BaTiO3_zbl.txt` | `tests_pytest/fixtures/models/nep_BaTiO3.txt` |

`model.xyz`、`run.in` 和 `resume.in` 使用 GPUMD 原生格式。驱动按固定文件名复制到空目录再启动程序。需要将同一初态用于 LAMMPS/MatPL 时，按[跨引擎静态对照](../lammps_reference/README.md)生成输入，并依据可证明的物理量等价规则比较。

## 相关回归

- [长程验收](../long_nve/README.md)独立维护 5 初态、100,000 步发布矩阵、双向构型回放和跨进程数重启。运行前须核对两份 manifest 中的 GPUMD 提交和可执行文件哈希一致。
- [NHC 与 BDP](../thermostats/README.md)另有标量参考、统计、MPI 和重启测试。发布验收还需要这些专项结果。
- 修改 O2 分区涉及的兼容层中心选择后，复跑单 rank golden 与长程验收，沿用现有 golden 和容差。P1 与全量复制在显式分区开关下的同步回退由 MPI 域分解驱动检查。逐原子量、有效中间产物和污染值测试见[串行分区标准](../../docs/standards/domain-partition.md)。
