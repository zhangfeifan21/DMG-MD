# GPUMD NEP MD baseline harness

本目录只锁定单进程、单 GPU 的 GPUMD NEP MD reference 行为，不包含 MPI，也不修改
DMG-MD 或 GPUMD 的生产物理内核。

LAMMPS/MatPL 对照输入可用
[`tests/lammps_reference/lammps_inputs.py`](../lammps_reference/README.md)
从这四组 case 生成；跨软件按物理量等价比较，允许可证明等价的格式转换。
本目录的 GPUMD committed golden 文件哈希与逐字节检查仍独立有效，不作为
LAMMPS 横向通过条件；见[三引擎对照标准](../../docs/standards/cross-engine-reference.md)。

从 DMG-MD 仓库根目录重复运行全部 baseline：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py
```

脚本默认使用 `../gpumd-reference/src/gpumd`，自动把子进程限制为一张可见 GPU，并验证
reference commit、reference executable SHA-256 和每个输入文件的 SHA-256。无 GPU 时会以
CUDA 错误失败，不会把测试标记为 skip。可用 `--device GPU_ID_OR_UUID` 选择另一张单卡。

将 DMG-MD 可执行程序与 GPUMD goldens 做 differential 比较：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md
```

只有在审查 reference 与输出差异后才能重建 goldens：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py --update-goldens
python3 tests/baseline/run_baselines.py --calibrate 30
```

`--update-goldens` 要求 reference 仓库干净且 commit/二进制哈希与 manifest 完全一致。
`--calibrate 30` 重复完整矩阵 30 次并更新 `calibration.json`，但不会自行修改容差。

输入和来源：

- `inputs/potentials/nep_C.txt`：逐字节复制自
  `../gpumd-reference/potentials/nep/C_2024_NEP4.txt`；
- `inputs/potentials/nep_water.txt`：逐字节复制自
  `../gpumd-reference/tests_pytest/fixtures/models/nep_water.txt`；
- `inputs/potentials/nep_BaTiO3_zbl.txt`：逐字节复制自
  `../gpumd-reference/tests_pytest/fixtures/models/nep_BaTiO3.txt`；
- case 目录中的 `model.xyz`、`run.in` 和 `resume.in` 均为直接提交、直接交给 GPUMD 的
  原生文本输入；执行时只按固定文件名复制到空工作目录，不解析后重写或转换。

完整覆盖、每项通过标准、单位、容差校准和已知非确定性来源见
[`docs/status/baseline-results.md`](../../docs/status/baseline-results.md)。

本目录继续只承担 8/9 原子、最多 6 个连续 NVE steps 的短程 committed golden。4096/12288/
5000 原子、100000-step、五初态 release、双向构型回放和跨 rank restart 位于
[`tests/long_nve/`](../long_nve/README.md)，两套基线不会互相覆盖或更新。

O2 的 compat 中心选择修改同样要求本套单 rank golden 与 long_nve 重验，不更新 golden 或
放宽容差。P1/M1 对显式分区开关的同步 fallback 由 MPI domain runner 专项验证；
分区逐原子量、有效中间产物及 poison 的验收合同见
[串行分区标准](../../docs/standards/domain-partition.md)。
