# 数据结构与内存布局

类别：现行标准。核对日期：2026-10-16。

## 计数、身份与权限

原子的全局 ID 在运行期间保持不变。它在某张 GPU 数组中的位置可以随迁移和重建而改变。负责积分、归约热力学量和提供输出记录的 rank 同一时刻只有一个。

`include/dmgmd/model.hpp` 的 `AtomCounts` 保存 `global_count`、`owned_count` 和 `ghost_count`，其中 `local_count()=owned_count+ghost_count`。在局部域分解模式下，`global_count` 只记录体系大小，设备数组按 `local_count` 寻址。

| 域 | 全量复制模式（replicated-full） | 局部域分解路径 |
| --- | --- | --- |
| 主机读取结果 `HostAtoms` | 每 rank 读取全部 N 个原子，`owned=N`、`ghost=0` | 初始化从完整输入选取负责原子，仍保留完整的主机身份数据 |
| 设备槽位数 `local_count` | N | 负责原子数 + 依赖副本数 + 仅坐标副本数 |
| 负责原子的槽位 | `SpatialOwnership` 选取槽位，按 gid 升序 | `LocalLayout` 的 `[0,owned)` 前缀，按 gid 升序 |
| 边界副本 | 无。非负责槽位仍参与全量复制的临时计算 | 两个后缀各按 `(source face, source rank, gid)` 排序，携带周期镜像偏移 |
| NEP 中间量和最终力 | 所有 N 个槽位参与计算，只有负责槽位提供最终结果 | `[0,owned+dep)` 计算描述符和部分力，`[0,owned)` 计算最终力。`owned=0` 时中间量的中心集合也为空 |

`global_id` 为 64 位输入行号。全量复制模式中，读取器在每个 rank 上记录 `owned=N`，运行时根据 `SpatialOwnership` 另选负责贡献物理量的槽位。

局部域分解模式将负责原子（owned atom）放在前段，边界副本（ghost atom）放在后段。依赖副本（dependency ghost）向本地原子提供描述符导数等中间量，仅坐标副本（coordinate-only ghost）只作为邻居候选。两类副本都不积分、不进入热力学归约，也不直接输出。一个缓存周期内，管理归属（manager owner，`manager_owner`）继续负责积分，即使按当前位置计算的几何归属（geometric owner，`geometric_owner`）已经改变。[缓存与迁移](cache-validity.md)

`HostAtoms` 在主机上仍为每 rank O(N)，整个作业为 O(NP)。根 rank 输出还要收集 N 个原子的快照。局部设备数组的大小只说明 GPU 上这部分存储的用量。

## Host 模型与 Box

Model 包含 BoxData 与 HostAtoms。reader 数组在两路径初始化时都为全局长度 N。

| 字段 | 类型 / 逻辑尺寸 | 语义 |
| --- | --- | --- |
| global_id | uint64 / local | 稳定身份 |
| species、type | string、int / local | 元素及 potential header 类型索引 |
| mass、charge | double、float / local | amu；缺省质量来自 GPUMD 元素表、charge=0 |
| position、velocity | double / 3local | Å；速度输入 Å/fs 乘 10.18051 为内部单位 |
| group_labels | int / methods×local | 原子持久 labels，contents 可重建 |
| has_input_velocity | bool | model 有 vel 时 velocity 命令不重新初始化 |

`HostAtoms::validate` 检查计数是否溢出，以及 `owned≤global`、标量长度为 local、向量长度为 3local 和每组标签长度是否成立。运行时另检查每个原子是否恰由一个 rank 负责。

BoxData 保存 periodic[3] 与 h[9]：

```text
h = [ ax bx cx
      ay by cy
      az bz cz ]
```

XYZ 的 `Lattice` 依次写 a、b 和 c 向量。解析时转置到 `h`。运行时用兼容层 `Box` 求逆矩阵、体积和最小镜像（MIC）。多 rank 支持正交全周期盒，单 rank 的支持条件另见 MPI 标准。[盒资格](mpi-runtime.md)

## SoA / ELL 寻址

令 L 为逻辑 local_count，所有 kernel 显式使用 L，不能用 owned/interior 数量或 capacity：

```text
position/velocity/force[axis*L + slot]             # 3 分量
virial[component*L + slot]                       # 9 分量
Fp[descriptor_component*L + slot]
NN[center], NL[neighbor_ordinal*L + center]       # ELL 存 local slot
f12{x,y,z}[angular_ordinal*L + center]
```

内部 virial 顺序为 `xx,yy,zz,xy,xz,yz,yx,zx,zy`。XYZ/thermo 的映射与单位由
[兼容矩阵](compatibility-matrix.md)维护。domain 邻居按 candidate gid 排序但存 local slot；
反边按 gid 查找本步 filtered angular ordinal，不能持久化其位置。

## Device 所有者与 buffer

全量复制模式用 `src/runtime.cu` 中的 `DeviceAtoms` 保存设备原子。局部域分解模式用 `src/domain_runtime.cu` 中的 `DomainDeviceAtoms` 和 `DomainState`。MPI 的传输缓冲区由 `src/mpi_runtime.cu` 管理。

| buffer | 逻辑尺寸 | 局部域分解有效域 |
| --- | --- | --- |
| gid/type | L | 全 local 身份/typewise filter |
| mass/charge | L | owned 权威；ghost 不积分/thermo/输出 |
| position | 3L | owned 更新；ghost 在普通步按缓存 face plan 刷新，重建时更新映射 |
| velocity | 3L | owned 权威，修速经 root gather/scatter |
| force/PE/virial | 3L / L / 9L | force 前全 L 清零；owned 权威，ghost 仅 scratch |
| unwrapped/previous_position | 按需 3L | owned；启用时 unwrapped 随迁移载荷传递 |
| epoch_displacement | 3L | 在 owned wrap 前累计，rebuild 清零。与用户 unwrapped 输出独立 |
| affine_reference_position / affine_images | 每项 3L | 仅实验 `affine` 路径使用。参考 wrapped 坐标和周期 image 计数用于计算连续位移 |
| decision_max_squared/reasons | 各一标量 | 从 owned manager copy 得出统一决议 |
| NEP Fp/sums/partial/count/list | L stride | producer 域 `[0,owned+dep)`，candidate `[0,L)` |
| face send/recv index | face plan 长度 | send 指 owned、recv 指 ghost；与 mapping epoch 绑定 |

全量复制模式的 mass/type/position 为完整副本，VV/thermo 按 owned index list。普通步不再复制 velocity；
迁移/修速前先恢复必要复制态。NEP 写全 N scratch，只有 owned 部分用于物理输出。

表中是逻辑长度。L=0 时，部分 `GPU_Vector` 仍分配至少一个元素。局部接口显式接收 L=0，在启动内核之前跳过空中心，填充空间不参与计算。Atom、NEP 和 Neighbor 使用 `resize_reuse`。容量充足时只更新逻辑长度，扩容时留出 25% 余量。寻址跨度和默认拷贝、填充范围都按逻辑长度计算。

## 布局提交与输出

`LocalLayout`、边界成员、通信映射和邻居参考数据必须对应同一次重建及同一组原子。普通步只刷新副本坐标，统一重建时才迁移原子、更新成员、建立新映射并上传。工作区逻辑大小随 L 更新，超出容量时才分配。布局变动后，邻居表失效。程序重新获取数组视图，并清零全部逻辑输出。[固定盒缓存证明](cache-validity.md)

局部 NPT 实验还单独跟踪盒的版本。边界就绪状态同时绑定步号、布局版本（layout epoch）和盒版本（box generation）。[变盒后的刷新和重建](domain-npt.md)

局部域分解初始化时，使用共享设备内核首次折回坐标。展开坐标（unwrapped）从原始输入初始化。若改用主机代码计算折回，FMA 可能导致 1 ULP 的差异。

XYZ 和 restart 只汇集负责原子前缀及其 gid，根 rank 恢复输入顺序。边界副本不参与热力学量计算、控温或直接输出。restart 启动的新进程重新初始化布局和缓存周期。

## O2/O3 附加状态

分区保持原 slot/grid/block/L stride；三张 L 长 int mask 加三个 int scratch，逻辑额外
空间 12L+12 B，高水位 capacity 可保留。只在启用时分配，生命周期见[分区合同](domain-partition.md)。

halo 为单在途槽位。device/pinned staging lease 内禁止 reserve/覆盖/释放，直到发送、接收、
物理 unpack 都完成；无第二份 payload。详见[halo 生命周期](halo-lifecycle.md)。
