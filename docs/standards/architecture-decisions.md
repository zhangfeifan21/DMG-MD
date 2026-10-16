# 架构决策

类别：现行标准。核对日期：2026-10-16。

## D-001：仓库内复现数值核心

GPUMD 锁定 `9d23496e41319b9e2af5221a7df6285387401d1e`。所需 tokenizer、Box、
GPU_Vector、Neighbor、Potential、NEP loader/kernels 在 `src/gpumd_compat/` 内复现，
命名空间为 gpumd_compat，头部记录 Origin 与裁剪说明。不编译、链接、include 或在 runtime
调用 sibling reference；它只作只读审计/golden 源。NEP 数学、精度、累加和 launch 修改须
连同 baseline/long_nve 验证，方向按 AGENTS 确认；分发遵守来源 GPLv3 许可证。

原因：保留参考数值语义，同时消除构建对外部 checkout 的耦合。旧直接构建 reference 模式
已于 2026-09-09 废除，历史由 Git 保存。

## D-002：身份、计数和权限分离

稳定 global_id 源自输入行号；global_count 只作元数据，local_count 是 SoA/ELL stride，
capacity 仅为物理存储。M1 reader owned_count=N，不代表 MPI 权威 owner；后者由 slab
槽位集合决定。M2a 以 local owned 前缀与 ghost 后缀表示权限，manager 可在 epoch 内暂时
不同于 geometric owner。详见[布局](data-layout.md)与[缓存](cache-validity.md)。

## D-003：先解析再执行

每 rank 完整解析 run.in 为带来源位置的 typed IR，再读取 model、初始化 GPU 并执行。
run/model/potential fingerprint 检查一致；当前不是 root-only parse/broadcast。
未支持或未知命令在科学输出前报错，不增加 MPI 专用 run.in token。

## D-004 / D-009：M1 保留完整 NEP scratch

M1 入口要求完整无 ghost 输入，每 rank 对全部 N 中心计算 ordinary NEP，只承认 owned
积分/thermo/输出。仅设置 NEP N1/N2 为 owned 会缺邻居 Fp/反向 partial，不能据覆盖证明
推导其数值完整。M2a 用深位置 halo 重算依赖中心，独立提供 ghost-aware domain 入口。

## D-005：唯一物理贡献和兼容 I/O

积分、thermo、控温只用 owned。XYZ/restart gather 按 gid 恢复输入顺序，科学文件仅 root 写；
ghost PE/virial 为 scratch。单位、virial 顺序、文件格式/触发步见[兼容矩阵](compatibility-matrix.md)。
restart 只承诺保存字段及跨 rank 重建，不承诺未序列化 time/RNG/thermostat 的连续性。

## D-006：显式支持白名单

只支持普通 NEP4/5、NEP-ZBL、NVE/nvt_ber 和支持矩阵列出的输入/输出命令。未知与已知
未支持项均明确 unsupported；多 potential 与 potential 方向参数拒绝，不能静默忽略。

## D-007：单一数据与几何模型

旧 include/newmd、CPU CSR neighbor 和查询 GPU 的旧 executable 已删除，不保留第二套
Atom/Box/neighbor。新增分布式编排围绕现有 compat kernel 做有证据的最小参数化。

## D-008：固定 Open MPI + UCX

原生每个 shell source `../env/md-mpi.sh`；Docker entrypoint 加载内置脚本。CMake 与预检
拒绝混栈，排除 HCOLL。HostStaged 默认；CudaAware 经 capability query、四类 device
collective 和 p2p 数值自检才启用，数值自检不符回退。每 rank 一 GPU，以 UUID 去重；
不能把 NEP_MULTIGPU 的单进程多设备编排当 MPI 层。

## D-010：M1/M2a 模式分派

potential 参数仅加载一次、workspace 延迟分配。P1 恒 M1；P>1 满足正交全周期、large-box
和 slab_width≥d_coord 时 M2a，否则在现有支持范围内回退 M1。P>1 triclinic/非周期仍拒绝。
M2a 采用保守两跳位置 halo，dependency 域重算 Fp/partial，最终 force 只写 owned；
邻居行和反边按 gid，logical local_count 独立传入，空 rank 不访问 padding。

两跳深度由实际 typewise 消费路径证明，不能用一个 nominal cutoff 猜测。初始 wrap 用共享
device kernel，unwrapped 从 raw 输入播种；默认保留 kernel 算术和每中心顺序。
实际协议见[MPI runtime](mpi-runtime.md)，M2b 等剩余工作见[扩展计划](../plans/domain-decomposition.md)。

## D-011：通信按明确口径记账

collective 的全局 input/output buffer 字节与每 rank halo/migration/control 类别分开。
不将 MPI 实现 wire traffic、p2p staging 与既有 collective staging 混算。精确字段与
call0/1000 记录以 MPI 标准和测试脚本为准，实测通过声明只放 status。

## D-012：实验分区与生命周期不等于 overlap

O2 分区默认 off，普通步按完整 Verlet 图闭包分配 producer/最终中心；O3 单槽位和显式
request/event lease 保证物理完成。生产仍 begin 后立即 finish 再算 NEP，无 progress thread。
合同见[分区](domain-partition.md)和[halo](halo-lifecycle.md)；O4/O5 尚在计划。
