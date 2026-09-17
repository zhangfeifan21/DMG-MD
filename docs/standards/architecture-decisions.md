# DMG-MD 架构决策

类别：现行标准。

更新日期：2026-09-09。参考 GPUMD commit：
`9d23496e41319b9e2af5221a7df6285387401d1e`。

## D-001：NEP 采用仓库内复现（2026-09-09 修订）

决定：DMG-MD 需要的 GPUMD 最小子集（tokenizer、Box、GPU_Vector、neighbor、Potential、
NEP loader 和 CUDA kernels）在 `src/gpumd_compat/` 内复现，统一放在
`namespace gpumd_compat`，文件头记录 Origin file 与裁剪说明。构建与运行不编译、不链接、
不 `#include` `../gpumd-reference`；该 checkout 只用于只读审计与 golden 基线生成。

原因：这是保持混合精度、邻居排序、small/large-box 分支和浮点累加顺序最可靠的方式，
同时消除对 sibling checkout 的构建期耦合。复现代码与参考 commit 逐符号对应，裁剪只删除
DMG-MD 不可达的路径（DFTD3、temperature/active-learning 重载、ILP/SW 邻居变体等），并在
删除处加 `NOTE(dmg-md)` 注释；数值修改必须连同 golden 基线一起重新验证。GPUMD 源码为
GPLv3，复制进本仓库后的发布/分发策略仍须遵守其许可证。

历史：2026-09-03 的旧版决策是 CMake 直接编译只读 GPUMD checkout 中的源文件
（`GPUMD_SOURCE_DIR` + commit 校验）。该模式已于 2026-09-09 由本决策修订并废除。

## D-002：全局元数据与本地寻址从 single-rank 起分离

决定：Atom 同时保存 `global_count`、`owned_count`、`ghost_count` 和 stable `global_id`；
`local_count=owned+ghost` 是所有 SoA 的 stride。replicated runtime 的 reader 模型仍为
owned=global、ghost=0；真正的 rank-local 积分/thermo/output 权限由独立的
`SpatialOwnership`（M0 为连续 `OwnedRange`，M1 起为空间 slab 的槽位子集）表示。

原因：不能把当前数值相等编码成“数组长度就是全局 N”。将来引入 ghost 时，现有 VV 和
thermo kernel 无需改变所有权边界；NEP adapter 则必须被有证据的 distributed orchestration
替换。

## D-003：run.in 解析与执行解耦

决定：先把整个文件解析成 `RunProgram`/typed command variants，每个节点保留文件、行号和
原始文本；只有完整解析成功后才读取 model、初始化 CUDA 并执行。

原因：unsupported/unknown 命令必须在产生 MD 输出之前失败；typed IR 也为以后 rank 0 parse
和广播规范化配置提供稳定边界。没有新增任何 MPI 专用 run.in token。

## D-004：replicated NEP adapter 对 ghost fail closed

决定：runtime 入口拒绝 `ghost_count != 0`。GPUMD Potential 中心域保持 `[0,global_count)`；
MPI authoritative output 由独立的所有权对象限定（M1 的 `SpatialOwnership`）。

原因：GPUMD large-box 的 `Fp` 和 directed partial 有两层依赖，small-box 又使用 Newton
atomic scatter。尚未实现 exchange 协议前，简单设置 `N1/N2` 或拼接 ghost 会产生漏力、
重复力或重复能量。硬错误比隐式“看起来能跑”安全。

## D-005：observable 和 I/O 只承认 owner

决定：thermo kernel 只归约 owned；XYZ/restart 只收集 owned record，并以 `global_id` 排序。
GPUMD 的 virial 分量顺序、thermo Voigt 映射、单位和 formatter 精度保持不变。

原因：这使 single-rank 输出与 GPUMD 兼容，同时固定未来 rank-0 gather 的所有权合同；ghost
永远不会直接参与 thermo 或输出。

## D-006：支持表采用显式白名单

决定：当前支持普通 `nep4/nep5` 与对应 ZBL、`potential`、`velocity`、`time_step`、
`ensemble nve/nvt_ber`、`correct_velocity`、`dump_thermo`、`dump_xyz`、`dump_restart` 和
`run`。其他已知命令/ensemble subtype 与未知命令都报带输入行的 unsupported。

原因：GPUMD 某些命令由二次扫描处理，原 dispatcher 甚至会静默跳过 `kspace/dftd3`；新
runtime 不允许复制这种静默行为。GPUMD 的多 GPU potential partition 参数在 single-rank
runtime 中也明确拒绝。

## D-007：删除历史重构代码

决定：删除旧的 `include/newmd` Atom/DeviceBuffer、正交 Box/PBC、CPU CSR neighbor 和仅查询
GPU 的 `newmd` executable。

原因：这些代码既不参与新的兼容 runtime，也缺少 global/owned/ghost/global-ID 语义；保留会
形成第二套 Box、邻居和 Atom 模型，与直接复用 GPUMD 核心的方向冲突。

## D-008：MPI 栈固定为 Open MPI+UCX

决定：构建、运行和测试统一 source 仓库同级 `env/md-mpi.sh`，只支持其中的 Open MPI+UCX。
核心允许使用 `mpi-ext.h`/`MPIX_Query_cuda_support()`，不再维护 MPICH/MVAPICH 兼容。默认
HostStaged；CudaAware 必须同时通过 Open MPI capability query 和覆盖
`MPI_Allreduce(MPI_IN_PLACE)`、`MPI_Allgatherv`、`MPI_Gatherv`、`MPI_Bcast` 的主动数值自检。

原因：固定并预检实际部署栈可以先排除 executable/libmpi/UCX 混装、缺少 CUDA transport、
错误 component path 和 HCOLL 抢占等环境故障，避免误判为 NEP 数值错误。HostStaged 仍保留为
默认正确性路径，但“可替换任意 MPI 实现”不再是产品目标。

## D-009：replicated-full NEP scratch，owned output 唯一

决定：replicated runtime 分片积分、thermo 和输出所有权（M1 起为空间 slab 槽位集合），
但每 rank 暂时执行完整 ordinary NEP scratch。不能直接把 `NEP::N1/N2` 设为 owned 集合。

原因：锁定 NEP force 读取远端中心 `Fp` 和反向 directed partial；没有 phase-level exchange
时，直接中心分片不完整。启动 coverage collective 证明 owned 槽位恰好覆盖一次，并明确
记录 NEP kernel 仍为 replicated-full。见 [replicated-mpi.md](./replicated-mpi.md)。
