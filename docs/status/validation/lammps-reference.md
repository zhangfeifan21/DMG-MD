# LAMMPS 与 GPUMD 静态对照

<a id="lammps-reference-20261009"></a>
## 2026-10-09 参考物理量

当次用锁定 GPUMD 和 LAMMPS/MatPL，从相同初态比较位置、速度（输入提供时）、逐原子能量、力、virial 和总势能。11 个静态对照全部通过对应数值判据。另用 4,096 原子碳核对了 DMG-MD 单卡静态求值。

测试配置见[验收 manifest](../../../tests/lammps_reference/manifest.json)，单位与势转换见[跨引擎标准](../../standards/cross-engine-reference.md)。本节未附原批次完整执行命令。

<a id="l20-lammps-reference-20261120"></a>
## 2026-11-20 LAMMPS/MatPL 单卡检查

当日在 NVIDIA L20 使用 LAMMPS `611ca3b7` 和 MatPL `21fabf34`，构建为 KOKKOS 4.6.2、CUDA/OpenMP、double。27 个构建相关 overlay 文件与仓库清单匹配。八原子碳静态输入通过 `matpl/nep/kk` 完成 `run 0`，启动参数为 `lmp -k on g 1 -sf kk -in in.lmp -log none`。[Overlay 版本](../../../third_party/lammps-matpl-overlay/manifest.json)

<a id="l20-benchmark-reference-gate-20261120"></a>
## 三引擎运行条件

旧 NVE benchmark 核对 GPUMD、LAMMPS 和 MatPL 三个参考 checkout。仅指定已安装的二进制，仍不能满足这一身份检查。短程 golden 使用的 GPUMD 5.9.1 也不同于旧 benchmark 锁定的 `9d23496e`。

三引擎动力学和正式效率矩阵仍待验收。实验室迁移另外使用 GPUMD-WPE 单卡作为性能基线。[测量方法](../performance/npt-benchmarks.md)
