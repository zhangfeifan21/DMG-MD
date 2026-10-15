# O1 串行通信/计算基线（2026-10-12）

类别：实测/实现备忘。范围只到 O1：M2a 原有串行路径的时间线、两后端 A/B 诊断编排、
CPU 与固定资源基线；没有 interior/boundary 分区或异步计算。所有速度数字是本机样本，
不是 O4/O5 的 overlap 收益。

## 身份、环境和资源映射

- DMG-MD HEAD `8669fb4256efabe262ec5943d5589f3a245bc0d8`，实验时工作树 **dirty**，
  没有提交或推送。代码改动为 `include/dmgmd/mpi_runtime.hpp`、`src/domain_runtime.cu`、
  `src/mpi_runtime.cu`，编排/测试为 `tests/benchmark/run_benchmark.py`、
  `run_overlap_rebuild.py`、`test_benchmark.py`，文档为本页、benchmark README、
  `replicated-mpi.md`、`docs/README.md`。每个 benchmark 输出的 `metadata.json`、
  [`source-dirty.patch`](../../dmgmd-overlap-o1-20261012/delivery-standard-proc-20261012-163857/source-dirty.patch)
  与 `dirty_patch_sha256` 锁定该次运行时的 tracked diff：
  `ff19cc3d30b51f37be48d065c7d457ec5c6d0a3171ba74df6ef0babbecda9045`。
  新 runner 作为 untracked 文件由运行 metadata 的 `untracked_source_sha256` 或
  专用重建运行 metadata 的 `sources` 哈希保存。metadata 中本报告的 untracked
  哈希仅对应实验当时草稿；本页在数据收齐后更新，不作为二进制构建输入。
- 三个只读 reference HEAD 分别为 GPUMD
  `9d23496e41319b9e2af5221a7df6285387401d1e`、LAMMPS
  `611ca3b7f8525ba802373b04f9e3d632d515f7e3`、MatPL
  `21fabf34eadc57a717f4c7fbce9bb80aad5fd991`。前后 tracked 状态均干净；
  GPUMD 原有忽略的 `build/` 文件最晚早于本日，本轮三个参考目录均无本日新增文件。
- 主机 `n4`，Linux `5.15.0-60-generic`，AMD EPYC 7K62（2 NUMA），Open MPI 5.0.10、
  UCX 1.22.0、CUDA Toolkit 12.9、NVIDIA driver 580.173.02。GPU0/1 为 RTX 4090，
  UUID 分别 `GPU-a4cdc2a3-d216-af6d-26bb-d6a5c81e2a80`、
  `GPU-d146f2b8-3d3d-4b60-b4a5-1b9ed07f2900`，均邻近 NUMA0。
- P=2 的每次正式/诊断启动使用 Open MPI
  `--map-by ppr:2:numa:PE=4 --bind-to core --report-bindings`，rank 0/1 的 GPU UUID
  与 `DMGMD_MPI` 及 C1 映射一致。C1 从实际 `/proc` task affinity 和 core/package/NUMA
  拓扑核验：rank 0 为逻辑 CPU `0-3,96-99`（物理核 0–3）、rank 1 为
  `4-7,100-103`（物理核 4–7），两者均 NUMA0、无交叠。GPU/CPU 对应在每个
  `--cpu-sampling proc` trial 的 `result.json.cpu_binding` 和 `cpu/summary.json.mappings`
  留证。`--cpu-sampling off` 组保留逐 trial `--report-bindings` stderr，但没有 C1 的
  逐 trial task 快照，仅作为采样器关闭的对照。
- CPU memory policy 是宿主默认策略，未通过 CPU affinity 推断内存页实际 NUMA；C1
  容量层次在本次观测为 `hierarchy_complete=false`，不发布完整 quota 归一化。
  GPU 使用/温度/时钟逐 trial 在 `telemetry.jsonl` 与 metadata；没有锁频或排他调度证明。

交付测量二进制 SHA-256：`fa6ad163ce84c3ae2bfc1638553847cd5767c67db0c27cc5a59c4176c8e823eb`。
源码 SHA-256：`src/domain_runtime.cu` 为
`2f936dd5930dc38da95687f734ddb6912705067e35880e9c2d2cb746ef64a93b`，
`src/mpi_runtime.cu` 为 `08ae11db385eba70d06a62e0e9147d23e246647568e4df7a549f3ae9b738d210`，
`include/dmgmd/mpi_runtime.hpp` 为
`04029a3734369fb465acf9d7f2012febe3cda0f66a8c8918b915a7af72485357`。
benchmark `manifest.json` 为 `560a9874e9a25622ab4992af4426d8c23410aac8eb1a49bc1a9300a380e1905f`；
交付时 `run_benchmark.py` 为
`94bcac23bbe9a6eca87a04694c5eddbefbd162ac50d2eba5da17f89b43cc2244`；
`run_overlap_rebuild.py` 为 `d6e42727d04a78098f5af41c4f74c775131289bfe9735093a5f2676981ebc2ea`。
C1 `cpu_sampling.py` 为 `4bcdcf96794b3e7fed72e945a122901101647c4d0d4a65d9cbed44410e2ec567`，
`analyze_cpu.py` 为 `a86c2d15a82bc29b24d19d09d2ead67e682bbd1562c2b2a9269357f8d5403a86`。

## 现有计时范围审计与 O1 补充

| 指标/代码位置 | 实际起止与包含项 | 不能推断的量 |
|---|---|---|
| `halo_pack_device` / `src/mpi_runtime.cu:exchange_p2p_indexed_device_soa` | 默认 stream 上 pack 前后 CUDA event，覆盖两侧 pack kernel 的设备区间；无元素时仍有 event。 | 不是从主机函数进入到打包完成的墙钟。 |
| `halo_transfer_wait` / 同函数 | pack event 入队之后到 unpack event 入队之前的 host `steady_clock` 区间；HostStaged 包含 pinned buffer reserve、阻塞 D2H、MPI 四次 post/Waitall、阻塞 H2D；CudaAware 包含既有输入/输出 `cudaDeviceSynchronize` 和同样 MPI 调用。 | 不能作为纯网络时间，也不能与 `mpi_wait` 相加。 |
| `mpi_wait` / `src/mpi_runtime.cu:begin_mpi_wait/end_mpi_wait` | 当前步所有被调用方包围的 MPI host 调用总量，包括 decision/thermo collectives、halo/迁移 p2p 的 post+Waitall；普通 halo 的单次 `begin_mpi_wait` 包住整个 p2p 函数。 | 不是单独 `MPI_Waitall`，也不是 NIC 活动。 |
| `halo_unpack_device` / 同函数 | 默认 stream 上 unpack 前后 CUDA event，包含两侧 unpack 的设备区间。 | 不包含后续 NEP 消费等待。 |
| `cell_neighbor_device`、`nep_device` / `src/gpumd_compat/nep.cu:compute_domain` marker | 0→1 为 cell/neighbor，2→3 为完整、未分区的 descriptor/force CUDA event 区间；两者之间的周期 `neighbor.out` D2H/MPI 诊断属 host 区间。 | `nep_device` 不含 neighbor；没有分区，`T_interior`/`T_boundary` 均 **不可观测**。 |
| `step_seconds` / `src/domain_runtime.cu:run_domain_segment` | 步首到 output、通信日志后的 host wall；decision、rebuild、integration、thermo、输出等 host 括号与 device event 各自记录。 `remaining_host` 为未分配的 host 差额。 | 设备时间/host 时间可能交叠，不能求和还原步长。 |
| 段 `DMGMD_TIMING phase=run` | 调用方在段前既有 device sync 与 MPI barrier 后开始，直到 `run_domain_segment` 返回；含 setup force、每步、段末既有 device sync 和诊断时的归约/输出。 | 诊断组的该值含 profiler 开销；正式速度只用关闭 profiler 的组。 |

新增、默认关闭的 `DMGMD_DOMAIN_TRACE=1` 要求 `DMGMD_DOMAIN_TIMING=1`。启动记录
`DMGMD_DOMAIN_DIAGNOSTICS ... timing=on/off trace=on/off` 是实际程序模式；runner 清掉
继承的全部 `DMGMD_*` 后，只按白名单写 `DMGMD_COMM_BACKEND`、`DOMAIN_TIMING`、
`DOMAIN_TRACE`、`DOMAIN_DIAGNOSTICS`、`COMM_LOG_INTERVAL`，并把值存入每 trial 的
`launch_environment`。`actual_profiling` 再校验实际模式与 trace 行数；两个后端还由
`DMGMD_TIMING phase=run` 的实际 `backend` 字段核验，`DMGMD_MPI` 和通信行提供
逐 rank 交叉证据。A/B 顺序沿用
benchmark `order_seed=20260922` 洗牌，summary 按 `profiling` 分组。

trace 的整数纳秒由 C++ `clock_gettime(CLOCK_MONOTONIC)` 获取；C1 在同一 host/time namespace
使用同一 clock。runner 用 C1 注册的 rank/PID/process start ticks 检验并生成
`cpu/markers.jsonl`，C1 解析器只纳入完全落在 marker 内的相邻采样区间。
CUDA event 的 `pack_device_seconds`、`unpack_device_seconds`、`nep_device_seconds` 是
设备相对时长，**不是** host 绝对时间戳。host enqueue bracket 与既有 thermo D2H/段末
同步只提供起始/就绪边界；本轮没有 CUDA event↔CLOCK_MONOTONIC 精确时钟变换，
不绘制伪精确的 GPU/CPU 绝对交叠。没有增加逐步 barrier、`cudaEventSynchronize` 或新的
`cudaDeviceSynchronize`；原先诊断代码在段末比生产多执行的一次 device sync 已合并到
所有模式原有的段末同步位置，仍恰好一次。

## 验证和实测

环境预检在沙箱内因 UCX `cuda_copy` 不可见而失败；按照 AGENTS.md 在宿主 source
相同脚本后 [通过](../../dmgmd-overlap-o1-20261012/gates/preflight-delivery.txt)：
Open MPI/UCX、两 GPU UUID、自检及 device-buffer collective/p2p 均 PASS。
CPU/解析测试 15 项通过；CTest 8 项通过、沙箱中的 CUDA 专项按环境规则跳过；
最终二进制四个单 rank [golden case](../../dmgmd-overlap-o1-20261012/gates/golden-delivery.txt)
PASS，M2a P=2 [HostStaged/CudaAware 正确性矩阵](../../dmgmd-overlap-o1-20261012/gates/domain-delivery.txt)
PASS（含迁移、重建、精确通信字节、timing A/B、oracle）。
两个 runner 的 [help](../../dmgmd-overlap-o1-20261012/gates/benchmark-help-delivery.txt)、
[dry-run](../../dmgmd-overlap-o1-20261012/gates/benchmark-dry-run-delivery.txt)、
[重建 help](../../dmgmd-overlap-o1-20261012/gates/rebuild-help-delivery.txt)、
[重建 dry-run](../../dmgmd-overlap-o1-20261012/gates/rebuild-dry-run-delivery.txt)
均已实际执行；所有最终 smoke/pilot trial `status=ok`、
有 C1 时 `cpu_status=ok`，off 与 trace 模式均从程序日志确认。
最终二进制与 runner 的四个
[smoke trial](../../dmgmd-overlap-o1-20261012/delivery-smoke-20261012-163518/summary.json)
均 `ok`，其 metadata 记录交付版二进制与 runner SHA。

pilot 普通步输入为 `carbon_200k`：`model.xyz`
`07c87849e5afdea3236fc46c8ba961477a35744451fcd697af7c250d4292df79`、`nep.txt`
`add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623`、`run.in`
`09a184b7ac67857a1cbf7fc9f061ca95a2d7a7aa4e0b41b2c05f1c4dc9432c58`。
派生重建诊断把同一生成模型所有原子的 y 速度统一加 2 Å/fs：`model.xyz`
`69ba9c1c92b26ef6c4edd41ba5559a25c9633a9a9c13bf698f235efa4d1473c5`、
`run.in` `8c0611ffc34278e2097d549b837f6077ae9191e6d739cf8ccc27633fe94c159e`；
它用于阶段成本，不作为物理正确性 oracle。另保留原 M2a crossings 小 fixture 的
8/8 重建步：`model.xyz` `9795b0322d4088a561a3592c11427a47234175958bab35ec913fb21c32eb8966`。
派生重建输入的最终两后端 0.01 s C1 采样的 `thermo.out` SHA-256
同为 `ad731e744f15488441a4af3b0e86f6977b22dcf4672cfab79c86b9565c90511d`；
小 crossings fixture 的两后端 thermo/trajectory/NVT 输出也分别逐字节一致。

下表是 [原始 trace 重算](../../dmgmd-overlap-o1-20261012/analysis.json) 中
测量段 `sequence=1` 的 **rank-step 中位数**，每后端普通步 200 个 rank-step，
派生输入重建步每后端 66 个 rank-step；两个输入的绝对耗时不能互相当作加速比。

| 输入/后端 | step ms | halo 或 rebuild host ms | staging 或 CudaAware sync ms | `MPI_Waitall` host ms | 完整未分区 descriptor/force CUDA ms | 本地 halo send/recv bytes 中位数 |
|---|---:|---:|---:|---:|---:|---:|
| 普通 HostStaged | 15.585 | halo 0.202 | D2H 0.049 / H2D 0.053 | 0.079 | 14.209 | 430080 / 430080 |
| 普通 CudaAware | 16.336 | halo 0.877 | input sync 0.0039 / output sync 0.0013 | 0.850 | 14.324 | 430080 / 430080 |
| 重建 HostStaged | 88.554 | rebuild 62.296 | 内部阶段未拆 | 该步 p2p 未单独括号 | 14.298 | 737280 / 737280 |
| 重建 CudaAware | 88.287 | rebuild 61.641 | 内部阶段未拆 | 该步 p2p 未单独括号 | 14.275 | 737280 / 737280 |

普通步两后端同样的本地 halo 字节，但 `MPI_Waitall` 差异是本机 Open MPI/UCX 的
host 调用时间，不能命名为网络带宽或证明 GPU/MPI overlap。重建步还记录本地 migration
send/recv 中位数各 40960 bytes、control 各 16 bytes；其通信、layout、upload 和同步
都在整体 rebuild 括号内，不能把该括号误称为纯传输。原始 `DMGMD_DOMAIN_TIMING`
summary/histogram 与每步 `DMGMD_DOMAIN_COMM` 也保留在同一 stdout。普通步每 rank
有 3 次 collective，现有日志的全局 MPI input/output 计数各 152 bytes；
HostStaged 的全局 device→host/host→device 计数各 128 bytes，CudaAware 为 0。
这些是 runtime 的应用字节计数，不等于链路实际传输字节。

CPU：普通 pilot 0.1 s C1 采样的测量段完整窗口中，HostStaged rank 0/1 各有
14 个有效区间、1.41/1.40 CPU-s、约 1.007/1.000 core equivalent；CudaAware
两 rank 各 15 个、1.52/1.51 CPU-s、约 1.013/1.007 core equivalent。
重建派生输入在 0.01 s 采样时，测量段 HostStaged rank 0/1 为 396/395 个有效区间、
3.95/3.96 CPU-s，CudaAware 为 396/396 个、3.99/3.99 CPU-s；
两后端的 66 个测量段重建 rank-step **均至少有一个完整 C1 区间**，总有效区间
分别 507/501，覆盖到的 CPU-s 分别 5.05/5.06（跨 rank 的样本窗口不完全同步，
不作为同一瞬间 job CPU）。普通步短于 0.1 s 采样周期时每步 CPU 为空；
小 crossings fixture 即使用 0.001 s 仍没有有效进程区间，保留 `null`。
CPU 时间无法区分 MPI 忙轮询与有效计算。

最终二进制的 pilot 中每后端各跑一次 trace/proc 与 off/proc。HostStaged 的
trace/off 正式段分别为 1.57558/1.56961 s，CudaAware 为 1.65045/1.65818 s。
这是 A/B 模式通路核验，不发布单次 trace 速度比或 profiler 开销估计。
四个普通 pilot trial 的 `thermo.out` SHA-256 均为
`b86131394b157db0616c0160fb5e8390c9a542044f1f77c914bb7d30a10471bf`；
关闭诊断没有改变本次科学输出。C1 采样器开关的有限校准见下方正式组；
C2/C3 研究尚未完成。

正式关闭 profiler 的 `standard` 窄矩阵：同一 `carbon_200k` 输入、200 步 warmup、
1000 步正式段、P=2、两后端、各 3 次；每个 trial 的正式段均 ≥10 s 且 `status=ok`。

| 后端 | C1 proc：中位 s（min–max），3/3 affinity verified | C1 off：中位 s（min–max） |
|---|---:|---:|
| HostStaged | 15.71772（15.57216–15.83396） | 15.68301（15.67573–15.74769） |
| CudaAware | 16.49771（16.46841–16.58226） | 16.45508（16.33535–16.45696） |

proc 组三次每后端都由 C1 验证上述 4 物理核/NUMA0/两唯一 GPU 的真实映射；
off 组每次有 Open MPI binding stderr 和相同启动策略，但没有 task 采样，故不把它写成
逐 trial affinity 已验证。两组全部正式 trial 的 `thermo.out` SHA-256 同为
`8c5d3271bca065d7c7e1e2554309a72d7651cdaf93445b1de058a60e47dc8c24`。
proc/off 的 HostStaged 中位比约 1.0022、CudaAware 约 1.0026；组序未交错，
差值与试验间波动同量级，仍无法解析出 C1 的精确开销。这是复用 C1 工具的有限
校准，不替代 C2/C3。该窄矩阵只提供 **串行 HostStaged/CudaAware 基线**；
当前没有 overlap 算法，不能计算加速比或强扩展效率。

## 原始产物与重跑命令

全部原始产物保留在仓库内忽略版本控制的
[`dmgmd-overlap-o1-20261012/`](../../dmgmd-overlap-o1-20261012/)；本报告和代码是 Git
工作树中的本轮改动。每个 trial 的 `stdout.txt`、`stderr.txt`、`telemetry.jsonl`、
`result.json` 与顶层 `metadata.json`/`source-dirty.patch` 均未过滤；启用 C1 的 trial
还保留 `cpu/samples.jsonl`、`cpu/summary.json`，trace trial 另有
`cpu/markers.jsonl`。代表性原始文件：

- 普通步 [HostStaged trace](../../dmgmd-overlap-o1-20261012/delivery-pilot-trace-20261012-163556/0000-carbon_200k-dmgmd-HostStaged-p2-r0-trace/stdout.txt)、[CudaAware trace](../../dmgmd-overlap-o1-20261012/delivery-pilot-trace-20261012-163556/0001-carbon_200k-dmgmd-CudaAware-p2-r0-trace/stdout.txt)；
- 重建步 [HostStaged trace](../../dmgmd-overlap-o1-20261012/delivery-rebuild-drift-20261012-163815/0000-carbon-drift-HostStaged-p2-r0/stdout.txt)、[CudaAware trace](../../dmgmd-overlap-o1-20261012/delivery-rebuild-drift-20261012-163815/0001-carbon-drift-CudaAware-p2-r0/stdout.txt)；
- CPU [原始样本](../../dmgmd-overlap-o1-20261012/delivery-rebuild-drift-20261012-163815/0000-carbon-drift-HostStaged-p2-r0/cpu/samples.jsonl)、[marker](../../dmgmd-overlap-o1-20261012/delivery-rebuild-drift-20261012-163815/0000-carbon-drift-HostStaged-p2-r0/cpu/markers.jsonl)、[解析结果](../../dmgmd-overlap-o1-20261012/delivery-rebuild-drift-20261012-163815/0000-carbon-drift-HostStaged-p2-r0/cpu/summary.json)；
- [普通 pilot metadata](../../dmgmd-overlap-o1-20261012/delivery-pilot-trace-20261012-163556/metadata.json)、[性能组 metadata](../../dmgmd-overlap-o1-20261012/delivery-standard-proc-20261012-163857/metadata.json)、[重建 metadata](../../dmgmd-overlap-o1-20261012/delivery-rebuild-drift-20261012-163815/metadata.json)、[重算脚本](../../dmgmd-overlap-o1-20261012/compute_analysis.py)。重算脚本 SHA-256 `c51e2e7b0e2c0caaff20d217b1fa407d97e71db6caa7530fa1f751b4a2aca8e4`，其 [分析 JSON](../../dmgmd-overlap-o1-20261012/analysis.json) SHA-256 `2deab4f4f26654e7aa58a2f87fb6ce9e2544795f497e863485d21d630a10b985`。
- 正式 [proc 组 summary](../../dmgmd-overlap-o1-20261012/delivery-standard-proc-20261012-163857/summary.json)、[无采样组 summary](../../dmgmd-overlap-o1-20261012/delivery-standard-nosampler-20261012-164248/summary.json)；各顶层 metadata/result 与逐 trial stdout/stderr、原始 C1 数据同目录保留。

每个新 shell 必须从 `newmd` 仓库根目录开始：

```bash
source ../env/md-mpi.sh
cmake --build build -j 8
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1 --ranks 2
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
python3 tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2 \
  --backends HostStaged,CudaAware --devices 0,1
python3 tests/benchmark/test_benchmark.py
```

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile smoke --engines dmgmd \
  --devices 0,1 --ranks 2 --backends HostStaged,CudaAware \
  --dmgmd-profiling off,trace --cpu-sampling proc --cpu-sample-interval 0.05 \
  --expected-cores-per-rank 4 --expected-numa-node 0 \
  --mpiexec-arg=--map-by --mpiexec-arg=ppr:2:numa:PE=4 \
  --mpiexec-arg=--bind-to --mpiexec-arg=core --mpiexec-arg=--report-bindings \
  --output dmgmd-overlap-o1-20261012/delivery-smoke
python3 tests/benchmark/run_benchmark.py --profile pilot --cases carbon_200k \
  --engines dmgmd --devices 0,1 --ranks 2 --backends HostStaged,CudaAware \
  --dmgmd-profiling off,trace --cpu-sampling proc --cpu-sample-interval 0.1 \
  --expected-cores-per-rank 4 --expected-numa-node 0 \
  --mpiexec-arg=--map-by --mpiexec-arg=ppr:2:numa:PE=4 \
  --mpiexec-arg=--bind-to --mpiexec-arg=core --mpiexec-arg=--report-bindings \
  --output dmgmd-overlap-o1-20261012/delivery-pilot-trace
```

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_overlap_rebuild.py --fixture carbon-drift \
  --candidate ./build/dmg-md --devices 0,1 --warmup 10 --steps 100 \
  --expected-cores-per-rank 4 --expected-numa-node 0 --cpu-sample-interval 0.01 \
  --mpiexec-arg=--map-by --mpiexec-arg=ppr:2:numa:PE=4 \
  --mpiexec-arg=--bind-to --mpiexec-arg=core --mpiexec-arg=--report-bindings \
  --output dmgmd-overlap-o1-20261012/delivery-rebuild-drift
python3 dmgmd-overlap-o1-20261012/compute_analysis.py
```

```bash
source ../env/md-mpi.sh
python3 tests/benchmark/run_benchmark.py --profile standard --cases carbon_200k \
  --engines dmgmd --devices 0,1 --ranks 2 --backends HostStaged,CudaAware \
  --dmgmd-profiling off --cpu-sampling proc --cpu-sample-interval 0.2 \
  --expected-cores-per-rank 4 --expected-numa-node 0 \
  --mpiexec-arg=--map-by --mpiexec-arg=ppr:2:numa:PE=4 \
  --mpiexec-arg=--bind-to --mpiexec-arg=core --mpiexec-arg=--report-bindings \
  --output dmgmd-overlap-o1-20261012/delivery-standard-proc
python3 tests/benchmark/run_benchmark.py --profile standard --cases carbon_200k \
  --engines dmgmd --devices 0,1 --ranks 2 --backends HostStaged,CudaAware \
  --dmgmd-profiling off --cpu-sampling off \
  --mpiexec-arg=--map-by --mpiexec-arg=ppr:2:numa:PE=4 \
  --mpiexec-arg=--bind-to --mpiexec-arg=core --mpiexec-arg=--report-bindings \
  --output dmgmd-overlap-o1-20261012/delivery-standard-nosampler
```

所有 `--output` 都由 runner 添加本地时间戳，已有目录不覆盖。`--help`/`--dry-run`
示例与固定 affinity 的解释在 [benchmark README](../../tests/benchmark/README.md#o1-串行时间线与-ab-配置)。
`source-dirty.patch` 在不同试验阶段的 SHA 随后续文档/runner 改动变化；同一二进制
SHA、精确输入 SHA 和每次 metadata 是判断可比较性的依据。

早期探索失败已留原始日志：`core:PE=4` 在本机 Open MPI 5 被拒绝（mapping object 只有
1 CPU），改为实测有效的 `ppr:2:numa:PE=4`；M2a 测试第一次误给 `--ranks 1,2`
触发测试自身 quiet case 使用 P=1，按其支持范围改为 `--ranks 2` 后 PASS；
crossings 初版校验器误假设含 ordinary，原始 trace 证明 8 步全 rebuild，修正并重跑 PASS。
这些失败不计入有效基线。

## 不可观测项与 O1 停点

- 没有分区数据结构，`T_interior`、`T_boundary` 和 overlap ratio 均为 N/A。
- MPI request post→Waitall completion 只是请求在途；本轮无独立 NIC 传输 trace、
  CUDA event 绝对时间校准或 UCX progress 机制证据，不能宣称网络/GPU 实际交叠。
- 重建内部 staging/MPI Waitall 没有 O1 专属拆分；整体 rebuild 括号包含迁移、
  membership、CPU layout、device upload。C1 短步缺窗保持 null，较密采样有 observer 成本。
- 仅单节点 P=2、两张 NUMA0 GPU 的窄基线；多 NUMA、多节点、P=4/8、其他输入和
  完整 C2/C3 采样扰动研究尚未覆盖，不能推断强扩展或普遍性能收益。

O1 到此停止。下一阶段 O2 需另行处理 O0 提出的分区实现方向确认；本轮没有接入异步计算。
