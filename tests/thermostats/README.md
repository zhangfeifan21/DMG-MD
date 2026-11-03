# NHC / BDP 验收

本目录是 A3–A6 的标量、物理统计与 MPI 验收入口。当前实现合同见
[系综标准](../../docs/standards/ensembles.md)，实际执行结果与精确命令见
[NHC](../../docs/status/validation/thermostat-validation.md#nhc-20261102)、
[BDP](../../docs/status/validation/thermostat-validation.md#bdp-20261102)和
[双路径矩阵](../../docs/status/validation/thermostat-validation.md#thermostat-matrix-20261102)。
下列命令为复现示例；`CANDIDATE`、设备列表和新的证据目录应按本机替换。
每个新 shell 都先从仓库根目录加载 `../env/md-mpi.sh`。

## 构建与标量检查

```bash
source ../env/md-mpi.sh
export PYTHONDONTWRITEBYTECODE=1
cmake -S . -B /tmp/dmgmd-thermostats-build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/dmgmd-thermostats-build -j8
ctest --test-dir /tmp/dmgmd-thermostats-build --output-on-failure
```

CTest 注册 NHC fixture、SVR fixture、统计分析器及原有测试。NHC fixture 是三个独立 case，
各连续四次 half-step，比较因子与完整链状态的 double 位模式；SVR fixture 为 16 case，
覆盖 seed、奇偶 ndeg、Gaussian cache 和 `taut<=0.1` 分支。控制器测试同时检查
non-root 无链/RNG 分配、每段重置及 Berendsen 旧表达式。

`fixtures/generate_nhc.py` 与 `fixtures/generate_svr.py` 只读提取锁定上游函数，
在临时目录独立编译并生成 oracle；它们不把上游源码链接进 DMG-MD。
上游 NHC 对 m=0 有一次结果被覆盖的负索引读取，oracle 用 padding 消除该读的未定义行为；
本地实现直接在读取前分支，保留有效表达式和求值顺序。

```bash
python3 tests/thermostats/fixtures/generate_nhc.py \
  --reference-root ../gpumd-reference --output /tmp/nhc-oracle-new.json
python3 tests/thermostats/fixtures/generate_svr.py \
  --reference-root ../gpumd-reference --output /tmp/svr-oracle-new.json
```

参考目录必须只读；所有输入/输出和构建在仓库外的新临时目录。不得更新既有 golden 或容差。

## 环境、短程和失败关闭

```bash
python3 tests/mpi/check_environment.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md --devices 2,4,5,7
python3 tests/thermostats/run_thermostats.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md --devices 2,4,5,7 \
  --ensembles nvt_nhc nvt_bdp --sections short negative \
  --work-root /tmp/thermostats-short-new
python3 tests/thermostats/check_bdp_contract.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md --devices 2,4,5,7 \
  --work-root /tmp/thermostats-bdp-contract-new
```

短程 case 是 2016/5184 原子的 NEP4 diamond；前者自然触发 P2/P4 M1 fallback，后者进入 M2a。
每个同 rank/backend 的重复要求科学输出文件逐字节一致；跨 rank 与 GPUMD NHC 比较使用
既有 baseline 容差。BDP 不与 wall-clock seed 的 GPUMD 比较轨迹。
负例设置超时并核验非零退出、错误文字、没有最终 XYZ/restart；零 KE 用两个互不作用的静止原子，
以确保 VV2 后仍为零。seed 合同另测默认值、非法字符串、rank 间数字或原始字符串不一致，
以及多段/新作业 restart。

```bash
python3 tests/thermostats/check_protocol.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md \
  --probe /tmp/dmgmd-thermostats-build/tests/libdmgmd_ensemble_factor_probe.so \
  --ensemble nvt_bdp --devices 2,4,5,7 --work-root /tmp/thermostats-protocol-new
python3 tests/thermostats/check_variants.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md --devices 2,4,5,7 \
  --work-root /tmp/thermostats-variants-new
```

协议检查也接受 `--ensemble nvt_nhc`（默认）。LD_PRELOAD 探针仅包装 host 2-double Bcast，
不插入 collective 或 CUDA 操作；检查每 rank 的因子位模式，以及相对 Berendsen 的逐步通信增量。
NEP5/ZBL 脚本复用 long-NVE 锁定输入/转换，记录实际路由；BaTiO3 在 P2 为 M2a、P4 为 fallback，
不能按 rank 数猜测 data plane。

对上面的成功源记录，可补做每个组合的 5-step 新作业 restart 与两种系综的 P1→P4 恢复：

```bash
python3 tests/thermostats/check_restarts.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md --devices 2,4,5,7 \
  --sources /tmp/thermostats-short-new /tmp/thermostats-variants-new \
  --work-root /tmp/thermostats-restarts-new
```

它先核验源输入/输出哈希，再读取快照；不比较链/RNG 轨迹连续性。

## 统计合同

`manifest.json` 是输入哈希与 KE 分布参数的第一事实源，`velocity-manifest.json` 规定
实际相互作用 NEP 体系的三个速度方向矩；禁止从结果反选 seed、burn-in、误差带或预算。
NHC 的 seed 指独立初速；BDP 同时用作 thermostat seed。主统计组是 432 原子的相互作用 cluster，
显式 Gaussian 初速、不去质心；输出温度始终按 3N。

`sample_statistics.py` 估计 K 及平方离差的自相关和 ESS，报告完整固定长度块及 seed 间的
Student-t CI，取两者较大的 half-width。只有 CI 完全位于已冻结误差带且满足 ESS/块数才 PASS；
不能把 CI 与误差带相交当通过。三次预定预算用 Bonferroni 校正后的单次约 98.33% CI，
不声称整个多矩阵联合覆盖率为 95%。`--minimum-statistics-steps` / `--minimum-steps`
仅用于保留前一轮证据后的预定延长，不能替换 seed 或缩短 burn。

```bash
export DMGMD_ACF_HELPER=/tmp/dmgmd-thermostats-build/tests/dmgmd_autocorrelation
python3 tests/thermostats/run_thermostats.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md --devices 2,4,5,7 \
  --ensembles nvt_bdp --sections statistics --parallel-seeds 4 --timeout 1800 \
  --work-root /tmp/thermostats-statistics-new
python3 tests/thermostats/check_velocity_statistics.py \
  --candidate /tmp/dmgmd-thermostats-build/dmg-md --ensemble nvt_bdp \
  --devices 2,4,5,7 --work-root /tmp/thermostats-velocity-new
```

KE runner 覆盖 P1、M1 P2/P4、M2a P2/P4 的双后端；减半步长保持物理耦合时间和采样时长；
BDP 另执行八组 GPUMD 分布对照。NHC 用同样入口的 `--ensembles nvt_nhc`。
并行 seed 只分配不重叠 GPU 组，不是性能 benchmark；MPI 默认 CPU 绑定可能使多个独立作业竞争
同一核心，需在操作记录中说明实际 affinity。

分析器的 native ACF helper 只加速测试分析，不参与生产。若设置该环境变量但 helper 无效则失败，
不静默改算法；不设置则用 Python 相同估计器。`check_velocity_statistics.py --analyze-existing --work-root ROOT`
只读核验已有数据并创建新 reanalysis 文件，原报告保留。

`run_harmonic.py` 与 `harmonic_sampler.cpp` 提供独立可解模型的 K oracle 和额外轴向诊断，
默认 NHC，可选 `--ensemble nvt_bdp`；模型积分仍使用同一独立 harmonic VV。
`harmonic-confirmation.json` 保留 NHC 独立固定预算确认轮。总 K PASS 不能掩盖轴向
INCONCLUSIVE，完整结果见各恒温器记录。该模型不构成当前生产 NEP 势支持范围或普遍遍历性保证。
`--parallel-seeds N` 可并行运行独立 seed 子进程，要求 N 为正整数，默认 `1`（串行）。
记录和统计仍按 manifest 中的 seed 顺序生成；该选项不改 seed、参数或统计口径。

```bash
python3 tests/thermostats/run_harmonic.py \
  --candidate /tmp/dmgmd-thermostats-build/tests/dmgmd_harmonic_sampler \
  --ensemble nvt_bdp --parallel-seeds 4 --work-root /tmp/thermostats-harmonic-new
```

## 相同 harmonic 模型的跨引擎对照

对照继续读取 `manifest.json` / `harmonic-confirmation.json`，保留全部初态、采样预算、
burn、CI、ESS 和误差带。共享初态按 C++ `mt19937_64` / `normal_distribution` 逐 mode
先取位置、再取速度；不使用各引擎自己的 velocity 初始化替代。LAMMPS 的 `units lj`
使用 `T=k_B*300`、mass=1、24 个自由度；恒温器和 thermo 均不扣除质心自由度。
NHC 链长为 4，LAMMPS 的链积分和初始热浴状态仍采用其自身实现，不要求跨引擎随机轨迹相同。

`reference_gpumd_harmonic.py` 从锁定 GPUMD 源码抽取未改动的 NHC/SVR 函数，
在独立 CPU driver 中供给谐振力；它不是原生 GPUMD 运行。NHC 的前后半步均保留
GPUMD 的温度到动能往返计算。BDP 每个 seed 使用新进程，隔离上游函数静态 Gaussian cache。

```bash
export DMGMD_ACF_HELPER=/tmp/dmgmd-thermostats-build/tests/dmgmd_autocorrelation
python3 tests/thermostats/reference_gpumd_harmonic.py \
  --stage pilot --work-root /tmp/gpumd-harmonic-source-pilot-new
python3 tests/thermostats/reference_gpumd_harmonic.py \
  --stage confirm --ensemble nvt_nhc --work-root /tmp/gpumd-harmonic-source-confirm-new
python3 tests/thermostats/reference_lammps_harmonic.py \
  --lmp /absolute/path/to/cpu-extra-fix-build/lmp \
  --campaign pilot --work-root /tmp/lammps-harmonic-pilot-new
python3 tests/thermostats/reference_lammps_harmonic.py \
  --lmp /absolute/path/to/cpu-extra-fix-build/lmp \
  --campaign confirmation --work-root /tmp/lammps-harmonic-confirm-new
```

LAMMPS 必须包含 `EXTRA-FIX` 的 `temp/csvr`；预留的 NEP/Kokkos 二进制未必包含此包。
需要构建时，从锁定 reference 导出独立源码，在新目录启用 `PKG_EXTRA-FIX=on`；
不得修改或原位构建 reference。CPU-only 对照可关闭 MPI、OpenMP 和 Kokkos。
LAMMPS driver 的 `split-validation` 比较输出设置所需的 `run 1` / `run N-1` 与单段运行；
这不是对 DMG-MD 分段隐藏状态的测试。

GPUMD 的 stock FCP 会减去全体系平均力，不能直接代表该独立外场。
`build_gpumd_harmonic_native.py` 在独立源码/构建目录先构建原版，再生成测试适配器：
只为此外场绕过 FCP 净力修正，提供 BDP seed 和 `compute2` 后的只读速度统计。
NHC/SVR 公式和 GPU 积分器不改；FCP 位移、系数、力计算仍使用原生 float 路径。
适配器不接入 DMG-MD 生产构建，不把修改后的测试接口声明为 stock GPUMD 输入能力。

```bash
python3 tests/thermostats/build_gpumd_harmonic_native.py \
  --work-root /tmp/gpumd-native-harmonic-new --cuda-arch 89 --jobs 16
python3 tests/thermostats/run_gpumd_native_harmonic.py \
  --candidate /tmp/gpumd-native-harmonic-new/gpumd-harmonic-native-build/gpumd-harmonic-test-adapter \
  --devices 4,5 --campaign smoke --work-root /tmp/gpumd-native-harmonic-smoke-new
python3 tests/thermostats/run_gpumd_native_harmonic.py \
  --candidate /tmp/gpumd-native-harmonic-new/gpumd-harmonic-native-build/gpumd-harmonic-test-adapter \
  --devices 4,5 --campaign pilot --work-root /tmp/gpumd-native-harmonic-pilot-new
python3 tests/thermostats/run_gpumd_native_harmonic.py \
  --candidate /tmp/gpumd-native-harmonic-new/gpumd-harmonic-native-build/gpumd-harmonic-test-adapter \
  --devices 4,5 --campaign confirmation --work-root /tmp/gpumd-native-harmonic-confirm-new
```

GPU 作业需在可访问驱动的环境运行，并先通过项目环境门。所有输出目录必须为新目录，
统计非 PASS 返回非零；原始样本和非通过报告均保留。
固定预算的实际结果和测试接口有效性见
[2026-11-03 跨引擎对照](../../docs/status/validation/thermostat-validation.md#thermostat-reference-controls-20261103)。
