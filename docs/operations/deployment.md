# 用普通账号构建和运行 DMG-MD

本文从配置已有依赖开始，完成编译、自检和一个可核对输出的单卡作业。

节点须提供 NVIDIA 驱动、CUDA Toolkit、CUDA-aware Open MPI、UCX、C++17 编译器和 CMake ≥ 3.24。使用分配给你的 GPU，在自己的目录构建和运行。

## 1. 选择同一套编译和运行环境

在登录或作业 shell 加载站点的 CUDA、Open MPI 和 UCX，使下面四个程序可见。站点发布的环境脚本如果已经提供相同配置，可直接使用。记录模块名，在每个计算作业中加载同一套环境。

```bash
command -v nvcc mpirun mpicxx ucx_info
nvidia-smi --query-gpu=name,compute_cap --format=csv
prefix_of() { dirname "$(dirname "$(readlink -f "$(command -v "$1")")")"; }
export CUDA_HOME="$(prefix_of nvcc)"
export OMPI_HOME="$(prefix_of mpirun)"
export UCX_HOME="$(prefix_of ucx_info)"
export PATH="$OMPI_HOME/bin:$UCX_HOME/bin:$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$OMPI_HOME/lib:$UCX_HOME/lib:$CUDA_HOME/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export OMPI_MCA_pml=ucx OMPI_MCA_coll='^hcoll'
export OMPI_MCA_mca_base_component_path="$OMPI_HOME/lib/openmpi"
export UCX_WARN_UNUSED_ENV_VARS=n
ompi_info --config
ucx_info -v
```

上述命令从可执行文件位置推导安装前缀。站点包装程序或模块若采用另一目录，改为显式设置三个前缀。CUDA 和 UCX 路径参考 `ompi_info --config` 的 `--with-cuda`、`--with-ucx`，Open MPI 路径取其实际安装前缀。

CMake 会核对 CUDA 编译器、MPI 包装器、启动器和 UCX 的实际路径。PMIx 组件路径由站点安装决定。

## 2. 克隆、编译、自检

```bash
git clone https://github.com/zhangfeifan21/DMG-MD.git
cd DMG-MD
repo=$PWD
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DCMAKE_CUDA_COMPILER="$CUDA_HOME/bin/nvcc" \
  -DMPI_CXX_COMPILER="$OMPI_HOME/bin/mpicxx" -DMPIEXEC_EXECUTABLE="$OMPI_HOME/bin/mpiexec"
cmake --build build --parallel 8
ctest --test-dir build --output-on-failure
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0 --ranks 1
```

示例中的 `89` 对应计算能力 8.9，设备编号换成实际分配的 GPU。检查 CTest 结果和跳过项；设备自检会报告 rank、GPU 身份、通信后端和 CUDA 缓冲通信结果。

更换工具链时使用新的构建目录。运行用户输入只需本仓库及上述依赖；维护者更新数值 golden 时使用该套清单登记的参考程序。性能测试直接使用站点安装的引擎。[L20 已测版本](../standards/cross-engine-reference.md#benchmark-engines)

## 3. 运行一个能核对输出的单卡作业

在上一步的 shell 继续执行。示例使用仓库附带的小型碳结构，先计算初始力，再做 6 步 NVE。每次运行使用新的作业目录，避免输出追加到上一次结果。

```bash
app="$repo/build/dmg-md"
work=$(mktemp -d "$PWD/carbon-run.XXXXXX")
cp "$repo/tests/baseline/inputs/single_large_nve/"{model.xyz,run.in} "$work/"
cp "$repo/tests/baseline/inputs/potentials/nep_C.txt" "$work/nep.txt"
cd "$work"
CUDA_VISIBLE_DEVICES=0 mpiexec -n 1 "$app" > run.log 2>&1
awk '!/^#/ && NF {n++} END {print n}' thermo.out
ls initial.xyz trajectory.xyz restart.xyz
```

预期有 7 行 thermo 数值数据，并生成初始力、轨迹和重启文件。自己的作业同样在包含 `model.xyz`、`run.in` 和输入所指定势文件的目录中启动。

## 4. 检查多卡实验作业的实际分区

在已获分配的单节点四卡上，先在仓库执行设备自检。在具备条带宽度的各向同性正交全周期 NEP 作业目录运行下一条命令。上面的八原子示例只用于单卡自检。

```bash
(cd "$repo" && python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4)
# 在含大盒 NPT 输入的作业目录执行
CUDA_VISIBLE_DEVICES=0,1,2,3 DMGMD_DOMAIN_NPT=affine DMGMD_COMM_BACKEND=HostStaged \
  mpiexec -n 4 "$app" > run.log 2>&1
grep -E '^DMGMD_DOMAIN(_NPT)? ' run.log
```

实际局部 NPT 需要日志同时包含 `DMGMD_DOMAIN mode=m2a` 和 `DMGMD_DOMAIN_NPT mode=affine`。若看到 `m1-fallback`，此次作业选择了全量复制。改用单卡，或减少进程后重新检查分区。

不显式开启局部 NPT 时，默认路径打印 `mode=m1 reason=npt-m1-only`。待实现的生产入口将让不合格的多卡请求在积分和科学输出前一致退出，给出单卡重跑命令；只有更少卡数已通过检查时才推荐该卡数。[实现导读](../reference/multi-gpu-implementation.md) · [试用状态](../status/performance/npt-benchmarks.md)

## 构建或运行失败时

- CMake 报工具链不匹配，查 `command -v`、三个前缀和 `ompi_info --config`，统一后使用新的构建目录。
- MPI 的设备探针失败，先核对分配到的 GPU、每节点进程数及站点的 CUDA/UCX 支持，再按探针错误检查设备缓冲通信。[MPI 环境合同](../standards/mpi-runtime.md)
- 数值回归报告参考身份不符时，查该套回归的 manifest。性能计时找不到程序时，指定已安装的可执行文件或加载对应环境。核心程序和上面的用户示例只链接仓库内代码及系统工具链。[数值验证](../../tests/baseline/README.md) · [参考引擎合同](../standards/cross-engine-reference.md)
