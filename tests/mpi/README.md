# MPI 验收入口

从包含 AGENTS.md 的仓库根目录运行，每个新 shell 先加载环境：

```bash
source ../env/md-mpi.sh
cmake --build build -j 8
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
```

GPU 门槛通过后才启动数值矩阵；沙箱隔离按 AGENTS.md 处理，不回退 system MPI/UCX。
已有 replicated/migration/domain runner 各自的 `--help` 是选项事实源。

## O3 halo 生命周期

```bash
source ../env/md-mpi.sh
python3 tests/mpi/run_halo_lifecycle.py --help
python3 -u tests/mpi/run_halo_lifecycle.py --candidate ./build/dmg-md \
  --devices 0,1,2,3 --work-root dmgmd-halo-lifecycle-new
python3 -u tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2,4 \
  --backends HostStaged,CudaAware --devices 0,1,2,3 --partition-modes off,serial,verify \
  --work-root dmgmd-halo-domain-new
```

`--work-root` 必须是不存在的新目录，所有精确命令/环境、stdout/stderr 与 results.json
会保留。生命周期 runner 自行重复四 GPU 门槛，使用默认构建的独立
`build/tests/dmgmd_mpi_halo_lifecycle_tests`（可由 `--test-executable` 指定）。
CTest 不自动启动此 MPI/GPU runner。

正常矩阵覆盖 HostStaged/CudaAware、P=1/2/4、count 1/0/17/2/1 的增长与复用，两个
component、不同方向数据、立即 finish/progress-only、重复 finish 和精确字节记账。
`receive-first`/`send-first` 由测试 wrapper 暂缓一组 Testall 完成观察，检查接收 GPU/unpack
完成与发送源复用的不同条件；不把它解释成真实网络完成顺序或 overlap 证据。

故障矩阵仅 rank0 注入：非法 count、第二个 send post 失败、Waitall/Testall 失败、
unpack event 失败、重复 begin、在途 host face 操作、非法 token、未 finish 的析构、
启动自检第二个 post 失败，以及 HostStaged D2H/H2D 失败。每例要求整个作业非零退出且
输出指定 halo 原因；超时直接失败，不把被杀死的挂起作业计作通过。
GNU 链接 wrapper 只存在于独立测试程序，生产没有测试开关。

数值 runner 继续比较 P1 oracle、full/serial/verify 的逐原子 energy/force/virial、
轨迹/输出/restart，验证实际 split、回退、空 rank、多段 run、skin/rebuild/迁移与精确字节。
O3 验收另将结果与锁定 O2 产物逐字节核对；本轮具体命令、哈希、结果见
[O3 状态](../../docs/status/halo-lifecycle-20261015.md)，接口见
[halo 生命周期标准](../../docs/standards/halo-lifecycle.md)。
