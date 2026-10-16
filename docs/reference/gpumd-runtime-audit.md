# 锁定 GPUMD runtime 审计

类别：参考代码证据。参考 commit：`9d23496e41319b9e2af5221a7df6285387401d1e`。
核对日期：2026-10-16。下文 src 路径/行号均指只读 `../gpumd-reference`，不是 DMG-MD。

范围为普通单 bead NEP/NEP-ZBL、NVE/nvt_ber。参考程序多数数组以全局 N 为 stride；
这些事实解释数值来源，不能当当前 MPI 编排。DMG-MD 的差异见文末与现行标准。

## 入口与初始化

```text
main → Run::Run
  → initialize_position：扫描 run.in 的 potential，读取 species/schema/atoms/groups
  → allocate_memory_gpu：完整 N SoA
  → Velocity::initialize：model vel 或默认 300 K
  → execute_run_in：逐行分词并立即执行
```

| 符号 / 位置 | 已证实行为 | 对分布式实现的约束 |
| --- | --- | --- |
| main / main_gpumd/main.cu:29 | 工作目录固定输入，普通路径构造 Run | MPI/device 生命周期属于 DMG-MD 启动层 |
| Run::Run / main_gpumd/run.cu:144 | 全局 Box/Atom/Force/Integrate/Measure | 不共享全局对象作分布式状态 |
| initialize_position / model/read_xyz.cu:482 | 完整输入；行序隐含身份 | DMG-MD 显式 gid，拆 global/owned/local |
| get_filename_potential / read_xyz.cu:427 | 最后一个 potential 行决定读取 species 的文件 | 多 potential 存歧义；DMG-MD 明确拒绝多势 |
| read_line2 / read_xyz.cu:155 | 等号/引号解析、大小写规则 | 语法与当前实现差异见兼容矩阵 |
| Group::find_size/find_contents / model/group.cu:25 | contents 存全局数组下标 | labels 是持久字段，本地下标不得当身份 |
| allocate_memory_gpu / read_xyz.cu:532 | 全 N type/mass/charge/position 与输出 | M2a 另按 local_count 分配 |
| Velocity::initialize / main_gpumd/velocity.cu:312 | 全局 CPU 随机序列与动量修正 | 当前 root 按全局顺序生成并广播，不是 counter RNG |

## 解析与多 run

`Run::execute_run_in`（run.cu:175）用 `get_tokens`（utilities/error.cu:124）空白分词，
首字符 # 的 token 起截断；无 shell 引号，最多 32 tokens。`parse_one_keyword`（run.cu:339）
是命令集合入口，`kspace/dftd3` 有静默占位；DMG-MD 不复制静默行为。

参考 `Force::parse_potential`（force/force.cu:75）按可见 GPU 数选 NEP 或 NEP_MULTIGPU；
后者在单进程中枚举多设备，不等于一 rank 一 GPU。NEP3 在该入口白名单与 NEP 构造器
版本分支不一致，不能据入口名宣称支持。

参考多 run 保留 box/atom/potential vector/time_step/global_time；后续 potential 会 append。
Measure::finalize 清 properties，Integrate::finalize 清 fix/move/deform，Velocity::finalize
清 correct_velocity；max_distance_per_step 段末置 0（run.cu:336，初始为 -1）。
run0/负步数和缺省 ensemble 的完整可观察边界尚未建立 executable corpus。
DMG-MD 只复现已声明范围，具体状态表见[兼容矩阵](../standards/compatibility-matrix.md)。

## 力与 timestep 主链

`Run::perform_a_run`（run.cu:211）在循环前初始化 integrator/measurement 并求一次初始力。
所以 step0 VV1 读初始 force；输出按 step+1，无 step0 dump。

```text
correct_velocity → adaptive timestep → VV1
→ Force::compute：wrap → clear → NEP
→ VV2 → global thermo（参考为单 GPU 全 N）→ 可选 Berendsen 缩速
→ measurement
```

| 阶段 | 锚点 | 读写/数值语义 |
| --- | --- | --- |
| wrap/clear | force/force.cu:424 gpu_apply_pbc；:314 initialize_properties；:771 Force::compute | 全 N position wrap、force/PE/9 virial 清零 |
| VV | integrate/integrate.cu:332/377；ensemble.cu:176 | v += dt/2*f/m，VV1 另 r += dt*v；参考全 N |
| 修速 | main_gpumd/velocity.cu:210/273 | 全局线/角动量修正，step%interval 含 step0 |
| nvt_ber | integrate/ensemble_ber.cu | VV2/thermo 后按瞬时温度缩速；不是严格 canonical sampler |
| thermo | integrate/ensemble.cu:432 | 动能/PE/六 stress，温度 DOF 受 fixed/move 等影响 |
| measurement | measure/measure.cu | 按属性顺序；thermo/XYZ append、restart 覆盖 |

large-box 由 `NEP::compute_large_box`（force/nep.cu:978）编排：

```text
Verlet (cutoff+skin) → typewise filter
→ descriptor/PE/Fp/sums → radial force
→ angular directed partial → many-body reverse-edge gather → optional ZBL
```

radial 读邻居 Fp，many-body 读反向 partial，故 owned final force 需要邻居中心的完整产物。
这能用深位置 halo 重算，也能另设计中间场 forward exchange，不能只裁 N1/N2。
small-box（nep.cu:1141、nep_small_box.cuh）显式多 image、Newton atomic scatter 到邻居，
分布式映射需 image 身份和 reverse force/virial exchange。逐 kernel 证据在[kernel 清单](kernel-inventory.md)。

## 输出与恢复

输出文件名、精度、单位、张量顺序和触发见[兼容矩阵](../standards/compatibility-matrix.md)，
不在审计复制。reference restart 为 species/position/mass/velocity/group 的 XYZ，
不含 time、RNG、thermostat、force、PE、virial 或显式 gid；下次作为 model.xyz 读取，
不自动改名。跨 rank 可恢复保存字段，不保证未保存状态连续。

## DMG-MD 当前对应关系

构建仅显式列举本仓库文件；三个 reference checkout 均只读。

| 层 | 当前文件 / 职责 |
| --- | --- |
| compat | src/gpumd_compat：最小数值子集，Origin 注释、保留数学/精度 |
| input / entry | src/model_parser.cpp、run_parser.cpp、dmgmd_main.cpp；typed IR 与来源错误，每 rank 解析再核对 fingerprint |
| M1 / formatter | src/runtime.cu：replicated-full、indexed ownership、共享兼容输出 |
| shared | src/runtime_internal.hpp：公共 helper、RankIoIsolation 与 formatter 声明 |
| M2a | src/domain_runtime.cu、include/dmgmd/domain_layout.hpp：local owned/ghost、统一 rebuild、halo/迁移 |
| NEP 分区 | src/gpumd_compat/domain_partition*、NEP::compute_domain：普通步 mask 与 oracle |
| MPI | src/mpi_runtime.cu、include/dmgmd/mpi_runtime.hpp：GPU binding、collective/p2p、halo lifecycle；spatial_ownership.hpp 提供 M1 owner |

M2a、migration、halo 与 local neighbor 已在当前闭包内，不能再写成“未来组件”。
M2b、3D 和实际 overlap 仍为[剩余计划](../plans/domain-decomposition.md)。runtime 合同见
[MPI](../standards/mpi-runtime.md)、[布局](../standards/data-layout.md)、[缓存](../standards/cache-validity.md)。
