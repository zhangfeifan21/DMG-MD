# 局部域分解 halo 请求与缓冲区生命周期

普通步沿已有计划发送本地坐标，把收到的坐标解包到 ghost 槽位。通信接口（O3）把一次交换拆成发起和完成两部分。默认发起后立即等待完成。显式开启重叠调度（O4）后，程序先完成求力准备，再发起交换，并在等待前提交只读本地坐标的计算。

默认后端为 `HostStaged`，分区默认 `off`。通信在 MPI 初始化线程推进，两种调度沿用同一粒子布局和通信半径。

接口为 `MpiRuntime::begin_p2p_indexed_device_soa`、`progress_halo` 和 `finish_halo`。`exchange_p2p_indexed_device_soa` 封装了立即发起、完成的顺序。`refresh_ghost_positions` 默认也立即调用完成接口。两条路径都在解包实际写完后，才发布 `refreshed_epoch` 和本步的 halo 就绪凭证。

## 状态、所有权与复用

运行时用一个 `HaloSlot` 保存这次交换的缓冲区状态、四个 `MPI_Request` 和四个不计时的 CUDA 事件。调用者持有的 token 只有递增的交换版本（generation）。发送结束、接收结束、GPU 可以读取接收缓冲区和 ghost 写完分别记录。

| 状态字段 | 发布依据 | 后续允许动作 |
| --- | --- | --- |
| `pack_ready` | 默认 stream 的 pack event；begin 的 D2H event 等待或 CudaAware input device sync 已覆盖它 | 已打包数据稳定 |
| `send_source_ready` | HostStaged D2H event 完成；CudaAware input device sync 完成 | 发送源可交给 MPI |
| `sends_complete` | 发送组 Testall 成功且 flag=true，或 finish 的 Waitall 成功 | MPI 不再读取发送源；不代表接收完成 |
| `receives_complete` | 接收组 Testall 成功且 flag=true，或 finish 的 Waitall 成功 | 可发起 H2D/应用目标 MPI 的接收可见性合同 |
| `receive_gpu_ready` | HostStaged H2D event 完成，或 CudaAware receive completion 后原有 output device sync 完成 | GPU 可消费接收打包数据；不代表发送源已释放 |
| `unpack_complete` | 默认 stream 的 unpack-complete event 完成（零接收也记录事件） | ghost SoA 的写入物理完成 |
| `reusable` | 两组 MPI 完成、四个 request 均为 NULL、unpack event 完成 | 解锁整个槽位，可 reserve/下一次 begin/销毁 |

`begin` 在初始化线程打包发送坐标，等发送缓冲区就绪，再按原顺序发出两次 `Isend` 和两次 `Irecv`。调用会等待发送准备，返回的 token 表示交换尚未结束。

`progress` 在同一线程分别用 `Testall` 检查发送组和接收组。接收组先结束时，可以先提交 H2D 和解包。它同时查询 CUDA 事件，所有操作完成后只记账、解锁一次。`CudaAware` 保留接收后的设备同步，MPI 自身也可能阻塞。

`finish` 对尚未确认的请求执行一次四请求 `Waitall`，再等解包事件完成。对同一个 generation 重复调用 `finish` 或 `progress` 保持幂等。开始下一次交换后，旧 token 失效。`halo_state` 只读取已经观察到的状态。

四个专用点对点缓冲区分别用于设备和固定页主机内存的收发。程序在 `begin` 首次使用 CUDA 或 MPI 前扩容，并持有使用锁（lease）直至槽位可复用。锁定期间拒绝调用 `reserve`，包括已经有足够容量的情况。重建期的主机边界交换共用方向 tag，在普通步交换尚未结束时也须拒绝。其他集合通信有各自的传输缓冲区。O4 只在发出请求后提交独立计算，集合通信次数保持不变。

调用者须保持位置、设备发送索引及接收槽位数组、`CommunicationVolume` 和可选 `P2pTimingEvents` 的地址与内容有效，直到 `finish` 返回。即使打包已经完成，也要保留输入和映射，期间不能 resize、释放或覆盖。运行时私有缓冲区由运行时自行管理。

O4 同时冻结求力掩码、Verlet 表、类型、gid、工作区和盒。本地位置保持只读。打包只读位置，解包只写边界副本，第一批计算只读负责原子。当前每次刷新完成后才开始下一次刷新、重建或运行段，因此只用一个槽位和一份收发载荷。

## 后端依赖依据与同步变化

所有 pack/copy/unpack 仍在原默认 stream，没有新 stream 或跨 stream 假设。

| O2 同步/动作 | O3 动作 | 依赖依据 |
| --- | --- | --- |
| HostStaged blocking D2H | pinned cudaMemcpyAsync + send-source event + host EventSynchronize | 同 stream pack→D2H→event；等待后才 Isend，MPI 不会读未完成的 pinned 数据 |
| HostStaged blocking H2D 后 unpack | pinned cudaMemcpyAsync→GPU-ready event→unpack | 接收 request 完成后才上传；同 stream 保证 unpack 不早读，unpack event 完成也证明 H2D 完成 |
| CudaAware input DeviceSynchronize | 保留 | MPI 没有应用 stream 参数，不假设它等待 pack stream |
| CudaAware output DeviceSynchronize | 保留，位于 receive request 完成后 | 保持 O2 目标栈可见性围栏；不把 CUDA event 当作 MPI 完成事件 |
| O2 仅 enqueue unpack 后返回 | finish 等 unpack-complete event | 加强为物理完成，释放缓冲区前无 GPU 消费者；新增 host 等待成本单独实测 |
| MPI 四请求 Waitall | begin 发出、finish Waitall；显式 progress 才 Testall | 保留立即 finish 的原 MPI 调用数量和顺序，Testall 不在默认普通步额外执行 |

目标部署合同是 Open MPI + UCX。MPI completion 的 request 释放语义见
[Open MPI 5 Wait](https://docs.open-mpi.org/en/v5.0.x/man-openmpi/man3/MPI_Wait.3.html)；
GPU pointer 支持范围见 [Open MPI 5 CUDA](https://docs.open-mpi.org/en/v5.0.x/tuning-apps/networking/cuda.html)。
[Open MPI 开发者关于应用 stream 的说明](https://github.com/open-mpi/ompi/issues/7733)
没有提供应用 stream 自动排序保证，故本轮不删全局同步。作为同版本上游实现核对（不等于已安装二进制的逐指令证明），
[UCX v1.22.0 cuda/base/cuda_iface.c](https://github.com/openucx/ucx/blob/v1.22.0/src/uct/cuda/base/cuda_iface.c)
的 `uct_cuda_base_progress_event_queue` 在 CUDA event 查询完成后才调用 transport completion；
[cuda_copy](https://github.com/openucx/ucx/blob/v1.22.0/src/uct/cuda/cuda_copy/cuda_copy_iface.c) /
[cuda_ipc](https://github.com/openucx/ucx/blob/v1.22.0/src/uct/cuda/cuda_ipc/cuda_ipc_iface.c) 使用该 CUDA base。此源码证据不代表所有 transport 都后台进展，也不
替代实际安装栈的自检。仍要求 capability query、四类 device collective 与实际双方向
设备缓冲 p2p 数值自检；数值失败仍回退 HostStaged。每次环境变更先执行 GPU 门槛。

## 边界、退出与失败

P=1 时返回已完成的空 token，通信计数不增加，也不创建生命周期事件。P>1 且 count 为零时，仍发出四个合法的零字节请求。内部缓冲区最少分配一个 double，跳过零维内核启动和空指针偏移。

左右方向的 tag 分别为 1101 和 1102，接收使用对应的对侧方向。P=2 的两个面可以发给同一 rank。多个运行段共享运行时，只有前次操作实际完成才能复用或扩容，generation 跨段连续。

程序申请 `MPI_THREAD_FUNNELED`，在初始化线程执行全部 MPI 调用。MPI 由外部初始化时，先检查调用者是否为 main thread，并检查线程等级。正常退出先调用 `finish`，再销毁事件和通信器，最后结束 MPI。运行时析构时如果槽位仍未完成，会输出诊断并调用 `MPI_Abort`。

非法 token、重入 begin、在途 host face 交换、分配/CUDA/MPI 异常均输出本地
`DMGMD_ERROR category=halo` 并 MPI_Abort，不发诊断 collective、不等待仅本地成功发出的
部分请求、不释放在途 buffer。MPI_ERR_IN_STATUS/PENDING 同样终止作业；不承诺 MPI 错误后恢复。
重建期同步 host exchange 和启动 p2p 自检也在部分 post/Waitall 错误时先 abort，禁止局部
请求数组出作用域后继续运行。自检正常完成但数值不符的后端回退合同不变。
线程检查在任何 halo MPI 调用前拒绝非初始化线程；调用者不得在工作线程调用 MPI API。

## 字节、内存与测试

两后端 halo payload、左右顺序、MPI_BYTE count、方向 tag 与四个请求数量均不变。
每 face 每原子每 component 为 8 B；位置仍 24 B/原子。HostStaged 的本地 D2H/H2D
仍分别等于本地 send/recv payload；CudaAware 无 host staging。既有全局 collective
staging 字段不计入 p2p staging，不能把字段未增加解释为无拷贝。完成记账恰一次。
无额外 payload 内存；仍为 device send+recv 高水位，HostStaged 另有 pinned send+recv。
新增固定状态、四 request 持久存储和四个 event handle（CUDA 内部 event 分配大小未知），
与原子数无关。原验收的 x86-64 ABI 中，Impl 对象从 472 B 增至 736 B（+264 B）；
该数字不含 CUDA opaque event 内部存储，重建到其他 ABI 不承诺相同 padding。
生命周期事件是必要依赖；详细 profiler 关闭仍创建，不属于计时 event。

旧 timing schema 字段名保留；HostStaged `h2d_begin/end` 从 blocking copy 耗时改为 H2D
**enqueue** 区间，物理就绪由事件证明。`halo_transfer_wait` 同样止于 unpack enqueue 前，
现在不含末端 unpack-complete 等待；该等待仍在整体 halo host 括号/step 墙钟内。
D2H 括号含发送源 event 等待，MPI wait/post 语义保持。不得把新旧 transfer-wait 直接
解释为纯传输加速；设备 unpack event 仅测 kernel，末端 host 等待没有新增诊断字段。

独立入口 `tests/mpi/run_halo_lifecycle.py` 在四 GPU 门槛后检查状态、方向 tag、零 count、
增长/复用、重复 finish、progress-only、两种完成顺序和 rank0 单边故障；故障注入通过
独立测试二进制的链接 wrapper，生产没有故障开关。完整 O2 domain 三模式矩阵继续保留，
同时比对已锁定 O2 输出；验收命令/结果/串行成本见
[O3 实测](../status/validation/communication-computation-overlap.md#halo-lifecycle-20261015)。

## O4 消费者与诊断

首选同 stream 调度：prepare→begin（输入同步已完成）→clear/early→finish→late。
`wait` 不显式 Testall；`poll` 在 early enqueue 后最多 8 次 progress，全部完成或收发均完成
则提前结束，之后 finish。接收完成可排 H2D/unpack，但默认 stream 保证它们在 early 后；
CudaAware output sync 保留。lease、单槽位、四个请求、方向 tag 与完成记账规则完全相同。
异常仍整作业 abort，不在某 rank 自行退回 full 或释放在途资源。

可选 `HaloTrace*` 只借用调用者存储，生命周期与 volume/timing 相同。记录 pack/source
ready 的 host 观察、post 前后、send/recv 各自首次观察和 unpack 完成观察；均使用
CLOCK_MONOTONIC。trace 关闭不读时钟。观察时间是完成的上界，不是 NIC 完成瞬间。
独立 wrapper 延迟 post/完成用于状态验收，不能作为网络实际传输证据。O4 验收与 profiler
重算入口见 [O4 报告](../status/validation/communication-computation-overlap.md#overlap-o4-20261016)。
