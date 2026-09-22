# 在其他服务器部署 DMG-MD 与 GPUMD

类别：操作手册。目标：单节点 NVIDIA GPU 服务器，重建同一源码与工具链后执行正确性门槛和
benchmark。本手册不把尚未硬化的多节点运行当作已验证能力。

## 推荐交付内容

保持如下兄弟目录关系，可放在任意用户可写路径：

```text
md-stack/
  env/md-mpi.sh
  newmd/                 # DMG-MD 源码、tests、势文件、脚本和文档
  gpumd-reference/       # GPUMD 锁定 checkout，只读对照
```

复制源码和势，不搬旧 build、CMakeCache、CUDA 对象文件。保留 `.git` 以记录 revision；本次新增
benchmark 文件尚未提交时，要用工作树复制，单用 `git archive HEAD` 会遗漏它们。

源服务器示例（把 host 和目标路径换成实际值；不会删除目标已有文件）：

```bash
# 在包含 newmd/ 和 gpumd-reference/ 的父目录执行
rsync -a --exclude='build/' --exclude='build-*/' --exclude='dmgmd-*/' \
  --exclude='__pycache__/' --exclude='*.pyc' newmd/ host:~/md-stack/newmd/
rsync -a --exclude='build/' --exclude='src/gpumd' --exclude='src/nep' \
  --exclude='src/gnep' --exclude='*.o' gpumd-reference/ host:~/md-stack/gpumd-reference/
```

也可以在目标机克隆自己的 DMG-MD 仓库，并从官方仓库取得 reference：

```bash
cd ~/md-stack
git clone https://github.com/brucefan1983/GPUMD.git gpumd-reference
git -C gpumd-reference checkout --detach 9d23496e41319b9e2af5221a7df6285387401d1e
```

不要用最新 master 替代锁定版本，否则数值、日志格式和多卡算法变化会混入对比。保持源 checkout
干净，在 DMG-MD 目录下进行 GPUMD 的 out-of-source 构建。

## 选择工具链

原服务器观测基线：CUDA 12.9.86、GCC 11.4、Open MPI 5.0.10、UCX 1.22.0、RTX 4090。
这是一组已存在的环境，不宣称是唯一兼容组合；新机变化必须重新过门槛。需要 CMake ≥3.24、
Python ≥3.10、NVIDIA 驱动和匹配的 CUDA Toolkit、支持 C++17 的编译器。GPUMD 还链接 CUDA 的
cuBLAS/cuSOLVER/cuFFT；DMG-MD 必须使用带 CUDA 的 Open MPI+UCX。

最快路径是复用目标机已有且可验证的 CUDA-aware Open MPI/UCX 安装（如集群 modules），填写
真实安装路径。CMake 会检查 `ompi_info --config` 中 CUDA/UCX 路径与环境一致；仅有系统
`mpirun` 或容器中的可执行文件并不足够。不要因门槛失败改用另一 MPI 实现或绕过自检。

缺少工具链时，在用户目录编译 UCX，再编译 Open MPI。发行版管理员准备编译依赖（Ubuntu
示例：build-essential、gfortran、pkg-config、libevent-dev、libhwloc-dev、libpmix-dev；有 IB 时
增加 libibverbs-dev、librdmacm-dev）。CMake/Python 版本和 NVIDIA 驱动由目标机管理员确认。
从 UCX/Open MPI 官方发行包或原服务器保留的源码包取对应版本，并记录包 SHA-256。

在解压好的源码目录分别执行：

```bash
# 每个新 shell 重设；务必在 configure 与运行时使用相同规范路径
export CUDA_HOME=/usr/local/cuda
export UCX_HOME="$HOME/software/ucx-cuda"
export OMPI_HOME="$HOME/software/openmpi-cuda"

# 在 UCX release 源码根目录
./configure --prefix="$UCX_HOME" --libdir="$UCX_HOME/lib" --with-cuda="$CUDA_HOME"
make -j8
make install

# 在 Open MPI release 源码根目录；系统依赖由 configure 检测
export PATH="$UCX_HOME/bin:$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$UCX_HOME/lib:$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
./configure --prefix="$OMPI_HOME" --libdir="$OMPI_HOME/lib" \
  --with-cuda="$CUDA_HOME" --with-ucx="$UCX_HOME"
make -j8
make install
```

有 IB/RDMA 时 UCX 可显式加 `--with-verbs --with-rdmacm` 并安装对应开发包；单节点 PCIe
测试不以 RDMA 为先决条件。不要把 CUDA `stubs` 放进运行时 LD_LIBRARY_PATH。
UCX 和 Open MPI 都需启用 CUDA；构建与能力检测依据
[Open MPI 5 CUDA 文档](https://docs.open-mpi.org/en/v5.0.x/tuning-apps/networking/cuda.html)。

Open MPI 的 PMIx、hwloc、libevent 必须避免加载成不一致版本，参见
[Open MPI 依赖库说明](https://docs.open-mpi.org/en/v5.0.x/installing-open-mpi/required-support-libraries.html)。
本项目现有预检要求显式且有效的 PMIx component path。外部 PMIx 与 Open MPI 内置 PMIx 的
安装位置不同，**不能原样复制原服务器的 `/usr/lib/.../pmix2/lib/pmix`**。

## 唯一环境入口

```bash
cd ~/md-stack/newmd
mkdir -p ../env
# 仅首次复制，若已有环境入口则先检查并按新服务器路径编辑
cp -n scripts/md-mpi.sh.example ../env/md-mpi.sh
```

编辑 `../env/md-mpi.sh` 的 CUDA_HOME、UCX_HOME、OMPI_HOME、
PMIX_MCA_mca_base_component_path 四项。先用 `ldd "$OMPI_HOME/lib/libmpi.so"` 看实际 PMIx
依赖；用 `rg --files /usr/lib "$OMPI_HOME/lib" | rg '/(pmix|mca_pmix)'` 查找组件目录，或查询
目标机 PMIx 安装清单。组件路径应属于该 libpmix 安装，而不是 Open MPI 的 `lib/openmpi`。
模板检测目录存在；安装选择和功能是否匹配最终仍由预检确认。

```bash
source ../env/md-mpi.sh
command -v nvcc mpiexec mpicxx ompi_info ucx_info
ompi_info --config
ompi_info --parsable --all | rg 'mpi_built_with_cuda_support:value:true|mca:pml:ucx:'
ucx_info -v
ucx_info -d | rg 'cuda_copy|cuda_ipc'
nvidia-smi
nvidia-smi topo -m
```

每个 tmux、SSH、batch job 的新 shell 都 source 同一个脚本；不要叠加系统 MPI 或其他 UCX。
模板保留原项目的 UCX PML、HCOLL 排除和各自 MCA 路径。

## 构建与门槛

```bash
cd ~/md-stack/newmd
# 第一参数为 CUDA 架构；4090 用 89；第二参数为编译并行度
bash scripts/build_benchmark.sh 89 8
```

该脚本 source 环境入口、校验 GPUMD commit/工作树，分别创建 `build-benchmark` 和
`build-benchmark-gpumd`，双方都是 Release、相同架构；不改参考源码、不覆盖旧 build。
环境入口不在默认位置时可设置 `MD_ENV_FILE`。更换工具链/架构时用新 build 目录或手动独立
配置，避免复用旧 CMakeCache。A100/H100 等应根据目标 GPU 与 Toolkit 支持设置对应架构；
不要将 4090 的 sm_89 二进制当作通用二进制搬走。

```bash
source ../env/md-mpi.sh
# 先 CPU 测试（不启动 CUDA 测试）
ctest --test-dir build-benchmark --output-on-failure -E domain_neighbor_cuda

# devices 换成实际可用设备；此门槛会真的启动 GPU/MPI 自检
python3 tests/mpi/check_environment.py --candidate ./build-benchmark/dmg-md --devices 0,1

# 单卡 golden 与 MPI 正确性测试；新服务器上先做这一步，再评估性能
python3 tests/baseline/run_baselines.py --candidate ./build-benchmark/dmg-md --device 0
python3 tests/baseline/run_baselines.py --reference ./build-benchmark-gpumd/gpumd --device 0
python3 tests/mpi/run_mpi_domain.py --candidate ./build-benchmark/dmg-md --devices 0,1,2,3

# 短性能编排检查
python3 tests/benchmark/run_benchmark.py --profile smoke --devices 0,1 --ranks 1,2 \
  --candidate ./build-benchmark/dmg-md --reference ./build-benchmark-gpumd/gpumd \
  --output dmgmd-deployment-smoke
```

MPI domain 验收使用的 4 卡必须均已分配且空闲；少卡机器应按该脚本 `--help` 调整 ranks。
正式性能前还应跑相应 long_nve nightly；方法、完整大规模命令见
[benchmark 操作说明](../../tests/benchmark/README.md)。性能运行器自动重复 MPI 环境门槛。

## 调度与迁移验收清单

建议向调度器申请一个独占节点，GPU 数覆盖所测最大 P，主机内存覆盖模型生成及两者初始化。
GPUMD 始终一个进程；不要 `srun -n8 gpumd`。DMG-MD 仍由已选择的 Open MPI 启动 P ranks。
在 Slurm 分配的 shell 内运行 benchmark 时，`--devices` 传分配给你的完整 GPU UUID；脚本不会
自动推断调度器分配，不要默认请求宿主机全部 0–7。当前运行器是单节点，不传 hostfile。

保留：源码 revision 与 dirty diff、源码包 SHA-256、configure/build 日志、CMakeCache、
二进制哈希、CPU/GPU/驱动/拓扑、环境脚本、正确性报告、benchmark 全目录。
新机验收完成标准是“环境自检 → 单卡数值 → 多卡数值 → benchmark smoke → pilot → 正式矩阵”，
不能用编译成功替代运行验证。将不同服务器的曲线分别标注硬件和功率配置，不能混合求 speedup。

可选容器适合统一用户空间，但仍依赖宿主驱动、GPU 分配与 IPC/共享内存权限；当前没有经过
验收的容器镜像，本手册优先提供原生用户目录部署，不把容器命令列为已验证捷径。
