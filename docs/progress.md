# DMG-MD 实现进度

更新日期：2026-09-02。

## 当前结论

新的 `dmg-md` executable 的 single-rank 阶段已经完成，并通过锁定 GPUMD golden 的
全量 differential comparison。当前明确不包含空间域分解、halo/MPI 通信或多-rank 力计算。

## 已完成

- CMake 生成 `build/dmg-md`，并校验 GPUMD reference commit 必须是
  `9d23496e41319b9e2af5221a7df6285387401d1e`。
- 从只读 GPUMD 源树直接编译 tokenizer、Box、NEP loader、neighbor、NEP/NEP-ZBL CUDA
  kernels 和 many-body Potential 核心；newmd 中没有第二份 NEP 数学或参数布局。
- `model.xyz` parser 支持 PBC、9 分量 Lattice、扩展 Properties、未知 property 宽度、
  species/type、默认/显式 mass、charge、velocity、group，并保持 GPUMD 单位转换。
- `run.in` 先解析为带源文件/行号的 typed command IR，再由 runtime 顺序执行。注释、
  token 上限、默认 1 fs timestep、NVE、`nvt_ber`、多段 run 和 measurement 清理语义已落地。
- 所有已知未支持命令和未支持 ensemble subtype 在 GPU 初始化前 fail fast；错误包含命令、
  输入文件、行号和原始行。没有增加 MPI 专用 run.in 语法。
- host/device Atom 数据从第一版起显式区分 `global_count`、`owned_count`、`ghost_count`、
  `local_count` 和 `global_id`。所有 SoA 以 local count 为 stride；VV、thermo 和输出只遍历
  owned atoms。
- single-rank NEP adapter 明确要求 `ghost_count == 0`；GPUMD NEP 工作区以 local count
  构造，中心域为 owned 前缀。非零 ghost 会直接失败，不会被误当成完整全局数组。
- 完成 velocity-Verlet NVE、Berendsen NVT、owned-only thermo、`dump_thermo`、
  `dump_xyz` 和 `dump_restart`。输出按 `global_id` 排序，保持 GPUMD 文件名、header、列序、
  precision、单位、append/overwrite 和多段 run 行为。
- 删除未再使用的早期 `include/newmd` DeviceBuffer/Atom/正交 Box/PBC/CPU CSR 代码及测试，
  避免仓库同时保留两套会独立演化的数据模型和邻居/PBC 语义。

## 验证结果

构建与 CPU tests：

```text
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
ctest --test-dir build --output-on-failure
2/2 tests passed
```

全新 build 目录也使用上述标准命令验证通过；项目默认覆盖 CUDA 架构
`75;80;86;89;90`，调用方仍可通过 `CMAKE_CUDA_ARCHITECTURES` 覆盖。

锁定 GPUMD baseline：

```text
python3 tests/baseline/run_baselines.py --reference ../gpumd-reference/src/gpumd --device 0
PASS: all 4 baseline cases match for pinned GPUMD reference
```

DMG-MD differential：

```text
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
PASS: all 4 baseline cases match for candidate
```

覆盖 `single_small_static`、`single_large_nve`、`multi_nvt_restart` 和
`nep_zbl_boundary`，共 5 个独立进程。energy、force、position、temperature、restart 字段
均为 0 差；最大非零差为 velocity `8.674e-19`、virial `1.776e-15`、XYZ stress
`6.353e-22`，均远低于已经校准的 golden 容差。

## 下一阶段门槛

当前不得进入多-rank 物理实现，除非 single-rank golden 持续保持通过。下一阶段若获批准，
应从 replicated MPI lifecycle/device binding 开始，再单独实现 domain decomposition、stable-ID
migration、position/type halo、NEP intermediate exchange、owned-only global reductions 和 rank-0
gather；不得直接放宽 `NepForce` 的 ghost 检查。
