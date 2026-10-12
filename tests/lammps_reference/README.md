# LAMMPS/MatPL NEP 对照输入与静态物理量检查

类别：测试操作说明。三引擎横向判定原则见
[对照标准](../../docs/standards/cross-engine-reference.md)；具体误差门槛见本目录
`manifest.json`。

从 `newmd` 根目录生成目前正确性测试使用的输入；输出目录可放在 `/tmp` 或
`newmd` 的忽略版本控制目录中，不向三个 reference checkout 写文件：

```bash
source ../env/md-mpi.sh
NEWMD_ROOT="$(pwd -P)"
python3 tests/lammps_reference/lammps_inputs.py --suite baseline \
  --output /tmp/dmgmd-lammps-baseline
python3 tests/lammps_reference/lammps_inputs.py --suite long-nve \
  --profile smoke --seed 0 --steps 10 --output /tmp/dmgmd-lammps-long
```

baseline 覆盖四个 case 的五个 stage（含从 committed restart 构造的 resume 初态）；
long-nve 的 smoke 覆盖三组物理势和四组兼容性派生势。每个 stage 有独立的
`static/` 与 `nve/` 目录，均包含 `model.xyz`、GPUMD 原势 `nep.gpumd.txt`、
LAMMPS 势 `nep.txt`、`data.lmp`、`in.lmp`；根目录 `inputs.json` 记录原子数、
时间步、元素顺序、转换规则与所有哈希。`static` 做 step-0 力/能量/virial，
`nve` 从相同初态积分指定步数。benchmark 的 LAMMPS 输入由
`tests/benchmark/run_benchmark.py` 使用同一个转换器按 manifest 生成。

以 `carbon_crystal` 为例运行单 GPU 静态参考：

```bash
cd /tmp/dmgmd-lammps-long/smoke/carbon_crystal/seed-0/static
CUDA_VISIBLE_DEVICES=0 "$NEWMD_ROOT/build-lammps-nep-kk-preserved/lmp" \
  -k on g 1 -sf kk -in in.lmp -log none > lammps.stdout
```

GPUMD 只暴露同一张 GPU，在另一个临时目录用对应 `model.xyz` 与
`nep.gpumd.txt` 作为 `nep.txt` 运行 `long_nve_common.static_run()` 生成的
`run.in`；其 `static.xyz` 与 LAMMPS dump 可这样比较：

```bash
python3 "$NEWMD_ROOT/tests/lammps_reference/compare_static.py" \
  --gpumd-xyz /tmp/gpumd-carbon/static.xyz \
  --lammps-dump /tmp/dmgmd-lammps-long/smoke/carbon_crystal/seed-0/static/static.lammpstrj \
  --potential /tmp/dmgmd-lammps-long/smoke/carbon_crystal/seed-0/static/nep.gpumd.txt \
  --report /tmp/carbon-lammps-physics.json
```

若已有 DMG-MD 同初态静态 XYZ，可加 `--dmgmd-xyz`，比较器同时输出三组两两
物理量比较。LAMMPS 的 `static` dump 必须含 step 0，`nve` 目录仅供短程轨迹
观察。现有 GPUMD/DMG-MD 长程正确性 runner 的 golden 门槛仍分别执行；此工具
只承担三引擎横向的静态等价检查。
