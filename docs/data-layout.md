# DMG-MD 数据结构与内存布局

更新日期：2026-09-02。

本文档只描述当前 single-rank `dmg-md` 已实现的数据面，并以现有代码为权威来源。

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
| `owned_count` | 本 rank 负责积分、thermo 和输出的原子数 | 只用于确定 owned 域边界 |
| `ghost_count` | 本 rank 可寻址但不拥有的原子数 | 否，当前必须为 0 |
| `local_count()` | `owned_count + ghost_count` | 是，所有每原子 SoA 的 stride |

当前 reader 初始化为：

```text
global_count = N
owned_count  = N
ghost_count  = 0
global_id    = [0, 1, ..., N - 1]
```

这些值在 single-rank 下数值相等，但代码仍分别使用 `global_count`、`owned_count` 和
`local_count()`。不得把这种暂时相等重新编码为“数组长度就是全局 N”。

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
| `mass` | `double` | `local` | 积分和 thermo 读取 owned |
| `charge` | `float` | `local` | dump 使用 owned |
| `position` | `double` | `3 * local` | force 可寻址 local；积分只写 owned |
| `velocity` | `double` | `3 * local` | 积分和控温只写 owned |
| `force` | `double` | `3 * local` | 当前 NEP 写 owned；积分只读 owned |
| `potential` | `double` | `local` | thermo/dump 只读 owned |
| `virial` | `double` | `9 * local` | thermo/dump 只读 owned |
| `unwrapped` | `double` | 按需 `3 * local` | `dump_xyz position_unwrapped` 时启用 |
| `previous_position` | `double` | 按需 `3 * local` | 更新 unwrapped position 时使用 |

`species` 和 group labels 当前保留在 host identity 模型中；普通 NEP force 不需要它们驻留
GPU。创建输出快照时下载 local 数组，但 record selection 只选择 owned 前缀。

## 6. Kernel 所有权边界

当前 kernel 明确接收 `owned_count` 和/或 local stride：

- velocity-Verlet、速度缩放和 unwrapped position 更新只 launch/写入 owned atoms；
- thermo reduction 只遍历 owned atoms；
- force 前清零只清 owned force、potential 和 virial；
- position PBC wrap 使用 local 可寻址域；
- XYZ/restart 只选择 owned records，再按 `global_id` 排序。

因此 ghost 即使将来出现在 local 数组尾部，也不会自动被积分、计入 thermo 或直接输出。
这并不表示当前 force 路径已经支持 ghost。

## 7. Single-rank NEP 边界

`NepForce` 直接构造锁定 GPUMD 源码中的 `NEP`：

```text
NEP workspace size = local_count
NEP center range   = [0, owned_count)
```

构造和每次 force 计算都验证：

```text
ghost_count == 0
owned_count == local_count
```

不满足条件会报错 `multi-rank force evaluation is not implemented`。这是刻意的 fail-closed
边界：当前实现没有 halo、迁移、NEP intermediate exchange 或 reverse-force exchange，不能
通过删除检查来获得正确的多-rank 力。

## 8. 初始化与输出合同

single-rank runtime 入口额外要求：

```text
owned_count == global_count
ghost_count == 0
```

`global_count` 仅用于全局元数据，例如 restart 的原子数 header。实际 per-atom 输出来自
owned atoms；`global_id` 决定稳定顺序。thermo 的温度、势能和 stress 也只从 owned 数据归约。

当前布局已通过四组锁定 GPUMD golden 的 force、energy、thermo、trajectory 和 restart
differential tests。验证结果见 [progress.md](./progress.md)，相关架构选择见
[decisions.md](./decisions.md)。
