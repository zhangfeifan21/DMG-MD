# 四引擎静态核验

用同一帧含速度的 GPUMD 扩展 XYZ 启动普通 GPUMD、DMG-MD、LAMMPS/KOKKOS/NEP 和 GPUMD-WPE。普通 GPUMD 5.9.1 提供参考值。三组候选各自与参考比较原子势能、三分量力和九分量 virial。

## 求力和原子映射

普通 GPUMD、DMG-MD 和 WPE 用 `time_step 0`、`ensemble nve`、`run 1` 输出零时刻的力。LAMMPS 用 `run 0`，省略积分器。XYZ 的输入行序定义从 1 开始的原子 ID。LAMMPS 数据沿用该 ID，输出按 ID 排序，逐行核对元素、坐标和速度。比较器用正交盒的最小镜像核对坐标，接受等价的周期映像。

力用 eV/Å，势能和 virial 用 eV。GPUMD 系列按矩阵行序写出九个原子 virial 分量。LAMMPS 的 `compute centroid/stress/atom NULL virial` 读取 MatPL 的九分量 `cvatom`，输出顺序为 xx、yy、zz、xy、xz、yz、yx、zx、zy。把结果乘 `-1/1602176.5` 后重排为 xx、xy、xz、yx、yy、yz、zx、zy、zz。两侧都保留 17 位有效数字。整体系能量和 virial 单独记录累计差值。LAMMPS 的总势能差除以原子数后须 ≤5 × 10⁻⁵ eV，沿用跨引擎清单的上限。

LAMMPS 转换器保留 NEP 文件的哈希和格式转换记录。三种公开输入沿用统一 cutoff 行。铜作业创建一个 Cu 原子类型，`pair_coeff * * Cu` 从 16 元素势中选取 Cu，原始势保留全部元素。

## 构建 MatPL 周期几何兼容模式

普通 GPUMD 5.9.1 在大正交盒中先对两个主胞坐标求差，再转成单精度，最后用单精度盒长求最小镜像。为逐原子复核该版本的结果，MatPL 提供显式的 `geometry gpumd_5_9_1` 选项。它保留 LAMMPS 已选择的邻居及其所属进程。对范围内的邻边，描述符和配对力导数使用与参考相同的位移舍入顺序。角向 virial 汇总沿用 LAMMPS 已平移邻居的双精度力臂。普通 GPUMD 在这一汇总步骤以双精度求最小镜像，再将力臂转成单精度。该模式要求双精度 NEP 推理、单个无电荷的 NEP4 或 NEP4-ZBL 势，以及三轴全周期的正交大盒。各轴长度须大于 2.5 ×（最大径向截断半径 + 1 Å）。每次求力都会检查当前盒。

在 scratch 中准备 LAMMPS `611ca3b7` 和 MatPL `21fabf34` 的源码副本。仓库保存了原始 overlay 和源码身份。以下命令从 DMG-MD 仓库根目录执行。`MATPL_SRC` 指向 scratch 中的独立源码副本，`BUILD` 指向新的构建目录。若从干净的 LAMMPS 源码开始，先把 `third_party/lammps-matpl-overlay/files/src/` 的内容合并到 `$MATPL_SRC/src/`，并复制该 overlay 的 `files/cmake/CMakeLists.txt` 到 `$MATPL_SRC/cmake/`。[原始 overlay 清单](../../../third_party/lammps-matpl-overlay/manifest.json) · [周期几何补丁](../../../third_party/lammps-matpl-overlay/gpumd-5.9.1-geometry.patch)

```bash
patch="$PWD/third_party/lammps-matpl-overlay/gpumd-5.9.1-geometry.patch"
git -C "$MATPL_SRC" apply --check "$patch"
git -C "$MATPL_SRC" apply "$patch"
# L20，传入实际主机编译器的绝对路径。构建环境提供 MPI、CUDA、CMake 和 Ninja。
export NVCC_WRAPPER_DEFAULT_COMPILER="$HOST_CXX"
cmake -G Ninja -S "$MATPL_SRC/cmake" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=17 -DCMAKE_C_COMPILER="$HOST_CC" \
  -DCMAKE_CXX_COMPILER="$MATPL_SRC/lib/kokkos/bin/nvcc_wrapper" \
  -DCMAKE_CUDA_COMPILER="$CUDA_HOME/bin/nvcc" -DCMAKE_CUDA_HOST_COMPILER="$HOST_CXX" \
  -DCMAKE_CUDA_ARCHITECTURES=89 -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
  -DBUILD_MPI=ON -DBUILD_OMP=ON -DBUILD_SHARED_LIBS=OFF \
  -DPKG_KOKKOS=ON -DPKG_NEP_KK=ON -DPKG_KSPACE=ON -DPKG_MANYBODY=ON \
  -DPKG_MOLECULE=ON -DPKG_RIGID=ON -DPREC_NEPINFER=ON \
  -DKokkos_ARCH_ADA89=ON -DKokkos_ENABLE_CUDA=ON -DKokkos_ENABLE_CUDA_LAMBDA=ON \
  -DKokkos_ENABLE_OPENMP=ON -DKokkos_ENABLE_SERIAL=OFF -DFFT=KISS -DFFT_KOKKOS=CUFFT \
  -DFFT_SINGLE=OFF -DWITH_GZIP=ON -DWITH_PNG=OFF -DWITH_JPEG=OFF -DWITH_FFMPEG=OFF
cmake --build "$BUILD" --target lmp -j 24
LAMMPS="$BUILD/lmp"
```

L20 的验证构建使用 GCC 13.4.0 主机编译器和 sm_89。使用其他硬件时需选用相应的 CUDA 架构。显式选择兼容模式后，首次求力的日志须包含独立一行 `MatPL NEP geometry: gpumd_5_9_1`。测量、容量复验和留出终验均核对该行。默认的 `--lammps-geometry native` 可以复现安装版的测量。发布清单沿用终验实际选取的模式。

初态恰在周期面或紧邻该面时，普通 GPUMD 和 LAMMPS 的 `read_data` 可能把同一原子放在相对的两个面上。转换器保留原始 XYZ，并在两种几何模式下生成逐字节相同的 `data.lmp`。它用输入行的原子 ID、原盒及普通 GPUMD 的逆盒矩阵算序，找出需要在起始求力时取另一侧映像的原子。静态零步长作业计入初始化和第一步的两次折回，短程重放的初始求力计入一次折回。

有这类原子时，转换器生成相应的 LAMMPS group，将最多六个组名依次作为 `initial_face_images UX UY UZ LX LY LZ` 传给 MatPL。顺序为三个上界面、三个下界面，空位用 `NULL`。组随原子在 MPI 进程间交换。首次启用的日志还应有独立一行 `MatPL NEP initial face images: active`。手写输入若在同一时间步重复求力，期间须保持导入的盒和坐标。时间步首次推进后，MatPL 停用这组初态映像。测量记录给出六个面的原子数。

两个微型回归输入来自 401 校准帧。水取原始 ID 1210639～1210641 的一个水分子。碳取中心原子 959750 和第一近邻 959749、959755、987975、987977。两者均保留原坐标、速度、盒和行序。可在下文的 `measure` 命令中指定 `--model tests/npt/public_inputs/regression/periodic-water.xyz` 或 `periodic-carbon.xyz`，使用对应材料的势，将试跑选项改为 `--trial regression --check-guards`。命令检查全部分量与总势能上限，超限时保存测量并返回 1。另一个水样例 `regression/exact-face-water.xyz` 让氢原子恰好位于 x 上界面。用同一命令可核对这一原子的组标记和独立的源坐标。

## 校准和留出帧

在仓库根目录加载 `../env/md-mpi.sh`，给每次 `measure` 指定原始快照、势、材料、一张 GPU 的 UUID、四个可执行文件和空输出路径。独立校准用 `--trial calibration-<seed>` 与对应的 `--trial-evidence`。执行记录的种子、势哈希和输出快照哈希须与实参相符。

```bash
python3 tests/npt/public_inputs/static_observables.py measure \
  --material copper --model "$SNAPSHOT" --potential "$NEP" \
  --trial calibration-401 --trial-evidence "$EXECUTION_JSON" --gpu "$GPU_UUID" \
  --gpumd-bin "$GPUMD" --dmgmd-bin "$DMGMD" \
  --lammps-bin "$LAMMPS" --lammps-geometry gpumd_5_9_1 --wpe-bin "$WPE" --output "$STATIC_DIR"
```

每种材料收集至少 3 张独立校准帧。先生成 WPE 诊断记录，再用 `calibrate --frames <measurement.json ...> --wpe-diagnosis <wpe-diagnosis.json> --output <calibration.json>` 冻结门槛。每个候选与参考配对，每个物理量独立取全部校准帧的逐分量最大绝对误差。经验门槛取最大误差的 2 倍，观测全零时要求留出帧精确相等。该倍数用于预测同材料独立帧的误差，无统计置信度解释。

逐分量还要通过绝对加相对误差上限。DMG-MD 沿用 baseline 清单，LAMMPS 沿用跨引擎清单。WPE 使用跨引擎清单中的数值作额外上限。校准任一帧超限时，`calibrate` 仍写出失败证据，再返回退出码 1。

容差冻结后，为 `measure` 指定 `--trial holdout --calibration "$CALIBRATION"`，同时用 `--trial-evidence` 指向所选末帧同目录的 `execution.json`。碳和铜选择 `scr/`，水选择经分段检查并预先登记的末帧，累计 60 ps 对应 `scr-extension/`，累计 110 ps 对应 `scr-extension-2/`。程序先复核校准来源、引擎身份、完整制备链的物理判据及末帧 SHA-256，再对通过检查的 SCR 末态求力。再执行 `verify`，使留出帧既通过经验门槛，也通过绝对加相对上限。

```bash
python3 tests/npt/public_inputs/static_observables.py verify \
  --frame "$HOLDOUT/measurement.json" --calibration "$CALIBRATION" \
  --output "$HOLDOUT_RESULT"
```

`measurement.json` 记录输入和可执行文件哈希、GPU 身份、单位、逐分量最大误差和误差最大的原子 ID。`calibration.json` 记录校准来源和冻结门槛。`verify` 复核校准测量和源作业记录的哈希，再重算已冻结的字段。它还检查构建身份、快照独立性和门槛时间，写出逐项结果。校准记录还冻结 WPE 诊断的哈希。`numeric_status` 仅表示静态量数值判定。发布资格另须核对 WPE 差异查因、平衡态判定及复读重放证据。

## 复做 WPE 差异诊断

准备普通 GPUMD 5.9.1 和已启用 WPE 1.0.0 的宿主。还需从 WPE 的同一 GPUMD 宿主源码构建一个原生对照。宿主源码提交为 `a0e55bc87390b93d009da37c798616982573bab2`。L20 上在宿主源码的 `src` 目录执行 `make CUDA_ARCH=-arch=sm_89 gpumd`，把生成的原生程序路径放入 `NATIVE`。

在仓库根目录运行诊断。确保 `nsys` 和 `cuobjdump` 在 `PATH`。程序复用三种材料的公开势和构建函数，生成 5,760 个碳原子、7,680 个水原子和 8,192 个铜原子的三个小体系。铜势路径和变量定义见[制备说明](README.md)。

```bash
source ../env/md-mpi.sh
python3 tests/npt/public_inputs/static_wpe_diagnosis.py \
  --gpumd-bin "$GPUMD" --native-bin "$NATIVE" --wpe-bin "$WPE" \
  --host-revision a0e55bc87390b93d009da37c798616982573bab2 \
  --copper-potential "$COPPER_POTENTIAL" --gpu "$GPU_UUID" \
  --output "$work/static"
```

程序先核对普通 GPUMD 与同宿主原生结果，再检查重复运行和重排原子行序对 WPE 输出的影响。原子行序打乱后按已知 ID 还原。对水捕获实际执行的 CUDA kernel，并核查描述符、神经网络和角向求力 kernel 的浮点操作。结果写入 `static/wpe-diagnosis.json`。逐原子输出和 CUDA 跟踪放在相邻的 `static/wpe-diagnosis/`，诊断记录用相对路径登记证据及哈希。

诊断结论覆盖已登记引擎和三种小体系。输出记录可执行文件、WPE 库、结构和势的哈希，以及实际 GPU。代码路径或运行硬件发生变化时重新做诊断。该项通过表示已定位差异来源，目标规模的数值门槛由独立校准帧和留出帧判断。
