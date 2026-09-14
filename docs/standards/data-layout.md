# DMG-MD 数据结构与内存布局

类别：现行标准。

更新日期：2026-09-03。

本文档描述当前 single-rank 与 replicated-data MPI `dmg-md` 的数据面，并以现有代码为
权威来源。

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

| 计数 | 当前含义 | 可用于数组寻址 |
| --- | --- | --- |
| `global_count` | 全局原子数元数据 | 否 |
| `owned_count` | reader 模型中的记录数；replicated prototype 当前为 N | 否 |
| `ghost_count` | 本 rank 可寻址但不拥有的原子数 | 否，当前必须为 0 |
| `local_count()` | `owned_count + ghost_count` | 是，所有每原子 SoA 的 stride |

当前 reader 初始化为：

```text
global_count = N
owned_count  = N
ghost_count  = 0
global_id    = [0, 1, ..., N - 1]
```

这些值在 replicated prototype 中也保持相等。实际 MPI 所有权由独立的 `OwnedRange` 表示，
不得把 reader 的 `owned_count=N` 误当成每 rank 都拥有 N 份物理输出。

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
| `mass` | `double` | `local` | replicated input；积分/thermo 按 MPI owned range 读 |
| `charge` | `float` | `local` | replicated input；rank 0 formatter 使用 |
| `position` | `double` | `3 * local` | replicated input；积分只写 MPI owned range |
| `velocity` | `double` | `3 * local` | replicated state；积分/控温只写 MPI owned range |
| `force` | `double` | `3 * local` | NEP 写全量 scratch；仅 MPI owned range authoritative |
| `potential` | `double` | `local` | NEP 写全量 scratch；thermo/dump 只认 MPI owned range |
| `virial` | `double` | `9 * local` | NEP 写全量 scratch；thermo/dump 只认 MPI owned range |
| `unwrapped` | `double` | 按需 `3 * local` | `dump_xyz position_unwrapped` 时启用 |
| `previous_position` | `double` | 按需 `3 * local` | 更新 unwrapped position 时使用 |

`species` 和 group labels 当前保留在 host identity 模型中；普通 NEP force 不需要它们驻留
GPU。创建输出快照时，各 rank 只打包 MPI owned range，`MPI_Gatherv` 在 rank 0 恢复全局 SoA。

## 6. Kernel 所有权边界

当前 runtime kernel 明确接收 MPI owned `begin/end` 和 replicated stride：

- velocity-Verlet、速度缩放和 unwrapped position 更新只 launch/写入 owned atoms；
- thermo reduction 只遍历 owned atoms；
- force 前清零全量 NEP scratch，但后续积分/reduction/gather 只承认 owned range；
- position PBC wrap 使用 local 可寻址域；
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

MPI owned range 与 `AtomCounts::owned_count` 不混用：后者描述 reader 得到的完整 replicated
input，前者决定积分、local thermo sum 和输出 gather。NEP scratch 保持全中心，是因为直接按
owned 设置 `N1/N2` 会缺失分片外 `Fp` 和 reverse partial。详见 `replicated-mpi.md`。

## 8. 初始化与输出合同

replicated runtime 入口要求：

```text
local_count == global_count
ghost_count == 0
```

每 rank device input 都使用 `global_count` 寻址。实际 per-atom 输出只来自该 rank 的 MPI
owned range，并在 rank 0 按 `global_id` 恢复顺序；thermo 只对 owned range 求 local sum后做
全局归约。

single-rank 实现以及 Open MPI+UCX 环境下的 HostStaged/CudaAware 1/2/4-rank prototype 均已
在沙箱外 GPU 上通过四组锁定 GPUMD golden。验证状态与命令见
[current.md](../status/current.md)。
