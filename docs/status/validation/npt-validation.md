# NPT 验收与证据汇总

本页整合 B0–B4 与 G-B 相关的压力、盒变更、Berendsen、SCR、输出/restart、安全负例、回归及验收矩阵记录。各日期节保留当时执行身份、命令、结果和限制；较早状态不代表当前状态。

**当前结论：**截至 2026-11-04，B0–B4 与 G-B 在所列范围 PASS。2026-11-03 原注册18个 formal looks 的总体结论仍为 INCONCLUSIVE；2026-11-04 全新160条完整链独立确认 PASS。

**支持范围：**结论严格限于冻结 NEP-C、216 原子、300 K/0 GPa、单节点全量复制模式（replicated-full）、各向同性正交全周期盒及明确矩阵；不能外推到普遍 NPT、NEP5/ZBL 统计、RDMA 或跨节点。

**证据保留：**文档化 evidence 包目前保留最终 paired-analysis、注册 manifest/protocol 与原18-look机器索引。原始测试数据仍保存在 ignored 的 `artifacts/validation/npt-scr-gb/` 下（包括 `pre-reboot-archive`、`golden`、`restart-contract`、`diagnostics` 和 `n3-formal`）；2026-11-05 移除的是 `docs/status/validation/evidence/` 中1035份中间归档包。部分过程快照已清理，因此不能声称可从当前保留材料完整重放。下文的 `/tmp` 路径和工作目录仅记录历史执行位置，不是当前文件定位。

## 索引

- [压力 oracle（2026-11-03）](#pressure-oracle-20261103)
- [盒变更合同（2026-11-03）](#box-contract-20261103)
- [NPT Berendsen（2026-11-03）](#npt-ber-20261103)
- [SCR 入口与早期合同（2026-11-03）](#npt-scr-20261103)
- [SCR 原18-look最终冻结（2026-11-03）](#npt-scr-final-20261103)
- [SCR 独立确认（2026-11-04）](#npt-scr-confirmation-20261104)
- [输出/restart合同（2026-11-03）](#npt-output-20261103)
- [旧路径回归（2026-11-03）](#npt-regression-20261103)
- [迁移后复核（2026-11-04）](#npt-regression-20261104)
- [验收矩阵（2026-11-04）](#npt-matrix-20261104)


<a id="pressure-oracle-20261103"></a>
## 2026-11-03 — 压力 oracle

本节记录 B0 的压力单位、符号、输出列与 virial 布局验收。B0 不改 runtime；后续控压器以这里锁定的定义为基准。


**结论：PASS。** CPU 合成值直接经过生产 `write_thermo_row` / `write_xyz`；五组单 rank、同输入、零时间步 DMG-MD 与锁定 GPUMD 输出逐项一致。压缩/拉伸方向以已实测的近零压参考态判定，结果相反；不把该构型的有限采样推广为普遍统计结论。

### 定义与布局

- 内部温度使用 `3N k_B`，不减质心自由度。六个内部应力为 `(virial + kinetic) / V`，归一化后单位是 eV/Å³；应力行列只在 `thermo.out` 写出时乘 `PRESSURE_UNIT_CONVERSION = 160.2177`，成为 GPa。项目锁定 `K_B = 8.617343e-5 eV/K`；解析理想气体 oracle 也使用该常数，避免以不同的 CODATA 常数制造假误差。
- 内部六分量顺序是 `sxx syy szz sxy sxz syz`。生产 `thermo.out` 顺序是 `T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz`，因此应力输出索引为内部 `[2,3,4,7,6,5]`。由真实 writer 输出的非对称合成值逐列验证了排列和单次单位换算。
- NEP 的内部每原子 virial SoA 分量顺序为 `xx yy zz xy xz yz yx zx zy`。`dump_xyz` 每原子九列通过生产 writer 输出为矩阵行序 `xx xy xz yx yy yz zx zy zz`；frame `virial` 与 `stress` 同样按 3×3 行序写出。合成九分量彼此不同，直接检查了生产输出文件的所有列。剪切压力是物理应力（eV/Å³/GPa），每原子 virial 是 eV；二者语义不同。

生产实现核对点：`src/runtime.cu::find_owned_thermo_sums`、`src/runtime_internal.hpp::normalize_global_thermo`、`src/runtime.cu::write_thermo_row`、`src/runtime.cu::write_xyz`；锁定 GPUMD 对照为 `../gpumd-reference/src/integrate/ensemble.cu::Ensemble::find_thermo` 与 `../gpumd-reference/src/measure/dump_xyz.cu::Dump_XYZ::output_line2`。GPUMD 内部 virial SoA 注释见 `../gpumd-reference/src/force/dp.cu`。

### 执行结果摘要

理想气体为 8 个 C 原子、24 Å 立方盒，最小周期原子间距 8 Å，大于 NEP 截断；速度的三个分量平方和相同且交叉和为零。解析式 `P = N k_B T / V` 给出 `7.204013176986109e-5 eV/Å³ = 0.011542104219864074 GPa`。DMG-MD 输出温度 `1444.5908408 K`，三对角压力均值为 `0.011542104219 GPa`，相对误差 `7.486266679093696e-11`（门槛 `<1e-10`）。GPUMD 与 DMG-MD 的六压力列一致。

当时使用 NEP 碳金刚石 8 原子构型构造近零压参考态，立方晶格常数 `3.57 × 1.0023 Å`，零速度；GPUMD 三个对角压力绝对值均值为 `7.794523938033333e-4 GPa`（预设近零界限 `0.1 GPa`）。相对此态各向同性压缩 0.5% 后，平均对角压力增量为 `+6.490716484406197 GPa`；各向同性扩张 0.5% 后为 `-6.248679272827136 GPa`。这确认当时参考态附近的响应方向，并未对任意非平衡初态作符号假设。

另一个剪切 case 对同一构型施加 `gamma_xy = 0.025`，GPUMD `thermo.out` 非对角压力中 `sxy = -12.796835995 GPa`，验证了非零物理剪切分量。GPUMD/DMG-MD 在五个 case（理想气体、近零参考、压缩、扩张、剪切）中比较了六个 thermo 压力列、frame `virial/stress` 的 9 分量、所有原子的每原子 9 分量 virial；共 480 个分量比较全部通过。压力列最大绝对误差为 `0 GPa`，frame tensor 最大绝对误差为 `2.220446049250313e-16`（相应 eV 或 eV/Å³），per-atom virial 最大绝对误差为 `0 eV`。误差门槛沿用 `tests/baseline/manifest.json`：thermo stress `1e-5 GPa + 1e-4 × |reference|`，XYZ stress `1e-8 eV/Å³ + 1e-4 × |reference|`，virial `1e-6 eV + 1e-4 × |reference|`；既有 golden 未变、未放宽。

### 命令、构建与产物

基线 binary 在添加 B0 测试前独立构建，当时用于后续 B1–B4 旧路径零变化对照：

```bash
source ../env/md-mpi.sh
cmake -S . -B /tmp/dmgmd-b0-b4-20261103/build \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=75
cmake --build /tmp/dmgmd-b0-b4-20261103/build -j 8
```

冻结 DMG-MD binary：`/tmp/dmgmd-b0-b4-20261103/build/dmg-md`，SHA-256 `9cd23b28d693e1d776e7da6a1e1bf06def88f821c0752df08bfd7c649bf0b962`。构建环境为 GCC 11.4、CUDA 12.9.86、项目 `../env/md-mpi.sh` 指定的 Open MPI+UCX。物理对照命令（GPU 0）：

```bash
source ../env/md-mpi.sh
python3 tests/thermo/run_pressure_oracle.py \
  --candidate /tmp/dmgmd-b0-b4-20261103/build/dmg-md \
  --reference ../gpumd-reference/src/gpumd --device 0 \
  --output /tmp/dmgmd-b0-b4-20261103/pressure-gpu0-final
```

GPU 为 NVIDIA GeForce RTX 4090，driver `580.173.02`。参考 checkout 为 GPUMD commit `9d23496e41319b9e2af5221a7df6285387401d1e`、binary SHA-256 `3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`。当时的 runner 在 JSON 中记录每个 case 的 `model.xyz`、势文件、`run.in`、两引擎原始 stdout/stderr、`thermo.out`、`pressure.xyz` 哈希；JSON 的历史路径为 `/tmp/dmgmd-b0-b4-20261103/pressure-gpu0-final/pressure-oracle.json`（SHA-256 `2bfdce6fb892a092df425231cf2b0a6970fa51eb70c863f5787fc37e58fa0559`），含 5 个 case 的输入/输出精确 SHA 与 480 项逐分量误差记录。命令终端日志当时写入 `/tmp/dmgmd-b0-b4-20261103/pressure-gpu0-final/run.log`，SHA-256 同 JSON。首次 GPU0 尝试时显存仅剩 113 MiB，GPUMD 启动分配 OOM；该尝试未产生数值数据。显存释放后以同一 frozen binary 在 GPU0 重跑并 PASS；OOM 原始日志当时写入 `/tmp/dmgmd-b0-b4-20261103/pressure-gpu0/run.log`。

生产 host writer 测试由 CUDA 编译，直接链接 `dmgmd_runtime` 并调用 `write_thermo_header`、`write_thermo_row`、`write_xyz`，没有复制生产格式化代码。CPU 验证命令：

```bash
source ../env/md-mpi.sh
cmake --build /tmp/dmgmd-b0-b4-20261103/build -j 8 --target dmgmd_pressure_writer_tests
ctest --test-dir /tmp/dmgmd-b0-b4-20261103/build \
  -R 'dmgmd.pressure_(oracle_cpu|writer)' --output-on-failure
```

结果为 2/2 PASS；CTest 日志当时写入 `/tmp/dmgmd-b0-b4-20261103/pressure-writer-ctest.log`，SHA-256 `893ec2b91b1bdceb977fd5bad5feea1b514472fda6351993a5c3f941b74c7472`。没有执行整套既有测试；host writer 只需 CPU 即可运行。

### Reference 状态与覆盖边界

测试前快照路径为 `/tmp/dmgmd-b0-b4-20261103/freeze-before.txt`（SHA-256 `a1dce45abc6716d6e06830e1eb71e5b796f8f3a498eb5321e99f5bfdb2f70fa6`），测试后快照路径为 `/tmp/dmgmd-b0-b4-20261103/freeze-after.txt`（SHA-256 `ae65f9fa8528d4cf71b7ee8ced450959303a3b78f45fa2ebce98a0f83a4b9dc7`）。测试前 freeze 记录了各 reference 的 ignored/untracked 数量；完整路径清单当时写入 `/tmp/dmgmd-b0-b4-20261103/reference-ignored-after.txt`。由于测试命令从未以 reference checkout 为工作目录，也没有任何会写入 reference 的命令，前后清单保持相同；`reference-ignored-before.txt` 当时作为该未变清单的副本，用于逐行 diff，两份 SHA-256 均为 `4a151ad41b31598c00ca2780bfea5840a153616785cb479aa664318d0a9d1295`。GPUMD、LAMMPS、MatPL 三个只读 checkout 的 HEAD 前后一致，tracked dirty 均为 0；GPUMD ignored/untracked 数量前后均为 208，LAMMPS 与 MatPL 均为 0。GPUMD 与当时记录的 LAMMPS 可执行 SHA 前后也一致；所有运行 cwd 与输出均位于 `/tmp`，未在 reference checkout 构建或写输出。当时主仓原有 ignored build/archive 目录保持原样，Python 验证生成的临时 `tests/thermo/__pycache__/` 当时已清除。

未覆盖：LAMMPS 交叉运行；压力误差对尺寸、温度、势种类和各向异性盒的推广；NPT/barostat；统计系综遍历性。B0 只锁定当时验收的 NEP 单 rank 压力 oracle，不代表 NPT 已支持。


<a id="box-contract-20261103"></a>
## 2026-11-03 — 盒变更合同

构建目录为 `/tmp/dmgmd-b0-b4-20261103/build-b1`，与 B0 冻结构建隔离。完整链接后的 `dmg-md` SHA-256 为 `4fd8d63c2d6db13fe8706a8e2745fcb0b25e66ae4dc17c203abe23f661c1f2e7`.

以下命令均在仓库目录加载 `source ../env/md-mpi.sh` 后执行；命令及输出路径记录当时执行位置：

- `cmake --build /tmp/dmgmd-b0-b4-20261103/build-b1 -j4` — 最终修改后完整构建通过，包含 `dmg-md` 重新链接。
- `/tmp/dmgmd-b0-b4-20261103/build-b1/tests/dmgmd_box_control_host_tests` — 固定 seed 的正交几何往返、逆变换一致性和无效输入拒绝均通过。输出：`/tmp/dmgmd-b0-b4-20261103/b1-host.log`。
- `CUDA_VISIBLE_DEVICES=0 /tmp/dmgmd-b0-b4-20261103/build-b1/tests/dmgmd_box_control_tests` — GPU 位置预检与缩放、padding 保留、有效大盒缓存失效、缩放坐标与新邻居表对照、所有权哈希不变、厚度刷新、快照合同及拒绝操作原子性均通过。输出：`/tmp/dmgmd-b0-b4-20261103/b1-gpu.log`。
- `python3 tests/baseline/run_baselines.py --candidate /tmp/dmgmd-b0-b4-20261103/build-b1/dmg-md --device 0 --keep-work` — 冻结基线 4 个用例全部通过。输出：`/tmp/dmgmd-b0-b4-20261103/b1-baseline-final.log`；工作目录当时为`/tmp/dmgmd-gpumd-baseline-hga556n4`。
- `python3 tests/long_nve/run_long_nve.py --candidate /tmp/dmgmd-b0-b4-20261103/build-b1/dmg-md --devices 0 --profile smoke --cases carbon_crystal --sections short,nvt --keep-work --report /tmp/dmgmd-b0-b4-20261103/build-b1/long-nve-b1-final.json --ui plain` — 通过。输出：`/tmp/dmgmd-b0-b4-20261103/b1-long-smoke-final.log`；工作目录当时为`/tmp/dmgmd-long-nve-smoke-pzicz_a5`。

较早的一次完整 smoke 使用最终链接前的可执行文件；应最终重新链接的要求主动停止。该次已被后续结果取代，不是测试失败；部分工作目录当时为 `/tmp/dmgmd-long-nve-smoke-tn8vta5r`.

最终实现涉及 `include/dmgmd/box_control.hpp`、`src/box_control.cpp`、`src/box_control.cu`、`src/runtime.cu`、`src/gpumd_compat/neighbor.cuh`、`src/gpumd_compat/neighbor.cu` 和 `src/gpumd_compat/nep.cuh`；构建和测试登记位于根目录及 tests 目录下的 `CMakeLists.txt`。

独立 GPU 缓存测试包含两种情形：（1）有效大盒故障注入，仅修改盒并验证一次性通知会触发重建；（2）执行真实各向同性缩放后发出通知，并验证邻居行与新建 `Neighbor` 实例一致。仅改盒的故障注入不代表仿射 NPT 缩放。

B1 未加入 runtime NPT 路径。盒缩放原语仅供后续接入；runtime 现有 NVE/NVT 路径保持不变。


<a id="npt-ber-20261103"></a>
## 2026-11-03 — Berendsen 集成与重启分类


B2 `npt_ber` 候选通过冻结的 200 步 GPUMD golden、P1/P2/P4 两种通信后端下的全量复制模式路由矩阵、压力响应方向、负例诊断和重启输出检查。本节记录兼容与输出合同，不证明 NPT 严格采样。B3 SCR 长程统计在 2026-11-03 本次 B2 执行时尚未开始；后续统计结果见 SCR 节。

候选构建命令为 `source ../env/md-mpi.sh && cmake --build /tmp/dmgmd-b0-b4-20261103/build-b2 -j4`。构建日志 `/tmp/dmgmd-b0-b4-20261103/b2-build-final.log`；候选 `/tmp/dmgmd-b0-b4-20261103/build-b2/dmg-md` SHA-256 `52a535ad91ad5445741bc3f9cabe6975231553198bd71ca097cbb16a26bea617`。锁定 GPUMD `../gpumd-reference/src/gpumd` SHA-256 `3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`。候选、GPUMD、输入、脚本和 baseline 容差清单冻结于 `/tmp/dmgmd-b0-b4-20261103/npt-b2/freeze-b2-v6.json`（SHA-256 `fd7a0c6c2b72a49683a7a70ed1576855a3eb94fe21f2a8505a3ed32769d1076a`）。NEP-C 势文件 SHA-256 `add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623`。

正式运行命令：

```bash
source ../env/md-mpi.sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/npt/run_npt_ber.py \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b2/dmg-md \
  --reference ../gpumd-reference/src/gpumd \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b2/freeze-b2-v6.json \
  --devices 4,5,6,7 \
  --work-root /tmp/dmgmd-b0-b4-20261103/npt-b2/results-v6
```

环境门通过：Open MPI 5.0.10、UCX 1.22.0，CUDA-aware `cuda_copy/cuda_ipc` 探针通过。串行矩阵只使用获批 GPU 4–7。结果 `/tmp/dmgmd-b0-b4-20261103/npt-b2/results-v6/results.json` SHA-256 `6eb464301795d6b90aee9244a010321cddeb9c4b3d53ef007bbfea4569b6eb71`，共 40 项：29 PASS、11 项预期负例。对输入和输出摘要独立复算，302 个文件全部匹配。

216 和 2016 原子的 200 步 Ber 测试均显式输入速度，GPUMD 与候选使用相同 `model.xyz`、`run.in` 和势文件，并沿用现存 `tests/baseline/manifest.json` 容差，两组均通过。216 原子：thermo 温度最大差 0 K、六列应力 0 GPa、盒长 0 Å；XYZ 位置最大差 `3.56e-15 Å`、力 `2.22e-16`、每原子 virial `2.67e-15 eV`。2016 原子 thermo 应力最大差 `1.00e-16 GPa`，位置和 virial 完全一致。既有 golden 与阈值未改。

路由矩阵覆盖 216 和 5184 原子（局部域分解模式 local-domain 适用体系）的 P1/P2/P4 与 HostStaged、CudaAware，共 12 个候选作业。每次均输出 `DMGMD_DOMAIN mode=m1 reason=npt-m1-only`，并与对应 GPUMD 及候选 P1 结果相符，确认符合局部域分解条件的尺寸运行 NPT 时仍路由到全量复制模式。

压缩体系首步平均对角压力 `+7.23075895627 GPa`，盒子首步扩张；扩张体系首步压力 `-5.53071254670 GPa`，盒子首步收缩。`-1 GPa` 目标压力被接受并与 GPUMD 对照。`B=2001 GPa` 固定盒分支的 thermo 晶格最大变化 `1.78e-15 Å`；该用例没有 restart 命令，故用最后一条 thermo 盒信息核验。

重启输出以轨迹最后一帧为参照。高精度 XYZ 的 Lattice、位置、速度分别按 `.6g` 格式化后，与 `restart.xyz` 每个 token 完全一致，符合 writer 的 C `%g` 合同。P4 HostStaged 缩放后 restart 也由新 P4 作业成功读入。这验证序列化精度和新作业可读性，不声称轨迹逐位连续。

独立冻结的 restart 分类固定 `T1=T2=300 K`、`dt=0.25 fs`、目标压力 0 GPa 和显式 216 原子 seed-101 初态，比较单段 40 步、同一进程两段 20 步（第二段前重声明按段生效的 dump 命令），以及文件 restart 后新作业续跑 20 步。freeze `/tmp/dmgmd-b0-b4-20261103/npt-b2/restart-freeze-v3.json` SHA-256 `e11bae6b62aa8b0222a5d7f93edb1b73c0f47d4c9682e4560c51a0bb5237b234`；结果 `/tmp/dmgmd-b0-b4-20261103/npt-b2/restart-classification-v3/results.json` SHA-256 `0e768f9fb2b72f7cba775fe2a922478766821e7e323bfa2511260a28a165c34c`。相对单段后 20 步，进程内分段 thermo 最大绝对差 `8.595e-5`，文件 restart 最大差 `6.61541e-4`；最终位置/速度分量最大差分别 `2.9822e-8`、`8.0208e-6`。当时的 JSON 记录了首次数值差和完整指标，不套用连续性阈值。首版未在第二段前重复 dump 声明；当时的 runtime 会在 run 边界清空 measurements，因此当时将其标记为 superseded 证据。冻结 v3 当时在第二段前重声明输出命令。

11 个负例当时覆盖非有限初温、零体模量、零压强耦合时间、12/18-token 格式、非周期和三斜盒、P1/P4 非正缩放因子，以及 P1/P4 零瞬时动能温度。当时每例均在超时前非零退出、匹配专属诊断、无数值 thermo 行且无 trajectory/restart。危险缩放例报告 `step=1 P_GPa=10000 ... factor=-3332.09`；零动能例报告 `NPT Berendsen instantaneous temperature must be finite and positive`。P4 作业当时在所有 rank 输出 MPI abort 信息，并由 root 输出诊断。当时的 runner 将 CUDA OOM、PMIx/ORTE 错误作为基础设施失败，不能误记负例通过。

当时检查结果显示三个 reference checkout 均未变化。含 ignored path 的前后清单为 `/tmp/dmgmd-b0-b4-20261103/npt-b2/reference-status-before-v6.json`、`reference-status-after-v6.json`；字节相同，SHA-256 均为 `16076f165b8895cc38b643de85799478b20ad01397e568ba6a2a0dae35315567`。GPUMD HEAD `9d23496e41319b9e2af5221a7df6285387401d1e`，208 个 ignored path（清单 SHA `7314f1304d44a8fee9cf50ce62bf369f93e160eebf63783603086c875e21f405`）；LAMMPS HEAD `611ca3b7f8525ba802373b04f9e3d632d515f7e3`；MatPL HEAD `21fabf34eadc57a717f4c7fbce9bb80aad5fd991`。当时后两者 tracked 状态干净且 ignored 文件数为 0。当时快照逐项列出 ignored path；执行未写入 reference checkout。

证据加固期间，候选 SHA 变化、GPUMD 相对路径修正、输出精度合同和 `box_control` 诊断匹配使中间 freeze 被取代。部分日志当时写入 `/tmp/dmgmd-b0-b4-20261103/npt-b2/results-v*`；完整 B2 矩阵以 v6 为准。v5 首次非周期诊断正则不匹配是测试预期文本错误，不是模拟或基础设施失败。

离线验证通过：

```bash
source ../env/md-mpi.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/npt/test_manifest.py
python3 -m py_compile tests/npt/*.py
git diff --check
```

2026-11-03 本次 B2 执行未覆盖 B3 SCR 八 seed 统计、半步长收敛、独立 EOS 响应斜率或 B4 多 rank/backend 完整 restart 矩阵；本节也不作更广泛的 NPT 采样结论。425 GPa 是 barostat 控制参数；同条件 GPUMD 一致代表兼容性结果，不是普遍严格采样证明。


## SCR 统计历史与独立确认

截至 2026-11-04，独立确认科学判定 PASS，B0–B4/G-B 最终验收见[最终确认](#npt-scr-confirmation-20261104)及[独立索引](npt-scr-n3-confirmation-index.json)。以下 2026-11-03 进度与18-look结果均为历史记录。

<a id="npt-scr-20261103"></a>
### 2026-11-03 进度与预注册条件

本节记录 B3 的预注册 SCR 多种子验证。统计兼容结论以同条件 GPUMD 数据为参照；理论平衡量作为独立诊断。即便两个引擎相互一致，也不据此宣称遍历性或对所有体系成立的严格采样正确性。若共同偏离理论目标，将如实报告，不单独阻断引擎兼容结论。

模型为周期 216 原子 diamond，初态和模型随机种子为 101–108。base 使用 `dt=0.25 fs`、`Tdamp=40 step`、`tau_p=200 step`，固定前缀 look 为 20k、40k、80k；half 使用 `dt=0.125 fs`、`Tdamp=80 step`、`tau_p=400 step`，look 为 40k、80k、160k。GPUMD 每个 profile/seed 运行一次最大预算单段，candidate 在固定初态和同一 seed 下整段重跑后取前缀；不得从 restart 追加或拼接。每步写 thermo，不写 XYZ。base 条件为 P1/HostStaged、P2/HostStaged、P2/CudaAware、P4/HostStaged、P4/CudaAware；half 仅 P1/HostStaged。

冻结的 burn、block、ESS、三 look CI、均值/方差和分布等效阈值沿用已审定 manifest，正式结果完成后按冻结 analyzer 报告。ESS/区块门不足或任一 look 超阈值均会原样披露；不能看结果后改变 seed、参数、look 或阈值。

候选 binary 当时为 `/tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md`，SHA-256 `c3c9cd0b81a3f18ebbd8f4379dfbd3dbf929785eb5aa883fa4730b025d70bcdf`；锁定 GPUMD reference binary SHA-256 `3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`。科学 freeze 的历史路径为 `/tmp/dmgmd-b0-b4-20261103/npt-b3/freeze-b3.json` SHA-256 `d9b1ca661cb8c6963b99c968e27d4b952182ab6dd90b1c57584e19ce8596734c`，记录 171 个注册输入。execution-only amendment v2 SHA-256 `351dc0bc1ebf75ce49b2f961f3d4f0b99ed3aae55d319323c17b715db0b5e526`；v1 的首次 candidate 启动因 `DMGMD_COMM_LOG_INTERVAL=0` 被运行时拒绝，没有产生数值结果，原始结果当时记录。修正后正式链使用正 interval，科学参数未变。

执行 amendment v3 的历史路径为 `/tmp/dmgmd-b0-b4-20261103/npt-b3/freeze-b3-execution-v3.json`，SHA-256 `19fb3d55aae543adc33d0bdc45dcbc30843c15f0fbb1011c52e9dabe88c5c4cd`。它保留 science freeze 的原始冻结时间和 `formal_results_seen_before_freeze=false` 父字段，并将自身顶层说明为已有执行输出、尚无正式统计结果。v3 当时收录环境证明及活动结果清单；另预建 half-reference 的 `half-reference-s102` 空目录作为安全停止标记。runner 的 `stage()` 先执行 `target.mkdir(exist_ok=False)`，再复制输入或启动子进程；seed 101 会自然完成并记录，之后未启动的 seed 102–108 由 fresh work root 分片补齐。停止证据的历史路径为 `/tmp/dmgmd-b0-b4-20261103/npt-b3/half-reference-gpu7/scheduled-stop-after-s101.json` SHA-256 `fc101dc74559471410425e3777656c6f9bfb551d1a72b7b2a1dcaf5b8630542d`，记录活动 PID、thermo 快照行数/哈希和预留 seed；没有向活动 GPUMD 发送信号。

GPUMD release binary 的 RNG 初始化通过 `nm`/`objdump` 确认为 `system_clock::now()` epoch count；内部 seed 不可直接观测。reference runner 串行启动并记录 UTC、PID、命令及完整输入哈希；UTC 秒间隔仅用于减少时钟种子碰撞风险，不代表观测到内部随机种子。Native ACF helper 为 `/tmp/dmgmd-b0-b4-20261103/build-b2/tests/dmgmd_autocorrelation`，SHA-256 `ffa85c62195c6886c40510024ffd60ecea30531fc0851242208f0fc796db9761`，source `tests/thermostats/autocorrelation.cpp` SHA-256 `3453c32097ae4e80e8bbcd20fb805159614802bf694a6a32d24ce9cb1c2ac1b9`。

截至 2026-11-03 06:14 UTC，base 与 half 的 GPUMD reference 都已完成 8 个最大预算单段 seed；half P1/HostStaged candidate 也已完成 8 个 160k 单段 seed。全局账本 `/tmp/dmgmd-b0-b4-20261103/npt-b3/execution-schedule.json` 保持唯一 `(profile,engine,ranks,backend,seed,actual_steps)` key，不重复科学链；实际 GPU 可见性与 PID 只从 escalated 设备环境核验。

half P1/HostStaged 的三个 look records 已从同一批 16 条完整 160k 链生成，build_records 对实际输入/thermo SHA 与 8 个 reference 启动秒进行了校验：40k `/tmp/dmgmd-b0-b4-20261103/npt-b3/records-half-p1-hoststaged-look40k.json` SHA-256 `a9f3456a7554947fe8c5fbcbcfa02df87a613f33b0f3e41d4c58f67b917fd3d4`；80k `records-half-p1-hoststaged-look80k.json` SHA-256 `98d57bc12dcad5f1caded2202734a8fd0ab3dcf112c170a7ae4bee7d9c8319bc`；160k `records-half-p1-hoststaged-look160k.json` SHA-256 `1d8de57de30fd23ebc637a484c086c731fbaa8f9904232327a9356f3ad42fada`。三份均包含 16 个 PASS records；已交独立 frozen analyzer 按注册顺序分析。

base P1/HostStaged 的 20k 旧候选链和 reference 首 look 已分析为 `INCONCLUSIVE`（P/V pooled ESS 未达 1000，T mean/variance pass），当时的分析结果路径为 `/tmp/dmgmd-b0-b4-20261103/npt-b3/analysis-final/base-p1-hoststaged-look20k.json`。随后按批准计划跑了 8 个完整 80k candidate 单段，以同一批 base GPUMD references 生成 40k records SHA-256 `6a6b1914a136b3d781c6f7615d2e34b0057a8c285c0e062eb7ae0b9d1d2edf51` 与 80k records SHA-256 `bca58c05fd22c8a429e473318f0fb4702ab06d3e8b50c8d723ce2d450f16187d`。该新单段链的额外 20k diagnostic records 为 `records-base-p1-hoststaged-look20k-full80k-chain.json` SHA-256 `4e450d057088582fc029a2a6ed5ac547f2cfdbc2ea584d74055e27dc7e71f1fd`；它不替代旧 20k 正式结果，也不增加或替换注册 look。

旧 20k 与新 80k candidate 的 20k thermo 前缀没有逐字节匹配，故不拼接或复用前一条链。八个 seed 的 model.xyz 与 nep.txt SHA 均一致；thermo 均在数值列出现末位差。旧链使用 `CUDA_VISIBLE_DEVICES=6`、`mpirun -n 1`（无 `--bind-to none`），`DMGMD_COMM_LOG_INTERVAL` 未显式设置；新链 seed101–104 使用 GPU5、105–108 使用 GPU6，命令含 `--bind-to none` 且 `DMGMD_COMM_LOG_INTERVAL=1000`。这里只记录执行差异，不推断误差成因。正确映射后的派生比较 `/tmp/dmgmd-b0-b4-20261103/npt-b3/base-p1-20k-prefix-mapped-diagnostic.json` SHA-256 `b367cc807fe4f5fbe1ab21824369ed98f58b0139d41edf30ba5e84495c55615e`：18 列顺序为 T、KE、PE、六个 stress 分量、九个 box 分量；scalar pressure 为前三个对角应力的均值，volume 为 box 矩阵行列式。更正此前口头摘要中将原始列差直接称作物理量差的错误：18 列顺序为 T[0]、KE[1]、PE[2]、stress[3:9]、box[9:18]；scalar pressure 按 `(sxx+syy+szz)/3` 从列3–5计算，volume 由列9–17的盒矩阵行列式计算。8 seed 的跨链最大差为 T `9.4877e-4 K`、KE `2.64898e-5 eV`、PE `2.966e-4 eV`、scalar P `1.41190e-4 GPa`、V `1.18408e-5 Å³`。当时的派生 JSON 记录了原始逐列数组；这项比较未满足 byte-prefix 合同。

截至该时间点，MPI 长链条件当时按单 seed 分片执行；P2/P4 两个 backend 各计划完成 8 seed。当时的账本列出活动 PID/GPU。已完成的同 seed candidate-vs-P1 全 80k thermo 派生诊断包含 P2/HostStaged seeds101–104、P2/CudaAware seeds101–102、P4/CudaAware seeds101–102。映射修正后的 JSON 为 `/tmp/dmgmd-b0-b4-20261103/npt-b3/mpi-vs-p1-mapped-diagnostic-current.json` SHA-256 `8e84b52c2e5bc0c442059702d15fdb1dd252acc3830c70c05b26c7e9eb4534ca`，保留原逐列差异、首差位置，并另从 T、KE、PE、对角 stress 均值及 box 行列式计算派生差；当时已完成配对的最大差为 T `0.00473933 K`、KE `1.32323e-4 eV`、PE `3.596e-4 eV`、scalar P `1.46979e-4 GPa`、V `3.08661e-5 Å³`。该诊断用于描述性比较，不增加容差门；后续 seed 完成后会扩充并重算。

当时的 execution-only freeze v12 SHA-256 `dac2054d56bc1029dfb6b445a1215385387ddef4855f57af940fb594f5954dbd` 适用于 GPU4–7；v13 SHA-256 `099e5ead7a5303b77db093df1802dea7ce72af8d2f57daa1994fed9e17b9be9f` 适用于 GPU0–3。环境证明分别为 GPU4–7 `/tmp/dmgmd-b0-b4-20261103/npt-b3/environment-proof-gpu4-7.json` SHA-256 `2d24ed18fc6a884a9a0bba6610bb9bf0a1256e2672ee196d0b3f4aad3676df8a` 与 GPU0–3 `/tmp/dmgmd-b0-b4-20261103/npt-b3/environment-proof-gpu0-3.json` SHA-256 `b94ca00e60e327188757d1fa760e4401a4fd2d80869fd9d7e744e83f4f4a2733`。当时的 reference launch registry 由文件锁保护，并在启动时占用互异 UTC 秒；该隔离不观察 GPUMD 内部 RNG seed。Candidate MPI launcher 使用 `--bind-to none` 并保存 rank/launcher CPU affinity。短 route 覆盖 P1/HostStaged 与 P2/P4 两 backend，不代表已覆盖 P1/CudaAware。

### 静态 EOS 独立诊断

为区分控压输入 `B=425 GPa` 与材料响应，复用了已验收 B0 中同一 diamond/NEP 的 near-zero、各压缩/展开 0.5% 静态样本。所有三组 `model.xyz` 初始速度分量均严格为零，thermo 的 KE 列也严格为 `0 eV`。每组使用 `time_step 0` 的单帧 NVE；体积由 thermo 输出晶格矩阵行列式计算，压力取 GPUMD `thermo.out` 的 `(sxx+syy+szz)/3`。同组 GPUMD 与 candidate 的输入 SHA 和输出 thermo SHA 完全相同。

| 状态 | V (Å³) | 平均对角压力 (GPa) | model.xyz SHA-256 | thermo SHA-256 |
|---|---:|---:|---|---|
| 压缩 0.5% | 45.130181658144835 | 6.4914959368 | `f98df0db59fb20c7608f95448be4b5edf1bc1b0b11fe25912087368ce01ed6f9` | `4252f6168538a5b2a4d4dfbaa30d9ffcfc3ee084ec68def2ef31c3fff485a699` |
| near-zero reference | 45.813960749069814 | 0.0007794523938033333 | `5aa37c99208c3ceb85e2e404c62ca25b1f58f2bd3c13026fc8eff1f464d5815c` | `2e642c4471fec8261354408d667c2c8f7d9145cc5fe239e4ba046145c0fca24e` |
| 展开 0.5% | 46.50461193410712 | -6.247899820433333 | `a4504e07ed86e46a24f9cada0245889a703c5da1bfa672530ee1fd5a79af3d83` | `c8d0bb33648ad3b8a071af30ac9bb2fabc0be4d53e8c595b6ba3d6e270cc13ca` |

三点无权 OLS 得到 `dP/dV=-9.268484864179515 GPa/Å³`，在 reference 体积处 `-V dP/dV=424.62600177086796 GPa`。左右单边割线给出 434.8852344222257 GPa 与 414.5026507460001 GPa；OLS 最大绝对残差为 0.10191037061477154 GPa。这个左右响应范围体现三点曲线的非线性，残差和割线范围不是采样置信区间。它仅描述这一组 0 K 静态 NEP 构型，不是 300 K 等温压缩率或普遍材料模量；`B=425 GPa` 是 barostat 控制参数，不是本分析的预设测量答案。

当时执行的复算命令如下；命令引用的是历史 `/tmp` 路径，不代表当前可直接重跑：

```bash
source ../env/md-mpi.sh
python3 tests/npt/derive_static_eos.py \
  --pressure-oracle /tmp/dmgmd-b0-b4-20261103/pressure-gpu0-final/pressure-oracle.json \
  --output /tmp/dmgmd-b0-b4-20261103/npt-b3/static-eos-b0.json
```

派生 JSON SHA-256 为 `baeca10a143f532b3597601f799c40bd5e8d8bcca8e5074639b3bccbc4389649`。上游 pressure oracle JSON SHA-256 为 `2bfdce6fb892a092df425231cf2b0a6970fa51eb70c863f5787fc37e58fa0559`，完整 GPUMD/candidate 原始文件、各输入输出哈希及命令位于 `/tmp/dmgmd-b0-b4-20261103/pressure-gpu0-final/`。

### 早期进度快照（已由下方最终统计节更新）

本节记录了正式运行期间的时间点快照；其中“尚未覆盖”和“正在执行”仅描述当时状态，不代表最终覆盖。最终冻结矩阵、分析身份与结论以 2026-11-03 最终统计节和机器索引为准。


<a id="npt-scr-final-20261103"></a>
### 2026-11-03 最终冻结统计矩阵

**状态：18 个预注册 formal looks 全部完成；总体兼容门为 INCONCLUSIVE。** 分析使用冻结的 `tests/npt/analyze_statistics.py`、`tests/npt/manifest.json` 和 native ACF helper。8 seed 的 base 五条件各有 20k/40k/80k 三 look；half-dt P1/HostStaged 有 40k/80k/160k 三 look。20k、40k 与 80k 的 reference 使用各自 dt profile 的最大长度单段链前缀；不将 reference 前缀当作独立样本。

逐 look 的 records SHA、analysis JSON SHA、条件、profile、decision 与各温度/压力/体积的 mean、variance ratio、CDF 判定收录于当前保留的[机器可读 formal index](npt-scr-formal-index.json)，其SHA-256 为 `c9da2f5d27beeb4eaa6d7d91655f55fa2eb65809fb7cc51de901c7c3a187e86d`。索引固定候选 binary SHA `c3c9cd0b…d70bcdf`、GPUMD SHA `3ab365cc…6e414`、NEP SHA `add6b3f6…c531623`、source digest SHA `52069b5a…5b1c18`、冻结 analyzer SHA `da0b2627…520496`、原统计 manifest SHA `2c39011c…17da60` 及 ACF helper source/binary SHA。Root 独立复核18项 records→analysis SHA 与原始 thermo 身份匹配；这些身份检查通过不构成统计兼容 PASS。

各 condition 在 80k base look 的温度/体积均值门及 variance ratio 门通过，压力 variance ratio 也通过；压力均值差约 `+0.13563 GPa`，CI 约 `[+0.000681,+0.270584] GPa`，仍跨过 `±0.1 GPa` 等效界，因此四个 MPI base 条件的总体统计结论均为 INCONCLUSIVE。体积分布 ECDF sup 作为预注册辅助门报告（约 `0.0481`），不能替代压力均值门。half-dt 各 look 也完整保留为 INCONCLUSIVE；不因理论 target 诊断覆盖 reference 兼容结果，也不据此宣称普遍严格采样。

P1/HostStaged base 的执行偏差单独保留：原始 20k 首 look 是正式 look；随后 40k/80k 使用全新的完整 80k candidate 链，其 20k prefix 不与原链逐字节一致。该差异按当时冻结输入/输出逐列诊断记录，不拼接、不追加 formal look；由 rerun 链重建的 20k 结果仅为 diagnostic，不替换原 20k look。

截至 2026-11-03，原注册18个 formal looks 全部完成但总体兼容结论为 `INCONCLUSIVE`，该结论保持不变。Root 于当日批准单独进行独立确认；旧确认批次没有运行最终分析，之后的 n3 successor 按原注册条件重新执行，未合并旧批数据。最终 scientific status 与 G-B 见[2026-11-04 n3 最终确认](#2026-11-04-n3-final)和[独立结果索引](npt-scr-n3-confirmation-index.json)。

<a id="npt-scr-migration-20261104"></a>
<a id="2026-11-04-n3-migration"></a>

### 2026-11-04 n3 migration and independent confirmation

迁移与独立确认最终通过；详细迁移校验、逐作业执行记录、源/输入快照和审计回执原用于复现与追溯过程，不构成额外科学结论。按用户授权，文档化 evidence 包保留最终 paired-analysis、注册 manifest/protocol、原18-look索引及本节摘要；原始测试数据仍在 ignored 的 `artifacts/validation/npt-scr-gb/`。部分迁移校验、逐作业记录、源/输入快照和审计回执已不在文档证据包中，因此不能据此宣称可完整重放。

旧确认在原机执行留下 63 条 completed、6 条 interrupted、91 条 planned；未对旧批次运行最终分析，其数据均未并入 successor。迁移机 n3 的 NVIDIA driver 为 `570.86.10`，原 reference binary 因 PTX 8.8 出现 CUDA error 222。基于锁定 GPUMD source commit `9d23496e41319b9e2af5221a7df6285387401d1e`，在隔离环境以 CUDA 12.8.61、g++ 13.3.0、sm_89 重建 reference，binary SHA-256 `045d1edbf893d6dcb8e18cff0bff27eea70070e078b7c4950cbe1c5cc37ac5f7`；候选 binary SHA-256 保持 `c3c9cd0b81a3f18ebbd8f4379dfbd3dbf929785eb5aa883fa4730b025d70bcdf`。迁移后的短程资格检查通过：4 个 baseline case、2 个 Berendsen golden fixture（各 200 步）及 16 条旧 pilot seeds 101/102 的 200-step candidate 前缀诊断均 PASS。前缀诊断满足原有容差、4 对 P1 重复逐字节一致、8 个 MPI/P1 route 检查通过，最大原始列差约 `1e-14`，派生压力差 `3.33e-15 GPa`，体积差为零；这些诊断不进入正式统计。

successor 使用原注册的科学参数、固定种子、160 条预算、统计方法和阈值，仅更新 reference/runtime 身份；执行前审核了 [successor manifest](../../../tests/npt/independent-confirmation-n3-manifest.json) 与 [successor protocol](../../../tests/npt/independent-confirmation-n3-protocol.md)。n3 单节点环境门、CudaAware 探测、restart 与 golden 检查通过；作业使用 GPU 1–7，GPU 0 未使用。环境限定为本地 shared-memory、TCP 和 CUDA transport，不代表 RDMA 或跨节点验收。

160 条完整链于 2026-11-04 10:25:30 UTC 结束，组成 base primary 64 条、half primary 64 条、base MPI bridge 32 条。冻结分析仅运行一次，于 10:29:27 UTC 启动、11:39:03 UTC 结束，exit code 0。执行身份和环境审计通过；独立审查结论、科学结果与范围见[最终确认](#2026-11-04-n3-final)和[独立结果索引](npt-scr-n3-confirmation-index.json)。原18-look总体 `INCONCLUSIVE` 结论保持不变，旧批次未合并。

<a id="npt-scr-confirmation-20261104"></a>
<a id="2026-11-04-n3-final"></a>
### 2026-11-04 n3 最终独立确认与 B4 收尾

**科学判定：PASS。主审验收：B0–B4完成，G-B PASS（限本节范围）。** 本次为迁移后单独冻结的160条全新完整链：base primary64条、half primary64条、base MPI bridge32条，合计35,840,000行原始thermo。唯一一次冻结分析当时于10:29:27 UTC启动，11:39:03 UTC（北京时间19:39）正常结束，exit code 0；未追加种子、预算或统计look，未改变方法或容差。

当前保留的完整 paired-analysis：[paired-analysis.json](evidence/npt-scr-gb/results/n3/paired-analysis.json)，SHA-256 `ddc4b004888706b2915d60f494a1590869dda6f637b35acc0a67e2aa5c565461`。单次分析、执行身份与迁移审计的最终摘要见[独立结果索引](npt-scr-n3-confirmation-index.json)；原18-look索引保持不变。执行日志、路径映射及快照文件已按授权清理，不提供过程文件级复现声明。

#### Primary：P1 / HostStaged

每个profile为32条candidate与32条GPUMD reference，seeds201–232；base160k步、half320k步，物理时长均40ps。丢弃前10%，125fs完整block（每seed288个）；固定20,000次variance bootstrap，analysis seed2026110301，component z=2.64。均值、variance CI、volume CDF及ESS门全部通过。下表只摘录已生成报告，不重新估计：

| Profile | 指标 | candidate − reference 均值 CI | variance ratio CI | 判定 |
| --- | --- | --- | --- | --- |
| primary_base | temperature_K | [-0.43356112, 0.24565549] | [0.97915239, 1.02168911] | PASS |
| primary_base | pressure_GPa | [-0.02183167, 0.07818323] | [0.97665437, 1.06645089] | PASS |
| primary_base | volume_A3 | [-0.23661163, 0.05857776] | [0.93543894, 1.02032115] | PASS |
| primary_half | temperature_K | [-0.32213118, 0.36285787] | [0.98577293, 1.02419238] | PASS |
| primary_half | pressure_GPa | [-0.04242195, 0.05107708] | [0.97589314, 1.07712625] | PASS |
| primary_half | volume_A3 | [-0.15483652, 0.12241364] | [0.93370401, 1.02895138] | PASS |

压力均值界为±0.1GPa；温度±6K；体积±0.25%对应profile的reference mean；variance ratio区间须整体在[0.85,1.15]内。base/half的体积pooled CDF距离分别为0.00974327/0.00553961，均小于0.15。primary mean/平方偏差pooled ESS的最小值为10,919.9984。

#### MPI bridge：P2/P4 × 两后端

每个条件使用seeds201–208的8条160k完整链，与相同初态、seed、时间block的P1候选配对，再与32+32 primary结果合成。均值CI按端点相加，正variance ratio CI按端点相乘，使用冻结的Bonferroni组合；不假设两个分量独立。全部12个T/P/V桥接指标通过。

| 条件 | 合成压力均值 CI / GPa | 体积CDF三角上界 | T/P/V及ESS判定 |
| --- | --- | --- | --- |
| P2/CudaAware | [-0.02183177, 0.07818334] | 0.01323980 | PASS |
| P2/HostStaged | [-0.02183176, 0.07818335] | 0.01323893 | PASS |
| P4/CudaAware | [-0.02183177, 0.07818334] | 0.01323893 | PASS |
| P4/HostStaged | [-0.02183177, 0.07818334] | 0.01323980 | PASS |

桥接mean/平方偏差pooled ESS的最小值为2,886.7688，超过门槛1,000。体积CDF三角上界均≤0.01323981，小于0.15；该上界若超限只会导致INCONCLUSIVE。T/P CDF保留为REPORT_ONLY。完整分量、合成区间与 ESS 见当前保留的 paired-analysis 与机器索引。

#### 身份复核与适用范围

单次分析身份与决策算术核对为`PASS_METADATA_AND_DECISION_CONSISTENCY`；最终主审、独立科学复核及迁移身份审计均 PASS，摘要见[独立结果索引](npt-scr-n3-confirmation-index.json)。身份审计不重读 thermo，也不重新运行统计分析。

通过范围限于所列NEP-C、216原子、300K/0GPa、全量复制模式下的各向同性正交全周期盒及单节点 P1/HostStaged、P2/P4双后端；half-dt仅P1/HostStaged。NEP5/ZBL只有另行记录的短合同证据。`THEORY_TARGET_INCONCLUSIVE`仍单列；GPUMD内部clock seed未观测；不外推普遍严格NPT采样、遍历性、非NEP、局部域分解模式 NPT、RDMA或跨节点。原18 looks的INCONCLUSIVE及旧确认当时记录的63 completed/6 interrupted/91 planned未混入本次统计。

独立科学复核结果为 PASS，核对了当时的 paired-analysis 报告的 primary/bridge 判据与身份，未重估统计量。分析完成后进行的 draft fixture 测试维护通过 **6/6 CPU 测试**，未改变生产代码、runner、analyzer 或科学注册；维护范围见[回归报告](#npt-regression-20261104)。

主审当时综合B0–B4既有功能/安全/回归证据、本次冻结统计与独立复核、6项分析后CPU测试完成验收。最终完整性复查465项无失败；347个冻结身份叶子中仅有已记录的分析后测试源维护差异，19个关键生产源码和三个reference状态保持原样。21份相关文档的405个本地链接/锚点检查及`git diff --check`通过。C/D未启动，v1.0未发布。


<a id="npt-output-20261103"></a>
## 2026-11-03 — 输出、restart 与错误合同

本文记录 B4/G-B 的可复核输出时点、restart 与多元素势补充测试。结论只覆盖明确列出的二进制、输入、rank、后端和环境；短合同不外推为长期统计结论。

### 2026-11-03：P1 合同及水 / BaTiO₃-ZBL 短程

**状态：本节所列 B4 合同子集全部通过；不覆盖长期统计矩阵。** 候选为 `/tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md`，SHA-256 `c3c9cd0b81a3f18ebbd8f4379dfbd3dbf929785eb5aa883fa4730b025d70bcdf`。生产源码与二进制在本轮保持冻结。P1 固定输入清单为 `/tmp/dmgmd-b0-b4-20261103/npt-b4/freeze-v6/freeze.json`，SHA-256 `4288b101a4268bb176dac82bd11cff96e565b5b59786517491cc041a0546ea94`；对应结果的历史路径为 `/tmp/dmgmd-b0-b4-20261103/npt-b4/p1-contracts-v6/results.json`，SHA-256 `12ee2d5252c28bfb127204e605210a9ad08e84d73f77ff1f75a43cd88c8ac785`。该 P1 freeze 锁定的 runner SHA 为 `6ffd5652abe16f11c37b85b12fb12969056c894a75ae5d3d5b20efefae5aa43c`；之后扩展到固定负压输入的 v7 runner 单独冻结并运行。

执行环境为 GPU5、P1、HostStaged 和 CudaAware；每个新 shell 先执行 `source ../env/md-mpi.sh`。冻结包含 long-NVE 既有生成器产出的显式速度输入、锁定势文件、脚本/候选/GPUMD SHA、long-NVE 与 baseline manifest SHA，以及 reference HEAD、工作树状态与 ignored 文件清单摘要。水输入为 12,288 原子，模型 SHA-256 `88f2caeb21e0e2b61072f06a1e790d67f4d8b2e1b60d6faeebba149e3b33fe6a`；BaTiO₃-ZBL 输入为 5,000 原子，模型 SHA-256 `c9650e7d34d1891994378c561448e3933d8028e29c835f1ff007eec7bb45e844`。势文件分别沿用 long-NVE manifest 锁定的水与 BaTiO₃-ZBL 文件及 SHA。

两种势分别执行了 `npt_ber` 100 步、`npt_scr` 20 步；均覆盖 HostStaged 与 CudaAware。每个作业检查了 18 列 thermo 数量与有限盒、trajectory/restart 形状、原子序、`pos`/`vel` 与 restart 的 `%g` 六位有效数字序列化匹配，以及 `unwrapped_position` 输出。SCR 每个配置以同一固定 seed 重复，thermo 输出逐字节一致。两种势的 P1 HostStaged `npt_ber` 100 步候选输出均按冻结的 baseline 容差与 GPUMD 对比通过；最坏位置绝对差为水 `1.07e-14 Å`、BaTiO₃-ZBL `1.78e-14 Å`，速度最大绝对差分别 `2.09e-17` 与 `5.56e-17 Å/fs`，温度、热力学应力及 restart 数值完全相同。GPUMD 与 DMG-MD 对照只覆盖这两个 NEP 模型、P1 HostStaged、100 步，不代表其他势或并行矩阵。此处共有 21 条结果记录：20 条实际作业，加 1 条由连续/SCR split 两次作业派生的分类记录。

另以同一个 216 原子碳初态和 seed 对每种 NPT 做单步 moving-box `B=425 GPa` 与固定盒 `B=2001 GPa` 对照。两者 thermo 前 9 列逐位一致；thermo 后 9 个盒分量与各自 trajectory 的 post-scale 盒相符。moving-box 的位置恰为固定盒位置乘 isotropic 因子；速度、unwrapped position、力、每原子能量和 virial 保持一致至浮点舍入误差（最大差 `1.78e-15`）。`npt_ber` 因子为 `1.0000028586357597`，`npt_scr` 因子为 `0.9998521398884814`。这直接核验了 snapshot 的位置/盒在缩放后，而力、virial、能量与温度反馈样本在缩放前；输出盒列报告缩放后盒。restart 与最终 trajectory 的 `pos`、`vel`、`Lattice` 均满足 `%g` 序列化合同。

SCR 固定 seed 连续 40 步与同进程 `20+20` 分段分类输入均完成；输入在第二段前重复 dump 命令，以匹配 run 段级 measurement 生命周期。thermo 首个差异位于第二段首行（连续作业 `T=109.71363604 K`，split `109.7124713 K`）；当时结果仅用于分段分类，restart 不承诺控制器状态或轨迹连续。对应输入与逐文件输出曾位于当时的 evidence 目录；该过程材料已清理。

当时使用的 P2/P4 矩阵命令（从仓库根目录；`--work-root` 必须换成尚不存在的空目录）：

```bash
source ../env/md-mpi.sh
python3 tests/npt/run_npt_contracts.py --run \
  --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md \
  --reference ../gpumd-reference/src/gpumd \
  --freeze /tmp/dmgmd-b0-b4-20261103/npt-b4/freeze-v7/freeze.json \
  --work-root /tmp/dmgmd-b0-b4-20261103/npt-b4/p2-p4-replay-new \
  --devices 0,1,2,3 --ranks 2,4 --backends HostStaged,CudaAware --timeout 900
```

### SCR 固定盒负压力与 P1 负例补测

在上述单步输出时点对照之外，使用相同 216 原子碳 fixture 运行了 SCR 20 步固定盒作业，输入为 `ensemble npt_scr 300 300 40 -1 2001 200`。该短作业验证 SCR 接受负目标压力；`B=2001 GPa` 路径保持盒固定。GPU5、P1、HostStaged、seed `12345678`，日志与逐文件 SHA-256 清单见 `/tmp/dmgmd-b0-b4-20261103/npt-b4/scr-fixed-negative-p1-v7/contract.json`（SHA-256 `1432ee985f890ecb0f078e8d99b76ba33513a4acd734607a0d63eea73b63d732`）。记录锁定候选 SHA `c3c9cd0b81a3f18ebbd8f4379dfbd3dbf929785eb5aa883fa4730b025d70bcdf`、输入 SHA、20 行 18 列数值 thermo、4 个 trajectory frame；所有 thermo 盒分量恒定，trajectory 的 4 个立方盒均为边长 `10.734633 Å`。stdout 明确记录 `mode=m1 reason=npt-m1-only`，stderr 为空。该项只证明 SCR 接受负压且固定盒分支不改盒，不代表非固定盒下负目标压力响应。

同一冻结候选下，P1 单 rank 运行了 22 个预期失败输入，覆盖 Ber 的 `correct_velocity`、adaptive `time_step` 与四项环境 guard，以及 SCR 的非有限温度/压力、非法 B/tau、12/18-token、非周期/三斜盒、unsafe scale、zero-KE、两项 thermostat/time-step 限制和四项环境 guard。结果 `/tmp/dmgmd-b0-b4-20261103/npt-b4/p1-negative-v4/results.json`（SHA-256 `38be85b5648f6a6b70f74618ea9b6a87a512cbe04eb12792a59099d001231dab`），对应冻结 `/tmp/dmgmd-b0-b4-20261103/npt-b4/negative-freeze-v4/freeze.json`（SHA-256 `13d951c3ed6c5816e6e023580a1d78ed432a4dee84f5d2d216fdb4a4ba04c39b`）：22/22 都匹配各自特定 diagnostic、非零退出、0 行数值 thermo，且没有 trajectory/restart 成功产物。这里的环境负例为单 rank，不覆盖跨 rank 不一致传播。

### 已通过且可复用的前序证据

launcher 异常尝试的结果的历史路径 `/tmp/dmgmd-b0-b4-20261103/npt-b4/p4-collective-negative-v5/results.json` SHA-256 `69ef7edcf01550f030893b9efbe1670d80a1c0769877e5c52f65225f772d58ac` 当时标为失败尝试，不计通过。替代 wrapper 文件 SHA-256 `9e479e95487024a6b9c58a027028e2fa34c33dd6db54eff091b4d3203b40be63`；两项 negative 当时分别写入独立目录，并记录原始日志和输出哈希；这些过程材料已清理。

六个正例/负例输入 freeze（P1、v7 多元素、P1 negative、P4 mismatch negative、NEP5、P1↔P2 restart）都记录了 GPUMD、LAMMPS、LAMMPS-MatPL 三个 reference checkout 的 HEAD、status 与 ignored inventory。运行后快照 `/tmp/dmgmd-b0-b4-20261103/npt-b4/reference-integrity.json` SHA-256 `3815139f6262ec2c20678154d703463ea74e8a53c3861abf50941c9b63f2f7d0` 与六个 freeze 一致：三个 checkout 的 HEAD/status/ignored 清单 hash 均未变化；GPUMD ignored 文件 208 个，hash `7314f1304d44a8fee9cf50ce62bf369f93e160eebf63783603086c875e21f405`，另两个 checkout ignored 数量为 0。

### P2/P4 双后端与跨 rank restart

用 freeze-v7 (`/tmp/dmgmd-b0-b4-20261103/npt-b4/freeze-v7/freeze.json`，SHA-256 `65957fef2a71e4a78c940d260a31b8f2aa2f46678d32e54148361238ef4fe9c4`) 和同一候选运行水与 BaTiO₃-ZBL 两套 fixture；本次 runner SHA-256 为 `ccf8c0a76abb786d0f8a81d98e2b1b6ebf3b21b3929a5f53815a5495a6b35bde`。结果 `/tmp/dmgmd-b0-b4-20261103/npt-b4/p2-p4-output-contracts-v7/results.json`，SHA-256 `f9db02aba7b337444004eb6fe4369a676ff4dd1616864141b78e7af94cbbd9de`，48 条记录包括 47 个实际作业和 1 个 split 分类记录，全部通过：两种势的 Ber 100 步、SCR 20 步与固定 seed SCR 重复均覆盖 P2/P4 和 HostStaged/CudaAware；每种势、每个系综均从 P2 checkpoint 启动 P4 新作业、从 P4 checkpoint 启动 P2 新作业。新作业检查全量复制模式路由、thermo 行数/有限盒、trajectory/restart 形状和 `%g` 序列化，不要求跨 rank 轨迹连续。

额外补了代表性 dense-water P1↔P2 文件 restart。原始 fixture 模型 SHA 为 `88f2caeb21e0e2b61072f06a1e790d67f4d8b2e1b60d6faeebba149e3b33fe6a`，固定势 SHA 为 `8638300c8c6ba7eca589fa2fdc111d2e6a1f6ad9c88cc522ea9db220538a66e3`；恢复作业的输入状态来自下列已通过作业的 checkpoint，而不是原始 fixture 模型。Ber checkpoint SHA 两端均为 `4c46a3bcb9197cbc3e6c9f0c3e1659b8f0d1ff16f172373a0d6f5ad50dae03d2`，SCR checkpoint SHA 两端均为 `e0a4bebc567a19cc1f4a296e6f4f65d683df290a714253dcb2f3ed6847eeba44`。四个 5 步恢复作业（Ber/SCR 各 P1→P2 与 P2→P1）结果 `/tmp/dmgmd-b0-b4-20261103/npt-b4/crossrank-p1-p2-v1/results.json`，SHA-256 `1d34cc98ec8f53b5dec5e4d3dca3bf09d521918521ccc4500e95114aa74b841f`；源结果与冻结 checkpoint 路径/哈希记录在 `/tmp/dmgmd-b0-b4-20261103/npt-b4/crossrank-p1-p2-freeze-v1/freeze.json`（SHA-256 `3b611f8446d3daf084aa61de398bbf576bdaab96671e65e37ccf7ab82627b4c8`）。四项均通过 finite thermo、全量复制模式路由、snapshot/restart 形状和 `%g` 检查。连续性不设门。

### NEP5 等价势短合同

为补齐 NEP4 / NEP5 / ZBL 的势类型覆盖，从既有 216 原子碳初态构造 NEP5 fixture，转换严格复用 `long_nve_common.POTENTIAL_TRANSFORMS["nep5_equivalent"]`。转换前 NEP4 势 SHA-256 为 `add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623`，转换后 NEP5 势 SHA-256 为 `685fcc2b4eab8fa7e87de3f2112389a54f1940b12df9f276c06b82d0cf60d182`，与 long-NVE manifest 锁定值一致。独立 freeze `/tmp/dmgmd-b0-b4-20261103/npt-b4/nep5-freeze-v2/freeze.json` SHA-256 `b86bccfebc375a0abe72fb8fae60e4ca6a53010f2195305d44dc24a4dde7205e`；执行脚本 [run_npt_nep5_contract.py](../../../tests/npt/run_npt_nep5_contract.py) SHA-256 `300edc01fb8c57337af409785b0ffcdeb27983c077177cc4a9e37c7455256ff0`。

结果的历史路径 `/tmp/dmgmd-b0-b4-20261103/npt-b4/nep5-contracts-v2/results.json` SHA-256 `a9a269915ec7f66ab8be3884fb45a32c4ae6603ad7921dfd591bb6f310dfa79a`，19/19 记录通过：18 个候选作业覆盖 P1/P2/P4、双后端、Ber 100 步与 SCR 20 步固定 seed 重复，另 1 个 GPUMD P1 Ber 参考作业。所有候选输出检查全量复制模式路由、有限 thermo、trajectory/restart 形状和 `%g` 序列化；SCR 每条件重复 thermo 字节相同。P1 HostStaged Ber 与 GPUMD 的 100 步 thermo/应力/restart 完全一致，位置最大差 `3.55e-15 Å`、速度最大差 `9.54e-18 Å/fs`。当时同一路径还记录了独立四卡 candidate CUDA-aware 环境探针：`environment-proof-p4/proof.json` SHA-256 `52549cc3519ead7e6c60d4c9f4ab69134dabe441219b6459d96f3aa8a3195e22`；GPU0–3 每 rank 的 CUDA-aware collective 与 P2P 自测均通过，候选 SHA 与本报告相同。

### 跨 rank 一致性负例

原 MPMD 启动方式遇到 Open MPI launcher `-11`、空日志；该尝试当时记录在 `p4-collective-negative-v5`，不计作合同结果。改用冻结的普通 `mpirun -n 4` rank wrapper：wrapper 在 rank 3 注入 seed 或 `DMGMD_DOMAIN_PARTITION` 不一致，其源代码随 negative freeze 记录。freeze-v6 的历史路径为 `/tmp/dmgmd-b0-b4-20261103/npt-b4/negative-freeze-v6/freeze.json`（SHA-256 `1438f72ad65cf15c75ed0ad1aff41b9173bb31d64cb61406e3c8dd9c6c423563`），runner SHA-256 `eb809fd97bc0bbeed8db7eed5b54867798fe58241638f40640ddfea87d8a81a4`。P4 结果 `/tmp/dmgmd-b0-b4-20261103/npt-b4/p4-collective-negative-v6-retry/results.json` SHA-256 `d95141b65990d85f6166a513fe239565854f707ca0d35cfcf52772353d48a901`：seed 不一致和 partition 不一致两项都在 rank 0–3 匹配对应 diagnostic，非零退出、0 行数值 thermo、没有 trajectory/restart 成功产物。

- B2 Berendsen v6 结果 `/tmp/dmgmd-b0-b4-20261103/npt-b2/results-v6/results.json` 与 restart 分类 v3 `/tmp/dmgmd-b0-b4-20261103/npt-b2/restart-classification-v3/results.json` 已由主审验收。包括 P1/P2/P4、HostStaged/CudaAware 路由、200 步 GPUMD golden、单 rank long/split/file restart 分类，以及非有限温度、B/tau、12/18-token、非周期/三斜、非正 scale、zero-KE 的逐类诊断与无数值 thermo/trajectory/restart 负例。其合同和范围见 [Berendsen 节](#npt-ber-20261103)。
- B3 SCR short 结果 `/tmp/dmgmd-b0-b4-20261103/npt-b3/short-gpu4-7/results.json` 已验收，覆盖 P1 HostStaged，以及 P2/P4 双后端、固定 seed 重复、仅使用全量复制模式的路由及 P4 zero-KE all-rank 失败且不生成有效输出。该结果没有 P1 CudaAware；本节的 P1 B4 作业已补上该条件。B3 正式统计另见 [验收矩阵](#npt-matrix-20261104)。

### 范围

本节汇总已完成的 B4 短合同子集，不代替 B3 长时统计矩阵；B2 是确定性 golden 验证，不属于统计矩阵。短作业不承诺跨进程布局或新作业 restart 的轨迹连续性。Reference 在本轮保持只读。


<a id="npt-regression-20261103"></a>
## 2026-11-03 — 旧路径回归

类别：稳定主题验收记录。执行日期：2026-11-03。本节记录 B3 冻结候选上的旧 NVE/NVT、MPI ownership、局部域分解模式与 CTest 回归；结果不能代替 SCR 正式统计、B4 输出/错误矩阵或 G-B 主审结论。当日G-B为PENDING；2026-11-04完成独立确认与最终整合后的状态为**PASS**，见[最终记录](#npt-scr-confirmation-20261104)。

### 候选与执行环境

| 项目 | 冻结身份 |
| --- | --- |
| 候选 | `/tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md` |
| 候选 SHA-256 | `c3c9cd0b81a3f18ebbd8f4379dfbd3dbf929785eb5aa883fa4730b025d70bcdf` |
| Source HEAD | `80c57680664d5e8b4d355946907222da009177ed` |
| 源文件 SHA 清单 | `/tmp/dmgmd-b0-b4-20261103/b3-source-digest.sha256`，SHA-256 `52069b5aca1dc5d3d7b9a674041e814ef3898eb9a6f93d15d6c38653ff5b1c18`；19/19 列出的源码与构建清单逐项校验通过 |
| 构建日志 | `/tmp/dmgmd-b0-b4-20261103/b3-build-final.log`，SHA-256 `3215448211c7b68745290d84cd7f2028a3c5045673c32c932eabd88122b7c1a8` |
| GPU / MPI | GPU 0–3；Open MPI 5.0.10、UCX 1.22.0；脚本环境门报告 `cuda_copy/cuda_ipc`，CudaAware 四 rank probe 通过 |

每个新 shell 都先加载 `source ../env/md-mpi.sh`。候选在该次执行开始与完成时 SHA 均为上述值。B1 随后只在 `tests/CMakeLists.txt` 注册两个已有 NPT CPU 测试；重配时仅构建既有 `dmgmd_autocorrelation` 测试依赖，之后再次核对候选 SHA 未变。Reference GPUMD checkout 保持只读。

### 回归结果

| 范围 | 选择与覆盖 | 结果 |
| --- | --- | --- |
| 生产候选原有完整 CTest 集合 | 冻结生产二进制 `c3c9cd0b…d70bcdf` 上的原有 28 项，串行执行；包括 CUDA box/neighbor/pressure writer 与 MPI NPT guard | **28/28 PASS，0 skip** |
| 后续新增 NPT CPU CTest | `dmgmd.npt_manifest`、`dmgmd.npt_statistics`，在之后单独注册并执行 | **2/2 PASS**；这是独立的针对性 CPU 检查，不与上一行合并成一次 30/30 或 32/32 CTest 运行 |
| 后续 paired-confirmation 数学/集成 CPU CTest | `dmgmd.npt_paired_math`、`dmgmd.npt_paired_integration`，针对独立确认分析工具单独注册执行 | **2/2 PASS**；不属于生产候选原有完整 CTest 集合，也不表示某次全量 CTest 达到 32/32 |
| Golden baseline | 四个 manifest case，P1 HostStaged，GPUMD goldens | **4/4 PASS**。能量、力、晶格、位置、重启质量/位置/速度、温度、thermo stress、时间为零差；其余最大差：速度 `8.674e-19`、virial `1.776e-15`、XYZ stress `6.353e-22`，均在原 tolerance 内 |
| long-NVE smoke | `carbon_crystal`、`dense_water`、`batio3_zbl`；P1 HostStaged；`short,nvt` | **3/3 PASS**。最终报告的字段差异汇总与冻结 B0 `long-smoke.json` 完全相同：energy/force/position/lattice/mass/temperature/stress/time/virial 聚合最大差为零；velocity 最大绝对差 `3.469e-18`，XYZ stress `8.132e-20` |
| NHC/BDP | `short,negative`；fallback 与 domain fixture；P1/P2/P4 和适用的双后端；包括重复运行与 fresh-controller restart | **142 条记录全部 PASS**。相对 B0，P1 HostStaged 的 12 个短程/restart 标签输入 SHA 与科学输出 SHA 全部逐字相同。非法控制器命令、非默认运行时守卫和 zero-KE 用例均按预期拒绝，没有生成最终科学快照 |
| 全量复制模式下的 ownership migration | crossings、empty slab、N<P、restart/resume；P1/P2/P4、HostStaged/CudaAware | **全部 PASS**。owner 迁移记录、周期回绕、多 slab 跨越、空 ownership、跨 rank restart 与 P1 动力学对照均通过 |
| 局部域分解模式相关回归 | 17 个 runtime case：`lattice`、`chain`、`micro_crossings`、`empty_local`、`crossings`、`resume`、`nep5`、`water`、`zbl`、`typewise`、`flexible_zbl`、`typewise_zbl`、`partition_aux`、`partition_skin`、`partition_rebuild`、`partition_migrate`、`partition_nvt`；P2/P4 × HostStaged/CudaAware × `off,serial,verify` | **204/204 PASS**。另有 P1 oracle/fallback、mode/layout/owner/ghost poison、精确通信字节核验及 focused cache/timing/rebuild/fatal-path 检查均通过 |

长程 smoke 的 B0 对比使用同一冻结参考、输入和 acceptance comparator；汇总指标对象完全相同。NHC/BDP 对比进一步逐标签核对了 P1 HostStaged 输入与输出哈希。主矩阵中各 rank/backend 与分区模式的科学差异仍由各 runner 的 P1 oracle 和原 manifest tolerance 判定，没有更改 golden 或阈值。

### 执行失败与范围边界

第一次启动 migration runner 时没有及时显示缓冲输出，我将该次尝试中止；当时生成的空日志 `/tmp/dmgmd-b0-b4-20261103/regression/final-b3/migration.log` 与 exit code `120` 记录这次人为中止。随后用 `python3 -u` 和新的 work directory 完整重跑，全量复制模式相关测试全部通过。该中止记录不是科学失败，也当时没有删除或覆盖 B0 的历史尝试。

B0 冻结清单还记录过一次 GPU1 空闲显存不足导致的 OOM（当时仅 1289 MiB free）；B0 后续在 GPU6 的成功基线未覆盖它，本轮 GPU0–3 的所有验收运行均完成。无 CTest skip、无本轮科学/通信失败，也没有启动性能、nightly 或 release profile。B3 SCR 统计分析、B4 NPT 输出/restart/负例矩阵另见本页相关日期节；本节仅放行所覆盖的旧路径回归。

### 复现命令

从 `newmd/` 项目目录运行；各命令前先执行 `source ../env/md-mpi.sh`。所有输出写入本轮新的 `/tmp/dmgmd-b0-b4-20261103/regression/final-b3/` 工作目录。

```bash
export CUDA_VISIBLE_DEVICES=0,1,2,3
export CUDA_DEVICE_ORDER=PCI_BUS_ID
ctest --test-dir /tmp/dmgmd-b0-b4-20261103/build-b3 --output-on-failure -j1
python3 tests/baseline/run_baselines.py --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md --device 0 --keep-work
python3 tests/long_nve/run_long_nve.py --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md --devices 0 --profile smoke --cases carbon_crystal,dense_water,batio3_zbl --ranks 1 --backends HostStaged --sections short,nvt --retries 0 --keep-work --ui plain
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md --devices 0,1,2,3 --ranks 1,2,4 --backends HostStaged CudaAware --ensembles nvt_nhc nvt_bdp --sections short negative --work-root /tmp/dmgmd-b0-b4-20261103/regression/final-b3/thermostats
python3 -u tests/mpi/run_mpi_migration.py --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md --devices 0,1,2,3 --ranks 1,2,4 --backends HostStaged,CudaAware --timeout 300 --keep-work
python3 -u tests/mpi/run_mpi_domain.py --candidate /tmp/dmgmd-b0-b4-20261103/build-b3/dmg-md --devices 0,1,2,3 --ranks 2,4 --backends HostStaged,CudaAware --partition-modes off,serial,verify --work-root /tmp/dmgmd-b0-b4-20261103/regression/final-b3/domain --timeout 600 --keep-work
```

新增两个 CPU CTest 的日志为 `npt-cpu-tests.log`；本轮主日志和结构化结果当时写入上述 regression 目录。输入清单锁定身份如下：B0 freeze manifest SHA-256 `9c85bfaa2fe59724155fb4a95895e63ee6892e57921f41d63f2a874dc81accc6`；`tests/baseline/manifest.json` `da3d7f30bd612a34b8f343bdf9c477f1a45dee8fe3a1ae0bf317e02e57ec27c5`；`tests/long_nve/manifest.json` `30010c0d15b9ef383ace008aa48c5a4b80821bbf49e6a408df69ff7b9c179c50`；`tests/thermostats/manifest.json` `84d3ea6fc94e6860eab730e0bcd0e3106e75954ed12ff43bcf3ab69233e60c9f`。逐次执行的 model/run/potential、executable、输入和输出 SHA 当时记录在 runner 的 `results.json`、long-smoke JSON 及原始工作目录中。

| Runner / 测试登记 | SHA-256 |
| --- | --- |
| `tests/baseline/run_baselines.py` | `e675877202f02ae6aa279dda47d40d7fa442f52a5422a004f86826415ac11d3f` |
| `tests/long_nve/run_long_nve.py` | `8471c3cb2ece63cb4395b17cdae1b00fb019701b2c2e9c136a82127eb9f3ae3a` |
| `tests/thermostats/run_thermostats.py` | `c8c351de6d534a7b578e77b57531fe06e59ec36dac93a59b12ae893b00105c1c` |
| `tests/mpi/run_mpi_migration.py` | `23ba5c55e3abc3fdc6979049786cafd066b91d9303c10e2e07b25793b7acfb7f` |
| `tests/mpi/run_mpi_domain.py` | `221bbd9ef0e81d59dcb61769c413bdd257d7aabf0734fb2b5dcd7275039cb890` |
| `tests/CMakeLists.txt`（含新增两项 CPU CTest） | `4ff75ab8a47257c0a2110ef2bd57470fc5e68acc8c5229e8d6ef046001d46ed1` |

| 输出证据 | SHA-256 |
| --- | --- |
| `ctest.log` | `413c1da2feaac3bce6b289088e8a41384ed00cbf38819f1c13e2e3a9021b7c94` |
| `baseline.log` | `bc17df88cc01a44da49da8c0e73083cb1ecccd7bef4eddbd0c9e9950b44ffe82` |
| `long-smoke.json` | `7cc354c930475c4cb675289238d43a79d783ebf36ea049e6272a8781e08c5a9a` |
| `thermostats/results.json` | `6f406ee7ea99acbb44225cc284d13f82615298659a630cad5fb949a0349bff34` |
| 首次中断的空 `migration.log` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 首次中断的 `migration.exit_code`（120） | `97b912eb4a61df5f806ca6239dde3e1a4f51ad20aced1642cbb83dc510a5fa6b` |
| `migration-rerun.log` | `5b26de0efa61e789e3a4c2a27c1a924e943c019f83d6e7431a431324942c59dc` |
| `domain.log` | `146fdc63b189e38375dff376833586be5c098e24e013142d7a9ed56117e56436` |
| `npt-cpu-tests.log` | `b22e5f5ed3f922716e66a81a7d73f9debcaff9bd24524e6cf4ba22185d37eefa` |




<a id="npt-regression-20261104"></a>
## 2026-11-04 — 迁移后复核与测试维护

上述2026-11-03回归原始证据经迁移校验；不宣称在 n3 重跑了整个旧矩阵。n3 另行通过环境、candidate/reference baseline、Ber golden、cross-rank restart 与候选前缀资格检查；生产源码与候选 binary 身份未变。迁移校验、逐作业执行记录、源/输入快照和审计回执当时用于追溯过程，不构成额外科学结论；当前文档证据包只保留最终结果摘要，部分过程快照已移除。原始测试数据仍见 ignored 的 `artifacts/validation/npt-scr-gb/`，但缺失的快照使完整重放无法保证。

独立确认分析于 2026-11-04 11:39 UTC 完成后，应用两处仅影响 draft fixture 的 CPU 测试维护。`source ../env/md-mpi.sh` 后运行 `python3 tests/npt/test_run_scr_independent_confirmation.py`，**6/6 PASS**。未改变生产代码、runner、analyzer 或科学注册，也未再次运行正式统计；最终验收摘要见 [n3 最终结果](#npt-scr-confirmation-20261104)及[独立结果索引](npt-scr-n3-confirmation-index.json)。


<a id="npt-matrix-20261104"></a>
## 2026-11-04 — 验收矩阵与最终状态

类别：稳定主题验收记录。更新日期：2026-11-04。本节汇总 B0–B4/G-B；每次正式执行保留独立日期记录。B0–B2、B4 输出/restart/负例及旧功能回归已通过；n3 的160条全新独立确认及唯一一次冻结分析为 PASS，身份、科学决策和分析后测试维护均经复核。**当前 B0–B4 完成，G-B：PASS（限本文所列范围）。** 原18个统计 look 的 INCONCLUSIVE 保留不变。

### 2026-11-04 — B0–B4 最终验收汇总

| Step / 门 | 状态 | 已确认范围与证据 | 未覆盖 |
| --- | --- | --- | --- |
| B0 压力 oracle | PASS | 同一 frozen build 的生产 writer、GPUMD 压力与 virial 布局对照；解析 oracle、近零压、压缩/扩张与剪切案例见[报告](#pressure-oracle-20261103)。 | 不外推到 NPT、其他势、温度/尺寸或各向异性变盒。 |
| B1 盒变更合同 | PASS | preflight 原子性、float/double 盒逆、缩放位置、所有权哈希及 neighbor 通知/重建见[报告](#box-contract-20261103)。 | 只验证全量复制模式的变盒原语；局部域分解模式尚未接入 NPT。 |
| B2 `npt_ber` | PASS | 200-step GPUMD golden、P1/P2/P4 均采用全量复制模式路由、216/2016 原子、压力方向、负目标压力、B>2000、restart snapshot 与错误矩阵见[NPT Berendsen 报告](#npt-ber-20261103)。 | 不证明严格 NPT 采样。 |
| B3 `npt_scr` | PASS，n3 独立确认 | 32+32 base、32+32 half primary及32条MPI bridge，共160条；均值、variance、体积CDF与ESS门全部通过，见[最终确认](#npt-scr-confirmation-20261104)及[新索引](npt-scr-n3-confirmation-index.json)。 | 统计限216原子NEP-C与注册条件；原18 looks仍INCONCLUSIVE，不外推普遍严格采样。 |
| B4 文档与回归矩阵 | PASS | [旧路径回归](#npt-regression-20261103)、[输出/restart/负例/NEP5-ZBL短合同](#npt-output-20261103)、身份与科学复核、分析后6项CPU测试及最终证据/文档整合完成。 | NEP5/ZBL短合同不等于独立统计；性能、跨节点和release门未执行。 |
| G-B 控压器阶段门 | **PASS** | 主审综合B0–B4、冻结科学报告、独立审计与最终检查放行；证据身份和审查记录见[新索引](npt-scr-n3-confirmation-index.json)。 | 仅单节点全量复制模式、各向同性正交全周期盒；C/D未启动，v1.0未发布。 |

### 2026-11-04 — n3 迁移与新独立确认执行

迁移 checkpoint 的 10,812 个文件和19个关键生产源码哈希核对通过。旧独立确认当时记录63条 completed、6条 interrupted、91条 planned；未对该批运行最终分析，数据也未并入新批次。原 reference binary 在 n3 的 driver 570.86.10 上出现 CUDA error 222；已从相同锁定 GPUMD commit 在隔离目录以 CUDA 12.8/sm89 重建，未修改三个只读 reference checkout。

新环境当时通过两组四 rank CUDA-aware 门槛；candidate/reference baseline 各4项、两个 Ber golden fixture、4项 cross-rank restart 和16条候选迁移前缀诊断通过。Root 在采样前审核新环境、二进制、注册与执行冻结，160条全新 full chains 于 2026-11-04 12:35（Asia/Shanghai）启动，18:25 全部正常完成。完整身份审计通过，唯一一次冻结分析于18:29启动、19:39结束，科学判定PASS；原种子、预算、统计方法与阈值不变。精确身份、命令和证据路径见[迁移记录](#npt-scr-migration-20261104)与[最终确认](#npt-scr-confirmation-20261104)。本次资格检查仅覆盖单节点，不代表RDMA或跨节点验收。

### G-B 必须闭合的矩阵

矩阵对象为冻结 NEP-C、216 原子金刚石和明确的 `model.xyz`/`run.in`/potential/executable 身份。`npt_ber` 与 `npt_scr` 分别覆盖 P1/P2/P4；各 rank 数均采用全量复制模式执行，覆盖 HostStaged 和环境门通过时的 CudaAware。统计 SCR 的 half-dt 仅适用于 P1 HostStaged。每个压力系综都须覆盖符合实际注册合同的输出和 restart 验证；B2 已完成项不因后续 SCR 测试失败而抹除。

| 验收主题 | 证据要求 | 状态 |
| --- | --- | --- |
| `npt_ber` GPUMD deterministic golden | 200-step thermo 六应力、box、XYZ/restart 按现有 tolerance；原 manifest/golden 不改 | PASS，见 B2 报告 |
| 两种 NPT 输入与物理限制 | 8-token isotropic；12/18 token 拒绝；正交、全周期；调度器对局部域分解模式的路由和直接入口防御 | B2 Ber 与 B4 SCR 直接合同/负例 PASS；统计单列 |
| 并行与后端 | P1/P2/P4 均采用全量复制模式；HostStaged/CudaAware（仅环境自检通过时）；失败须正确分类 | B2 Ber、B4 SCR合同矩阵及n3统计确认PASS；SCR统计为P1 HostStaged、P2/P4双后端，half仅P1 HostStaged |
| 控压直接错误/安全门 | 非有限/非法 T、B、tau；无效缩放；零/非有限瞬时温度；P4 失败 collective 不挂起且不生成伪成功输出 | B2/B4 已列的 Ber/SCR 安全负例 PASS；范围见两份结果报告 |
| 兼容边界 | 负目标压力可接受；B>2000 固定盒；SCR 该分支仍消费注册 Gaussian；盒/位置/速度/virial/unwrapped 时点与合同一致 | B2 Ber 与 B4 SCR 输出合同 PASS；SCR RNG 调用流由源审计记录 |
| restart 和多段 run | 位置/盒/速度 snapshot、新作业启动、分段 dump 重新声明；记录实际数值差异，不承诺轨迹连续 | B2 restart 分类与 B4 SCR snapshot/跨 rank 新作业重建合同 PASS |
| `npt_scr` 同条件统计门 | 各 engine/seed 独立轨迹，锁定 burn/block/ESS/CI/variance bootstrap/quantile/CDF 方法；reference 输入和单位一致 | n3独立确认PASS，见[最终结果](#npt-scr-confirmation-20261104)；[原18 looks](#npt-scr-final-20261103)仍INCONCLUSIVE |
| 旧能力回归 | 已冻结的 NVE/NVT、NEP golden、MPI owner/通信、安全负例与相关 CTest | PASS，见[旧路径回归](#npt-regression-20261103) |

用户批准的统计口径是：候选与 GPUMD 或 LAMMPS 在同条件下相容即可判相容通过，以避免体系差异造成假失败。当前 SCR 正式合同按同条件 GPUMD 参考判断；若候选与参考共同偏离理论 target，理论对照单列为 `THEORY_TARGET_INCONCLUSIVE`，不推翻同条件兼容 PASS。它不代表普遍严格采样或遍历性。统计区间过宽、ESS 不足时保留 INCONCLUSIVE；代码错误、无效盒缩放、非有限值、非法输入、MPI 不收敛或不安全输出仍须由直接检查拒绝，参考一致不能覆盖这些缺陷。

### 原 B3（18 looks）冻结合同和限制

冻结输入为 NEP-C、216 atoms、8 seeds（101–108）、NPT `npt_scr`、P=0 GPa、T=300 K、正交全周期盒，采用全量复制模式。base 条件 `dt=0.25 fs` 使用每 seed 20k/40k/80k 步 looks；half-dt `dt=0.125 fs` 使用 40k/80k/160k looks，仅 P1 HostStaged。half-dt 将步数型控制参数同步加倍（`Tdamp` 40→80 步、`tau_p` 200→400 步），并将 look 步数加倍，以保持相同物理耦合时间和模拟时长；这里保持的是物理时间尺度，不是参数的输入步数值。每一 look 独立丢弃前 10%，block 物理长度 125 fs；每 seed 至少 32 个完整 block；pooled ESS 的 mean 和 squared-deviation/variance 对每个 engine/metric 均至少 1000。

candidate/reference 每 seed 使用相同初始 `model.xyz` 和 potential bytes，并保持相同系综、目标温度/压力、B、Tdamp、tau_p 与 timestep；`run.in` 按实际 run steps 单独锁定，所以 candidate 的预算长度与固定最大长度 reference 的 run 行可以不同。比较时只取两者相同的注册 prefix；每个 timestep profile 各有独立的固定最大长度 reference 链，并只在同一 profile 内跨 look、候选 rank/backend 复用，不能把 looks 当作额外独立样本。候选 job 由同一冻结 binary 按 condition 执行。GPUMD SCR release RNG 从 `system_clock` epoch count 初始化；UTC start 秒互异和串行启动只作保守隔离证据，不表示测到内部 seed，也不按相同 seed 做逐步轨迹配对。

注册统计界为：均值差 `T ±6 K`、`P ±0.1 GPa`、`V ±0.25%` reference mean；T/P/V variance ratio 的 CI 必须整体位于 `[0.85,1.15]`；V pooled ECDF sup ≤0.15；同时报告 T/P/V 的 5/50/95% quantiles、block/seed uncertainty、ACF/IAT/ESS。Variance CI 使用同一 reference center 的全局中心二阶矩 ratio，分层 seed 与每引擎独立 block percentile bootstrap，20,000 replicates、固定 analysis seed。实现、完整合同与输入字段见 [analyzer](../../../tests/npt/analyze_statistics.py)、[CPU tests](../../../tests/npt/test_statistics.py) 和 [preregistration](../../../tests/npt/manifest.json)。正式采样前冻结的文件哈希必须留在执行证据中。

该统计门衡量冻结候选与同条件参考的有限轨迹兼容，不是严格采样证明。禁止 iid KS p-value；不得看正式数据后修改 seeds、burn-in、looks、block、统计 estimator/CI、阈值或模型输入。原统计结果包含所有 FAIL 和 INCONCLUSIVE 结果；当前文档证据包保留正式冻结索引而非逐作业过程材料，统计结论不替代安全检查。
