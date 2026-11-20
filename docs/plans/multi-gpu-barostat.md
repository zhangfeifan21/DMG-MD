# DMG-MD 多 GPU 控压器开发方案

类别：后续开发方案。状态：**IN PROGRESS**。更新日期：2026-11-06。
代码审计基线：`d1b933c05c857e536ffb0e71e2212bc02939f5bc`。

DN0–DN4 已在[局部 NPT 标准](../standards/domain-npt.md)和[最终验证记录](../status/validation/domain-npt-validation.md#dn4-final-20261116)所列有限范围完成。计划整体仍为 IN PROGRESS：DN5 性能与默认采用待后续；§1–5 保留实施前的审计基线与设计推导。

建议在现有局部域分解模式上接入 `npt_ber` 和 `npt_scr`：每张 GPU 计算本域力与局部热力学量，MPI 归约少量全局统计，rank 0 推进控制器并广播缩放因子，各 GPU 只更新 owned 原子。真正需要新增的是**变盒与域分解、halo、邻居缓存之间的正确性协议**。完成按形变判定的缓存复用后，才把该路径作为多 GPU NPT 性能候选。

首期限定 NEP、各向同性、正交全周期盒、一 rank 一 GPU、固定正步长，沿用现有两跳位置 halo。先实现 Berendsen，再接 SCR；3D 分解、分阶段中间场交换方案、各向异性、MTTK/Parrinello–Rahman 和降低控压频率分别立项。本方案扩展现有 v1.0 计划中的暂缓项，不改变现行 P0 发布门与支持声明。实际支持范围仍以[控压器标准](../standards/barostat.md)为准。

## 1 当前瓶颈与已有基础

| 当前事实 | 代码证据 | 开发含义 |
| --- | --- | --- |
| 程序只要包含 NPT，就跳过局部域分解资格判定，强制全量复制；局部入口再次拒绝 | [runtime.cu](../../src/runtime.cu) `run_replicated`，约 1347、1392、1433 行；[domain_runtime.cu](../../src/domain_runtime.cu) `run_local_domain`，约 2173 行 | 必须同时修改 dispatcher、入口守卫和组合验证，不能仅删除拒绝语句 |
| 全量复制路径每 rank 分配全局 NEP workspace，`N1=0,N2=local=N`，完整执行 NEP | `runtime.cu` `NepForce`，538–568 行 | 主导力计算未按 GPU 数分摊，另有逐步位置 allgather 和 ownership 协议成本 |
| thermo 已只计 owned，并做 8-double SUM | `runtime.cu::compute_thermo`，513–535 行；`domain_runtime.cu::compute_domain_thermo`，1384–1419 行 | 问题并非压力被重复累计；无需重新发明分布式压力计算 |
| 控制器已与数据面分离，root 广播温控/控压因子 | [ensemble_control.cpp](../../src/ensemble_control.cpp) `barostat_scale`；[runtime_internal.hpp](../../src/runtime_internal.hpp) `ensemble_box_factor`，207 行起 | 复用算法、单位、root RNG 和错误广播 |
| 当前盒缩放 API 要求完整位置数组 | [box_control.cu](../../src/box_control.cu) `apply_isotropic_scale`，64–108 行 | 需要显式 owned_count/local_stride 的新原语，不能将 global_count 偷换成 local_count |
| 局部路径已有 owned VV、两跳 halo、迁移、统一缓存 reason OR | `domain_runtime.cu::run_domain_segment`，1748–1968 行 | 在已有执行链插入盒事务，保持原子权威唯一 |
| 迁移不携带旧 force/PE/virial；段首会重算力 | `domain_runtime.cu` `MigrationWireRecord`、`do_migration`、段首 force，约 255、1051、1710 行 | 迁移时点和新段入口必须单独设计 |

因此，多 GPU 控压的收益主要来自恢复分布式 NEP 计算。将几个控压标量放到 GPU，或把全量复制路径的 allgather 改成更快后端，均不能消除每卡完整 NEP 的主成本。这里是结构性瓶颈判断，尚无本方案的实测加速比。

## 2 从 LAMMPS 与 GROMACS 借鉴什么

### 2.1 LAMMPS 与 KOKKOS

本地参考锁定 `stable_22Jul2025_update4`、commit `611ca3b7f8525ba802373b04f9e3d632d515f7e3`；在线 LAMMPS 手册当前标注 `30Sep2026`，两者分别作为实现证据与通用说明。

`FixNHKokkos` 继承 `FixNH`：设备 kernel 遍历本 rank 的 `nlocal` 完成积分、速度缩放及坐标 remap；全局温度和压力来自本地归约后的 MPI 归约。`remap` 使用分数坐标更新盒及本地域几何。可核对锁定源码的 [fix_nh_kokkos.cpp](https://github.com/lammps/lammps/blob/611ca3b7f8525ba802373b04f9e3d632d515f7e3/src/KOKKOS/fix_nh_kokkos.cpp) 中 `initial_integrate`、`remap`、`nve_v/nve_x`，以及 [compute_temp_kokkos.cpp](https://github.com/lammps/lammps/blob/611ca3b7f8525ba802373b04f9e3d632d515f7e3/src/KOKKOS/compute_temp_kokkos.cpp) 和 [compute_pressure.cpp](https://github.com/lammps/lammps/blob/611ca3b7f8525ba802373b04f9e3d632d515f7e3/src/compute_pressure.cpp)。

更值得借鉴的是重建协议：`NeighborKokkos::check_distance_kokkos` 把盒边界变化计入 skin 预算，再全局决议是否重建；`VerletKokkos` 在重建分支重设通信几何、迁移、重建 ghosts/neighbor，普通步只刷新 ghosts。它并非因每次盒数值变化就无条件重建。证据见 [neighbor_kokkos.cpp](https://github.com/lammps/lammps/blob/611ca3b7f8525ba802373b04f9e3d632d515f7e3/src/KOKKOS/neighbor_kokkos.cpp) 162 行起、[verlet_kokkos.cpp](https://github.com/lammps/lammps/blob/611ca3b7f8525ba802373b04f9e3d632d515f7e3/src/KOKKOS/verlet_kokkos.cpp) 310 行起及[官方步进流程](https://docs.lammps.org/Developer_flow.html)。

DMG-MD 应采用这种局部原子更新与变盒缓存管理方式，继续使用自身 CUDA/MPI 层。LAMMPS NPT 的 Nose–Hoover/MTK 分裂、速度耦合和自由度定义不能替代 DMG-MD 现行 Berendsen/SCR 算法；LAMMPS 的状态复制方式也不替代 DMG-MD 的 root RNG 合同。[KOKKOS 官方指南](https://docs.lammps.org/Speed_kokkos.html)说明设备执行与 MPI 配合方式，但采用 KOKKOS 库本身不是本方案的前置条件。

### 2.2 GROMACS

采用 GROMACS 2026.3 性能和参数手册，DD 算法说明参考 2025.0 固定版本。其空间域映射到 PP rank，支持把短程力和适用的 update 放到 GPU，减少逐步整域 CPU/GPU 往返；直接 GPU 通信与 GPU-resident 路径在适用配置下配合。2026.3 的 GPU update 支持有条件的 DD，带约束时要求约束处于 update groups 内；还存在虚拟位点等组合限制，不能概括为任何 NPT 输入都可全程 GPU 执行。参见 [2026.3 性能指南](https://manual.gromacs.org/2026.3/user-guide/mdrun-performance.html)与 [DD 算法](https://manual.gromacs.org/documentation/2025.0/reference-manual/algorithms/parallelization-domain-decomp.html)。

GROMACS 区分控压、能量计算和邻居更新频率。`nstpcouple`、`nstcalcenergy` 的安排可降低全局统计通信；`nstlist` 则管理邻居更新。DMG-MD 当前每步控压的合同应保留，后续若改变频率，须重新定义耦合时间、SCR 噪声增量和离散算法并独立验证，不能直接照搬其默认频率。参见 [2026.3 mdp 参数](https://manual.gromacs.org/2026.3/user-guide/mdp-options.html#mdp-nstpcouple)。

源码也体现了可复用的边界：v2026.3 的 `pressureCouplingScaleBoxAndCoordinates` 接收局部原子范围，调用处使用 `start/homenr`；`decideWhetherToUseGpuForUpdate` 按 DD、约束和 update groups 等条件决定设备路径。算法比较则须谨慎：GROMACS 的 C-rescale 分支对速度使用逆缩放矩阵，而 DMG-MD 当前 SCR 的 barostat 不缩放速度，不能将两者作为逐步轨迹等价的实现。参见 [coupling.cpp](https://github.com/gromacs/gromacs/blob/v2026.3/src/gromacs/mdlib/coupling.cpp) 的调用处与 1199 行起的模板，以及 [decidegpuusage.cpp](https://github.com/gromacs/gromacs/blob/v2026.3/src/gromacs/taskassignment/decidegpuusage.cpp) 的 DD 条件检查。

GROMACS 的常见 pair-force reverse communication 也不能直接移植到 NEP。DMG-MD 当前中心 gather 依赖两跳坐标、dependency-center descriptor/partial 重算，应继续按[现有依赖证明](../standards/cache-validity.md)维护；中间场 forward exchange 或 Newton scatter/reverse exchange 属于独立协议。

| 参考机制 | DMG-MD 首期选择 |
| --- | --- |
| 空间分解与 GPU 局部工作 | 复用当前 slab、owned/ghost 和 `NEP::compute_domain` |
| 全局压力、局部坐标缩放 | 复用 owned thermo SUM，root 控制器广播；新建 owned 缩放 API |
| 盒形变参与 neighbor 安全判定 | 先强制重建作正确性基线，再加入参考盒度量的复用证明 |
| GPU 常驻与小量全局控制 | 保留局部 GPU dynamics；HostStaged/CudaAware 分别验证，控制标量暂留 CPU |
| 调整耦合与计算频率 | 首期保持每步反馈；降频作为后续算法变更 |
| 三维 DD、动态负载均衡 | 后续扩展，不阻塞首个满足几何条件的大盒 NPT 路径 |

## 3 目标数据面与控制面

所有 rank 持有一致盒矩阵、逆矩阵、float 副本、目标参数和全局步号；只有 rank 0 持有并推进 SCR 的随机状态。大规模粒子数据继续使用 `owned | dependency ghosts | coordinate-only ghosts` 与 `local_count` stride。

| 操作 | 权威与范围 | 通信 |
| --- | --- | --- |
| VV、温控和盒坐标缩放 | GPU owned 原子 | 无原子全局收集 |
| NEP 力、每原子能量和 virial | owned force centers；owned+dependency descriptor centers | 当前两跳 position halo |
| 温度和压力 | owned sums，ghost 不计入 | 沿用每步一次 8-double SUM，逻辑 payload 64 B/rank |
| 控制器更新 | rank 0，严格保留当前调用顺序 | 复用温控与控压的 status/factor 广播 |
| 盒提交前校验 | 所有 rank 检查自身 owned 坐标与同一候选盒 | 新增一致失败判定；不得单 rank 抛错后其他 rank 继续通信 |
| 重建和迁移 | 统一 reason 决议后执行 | 复用现有 p2p membership 与必要时的 Alltoall/Alltoallv |
| 输出 | root 按 global ID 恢复顺序 | 仅请求输出时 gather owned snapshot |

压力沿用当前 virial 号、单位和时间层。按 DMG-MD 当前定义，等方压力为 `(2K + trace(W)) / (3V)`，并继续从已归一化 thermo 的三个对角分量取均值；不能套用其他引擎的 virial 符号或计数约定。首期不拆掉现有 8-double 归约中的 PE/非对角分量，以保留输出和数值路径。

控压新增通信随系统原子数为常数；这不表示 MPI 延迟随 rank 数为常数。普通、非输出步不得出现全量 position/force gather 或全局 N 大小的 GPU scratch。现有局部路径仍保留全局 host identity 元数据，本方案不声称全部主机内存已按 N/P 缩小。

## 4 步序与盒变更事务

### 4.1 保留物理时间层

现行 NPT 是 `VV1 → force → VV2 → thermo → thermostat → barostat → output`。thermo/压力使用缩放前样本；barostat 只缩盒与 wrapped position，不缩 velocity、unwrapped，也不立即重算 force。SCR 在同一 root 流中先消费 BDP 随机数，再消费压力 Gaussian；`B>2000` 固定盒分支仍保留规定的随机调用。

建议局部路径的连续步如下：

```text
owned VV1，消费上一轮留存力，累计真实积分 drift
    ↓
wrap 与连续性检查
    ↓
统一 cache 决议，合并待处理盒变更
    ├─ reuse：用当前坐标/盒刷新既有 ghost members
    └─ rebuild：必要的迁移 → 重建 membership/layout/Verlet
    ↓
local-domain NEP → owned VV2
    ↓
owned thermo sums → 8-double Allreduce
    ↓
root thermostat → 广播 → owned velocity scale
    ↓
root barostat → 广播候选 factor
    ↓
全 rank preflight → 提交盒和 owned position scale
    ↓
更新 box generation；ghost 标为待刷新；保存 pending cache 状态
    ↓
按既有时间层输出，进入下一步
```

**不能在步末缩盒后立刻调用现有迁移。**当前迁移不携带旧力；直接上传新 layout 会丢失下一 VV1 需要的力，亦可能破坏步末 XYZ 所需旧 force/PE/virial。连续步优先把重建放在下一次 VV1 之后、force 之前；此时旧力已被消费，新力随后计算，原迁移 payload 可保持不变。仅设置 invalid/pending 标志不得提前清空力缓冲或调用会重分配其内容的 workspace 操作。

段首是另一条入口：当前每段 `run` 都重算力。若上段末刚缩盒，必须先解决 pending 重建或 ghost 刷新，再执行段首 force，不能沿用“`neighbor_cache_valid` 为真即可直接求力”的判断。统一 readiness helper 应覆盖初始化、连续步、新段及 NPT/NVE/NVT 切换。段首重算力、每段重建控制器/RNG和 restart 新作业语义保持现状；同一段内 cache 重建不能重置 RNG。

### 4.2 两阶段提交

建议增加局部 API，例如 `prepare_domain_box_update`、`commit_domain_box_update` 和 `ensure_domain_force_ready`；名称为待开发接口，当前尚不存在。

1. rank 0 沿用 `ensemble_box_factor` 计算因子，验证候选 Box 并广播 status/factor。各 rank 使用完全相同的候选矩阵；必要时广播矩阵，优先保证一致性。
2. 所有 rank 完成本地 preflight：因子/坐标有限、逆矩阵和 float 副本有效、owned 缩放无溢出、满足 NEP large-box 与当前 slab 条件、无在途 halo/force 使用旧盒。统一 OR 后决定提交或共同失败；空 owned rank 也参与。
3. owned 缩放 kernel 使用独立 `owned_count` 与 `local_stride`。盒更新与 kernel 排序必须显式；只有完成就绪条件后才能输出或发起下一 halo。普通步保持 GPU 常驻，不将完整 owned dynamics 下载来缩放。
4. 更新当前盒/物理 slab 元数据和 `box_generation`；保持 manager ownership、local slots 与旧 force 不变。ghost 在下一 force 前从 owner 刷新，不在各 rank 独立积分或累积缩放旧 ghost。
5. 首期用额外的小标量 preflight collective 换取明确的一致失败协议。后续可融合温控/控压广播或状态判定，但不得改变随机调用顺序、失败顺序或引入循环等待；成本纳入 benchmark。

不可在申请因子失败后截断因子、重复抽样或静默跳过控压。正常数值拒绝在提交前达成一致；设备执行故障走统一 fatal 路径，不宣称可回滚继续运行。

### 4.3 几何失效策略

启动时继续检查两跳 `d_coord`、large-box 条件与 `slab_width >= d_coord`。NPT 资格检查必须使用当前/候选盒，不能始终引用初始 `model.box.h`。初期固定分解轴与 rank 分数边界，各向同性缩放时物理 slab 同比例变化，纯仿射缩放不会使原子的分数归属发生实质变化。

对于初始不满足条件的输入，保留全量复制回退并打印原因；对于运行中收缩到不满足条件的输入，首期在提交前一致终止，报告步号、盒长、slab 宽度及所需半径。不要在正在运行的段中偷偷转成全量复制：那需要全局内存、完整 dynamics/旧力搬运及控制器状态保持，是另一个功能。边界舍入导致的归属变化按一致 routing/rebuild 协议处理。

## 5 让变盒后的邻居缓存可以安全复用

### 5.1 正确性基线

第一版对每次实际盒变化记录 pending rebuild，在下一 force 前按第 4 节重建。因子恰为 1 时不制造盒事件。它用于验证局部控压、时序和输出，也是后续缓存优化的同算法 oracle。

这个版本可能每步重建 membership/Verlet，并在需要时迁移，不作为最终性能交付。不能以通过该版本测试就宣布多卡 NPT 已获得加速。

### 5.2 各向同性形变的候选充分条件

以下保留 DN2 设计推导；现行实现合同和验证范围见局部 NPT 标准及验证记录。保留当前[逐边两跳证明](../standards/cache-validity.md)，新增对仿射形变的度量。

设最近一次重建的盒为 `H0`，当前盒 `H=aH0`，`a>0`；`q_i` 为 epoch 参考位置。在连续周期 lift 下定义参考度量位移 `u_i`，使 `x_i=a(q_i+u_i)`；令 `δ=max_owned |u_i|`。每次 VV1 将 wrap 前真实位移 `Δx/a` 累入 `u_i`，纯仿射缩放不增加 `u_i`。它是专用缓存状态，不能直接复用“不随控压缩放”的输出 `unwrapped_position`，也不能通过两个 wrapped 坐标做一次 MIC 得到。

对当前距离在 cutoff `R` 内的任意作用边及其周期像，有：

```text
|q_j - q_i + H0 n| <= R/a + 2δ
```

令 `R*` 为所有实际 force/dependency 单边 cutoff 的保守最大值，包含 typewise、angular、ZBL 和 kernel 舍入上界。一个便于全局判定的充分条件是：

```text
2δ + max(0, 1/a - 1) R* <= skin
```

于是每条当前作用边在参考盒中都位于 `R+skin` 内；连续两边分别应用此界，可维持：

```text
d_dep,0   = max R_force + skin
d_coord,0 = max(R_force + R_dep) + 2 skin
```

这里 `R*` 是单边最大值；两跳闭包通过两次单边界相加，不能把两跳当成一条只消耗一次 skin 的边。纯压缩即使所有 `u_i=0` 也会消耗 skin；纯扩张仍要求满足非仿射位移界，且每次 force 都用当前盒和实际 cutoff 重新过滤行。

各 rank 用共同 `a,R*,skin` 和本地最大位移决定 reason bit，通过现有全局 OR 得到一致动作，无须仅为该阈值另增 double MAX。若压缩已耗尽预算则无条件重建。实现时先检查预算非负，再平方比较；不能将负预算平方成可复用阈值。

### 5.3 必须补齐的工程证明

- **实际矩阵与舍入：** repeated scaling 的 CPU/float 盒未必逐位满足理想 `aH0`。用实际提交矩阵与参考矩阵构造保守伸缩下界，界定位移累计误差、cutoff 舍入和比较裕量；不确定时重建。不要只累乘 factor 而忽略实际盒矩阵误差。
- **参考几何与当前几何：** epoch 的参考 slab/membership 参与闭包证明，应与当前物理 slab 分开保存。更新 `axis_length` 不得偷偷改变仍在复用的参考成员集；重建才重置参考盒、参考 slab 和 `u_i`。
- **身份和 image：** owned/ghost global ID、单槽 ghost/MIC、两面同 peer、周期跨越须继续满足现行表示条件；geometry eligibility 每次提交都检查。有限单步位移上限仍保留，不能依靠 MIC 隐藏多盒跳跃。
- **generation 分离：** `box_generation` 随盒变化，`layout_epoch` 随 membership/ownership 变化，Verlet generation 随重建变化。halo-ready 至少绑定本步、layout 与 box generation；仅 epoch 相等不足以证明缩盒后的 ghost 已刷新。
- **所有求力入口：** 连续步与新段首都先得到一致 reuse/rebuild 决议；旧 neighbor rows 只在已证明的参考度量界内复用。
- **实验分区与 overlap：** 当前 `DomainPartitionKey` 包含精确盒值，并在同 layout 下盒变化时拒绝。首期 NPT 要求 partition/overlap 为 off；不得删除这道保护来“支持”新路径。其图、mask、几何内缩和 force ticket 需另行适配并重新验收。

若参考度量实现代价或证明复杂度超出预算，可先采用更保守的“真实位移加盒边界变化”预算，但同样必须推导对 NEP 两跳 halo 的充分性并实测重建率。不能直接删除固定盒检查，也不能直接复制 LAMMPS 的 pair 邻居判据当作 NEP 两跳证明。

## 6 阶段结果与后续工作

DN 编号独立于已有 B/O 系列。各阶段完成本阶段门后再进入依赖阶段；正确性基线和性能缓存版必须能独立选择、比较。

| 阶段 | 开发内容与主要文件 | 完成门 |
| --- | --- | --- |
| DN0 合同与测试冻结（完成） | 冻结上述步序、几何失效策略、实验开关及矩阵；设计独立 pressure/box/cache oracle；明确 `run_replicated` 与多段 routing | 评审覆盖旧力、段首、RNG、halo 及两跳闭包；case/容差/统计预算写入 manifest；此阶段不开放生产路由 |
| DN1 Berendsen 局部基线（完成） | `box_control.hpp/.cu/.cpp` 新 owned API；`domain_runtime.cu` pending box/readiness；dispatcher 与守卫；强制重建模式 | 真正进入局部域分解；压力、盒、逐原子力/virial与短轨迹通过；旧力消费和段首反例通过；全量复制/P1 回归通过 |
| DN2 形变缓存（完成） | 新 reference-box/displacement 状态；统一 reason OR；`domain_layout.hpp` 几何状态；neighbor/context generation；默认关闭实验 overlap | 充分性证明和独立边/两跳 oracle 通过；与 DN1 同快照一致；弱耦合时有实际 reuse，强收缩时正确重建；通信计数符合设计 |
| DN3 SCR 接入（完成） | 复用 `EnsembleController` 与 `ensemble_box_factor`；检查重建、多段、fixed-box 分支的 RNG 调用 | 同 seed 可重复；同一输入标量流下不同 rank 数得到一致 root 随机序列；双路径短程合同通过 |
| DN4 科学与运行验收（完成，范围见验证记录） | 新局部 NPT runner；扩展 NPT、MPI、restart 与错误矩阵；独立多种子注册 | 所列势/体系/后端/rank 的正确性和统计门通过；未通过的范围保持未支持 |
| DN5 专用服务器性能与采用 | 同物理任务比较全量复制、DN1、DN2；profile、强/弱扩展、跨节点；更新标准和用户指南 | 预注册端到端加速门通过才采用；明确适用规模、rank 上限、后端和统计支持范围 |

实验模式 `DMGMD_DOMAIN_NPT=off|rebuild|affine` 已实现，默认仍为 `off`；当前验收状态以验证记录为准。全部 rank 验证设置一致。`off` 保留 NPT 全量复制，可用作同 P oracle；其余模式仍受几何与组合约束。采用阶段再决定自动路由默认值，并同步修改路由负测，不能提前将历史“必须回退”测试整体删除。

DN0–DN4 的实现、测试及验收证据以[验证记录](../status/validation/domain-npt-validation.md#dn4-final-20261116)和[局部 NPT 标准](../standards/domain-npt.md)为准；以下验证设计保留为可审计的原要求。DN5 尚未执行。

## 7 验证设计

### 7.1 确定性与几何

复用 [NPT 测试](../../tests/npt/README.md)、[MPI domain runner](../../tests/mpi/run_mpi_domain.py)、[压力 oracle](../../tests/thermo/run_pressure_oracle.py)、[box host tests](../../tests/unit/box_control_host_tests.cu)；新增局部 NPT 专用 manifest 和 runner。现有 [m2a_npt_guard_tests.cu](../../tests/unit/m2a_npt_guard_tests.cu) 应转为按功能开关与不支持组合验证，不可只删守卫测试。

| 层次 | 必须覆盖 |
| --- | --- |
| 压力和局部原语 | 全局 owned 恰一次计数；ghost 填毒不影响 thermo；压力正负方向/单位；owned=0、local>owned、stride!=N、因子 1、非有限与溢出；提交前故障不部分更新 |
| 静态力与变盒 | 在同一缩放快照比较 P1/全量复制与局部路径的逐原子 E/F/全部 virial、总压力；均匀仿射缩放扫描与有限差分 virial/体积导数检查，沿用现有号约定 |
| 两跳闭包 | cutoff/skin 界内外与一个 ULP；i→j→k 跨 slab；typewise、NEP5、ZBL；P2 两面同 peer；PBC 与单槽 image；遗漏一个依赖点时 oracle 必须失败 |
| 形变缓存 | 纯压缩且零非仿射位移、纯扩张、压缩/扩张循环、长串小缩放、近零因子；同时存在 drift 和缩放；预算不足却平方为正的反例；非有限误差须失败 |
| manager 与迁移 | micro-crossing、反复跨边界、无迁移重建、多 slab 迁移、空 rank/N<P、零收发；重建前后 gid 权威恰一次 |
| 时间顺序 | 末步缩盒后下一 VV1 使用旧力；输出保留旧 E/F/virial；新 run 段首先刷新/重建；NPT→NVE/NVT→NPT；fixed-box 不制造盒重建 |
| 几何和失败 | 候选盒越过 large-box/slab 门；某 rank preflight 失败；空 rank 同步参与；所有 rank 同步退出，不挂 MPI；初始回退与运行中失败分别验证 |
| 输出与 restart | rank 0 写出、global ID 顺序、缩放后盒/坐标、现有 unwrapped 语义；P1↔P2↔P4、新作业重建 RNG；不要求 split/restart 轨迹连续 |

每项必须验证实际 `mode=m2a` 和新 NPT 路径标记，禁止把几何回退后的通过算作局部 NPT 通过。P1 恒走既有全量复制；P2/P4 必须使用足够大的真实局部域分解 fixture。216 原子既有 SCR 统计体系不能直接充当分布式性能或大盒 NPT 验收。

已有逐字段和短轨迹容差沿用各 manifest；新增项先用独立重复基线冻结误差门。DN1/DN2 的“一致”按该门逐项判定，不通过时查明来源，不能为候选任意放宽容差。

### 7.2 统计和回归

`npt_ber` 用确定性短程对照、压力/体积松弛及输出合同验证，不用其体积涨落证明正确 NPT。`npt_scr` 分成两种验收：固定标量输入验证 root RNG 与缩放公式；真实大盒多种子运行验证温度、压力、平均体积、体积方差/分布、有效样本量和步长依赖。跨 rank 归约会改变浮点轨迹，不能把长期逐位相同作为统计验收要求。

新统计注册需预先冻结体系、burn-in、链长度、seed、相关时间处理、误差预算和停止规则；用同算法全量复制作为并行等价参照，并保留独立 EOS/响应或可信采样器的科学对照。体积涨落检验中的材料压缩率来自独立测量，不能用输入的控压参数 B 代替。现有 216 原子 G-B PASS 的支持范围不能自动扩大；NEP5/ZBL 的短程通过也不能替代统计通过。

保留 P1 golden、全量复制 NPT、局部 NVE/三种 NVT、长期 NVE、MPI halo/迁移、输出和 restart 回归。CPU/静态检查完成后，按[环境合同](../standards/mpi-runtime.md) source 统一环境并通过 MPI/CUDA 自检，再执行 GPU 数值矩阵。HostStaged 与 CudaAware 单独记结果；跨节点是新增验收范围，不能从单机推断。

## 8 性能验收与后续扩展

### 8.1 先测端到端，再决定采用

候选每步成本可分解为：

```text
T(P) ≈ T_NEP(N/P + dependency工作)
     + T_owned_update(N/P) + T_halo(ghost数量)
     + T_thermo_reduce(P) + T_controller_and_preflight(P)
     + 重建比例 × T_rebuild(P) + T_output(P)
```

在专用服务器，用相同输入模型、势、NPT 参数、dt、步数、输出频率、精度、CPU/NUMA 绑定和 GPU 配置比较：P1 全量复制、P>1 全量复制、局部强制重建、局部形变缓存。分别报告 BER/SCR 与两通信后端。NVE/NVT 同规模测量只能用来分解 NPT 附加成本，不能替代 NPT 的加速比分母。

从已有大盒 NEP benchmark 中选定至少两类不同 descriptor 成本的体系，P=1/2/4/8 按几何资格运行。强扩展固定 N，弱扩展固定 owned 工作并记录实际形状；当前 1D slab 的表面积/体积比和 `P <= L_axis/d_coord` 限制必须可见，不满足资格的配置不记作局部性能样本。

正式样本前冻结重复次数、warm-up、配对/随机运行顺序、统计方法及追加规则。报告 steps/s、atom-steps/s、整段耗时、`S(P)=T(1)/T(P)`、`E(P)=S(P)/P`、置信区间、峰值显存、ghost/owned、重建/迁移频率，以及压力归约、factor 广播、preflight、halo、NEP 和输出占比。用独立 PMPI/trace 验证普通非输出步确实没有全量坐标通信，详细诊断不计入正式墙钟样本。

建议采用门是：科学与回归全部通过；预注册适用范围内，相对同算法 P1 和同 P 全量复制的加速比下置信界均大于 1；未达门的 rank/规模不宣称加速。是否要求更高效率应在采样前约定。不能预先承诺线性加速，也不能以控制 kernel 耗时下降替代整步收益。

### 8.2 后续任务的顺序

1. 若两跳 ghosts/descriptor 冗余主导，按[域分解扩展](domain-decomposition.md)评估 3D 分解或分阶段中间场交换方案；两者分别做成本模型与正确性门。
2. 若小标量同步主导，先评估合并控制消息、缩小必要同步及输出需求，保留当前物理反馈频率。CUDA-aware MPI 不等同于所有 collective 都无 host 往返，仍以实际 trace 为准。
3. 若需要降低控压/温控频率，新增明确的算法选项和误差/采样验证，重新处理步数型 `tau_p`、Tdamp 与 SCR 噪声时间间隔。
4. 各向异性/三斜盒、MTTK/Parrinello–Rahman、动态负载均衡及透明运行中回退分别立项。不能将它们与首期局部各向同性 NPT 合并为一次大改。

DN0–DN4 已完成上述有限范围的验证；DN5 的实测结果决定性能采用、默认路由与适用范围。
