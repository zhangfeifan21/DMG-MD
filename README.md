# DMG-MD

DMG-MD 用于使用 NEP 势函数进行经典分子动力学计算，可以读取 GPUMD 格式的
`model.xyz`（原子结构）、`run.in`（计算设置）和 NEP 势文件。

**第一次使用，请按下面的第 1～6 步操作。** Docker 可以理解为装好程序和配套软件的
“计算环境包”：准备好服务器后，一条命令就能调用它进行计算，无需逐个安装 CUDA、MPI 等依赖。

## Docker 部署（推荐跨服务器使用）

本指南面向 Ubuntu 22.04 / 24.04、Intel/AMD 64 位 CPU、NVIDIA 显卡服务器，包含 Tesla T4。
示例先使用一张显卡；多卡操作放在后面。新镜像和 T4 实机仍待验收，
[当前验证记录](docs/status/docker-validation.md) 如实列出了已完成与未完成的检查。

### 开始前：这些命令在哪里输入？

在**要运行计算的服务器终端**中输入；如果通过 SSH 登录服务器，就在登录后的窗口执行。
不要在自己电脑的终端中安装服务器软件。

- 按顺序复制每个代码框的全部内容，粘贴后按回车；代码框中的 `#` 开头行是说明。
- 多行命令末尾的 `\` 表示“下一行接着这一行”，复制时保留它。
- `sudo` 表示使用管理员权限。提示密码时输入服务器登录密码；输入时不显示字符是正常的。
- **任意一步出现报错，先解决该步，再继续。** 下方有常见问题表。

如果你使用课题组共享服务器或超算，请先向管理员确认允许使用 Docker，并分配一张显卡。
下面示例使用编号 **0** 的显卡；若分配的是其他编号，把命令中的 `device=0` 改成实际编号。

### 第 1 步：检查服务器是否已经准备好（每台服务器一次）

先复制：

```bash
nvidia-smi
```

**成功标志：** 出现显卡信息表，能看到显卡名称和 `Driver Version`。本方案要求驱动版本
至少为 **570.26**，例如 570.26、570.124.06、580.x 均满足版本门槛。
如果命令不存在、看不到显卡或驱动更旧，请先联系管理员处理，不必自行安装 CUDA。

然后复制以下命令，检查 Docker 能否使用显卡。第一次可能需要等待下载：

```bash
sudo docker run --rm --gpus '"device=0"' nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

**成功标志：** 再次出现显卡信息表。成功后直接进入第 2 步。

如果出现 `Unable to find image` 后又报告连接 `registry-1.docker.io` 失败，这是 Docker
服务器无法从 Docker Hub 拉取这个测试镜像。你已有第一台服务器构建的 `dmgmd:cuda12.8`
镜像时，不必从 Docker Hub 下载这个测试镜像：按后面的[镜像搬运步骤](#可选搬到另一台服务器省去重新编译)
把 DMG-MD 镜像复制到 T4 服务器并导入，然后用它检查 GPU：

```bash
sudo docker run --rm --gpus '"device=0"' dmgmd:cuda12.8 nvidia-smi
```

看到 T4 显卡信息表即表示 Docker 可把显卡交给容器。之后直接跳到第 4 步检查 DMG-MD，
**不要在 T4 上执行第 3 步的 `docker build`**。你已经有可运行的镜像，直接复制第 4 步
那条以 `sudo docker run` 开头的自检命令。若提示 `could not select device driver` 或
`could not select device driver with capabilities: [[gpu]]`，请管理员在 T4 服务器安装并配置
NVIDIA Container Toolkit；Docker Hub 的网络问题与显卡运行时配置是两项独立检查。

如果需要直接从 Docker Hub 下载镜像，服务器管理员还须检查 Docker 服务自己的出网权限或代理。
终端里的 `curl` 能联网，不一定表示 Docker 服务也能联网；参见[Docker 官方代理说明](https://docs.docker.com/engine/daemon/proxy/)。
给 `docker run` 加 `--network=host` 不会修复镜像拉取，因为拉取由 Docker 服务完成。

若 `sudo` 同时显示 `unable to resolve host <主机名>`，这是服务器主机名没有正确写入
`/etc/hosts`。该提示本身没有阻止 `sudo` 执行；请管理员核对 `/etc/hostname` 和 `/etc/hosts`，
确保 `/etc/hosts` 中有一行 `127.0.1.1 <主机名>`，其中名称与 `/etc/hostname` 完全一致。
Docker Hub 的连接失败才是上面镜像下载中断的原因。

如果失败，把下面这段话连同报错发给服务器管理员：

> 我需要运行 DMG-MD 的 Docker 计算环境，使用 CUDA 12.8。请确认 NVIDIA 驱动 ≥570.26，
> 安装 Docker Engine 和 NVIDIA Container Toolkit，配置 Docker 使用 GPU，并提供运行
> Docker 的权限及可用 GPU 编号。上面的 GPU 检查命令需要能正常运行。

<details>
<summary>我是管理员 / 我有管理权限：展开查看首次安装命令</summary>

以下命令由有管理权限的人在服务器上执行，适用于 Ubuntu 22.04/24.04。
已有 Docker Engine 时跳过第一组安装命令。若装有发行版 `docker.io` / `containerd` 等冲突包，
先按 [Docker 官方说明](https://docs.docker.com/engine/install/ubuntu/) 处理，不要在运行服务时直接替换。

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' \
  "$(dpkg --print-architecture)" "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
sudo systemctl enable --now docker
```

安装并配置 GPU 容器运行时（来源：[NVIDIA 官方指南](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)）：

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
# 在允许重启 Docker 服务的维护窗口执行
sudo systemctl restart docker
sudo docker run --rm --gpus '"device=0"' nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```


安装完成后，重新执行第 1 步的 GPU 检查命令。后续构建请使用实际计算用户的普通账号登录，
不要在 `sudo -i` 或 root 登录窗口中操作。

</details>

### 第 2 步：找到项目文件夹

向项目维护者取得 DMG-MD 源码，上传并解压到服务器。下文假设文件夹是 `~/newmd`，
其中 `~` 表示你在服务器上的个人目录。

```bash
cd ~/newmd
ls Dockerfile README.md
```

**成功标志：** 显示 `Dockerfile` 和 `README.md` 两个文件名。
如果你已从第一台服务器导入 `dmgmd:cuda12.8` 镜像，且 GPU 检查通过，可跳过第 2～3 步，直接进入第 4 步。
需要从源码构建或更新镜像时，再按第 2～3 步操作。若文件放在其他位置，只需把第一行的 `~/newmd` 换成真实路径。
例如文件夹是 `/data/alice/newmd`，就输入 `cd /data/alice/newmd`。
如果解压后多套了一层文件夹，需要进入真正包含这两个文件的那一层。

### 第 3 步：自动安装项目及其依赖（首次使用或更新程序时）

保持在第 2 步的文件夹内，完整复制下面的命令。最后的英文句点 `.` 也要保留。

```bash
sudo docker build --build-arg BUILD_JOBS=4 \
  --build-arg DMGMD_UID="$(id -u)" --build-arg DMGMD_GID="$(id -g)" \
  -t dmgmd:cuda12.8 .
```

它会自动下载并编译所需软件，首次执行会比较久，需要服务器能够访问下载网站。
`$(id -u)` 和 `$(id -g)` 会自动获取你的账号信息，**不用修改**。
安装期间不要关闭终端；屏幕上出现很多编译信息是正常的。

结束后复制：

```bash
sudo docker image inspect dmgmd:cuda12.8 --format '{{.Id}}'
```

**成功标志：** 安装命令没有报错，检查命令显示以 `sha256:` 开头的一长串字符。
`dmgmd:cuda12.8` 就是安装好的环境包名称，后面的计算命令会使用它。
日常更换材料、势函数或计算参数时，无需重复本步。

### 第 4 步：让程序自动检查安装是否正确

下面的命令使用程序自带的小体系检查显卡、并行通信和计算结果，无需准备输入文件。
完整复制即可；**其中 `--devices 0` 表示选中的显卡在容器内部的编号，不随外面的 GPU 编号修改。**

```bash
sudo docker run --rm --gpus '"device=0"' --shm-size=1g --ulimit memlock=-1:-1 \
  -w /opt/dmgmd/newmd dmgmd:cuda12.8 bash -c '
    set -e
    python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0 --ranks 1
    ctest --test-dir build --output-on-failure
    python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
  '
```

**成功标志：** 输出中依次出现 `PASS environment`、`100% tests passed` 和
`PASS: all 4 baseline cases match for candidate`。全部成功后再进行自己的计算。
若有 `FAIL` 或 `Error`，保留完整输出并联系维护者，不要跳过这一步。

### 第 5 步：先跑一个碳体系示例，找到计算结果

下面命令需在**第 2 步的项目文件夹**中执行。它会创建一个新的算例文件夹，自动放入
碳体系的结构、计算设置和对应 NEP 势；每次都会生成新文件夹，不会覆盖已有结果。

```bash
mkdir -p "$HOME/dmgmd-runs"
case_dir=$(mktemp -d "$HOME/dmgmd-runs/carbon-XXXXXX")
cp tests/baseline/inputs/single_large_nve/model.xyz "$case_dir/"
cp tests/baseline/inputs/single_large_nve/run.in "$case_dir/"
cp tests/baseline/inputs/potentials/nep_C.txt "$case_dir/nep.txt"
cd "$case_dir"
pwd
```

最后一行显示本次计算文件夹的完整路径，建议记下来。接着运行：

```bash
sudo docker run --rm --gpus '"device=0"' --shm-size=1g --ulimit memlock=-1:-1 \
  -v "$PWD:/work" dmgmd:cuda12.8
```

命令结束后查看结果：

```bash
ls
head thermo.out
```

**成功标志：** 没有运行错误，当前文件夹里出现 `thermo.out`、`trajectory.xyz`、`restart.xyz`
等输出，且 `thermo.out` 中有数值。这个示例只运行很短的轨迹，用来熟悉操作，不是生产计算参数。

其中 `-v "$PWD:/work"` 的作用是让程序读写你当前所在的文件夹。
输出保存在服务器的算例文件夹内，命令结束后仍然保留；`--rm` 只清理本次临时计算环境。

### 第 6 步：换成自己的材料体系（日常操作）

为每个新任务单独建立一个文件夹。例如：

```bash
mkdir -p ~/dmgmd-runs/my-sample
cd ~/dmgmd-runs/my-sample
```

通过你常用的文件传输软件，将以下三个文件放进去：

| 文件 | 内容 |
| --- | --- |
| `model.xyz` | GPUMD 格式的原子结构，元素类型须与势函数匹配 |
| `run.in` | 时间步长、系综、运行步数及输出设置 |
| `nep.txt` | 适用于该材料的 NEP 势文件；这里统一使用这个文件名 |

确认 `run.in` 中指定势文件的一行是 `potential nep.txt`。不要直接沿用其他服务器上的
绝对路径。程序目前支持的命令见后面的[当前支持范围](#当前支持范围)，并非 GPUMD 的所有功能。

在这个算例文件夹里复制：

```bash
ls model.xyz run.in nep.txt
```

确认列出了三个文件；如果提示缺少文件，先补齐。然后执行：

```bash
sudo docker run --rm --gpus '"device=0"' --shm-size=1g --ulimit memlock=-1:-1 \
  -v "$PWD:/work" dmgmd:cuda12.8
```

结果仍然写在当前文件夹，具体文件由 `run.in` 中的输出设置决定。
每个新计算使用新文件夹，避免结果与旧任务混在一起。

### 常见问题：看到这些提示时怎么办？

| 提示或现象 | 处理方法 |
| --- | --- |
| `sudo` 无权限 / `not in the sudoers file` | 请管理员完成安装并提供 Docker 使用权限；如果账号已获准直接运行 Docker，可去掉命令开头的 `sudo` |
| `docker: command not found` | 尚未安装 Docker，回到第 1 步的管理员安装说明 |
| 无法连接 Docker / `permission denied`（提到 `docker.sock`） | 请管理员检查 Docker 服务和你的访问权限 |
| `could not select device driver` / `unknown or invalid runtime` | 请管理员安装、配置 NVIDIA Container Toolkit，并重新执行第 1 步检查 |
| `unsatisfied condition: cuda>=12.8` / 驱动版本不足 | 请管理员确认驱动满足本方案要求 |
| 下载超时、`TLS handshake timeout`、`Could not resolve host` | 服务器访问下载网站失败，请管理员处理网络后重试第 3 步 |
| 找不到 `Dockerfile` | 当前不是项目文件夹，回到第 2 步 |
| 找不到 `run.in`、`model.xyz` 或势文件 | 先进入算例文件夹，检查文件名及 `potential` 行；再执行计算命令 |
| 创建输出文件时 `Permission denied` | 检查算例目录是否属于当前用户；迁移来的镜像还需核对账号 ID，见下方迁移说明 |
| `out of memory` / `CUDA ... memory allocation` | 显存不足，先减小体系或确认显卡未被其他任务占用 |
| `unsupported` | `run.in` 使用了当前尚未支持的命令，请对照支持范围调整 |
| 自检失败，但显卡信息能正常显示 | 保存自检完整输出给维护者；显卡可见不代表计算环境全部正常 |
| 构建时出现 `generated model hash mismatch` | 更新项目文件，确认 `tests/long_nve/long_nve_common.py` 是包含 `math.fsum` 的新版，然后重跑第 3 步 |
| 容器自检报 `run parser test failure: unsupported command 'dftd3'` | 更新项目文件，确认 `tests/run_parser_tests.cpp` 包含 `mkstemp`，重新执行第 3 步构建镜像，再运行第 4 步自检 |
| 导入镜像后误执行 `docker build`，报 `resolve image config for docker.io/docker/dockerfile:1` 或 `403 Forbidden` | 不要在 T4 上重建；直接运行第 4 步的自检命令。若确实需要从源码构建，构建服务器需能访问 Docker Hub 及其他构建依赖 |

### 可选：用两张显卡计算

单卡操作熟悉后再使用本节。先确认管理员分配了两张显卡。下面假设编号为 0、1，
在**自己的算例文件夹**中执行。先检查两卡环境：

```bash
sudo docker run --rm --gpus '"device=0,1"' --shm-size=1g --ulimit memlock=-1:-1 \
  -w /opt/dmgmd/newmd dmgmd:cuda12.8 \
  python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1 --ranks 2
```

出现 `PASS environment` 后，再运行：

```bash
sudo docker run --rm --gpus '"device=0,1"' --shm-size=1g --ulimit memlock=-1:-1 \
  -v "$PWD:/work" dmgmd:cuda12.8 mpiexec -n 2 dmg-md
```

`device=0,1` 选择两张显卡，`-n 2` 启动两个计算进程，一张卡对应一个进程。
如改成其他卡号，只修改 `device=...`；内部 `--devices 0,1` 仍保留。
该方法在同一台服务器上运行，不代表多台服务器联合计算已经完成验证。
正式多卡研究前，还应按下方“进阶验收”运行数值测试。

### 可选：搬到另一台服务器，省去重新编译

请管理员在目标服务器完成第 1 步。原服务器执行：

```bash
sudo docker save dmgmd:cuda12.8 | gzip > dmgmd-cuda12.8.tar.gz
sha256sum dmgmd-cuda12.8.tar.gz
```

把生成的 `dmgmd-cuda12.8.tar.gz` 用文件传输软件复制到新服务器，同时保存屏幕上的校验码。
在新服务器进入归档所在文件夹，执行：

```bash
sha256sum dmgmd-cuda12.8.tar.gz
```

两台服务器的校验码应完全相同；不一致时重新传输。确认一致后再导入：

```bash
gzip -dc dmgmd-cuda12.8.tar.gz | sudo docker load
```

导入完成后重新执行第 4 步自检。自己的算例文件夹需要另外复制。

**账号权限：** 两台服务器分别执行 `id -u` 和 `id -g`，两组数字应一致。
若不一致，最简单的方法是在新服务器用自己的账号重新执行第 2～3 步，构建适合该账号的镜像。
安装包本身不包含你的输入和计算结果。

### 进阶验收（维护者 / 正式多卡研究）

四卡节点在容器内先运行 `check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4`，
通过后运行 `run_mpi_domain.py`。可在服务器直接完整复制下列命令（需已分配 0～3 号卡）：

```bash
sudo docker run --rm --gpus '"device=0,1,2,3"' --shm-size=1g --ulimit memlock=-1:-1 \
  -w /opt/dmgmd/newmd dmgmd:cuda12.8 bash -c '
    set -e
    python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4
    python3 tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --devices 0,1,2,3
  '
```

单卡长程 smoke 命令：

```bash
sudo docker run --rm --gpus '"device=0"' --shm-size=1g --ulimit memlock=-1:-1 \
  -w /opt/dmgmd/newmd dmgmd:cuda12.8 \
  python3 tests/long_nve/run_long_nve.py --candidate ./build/dmg-md --devices 0 --profile smoke
```

单卡验收不替代多卡数值验收；更换驱动、镜像或显卡后重新检查。正式验收应保存终端完整输出，
并将支持 `--report` 的测试报告写入可写挂载目录，容器内 `/tmp` 文件会随 `--rm` 删除。
默认通信仍为 HostStaged；切换 CudaAware 的机制见后面的运行说明。

<details>
<summary>依赖版本、兼容性依据和构建细节（无需手动安装）</summary>

### 依赖选择与兼容边界

Dockerfile 固定以下基线，目标是 **Linux x86_64、Ubuntu 22.04/24.04 宿主机、T4 到
Hopper GPU**。这是依据上游支持范围选定的组合；编译成功不等于目标机数值验收通过，
每台机器还须执行下方自检。当前验证情况见 [Docker 验证记录](docs/status/docker-validation.md)。

| 组件 | 镜像内版本 / 配置 | 选择原因 |
| --- | --- | --- |
| 基础镜像 | `nvidia/cuda:12.8.0-devel-ubuntu22.04` | 用户态统一在 22.04，CUDA 不超过 12.8 |
| 编译器 | GCC/G++ 11，C++17 | CUDA 12.8 支持范围内，延续原项目 GCC 主版本 |
| CMake | 3.28.4 | 满足项目 ≥3.24，避开 Ubuntu 22.04 默认 3.22 |
| UCX | 1.18.1，启用 CUDA、多线程 | 使用正式维护版本，包含 CUDA 修复 |
| Open MPI | 5.0.10，CUDA + UCX PML | 延续原项目 MPI 版本；与 UCX 一起源码编译 |
| PMIx / PRRTE / hwloc / libevent | Open MPI 发行包内置版本 | 避免依赖不同宿主机的 PMIx ABI 和组件目录 |
| Python | Ubuntu 22.04 的 3.10 | 仅用于测试和构建工具，不安装 PyTorch 等框架 |
| GPU 代码 | `75;80;86;89;90` | 包含 T4 的原生 `sm_75`，以及 Ampere/Ada/Hopper |

宿主机只需 NVIDIA 驱动、Docker Engine、NVIDIA Container Toolkit；无需安装或降级宿主机
CUDA、GCC、MPI。容器共享宿主机内核和驱动，不能消除驱动限制。此方案采用 CUDA 12.8 GA 的
Linux 原生驱动基线 **≥570.26**；如果 `nvidia-smi` 显示 CUDA 12.8，通常符合此范围，仍需核对
实际 Driver Version。`nvidia-smi` 的 CUDA 字段表示驱动支持上限，不是已安装的 Toolkit。
不依赖 CUDA minor-version compatibility 或额外的 `cuda-compat` 包来放宽这条部署门槛。

依据：[CUDA 12.8 驱动表](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-toolkit-release-notes/)、
[CUDA 12.8 Linux/编译器支持](https://docs.nvidia.com/cuda/archive/12.8.0/cuda-installation-guide-linux/index.html)、
[T4 compute capability 7.5](https://developer.nvidia.com/cuda/gpus)、
[Open MPI CUDA 构建](https://docs.open-mpi.org/en/v5.0.x/tuning-apps/networking/cuda.html)、
[UCX 1.18.1 发布记录](https://github.com/openucx/ucx/releases/tag/v1.18.1)、
[Open MPI 5.0.10 发行包与校验值](https://www.open-mpi.org/software/ompi/v5.0/)。


镜像内自动加载 `/opt/dmgmd/env/md-mpi.sh`；保留源码、编译工具和测试样例供验收。
UCX/Open MPI 源码包校验 SHA-256。构建时自动执行 CPU 测试，不需要连接 GPU。
`.dockerignore` 排除旧 build 和大型计算结果。

默认编译五种 GPU 架构；仅部署 T4 时，可在构建命令中加 `--build-arg CUDA_ARCHITECTURES=75`，
但这样的镜像不再包含其他四种原生架构代码。大体系可适当增大 `--shm-size`。
跨节点网络、RDMA 和调度器集成仍需单独部署验收。

核心版本已固定，但基础镜像标签和 Ubuntu 安全更新可能变化，不保证跨日期重建的镜像完全相同。
生产部署建议保存同一镜像归档、校验码、源码版本和验收结果；镜像 ID 可通过第 3 步命令查询。

</details>

## 项目实现与开发者说明

下面介绍支持范围、实现状态和不使用 Docker 时的构建方法。使用上方 Docker 流程时，
日常计算无需执行这里的原生安装或构建命令。

DMG-MD 是面向多节点、多 GPU 经典分子动力学的 runtime。当前仓库包含与锁定 GPUMD
reference 兼容的单 rank 路径，以及一 MPI rank 一张 GPU 的 replicated-data MPI runtime
（M1）和 M2a local-domain 路径。

当前版本直接读取 GPUMD 格式的 `model.xyz`、NEP/NEP-ZBL potential 和 `run.in`。GPUMD
的最小数值核心已在 `src/gpumd_compat/` 中复现（tokenizer、Box、GPU_Vector、邻居构建、
NEP loader 及 CUDA kernels，复制自锁定 commit `9d23496e`），构建与运行均不依赖
`../gpumd-reference`。M1 replicated-full 路径在每个 rank 保留完整坐标/类型，只分片积分、
thermo 和 authoritative per-atom output；满足 M2a eligibility 的大盒输入则使用 rank-local
owned/ghost 布局、保守两跳位置 halo、点对点 halo/迁移通信和 NEP 中心/依赖域分片。不满足
eligibility 的输入自动回退 M1，small-box 输入继续可运行。M2b 的 Fp/partial 分阶段交换
和 M3 多节点硬化尚未实施。

## 当前进度

M2a 已于 2026-09-18 完成专属验收矩阵，并手动通过 nightly 正确性验证：

- `tests/mpi/run_mpi_domain.py`：9 cases × 2/4 rank × HostStaged/CudaAware，共 36 组全通过；
- `scripts/run_long_nve_nightly.sh`：7 cases、seed 0、1/2/4 rank、双通信后端，共 42 个配置全通过；
  其中 M2a 的 2/4-rank 配置 28/28 命中 `mode=m2a` 并通过；
- 100000-step release 矩阵尚未执行，因此当前不发布多卡性能或 scaling 结论。

详细环境、命令和结果证据见 [当前进度与验证结果](docs/status/current.md)。

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
- Open MPI+UCX（含 CUDA support、UCX PML、`cuda_copy` 和 `cuda_ipc`）；项目使用 Open MPI
  `mpi-ext.h`/`MPIX_Query_cuda_support()`，不支持替换为 MPICH、MVAPICH 或其他 MPI。

构建与运行不依赖 `../gpumd-reference`：DMG-MD 需要的 GPUMD 最小子集（tokenizer、Box、
GPU_Vector、邻居构建、Potential、NEP/NEP-ZBL CUDA kernels）已在 `src/gpumd_compat/`
中复现，锁定来源 commit `9d23496e41319b9e2af5221a7df6285387401d1e`。该参考 checkout 仅
用于生成和比对 golden 基线（见下文 Golden differential），并保持只读。

原生构建使用上级 `env/md-mpi.sh`；Docker 自动加载镜像内的同名入口
`/opt/dmgmd/env/md-mpi.sh`。CMake 检查 CUDA 编译器、Toolkit 与 MPI 的安装路径，
避免混用宿主机或旧 build cache 中的依赖。Python ≥3.10 仅用于测试，测试脚本无需第三方包。

## 原生构建与测试

```bash
source ../env/md-mpi.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
```

项目默认构建 CUDA 架构 `75;80;86;89;90`，可通过
`-DCMAKE_CUDA_ARCHITECTURES=<architecture>` 覆盖。

## 运行

多 rank 运行示例：

```bash
source ../env/md-mpi.sh
mpiexec -n <ranks> /absolute/path/to/dmg-md
```

默认通信后端是 `HostStaged`：CUDA device buffer → pinned host buffer → Open MPI/UCX →
pinned host buffer → CUDA device buffer。可选 `CudaAware` 必须显式请求，并通过启动时覆盖
`MPI_Allreduce(MPI_IN_PLACE)`、`MPI_Allgatherv`、`MPI_Gatherv`、`MPI_Bcast` 的
device-pointer 数值自检后才会启用：

```bash
DMGMD_COMM_BACKEND=CudaAware mpiexec -n 4 /absolute/path/to/dmg-md
```

启动先用 Open MPI `MPIX_Query_cuda_support()` 记录 capability；请求 `CudaAware`（或设置
`DMGMD_CUDA_AWARE_PROBE=1`）后还必须通过主动数值自检。只有 capability 为 supported 且自检
passed，生产 collective 才能接收 device pointer；否则回退 `HostStaged`。HostStaged 始终是
正确性默认路径。

每个 rank 启动时记录 Open MPI library version、Open MPI+UCX stack、hostname、world/local
rank、CUDA ordinal/UUID、capability、自检结果、实际 backend 和 ordinary single-device NEP
策略。默认每步的 collective buffer 字节数写到 rank 0 stdout；长期正确性测试可设置正整数
`DMGMD_COMM_LOG_INTERVAL` 做低频采样。物理链路字节数取决于 collective 算法，不伪装成
精确值。每个 run segment 和整个 replicated runtime 还会输出 rank 0 汇总的 `DMGMD_TIMING`，
包含各 rank wall time 的 min/mean/max 和按最慢 rank 计算的全局 atom-steps/s；它是诊断记录，
不是当前正确性 suite 的性能通过门槛。

发生异常时，出错 rank 会在 `MPI_Abort` 前写入并 flush 一条带 world/local rank、hostname 和
错误类别的 `DMGMD_ERROR` 到 stderr。异常路径不执行可能死锁的 MPI 日志汇聚；Open MPI/PRRTE
把远端 stderr 转发到 `mpirun` 启动端，由调用方统一捕获。

在同时包含 `run.in` 和 `model.xyz` 的工作目录执行：

```bash
/absolute/path/to/newmd/build/dmg-md
```

potential 文件路径按 `run.in` 中的 `potential` 命令解释。输出文件名、列顺序、默认值和单位
由 GPUMD golden tests 锁定。

## Golden differential

先验证原 GPUMD baseline，再比较 `dmg-md`：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py \
  --reference ../gpumd-reference/src/gpumd --device 0
python3 tests/baseline/run_baselines.py \
  --candidate ./build/dmg-md --device 0
```

MPI 1/2/4 rank 矩阵默认同时测试 HostStaged 与 CudaAware。脚本会在任何 MD case 之前验证
Open MPI/UCX 安装与链接、`cuda_copy/cuda_ipc`、GPU 数量/唯一绑定及 device-pointer
collectives；预检失败时不会启动数值测试：

```bash
python3 tests/mpi/run_mpi_differential.py \
  --candidate ./build/dmg-md --devices 0,1,2,3
```

M2a local-domain 专属矩阵使用满足 large-box 与 slab-width 条件的 fixture，覆盖两跳
coordinate halo、迁移、周期边界、空 rank、restart、NEP5、typewise cutoff 和 ZBL 分支：

```bash
python3 tests/mpi/run_mpi_domain.py \
  --candidate ./build/dmg-md --devices 0,1,2,3
```

100/10000/100000-step 长程正确性 suite 独立运行，不把长轨迹混入短程 committed golden，也
不采集性能数据。除 NVE 外，它还比较确定性 NVT 的温度统计、时间平均 RDF 和 MSD；
nightly/release 默认覆盖 HostStaged 与 CudaAware，并包含 NEP5、typewise cutoff、flexible ZBL
和 typewise ZBL cutoff 的静态/短轨迹分支。smoke 示例：

```bash
python3 tests/long_nve/run_long_nve.py \
  --candidate ./build/dmg-md --devices 0 --profile smoke \
  --report /tmp/dmgmd-long-nve-smoke.json
```

nightly 使用 4 张 GPU，覆盖 10000-step NVE/NVT、构型回放和跨 rank restart：

```bash
scripts/run_long_nve_nightly.sh
```

完整方法、smoke/nightly/release profile 几何、五初态 release 矩阵、NVT 统计、双向构型回放和
跨 rank restart 见 [长程正确性测试说明](tests/long_nve/README.md)。

实现协议、中心分片完整性结论和逐步通信量公式见
[replicated-mpi.md](docs/standards/replicated-mpi.md)。

## 性能测试与迁移部署

独立 benchmark 支持 GPUMD 与 DMG-MD 的 1/2/4/8 卡对比、强/弱扩展、百万级样例，
含 GPU 占用检查、预热排除、重复统计与 JSON/CSV 报告。它不代替长程正确性验收。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile standard --dry-run
```

- [性能测试方案与运行说明](tests/benchmark/README.md)
- [其他服务器快速部署](docs/operations/deployment.md)

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

- [文档索引与分类](docs/README.md)
- [当前进度与验证结果](docs/status/current.md)
- [Golden Test 标准](docs/standards/golden-test-standard.md)
- [架构决策](docs/standards/architecture-decisions.md)
- [当前数据布局](docs/standards/data-layout.md)
- [输入兼容矩阵](docs/standards/compatibility-matrix.md)
- [待实施计划](docs/plans/domain-decomposition.md)
- [Golden test 说明](tests/baseline/README.md)
- [长程 NVE/NVT 正确性测试](tests/long_nve/README.md)
