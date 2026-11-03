# 系综基础设施验收

> 2026-11-03 目录整理说明：本文历史测试路径保留执行时写法；相关源文件现位于 [`tests/unit/`](../../../tests/unit/)。见[测试总览](../../../tests/README.md)。

## 目标与阶段索引

本文件按同一验收目标整合日期阶段报告。日期按原记录的执行/复核口径列示；若实施和执行跨日，单独注明。各阶段原始身份、命令、结果、失败尝试、边界与产物证据均保留在对应章节。
| 阶段 / 目标 | 执行日期或阶段 | 结论与边界 | 章节 |
|---|---|---|---|
| A1 / NVT IR 与 parser | 2026-10-30 | 执行验证完成，待独立审核；NHC/BDP 当时仍被预检拒绝 | [报告](#ensemble-parser-20261030) |
| A2 / 共享 EnsembleController 与 Berendsen 迁移 | 2026-10-30 实施，2026-11-02 完成续跑验收 | DONE；宿主机验证于 11-02 完成 | [报告](#ensemble-control-20261102) |



<a id="ensemble-parser-20261030"></a>

<a id="ensemble-parser-20261030--a1nvt-ir-parser-扩展验收2026-10-30"></a>

## 2026-10-30：A1 NVT IR 与 parser


<a id="ensemble-parser-20261030--结论与状态"></a>
### 结论与状态

**执行验证完成，待统筹者独立审核；本记录不将 A1 标记为 DONE。** 本轮仅实施 A1。
代码基于 revision `f06284408f12b6d89ed5a19434c44c958307af7b`，工作区 dirty；未提交、未推送。
`nvt_nhc` / `nvt_bdp` 的 5-token 语法已接受，两种动力学当前仍由整个程序预检明确拒绝。
NPT 仅预留枚举，parser 仍报 unsupported。用户兼容矩阵按任务卡留待 A6 更新。
| 验收 | 实际结果 | 原始证据（目录均为 `/tmp/dmgmd-a1-20261030/`） |
| --- | --- | --- |
| 独立 Release 构建 | PASS，退出 0 | `configure.*`、`build.*` |
| parser 专项 | PASS，1/1；113 个系综案例，另保留原有 2 个测试函数 | `parser.*`、`parser-detail.log`、`parser-cases.json` |
| 完整沙箱 CTest | 18 passed、1 CUDA skip，0 failed | `ctest-sandbox.*`、`ctest-sandbox-detail.log` |
| 完整宿主机 CTest | **19/19 passed，0 failed，0 skipped** | `ctest-host.*`、`ctest-host-detail.log` |
| A0/A1 旧系综 parser 对照 | **36/36 一致**：字段、来源、错误、退出码 | `legacy-diff.*`、`legacy-differential.json`、`legacy-differential/` |
| diff / 所有权检查 | PASS，仅获准 5 个 tracked 源码/测试文件变化，另新增本文 | `a1-source.diff`、`a1-source.stat`、`ownership-check.json` |

沙箱 CUDA 用例的真实输出为 `domain_neighbor_cuda_tests: skipped (no CUDA device)`，
由现有 CTest `SKIP_RETURN_CODE 77` 处理。按 AGENTS.md 请求沙箱外执行完整 CTest 后，该用例输出
`domain_neighbor_cuda_tests: all checks passed`。两次结果分别保留；未把真实失败改成 skip。
构建有既有 `src/gpumd_compat/neighbor.cu:367` 未使用函数警告（5 个 CUDA architecture），未修改该文件。
CTest 中的 long-NVE/benchmark/overlap 项是分析脚本测试，本记录不把它们解释为新做的 GPU 数值矩阵。

<a id="ensemble-parser-20261030--范围对象所有权与-mpi"></a>
### 范围、对象所有权与 MPI
| 文件 / 符号 | 本轮变化 |
| --- | --- |
| `include/dmgmd/run_ir.hpp` / `EnsembleKind` | 在 `nve=0`、`nvt_ber=1` 后追加 NHC、BDP、两个保留 NPT 枚举；三温控字段及默认值原样复用 |
| `src/run_parser.cpp` / `parse_command` | 三种 NVT 共用原有严格 token 数、finite、正温度与耦合校验；其它系综仍 unsupported |
| `run_ir.hpp` / `run_parser.cpp` / `validate_runtime_ensembles` | 新增纯 host 的整个程序预检，保留 `InputError` 来源，拒绝未实现/保留/非法 kind |
| `src/runtime.cu` / `run_replicated` | 在既有 run/model/potential 三项一致性检查之后、`initialize_device` 之前调用预检 |
| `src/domain_runtime.cu` / `run_local_domain` | 入口再次调用同一预检，位于 local-domain 数据面分配之前 |
| `tests/run_parser_tests.cpp` | 正负例、精确来源、重复及分段行为、生产 guard 函数和保留枚举检查 |
| 本文 | 新增 A1 验收记录；主计划、进度与索引由统筹者维护 |

IR 继续由各 rank 作为 host 输入持有。预检只读取 `RunProgram`，不拥有或修改 atom、owned/ghost、
位置、速度、能量、virial、box、NEP workspace、RNG 或控制器状态。未新增 collective、设备操作或 I/O；
既有三项输入 fingerprint 的 collective 顺序不变。输入一致后各 rank 对同一程序作同一拒绝判断。
预检扫描全部命令，故位于先前 `run` 之后、被后续旧系综覆盖或没有后续 `run` 的 NHC/BDP 也会拒绝。
用户错误说明为“not implemented in the current version”，不包含内部 Step 编号。

没有修改 NEP/compat 数学、golden、容差、CMake、MPI 实现、数值积分或 thermostat 公式。
本轮不生成新系综轨迹；A1 证据限定于 parser、host guard 与完整既有 CTest。

<a id="ensemble-parser-20261030--revision工作区与已有修改"></a>
### Revision、工作区与已有修改

- 起始快照：`initial.json`，2026-10-30 15:09:38 +08:00。当时已有 `docs/README.md`、
  `docs/plans/dmg-md-v1.0-development-workflow.md` 修改，以及未跟踪的 progress 与 A0 status 两文件。
- 开工前刷新：`refreshed-before-edit.json`，22:14:32 +08:00。用户/统筹者在起始快照后又修改了下列
  文档；全部作为 **A1 之外的已有修改** 保留。获准 5 个源码/测试文件在刷新时仍与起始快照一致。
- 完成源码验收快照：`final-before-report.json`，22:24:17 +08:00。与刷新快照比较，仅 5 个获准 tracked
  文件变化；14 个外部 tracked 文档及 2 个已有 untracked 文件的 SHA-256 全部保留。
- 仓库原 `build/dmg-md` 哈希保持 `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32`；
  主仓库 ignored 路径集合保持不变（192152 项）。所有构建、日志与辅助探针均放 A1 `/tmp` 目录。

刷新时的外部 dirty 文档（前两份计划/索引文档在 15:09 后也有后续修改）：

```text
AGENTS.md
README.md
docs/README.md
docs/operations/deployment.md
docs/plans/communication-computation-overlap.md
docs/plans/dmg-md-v1.0-development-workflow.md
docs/plans/risk-and-backlog.md
docs/reference/gpumd-runtime-audit.md
docs/standards/architecture-decisions.md
docs/standards/cross-engine-reference.md
docs/standards/golden-test-standard.md
docs/standards/mpi-runtime.md
docs/status/current.md
tests/benchmark/README.md
```

已有未跟踪文件 `docs/plans/dmg-md-v1.0-progress.md`、
`docs/status/validation/ensemble-baseline-20261030.md` 也未编辑。

三个 reference 均保持 revision、Git 状态及所有 ignored 文件哈希：
| checkout | revision | tracked/untracked 状态 | ignored 文件 |
| --- | --- | --- | --- |
| GPUMD | `9d23496e41319b9e2af5221a7df6285387401d1e` | clean | 原有 208 项全部不变 |
| LAMMPS | `611ca3b7f8525ba802373b04f9e3d632d515f7e3` | clean | 0 |
| LAMMPS-MatPL | `21fabf34eadc57a717f4c7fbce9bb80aad5fd991` | clean | 0 |

只读链接 A0 `/tmp` 库制作旧 parser 探针；未重建或写入 A0 目录。A0 候选与封存 manifest 的哈希保持不变。
环境脚本所在目录不是独立 Git 仓库，快照查询该目录 revision 时产生的 Git 提示不属于测试失败；脚本以 SHA-256 标识。

<a id="ensemble-parser-20261030--构建与运行环境"></a>
### 构建与运行环境

- 构建目录：`/tmp/dmgmd-a1-20261030/build`；Release、C++17/CUDA17、GNU C++ 11.4.0、CMake 3.28.3。
- CUDA 12.9.86，目标架构 `75;80;86;89;90`；未修改环境或编译选项。
- Open MPI 5.0.10：`/data/home/zhangyifei/software/openmpi-cuda`；UCX 1.22.0（`8a6b06f`）：
  `/data/home/zhangyifei/software/ucx-cuda`。PML `ucx`，collective 配置 `^hcoll`。
- 主机 `n4`，8 × NVIDIA GeForce RTX 4090，driver `580.173.02`。宿主机完整 CTest 的 CUDA 用例执行通过。
  A1 不运行新的 MPI 数值矩阵，也不从 CTest 推断所有 GPU 或跨 rank 数值行为。
- 每个构建/测试 shell 均先 `source ../env/md-mpi.sh`；脚本 SHA-256：
  `3af45518352942d46a2a95db48d4d5e41b9fd46ba79222f13982b3810dccfe96`。
- A1 候选 `dmg-md` SHA-256：`155a5d8594ce2f823e5b198c7c4140d9084d077c93e4ad13e239dd454ba37dfd`；其它二进制、库、编译命令与 CMake cache 哈希见 `build-identity.json`。

<a id="ensemble-parser-20261030--精确命令与记录"></a>
### 精确命令与记录

以下命令的工作目录均为 `/fdata/zhangyifei/zhangyifei/newmd/newmd`。每个新 shell 的前置设置：

```sh
source ../env/md-mpi.sh
export PYTHONDONTWRITEBYTECODE=1
```

实际由 `python3 /tmp/dmgmd-a1-20261030/capture_command.py <记录名> -- <命令>` 包装，
逐次保存 argv、开始/结束时刻、环境、退出码以及原始 stdout/stderr。包装器拒绝覆盖原始证据。
`ctest-host` 与 `environment-host` 使用 `sandbox_permissions=require_escalated`；自动审批通过。
下面列出的 CTest 命令可直接复验；旧 parser 探针的逐条输入与 argv 另存 `legacy-differential.json`。

<a id="ensemble-parser-20261030--configure"></a>
#### configure

```sh
cmake -S . -B /tmp/dmgmd-a1-20261030/build -DCMAKE_BUILD_TYPE=Release
```

退出码 `0`；2026-10-30T22:17:25.125936+08:00 至 2026-10-30T22:17:31.207573+08:00；原始输出 `configure.stdout` / `configure.stderr`。

<a id="ensemble-parser-20261030--build"></a>
#### build

```sh
cmake --build /tmp/dmgmd-a1-20261030/build -j 8
```

退出码 `0`；2026-10-30T22:18:01.719882+08:00 至 2026-10-30T22:19:19.635261+08:00；原始输出 `build.stdout` / `build.stderr`。

<a id="ensemble-parser-20261030--parser"></a>
#### parser

```sh
ctest --test-dir /tmp/dmgmd-a1-20261030/build -R '^dmgmd\.run_parser$' --output-on-failure
```

退出码 `0`；2026-10-30T22:20:15.965566+08:00 至 2026-10-30T22:20:16.002304+08:00；原始输出 `parser.stdout` / `parser.stderr`。

<a id="ensemble-parser-20261030--ctest-sandbox"></a>
#### ctest-sandbox

```sh
ctest --test-dir /tmp/dmgmd-a1-20261030/build --output-on-failure
```

退出码 `0`；2026-10-30T22:20:25.096745+08:00 至 2026-10-30T22:20:51.763964+08:00；原始输出 `ctest-sandbox.stdout` / `ctest-sandbox.stderr`。

<a id="ensemble-parser-20261030--ctest-host"></a>
#### ctest-host

```sh
ctest --test-dir /tmp/dmgmd-a1-20261030/build --output-on-failure
```

退出码 `0`；2026-10-30T22:21:44.923610+08:00 至 2026-10-30T22:22:11.299931+08:00；原始输出 `ctest-host.stdout` / `ctest-host.stderr`。

<a id="ensemble-parser-20261030--legacy-build-baseline"></a>
#### legacy-build-baseline

```sh
/usr/bin/c++ -std=c++17 -O3 -DNDEBUG -I/tmp/dmgmd-a1-20261030/before/include -Iinclude /tmp/dmgmd-a1-20261030/legacy_probe.cpp -o /tmp/dmgmd-a1-20261030/legacy-probe-baseline /tmp/dmgmd-a0-20261030/build/libdmgmd_input.a /tmp/dmgmd-a0-20261030/build/libgpumd_compat.a /usr/local/cuda-12.9/targets/x86_64-linux/lib/libcudart.so
```

退出码 `0`；2026-10-30T22:23:07.784279+08:00 至 2026-10-30T22:23:08.341843+08:00；原始输出 `legacy-build-baseline.stdout` / `legacy-build-baseline.stderr`。

<a id="ensemble-parser-20261030--legacy-build-candidate"></a>
#### legacy-build-candidate

```sh
/usr/bin/c++ -std=c++17 -O3 -DNDEBUG -Iinclude /tmp/dmgmd-a1-20261030/legacy_probe.cpp -o /tmp/dmgmd-a1-20261030/legacy-probe-candidate /tmp/dmgmd-a1-20261030/build/libdmgmd_input.a /tmp/dmgmd-a1-20261030/build/libgpumd_compat.a /usr/local/cuda-12.9/targets/x86_64-linux/lib/libcudart.so
```

退出码 `0`；2026-10-30T22:23:22.032632+08:00 至 2026-10-30T22:23:22.593705+08:00；原始输出 `legacy-build-candidate.stdout` / `legacy-build-candidate.stderr`。

<a id="ensemble-parser-20261030--legacy-diff"></a>
#### legacy-diff

```sh
python3 /tmp/dmgmd-a1-20261030/legacy_diff.py
```

退出码 `0`；2026-10-30T22:23:22.826578+08:00 至 2026-10-30T22:23:23.109597+08:00；原始输出 `legacy-diff.stdout` / `legacy-diff.stderr`。

<a id="ensemble-parser-20261030--environment-host"></a>
#### environment-host

```sh
python3 /tmp/dmgmd-a1-20261030/environment.py
```

退出码 `0`；2026-10-30T22:24:16.323468+08:00 至 2026-10-30T22:24:16.947056+08:00；原始输出 `environment-host.stdout` / `environment-host.stderr`。

<a id="ensemble-parser-20261030--parser-用例表"></a>
### Parser 用例表

下表编号与 `parser-detail.log` / `parser-cases.json` 一一对应。
`typed NVT fields` 表示已断言 kind 和 T1/T2/Tdamp 三字段与输入对应的 double 值逐字段相等；
`comment excluded` 同时断言注释前的三字段。合法耦合覆盖 1、50、1e9、1e12；双温度覆盖升温、降温及科学计数。
每种 NVT 各 31 例（5 正例、26 负例），错误测试校验完整 `InputError` 消息及临时输入文件名、第 3 行与原文。
case 94 校验重复 NVE/NVT、连续 run、多段切换、字段默认值及旧 enum 值；case 103–110 调用实际生产预检，
校验相应第 1/3 行；case 111–113 校验构造 IR 的 `constructed.run:42` 来源。
现有主程序继续将 `InputError.location()` 格式化成文件名和行号，未修改该格式化逻辑。
| # | 输入（`\n` 表示换行） | 期望 | 实际 |
| --- | --- | --- | --- |
| 1 | `ensemble nvt_ber 300 300 1` | typed NVT fields | typed NVT fields |
| 2 | `ensemble nvt_ber 100 200 50` | typed NVT fields | typed NVT fields |
| 3 | `ensemble nvt_ber 200 100 1000000000` | typed NVT fields | typed NVT fields |
| 4 | `ensemble nvt_ber 1e-6 1e6 1e12` | typed NVT fields | typed NVT fields |
| 5 | `ensemble nvt_ber 300 300 1 # ignored extra tokens` | comment excluded from token count | comment excluded from token count |
| 6 | `ensemble nvt_ber` | ensemble nvt_ber should have 3 parameters | ensemble nvt_ber should have 3 parameters |
| 7 | `ensemble nvt_ber 300` | ensemble nvt_ber should have 3 parameters | ensemble nvt_ber should have 3 parameters |
| 8 | `ensemble nvt_ber 300 300` | ensemble nvt_ber should have 3 parameters | ensemble nvt_ber should have 3 parameters |
| 9 | `ensemble nvt_ber 300 300 1 extra` | ensemble nvt_ber should have 3 parameters | ensemble nvt_ber should have 3 parameters |
| 10 | `ensemble nvt_ber 0 300 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 11 | `ensemble nvt_ber -1 300 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 12 | `ensemble nvt_ber 300 0 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 13 | `ensemble nvt_ber 300 -1 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 14 | `ensemble nvt_ber 300 300 0` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 15 | `ensemble nvt_ber 300 300 0.5` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 16 | `ensemble nvt_ber 300 300 -1` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 17 | `ensemble nvt_ber nan 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 18 | `ensemble nvt_ber inf 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 19 | `ensemble nvt_ber -inf 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 20 | `ensemble nvt_ber 1e309 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 21 | `ensemble nvt_ber oops 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 22 | `ensemble nvt_ber 300 nan 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 23 | `ensemble nvt_ber 300 inf 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 24 | `ensemble nvt_ber 300 -inf 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 25 | `ensemble nvt_ber 300 1e309 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 26 | `ensemble nvt_ber 300 oops 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 27 | `ensemble nvt_ber 300 300 nan` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 28 | `ensemble nvt_ber 300 300 inf` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 29 | `ensemble nvt_ber 300 300 -inf` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 30 | `ensemble nvt_ber 300 300 1e309` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 31 | `ensemble nvt_ber 300 300 oops` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 32 | `ensemble nvt_nhc 300 300 1` | typed NVT fields | typed NVT fields |
| 33 | `ensemble nvt_nhc 100 200 50` | typed NVT fields | typed NVT fields |
| 34 | `ensemble nvt_nhc 200 100 1000000000` | typed NVT fields | typed NVT fields |
| 35 | `ensemble nvt_nhc 1e-6 1e6 1e12` | typed NVT fields | typed NVT fields |
| 36 | `ensemble nvt_nhc 300 300 1 # ignored extra tokens` | comment excluded from token count | comment excluded from token count |
| 37 | `ensemble nvt_nhc` | ensemble nvt_nhc should have 3 parameters | ensemble nvt_nhc should have 3 parameters |
| 38 | `ensemble nvt_nhc 300` | ensemble nvt_nhc should have 3 parameters | ensemble nvt_nhc should have 3 parameters |
| 39 | `ensemble nvt_nhc 300 300` | ensemble nvt_nhc should have 3 parameters | ensemble nvt_nhc should have 3 parameters |
| 40 | `ensemble nvt_nhc 300 300 1 extra` | ensemble nvt_nhc should have 3 parameters | ensemble nvt_nhc should have 3 parameters |
| 41 | `ensemble nvt_nhc 0 300 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 42 | `ensemble nvt_nhc -1 300 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 43 | `ensemble nvt_nhc 300 0 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 44 | `ensemble nvt_nhc 300 -1 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 45 | `ensemble nvt_nhc 300 300 0` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 46 | `ensemble nvt_nhc 300 300 0.5` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 47 | `ensemble nvt_nhc 300 300 -1` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 48 | `ensemble nvt_nhc nan 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 49 | `ensemble nvt_nhc inf 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 50 | `ensemble nvt_nhc -inf 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 51 | `ensemble nvt_nhc 1e309 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 52 | `ensemble nvt_nhc oops 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 53 | `ensemble nvt_nhc 300 nan 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 54 | `ensemble nvt_nhc 300 inf 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 55 | `ensemble nvt_nhc 300 -inf 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 56 | `ensemble nvt_nhc 300 1e309 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 57 | `ensemble nvt_nhc 300 oops 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 58 | `ensemble nvt_nhc 300 300 nan` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 59 | `ensemble nvt_nhc 300 300 inf` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 60 | `ensemble nvt_nhc 300 300 -inf` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 61 | `ensemble nvt_nhc 300 300 1e309` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 62 | `ensemble nvt_nhc 300 300 oops` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 63 | `ensemble nvt_bdp 300 300 1` | typed NVT fields | typed NVT fields |
| 64 | `ensemble nvt_bdp 100 200 50` | typed NVT fields | typed NVT fields |
| 65 | `ensemble nvt_bdp 200 100 1000000000` | typed NVT fields | typed NVT fields |
| 66 | `ensemble nvt_bdp 1e-6 1e6 1e12` | typed NVT fields | typed NVT fields |
| 67 | `ensemble nvt_bdp 300 300 1 # ignored extra tokens` | comment excluded from token count | comment excluded from token count |
| 68 | `ensemble nvt_bdp` | ensemble nvt_bdp should have 3 parameters | ensemble nvt_bdp should have 3 parameters |
| 69 | `ensemble nvt_bdp 300` | ensemble nvt_bdp should have 3 parameters | ensemble nvt_bdp should have 3 parameters |
| 70 | `ensemble nvt_bdp 300 300` | ensemble nvt_bdp should have 3 parameters | ensemble nvt_bdp should have 3 parameters |
| 71 | `ensemble nvt_bdp 300 300 1 extra` | ensemble nvt_bdp should have 3 parameters | ensemble nvt_bdp should have 3 parameters |
| 72 | `ensemble nvt_bdp 0 300 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 73 | `ensemble nvt_bdp -1 300 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 74 | `ensemble nvt_bdp 300 0 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 75 | `ensemble nvt_bdp 300 -1 1` | initial and final temperatures should be > 0 | initial and final temperatures should be > 0 |
| 76 | `ensemble nvt_bdp 300 300 0` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 77 | `ensemble nvt_bdp 300 300 0.5` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 78 | `ensemble nvt_bdp 300 300 -1` | temperature coupling should be >= 1 | temperature coupling should be >= 1 |
| 79 | `ensemble nvt_bdp nan 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 80 | `ensemble nvt_bdp inf 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 81 | `ensemble nvt_bdp -inf 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 82 | `ensemble nvt_bdp 1e309 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 83 | `ensemble nvt_bdp oops 300 1` | initial temperature should be a finite real number | initial temperature should be a finite real number |
| 84 | `ensemble nvt_bdp 300 nan 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 85 | `ensemble nvt_bdp 300 inf 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 86 | `ensemble nvt_bdp 300 -inf 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 87 | `ensemble nvt_bdp 300 1e309 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 88 | `ensemble nvt_bdp 300 oops 1` | final temperature should be a finite real number | final temperature should be a finite real number |
| 89 | `ensemble nvt_bdp 300 300 nan` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 90 | `ensemble nvt_bdp 300 300 inf` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 91 | `ensemble nvt_bdp 300 300 -inf` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 92 | `ensemble nvt_bdp 300 300 1e309` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 93 | `ensemble nvt_bdp 300 300 oops` | temperature coupling should be a finite real number | temperature coupling should be a finite real number |
| 94 | `ensemble nve\nensemble nvt_ber 100 200 50\nrun 2\nrun 3\nensemble nve\nensemble nve\nrun 4\nensemble nvt_ber 200 100 1\nrun 5\n` | legacy duplicate/segment fields and guard pass | legacy duplicate/segment fields and guard pass |
| 95 | `ensemble` | ensemble type is missing | ensemble type is missing |
| 96 | `ensemble nve 1` | ensemble nve should have 0 parameters | ensemble nve should have 0 parameters |
| 97 | `ensemble nvt_lan 300 300 100` | unsupported command 'ensemble nvt_lan' in the current version | unsupported command 'ensemble nvt_lan' in the current version |
| 98 | `ensemble nvt_bao 300 300 100` | unsupported command 'ensemble nvt_bao' in the current version | unsupported command 'ensemble nvt_bao' in the current version |
| 99 | `ensemble npt_ber 300 300 100 0 100 1000` | unsupported command 'ensemble npt_ber' in the current version | unsupported command 'ensemble npt_ber' in the current version |
| 100 | `ensemble npt_scr 300 300 100 0 100 1000` | unsupported command 'ensemble npt_scr' in the current version | unsupported command 'ensemble npt_scr' in the current version |
| 101 | `ensemble npt_mttk 300 300 100 0 100 1000` | unsupported command 'ensemble npt_mttk' in the current version | unsupported command 'ensemble npt_mttk' in the current version |
| 102 | `ensemble heat 300 300 100` | unsupported command 'ensemble heat' in the current version | unsupported command 'ensemble heat' in the current version |
| 103 | `ensemble nvt_nhc 300 300 1\nrun 1\n` | ensemble nvt_nhc is not implemented in the current version | ensemble nvt_nhc is not implemented in the current version |
| 104 | `ensemble nve\nrun 1\nensemble nvt_nhc 300 300 1\nrun 1\n` | ensemble nvt_nhc is not implemented in the current version | ensemble nvt_nhc is not implemented in the current version |
| 105 | `ensemble nvt_nhc 300 300 1\nensemble nvt_ber 300 300 1\nrun 1\n` | ensemble nvt_nhc is not implemented in the current version | ensemble nvt_nhc is not implemented in the current version |
| 106 | `# no following run\n\nensemble nvt_nhc 300 300 1\n` | ensemble nvt_nhc is not implemented in the current version | ensemble nvt_nhc is not implemented in the current version |
| 107 | `ensemble nvt_bdp 300 300 1\nrun 1\n` | ensemble nvt_bdp is not implemented in the current version | ensemble nvt_bdp is not implemented in the current version |
| 108 | `ensemble nve\nrun 1\nensemble nvt_bdp 300 300 1\nrun 1\n` | ensemble nvt_bdp is not implemented in the current version | ensemble nvt_bdp is not implemented in the current version |
| 109 | `ensemble nvt_bdp 300 300 1\nensemble nvt_ber 300 300 1\nrun 1\n` | ensemble nvt_bdp is not implemented in the current version | ensemble nvt_bdp is not implemented in the current version |
| 110 | `# no following run\n\nensemble nvt_bdp 300 300 1\n` | ensemble nvt_bdp is not implemented in the current version | ensemble nvt_bdp is not implemented in the current version |
| 111 | `constructed kind 4` | unsupported | unsupported |
| 112 | `constructed kind 5` | unsupported | unsupported |
| 113 | `constructed kind 999` | unsupported | unsupported |

<a id="ensemble-parser-20261030--与-a0-的旧行为直接对照"></a>
### 与 A0 的旧行为直接对照

`legacy_probe.cpp` 分别链接 A0 与 A1 的 `libdmgmd_input.a` / `libgpumd_compat.a`，
使用各自的 IR 头文件，输出 kind、三参数的 C++ hexadecimal floating-point 表示、run 步数和完整来源。
同一输入路径由两个独立可执行文件读取，避免将临时路径差异误判为来源变化。
共 36 例：上表 1–31、94–96，加 NVE 正例及 NVE 尾注释正例。有效输出逐字节相等即保证逐字段相等；
非法输入的退出码 2、错误信息、文件/行/原文也逐字节相等。所有例子实际为 36/36 PASS。
这项对照是 parser 兼容证据，不是 A2 要求的 GPU 轨迹字节对照。

<a id="ensemble-parser-20261030--dod-与下一步"></a>
### DoD 与下一步
| A1 DoD | 证据与判定 |
| --- | --- |
| NHC/BDP 严格 5-token，T 有限且 >0、Tdamp 有限且 >=1 | 62 个对应 parser 案例通过；错误 token/非有限/负值/边界均覆盖 |
| 旧 NVE/nvt_ber enum、字段、默认值、重复与分段行为保留 | static_assert、case 1–31/94–96 与 36 例 A0/A1 对照通过 |
| 未实现新系综不得静默进入动力学 | 生产 host guard 的 8 个整程序负例通过；两个 runtime 入口调用位于动力学之前 |
| NPT / lan / bao / heat 仍 unsupported | case 97–102；构造保留/非法 IR 的 case 111–113 也拒绝 |
| 错误保留文件与行号 | parser 和 guard 的 `InputError.location()` 精确断言通过 |
| parser 专项与完整 CTest | 专项 1/1，宿主机完整 19/19；沙箱 skip 独立保留 |
| 范围、原子所有权与 MPI 不变 | 5 文件源码 diff；预检无 state、无 collective；reference 与外部文档哈希检查通过 |

无实现或测试阻塞。**恢复后唯一下一步：由统筹者独立检查 A1 diff、原始日志、哈希与 DoD，
并更新 docs/plans 进度、主计划与 docs/README 索引。** 审核证据不足时继续停留 A1。
本执行代理不开始 A2，不自行标 DONE。

<a id="ensemble-parser-20261030--封存索引"></a>
### 封存索引

原始证据根目录 `/tmp/dmgmd-a1-20261030/`；`manifest.json` 汇总命令、结果、源码身份和所有权核对，
`raw-files.sha256.json` 覆盖原始日志、逐例输出、前后源码快照与选定构建产物。
manifest SHA-256：`1818ad68d5543492ca439340298696d230212dc8aa68725d9e0be77bb45c3325`。
| A1 文件 | SHA-256 |
| --- | --- |
| `include/dmgmd/run_ir.hpp` | `809cff67b6dfd0df018a54eda244edc3c783103d382e32d33fcdef019b0be2f8` |
| `src/run_parser.cpp` | `d787980fb3629637c365794b1f2815e0e9ca92b4705563e2377f90d6a2d071eb` |
| `src/runtime.cu` | `f357c37e8194bfab412984b624ee88d322d7484658f4e14c1e23731638324f3d` |
| `src/domain_runtime.cu` | `7921fa3c7b0b9c74f9cc33e1f6dd949d3f9a6777398d8afa5c5d3ed06dfa2b17` |
| `tests/run_parser_tests.cpp` | `806c936ae2a5d8408cc85bdf4174b3509ff1d278fe1aa3510b61a19f12a34f81` |


<a id="ensemble-control-20261102"></a>

<a id="ensemble-control-20261102--a2共享-ensemblecontroller-与-berendsen-迁移验收2026-11-02"></a>

## 2026-10-30 实施 / 2026-11-02 验收：A2 共享 EnsembleController 与 Berendsen 迁移


<a id="ensemble-control-20261102--结论与状态"></a>
### 结论与状态

**统筹者于 2026-11-02 独立审核通过，A2 状态为 DONE。** 本轮唯一 Step 为 A2。
源码和构建完成于 2026-10-30；经续跑前报告审核后，于 2026-11-02 顺序完成剩余宿主机验收。
revision 为 `f06284408f12b6d89ed5a19434c44c958307af7b`，工作区 dirty；未提交、未推送。
M1/M2a 共用纯 host 控制器，现有 NVE/`nvt_ber` 的科学输出与通信记录保持一致。
NHC/BDP 动力学仍由 A1 两处预检拒绝；没有开始 A3/A4。

全部原始证据位于 `/tmp/dmgmd-a2-20261030/`；下文使用该目录内的相对文件名。
`manifest.json` 与 `evidence-sha256.json` 汇总命令、输入/输出、源码快照、二进制和逐文件 SHA-256。
保留旧日志，续跑使用新的 `*-resume-20261102` 记录名。没有失败后放宽阈值或改写 golden。
| 验收项 | 实际结果 | 原始证据 |
| --- | --- | --- |
| 独立 Release 构建 | PASS，配置与构建退出 0 | `configure.*`、`build.*` |
| controller + parser 专项 | PASS，2/2；controller 128 案例 | `focused.*`、`focused-detail.log` |
| 沙箱完整 CTest | 19 passed、1 CUDA skip、0 failed（20 项） | `ctest-sandbox.*`、`ctest-sandbox-detail.log` |
| A1 host 环境门 | PASS，Open MPI/UCX/CudaAware P2 | `environment-before-host.*` |
| A2 host 环境门 | PASS，Open MPI/UCX/CudaAware P2 | `environment-after-host-resume-20261102.*` |
| 宿主机完整 CTest | **20/20 passed，0 failed，0 skipped** | `ctest-host-resume-20261102.*`、同名 `-detail.log` |
| A1/A2 单 rank golden | 各 4 case / 5 stage 通过；另比较 20 对科学文件 SHA，全相等 | `golden-*-host-resume-20261102.*`、`golden-ab-results.json` |
| M1 双后端 P1/P2 | 各候选 20 stage；80 对科学文件 SHA、76 对全局逐步记录相等 | `mpi-*-host-resume-20261102.*`、`m1-ab-results.json` |
| M2a runner（含固定附加检查） | 各候选 30 stage；101 对科学文件 SHA、155 对全局记录、160 对 rank 记录、30 对 segment summary 相等 | `domain-*-host-resume-20261102.*`、`ab-results.json` |
| M1+M2a 总比较 | **50 stage、181 对科学文件、231 对全局逐步记录、160 对 rank 记录完全一致** | `compare-ab-resume-20261102.*`、`ab-results.json` |
| 文件保护及最终 diff | PASS；A2 源码与实际构建快照一致 | `final-before-report.json`、`final-ownership.json`、`a2-source.diff` |

沙箱 CUDA 测试按现有 `SKIP_RETURN_CODE 77` 报无设备；宿主机同一测试输出
`domain_neighbor_cuda_tests: all checks passed`。两份日志分别保留。
构建仅有原 `src/gpumd_compat/neighbor.cu:367` unused-function 警告（5 个架构），未修改该文件。
CTest 中的 long-NVE/benchmark/overlap 项是分析脚本测试；本轮没有新增长程或性能矩阵。

<a id="ensemble-control-20261102--实施范围所有权与数值顺序"></a>
### 实施范围、所有权与数值顺序
| 文件 / 符号 | A2 变化 |
| --- | --- |
| `include/dmgmd/ensemble_control.hpp` / `EnsembleController` | 新增配置副本、目标温度、thermo 需求查询和 pre/post-force hook；仅 host 标量 |
| `src/ensemble_control.cpp` | 抽取原温度调度与 Berendsen 因子；拒绝未实现/保留 kind |
| `src/runtime.cu` / `run_segment` | 每段创建控制器；复用原 owned-index 速度缩放 kernel；保持每步 thermo |
| `src/domain_runtime.cu` / `run_segment` | 每段创建控制器；复用原 owned-prefix kernel；保留 NVE 按 consumer 计算 thermo、NVT 每步计算 |
| `CMakeLists.txt`、`tests/CMakeLists.txt` | 纯 C++ 静态库及 `dmgmd.ensemble_control` CTest 注册；单测只链接控制器库 |
| `tests/ensemble_control_tests.cpp` | 128 个标量/生命周期/无分配/拒绝案例 |
| 本文 | 新增 A2 status；主计划、进度与 docs 索引由统筹者维护 |

控制器只复制 `EnsembleCommand`，不持有 atom buffer、owned/ghost 数据或 thermo 指针。
NVE/Berendsen 不创建链或 RNG，不分配隐藏 heap 状态；各 rank 对同一已归约全局温度调用纯函数。
未来有状态系综的 rank 0 所有权边界已注释；本轮没有增加广播或归约。

每段 `run` 新建控制器；目标温度严格保留 `fraction = double(step)/double(steps)` 后
`T1 + (T2-T1)*fraction` 的表达式顺序，`step` 为段内 0 基，末步使用 `steps-1`。
`before_force(time_step, nullopt)` 位于速度修正/自适应 dt 后、VV1 前；当前返回无动作，
不虚构 KE，也不为此计算或通信。VV1 → 位置/halo/force/ownership → VV2 → 既有全局 thermo
归约及 `normalize_global_thermo` → `after_force` → owned 速度缩放 → 输出的顺序保留。
thermo 文件仍使用缩放前 thermo；XYZ/restart 中的速度仍为缩放后 owned 权威数据。

`coupling = 1.0/TAU`，只有 `coupling > 1.0e-5` 才计算
`sqrt(1.0 + coupling*(target/T - 1.0))` 并执行原 kernel。活动因子恰好 1 仍发起原缩放；
弱耦合返回无动作，但 M2a NVT 的 thermo consumer 仍在。kernel 的参数、launch、写范围和
迁移后的 ownership/stride 查询不变。ghost 不积分、不重复计入 thermo，rank 0 文件输出规则不变。

A1 的 IR/parser、两个 `validate_runtime_ensembles(program)` guard、`runtime_internal.hpp`、
MPI 实现、`src/gpumd_compat/`、golden 与容差均未修改。
最终静态核对确认两处 owned 缩放 kernel 原样保留、两 guard 各出现一次；两 runtime 的
`mpi.*(...)` 方法调用序列与 A1 快照完全相同（分别 52/54 个文本调用点，**不是运行时调用计数**）。

<a id="ensemble-control-20261102--构建与运行身份"></a>
### 构建与运行身份
| 项 | 记录 |
| --- | --- |
| A1 before | `/tmp/dmgmd-a1-20261030/build/dmg-md` |
| A1 SHA-256 | `155a5d8594ce2f823e5b198c7c4140d9084d077c93e4ad13e239dd454ba37dfd` |
| A2 after | `/tmp/dmgmd-a2-20261030/build/dmg-md` |
| A2 SHA-256 | `e040cb5a28da53eff53ada0914f40e75d9000382a0cc8f5cd0b058b1bd281bf7` |
| 编译 | Release，C++17/CUDA17；GNU 11.4.0，CMake 3.28.3，CUDA 12.9.86 |
| 架构及选项 | `75;80;86;89;90`，CXX/CUDA Release 均 `-O3 -DNDEBUG` |
| MPI / UCX | Open MPI 5.0.10；UCX 1.22.0，revision `8a6b06f`；PML `ucx`、collective `^hcoll` |
| 主机 | `n4`，Linux 5.15.0-60；2 × AMD EPYC 7K62 48-Core，192 logical CPUs |
| GPU | 主机 8 × RTX 4090，24564 MiB/卡；driver 580.173.02；本矩阵仅使用 devices `0,1` |
| 环境入口 | 每个新 shell 均 source `../env/md-mpi.sh` |
| 环境脚本 SHA | `3af45518352942d46a2a95db48d4d5e41b9fd46ba79222f13982b3810dccfe96` |

`build-config-comparison.json` 证明 A1/A2 的 22 条共同编译命令在只规范化 build 路径后相同，
关键 cache 值一致；仅新增控制器和单测两条编译命令。未重建/改写 A1 build，也未覆盖仓库 build。
`source-at-build.json`、`before/`、`after/` 将实际候选绑定到 A2 开工和构建时的文件内容。
`environment-resume-20261102.json` 保存当前 GPU UUID、工具链版本和两候选动态链接结果。
host gate/CTest/golden/MPI 命令均使用获准的 `sandbox_permissions=require_escalated`；自动审批通过。

<a id="ensemble-control-20261102--单元测试证据"></a>
### 单元测试证据
| case 编号 | 覆盖 | 结果 |
| --- | --- | --- |
| 1–48 | 常温、升温、降温、极小到大温度；段长 1/2/7/1000003；首/中/末步 | 原表达式与控制器的 double 位模式相同 |
| 49–120 | 8 个 TAU（含阈值及其上下相邻 double）、3 个温度、3 个步位 | 缩放有无及活动因子位模式相同 |
| 121 | 因子 1 保留活动分支；弱耦合无 thermo；活动耦合缺全局 thermo 拒绝 | PASS |
| 122 | 连续 run 重置段内 ramp、配置复制隔离、变更系综配置 | PASS |
| 123 | NVE/Berendsen 构造、hook、析构无 heap 分配；无链/RNG 状态 | allocation_delta=0；trivial copy/destruct 及大小静态断言通过 |
| 124–128 | NHC、BDP、NPT 保留枚举与非法 kind | 全部拒绝，A1 程序级 guard 同时保留 |

逐案例输入与 `expected_bits`/`actual_bits` 保存在 `focused-detail.log` 和 host CTest detail 中。
这验证当前两系综的标量与生命周期；不声称已实现未来链/RNG 的初始化或广播。

<a id="ensemble-control-20261102--ab-科学输出与路由"></a>
### A/B 科学输出与路由

两候选逐项使用同一输入、rank、backend、构建配置。baseline runner 使用原 manifest/golden
容差；A2 迁移判定另外要求 A/B 文件 SHA-256 **完全相同**，没有通过数值容差豁免字节差异。
`golden-ab-results.json` 给出额外 20 对文件；`ab-results.json` 给出其余 181 对的完整路径/哈希，
以及每个 stage 的 `run.in`、`model.xyz`、`nep.txt` 输入哈希。以下是六个要求的 NVT 路由代表：
| 路由 | backend | 输出文件 | A1 SHA-256 = A2 SHA-256 |
| --- | --- | --- | --- |
| M1 P1 | HostStaged | `thermo.out` | `243ca728dc4f810f4b46975c6ba009e81a27e18f85a3d68487c5377c3a491887` |
| M1 P1 | HostStaged | `trajectory.xyz` | `582aef33397e5ce4bc8f263d0783c1b9cefb09f55882a84f6cc15adaf2ae5524` |
| M1 P1 | HostStaged | `restart.xyz` | `729265fb52f7a9c0cebe92efc75327fabc48c7b408c261df3942c035a6fe3247` |
| M1 P2 | HostStaged | `thermo.out` | `243ca728dc4f810f4b46975c6ba009e81a27e18f85a3d68487c5377c3a491887` |
| M1 P2 | HostStaged | `trajectory.xyz` | `582aef33397e5ce4bc8f263d0783c1b9cefb09f55882a84f6cc15adaf2ae5524` |
| M1 P2 | HostStaged | `restart.xyz` | `729265fb52f7a9c0cebe92efc75327fabc48c7b408c261df3942c035a6fe3247` |
| M2a P2 | HostStaged | `thermo.out` | `3d8908c7b05fdec54018a507fa48018a32b48faa30541ffb7263bf61409d1af8` |
| M2a P2 | HostStaged | `static.xyz` | `ee0ddab091a7103ab97c0cd40909248ba94f67f8194327afa94c92e7a39b5f4c` |
| M2a P2 | HostStaged | `restart.xyz` | `c22cd99f31daba4aa4c6a9453cad76241d1b9f56de7eb87d207eb16d7d4c25ae` |
| M1 P1 | CudaAware | `thermo.out` | `243ca728dc4f810f4b46975c6ba009e81a27e18f85a3d68487c5377c3a491887` |
| M1 P1 | CudaAware | `trajectory.xyz` | `582aef33397e5ce4bc8f263d0783c1b9cefb09f55882a84f6cc15adaf2ae5524` |
| M1 P1 | CudaAware | `restart.xyz` | `729265fb52f7a9c0cebe92efc75327fabc48c7b408c261df3942c035a6fe3247` |
| M1 P2 | CudaAware | `thermo.out` | `243ca728dc4f810f4b46975c6ba009e81a27e18f85a3d68487c5377c3a491887` |
| M1 P2 | CudaAware | `trajectory.xyz` | `582aef33397e5ce4bc8f263d0783c1b9cefb09f55882a84f6cc15adaf2ae5524` |
| M1 P2 | CudaAware | `restart.xyz` | `729265fb52f7a9c0cebe92efc75327fabc48c7b408c261df3942c035a6fe3247` |
| M2a P2 | CudaAware | `thermo.out` | `3d8908c7b05fdec54018a507fa48018a32b48faa30541ffb7263bf61409d1af8` |
| M2a P2 | CudaAware | `static.xyz` | `ee0ddab091a7103ab97c0cd40909248ba94f67f8194327afa94c92e7a39b5f4c` |
| M2a P2 | CudaAware | `restart.xyz` | `c22cd99f31daba4aa4c6a9453cad76241d1b9f56de7eb87d207eb16d7d4c25ae` |

M1 用 `multi_nvt_restart/initial`：包含 NVE 初始化与两个 Berendsen 分段，另有 resume 对照。
P1 日志为 `mode=m1-fallback reason=p1`；P2 为
`mode=m1-fallback axis=y d_dep=7 d_coord=14 reason=slab-width-below-d-coord`。
M2a 用 `partition_nvt`：`mode=m2a axis=x d_dep=8 d_coord=16 axis_thickness=64 slab_width=32 ranks=2 reason=eligible`。
该例 12 步，输出集合严格为 `thermo.out/static.xyz/restart.xyz/neighbor.out`，两后端均检查实际路由。
`lattice` 覆盖 NVE；`crossings` 覆盖多段 NVT/NVE、迁移及输出。保留 runner 原有 mode proof、
layout/transition、oracle differential、精确 byte accounting 与负例检查。

本次 domain runner 的 30 个实际 stage 包括 17 个 P1 reference-oracle stage、6 个所选 P2 stage、
2 个 M1 partition fallback，以及 quiet、crossings-timing、continuous timing off/on、unroutable。
这里的 P1 oracle 均执行对应 DMG-MD 候选，没有运行或改写外部 reference 程序。
`unroutable` 是 runner 预期 fatal 的通过负例；不将其解释成成功轨迹。

<a id="ensemble-control-20261102--通信记录比较"></a>
### 通信记录比较

使用原生产日志的 `DMGMD_COMM`、`DMGMD_DOMAIN_COMM`、`DMGMD_DOMAIN_SUMMARY`，
按 step 或 rank/step、rank/sequence 索引后逐字段比较；不依赖进程输出行的交错顺序。
同时检查日志步集合完整、必需字段非负、实际 route 和输入相同。比较所有 collective/p2p 次数、
MPI 输入/输出字节、D2H/H2D/output-download、halo/migration/control 字段与 segment summary。
没有 PMPI 探针；运行时证据是既有账本，配合源码调用序列/位置审查。未将日志计数声称为独立 PMPI 测量。

下表为上述六个 stage 的逐步账本求和，**A1 与 A2 每条记录均相同**；计入该输入原有
修正、输出与其它 collective，不是额外控制器开销，也不是物理网络传输量。
| 路由 | backend | 记录步数 | collective_calls 总和 | MPI input bytes 总和 | MPI output bytes 总和 |
| --- | --- | --- | --- | --- | --- |
| M1 P1 | HostStaged | 8 | 46 | 10448 | 10448 |
| M1 P2 | HostStaged | 8 | 54 | 11216 | 12944 |
| M2a P2 | HostStaged | 12 | 104 | 18272 | 18272 |
| M1 P1 | CudaAware | 8 | 46 | 10448 | 10448 |
| M1 P2 | CudaAware | 8 | 54 | 11216 | 12944 |
| M2a P2 | CudaAware | 12 | 104 | 18272 | 18272 |

所选 NVT stage 每步记录（interval=1）；M1+M2a 共比较 231 对全局、160 对 rank 逐步记录及
30 对 segment summary。`default-quiet` 按其既有默认日志间隔没有逐步记录，`unroutable`
在完整积分前按预期拒绝；两者不计作逐步账本覆盖。其它已记录 NVE/NVT 步及静态调用审查均相符。
M1 保留每步 thermo；M2a NVE 的 consumer 判定和 NVT 每步反馈未变，没有新增 KE 归约或广播。

<a id="ensemble-control-20261102--revision已有修改及文件保护"></a>
### Revision、已有修改及文件保护

起始冻结为 `initial.json`，2026-10-30 23:19:44.757915 +08:00：19 个 tracked dirty、3 个
untracked 文档。2026-11-02 续跑预检重新核对 HEAD、候选、构建源码、A1 build、输出目录与进程，
通过后才继续验收；当时无相关活动进程，新的 golden/MPI TMPDIR 为空、domain work-root 不存在。

起始 14 个外部 dirty 文档如下；均作为用户/统筹者修改保留，A2 未编辑：

```text
AGENTS.md
README.md
docs/README.md
docs/operations/deployment.md
docs/plans/communication-computation-overlap.md
docs/plans/dmg-md-v1.0-development-workflow.md
docs/plans/risk-and-backlog.md
docs/reference/gpumd-runtime-audit.md
docs/standards/architecture-decisions.md
docs/standards/cross-engine-reference.md
docs/standards/golden-test-standard.md
docs/standards/mpi-runtime.md
docs/status/current.md
tests/benchmark/README.md
```

另外 5 个起始 dirty 源码/测试属于 A1：`include/dmgmd/run_ir.hpp`、`src/run_parser.cpp`、
`src/runtime.cu`、`src/domain_runtime.cu`、`tests/run_parser_tests.cpp`。A2 仅在两 runtime 的
获准位置叠加变更，保留 A1 guard；其余 3 个文件哈希不变。
起始未跟踪文档 `docs/plans/dmg-md-v1.0-progress.md`、
`docs/status/validation/ensemble-baseline-20261030.md`、
`docs/status/validation/ensemble-parser-20261030.md` 的 SHA-256 均保留。

相对 A2 冻结仅 4 个已有 tracked 文件变化：两 runtime 和两 CMake；另新增控制器头/实现、
单测和本文。其它 **242 个 tracked 文件、3 个起始 untracked 文档** 逐文件哈希相同。
报告写入后工作区为 21 个 tracked dirty、7 个 untracked（含已有 3 文档）；未暂存。
`a2-source.diff` 相对 A1 的 frozen worktree 生成，避免把 A1 guard 误归为 A2 改动。
7 个 A2 源码/构建文件与 `source-at-build.json` 相同；`git diff --check`（获准 tracked 范围）通过。

A1 的 **420 个 build 文件及 manifest 哈希完全不变**。A1 manifest SHA 为
`1818ad68d5543492ca439340298696d230212dc8aa68725d9e0be77bb45c3325`。
仓库原 `build/dmg-md` 保持 `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32`；
主仓库 ignored 路径集合仍为 192152 项。三个 reference 的 revision、Git 状态、ignored 路径及哈希均保留：
| checkout | revision | 状态 | ignored 文件 |
| --- | --- | --- | --- |
| GPUMD | `9d23496e41319b9e2af5221a7df6285387401d1e` | clean | 原有 208 项全部相同 |
| LAMMPS | `611ca3b7f8525ba802373b04f9e3d632d515f7e3` | clean | 0 |
| LAMMPS-MatPL | `21fabf34eadc57a717f4c7fbce9bb80aad5fd991` | clean | 0 |

<a id="ensemble-control-20261102--精确命令与执行顺序"></a>
### 精确命令与执行顺序

工作目录均为 `/fdata/zhangyifei/zhangyifei/newmd/newmd`，每个新 build/test shell 先执行：

```sh
source ../env/md-mpi.sh
export PYTHONDONTWRITEBYTECODE=1
```

下表按实际开始时刻排序。命令由
`python3 /tmp/dmgmd-a2-20261030/capture_command.py <记录名> -- <命令>` 包装；对应
`<记录名>.json/.stdout/.stderr` 保存 argv、cwd、环境、开始/结束、退出码与原始输出。
有 TMPDIR 的行将赋值放在包装命令之前。所有列出的退出码均为 0；精确时刻含时区，详见 JSON。
两个 `python3 -` 的原标准输入已逐字另存同名 `.stdin.py`，可用 `< 文件` 传入。
捕获器和比较器拒绝覆盖证据；复验应使用新的记录名/工作目录。
| 记录名 | 开始时间（+08:00） | 秒 | 退出码 |
| --- | --- | --- | --- |
| `configure` | 2026-10-30T23:24:56.245703+08:00 | 6.006 | 0 |
| `build` | 2026-10-30T23:25:30.752948+08:00 | 78.498 | 0 |
| `focused` | 2026-10-30T23:28:09.909336+08:00 | 0.043 | 0 |
| `ctest-sandbox` | 2026-10-30T23:28:48.993121+08:00 | 26.596 | 0 |
| `environment-before-host` | 2026-10-30T23:30:03.286365+08:00 | 10.122 | 0 |
| `preflight-resume-20261102` | 2026-11-02T09:19:39.356187+08:00 | 0.296 | 0 |
| `environment-after-host-resume-20261102` | 2026-11-02T09:19:53.422451+08:00 | 5.203 | 0 |
| `ctest-host-resume-20261102` | 2026-11-02T09:20:12.470832+08:00 | 35.268 | 0 |
| `golden-before-host-resume-20261102` | 2026-11-02T09:21:22.076176+08:00 | 11.973 | 0 |
| `golden-after-host-resume-20261102` | 2026-11-02T09:21:40.914634+08:00 | 11.823 | 0 |
| `compare-golden-resume-20261102` | 2026-11-02T09:22:14.488095+08:00 | 0.045 | 0 |
| `mpi-before-host-resume-20261102` | 2026-11-02T09:22:30.595969+08:00 | 55.720 | 0 |
| `mpi-after-host-resume-20261102` | 2026-11-02T09:23:36.378326+08:00 | 55.975 | 0 |
| `compare-m1-resume-20261102` | 2026-11-02T09:24:36.649066+08:00 | 0.074 | 0 |
| `domain-before-host-resume-20261102` | 2026-11-02T09:24:53.886832+08:00 | 87.426 | 0 |
| `domain-after-host-resume-20261102` | 2026-11-02T09:26:34.272751+08:00 | 86.880 | 0 |
| `compare-ab-resume-20261102` | 2026-11-02T09:28:24.410792+08:00 | 0.132 | 0 |
| `final-audit-before-report-resume-20261102` | 2026-11-02T09:28:36.541388+08:00 | 1.053 | 0 |
| `environment-metadata-host-resume-20261102` | 2026-11-02T09:29:08.864536+08:00 | 0.718 | 0 |

<a id="ensemble-control-20261102--configure"></a>
#### `configure`

```sh
cmake -S . -B /tmp/dmgmd-a2-20261030/build -DCMAKE_BUILD_TYPE=Release '-DCMAKE_CUDA_ARCHITECTURES=75;80;86;89;90'
```

<a id="ensemble-control-20261102--build"></a>
#### `build`

```sh
cmake --build /tmp/dmgmd-a2-20261030/build -j 8
```

<a id="ensemble-control-20261102--focused"></a>
#### `focused`

```sh
ctest --test-dir /tmp/dmgmd-a2-20261030/build -R '^dmgmd\.(ensemble_control|run_parser)$' --output-on-failure
```

<a id="ensemble-control-20261102--ctest-sandbox"></a>
#### `ctest-sandbox`

```sh
ctest --test-dir /tmp/dmgmd-a2-20261030/build --output-on-failure
```

<a id="ensemble-control-20261102--environment-before-host"></a>
#### `environment-before-host`

```sh
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a1-20261030/build/dmg-md --devices 0,1 --ranks 2
```

<a id="ensemble-control-20261102--preflight-resume-20261102"></a>
#### `preflight-resume-20261102`

```sh
python3 - < /tmp/dmgmd-a2-20261030/preflight-resume-20261102.stdin.py
```

<a id="ensemble-control-20261102--environment-after-host-resume-20261102"></a>
#### `environment-after-host-resume-20261102`

```sh
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a2-20261030/build/dmg-md --devices 0,1 --ranks 2
```

<a id="ensemble-control-20261102--ctest-host-resume-20261102"></a>
#### `ctest-host-resume-20261102`

```sh
ctest --test-dir /tmp/dmgmd-a2-20261030/build --output-on-failure
```

<a id="ensemble-control-20261102--golden-before-host-resume-20261102"></a>
#### `golden-before-host-resume-20261102`

```sh
TMPDIR=/tmp/dmgmd-a2-20261030/golden-before python3 tests/baseline/run_baselines.py --candidate /tmp/dmgmd-a1-20261030/build/dmg-md --device 0 --keep-work
```

<a id="ensemble-control-20261102--golden-after-host-resume-20261102"></a>
#### `golden-after-host-resume-20261102`

```sh
TMPDIR=/tmp/dmgmd-a2-20261030/golden-after python3 tests/baseline/run_baselines.py --candidate /tmp/dmgmd-a2-20261030/build/dmg-md --device 0 --keep-work
```

<a id="ensemble-control-20261102--compare-golden-resume-20261102"></a>
#### `compare-golden-resume-20261102`

```sh
python3 - < /tmp/dmgmd-a2-20261030/compare-golden-resume-20261102.stdin.py
```

<a id="ensemble-control-20261102--mpi-before-host-resume-20261102"></a>
#### `mpi-before-host-resume-20261102`

```sh
TMPDIR=/tmp/dmgmd-a2-20261030/mpi-before python3 tests/mpi/run_mpi_differential.py --candidate /tmp/dmgmd-a1-20261030/build/dmg-md --ranks 1,2 --backends HostStaged,CudaAware --devices 0,1 --keep-work
```

<a id="ensemble-control-20261102--mpi-after-host-resume-20261102"></a>
#### `mpi-after-host-resume-20261102`

```sh
TMPDIR=/tmp/dmgmd-a2-20261030/mpi-after python3 tests/mpi/run_mpi_differential.py --candidate /tmp/dmgmd-a2-20261030/build/dmg-md --ranks 1,2 --backends HostStaged,CudaAware --devices 0,1 --keep-work
```

<a id="ensemble-control-20261102--compare-m1-resume-20261102"></a>
#### `compare-m1-resume-20261102`

```sh
python3 /tmp/dmgmd-a2-20261030/compare_ab.py --m1-before /tmp/dmgmd-a2-20261030/mpi-before --m1-after /tmp/dmgmd-a2-20261030/mpi-after --report /tmp/dmgmd-a2-20261030/m1-ab-results.json
```

<a id="ensemble-control-20261102--domain-before-host-resume-20261102"></a>
#### `domain-before-host-resume-20261102`

```sh
python3 tests/mpi/run_mpi_domain.py --candidate /tmp/dmgmd-a1-20261030/build/dmg-md --ranks 2 --backends HostStaged,CudaAware --devices 0,1 --cases lattice,crossings,partition_nvt --partition-modes off --schedule split --work-root /tmp/dmgmd-a2-20261030/domain-before
```

<a id="ensemble-control-20261102--domain-after-host-resume-20261102"></a>
#### `domain-after-host-resume-20261102`

```sh
python3 tests/mpi/run_mpi_domain.py --candidate /tmp/dmgmd-a2-20261030/build/dmg-md --ranks 2 --backends HostStaged,CudaAware --devices 0,1 --cases lattice,crossings,partition_nvt --partition-modes off --schedule split --work-root /tmp/dmgmd-a2-20261030/domain-after
```

<a id="ensemble-control-20261102--compare-ab-resume-20261102"></a>
#### `compare-ab-resume-20261102`

```sh
python3 /tmp/dmgmd-a2-20261030/compare_ab.py --m1-before /tmp/dmgmd-a2-20261030/mpi-before --m1-after /tmp/dmgmd-a2-20261030/mpi-after --m2a-before /tmp/dmgmd-a2-20261030/domain-before --m2a-after /tmp/dmgmd-a2-20261030/domain-after --report /tmp/dmgmd-a2-20261030/ab-results.json
```

<a id="ensemble-control-20261102--final-audit-before-report-resume-20261102"></a>
#### `final-audit-before-report-resume-20261102`

```sh
python3 /tmp/dmgmd-a2-20261030/final_audit.py --report /tmp/dmgmd-a2-20261030/final-before-report.json
```

<a id="ensemble-control-20261102--environment-metadata-host-resume-20261102"></a>
#### `environment-metadata-host-resume-20261102`

```sh
python3 /tmp/dmgmd-a2-20261030/environment_metadata.py
```

报告写入后的最终只读保护检查及证据封存另存 `final-audit-resume-20261102.*`、
`seal-evidence-resume-20261102.*`，命令为：

```sh
python3 /tmp/dmgmd-a2-20261030/final_audit.py --report /tmp/dmgmd-a2-20261030/final-ownership.json --status-document docs/status/validation/ensemble-control-20261102.md
python3 /tmp/dmgmd-a2-20261030/seal_evidence.py
```

<a id="ensemble-control-20261102--a2-dod-对照与限制"></a>
### A2 DoD 对照与限制
| A2 要求 | 证据及结果 |
| --- | --- |
| M1/M2a 共享目标调度及 Berendsen 控制 | 单一纯 host 库、两 runtime 调用；`a2-source.diff` |
| 段内 0 基调度、阈值、表达式和缩放位置不变 | 位模式 128 案例；kernel 静态一致；A/B 哈希完全相等 |
| NVE 无链/RNG 分配，每段独立初始化 | 无分配及大小/trivial 静态断言；多段测试及 `multi_nvt_restart` |
| 全局 thermo / atom ownership / MPI 次序保持 | 原归一化及 owned kernel 保留；无新 MPI 方法调用；完整生产账本对照 |
| 同工具链/选项 A/B | 22 条共同编译命令和关键 cache 相同；两独立候选 SHA 固定 |
| M1 P1、P2 fallback、M2a P2 `nvt_ber` | 两后端六个实际路由均通过；thermo/XYZ/restart SHA 相同 |
| 既有功能回归 | focused + host full CTest、两候选 golden/MPI runner 均通过 |
| 不改 compat / golden / 容差 / A1 guard / 用户文件 | 全文件 SHA 保护与最终 diff 审查通过 |

统筹者独立复算全部 50 个 M1/M2a stage 的输入、科学输出哈希、route、global/local 通信记录与
segment summary，结果均与 A/B 记录相同；另核对 20 对 golden SHA、20 条捕获命令返回码、1123 项
证据哈希、A1 420 个 build 文件以及初始 dirty 工作区保护。diff、测试和 A2 DoD 均通过。
进度、主计划和 `docs/README.md` 已更新；这三处启动前均为已有 dirty 文档，统筹者只更新进度、A2/A3 状态和验收索引，其他用户修改均保留。本轮没有启动 A3。

**当前没有验收阻塞项；恢复后的唯一下一步为 A3。**
本证据限定于该主机、冻结工具链、上述短程输入及 P1/P2、HostStaged/CudaAware；
没有宣称新 NHC/BDP 动力学、跨节点、P4/P8、长程统计或性能收益。此次没有回滚；若审核发现差异，
保持当前证据并停在 A2，回退只能基于 `before/` 恢复 A2 delta，保留 A1 和用户修改。
