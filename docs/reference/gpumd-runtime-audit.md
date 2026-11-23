# GPUMD runtime 源码审计

本页沿着所审计版本的一次 GPUMD 模拟追踪输入、求力、积分与输出。范围限于普通单 bead NEP/NEP-ZBL、NVE 和 `nvt_ber`，核对日期为 2026-10-16。表内上游 src 路径和行号均指提交 `9d23496e41319b9e2af5221a7df6285387401d1e`。L20 的短程 golden 由普通 GPUMD 5.9.1 生成。[参考版本](../standards/compatibility-matrix.md)

参考程序大多用全体系原子数 N 作数组步长。DMG-MD 复现其数学和输出格式，自行实现 MPI 并行。阅读下表时，第二列描述上游行为，第三列说明分布式实现要满足的条件。

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
| allocate_memory_gpu / read_xyz.cu:532 | 全 N type/mass/charge/position 与输出 | 局部域分解另按 local_count 分配 |
| Velocity::initialize / main_gpumd/velocity.cu:312 | 全局 CPU 随机序列与动量修正 | 当前 root 按全局顺序生成并广播，不是 counter RNG |

## 解析与多 run

`Run::execute_run_in`（run.cu:175）调用 `get_tokens`（utilities/error.cu:124）按空白分词。某个 token 以 `#` 开头时，其后文字均为注释。输入不解释 shell 引号，每条命令最多 32 个 token。`parse_one_keyword`（run.cu:339）分派命令。上游为 `kspace/dftd3` 留了静默占位。DMG-MD 对未支持命令明确报错。

上游 `Force::parse_potential`（force/force.cu:75）按可见 GPU 数选 NEP 或 `NEP_MULTIGPU`。后者由一个进程枚举多设备。NEP3 虽列在这个入口的白名单里，NEP 构造器却没有相应的可用版本分支；仅凭白名单无法证明 NEP3 可运行。

上游多段 run 会保留 box、atom、potential vector、time_step 和 global_time。后续 potential 声明会追加到列表。段末分别由 `Measure::finalize` 清除 properties、`Integrate::finalize` 清除 fix/move/deform、`Velocity::finalize` 清除 correct_velocity。`max_distance_per_step` 初始为 -1，段末置为 0（run.cu:336）。

零步和负步数，以及缺省 ensemble 的完整行为仍缺少可执行测试语料。DMG-MD 只承诺已验证的部分。[当前多段运行状态](../standards/compatibility-matrix.md)

## 力与 timestep 主链

`Run::perform_a_run`（run.cu:211）在循环前初始化 integrator/measurement 并求一次初始力。
因此第 0 步的速度 Verlet 前半步（VV1）已有力可读。输出间隔按已完成的步数 step+1 判断，初始 step 0 不输出快照。

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

计算中心原子的径向力要读取邻居的 `Fp`，多体力还要读取反向边的部分力。把计算中心范围 N1/N2 裁到本 rank 负责的原子，会漏掉这些邻居中间量。局部域分解模式交换两跳位置边界，在本地重算中间量。另一种分阶段中间场交换方案尚未实现。

小盒路径（nep.cu:1141、nep_small_box.cuh）显式枚举同一原子的多个周期像，并以原子加法按牛顿第三定律向邻居写回。要分布式执行该路径，还须区分周期像，并把写在副本上的力和 virial 反向传回负责原子的进程。[核函数和依赖清单](kernel-inventory.md)

## 输出与恢复

历史 GPUMD 的 restart 使用 XYZ，保存 species、position、mass、velocity 和 group。
time、RNG、thermostat、force、PE、virial 和显式 gid 不存盘。新作业仍从
`model.xyz` 读取，因此恢复时需将 restart 文件用作该输入。
DMG-MD 的浮点格式、触发步和跨 rank 恢复见[兼容矩阵](../standards/compatibility-matrix.md)。

## DMG-MD 当前对应关系

DMG-MD 构建仅使用本仓库文件。外部 reference 只供审计和验证。

| 层 | 当前文件 / 职责 |
| --- | --- |
| compat | src/gpumd_compat：最小数值子集，Origin 注释、保留数学/精度 |
| input / entry | src/model_parser.cpp、run_parser.cpp、dmgmd_main.cpp；typed IR 与来源错误，每 rank 解析再核对 fingerprint |
| 全量复制模式和输出 | `src/runtime.cu` 按索引确定原子归属，提供两种模式共用的兼容格式写出函数（`replicated-full` 为模式英文名） |
| shared | src/runtime_internal.hpp：公共 helper、RankIoIsolation 与 formatter 声明 |
| 局部域分解模式 | src/domain_runtime.cu、include/dmgmd/domain_layout.hpp：local owned/ghost、统一 rebuild、halo/迁移 |
| NEP 分区 | src/gpumd_compat/domain_partition*、NEP::compute_domain：普通步 mask 与 oracle |
| MPI | src/mpi_runtime.cu、include/dmgmd/mpi_runtime.hpp：GPU binding、collective/p2p、halo lifecycle；spatial_ownership.hpp 定义全量复制模式的原子归属 |

DMG-MD 已实现局部域分解、迁移、halo 交换和局部邻居表。可选的重叠计算已实现并验收（O4），默认关闭。分阶段中间场交换方案和三维分解仍待实现。[扩展计划](../plans/domain-decomposition.md)、[MPI 标准](../standards/mpi-runtime.md)、[数据布局](../standards/data-layout.md)、[缓存规则](../standards/cache-validity.md)
