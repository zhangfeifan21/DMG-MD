# M2a halo 请求与缓冲区生命周期

类别：现行通信接口合同。O3 保持原调用位置 begin 后立即 finish；无通信/NEP overlap，
无进度线程，默认 HostStaged、分区默认 off、粒子布局与通信半径均不变。
实现入口是 `MpiRuntime::begin_p2p_indexed_device_soa`、`progress_halo`、`finish_halo`；
旧 `exchange_p2p_indexed_device_soa` 是 begin/finish 包装。`refresh_ghost_positions`
显式立即 finish，之后才发布 refreshed epoch/本步 halo-ready token。

## 状态、所有权与复用

runtime 持有唯一 HaloSlot、四个 MPI_Request 和四个禁用计时的 CUDA event；token 只有
递增 generation，不持有资源。以下状态是独立事实，不用单个“通信完成”替代：

| 状态字段 | 发布依据 | 后续允许动作 |
| --- | --- | --- |
| `pack_ready` | 默认 stream 的 pack event；begin 的 D2H event 等待或 CudaAware input device sync 已覆盖它 | 已打包数据稳定 |
| `send_source_ready` | HostStaged D2H event 完成；CudaAware input device sync 完成 | 发送源可交给 MPI |
| `sends_complete` | 发送组 Testall 成功且 flag=true，或 finish 的 Waitall 成功 | MPI 不再读取发送源；不代表接收完成 |
| `receives_complete` | 接收组 Testall 成功且 flag=true，或 finish 的 Waitall 成功 | 可发起 H2D/应用目标 MPI 的接收可见性合同 |
| `receive_gpu_ready` | HostStaged H2D event 完成，或 CudaAware receive completion 后原有 output device sync 完成 | GPU 可消费接收打包数据；不代表发送源已释放 |
| `unpack_complete` | 默认 stream 的 unpack-complete event 完成（零接收也记录事件） | ghost SoA 的写入物理完成 |
| `reusable` | 两组 MPI 完成、四个 request 均为 NULL、unpack event 完成 | 解锁整个槽位，可 reserve/下一次 begin/销毁 |

begin 在初始化线程完成 pack/发送源就绪等待，然后按原顺序发出两次 Isend、两次 Irecv，
返回未完成 token；因此 begin 本身包含发送准备的阻塞。progress 在同一线程分别 Testall
两组，接收完成即可排 H2D/unpack，不等发送完成；查询事件，全部完成时恰一次记账/解锁。
progress 不保证整次调用无阻塞：CudaAware 保留 output device sync，MPI 自身也可能阻塞。
finish 对尚未确认的请求做一次四请求 Waitall，再等待 unpack event，保证物理完成。
同 generation 的重复 finish/progress 幂等；下一 begin 使旧 token 失效。`halo_state`
只读已观察状态，不隐式推进。

缓冲区 capacity 只能在 begin 的任何 CUDA/MPI 使用之前增长。四个专用 p2p device/pinned
buffer 带 lease；即使 reserve 未超过 capacity，在 lease 内也拒绝调用。lease 直到全部
可复用条件满足才解除；重建期 host face exchange 同时使用方向 tag，因此在槽位活动时也
拒绝。其他 collective 使用独立 staging；O3 调用点没有插入额外 collective 或计算。

调用者必须保持 position、send-index/recv-slot device 数组、CommunicationVolume 和可选
P2pTimingEvents 的地址/内容有效直到 finish；不得 resize/free/覆盖。begin 已完成 pack，
但此合同仍保守锁定输入/映射直到 finish。runtime 的私有 staging 不向调用者暴露。
当前调度一次 refresh 完成后才有下一次 refresh/rebuild/run，单槽位足够；没有跨步在途
复用要求，因此没有双缓冲和第二份 payload 内存。

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

P=1 返回完成的空 token，不增加通信计数、不创建生命周期事件。P>1 零 count 仍发出四个
合法零字节请求，内部 buffer 至少分配一个 double；不会启动零维 kernel 或对空指针做偏移。
左右方向 tag 分别 1101/1102，接收匹配对侧方向，P=2 两面同 peer 也不混淆。
多 run 使用同一 runtime，前次物理完成后才复用/增长容量，generation 不在段界重置。

所有 MPI 调用仍在初始化线程，申请 MPI_THREAD_FUNNELED；halo API 检查线程身份。
外部已初始化 MPI 时检查 main-thread 与线程等级；未引入线程。正常退出先 finish，
然后销毁事件、通信器和 MPI；runtime 析构看到未完成槽位直接诊断并 MPI_Abort。

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
与原子数无关。本次 x86-64 ABI 的 Impl 对象从 472 B 增至 736 B（+264 B）；
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
[O3 实测](../status/halo-lifecycle-20261015.md)。
