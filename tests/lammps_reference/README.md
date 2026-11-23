# LAMMPS/MatPL NEP 对照输入与静态物理量检查

使用同一份初态比较 GPUMD、LAMMPS/MatPL 和 DMG-MD 的逐原子能量、力和 virial。模型和单位转换规则见[三引擎对照标准](../../docs/standards/cross-engine-reference.md)，数值门槛由本目录的 [manifest](manifest.json) 管理。

## 生成各引擎输入

在仓库根目录加载测试环境。把 `WORK_ROOT` 指向可写位置，每组输入使用新目录。从站点安装选取可执行文件。

```bash
source ../env/md-mpi.sh
REPO="$(pwd -P)"
WORK_ROOT="$REPO/../scratch/lammps-static-new"
python3 tests/lammps_reference/lammps_inputs.py --suite baseline \
  --output "$WORK_ROOT/baseline"
python3 tests/lammps_reference/lammps_inputs.py --suite long-nve \
  --profile smoke --seed 0 --steps 10 --output "$WORK_ROOT/long-nve"
```

`baseline` 生成 4 个用例的 5 个阶段，含从 golden restart 构造的续跑初态。`long-nve` 的 `smoke` 包括 3 组物理势和 4 组兼容性派生势。

每个阶段都有 `static/` 和 `nve/`，里面有 `model.xyz`、原始 GPUMD 势 `nep.gpumd.txt`、转换后的 LAMMPS 势 `nep.txt`、`data.lmp` 和 `in.lmp`。根目录的 `inputs.json` 记录原子数、步长、元素顺序、转换规则和哈希。`static/` 做第 0 步的物理量比较。`nve/` 从同一初态积分指定步数，供短轨迹观察。NVE 性能驱动也调用此转换器。

## 计算同一构型的静态量

以 `carbon_crystal` 为例。`GPU_ID` 填已分配的 GPU 编号或 UUID。`LAMMPS_BIN` 和 `GPUMD_BIN` 指向要比较的程序。LAMMPS 需要支持 MatPL NEP 的 Kokkos 构建。

```bash
GPU_ID=0  # 示例，换成分配的设备
LAMMPS_BIN="$(command -v lmp)"
GPUMD_BIN="$(command -v gpumd)"
STATIC="$WORK_ROOT/long-nve/smoke/carbon_crystal/seed-0/static"
GPUMD_WORK="$WORK_ROOT/gpumd-carbon"

(cd "$STATIC" &&
  CUDA_VISIBLE_DEVICES="$GPU_ID" "$LAMMPS_BIN" \
    -k on g 1 -sf kk -in in.lmp -log none > lammps.stdout)

mkdir "$GPUMD_WORK"
cp "$STATIC/model.xyz" "$GPUMD_WORK/model.xyz"
cp "$STATIC/nep.gpumd.txt" "$GPUMD_WORK/nep.txt"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/tests/long_nve" \
  python3 -c 'from long_nve_common import static_run; print(static_run(), end="")' \
  > "$GPUMD_WORK/run.in"
(cd "$GPUMD_WORK" &&
  CUDA_VISIBLE_DEVICES="$GPU_ID" "$GPUMD_BIN" > gpumd.stdout)
```

GPUMD 使用 `long_nve_common.static_run()` 生成的零步长输入，输出 `static.xyz`。LAMMPS 生成的 `static.lammpstrj` 必须含第 0 步。把两个文件交给比较器。

```bash
python3 tests/lammps_reference/compare_static.py \
  --gpumd-xyz "$GPUMD_WORK/static.xyz" \
  --lammps-dump "$STATIC/static.lammpstrj" \
  --potential "$STATIC/nep.gpumd.txt" \
  --report "$WORK_ROOT/carbon-lammps-physics.json"
```

如果已用 DMG-MD 计算同一初态，增加 `--dmgmd-xyz` 得到三组两两比较。比较报告用于静态物理量等价检查。GPUMD golden 与 DMG-MD 长程正确性的回归分别执行各自的门槛。
