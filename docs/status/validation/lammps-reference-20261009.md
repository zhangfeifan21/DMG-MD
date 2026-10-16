# LAMMPS/MatPL 三引擎对照验证（2026-10-09）

类别：进度与实测备忘。此记录对应 `newmd` HEAD
`0ba54eea4e16d157a01c0fb6e371b0af7d33032e` 加实验当时未提交的工作树；
当时新增输入/比较脚本未提交，单靠该 HEAD 不能复现本记录。

## 环境与软件身份

- LAMMPS reference：`stable_22Jul2025_update4`，
  `611ca3b7f8525ba802373b04f9e3d632d515f7e3`；MatPL reference：
  `21fabf34eadc57a717f4c7fbce9bb80aad5fd991`；GPUMD reference：
  `9d23496e41319b9e2af5221a7df6285387401d1e`。三个 checkout 的
  tracked/untracked Git 状态均干净，测试输入与输出均在 `/tmp` 或 `newmd` 内。
- 已保存的 LAMMPS/MatPL KOKKOS CUDA 可执行文件
  `build-lammps-nep-kk-preserved/lmp` SHA-256：
  `3c9fb06b0489ae93ad58c5d2680d8818dc9cbe1275e5c7bf2c6e176b301005e6`。
  DMG-MD `build/dmg-md` SHA-256：
  `13da7a81c9c77f46b53dd45ada81d7a593634800c30b2714c13d2fa3bc93b80d`；
  GPUMD `../gpumd-reference/src/gpumd` SHA-256：
  `3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`。
- NVIDIA RTX 4090，CUDA 12.9，Open MPI 5.0.10，UCX 1.22.0。运行前
  `source ../env/md-mpi.sh`；MPI 环境预检已通过 CUDA-aware device collective
  与 `cuda_copy/cuda_ipc` 检查。LAMMPS 日志确认
  `matpl/nep/kk` 的 KOKKOS GPU 路径；1/2 rank 的 GPU UUID 绑定记录均唯一。

旧 LAMMPS 原位安装的 MatPL overlay 与逐文件哈希已移到
`third_party/lammps-matpl-overlay/`，当时的构建物移到
`build-lammps-nep-kk-preserved/`。保留构建的 CMake cache 含旧绝对路径，
只用于保留和运行已验过的可执行文件，不能直接作为可迁移构建目录。

## 输入与静态物理量

从 `newmd` 根目录执行的输入生成命令：

```bash
python3 tests/lammps_reference/lammps_inputs.py --suite baseline \
  --output /tmp/dmgmd-lammps-baseline-v2
python3 tests/lammps_reference/lammps_inputs.py --suite long-nve \
  --profile smoke --seed 0 --steps 10 --output /tmp/dmgmd-lammps-long-v2
```

baseline 四个 case 的五个 stage 均生成 LAMMPS 静态/NVE 输入；本次静态运行与
committed GPUMD 初态的对照覆盖四个主/初始 stage，4/4 PASS。long-NVE 的
三组物理势及四组兼容势均生成并运行静态输入，7/7 PASS。每个
`static/physics.json` 保存逐原子物理量报告；结果目录分别为
`/tmp/dmgmd-lammps-baseline-v2` 和 `/tmp/dmgmd-lammps-long-v2`。
LAMMPS 使用 `-k on g 1 -sf kk -in in.lmp -log none`；GPUMD 只暴露一张
GPU，按 `long_nve_common.static_run()` 生成 `static.xyz`。比较器入口：

```bash
python3 tests/lammps_reference/compare_static.py \
  --gpumd-xyz /tmp/dmgmd-gpumd-long-v2/carbon_crystal/static.xyz \
  --lammps-dump /tmp/dmgmd-lammps-long-v2/smoke/carbon_crystal/seed-0/static/static.lammpstrj \
  --potential /tmp/dmgmd-lammps-long-v2/smoke/carbon_crystal/seed-0/static/nep.gpumd.txt \
  --report /tmp/dmgmd-lammps-long-v2/smoke/carbon_crystal/seed-0/static/physics.json
```

11 个 GPUMD/LAMMPS 静态对照全部通过
`tests/lammps_reference/manifest.json` 的位置、速度（源输出存在时）、
每原子能量、力、virial 与总势能门槛。baseline 最大力误差占对应容差
0.019，最大 virial 误差占对应容差 0.025；long-NVE 对应最大值为
0.638 和 0.418。额外在 4096 原子 `carbon_crystal` 上执行 DMG-MD
单 rank 静态计算，三组两两比较全部 PASS；DMG-MD 命令与比较命令为：

```bash
cd /tmp/dmgmd-threeway-carbon
CUDA_VISIBLE_DEVICES=0 DMGMD_COMM_BACKEND=HostStaged mpiexec -n 1 \
  /data/home/zhangyifei/fdata/zhangyifei/newmd/newmd/build/dmg-md \
  > run.stdout 2> run.stderr
cd /data/home/zhangyifei/fdata/zhangyifei/newmd/newmd
python3 tests/lammps_reference/compare_static.py \
  --gpumd-xyz /tmp/dmgmd-gpumd-long-v2/carbon_crystal/static.xyz \
  --lammps-dump /tmp/dmgmd-lammps-long-v2/smoke/carbon_crystal/seed-0/static/static.lammpstrj \
  --potential /tmp/dmgmd-lammps-long-v2/smoke/carbon_crystal/seed-0/static/nep.gpumd.txt \
  --dmgmd-xyz /tmp/dmgmd-threeway-carbon/static.xyz \
  --report /tmp/dmgmd-threeway-carbon/physics.json
```

本次 dense_water_typewise_cutoff 通过逐元素相同 cutoff 的等价折叠，保留源/目标哈希。
该结果不证明 MatPL 支持非统一 typewise 语法；转换规则见[三引擎标准](../../standards/cross-engine-reference.md)。

## benchmark 编排 smoke

两个结果目录的 `metadata.json` 记录了以下原始命令行（均在加载
`../env/md-mpi.sh` 后从 `newmd` 根目录执行）：

```bash
python3 tests/benchmark/run_benchmark.py --profile smoke --engines lammps \
  --devices 0,1 --ranks 1,2 --output /tmp/dmgmd-lammps-benchmark-smoke \
  --warmup 2 --steps 5
python3 tests/benchmark/run_benchmark.py --profile smoke --devices 0 --ranks 1 \
  --engines gpumd,lammps,dmgmd --backends HostStaged \
  --output /tmp/dmgmd-triengine-smoke
```

- LAMMPS 单引擎 1/2 GPU smoke：2/2 trial `status=ok`，记录在
  `/tmp/dmgmd-lammps-benchmark-smoke-20261009-171358`。
- GPUMD/LAMMPS/DMG-MD 三引擎单 GPU smoke：3/3 trial `status=ok`，
  `HostStaged`，记录在 `/tmp/dmgmd-triengine-smoke-20261009-172316`。
  该 smoke 只有一次重复、正式段五步，且没有固定 CPU/NUMA 绑定，时间只证明
  计时与报告链路可用，不作为三引擎效率排名。

尚待验证：三引擎短程 NVE/长程守恒或统计门槛、正式性能 case 的同势
正确性、固定每 rank CPU 核数与 NUMA 放置后的多 GPU 多次重复矩阵。
本轮没有修改三个 reference checkout。
