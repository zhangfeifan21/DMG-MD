# DMG-MD 数据结构与内存布局

类别：现行标准。

更新日期：2026-09-18。

本文档描述 single-rank、M1 空间所有权 replicated MPI 与 M2a rank-local
domain `dmg-md` 的数据面，并以现有代码为权威来源。运行时在两种数据面之间
按 `DMGMD_DOMAIN` eligibility 记录分派（见
[replicated-mpi.md](./replicated-mpi.md)）。

## 1. 数据域约定

原子数量由 `include/dmgmd/model.hpp` 中的 `AtomCounts` 显式表示：

```cpp
struct AtomCounts {
  std::size_t global_count;
  std::size_t owned_count;
  std::size_t ghost_count;

  std::size_t local_count() const {
    return owned_count + ghost_count;
  }
};
```

各计数的含义为：

| 计数 | M1 replicated 路径 | M2a local-domain 路径 | 可用于数组寻址 |
| --- | --- | --- | --- |
| `global_count` | 全局原子数元数据 | 全局原子数元数据 | 否 |
| `owned_count` | reader 模型中的记录数；M1 为 N | 本 rank 空间 owned 原子数 | 否 |
| `ghost_count` | 当前必须为 0 | 本 rank 可寻址但不拥有的原子数 | 否 |
| `local_count()` | `N`（replicated stride） | `owned + ghost` | 是，所有每原子 SoA 的 stride |

M1 路径的 reader 初始化为：

```text
global_count = N
owned_count  = N
ghost_count  = 0
global_id    = [0, 1, ..., N - 1]
```

这些值在 M1 replicated runtime 中也保持相等：`local_count() == N` 仍是所有 SoA per-atom
数组的唯一 stride。实际 MPI 权威所有权由独立的 `SpatialOwnership`
（`include/dmgmd/spatial_ownership.hpp`）表示——P>1 时是沿最长边的等宽 fractional slab
给出的**槽位子集**，P=1 时是 rank 0 的平凡全集映射；不得把 reader 的 `owned_count=N`
误当成每 rank 都拥有 N 份物理输出，也不得把它误当成空间 owned 数量。

M2a 路径下 reader 的完整模型在 bootstrap 时用于推导初始 owned 集
（`include/dmgmd/domain_layout.hpp` 的 `DomainAtomRecord`）；此后 `LocalLayout` 是 device
数据面的权威布局。完整 `HostAtoms` 当前仍在每个 rank 保留，作为静态 identity/输出格式
元数据，因此 host 内存仍为 O(NP)；它不得重新成为 force/integration/thermo 数据面。
local 槽位顺序为 `[0, owned)`（global_id 升序）| dependency
ghosts | coordinate-only ghosts，ghost 段按 `(source face, source rank, global_id)`
排序；`local_count` 是全部 SoA 与 NEP workspace 的唯一 stride，`global_count`
只是元数据。每个槽位携带 global_id、owner/source rank、face 与 image shift。



## 2. Host 模型

`dmgmd::Model` 由模拟盒和 `HostAtoms` 组成：

```text
Model
├── BoxData
│   ├── periodic[3]
│   └── h[9]
└── HostAtoms
    ├── AtomCounts
    ├── has_input_velocity
    ├── global_id[local]
    ├── species[local]
    ├── type[local]
    ├── mass[local]
    ├── charge[local]
    ├── position[3 * local]
    ├── velocity[3 * local]
    └── group_labels[method][local]
```

字段的存储类型和来源如下：

| 字段 | C++ 类型 | 尺寸 | 说明 |
| --- | --- | ---: | --- |
| `global_id` | `std::uint64_t` | `local` | 稳定身份；当前按 `model.xyz` 输入顺序生成 |
| `species` | `std::string` | `local` | 输入元素符号，用于输出和类型映射 |
| `type` | `int` | `local` | 元素在 NEP potential header 中的下标 |
| `mass` | `double` | `local` | 显式 mass 或 GPUMD 默认元素质量 |
| `charge` | `float` | `local` | 输入 charge；缺省为 0，与 GPUMD 类型一致 |
| `position` | `double` | `3 * local` | 笛卡尔坐标，单位 Å |
| `velocity` | `double` | `3 * local` | parser 已从 Å/fs 转为 GPUMD 内部单位 |
| `group_labels` | `int` | `methods * local` | 每种 grouping method 一条 local 数组 |

`HostAtoms::validate()` 检查：

- `owned_count + ghost_count` 不发生溢出；
- `owned_count <= global_count`；
- 每个标量数组长度等于 `local_count()`；
- position/velocity 长度等于 `3 * local_count()`；
- 每条 group label 数组长度等于 `local_count()`。

## 3. SoA 地址规则

三分量数组使用 GPUMD 兼容的分量分块 SoA。令 `L = local_count()`：

```text
position = [x[0:L]) [y[0:L]) [z[0:L])
velocity = [x[0:L]) [y[0:L]) [z[0:L])
force    = [x[0:L]) [y[0:L]) [z[0:L])
```

分量 `axis`、本地下标 `atom` 的地址为：

```cpp
base[axis * L + atom]
```

每原子 virial 使用九个同样的分量块：

```text
virial = [xx:L][yy:L][zz:L][xy:L][xz:L][yz:L][yx:L][zx:L][zy:L]
```

该顺序直接沿用锁定 GPUMD 实现，不得在输出或 thermo 中重新解释。

## 4. Box 布局

`BoxData::periodic[3]` 保存 x/y/z 三个方向的 PBC 标志。`BoxData::h[9]` 使用 GPUMD
`Box::cpu_h[0..8]` 的矩阵存储顺序：

```text
h = [ ax bx cx
      ay by cy
      az bz cz ]
```

extended XYZ 的 `Lattice` 按 `a, b, c` 三个向量依次给出，所以 parser 在写入 `h` 时执行
转置映射。runtime 将 `h` 直接复制到 GPUMD `Box`，再由共享实现计算逆矩阵、体积、正交性
和 minimum-image 行为。

## 5. DeviceAtoms

`src/runtime.cu` 中的 `DeviceAtoms` 是当前 GPU 数据所有者。它保留完整 `AtomCounts`，并使用
GPUMD 的 `GPU_Vector` 管理设备内存：

| buffer | 设备类型 | 分配尺寸 | 当前有效域 |
| --- | --- | ---: | --- |
| `global_id` | `unsigned long long` | `local` | 全部 local；输出恢复稳定顺序 |
| `type` | `int` | `local` | NEP 可寻址域 |
| `mass` | `double` | `local` | replicated input；积分/thermo 按 MPI 空间 owned 槽位读 |
| `charge` | `float` | `local` | replicated input；rank 0 formatter 使用 |
| `position` | `double` | `3 * local` | replicated input；积分只写 MPI 空间 owned 槽位 |
| `velocity` | `double` | `3 * local` | replicated buffer；非 owned 槽位不每步同步（M0），correct_velocity 触发步与 ownership 迁移步恢复复制态；积分/控温只写 MPI 空间 owned 槽位 |
| `force` | `double` | `3 * local` | NEP 写全量 scratch；仅 MPI 空间 owned 槽位 authoritative |
| `potential` | `double` | `local` | NEP 写全量 scratch；thermo/dump 只认 MPI 空间 owned 槽位 |
| `virial` | `double` | `9 * local` | NEP 写全量 scratch；thermo/dump 只认 MPI 空间 owned 槽位 |
| `unwrapped` | `double` | 按需 `3 * local` | `dump_xyz position_unwrapped` 时启用 |
| `previous_position` | `double` | 按需 `3 * local` | 更新 unwrapped position 时使用 |

`species` 和 group labels 当前保留在 host identity 模型中；普通 NEP force 不需要它们驻留
GPU。创建输出快照时，各 rank 只打包自己的 owned 槽位（indexed pack），`MPI_Gatherv` 在
rank 0 按 global_id/slot scatter plan 恢复全局 SoA。

## 6. Kernel 所有权边界

当前 runtime kernel 明确接收 MPI 空间 owned 槽位索引列表（`device_owned_indices`，
按 global_id 升序）和 replicated stride：

- velocity-Verlet、速度缩放和 unwrapped position 更新只 launch/写入 owned 槽位；
- thermo reduction 只遍历 owned 槽位；
- force 前清零全量 NEP scratch，但后续积分/reduction/gather 只承认 owned 槽位；
- position PBC wrap 仍是 full-N（wrap 结果同时是 M1 ownership 重算的确定性输入）；
- XYZ/restart 只收集 owned records，再按 `global_id` 排序且只由 rank 0 写。

因此 ghost 即使将来出现在 local 数组尾部，也不会自动被积分、计入 thermo 或直接输出。
这并不表示当前 force 路径已经支持 ghost。

## 7. Replicated MPI NEP 边界

`NepForce` 直接构造锁定 GPUMD 源码中的 `NEP`：

```text
NEP workspace/input stride = global_count
NEP scratch center range   = [0, global_count)
authoritative output range = owned MPI range [begin_r, end_r)
```

reader 仍生成完整无 ghost 模型：

```text
global_count == local_count
ghost_count == 0
```

MPI 空间所有权（`SpatialOwnership`）与 `AtomCounts::owned_count` 不混用：后者描述 reader
得到的完整 replicated input（M1 中仍是 N，即 SoA stride），前者决定积分、local thermo sum
和输出 gather 的槽位集合；每 rank 的空间 owned 数量记录在 `SpatialOwnership` 内，空集合
（空 slab）合法，且 M1 明确支持 `N<P`。NEP scratch 保持全中心，是因为直接按 owned 设置 `N1/N2` 会缺失分片外
`Fp` 和 reverse partial。详见 `replicated-mpi.md`。

## 8. M2a local-domain 数据面（现行）

M2a（`src/domain_runtime.cu`）的 device 数组按上述 local 布局寻址：

| buffer | 分配尺寸 | 有效域 |
| --- | ---: | --- |
| `global_id` / `type` | `local_count` | 全部 local 槽位（身份/typewise 过滤） |
| `mass` / `charge` | `local_count` | owned 权威；ghost 槽位不参与积分/thermo/输出 |
| `position` | `3 * local_count` | 全部 local（ghost 位置每步 24 B p2p 刷新） |
| `velocity` | `3 * local_count` | owned 权威（correct_velocity 经 gather/scatter 维护） |
| `force` / `potential` / `virial` | `3 / 1 / 9 * local_count` | owned 权威；ghost 槽位是 scratch（清零无害，永不 gather） |
| `unwrapped` / `previous_position` | 按需 `3 * local_count` | owned（unwrapped 随迁移载荷传递） |
| NEP `Fp/sum_fxyz/f12/NN/NL` workspace | `local_count` stride | descriptor 域 `[0, owned+dep)`；候选 `[0, local_count)` |

表中的尺寸是**逻辑尺寸/stride**。为避免底层零字节 device allocation 的实现差异，
`local_count==0` 时部分 `GPU_Vector` 物理 capacity 保留 1 个元素；NEP domain 接口另行显式
接收逻辑 `local_count`，所有中心范围均为空，任何 kernel、D2H 或邻居重建都不得访问该填充
元素。`local_count>0` 时物理 per-atom capacity 与逻辑 stride 相等。

每原子输出 gather 使用 local-owned-prefix + global ID（root 按 input-slot 顺序恢复
恰好 N 条记录），`global_count` 从不作为 device 数组 stride。布局在迁移或 halo
membership 重建时整体重建（epoch++），NEP workspace 只在 `local_count` 变化时
重新分配，但每次布局变化都强制 neighbor 重建并使全部缓存 view 失效。

## 9. 初始化与输出合同（双路径）

M1 replicated runtime 入口要求：

```text
local_count == global_count
ghost_count == 0
```

每 rank device input 都使用 `global_count` 寻址。实际 per-atom 输出只来自该 rank 的 MPI
空间 owned 槽位，并在 rank 0 按 `global_id` 恢复顺序；thermo 只对 owned 槽位求 local sum
后做全局归约。

M2a local-domain 入口在 eligibility 判定后由 bootstrap 建立：初始 owned 集来自 wrapped
输入坐标的 slab 归属，owned 位置的首次 wrap 使用共享的 device `wrap_positions` kernel
（与 M1 首次 force 的 wrap 逐位一致，host 端复算会因 FMA 收缩差一个 ULP），unwrapped
种子为 raw 输入坐标。输出/thermo 只承认 owned 前缀。

single-rank 实现以及 Open MPI+UCX 环境下的 HostStaged/CudaAware 1/2/4-rank prototype 均已
在沙箱外 GPU 上通过四组锁定 GPUMD golden；M1 空间所有权的 P=1 路径与 M0 逐字节一致，
P>1 由 differential 与迁移矩阵验证；M2a 由大盒 domain 矩阵验证（实测 per-atom
输出与 P=1 oracle 逐字节一致）。验证状态与命令见
[current.md](../status/current.md)。
