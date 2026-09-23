# Docker 适配验证（2026-09-23）

类别：带日期的验证记录。源码基线 `424433ff7e0d355c09629a88f9d003c0264984a2` 加本次
Docker/构建检查改动（未提交）；GPUMD reference checkout 未修改。

## 环境与结论

- 原生宿主：Ubuntu 24.04.2、RTX 4090、驱动 580.173.02；GCC 11.4.0、CMake 3.28.3、
  Python 3.12.3、CUDA 12.9.86、Open MPI 5.0.10、UCX 1.22.0。
- 独立编译检查：同一主机另装的 CUDA 12.8.61（12.8 GA），G++ 11，全部五种默认 GPU 架构。
- Docker 目标依赖：Ubuntu 22.04、CUDA 12.8.0、GCC 11、CMake 3.28.4、Open MPI 5.0.10、
  UCX 1.18.1；选择依据和部署边界见 [README](../../README.md#docker-部署推荐跨服务器使用)。

| 检查 | 结果 | 能证明的范围 |
| --- | --- | --- |
| Shell 语法、diff 空白检查 | 通过 | Docker 环境脚本/entrypoint 静态检查 |
| UCX 官方 1.18.1 源码包 | 下载并核验 SHA-256；确认 configure 参数存在 | 归档身份与参数，不代表镜像内构建已完成 |
| 原生 CMake configure / build | 通过 | 新 CUDA 路径检查兼容现有工具链 |
| CPU/分析 CTest | 6/6 通过 | 原有解析、布局、分析与 benchmark 编排回归 |
| 4 rank MPI 环境门槛 | 通过 | 原生 Open MPI/UCX 的 CUDA transport、唯一 GPU 绑定和 device-pointer 自检 |
| 单 rank committed golden | 4/4 case 通过 | 原生程序保持既有数值容差 |
| CUDA 邻居 CTest | 1/1 通过 | 原生邻居测试 |
| CUDA 12.8 独立对象编译 | 8/8 通过 | 源文件与 12.8 编译器/头文件兼容，含 `sm_75`；未验证新 MPI 库的链接/运行 |
| 混用 CUDA 12.8 编译器和 12.9 环境 | 按预期拒绝 | 防止旧缓存/路径误选 |
| PMIx 路径分支（mock 检查） | 4/4 通过 | 未覆盖或有效目录允许继续，空字符串/不存在目录拒绝 |
| 完整 Docker build / 容器 GPU 测试 | **未执行** | daemon 权限不足；不能称新依赖组合已实测通过 |
| Ubuntu 22.04 宿主 / Tesla T4 | **未实测** | 仅依据上游支持范围和 `sm_75` 编译检查选择 |

## 实际命令

以下命令从仓库根目录执行。GPU 命令按 AGENTS.md 要求在沙箱外执行，仍加载同一环境入口：

```bash
source ../env/md-mpi.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
ctest --test-dir build --output-on-failure -E domain_neighbor_cuda
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
ctest --test-dir build --output-on-failure -R domain_neighbor_cuda
```

CUDA 12.8 检查执行 `source ../env/md-mpi.sh && python3 /tmp/dmgmd-check-cuda128.py`。
该次临时脚本从 `build/compile_commands.json` 提取全部 nvcc 命令，展开 include response file，
将编译器和 CUDA 头文件路径替换为 `/usr/local/cuda-12.8`，显式使用 `-ccbin /usr/bin/g++-11`，
保留 Release/C++17、五种架构及 RDC 参数；对象输出重定向到 `/tmp/dmgmd-cuda128-compile`。
该目录的 `commands.json` 和逐文件 `.log` 是此次临时证据，未修改正式 build 的对象文件。

拒绝混合 CUDA 的负向测试：

```bash
source ../env/md-mpi.sh
cmake -S . -B /tmp/dmgmd-mixed-cuda-negative \
  -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.8/bin/nvcc \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-11 -DCMAKE_CUDA_ARCHITECTURES=75
```

该命令预期失败，实际输出 `CUDA compiler/toolkit must come from CUDA_HOME`。

## 未完成的容器验收

沙箱外 `docker version` 显示客户端 24.0.9，但连接 `/var/run/docker.sock` 返回
`permission denied`；`sudo -n docker version` 返回 `a password is required`。
这是宿主机账号权限限制，并非自动审批拒绝；未修改 Docker 服务、组权限或宿主机工具链。

具备 Docker 权限后，执行 README 的构建命令和单/多 GPU 验收命令。必须先通过新镜像内的
环境门槛，才能把新 Open MPI/UCX 组合称为运行兼容；还应在目标 T4 和 Ubuntu 22.04 服务器
上保留实际结果。现有原生通过记录不代替新镜像、T4、跨节点或性能验收。

## 2026-09-23：Ubuntu 22.04 / Python 3.10 构建测试修正

用户在 Docker 构建的 `dmgmd.long_nve_analysis` 中报告碳模型哈希失败。原代码在
`velocities()` 中用内置 `sum()` 计算质心；同一源码在本机 Python 3.10.6 得到用户报告的
`904ac9df...`（base）和 `5eccc7d1...`（nightly），在 Python 3.12.3 得到 manifest 锁定的
`e5e3ae72...` 和 `59dcd7aa...`。根因是 Python 3.12 的浮点 `sum()` 行为发生变化，
见 [Python 官方文档](https://docs.python.org/3.12/library/functions.html#sum)。

该函数改用 `math.fsum()` 计算总质量与加权速度，保留原有模型哈希和势函数数据。
在原生 Ubuntu 24.04.2 主机上执行：

```bash
/usr/bin/python3.10 tests/long_nve/test_long_nve.py
/usr/bin/python3.12 tests/long_nve/test_long_nve.py
```

两种解释器均为 23/23 通过。全部 90 个锁定哈希另按下面的命令逐一核对：

```bash
for md_python in /usr/bin/python3.10 /usr/bin/python3.12; do "$md_python" - <<'PY'
import sys
sys.path.insert(0, 'tests/long_nve')
import long_nve_common as common
manifest = common.load_manifest()
for profile in ('base', 'nightly', 'release'):
    for name in ('carbon_crystal', 'dense_water', 'batio3_zbl'):
        case = (manifest['cases'][name] if profile == 'base' else
                common.profile_case(manifest, profile, name))
        for seed in range(10):
            common.validate_generated_model(case, seed, common.generate_model(case, seed))
print('PASS', sys.version.split()[0], '90 locked model hashes')
PY
done
```

两种解释器均为 90/90 通过。尚未在用户的 Docker 环境中重新构建镜像或运行 GPU 验收。

## 2026-09-23：运行时解析器测试的临时文件权限

用户报告镜像构建成功、单卡 `check_environment.py` 自检通过，但容器内完整 CTest 的
`dmgmd.run_parser` 失败，报 `unsupported command 'dftd3'`；其余 6 项 CTest 通过。
`run_parser_tests.cpp` 原来固定使用 `/tmp/dmgmd-run-parser-test.in`。构建阶段 root 运行
CTest 后留下这个文件，内容是第二个解析器测试的 `dftd3` 命令；镜像运行阶段使用普通用户，
无法覆盖 root 拥有的旧文件，第一个解析器测试遂读到旧内容。这是测试的临时文件问题，
不是 `dftd3` 被错误接受或实际算例需要支持该命令。

在当前原生环境中，用不可写的旧文件复现了完全相同的失败输出；改为每次用 `mkstemp`
创建唯一文件、验证写入并在测试后清理。执行
`cmake --build build --target dmgmd_run_parser_tests -j4` 和
`ctest --test-dir build --output-on-failure -R '^dmgmd.run_parser$'`，结果通过；同一不可写
旧文件仍存在时，新测试也通过且没有留下新文件。用户的镜像仍需同步源码、重新构建后验收。
