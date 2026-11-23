# NHC / BDP 验收

这里检查 Nosé–Hoover 链（NHC）和 Bussi–Donadio–Parrinello（BDP）恒温器。改控制器公式时先跑标量检查。改路由、归约或重启时，选择短程和协议检查。统计验证需要独立初态与预注册预算。A3–A6 已验收的范围和结果见 [NHC](../../docs/status/validation/thermostat-validation.md#nhc-20261102)、[BDP](../../docs/status/validation/thermostat-validation.md#bdp-20261102)和[双路径矩阵](../../docs/status/validation/thermostat-validation.md#thermostat-matrix-20261102)。

## 构建与标量检查

从仓库根目录加载实验室环境。`WORK_ROOT` 指向可写位置。下文的各证据目录须不存在。`GPU_IDS` 填 4 张已分配的设备，供 P1/P2/P4 运行。只执行标量检查仍需要本项目构建要求的 CUDA Toolkit 和 MPI。

```bash
source ../env/md-mpi.sh
export PYTHONDONTWRITEBYTECODE=1
WORK_ROOT="$PWD/../scratch/thermostats-new"
BUILD_DIR="$WORK_ROOT/build"
CANDIDATE="$BUILD_DIR/dmg-md"
GPU_IDS=0,1,2,3  # 示例，换成分配的设备
cmake -S . -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR" -j 8
ctest --test-dir "$BUILD_DIR" --output-on-failure
python3 tests/thermostats/test_reference_options.py
```

CTest 注册 NHC、随机速度重标定（SVR）的标量测试、统计分析器检查和其他项目测试。NHC 的 3 个独立用例各执行连续 4 次半步，比较缩放因子和完整链状态的双精度位模式。SVR 的 16 个用例覆盖种子、自由度奇偶、Gaussian 缓存及 `taut<=0.1`。控制器测试还核对非根进程不分配链及随机数状态、每段重置和 Berendsen 原表达式。

重新生成标量参考时，脚本从锁定的 GPUMD checkout 读取函数，在独立目录编译。上游 NHC 在 `m=0` 时有一次会被覆盖结果的负索引读取。参考程序加 padding 避免未定义行为。DMG-MD 在读取前分支，保留有效表达式和求值顺序。参考 checkout 只读，既有 golden 与容差继续用作比较依据。

```bash
python3 tests/thermostats/fixtures/generate_nhc.py \
  --reference-root ../gpumd-reference --output "$WORK_ROOT/nhc-oracle.json"
python3 tests/thermostats/fixtures/generate_svr.py \
  --reference-root ../gpumd-reference --output "$WORK_ROOT/svr-oracle.json"
```

<a id="环境短程和失败关闭"></a>

## 短程、通信与重启

### 查短程输出与错误处理

先检查 4 张设备的绑定和 CUDA 缓冲通信。短程驱动使用 2,016 和 5,184 原子的 NEP4 金刚石。较小体系在 P2/P4 走全量复制回退，较大体系走局部域分解。同 rank 和后端的重复运行须产生逐字节一致的科学文件。跨 rank 差分及 GPUMD NHC 比较用现有 baseline 容差。GPUMD BDP 的参考随机数取自墙钟，使用分布而非逐步轨迹作对照。

```bash
python3 tests/mpi/check_environment.py --candidate "$CANDIDATE" --devices "$GPU_IDS" --ranks 4
python3 tests/thermostats/run_thermostats.py \
  --candidate "$CANDIDATE" --devices "$GPU_IDS" \
  --ensembles nvt_nhc nvt_bdp --sections short negative \
  --work-root "$WORK_ROOT/short"
python3 tests/thermostats/check_bdp_contract.py \
  --candidate "$CANDIDATE" --devices "$GPU_IDS" --work-root "$WORK_ROOT/bdp-contract"
```

负例须在时限内非零退出，显示指定错误，且不写最终 XYZ 与重启文件。零动能用例使用两个不相互作用的静止原子，第二次速度 Verlet 半步后动能仍为零。BDP 另检查默认种子、非法字符串、rank 间种子数值或原始字符串不一致，以及多段和新作业的重启。

`run_thermostats.py` 和 `check_variants.py` 可用 `--reference` 选择隔离构建的参考程序。用 `--reference-provenance` 和 `--reference-provenance-sha256` 一起提供出处和哈希。脚本沿用长程验收对归档、源码 Git blob、构建和二进制身份的核验，并在每次参考运行前复核程序哈希。无出处参数时沿用原锁定身份。只给其中一个参数会在创建结果和预检 GPU 之前失败。上节的 CPU 测试覆盖这些分支。

### 核对缩放因子和其他势

```bash
python3 tests/thermostats/check_protocol.py --candidate "$CANDIDATE" \
  --probe "$BUILD_DIR/tests/libdmgmd_ensemble_factor_probe.so" \
  --ensemble nvt_bdp --devices "$GPU_IDS" --work-root "$WORK_ROOT/protocol"
python3 tests/thermostats/check_variants.py --candidate "$CANDIDATE" \
  --devices "$GPU_IDS" --work-root "$WORK_ROOT/variants"
```

`check_protocol.py` 默认测 `nvt_nhc`，示例指定 BDP。`LD_PRELOAD` 探针只包装主机的 2-double `Bcast`，检查各 rank 收到的因子位模式及相对 Berendsen 增加的逐步通信。探针不增加集合通信或 CUDA 操作。

NEP5 与 ZBL 检查沿用长程锁定输入和转换。用运行记录确认实际路由。BaTiO₃ 在 P2 走局部域分解，在 P4 回退全量复制。

### 从已通过的结果重启

先完成短程与势变体检查。脚本从成功源记录核对输入输出哈希，再读快照。各组合用新作业运行 5 步，并单独检查两种系综的 P1→P4 恢复。链与随机数状态按新作业初始化，验收比较的是可恢复的物理字段。细节见[系综标准](../../docs/standards/ensembles.md)。

```bash
python3 tests/thermostats/check_restarts.py --candidate "$CANDIDATE" \
  --devices "$GPU_IDS" --sources "$WORK_ROOT/short" "$WORK_ROOT/variants" \
  --work-root "$WORK_ROOT/restarts"
```

## 统计合同

### 动能和速度分布

[manifest.json](manifest.json) 锁定输入哈希和动能分布参数。[velocity-manifest.json](velocity-manifest.json) 定义相互作用 NEP 体系的三个速度方向矩。主统计用 432 原子的相互作用体系，显式设置 Gaussian 初速并保留质心运动，输出温度按 `3N` 个自由度。NHC 的种子区分初速，BDP 同时用该种子初始化恒温器。

`sample_statistics.py` 计算动能和平方离差的自相关、有效样本量（ESS）。按完整定长块和种子间分别计算 Student-t 置信区间，取较宽的半宽。区间完全落在冻结误差带内，且 ESS 和块数达标，才判 `PASS`。三个预定预算做 Bonferroni 校正，每次约 98.33% 覆盖。该口径适用于各项区间，整个矩阵的联合覆盖率未定为 95%。

动能检查覆盖 P1、P2/P4 全量复制和 P2/P4 局部域分解，后两类都走两种后端。半步长保留物理耦合时间与采样时长。BDP 另有 8 组 GPUMD 分布对照，NHC 用同一入口的 `--ensembles nvt_nhc`。`--minimum-statistics-steps` 和 `--minimum-steps` 只用于保留前一轮证据后的预定延长。种子、预热舍弃区间、误差带及预算均按注册值执行。

```bash
export DMGMD_ACF_HELPER="$BUILD_DIR/tests/dmgmd_autocorrelation"
python3 tests/thermostats/run_thermostats.py \
  --candidate "$CANDIDATE" --devices "$GPU_IDS" \
  --ensembles nvt_bdp --sections statistics --parallel-seeds 4 --timeout 1800 \
  --work-root "$WORK_ROOT/statistics"
python3 tests/thermostats/check_velocity_statistics.py \
  --candidate "$CANDIDATE" --ensemble nvt_bdp \
  --devices "$GPU_IDS" --work-root "$WORK_ROOT/velocity"
```

并行种子使用互不重叠的 GPU 组。同一节点上独立作业的默认 MPI 核绑定可能相互竞争，需记录实际 CPU 亲和性。此结果用于统计验收。分析可选用原生自相关函数（ACF）程序，设置了路径但程序无效时直接失败。未设置时使用同一估计器的 Python 版本。`check_velocity_statistics.py --analyze-existing --work-root ROOT` 复核旧数据并写新分析文件，保留原报告。

### 独立谐振模型

`run_harmonic.py` 和 `harmonic_sampler.cpp` 使用独立可解模型检查总动能和各方向矩。积分沿用这套独立模型的速度 Verlet。默认测 NHC，也可指定 BDP。[harmonic-confirmation.json](harmonic-confirmation.json) 保留 NHC 的独立固定预算确认。总动能 `PASS` 时继续按原判据报告各轴的 `INCONCLUSIVE`。结果适用于该模型。

```bash
python3 tests/thermostats/run_harmonic.py \
  --candidate "$BUILD_DIR/tests/dmgmd_harmonic_sampler" \
  --ensemble nvt_bdp --parallel-seeds 4 --work-root "$WORK_ROOT/harmonic"
```

`--parallel-seeds N` 取正整数，默认串行。无论并行度如何，记录和统计均按 manifest 的种子顺序生成，初态、参数与统计规则保持一致。

<a id="相同-harmonic-模型的跨引擎对照"></a>

## 同一谐振模型的跨引擎对照

三个实现共用 manifest 规定的初态、采样预算、预热区间、置信区间、有效样本量和误差带。C++ `mt19937_64` 与 `normal_distribution` 对各模态先抽位置再抽速度，所有引擎共用生成结果。LAMMPS 用 `units lj`，设置 `T=k_B*300`、`mass=1`，保留 24 个自由度，温控与 thermo 均不扣除质心。NHC 链长为 4。LAMMPS 使用自己的链积分和热浴初态，统计比较允许不同随机轨迹。

### CPU 参考

`reference_gpumd_harmonic.py` 只读抽取未改动的 GPUMD NHC/SVR 函数，在独立 CPU 驱动中施加谐振力。NHC 前后半步保留 GPUMD 的温度到动能往返计算。每个 BDP 种子启动新进程，隔离上游静态 Gaussian 缓存。

LAMMPS 需要 `EXTRA-FIX` 的 `temp/csvr`，应先核对现有 NEP/Kokkos 二进制的包。若需要构建，从锁定参考导出独立源码，并启用 `PKG_EXTRA-FIX=on`。CPU 对照可关闭 MPI、OpenMP 和 Kokkos。`split-validation` 比较输出设置所需的 `run 1` / `run N-1` 与单段结果，验证 LAMMPS 的输出安排。`LAMMPS_CPU` 填符合条件的可执行文件。

```bash
export DMGMD_ACF_HELPER="$BUILD_DIR/tests/dmgmd_autocorrelation"
python3 tests/thermostats/reference_gpumd_harmonic.py \
  --stage pilot --work-root "$WORK_ROOT/gpumd-source-pilot"
python3 tests/thermostats/reference_gpumd_harmonic.py \
  --stage confirm --ensemble nvt_nhc --work-root "$WORK_ROOT/gpumd-source-confirm"
python3 tests/thermostats/reference_lammps_harmonic.py \
  --lmp "$LAMMPS_CPU" --campaign pilot --work-root "$WORK_ROOT/lammps-pilot"
python3 tests/thermostats/reference_lammps_harmonic.py \
  --lmp "$LAMMPS_CPU" --campaign confirmation --work-root "$WORK_ROOT/lammps-confirm"
```

### GPUMD GPU 测试适配器

原版 GPUMD 的力常数势（FCP）减去全体系平均力。为表示这个独立外场，`build_gpumd_harmonic_native.py` 先在独立源码和构建目录编译原版，再构建测试适配器。适配器只在此测试中绕过 FCP 净力修正，提供 BDP 种子和 `compute2` 后的只读速度统计。NHC/SVR 公式、GPU 积分器和 FCP 的单精度位移、系数与力计算保持原样。测试接口只由适配器提供。

`GPU_IDS_2` 填 2 张已分配设备。按上文方法预检设备和通信后再跑 GPU 作业，使用新目录并保留原始样本和未通过的报告。统计状态非 `PASS` 时驱动返回非零。

```bash
python3 tests/thermostats/build_gpumd_harmonic_native.py \
  --work-root "$WORK_ROOT/gpumd-native" --cuda-arch 89 --jobs 16
GPUMD_ADAPTER="$WORK_ROOT/gpumd-native/gpumd-harmonic-native-build/gpumd-harmonic-test-adapter"
python3 tests/thermostats/run_gpumd_native_harmonic.py --candidate "$GPUMD_ADAPTER" \
  --devices "$GPU_IDS_2" --campaign smoke --work-root "$WORK_ROOT/gpumd-gpu-smoke"
python3 tests/thermostats/run_gpumd_native_harmonic.py --candidate "$GPUMD_ADAPTER" \
  --devices "$GPU_IDS_2" --campaign pilot --work-root "$WORK_ROOT/gpumd-gpu-pilot"
python3 tests/thermostats/run_gpumd_native_harmonic.py --candidate "$GPUMD_ADAPTER" \
  --devices "$GPU_IDS_2" --campaign confirmation --work-root "$WORK_ROOT/gpumd-gpu-confirm"
```

例中的 CUDA 架构 `89` 对应计算能力 8.9。已执行的固定预算、结果和测试接口证据见 [2026-11-03 跨引擎对照](../../docs/status/validation/thermostat-validation.md#thermostat-reference-controls-20261103)。
