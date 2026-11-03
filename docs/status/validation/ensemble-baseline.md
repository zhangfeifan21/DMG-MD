# 系综开发前基线验收

## 目标与阶段索引

本文件按同一验收目标整合日期阶段报告。日期按原记录的执行/复核口径列示；若实施和执行跨日，单独注明。各阶段原始身份、命令、结果、失败尝试、边界与产物证据均保留在对应章节。
| 阶段 / 目标 | 执行日期或阶段 | 结论与边界 | 章节 |
|---|---|---|---|
| A0 / 开发前独立基线 | 2026-10-30 | DONE；性能 D4 保持 PENDING | [报告](#ensemble-baseline-20261030) |



<a id="ensemble-baseline-20261030"></a>

<a id="ensemble-baseline-20261030--a0-系综开发前基线冻结2026-10-30"></a>

## 2026-10-30：A0 系综开发前独立基线


类别：验证证据。责任 Step：A0。时间口径：Asia/Shanghai（UTC+08:00）。
状态：**DONE**（2026-10-30，统筹者独立验收完成）。性能项为任务卡明确允许的
**PENDING-D4**，未运行性能 trial。

<a id="ensemble-baseline-20261030--身份与修改边界"></a>
### 身份与修改边界

- 实际 revision：`f06284408f12b6d89ed5a19434c44c958307af7b`。构建前及全部测试后、
  本报告写入前，`git status --porcelain=v1 --untracked-files=all` 均为空。
  本次执行代理仅新增本报告；流程进度及文档索引由统筹者维护。
- 所有 tracked 文件的前后 SHA-256 一致；runtime、parser、compat、NEP 数学、
  测试代码、committed goldens、manifest 和容差均未修改。
- 在 `/tmp/dmgmd-a0-20261030/build/` 独立构建当前源码，保留仓库原 `build/`。
  原 `build/dmg-md` SHA-256 为
  `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32`，前后相同。
- 本轮 candidate：`/tmp/dmgmd-a0-20261030/build/dmg-md`；SHA-256：
  `dc3177b91af90b39b3b7f1a8bac44cc02f5511e6ac6418b136608547509abfa1`。
  构建后与测试后哈希一致；编译清单未出现 reference checkout 源文件。
- 不改变 owned/ghost 生命周期、global ID、数组 stride、halo、MPI collective 或
  输出所有权；GPU 验收沿用一 rank 一 GPU。没有用户可见行为变化。

三个 reference 的 revision、tracked/untracked 状态、ignored 路径及其文件哈希前后相同：
| checkout | 锁定 revision | tracked/untracked | 既有 ignored 文件 |
| --- | --- | --- | ---: |
| GPUMD | `9d23496e41319b9e2af5221a7df6285387401d1e` | clean | 208 |
| LAMMPS | `611ca3b7f8525ba802373b04f9e3d632d515f7e3` | clean | 0 |
| MatPL | `21fabf34eadc57a717f4c7fbce9bb80aad5fd991` | clean | 0 |

GPUMD oracle 二进制 SHA-256 为
`3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`，
由现有 baseline runner 按 committed manifest 核验；运行工作目录均在 `/tmp`。

<a id="ensemble-baseline-20261030--环境"></a>
### 环境
| 项目 | 本轮实际值 |
| --- | --- |
| 主机 / OS | `n4`；Ubuntu 24.04.2 LTS；Linux `5.15.0-60-generic` |
| CPU | 双路 AMD EPYC 7K62，96 个物理核、192 个逻辑 CPU，2 个 NUMA node |
| GPU | 8 × NVIDIA GeForce RTX 4090，单卡 24564 MiB；本轮数值使用 GPU 0，环境门使用 0–3 |
| 驱动 / CUDA Toolkit | `580.173.02` / `12.9.86` |
| Host compiler / CMake / Python | GCC `11.4.0` / CMake `3.28.3` / Python `3.12.3` |
| Open MPI / UCX | Open MPI `5.0.10` / UCX `1.22.0`（revision `8a6b06f`） |
| 构建 | Release；C++17/CUDA17；架构 `75;80;86;89;90`；`-j 8` |
| 环境入口 | 每个新 shell 均 `source ../env/md-mpi.sh`；Open MPI + UCX PML，HCOLL 禁用 |

`../env/md-mpi.sh` 不属于可识别的 Git checkout；其 revision 记为无，SHA-256 为
`3af45518352942d46a2a95db48d4d5e41b9fd46ba79222f13982b3810dccfe96`，前后相同。
完整 GPU UUID、驱动、编译及 MCA/loader 信息保存在原始证据中。

<a id="ensemble-baseline-20261030--实际命令与结果"></a>
### 实际命令与结果

工作目录为包含 `AGENTS.md` 的仓库根目录。原始命令参数、cwd、选定环境变量、起止时间、
退出码与 stdout/stderr 分别保存在同名 JSON/stdout/stderr 文件中。

每个 shell 的环境入口为：

```bash
source ../env/md-mpi.sh
export PYTHONDONTWRITEBYTECODE=1
```

构建及沙箱检查：

```bash
cmake -S . -B /tmp/dmgmd-a0-20261030/build -DCMAKE_BUILD_TYPE=Release
cmake --build /tmp/dmgmd-a0-20261030/build -j 8
ctest --test-dir /tmp/dmgmd-a0-20261030/build --output-on-failure
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a0-20261030/build/dmg-md --devices 0 --ranks 1
```

沙箱 GPU inventory 返回 9，提示无法访问 NVIDIA driver；沙箱 CTest 的 CUDA 项返回
skip，沙箱 MPI 门报告 `UCX transport is unavailable: cuda_copy`。这些原始结果保留。
依照 `AGENTS.md` 使用 `require_escalated` 后，宿主机 GPU 可见，以下命令实际执行：

```bash
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a0-20261030/build/dmg-md --devices 0 --ranks 1
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a0-20261030/build/dmg-md --devices 0,1 --ranks 2
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a0-20261030/build/dmg-md --devices 0,1,2,3 --ranks 4
export TMPDIR=/tmp/dmgmd-a0-20261030/work
ctest --test-dir /tmp/dmgmd-a0-20261030/build --output-on-failure
python3 tests/baseline/run_baselines.py --candidate /tmp/dmgmd-a0-20261030/build/dmg-md --device 0 --keep-work
python3 tests/baseline/run_baselines.py --device 0 --keep-work
python3 tests/long_nve/run_long_nve.py --candidate /tmp/dmgmd-a0-20261030/build/dmg-md --devices 0 --profile smoke --report /tmp/dmgmd-a0-20261030/long-nve-smoke-report.json --keep-work
```

`work/` 在执行前创建；运行器自行创建独立 case/stage 目录。本轮串行执行，任一命令
非零会停止后续数值作业。未执行 `--update-goldens`、`--calibrate` 或 `--adopt-existing`。
| 日志前缀（证据根目录下） | 实际结果 | 退出码 |
| --- | --- | ---: |
| `configure` / `build` | PASS；构建 77.94 s，只有既有未使用函数编译 warning | 0 / 0 |
| `ctest-sandbox` | 18 PASS，`dmgmd.domain_neighbor_cuda` SKIPPED | 0 |
| `mpi-environment-p1-sandbox` | FAIL；沙箱 UCX `cuda_copy` 不可用 | 1 |
| `mpi-environment-p1-host` | PASS；P1 CudaAware 环境主动自检 | 0 |
| `mpi-environment-p2-host` | PASS；P2 CudaAware 环境主动自检 | 0 |
| `mpi-environment-p4-host` | PASS；P4 CudaAware 环境主动自检 | 0 |
| `ctest-host` | **19/19 PASS，无 skip**；26.57 s | 0 |
| `baseline-candidate` | **4/4 PASS**；既有 manifest 容差 | 0 |
| `baseline-reference` | **4/4 PASS**；与 committed goldens byte-exact | 0 |
| `long-nve-smoke` | **完整 smoke PASS**；195.00 s | 0 |

环境门核验 Open MPI/CUDA support、UCX `cuda_copy/cuda_ipc`、唯一 GPU UUID、真实
device-pointer collectives 和 p2p；要求实际 backend 为 CudaAware。P2/P4 仅证明本轮环境门，
不等于完整 MPI numerical differential/domain 矩阵。

CTest 按当前 `tests/CMakeLists.txt` 的 19 项执行；golden 标准中的历史十项表未用来缩减矩阵。
沙箱的 skip/失败由宿主机新记录解除，不覆盖或改写原始日志。

<a id="ensemble-baseline-20261030--科学基线与代表输出"></a>
### 科学基线与代表输出

四个 committed case 为 `single_small_static`、`single_large_nve`、
`multi_nvt_restart`、`nep_zbl_boundary`。candidate 的 baseline 最大绝对差仅出现在
velocity（`8.674e-19`）、virial（`1.776e-15`）和 xyz stress（`6.353e-22`）；
其他报告字段最大差为 0。全部按原 manifest 通过，未改容差。

下表冻结本轮 candidate 的实际输出；`dump_xyz` 实际文件名为 `trajectory.xyz`。
后续 A2 应先与这些哈希比较，再按既有 manifest 解释数值差异。
| case / stage | 文件 | SHA-256 |
| --- | --- | --- |
| NVE `single_large_nve/main` | `thermo.out` | `c098a415aa9b6c4259e8acd7b7e9808370f031be87ceb7d1ff6b3ae3766a0f9f` |
| 同上 | `trajectory.xyz` | `18e1eca40a11fc18d49f94b7070c6b1447ed7e2d5f8da471f841bb7e7e4cb8cf` |
| 同上 | `restart.xyz` | `014af16eba237627ffe3c66fee4938ad4967ada8b5ca87c05a03f505db5bf548` |
| NVT `multi_nvt_restart/initial` | `thermo.out` | `243ca728dc4f810f4b46975c6ba009e81a27e18f85a3d68487c5377c3a491887` |
| 同上 | `trajectory.xyz` | `582aef33397e5ce4bc8f263d0783c1b9cefb09f55882a84f6cc15adaf2ae5524` |
| 同上 | `restart.xyz` | `729265fb52f7a9c0cebe92efc75327fabc48c7b408c261df3942c035a6fe3247` |
| NVT `multi_nvt_restart/resume` | `thermo.out` | `6c4056b335ab30add1371ac6b814f9f1b365d8af0c9370a0cb8b5f09906079fd` |
| 同上 | `trajectory.xyz` | `8cde06e22b17dca4f7f930f3a35341c7e47a612fbe4c7c215dd55e1aadf9c7ce` |
| 同上 | `restart.xyz` | `81bde0abf5da44923042a0022b95f13fb160d3101da43fa09add6e2fb20149ba` |

上述 thermo/restart 与 GPUMD goldens 字节相同；三个 trajectory 文件哈希不同，逐字段
比较通过。不能将本轮 candidate 与 reference 统称为 byte-exact。

thermo 排除 `#` header 后每个数据行均为 **18 列**：
`T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz`。
完整首尾 18 列、每个数据行、header 和参考哈希保存在
`scientific-output-summary.json`，并嵌入 `manifest.json`。下表摘录 T/KE/PE：
| case / stage | 数据行数 | 首行 T / KE / PE | 末行 T / KE / PE |
| --- | ---: | --- | --- |
| NVE main | 7 | `1.2971221924e+02 / 1.3413296214e-01 / -3.7255166531e+01` | `1.9978486858e+02 / 2.0659376865e-01 / -3.7327607632e+01` |
| NVT initial | 6 | `2.3110727279e+01 / 2.6885663633e-02 / -1.2721705437e-01` | `2.5234540726e+01 / 2.9356383539e-02 / -1.2351512909e-01` |
| NVT resume | 2 | `2.5233140503e+01 / 2.9354754602e-02 / -1.2349843979e-01` | `2.4695923038e+01 / 2.8729787335e-02 / -1.2286901474e-01` |

完整 smoke 使用现有 manifest 默认选择：7/7 配置，seed 0、P1、HostStaged，全部
`m1-fallback`。三物理体系执行 100-step NVE、20-step NVT 平衡后 100-step 采样、双向回放和
3 个 P1→P1 restart；四个兼容势执行静态与 10-step 短轨迹。共 70 个 stage 实际执行，
全部首次通过，无复用、失败或重试。测试范围与阈值仍以 frozen manifest 和 runner 为准。

<a id="ensemble-baseline-20261030--性能已知-skip-与未覆盖范围"></a>
### 性能、已知 skip 与未覆盖范围

- **PENDING-D4**：GPU inventory 显示 8 张 GPU 均已有 Python compute context，分别占用
  约 1.7–3.4 GiB；当时采样利用率为 0%，但未建立独占或受控的性能资源条件。
  本轮没有 pilot 或正式性能 trial，没有性能中位数或回归结论，也不将正确性耗时用作性能数据。
  依据 A0 实施要点第 5 项保留此项；D4 应冻结受控 GPU/CPU 资源及同一输入/二进制，再执行
  固定 warmup、步数和三次重复。计划口径及原始进程证据见 `performance-status.json`。
- 沙箱 CUDA skip 和 `cuda_copy` 失败已由宿主机实际通过记录解释；本轮没有观察到数值 flaky。
  此说明只适用于本轮，不能用来解释未来新增代码失败。
- 未执行 nightly、100000-step release、P2/P4 完整数值矩阵、跨 rank restart 或多节点验收；
  smoke 通过不能替代这些门槛。未验证新系综、NPT 或非 NEP 势。
- 所有结论限定于本轮冻结源码、二进制与主机环境。三个 reference 均未产生新文件。

<a id="ensemble-baseline-20261030--证据索引与-dod-交接"></a>
### 证据索引与 DoD 交接

证据根目录：`/tmp/dmgmd-a0-20261030/`。
| 证据 | 用途 |
| --- | --- |
| `manifest.json` | baseline 身份、精确命令、结果、代表输出与限制 |
| `initial.json` / `final-before-report.json` | revision/status、全部 tracked 哈希、reference ignored 清单及哈希 |
| `integrity-comparison.json` | 前后身份比较；原 build 和环境脚本保留证明 |
| `environment-sandbox.json` / `gpu-inventory-host.*` / `gpu-apps-host.*` | OS/CPU/toolchain/MPI/UCX/GPU 及已有 GPU 进程 |
| `ctest-*-detail.log` | 完整逐项测试日志；沙箱与宿主机分开保留 |
| `scientific-output-summary.json` | NVE/NVT 输出哈希、完整 thermo 首尾及所有数据行 |
| `long-nve-smoke-report.json` / `work/` | 完整 smoke 比较、70 stage checkpoint、输入及实际输出 |
| `frozen-input-contracts/` | 原 baseline/long_nve/benchmark manifest 与 calibration 原样副本 |
| `raw-files.sha256.json` | 808 个原始证据文件哈希；排除无关编译中间文件 |

`manifest.json` SHA-256：
`6ca67dbd674eae743647924171af69c520956ed7e8ac860166ee7cc405cb5779`。

`raw-files.sha256.json` SHA-256：
`195e0f58d43819e39d5abe5b1e8ad822dde42904e3a69c8546b15eac8b9a2c5d`。

原始大文件只在本机 `/tmp` 保留；本文不声称这些产物随仓库分发。统筹者已完成独立验收，
原始证据继续保留，供后续 Step 复核。
| A0 / 通用 DoD | 执行证据 |
| --- | --- |
| 仅证据文档，无 runtime/NEP 修改 | PASS；前后源码哈希一致，本次仅新增本报告 |
| 现有 CTest、4-case baseline、smoke | PASS；宿主机 19/19、candidate 4/4、reference 4/4、smoke 7/7 |
| MPI/所有权影响与环境门 | 无实现变化；P1/P2/P4 CudaAware 环境门 PASS |
| revision、环境、命令、结果与限制 | 已冻结至 manifest 和原始日志；统筹者复核了命令结果与封存哈希 |
| NVE / nvt_ber 输出与 thermo | 哈希及全部 18 列数据行已冻结；容差/golden 未修改 |
| 性能基线 | PENDING-D4，按 A0 允许分支记录；未伪造结果 |
| 用户可见合同 | 无变化，无新增兼容承诺 |
| reference、用户修改、提交/推送 | 全部保留；未提交、未推送 |

统筹者独立检查确认：宿主机 CTest 19/19、candidate/reference baseline 各 4/4、long-NVE
smoke 7/7、P1/P2/P4 环境门通过；808 个封存文件哈希及 18 个代表输出哈希均复算匹配，
无代码或 reference 修改。下一步受 G0 阶段门约束，等待维护者确认第 5 节决策；当前进度见
`docs/plans/dmg-md-v1.0-progress.md`。执行代理未开始 A1。回滚本次证据只需移除本报告，
数值代码及原 `build/` 没有需要回滚的修改；原始证据须保留。

<a id="ensemble-baseline-20261030--2026-10-30-后记"></a>
### 2026-10-30 后记

G0 已由维护者于 2026-10-30 确认，A0 已验收，A1 已开始。上文“等待维护者确认第 5 节决策”
和“执行代理未开始 A1”仅反映本报告封存时的状态；原始测试、哈希与证据未改。
