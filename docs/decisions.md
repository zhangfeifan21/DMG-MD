# DMG-MD 架构决策

更新日期：2026-09-02。参考 GPUMD commit：
`9d23496e41319b9e2af5221a7df6285387401d1e`。

## D-001：NEP 采用锁定源码树的源级共享

决定：CMake 直接编译只读 GPUMD 中的 tokenizer、Box、neighbor、Potential、NEP loader 和
CUDA kernels。newmd 不复制 NEP 参数、descriptor、force 或 ZBL 数学。

原因：这是保持混合精度、邻居排序、small/large-box 分支和浮点累加顺序最可靠的方式，且
避免两个 NEP 实现独立演化。构建时强制检查 reference commit；代价是当前 checkout 必须能
找到 `GPUMD_SOURCE_DIR`。GPUMD 源码为 GPLv3，发布/分发策略仍须遵守其许可证。

## D-002：全局元数据与本地寻址从 single-rank 起分离

决定：Atom 同时保存 `global_count`、`owned_count`、`ghost_count` 和 stable `global_id`；
`local_count=owned+ghost` 是所有 SoA 的 stride，`owned_count` 是积分、thermo、PE/virial 和
输出的域。single-rank 只是在入口验证 owned=global、ghost=0。

原因：不能把当前数值相等编码成“数组长度就是全局 N”。将来引入 ghost 时，现有 VV 和
thermo kernel 无需改变所有权边界；NEP adapter 则必须被有证据的 distributed orchestration
替换。

## D-003：run.in 解析与执行解耦

决定：先把整个文件解析成 `RunProgram`/typed command variants，每个节点保留文件、行号和
原始文本；只有完整解析成功后才读取 model、初始化 CUDA 并执行。

原因：unsupported/unknown 命令必须在产生 MD 输出之前失败；typed IR 也为以后 rank 0 parse
和广播规范化配置提供稳定边界。没有新增任何 MPI 专用 run.in token。

## D-004：single-rank NEP adapter 对 ghost fail closed

决定：当前 `NepForce` 构造和 compute 都拒绝 `ghost_count != 0`，并显式设置 GPUMD Potential
的中心域 `[0, owned_count)`。

原因：GPUMD large-box 的 `Fp` 和 directed partial 有两层依赖，small-box 又使用 Newton
atomic scatter。尚未实现 exchange 协议前，简单把 ghost 拼到数组尾部会产生漏力、重复力或
重复能量。硬错误比隐式“看起来能跑”安全。

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

## D-008：多-rank 暂停在门外

决定：本阶段不添加 MPI 依赖、rank lifecycle、domain decomposition、halo、migration 或
multi-rank force。只有 single-rank golden 全部持续通过后，才按独立小切口推进。

原因：当前 differential 已把 parser、NEP、积分、thermo 和 I/O 作为 single-rank oracle
锁定；在此之前混入分区变量会显著扩大数值差异的定位空间。
