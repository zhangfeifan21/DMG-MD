# CPU 采样 C1 验证（2026-10-09）

类别：实测/实现备忘。C1 完成；C2/C3、正式矩阵、M2b 未启动。本记录验证工具接线、
指标计算与生命周期，不发布生产 CPU 利用率、采样低扰动或 MPI 等待归因结论。

## 实现身份

DMG-MD 起始 HEAD 为 0ba54eea4e16d157a01c0fb6e371b0af7d33032e，当时采样/编排改动未提交。
生产 runtime/NEP、manifest 与参考树未修改。实现入口、schema 和指标合同在
[CPU 标准](../../standards/cpu-sampling.md)，操作在[benchmark README](../../../tests/benchmark/README.md#cpu-采样c1)。
以下为当次验证，后续 O1 已接入 marker，当前能力见[状态](../current.md)。

## 环境、命令与结果

主机 n4，Linux 5.15.0-60-generic x86_64，Python 3.12.3；Open MPI 5.0.10、UCX 1.22.0；
GPU 0/1 为 RTX 4090，driver 580.173.02。原生每个新 shell 都 source `../env/md-mpi.sh`。
复用现有 build/dmg-md，SHA-256
`13da7a81c9c77f46b53dd45ada81d7a593634800c30b2714c13d2fa3bc93b80d`，未重建生产代码。
GPU UUID 为 `GPU-a4cdc2a3-d216-af6d-26bb-d6a5c81e2a80` 和
`GPU-d146f2b8-3d3d-4b60-b4a5-1b9ed07f2900`；各运行快照的源码 hashes/dirty patch 在 metadata 中。

所有验证日志和原始数据根目录：`/tmp/dmgmd-c1-20261009/`（临时目录，长期保留需归档）。

```bash
# CPU-only，无 GPU
python3 tests/benchmark/test_cpu_sampling.py
python3 tests/benchmark/test_benchmark.py
python3 tests/long_nve/test_long_nve.py
source ../env/md-mpi.sh
cmake -S . -B build
ctest --test-dir build -R 'dmgmd\.(cpu_sampling|benchmark_analysis|long_nve_analysis)$' --output-on-failure
```

14 项 CPU 测试、原有 9 项 benchmark 测试、23 项 long_nve 分析测试均通过；对应三个 CTest
全部通过。覆盖 tick/真实窗口、comm 括号、PID/TID 重用、缺样/截断、quota 共享及不相加、
限制变化/隐藏祖先、marker 边界、GPU 映射冲突、namespace 拒绝、缺工具和权限拒绝。
真实 CPU load/sleep 子进程仅比较 CPU 时间趋势；线程出生退出、exit 42、超时、中断和无关
进程存活测试通过，无脆弱精确百分比门槛。`cpu-tests.txt`、`benchmark-tests.txt`、
`long-nve-analysis.txt`、`ctest.txt` 保存输出；`cpu-load-sleep.py` 是保留原始趋势样本的实际命令脚本，
执行 `python3 /tmp/dmgmd-c1-20261009/cpu-load-sleep.py`（首次执行，目录已存在时拒绝覆盖）。
`sleep-raw/cpu/`、`load-raw/cpu/` 保存样本；它们使用合成 OMPI 环境，没有真实 GPU binding，
mapping=unavailable 是预期结果。

GPU 预检先在沙箱内执行，失败为 `UCX transport is unavailable: cuda_copy`，日志
`preflight-sandbox.txt`。随后经授权在沙箱外 source 同一脚本执行相同命令，PASS（不是宿主无 GPU）：

```bash
source ../env/md-mpi.sh
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1 --ranks 2
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
```

`preflight-host.txt`：cuda_copy/cuda_ipc 和两 rank CudaAware device-buffer probe 通过。
`golden.txt`：全部四个单 rank baseline case 匹配锁定 golden。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile smoke --engines dmgmd \
  --devices 0,1 --ranks 1,2 --backends HostStaged,CudaAware \
  --cpu-sampling proc --cpu-sample-interval .2 \
  --output /tmp/dmgmd-c1-20261009/gpu-smoke
```

输出 `gpu-smoke-20261009-102225/`，四个 trial 数值 status=ok、cpu_status=ok；所有六个
rank 实例 exec/PID/GPU 映射 verified。各 rank 有效**进程观测窗口**分别为 10、12/12、10、
12/13；包括初始化等阶段，不等于正式 run 样本数。样本无 JSONL 解析错误，collector 正常回收。
quota visibility 保持默认 unknown，capacity_percent=null/incomplete_capacity_hierarchy，
未为了得到百分比而假定完整宿主祖先可见。没有可靠 marker，run CPU 利用率为空。

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile smoke --engines dmgmd \
  --devices 0,1 --ranks 2 --backends HostStaged --cpu-sampling off \
  --output /tmp/dmgmd-c1-20261009/gpu-off
```

输出 `gpu-off-20261009-102324/`，一个 trial 通过。无 cpu 目录，command 无 wrapper；
与开启采样的同配置 P=2 HostStaged `thermo.out` **逐字节一致**。这验证关闭路径与本次输出，
不是五次 A/B 采样开销校准。新选项 `--help`/`--dry-run` 均通过，dry-run 生成四个计划 trial、
不写结果目录；日志 `benchmark-help.txt`、`dry-run.json`。

实际重算示例（可在无 GPU 的 shell 执行）：

```bash
python3 tests/benchmark/analyze_cpu.py \
  /tmp/dmgmd-c1-20261009/gpu-smoke-20261009-102225/0003-carbon_smoke-dmgmd-HostStaged-p2-r0 \
  --output /tmp/dmgmd-c1-20261009/recomputed/0003-carbon_smoke-dmgmd-HostStaged-p2-r0
```

四个 trial 都用最终解析器独立重算两次，结果一致；`recomputed/` 保存输出，
`final-check.json` 保存窗口计数、mapping 核验和 off/thermo 对照结果。GPU smoke 后的解析器
完整性/缺失原因完善由 CPU/CTest 和原始数据重算覆盖，未反复启动 GPU 矩阵。
`git diff --check`、Python 编译检查通过；参考仓库仍干净。

## 限制与 C2 入口

只支持同机/同 namespace 的 Linux Open MPI rank。容器方式已定义且边界有 CPU 测试，
本轮未启动 Docker；多节点远端 /proc、跨 namespace 映射不支持。cgroup v1 或隐藏/不可读
祖先只保留可见原始证据，不给完整 quota 归一化。pidstat/perf 非必需，实际硬件 perf 计数
未验收；缺工具/权限路径由可控替身测试覆盖。短命线程与采样首尾删失不可消除。

P>1 的 proc sweep 不是同步读数，job 通常只报 observed subtotal；未提供正式 run/step
归因、可发布的跨 trial CPU 均值/峰值、线程库角色或 MPI 忙轮询结论。collector 原始成本
已记，但低扰动结论仍 UNKNOWN。C2/C3 的剩余矩阵和校准要求见[CPU 研究计划](../../plans/cpu-usage-study.md)。
本次没有 run marker；O1 后续接入不改变本报告数据的观测窗口。
