# LAMMPS 与 GPUMD 静态对照

<a id="lammps-reference-20261009"></a>
## 2026-10-09 参考物理量

当次用锁定 GPUMD 和 LAMMPS/MatPL，从相同初态比较位置、速度（输入提供时）、逐原子能量、力、virial 和总势能。11 个静态对照全部通过对应数值判据。另用 4,096 原子碳核对了 DMG-MD 单卡静态求值。

测试配置见[验收 manifest](../../../tests/lammps_reference/manifest.json)，单位与势转换见[跨引擎标准](../../standards/cross-engine-reference.md)。本节未附原批次完整执行命令。

<a id="l20-lammps-reference-20261120"></a>
## 2026-11-20 LAMMPS/MatPL 单卡检查

当日在 NVIDIA L20 使用 LAMMPS `611ca3b7` 和 MatPL `21fabf34`，构建为 KOKKOS 4.6.2、CUDA/OpenMP、double。27 个构建相关 overlay 文件与仓库清单匹配。八原子碳静态输入通过 `matpl/nep/kk` 完成 `run 0`，启动参数为 `lmp -k on g 1 -sf kk -in in.lmp -log none`。[Overlay 版本](../../../third_party/lammps-matpl-overlay/manifest.json)

<a id="l20-benchmark-reference-gate-20261120"></a>
## 2026-11-20 NVE 计时入口检查

当日的 NVE 脚本要求核对外部源码提交。普通 GPUMD 的提交与旧配置不同，单选 LAMMPS 时也缺少当时要求的源码目录。两次检查都在计时前退出。

当次没有得到动力学计时。按 2026-11-23 口径，LAMMPS/KOKKOS/NEP 还须作为同卡数对标纳入三材料 NPT，逐格核验控压可比性。普通 GPUMD 的同卡数比较和 WPE 固定单卡比较各自报告。[测量方法](../performance/npt-benchmarks.md#performance-matrix)

<a id="l20-installed-nve-20261123"></a>
## 2026-11-23 已安装引擎的短程 NVE 计时

在单节点 4 × NVIDIA L20 上，DMG-MD `b103010` 工作树的计时脚本直接运行已安装的引擎。普通 GPUMD 5.9.1、LAMMPS 22 Jul 2025 Update 4（MatPL NEP、KOKKOS 4.6.2）及 DMG-MD 分别使用一张和两张卡。GPUMD-WPE（宿主 `a0e55bc`、Wisevolve 1.0.0）仅使用一张卡。7 个作业全部返回 `ok`。各作业使用 49,152 原子的 `carbon_smoke`，预热 2 步、计时 5 步，DMG-MD 采用 HostStaged。

以下变量分别指向当次普通 GPUMD、GPUMD-WPE 和 LAMMPS 的可执行文件，输出前缀可自行选择。

```bash
python3 tests/benchmark/run_benchmark.py --profile smoke --engines gpumd,wpe,lammps,dmgmd \
  --candidate ./build/dmg-md --reference "$GPUMD_BIN" --wpe-bin "$WPE_BIN" \
  --lammps-bin "$LAMMPS_BIN" --devices 0,1,2,3 --ranks 1,2 --backends HostStaged \
  --output "$WORK_ROOT/nve"
```

这次检查覆盖两段 NVE 输入、计时字段、实际后端和热力学输出。三材料 NPT 的正式配对耗时仍待测量。[已测构建](../../standards/cross-engine-reference.md#benchmark-engines) · [NPT 对标矩阵](../performance/npt-benchmarks.md#performance-matrix)
