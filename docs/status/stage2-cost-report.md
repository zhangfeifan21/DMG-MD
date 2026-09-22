# 第二阶段统一重建与分步计时成本报告

类别：进度与实测备忘。

更新日期：2026-09-22。本文只记录本次实际运行，不定义容差或新的性能门槛。

## 1. 结论

第二阶段已经具备进入 M2b/MatPL 比较所需的正确性与计时基础：统一 cache decision、epoch 内
manager owner 保持、普通步固定 ghost 坐标刷新、rebuild transaction 内迁移与
membership/layout/neighbor 更新均已实现；详细计时默认关闭，开启后不逐步打印、不逐步增加
MPI 汇总，也不增加默认 device synchronize。

定向 `micro_crossings` 在 P=2/4、HostStaged/CudaAware 下让原子以累计位移不超过
`skin/2=0.5 Å` 连续跨内部 slab 与周期端，所有配置均保持一个 layout epoch、
`migration_steps=0`、`rebuild_steps=0`，且与 P=1 oracle 一致。因此高频微小跨界已不再单独
触发重建或迁移。

真实 10000-step nightly 几何中，carbon 没有 rebuild；water 为 225/10000（2.25%）；
BaTiO3 为 19/10000（0.19%）。water/BaTiO3 的 rebuild 步平均约 5.21–6.05 ms，普通步约
1.32–1.53 ms。carbon 普通步约 4.07–4.66 ms，NEP device 本身约 3.27–3.67 ms，是主要成本。
HostStaged 在本机所有 P=2/4 配置都快于 CudaAware；当前 CudaAware 路径仍在 MPI 前后显式满足
数据就绪且没有通信计算重叠，本阶段没有机械删除同步。

## 2. Provenance 与口径

- 源 revision：`cfb3b2ec3ac1a4dccd16de43e2d81f3d9ef84432` + 未提交 dirty diff；完整
  status、binary diff、dirty 文件哈希和 CMake cache 保存在两个结果目录的 `provenance/`。
- Release：`CMAKE_BUILD_TYPE=Release`，CUDA arch `75;80;86;89;90`；最终
  `build/dmg-md` SHA-256
  `13da7a81c9c77f46b53dd45ada81d7a593634800c30b2714c13d2fa3bc93b80d`。
- 环境：Open MPI 5.0.10、UCX 1.22.0、CUDA 12.9、driver 580.173.02；使用 GPU 0–3，均为
  RTX 4090。`check_environment.py` 验证 `cuda_copy/cuda_ipc`、唯一绑定、device collective
  与 CudaAware p2p。
- manifest SHA-256：`30010c0d15b9ef383ace008aa48c5a4b80821bbf49e6a408df69ff7b9c179c50`。
- nightly model SHA-256：carbon
  `59dcd7aa32b3c451e8bcceb2b82d4a4332b8a5b4c0e96d30495d8de0f8b2f5ca`，water
  `e7e3441967fd834824af3b1122321106b2d8415d8233298a067e0382637829de`，BaTiO3
  `0fd8e54db9ccd4dd0b72ec89e2cab349965d230e7c1f97f642d28c125d246aaa`。
- potential SHA-256 与第一阶段记录相同：carbon
  `add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623`、water
  `8638300c8c6ba7eca589fa2fdc111d2e6a1f6ad9c88cc522ea9db220538a66e3`、BaTiO3
  `d9d5801eb267772294deee6eebd2e1e40f83908f867b5bf69467d1826034aa02`；未改 potential、
  密度、步长、精度或容差。
- 正式端到端结果：[`dmgmd-stage2-cost-total-final2-20260922`](../../dmgmd-stage2-cost-total-final2-20260922/report.json)，
  `DMGMD_DOMAIN_TIMING=0`、diagnostics 关闭；详细结果：
  [`dmgmd-stage2-cost-detail-final2-20260922`](../../dmgmd-stage2-cost-detail-final2-20260922/report.json)，
  P=2/4、`DMGMD_DOMAIN_TIMING=1`。两者都保留每个 stage 的输入、stdout、checkpoint 和输出哈希。
- 正式口径是 runtime `DMGMD_TIMING phase=run seconds_max`，包含 10000 步 NVE、既有 thermo/
  trajectory/restart I/O 和 MPI，不用 wrapper elapsed。每个配置本轮只采一个样本，所以不提供
  置信区间；`seconds_min/max` 是同一次 MPI 作业的 rank 范围，不是重复实验误差。

精确命令：

```bash
source ../env/md-mpi.sh
scripts/run_long_nve_nightly.sh \
  --result-root dmgmd-stage2-cost-total-final2-20260922 \
  --cases carbon_crystal,dense_water,batio3_zbl --ranks 1,2,4 \
  --backends HostStaged,CudaAware --sections long --performance --ui plain
scripts/run_long_nve_nightly.sh \
  --result-root dmgmd-stage2-cost-detail-final2-20260922 \
  --cases carbon_crystal,dense_water,batio3_zbl --ranks 2,4 \
  --backends HostStaged,CudaAware --sections long --domain-timing --ui plain
```

## 3. 端到端结果

| case / atoms | backend | P1 s | P2 s / speedup | P4 s / speedup |
| --- | --- | ---: | ---: | ---: |
| carbon / 24576 | HostStaged | 50.583 | 43.805 / 1.155× | 40.023 / 1.264× |
| carbon / 24576 | CudaAware | 50.542 | 45.401 / 1.113× | 41.424 / 1.220× |
| water / 24576 | HostStaged | 15.243 | 14.307 / 1.065× | 13.635 / 1.118× |
| water / 24576 | CudaAware | 14.177 | 15.133 / 0.937× | 14.513 / 0.977× |
| BaTiO3 / 20000 | HostStaged | 14.228 | 13.524 / 1.052× | 13.240 / 1.075× |
| BaTiO3 / 20000 | CudaAware | 13.338 | 15.086 / 0.884× | 14.696 / 0.908× |

CudaAware 相对 HostStaged 的 P2/P4 `seconds_max` 分别慢：carbon 3.6%/3.5%，water
5.8%/6.4%，BaTiO3 11.5%/11.0%。P1 的 backend 名仅来自相互独立的重复运行；P1 恒走
M1 fallback，没有 M2a p2p 后端差异。

用户给出的 2026-09-18 correctness-nightly 观测使用旧二进制和详细正确性日志，不能作为本表
无 I/O A/B 的置信基线；本表只与同一最终二进制、同一 runner 内的 P1 参照计算 speedup。

## 4. 普通步、重建步与阶段成本

下表均为详细计时运行的 rank-mean 每步毫秒。`thermo host` 包含等待此前 GPU 工作与 thermo
Allreduce 的关键路径，`MPI wait` 是 MPI call wall interval；它们与 NEP/device interval 重叠，
不能横向相加。`I/O` 包括 scientific gather/formatter/file，非输出 rank 接近零。

| case | backend/P | ordinary/rebuild | step ord/reb | decision ord | halo wait ord | cell ord | NEP ord/reb | thermo host ord/reb | I/O ord | MPI wait ord/reb |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| carbon | H/2 | 10000/0 | 4.404/— | 0.167 | 0.057 | 0.224 | 3.671/— | 3.970/— | 0.110 | 0.211/— |
| carbon | H/4 | 10000/0 | 4.071/— | 0.223 | 0.090 | 0.158 | 3.273/— | 3.595/— | 0.058 | 0.379/— |
| carbon | C/2 | 10000/0 | 4.661/— | 0.166 | 0.239 | 0.225 | 3.662/— | 4.032/— | 0.114 | 0.500/— |
| carbon | C/4 | 10000/0 | 4.244/— | 0.217 | 0.252 | 0.159 | 3.287/— | 3.598/— | 0.062 | 0.583/— |
| water | H/2 | 9775/225 | 1.373/5.488 | 0.155 | 0.036 | 0.083 | 0.839/0.837 | 0.976/1.140 | 0.109 | 0.169/0.777 |
| water | H/4 | 9775/225 | 1.317/5.210 | 0.204 | 0.048 | 0.077 | 0.750/0.748 | 0.905/1.001 | 0.057 | 0.256/1.103 |
| water | C/2 | 9775/225 | 1.463/5.662 | 0.156 | 0.108 | 0.083 | 0.838/0.836 | 0.977/1.146 | 0.113 | 0.272/0.840 |
| water | C/4 | 9775/225 | 1.392/5.421 | 0.201 | 0.115 | 0.078 | 0.750/0.748 | 0.900/1.011 | 0.061 | 0.351/1.131 |
| BaTiO3 | H/2 | 9981/19 | 1.375/5.861 | 0.150 | 0.049 | 0.065 | 0.870/0.870 | 0.983/1.090 | 0.091 | 0.170/0.280 |
| BaTiO3 | H/4 | 9981/19 | 1.335/5.250 | 0.195 | 0.066 | 0.060 | 0.790/0.790 | 0.919/1.050 | 0.047 | 0.250/0.419 |
| BaTiO3 | C/2 | 9981/19 | 1.533/6.046 | 0.152 | 0.186 | 0.065 | 0.873/0.873 | 0.987/1.103 | 0.095 | 0.345/0.392 |
| BaTiO3 | C/4 | 9981/19 | 1.494/5.352 | 0.198 | 0.204 | 0.060 | 0.790/0.789 | 0.921/1.056 | 0.051 | 0.440/0.483 |

重建专属的 rank-mean 毫秒如下；carbon 无真实 rebuild，因此不伪造平均值。water 的 225 次中
P2/P4 分别 195/202 次实际迁移；BaTiO3 的 19 次全部迁移。

| case | backend/P | migration | membership/layout | allocation/upload | cell/neighbor | decision | remaining host |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| water | H/2 | 1.458 | 1.105 | 0.470 | 0.754 | 0.523 | 0.575 |
| water | H/4 | 1.194 | 1.031 | 0.454 | 0.684 | 0.768 | 0.581 |
| water | C/2 | 1.483 | 1.173 | 0.486 | 0.782 | 0.526 | 0.604 |
| water | C/4 | 1.267 | 1.079 | 0.490 | 0.722 | 0.759 | 0.617 |
| BaTiO3 | H/2 | 1.410 | 2.211 | 0.454 | 0.618 | 0.072 | 0.525 |
| BaTiO3 | H/4 | 1.008 | 2.056 | 0.472 | 0.537 | 0.075 | 0.488 |
| BaTiO3 | C/2 | 1.448 | 2.301 | 0.476 | 0.637 | 0.069 | 0.547 |
| BaTiO3 | C/4 | 1.020 | 2.103 | 0.480 | 0.556 | 0.080 | 0.509 |

详细计时相对独立的 timing-off `seconds_max` 增加 0.84%–4.39%；这同时含运行间噪声，不能当
纯 instrumentation 消融，但足以说明详细结果不可替代正式吞吐。默认关闭时没有 CUDA event、
计时 summary reduction 或额外同步。

## 5. 布局、通信、分配与 rank 差异

以下范围覆盖 HostStaged 详细 run 的所有实际 layout epoch；dependency centers 是
`owned + dependency ghosts`，ghost 范围含 dependency 与 coordinate-only。

| case/P | epochs | owned/rank | dependency centers/rank | ghosts/rank | local/rank | ordinary halo send B/rank/step |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| carbon/2 | 1 | 12288 | 14464 | 4480 | 16768 | 107520 |
| carbon/4 | 1 | 6144 | 8320 | 4480 | 10624 | 107520 |
| water/2 | 226 | 12263–12313 | 13076–13171 | 1702–1819 | 13997–14107 | 40848–43656 |
| water/4 | 226 | 6107–6174 | 6930–7040 | 1674–1822 | 7841–7966 | 40128–43728 |
| BaTiO3/2 | 20 | 9979–10021 | 11800 | 3490–3539 | 13495–13524 | 83760–84936 |
| BaTiO3/4 | 20 | 4972–5016 | 6800 | 3468–3534 | 8479–8530 | 83352–84648 |

普通步固定有 3 次 collective：decision MAX + reason OR + thermo Allreduce，input/output 各
`76P` B；每 1000 force call 的 `neighbor.out` 采样步另有两次 MAX、各 `16P` B。halo 是
`24*(send_left+send_right)` B/rank/step，上表给出实际 layout 范围；HostStaged/CudaAware
字节语义完全相同。rebuild 另有 40 B/membership record、8 B/rank face count，以及发生迁移时
80 B/atom（本 run 未启用 unwrapped）的 direct Alltoallv 记录。逐步精确字节模型已由
`run_mpi_domain.py` 双后端通过。

所有真实 nightly 段的 `capacity_growth_events=0`，即 water 225 次、BaTiO3 19 次 layout
upload 都复用已有 capacity；段内 `GPU_Vector` allocation 为 6、cumulative 为 38，来自一次性
decision/reference scratch，不随 rebuild 次数增长。

P4 ordinary 的 rank min–max 每步毫秒显示，NEP 计算差异小于 MPI 等待差异：

| case/backend | NEP min–max | MPI wait min–max | scientific output min–max |
| --- | ---: | ---: | ---: |
| carbon/H | 3.190–3.356 | 0.141–0.510 | 0.003–0.224 |
| carbon/C | 3.197–3.373 | 0.347–0.726 | 0.008–0.222 |
| water/H | 0.733–0.768 | 0.101–0.320 | 0.003–0.218 |
| water/C | 0.735–0.767 | 0.199–0.412 | 0.007–0.222 |
| BaTiO3/H | 0.772–0.805 | 0.109–0.312 | 0.002–0.183 |
| BaTiO3/C | 0.769–0.806 | 0.296–0.505 | 0.005–0.187 |

所以 run 的 `seconds_min/max` 接近并不表示没有不均衡：rank 0 的 output/gather 明显更高，
其他 rank 在 collective/MPI 中等待；CudaAware 的等待区间整体更高。owned 数很均衡，但两跳
ghost 使 P=4 的全局 dependency-center 总工作反而上升（例如 carbon 从 P2 的 28928 增到 P4
的 33280），限制了强扩展。

## 6. 剩余瓶颈与下一步

- 普通步：carbon 仍由 NEP 主导；water/BaTiO3 的 NEP 已较短，固定 decision、halo、thermo
  Allreduce/D2H 和 MPI 到达偏斜占比上升。P=4 两跳 ghost/dependency-center 复制和当前全局盒
  cell 网格进一步限制扩展。
- 重建步：water/BaTiO3 主要是 direct migration、membership/layout/upload 与 cell/neighbor；
  NEP 本身与普通步接近。减少重建已生效，但真实 water 仍有 2.25% rebuild，应在第三阶段对比
  M2b/MatPL 时继续单列，不能只看总时间。
- 后续具体待办：把重建的 cell list 改为 rank-local cell 网格（保留相同候选/排序 oracle）；
  对 NVE 只在科学输出/控制真正需要时计算 thermo，同时保持 NVT thermostat 每步全局 thermo；
  第三阶段按计划实现并比较 M2b 与 MatPL 式 ghost 力回传。通信计算重叠仍留到之后。
- 本轮不实现 M2b、ghost force return、overlap、3D decomposition、动态负载均衡或新势函数；
  也不因 water/BaTiO3 的 CudaAware 仍负扩展而放宽任何正确性门槛。

## 7. 最终验证证据

以下均在最终二进制上、每个新 shell 先 `source ../env/md-mpi.sh` 实际执行：

| 命令 | 结果 |
| --- | --- |
| `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j` | PASS |
| `ctest --test-dir build --output-on-failure` | 6 项：5 PASS，CUDA 项在沙箱内按合同 SKIP；宿主机单独 PASS |
| `python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS：Open MPI 5.0.10、UCX 1.22.0、4-rank CudaAware probe |
| `./build/tests/dmgmd_domain_neighbor_cuda_tests` | PASS |
| `python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0` | 4/4 PASS，无 golden/容差变更 |
| `python3 tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS：P=2/4 × 双后端，含 micro-crossing、两跳闭包、空 rank、PBC/multi-slab、连续 run、计时 A/B、restart 与精确字节模型 |
| `python3 tests/mpi/run_mpi_differential.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS：P=1/2/4 × 双后端 |
| `python3 tests/mpi/run_mpi_migration.py --candidate ./build/dmg-md --devices 0,1,2,3` | PASS：unsupported、空域、N<P、迁移与跨 rank restart 全矩阵 |
| `scripts/run_long_nve_profile.sh --profile smoke --result-root dmgmd-stage2-smoke-final-20260922 --ui plain` | PASS：7/7；三体系 NVE/NVT、双向回放、restart 与四个兼容势短程 |
| `python3 tests/mpi/run_rank_io_isolation.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4` | PASS：健康路径及 mkdir/file/chdir/restore/cleanup 故障注入；无挂起 |
| 本文 §2 两条 nightly 命令 | timing-off 18/18、timing-on 12/12 PASS；三体系 long 正确性检查均通过 |

定向 all-rebuild fixture 已由同一最终二进制的 `run_mpi_domain.py` 验证 rebuild 分类与
transaction 路径；该体系仅 8 个原子且逐步 I/O，不用于真实 MD 性能结论。100000-step release
矩阵仍未执行。
