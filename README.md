# DMG-MD

DMG-MD 是面向多节点、多 GPU 经典分子动力学的 runtime。当前仓库已完成第一阶段：一个与
锁定 GPUMD reference 兼容的 single-rank、单 GPU `dmg-md` executable。

当前版本直接读取 GPUMD 格式的 `model.xyz`、NEP/NEP-ZBL potential 和 `run.in`。它复用
GPUMD 的 tokenizer、Box、NEP loader、邻居构建及 CUDA kernels，没有维护第二份 NEP 数学。
空间域分解、MPI、halo 通信和多-rank 力计算尚未实现。

## 当前支持范围

- NEP4、NEP5 及对应的 NEP-ZBL potential；
- `potential`、`velocity`、`time_step`；
- `ensemble nve` 和 `ensemble nvt_ber`；
- `correct_velocity`；
- `dump_thermo`、`dump_xyz`、`dump_restart`；
- `run` 及多段 run。

`run.in` 会先完整解析为带文件和行号的 command IR，再开始执行。未知命令、已知但尚未支持
的命令和 ensemble subtype 都会立即失败，不会被静默忽略。

## 依赖

- CMake 3.24 或更高版本；
- 支持 C++17 的 host compiler；
- CUDA Toolkit 和支持的 NVIDIA GPU；
- 位于 `../gpumd-reference` 的只读 GPUMD checkout，commit 必须为
  `9d23496e41319b9e2af5221a7df6285387401d1e`。

如 reference 位于其他目录，可配置 `-DGPUMD_SOURCE_DIR=/absolute/path/to/gpumd`；该 checkout
仍必须处于上述锁定 commit。

## 构建与测试

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
```

项目默认构建 CUDA 架构 `75;80;86;89;90`，可通过
`-DCMAKE_CUDA_ARCHITECTURES=<architecture>` 覆盖。

## 运行

在同时包含 `run.in` 和 `model.xyz` 的工作目录执行：

```bash
/absolute/path/to/newmd/build/dmg-md
```

potential 文件路径按 `run.in` 中的 `potential` 命令解释。输出文件名、列顺序、默认值和单位
由 GPUMD golden tests 锁定。

## Golden differential

先验证原 GPUMD baseline，再比较 `dmg-md`：

```bash
python3 tests/baseline/run_baselines.py \
  --reference ../gpumd-reference/src/gpumd --device 0
python3 tests/baseline/run_baselines.py \
  --candidate ./build/dmg-md --device 0
```

当前四组 cases 全部通过。详细结果见 [docs/progress.md](docs/progress.md) 和
[docs/baseline-results.md](docs/baseline-results.md)。

## VS Code / clangd

测试源码依赖 CMake target 提供的 C++17、include 路径和 compile definitions。请先运行一次
CMake configure，并让编辑器读取：

```text
build/compile_commands.json
```

使用 Microsoft C/C++ extension 时，可将 `C_Cpp.default.compileCommands` 指向
`${workspaceFolder}/build/compile_commands.json`；使用 clangd 时可设置
`--compile-commands-dir=build`。请把包含本 README 和顶层 `CMakeLists.txt` 的目录作为 VS Code
workspace root；如果打开的是它的父目录，compile database 路径需要相应写成
`newmd/build/compile_commands.json`。

`tests/model_parser_tests.cpp` 中的 `DMGMD_SOURCE_DIR` 是
`dmgmd_model_parser_tests` target 专属的 compile definition。如果编辑器没有加载上述 compile
database，它会错误地认为该宏未定义，并且可能找不到 `dmgmd/model.hpp`；CMake 构建本身不会
出现这个问题。

## 文档

- [当前进度与验证结果](docs/progress.md)
- [架构决策](docs/decisions.md)
- [当前数据布局](docs/data-layout.md)
- [输入兼容矩阵](docs/compatibility-matrix.md)
- [Golden test 说明](tests/baseline/README.md)
