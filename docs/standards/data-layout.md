# 数据结构与内存布局

类别：现行标准。核对日期：2026-10-16。

## 计数、身份与权限

`include/dmgmd/model.hpp` 的 AtomCounts 保存 global_count、owned_count、ghost_count，
`local_count()=owned_count+ghost_count`。global_count 是元数据，不作为 M2a device stride。

| 域 | M1 replicated-full | M2a local-domain |
| --- | --- | --- |
| reader HostAtoms | 每 rank 完整 N；owned=N、ghost=0 | bootstrap 从完整输入挑 owned；完整 host identity 仍保留 |
| device local_count | N | owned + dependency ghosts + coordinate-only ghosts |
| 权威 owned | SpatialOwnership 的任意槽位集合，按 gid 升序 | LocalLayout 的 `[0,owned)` 前缀，按 gid 升序 |
| ghost | 无；non-owned 仍为 replicated scratch | 两个后缀按 `(source face, source rank, gid)` 排序，携带 image shift 元数据 |
| NEP producer / final | 全 N scratch / owned 槽位权威 | `[0,owned+dep)` descriptor/partial，`[0,owned)` final force；owned=0 时 producer 为空 |

global_id 为 64-bit 输入行号，跨迁移稳定；local slot 不是身份。M1 的 reader owned=N
不能误当每 rank 都拥有 N 份物理贡献。M2a manager owner 在 cache epoch 内固定，可能
暂时不同于当前位置的 geometric owner；只在统一 rebuild 提交迁移。

HostAtoms 当前为 O(N)/rank，整个作业为 O(NP)；root 输出还需 N-sized snapshot，不能据
local device 布局宣称端到端内存 scaling。

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

HostAtoms::validate 检查计数溢出、owned≤global、各标量长度=local、向量长度=3local 和
每组 label 长度；它不证明 MPI owned 覆盖，后者由 runtime 校验。

BoxData 保存 periodic[3] 与 h[9]：

```text
h = [ ax bx cx
      ay by cy
      az bz cz ]
```

XYZ Lattice 按 a/b/c 向量排列，parser 转置进 h；runtime 使用 compat Box 求逆、体积和 MIC。
P1 支持范围与 P>1 正交全周期约束见[MPI](mpi-runtime.md)，不由矩阵布局推导分布式支持。

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

M1 的 DeviceAtoms 位于 `src/runtime.cu`，M2a 的 DomainDeviceAtoms/DomainState 位于
`src/domain_runtime.cu`；MPI staging 由 `src/mpi_runtime.cu` 管理。

| buffer | 逻辑尺寸 | M2a 有效域 |
| --- | --- | --- |
| gid/type | L | 全 local 身份/typewise filter |
| mass/charge | L | owned 权威；ghost 不积分/thermo/输出 |
| position | 3L | owned 更新；ghost 每步按固定 face plan 刷新 |
| velocity | 3L | owned 权威，修速经 root gather/scatter |
| force/PE/virial | 3L / L / 9L | force 前全 L 清零；owned 权威，ghost 仅 scratch |
| unwrapped/previous_position | 按需 3L | owned；启用时 unwrapped 随迁移载荷传递 |
| epoch_displacement | 3L | owned wrap 前累计，rebuild 清零；与用户 unwrapped 输出无关 |
| decision_max_squared/reasons | 各一标量 | 从 owned manager copy 得出统一决议 |
| NEP Fp/sums/partial/count/list | L stride | producer 域 `[0,owned+dep)`，candidate `[0,L)` |
| face send/recv index | face plan 长度 | send 指 owned、recv 指 ghost；与 mapping epoch 绑定 |

M1 的 mass/type/position 为完整副本，VV/thermo 按 owned index list。普通步不再复制 velocity；
迁移/修速前先恢复必要复制态。NEP 写全 N scratch，只有 owned 部分用于物理输出。

表中为逻辑 size。L=0 时部分 GPU_Vector capacity 至少为 1，domain API 仍显式接收 L=0，
空中心 launch 前短路，不访问 padding。Atom/NEP/Neighbor 使用 resize_reuse，容量足够只改
逻辑尺寸，扩容留 25% 余量；capacity 不作 stride。默认长度 copy/fill 以逻辑 size 为准。

## 布局提交与输出

LocalLayout、membership、face map、neighbor reference 的 epoch/身份必须一致。普通步不改
槽位/分类/map；rebuild 才统一迁移、重建并上传。workspace 逻辑 size 随 L 更新，超 capacity
才分配；布局变化强制 neighbor 失效、重新取得 view，清零全部逻辑输出。
缓存证明见[cache-validity](cache-validity.md)。

M2a bootstrap 的首次 wrap 使用共享 device kernel，unwrapped 从 raw 输入播种；host 重算
wrap 可能因 FMA 差 1 ULP。XYZ/restart 只 gather owned prefix + gid，root 恢复输入顺序；
ghost 不参与 thermo、控温或直接输出。restart 新进程重新 bootstrap，不复用 epoch。

## O2/O3 附加状态

分区保持原 slot/grid/block/L stride；三张 L 长 int mask 加三个 int scratch，逻辑额外
空间 12L+12 B，高水位 capacity 可保留。只在启用时分配，生命周期见[分区合同](domain-partition.md)。

halo 为单在途槽位。device/pinned staging lease 内禁止 reserve/覆盖/释放，直到发送、接收、
物理 unpack 都完成；无第二份 payload。详见[halo 生命周期](halo-lifecycle.md)。
