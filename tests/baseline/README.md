# GPUMD NEP MD baseline harness

本目录只锁定单进程、单 GPU 的 GPUMD NEP MD reference 行为，不包含 MPI，也不修改
DMG-MD 或 GPUMD 的生产物理内核。

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
