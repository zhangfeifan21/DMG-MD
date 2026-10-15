# O3 halo 生命周期验收（2026-10-15）

类别：实现/实测备忘。状态：**PASS（O3 完成）**。
本轮仅 O3：原位置 begin→立即 finish，不执行 O4、不新增 progress thread。

## 身份、授权与边界

维护者本轮明确要求执行 O3，检查 O2 正确性后实施。仓库实际根目录是工作区内
`newmd/` 子目录；起始 HEAD `bcedb72bdc57c2fe83202e3b423292d0bc3562fe`，工作树干净。
本轮未提交、未推送。逐文件核对 O2 source-state.json 的全部源码哈希与当前起始源码一致，
原二进制 SHA-256 `43629bed12fb9b5e9bea93ea677d9c2512e28b9f17a5d5f3cd1195df461151b6`
也一致，复制为 [dmg-md-o2](../../dmgmd-overlap-o3-20261015/dmg-md-o2) 保留。
O2 已通过完整 192 组数值矩阵、golden、CUDA 单测与 long_nve smoke，见
[O2 报告](./overlap-partition-20261015.md)；原始源码/产物已真实存在，未以设计完成代替通过。

O3 交付二进制 SHA-256：`d1abb2deb8e54d1552a54c1030a33dc89a12795fe946b890671d93d77989e646`。
测试二进制/源码哈希见 [delivery-build-sha256.json](../../dmgmd-overlap-o3-20261015/delivery-build-sha256.json)。
完整 tracked diff、新文件与身份见 [source-state.json](../../dmgmd-overlap-o3-20261015/source-state.json)
与同目录 source-final.patch/source-files。仅 HEAD 无法标识本轮 dirty 实现。

起始与结束检查主仓库、GPUMD、LAMMPS、MatPL 四个 checkout。reference HEAD 分别为
`9d23496e41319b9e2af5221a7df6285387401d1e`、
`611ca3b7f8525ba802373b04f9e3d632d515f7e3`、
`21fabf34eadc57a717f4c7fbce9bb80aad5fd991`；起始 Git 无修改，GPUMD 有既有 ignored
构建物，其他两个无 ignored 产物。快照包含路径、lstat 大小/mtime（不追随旧的失效符号链接），
见 [start-state.json](../../dmgmd-overlap-o3-20261015/start-state.json)。所有运行/产物在主仓库
或临时目录，reference 未原位构建、修改或清理。末次路径/大小/mtime 与起始完全相同：
[reference-end-state.json](../../dmgmd-overlap-o3-20261015/reference-end-state.json)。

## 状态机与错误处理交接

生效合同迁入 [halo 生命周期标准](../standards/halo-lifecycle.md)。MPI 初始化线程仍申请
FUNNELED；没有新线程、新 stream、双缓冲或默认后端/分区策略变化。状态分别记录 pack
ready、send source ready、send completion、receive completion、receive GPU ready、
unpack completion 与 reusable。runtime 持有 request 与 storage，token 仅为 generation。
HostStaged pinned D2H/H2D 使用显式事件；CudaAware 保留 O2 输入/输出全局同步与目标
MPI/UCX 自检。每项同步替换的代码注释和标准表给出 producer/consumer 的依赖依据。

begin 完成发送源准备后发出四请求；finish 在原位置 Waitall 并等待物理 unpack 完成。
progress 分别测试收/发组，接收完成即可 H2D/unpack，但两侧完成且 unpack 结束才能解除
整个 storage lease。重复 finish/progress 幂等，下一 begin 拒绝旧 generation。单在途槽位
足够承载当前逐次 refresh→finish→compute 与段界重建调度，没有跨步在途复用需求。

部分 post、Waitall/Testall、CUDA、非法 token/重入/在途 host face 操作、未 finish 析构时，
本地诊断后 MPI_Abort，先终止作业再由进程释放资源，不做可能死锁的诊断 collective 或
只等待本 rank 已发出子集。审查修复了旧 p2p 自检的短路 post/wait：MPI 出错现在立即
abort；自检正常完成但数值错误仍按原合同回退 HostStaged。同步 host face exchange
同样保护部分 post 错误。禁止静默丢弃请求。

## 环境与验收

所有有效 shell 从实际仓库 source `../env/md-mpi.sh`，没有回退 system MPI/UCX。
首次在包装目录 source 路径失败后定位实际根目录；快照首次追随历史失效链接失败，改用
lstat 后重建快照。这两次均未修改生产代码或参考仓库。
沙箱 [环境预检](../../dmgmd-overlap-o3-20261015/preflight-sandbox.log) 的 cuda_copy 不可见，
按 AGENTS.md 到宿主执行同一脚本，O2 基线和 O3 最终构建四 GPU 门槛均通过。
宿主 n4，RTX 4090 GPU0–3，driver 580.173.02、CUDA 12.9；完整 CPU/GPU UUID/
MPI/UCX/compiler 信息见 [environment.json](../../dmgmd-overlap-o3-20261015/environment.json)。

| 验收 | 当前结果/原始证据 |
| --- | --- |
| O3 最终构建 | PASS，[build-final.log](../../dmgmd-overlap-o3-20261015/build-final.log) |
| 四 GPU 环境门槛 | PASS，Open MPI 5.0.10、UCX 1.22.0、cuda_copy/cuda_ipc、四 rank 实际 device-pointer probe；[preflight-final.log](../../dmgmd-overlap-o3-20261015/preflight-final.log) |
| 单 rank golden | PASS，4/4，原 manifest 容差；[golden-final.log](../../dmgmd-overlap-o3-20261015/golden-final.log) |
| CTest | PASS，10/10，CUDA 实际运行未 skip；[ctest-final.log](../../dmgmd-overlap-o3-20261015/ctest-final.log) |
| 生命周期/单边错误 | PASS，68/68 作业；[lifecycle-final.log](../../dmgmd-overlap-o3-20261015/lifecycle-final.log)、[results.json](../../dmgmd-overlap-o3-20261015/lifecycle-final/results.json) |
| O2 domain 数值矩阵 | PASS，16 cases × P2/4 × 两后端 × off/serial/verify = 192 组；[domain-final.log](../../dmgmd-overlap-o3-20261015/domain-final.log)、[原始产物](../../dmgmd-overlap-o3-20261015/domain-final/) |
| 锁定 O2 科学输出逐字节核对 | PASS，808 次比较，覆盖 707 个不同 O3 科学文件；[comparison.json](../../dmgmd-overlap-o3-20261015/o2-output-comparison.json) |
| long_nve smoke | PASS，7/7 配置、70 executed checkpoints，P1/HostStaged；[日志](../../dmgmd-overlap-o3-20261015/long-nve-final.log)、[报告](../../dmgmd-overlap-o3-20261015/long-nve-report.json)、[原始工作目录副本](../../dmgmd-overlap-o3-20261015/long-nve-work/) |
| O2/O3 固定资源串行成本 | PASS（测量通路），24/24 trial status=ok/cpu_status=ok；[cost-analysis.json](../../dmgmd-overlap-o3-20261015/cost-analysis.json) |

生命周期实际覆盖两后端 P=1/2/4、count 1/0/17/2/1、方向独立数据、普通立即 finish、
progress-only、重复 finish、完成后 free/下一代增长，以及 P=2 同 peer 的正确方向。
受控 wrapper 分别暂缓发送/接收 Testall 的完成观察，核对另一侧不会解锁槽位；这是
完成状态分离证据，不是网络完成顺序或真正 overlap 实测。代表性
[receive-first](../../dmgmd-overlap-o3-20261015/lifecycle-final/CudaAware-p2-receive-first/stdout.txt)
包含 `sends=0 receives=1 gpu=1 unpack=1 reusable=0`，随后五代 `HALO_REUSE` 全部完成。

错误矩阵只在 rank0 注入，其他 rank 正常执行；每例必须整作业非零退出、包含指定原因，
且未超时。覆盖非法 count、第二 send post（已有一条请求）、Waitall/Testall、unpack
事件、重复 begin、在途 host face、非法 token、活动槽位析构、自检第二 post，以及
HostStaged D2H/H2D 失败。故障 wrapper 只链接进测试程序，生产没有测试开关。

首轮测试的 CudaAware partial-post 注入计数混入了启动自检，因此测试自身未注入预期
故障而停止（[lifecycle-host.log](../../dmgmd-overlap-o3-20261015/lifecycle-host.log)）；
已在初始化后隔离计数，并单独新增 selftest-post 测试。交付结论仅引用最终 68/68 日志，
未把早期失败计作通过，没有放宽判定或修改数值容差。

## 字节、调用与额外内存

两后端普通位置 halo 的 MPI 字节均与 O2 相同：每 face 每原子 24 B，2 Isend + 2 Irecv
与 tag 1101/1102 不变，无新 collective。HostStaged 的 D2H=本地 send bytes、H2D=本地
recv bytes；CudaAware 均为零 staging。所有 byte/call 只在完成时加一次，重复 finish
不加。既有 p2p/collective 分栏口径不变；MPI 计数是应用缓冲字节，不是物理链路流量。

payload 额外内存为 **0 B**：原有 device send/recv 和 HostStaged pinned send/recv
高水位复用。新增四个 timing-disabled CUDA event、持久 request/状态、thread id 和
buffer lease 标志。x86-64 编译 sizeof 实测 Impl 从 **472 B→736 B（+264 B/rank）**，
DeviceBuffer/PinnedBuffer 各 16→24 B；CUDA event/driver 内部存储大小未知且不在该
sizeof 中。测量源码仅在产物目录放宽 C++ private 可见性来读取 sizeof，不构造 MPI/CUDA
对象、不改生产源；[测量结果](../../dmgmd-overlap-o3-20261015/memory-audit/result.txt)、
[精确测量命令](../../dmgmd-overlap-o3-20261015/memory-audit/run.sh)。

begin 的 HostStaged event wait 替换原 blocking D2H；H2D 与 unpack 用原 stream 排序，
finish 新增物理 unpack event host wait。CudaAware 原 device sync 均保留。四个生命周期
事件在关闭 profiler 时也存在，常规每交换 HostStaged 记录四个、CudaAware 三个；后者
send-source 由原 input device sync 证明，第四个 handle 保留。计时字段 H2D 现在表示
enqueue，transfer-wait 不包含末端 unpack host wait；总 halo host 与 step wall 含它。

## 串行成本实测

复用 benchmark/C1，宿主 n4 GPU0/1；carbon_200k 实际 N=196608，P=2，每 rank
固定 4 物理核，CPU affinity 为 NUMA0，GPU UUID 固定。24 个 trial 均由 C1 核验
rank0 为逻辑 CPU 0–3/96–99、rank1 为 4–7/100–103；不由 CPU affinity 推断内存页
驻留。每个 partition 模式各 3 组 O2/O3 配对，seed=20261015 决定组内先后顺序；
每次跑两后端，20 步 warmup + 100 步正式段，详细 profiler 关闭。

| 分区 | 后端 | O2 ms/step 中位数 | O3 ms/step 中位数 | 相对变化 |
| --- | --- | ---: | ---: | ---: |
| off | HostStaged | 15.7202 | 15.7426 | +0.14% |
| off | CudaAware | 16.4089 | 16.4358 | +0.16% |
| serial | HostStaged | 20.4775 | 20.4015 | -0.37% |
| serial | CudaAware | 21.2081 | 21.2842 | +0.36% |

时长为正式 run 的 seconds_max/100，含双方共有的段首 force；不包括进程启动、首次
staging/event 分配成本。每格仅三个样本，逐 trial 时间/离散范围保存在 cost-analysis；
变化与样本波动同量级，没有锁频/排他调度/正式置信区间，不作显著性或收益结论。
这里的比较仅记录 O3 生命周期新增等待/事件与 staging 调整的本机串行成本，不是 O5。

所有 trial 的 thermo.out SHA-256 均为
`3a7070599ccf516a6ff37e9b1d407e5decbca366d9cf95446106b5d99095406f`；
serial 均有真实 split，默认 off 与 backend 由实际日志核验。每个 trial 保存二进制/输入
哈希、launch_environment、C1 原始样本/绑定和 telemetry。两个二进制分别为上方锁定
O2 与交付 O3；不能以性能 trial 当时的 dirty patch 误认 O2 二进制来源。

## 精确命令

所有 GPU 命令在宿主执行，按以下顺序通过前门槛才继续；每个新 shell 重新 source。
输出路径重跑需换新目录，不能覆盖现存证据。

```bash
cd /data/home/zhangyifei/fdata/zhangyifei/newmd/newmd
source ../env/md-mpi.sh
cmake --build build -j 8
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1,2,3 --ranks 4
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
ctest --test-dir build --output-on-failure
python3 -u tests/mpi/run_halo_lifecycle.py --candidate ./build/dmg-md --devices 0,1,2,3 \
  --work-root dmgmd-overlap-o3-20261015/lifecycle-final
python3 -u tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --ranks 2,4 \
  --backends HostStaged,CudaAware --devices 0,1,2,3 --partition-modes off,serial,verify \
  --work-root dmgmd-overlap-o3-20261015/domain-final
python3 -u tests/long_nve/run_long_nve.py --candidate ./build/dmg-md --profile smoke \
  --devices 0,1,2,3 --partition-mode off \
  --report dmgmd-overlap-o3-20261015/long-nve-report.json --ui plain --keep-work
python3 dmgmd-overlap-o3-20261015/analyze.py
python3 dmgmd-overlap-o3-20261015/measure_cost.py
python3 dmgmd-overlap-o3-20261015/analyze.py
```

固定资源成本编排脚本先写预定 [cost-plan.json](../../dmgmd-overlap-o3-20261015/cost-plan.json)，
复用现有 benchmark/C1，详细命令及实际环境由每 trial metadata/launch_environment 保存。

## 未解决项与阶段边界

本轮没有删除 CudaAware 全局同步，没有证明 stream-aware 或异步网络后台进展；opaque CUDA event 内部内存仍 UNKNOWN。目标栈
改变须重跑自检与门槛。progress 保留可能阻塞的 MPI/CUDA 行为，O4 需要单独设计调度、
重新证明计算依赖/竞态并取得授权。本轮不移动 compute_domain/force ticket 到在途 halo 前，
默认分区 off，仍等待 halo 后计算。无实际 overlap、跨节点或 O5 收益声明。

O3 验收已完成，无剩余 O3 正确性阻塞项；nightly/release 长程矩阵与跨节点验收未新增执行，
不把 smoke 解释为其通过。下一阶段 O4 仍需单独授权，本轮到此停止。
