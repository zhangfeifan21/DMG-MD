# 输入/输出兼容矩阵

已有 GPUMD 输入可先按下表确认势和系综，再核对 `model.xyz` 的属性、`run.in` 命令及需要的输出。矩阵描述 2026-11-20 核对的程序，显式局部 NPT 的启动与停止行为于 2026-11-27 复核。输入虽然能通过解析，但专项测试尚未确认的边界仍记为 UNKNOWN。

兼容层的源码来自 GPUMD 提交 `9d23496e41319b9e2af5221a7df6285387401d1e`。除特别注记外，上游 `src/force`、`src/measure` 等路径和行号均指该提交。L20 的短程 golden 改用 Spack GPUMD 5.9.1（`11ba6fbdb4d71b2cdb351b39972fa057521585b3`）。可执行文件 SHA-256 和输入哈希见 [baseline manifest](../../tests/baseline/manifest.json)。历史验收以每份报告记录的版本为准。

DMG-MD 启动入口是 `src/dmgmd_main.cpp`。命令和模型分别由 `src/run_parser.cpp`、`src/model_parser.cpp` 解析，两种并行模式分别由 `src/runtime.cu`、`src/domain_runtime.cu` 执行。

## 1. 产品与并行范围

每个作业使用一个势文件，运行单 bead 经典分子动力学模拟。当前接受普通 NEP4/5 和各自的 ZBL 变体。NEP3、temperature、dipole、polarizability、charge 模型和多势组合均拒绝。

LJ 和 EAM/alloy 仍待实现。生产版要求多卡必须有局部求力实现，这两种势先交付通过数值验收的单卡版本。Tersoff、Langevin 和 BAOAB 是后续候选。[生产多卡路由](architecture-decisions.md#production-domain-routing)、[开发进度](../plans/dmg-md-v1.0-progress.md)

| 系综 | 全量复制模式 | 局部域分解模式 |
| --- | --- | --- |
| NVE、`nvt_ber`、`nvt_nhc`、`nvt_bdp` | 已验收，见[系综标准](ensembles.md) | 满足盒和 slab 几何判据时启用，已有单节点验收 |
| `npt_ber` | 默认路径，已验收各向同性、正交全周期 NEP | 显式实验路径，范围见[局部 NPT 标准](domain-npt.md) |
| `npt_scr` | 默认路径，已通过 G-B 的 216 原子 NEP-C 冻结统计矩阵 | 显式实验路径，已通过独立的 5760 原子 NEP4-C 冻结统计矩阵 |

单 rank（P1）始终使用全量复制模式。多 rank（P>1）会拒绝三斜盒及有非周期方向的盒。对于正交全周期盒，还须通过 NEP 大盒和分片宽度检查，才有资格进入局部域分解模式。多卡作业需要局部域分解但局部资格不足时，程序在首份科学输出前退出，内部全量复制对照有显式开关。[局部资格与恢复](domain-npt.md)
NVE/NVT 的 P1 路径没有这两项分布式几何守卫，完整的三斜和非周期输入兼容性未验收。
NPT 在所有 rank 数均要求正交全周期盒。仅显式设置 `DMGMD_DOMAIN_NPT=rebuild|affine`
才允许试用局部 NPT。一旦进入局部积分，候选盒失格时所有 rank 都在提交前终止。实际分派见 [MPI runtime](mpi-runtime.md)。

两套 SCR 统计证据分别受原子数、势、温压、步长、rank、后端及路由条件约束。
NEP5/ZBL 只有短程 NPT 合同证据。原注册 18 个 formal look 的总体 INCONCLUSIVE 结论保留。
完整矩阵见[控压器标准](barostat.md)和[局部 NPT 标准](domain-npt.md)。

第 4 节列出全部 `run.in` 命令。白名单外的命令或系综会报带来源位置的 `unsupported`，
未知命令另带 `unknown`。当前缺少键合相互作用和刚性约束、长程静电、多势混合、
能量最小化、任意变盒、外场和增强采样。RDF、MSD、声子、PIMD、MC 等命令也在白名单之外。

## 2. 文本与 model.xyz

每行 `run.in` 只写一条命令，按空白分词，区分大小写，不解释 shell 引号或转义。只有 token 的第一个字符是 `#` 时才从该 token 起截断注释。`abc#def` 会保留。每条非空命令最多 32 个 token。程序先解析完整输入，再开始执行。

`model.xyz` 第一行只能有一个整数 N，要求 N≥2。第二行省略 `pbc` 时，默认 T/T/T。显式指定时恰好给三个 T/F，忽略大小写。`Lattice` 须有九个有限值。奇异盒的错误行为尚未专项验证。

第二行允许等号周围有空白，也接受引号分组。schema 和 property 名忽略大小写，元素名区分大小写。`Properties` 由 `name:type:width` 三元组组成，width>0，必须包含 species 和 pos。每行原子数据的总列数须与声明相同。额外属性占列，但程序不解析其值。程序只读取 N 行原子，忽略其后多余行。

当前解析器没有严格核验每种标准属性的类型和宽度。畸形 schema 的完整输入语料和安全边界仍待验证。

```text
N
pbc="T T T" Lattice="ax ay az bx by bz cx cy cz" Properties=species:S:1:pos:R:3
<species> <x> <y> <z>
... N atom lines
```

| property | 列数 | 当前语义 / 缺省 |
| --- | --- | --- |
| species | 1 | 必需；精确匹配 potential header，定义 type |
| pos | 3 | 必需；double Å |
| mass | 1 | 可选，单位 amu，按 double 存储。显式值须 >0。省略时查 GPUMD 元素表，查不到默认质量则报 InputError |
| charge | 1 | 可选，按 float 存储，默认 0。可输出到 XYZ，普通 NEP 求力不使用该字段 |
| vel | 3 | 可选；Å/fs 输入乘 10.18051；存在 vel 时 velocity 命令不重生成 |
| group | M | 可选；M 个 int labels，0≤label<N；允许缺失 group ID，形成空组 |

上游的语法实现位于 `src/model/read_xyz.cu`。本程序由 `model_parser.cpp` 报错，报错文字可能与上游 `std::map::at` 异常不同。输入上限、奇异盒与恶意 schema 的完整测试语料尚未建立。

## 3. 普通 NEP/NEP-ZBL 文件

### 3.1 头部语法

按模型版本选择首行。`_zbl` 版本还需给出 ZBL 行。以下两种 `cutoff` 写法分别给各元素共用的半径和按元素指定的半径。

```text
nep4|nep5|nep4_zbl|nep5_zbl  num_types  symbol_0 ... symbol_(T-1)
[zbl rc_inner rc_outer [typewise_factor]]
cutoff rc_radial rc_angular MN_radial MN_angular
# 或 typewise:
cutoff rR_0 rA_0 ... rR_(T-1) rA_(T-1) MN_radial MN_angular
n_max n_max_radial n_max_angular
basis_size basis_size_radial basis_size_angular
l_max L has_q_222 has_q_1111 [has_q_112] [has_q_123] [has_q_233] [has_q_134]
ANN num_neurons 0
<num_para + descriptor_dim lines, first token is one real each>
[<10*T*(T+1)/2 flexible-ZBL scalar lines>]
```

### 3.2 版本和 type

| 项 | 实际行为 |
| --- | --- |
| version token | DMG-MD 入口的 `load_potential_metadata` 只接受 `nep4`、`nep4_zbl`、`nep5`、`nep5_zbl`。上游 `NEP::NEP()` 的其他分支见 `src/force/nep.cu:108-143` |
| `num_types` | int；第一行 token 总数必须正好 `2+num_types` |
| symbols | 定义 model species→type 映射；同时在内置元素表查 Z。未知 symbol 的 Z 保持 0，代码不报错 (`nep.cu:157-166`) |

参考入口差异：`Force::parse_potential()` 的白名单还列出 `nep3*` (`src/force/force.cu:128-138`)，但 `NEP::NEP()` 随即拒绝这些版本。DMG-MD 当前只接受普通 NEP4/5 和 ZBL，明确拒绝 NEP3；NEP3 的参考可用性未证明。

### 3.3 ZBL 行

- 仅 `_zbl` version 有此行。
- token 数必须是 3 或 4；代码读取 token 1/2/3，但**不验证 token 0 真的是 `zbl`**。
- `rc_inner==0 && rc_outer==0` 选择 flexible ZBL，随后在所有常规模型参数后读取每个无序 type pair 的 10 个标量。
- 第四项启用 covalent-radius typewise outer cutoff factor；没有第四项为固定 inner/outer。
- 本次代码未发现 inner/outer 正值、顺序或 outer 不超过 angular cutoff 的校验。
- ZBL kernel 实际遍历 angular neighbor list，因此合法通信/候选范围不能仅从 `rc_outer` 推断。

### 3.4 cutoff 与容量

- uniform 行正好 5 tokens；typewise 行正好 `2*T+3` tokens。
- 代码不验证首 token 是 `cutoff`。
- cutoff 单位为 Å；type-pair cutoff 在 kernel 中取两个 type 值的算术平均。
- 全局邻居表按各类型最大的径向截断半径 `rc` 建表。后续先按径向半径过滤，剩下的邻居才按角向半径过滤。即使输入的角向半径较大，径向半径外的原子也不会进入角向表。程序没有显式检查两种半径的大小关系。
- `MN_radial>819` 明确报错；`MN_angular` 上限以及二者正值未见完整校验。
- device 容量取 `ceil(MN_input*1.25)`，`neighbor.out` 打印放大后的最大值。

### 3.5 descriptor/ANN 参数布局

- `n_max`、`basis_size` 各必须正好 3 tokens，但不验证 keyword。
- `l_max` 至少有 4 个 token。代码最多读取到第 8 个参数，忽略额外 token。各 `has_q_*` 按 int 读取，没有检查值是否限于 0/1。
- `ANN` 行恰有 3 个 token，不检查关键字，也不检查第三项是否为 0。

对上述版本和参数布局，描述符维数决定势文件后续要读多少行。T 表示元素类型数。

- `dim=(nmaxR+1)+(nmaxA+1)*num_L`。
- NEP4 ANN 参数数：`(dim+2)*num_neurons*num_types+1`。
- NEP5 ANN 参数数：`((dim+2)*num_neurons+1)*num_types+1`。
- descriptor 参数数：`T^2*((nmaxR+1)*(basisR+1)+(nmaxA+1)*(basisA+1))`。
- 随后读取 `num_para+dim` 行（ANN+descriptor+q_scaler），每行只用第一个 token，转换为 float 后存入 GPU 缓冲区。
- 对缺行/空行/额外字段的稳定错误文字尚未用 executable test 证明，标记 `UNKNOWN`。

## 4. 当前 run.in 命令

| 命令 | 支持语法 | 校验 / 语义 |
| --- | --- | --- |
| potential | `potential FILE` | 必须在 run 前；第二条 potential 或第三方向参数拒绝 |
| velocity | `velocity T [ANY_TOKEN SEED]` | T>0 K；四 token 形式读取最后的 int seed，第三 token 不校验 |
| time_step | `time_step DT [MAX_DISTANCE]` | DT 为有限实数 fs，默认 1 fs；可选最大距离>0 Å，按全局 owned 最大速度限步。NHC/BDP/NPT 要求固定有限正步长且不接受 MAX_DISTANCE；旧系综负 DT 边界未验收 |
| ensemble | `ensemble nve` / `ensemble nvt_ber T1 T2 TAU` | T1/T2>0 K，TAU≥1；目标按 step/steps 插值；run 必须已有 ensemble |
| ensemble | `ensemble nvt_nhc T1 T2 Tdamp` / `ensemble nvt_bdp T1 T2 Tdamp` | 温度有限且>0 K，Tdamp 有限且≥1，单位步数；目标同上。root 链/RNG、段首重置、默认 seed 见[系综标准](ensembles.md) |
| ensemble | `ensemble npt_ber T1 T2 Tdamp P B tau_p` | 8 个 token，各向同性。T 以 K 输入，Tdamp/tau_p 按步数，P/B 以 GPa 输入。P 可负，B 须有限且 >0。限 NEP/NEP-ZBL 和正交全周期盒，默认采用全量复制模式。实验局部设置见上文。[Berendsen 验收（B2）](barostat.md) |
| ensemble | `ensemble npt_scr T1 T2 Tdamp P B tau_p` | G-B PASS 仅限上述全量复制模式的 216 原子冻结矩阵。[5760 原子局部实验](domain-npt.md)单独通过。原注册 18 个分析节点总体仍为 INCONCLUSIVE。其他势、RDMA、跨节点和普遍严格 NPT 均不在统计范围内。见[最终确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)与[NPT矩阵](../status/validation/npt-validation.md#npt-matrix-20261104) |
| run | `run STEPS` | int；0/负步数 parser 未拒绝，未作为验证过的动力学兼容行为 |
| dump_thermo | `dump_thermo INTERVAL` | int>0；thermo.out append |
| dump_xyz | `dump_xyz INTERVAL FILE [OPTIONS/QUANTITIES]` | interval>0；见下表；同段可多条 |
| dump_restart | `dump_restart INTERVAL` | int>0；restart.xyz 覆盖 |
| correct_velocity | `correct_velocity INTERVAL [GROUPING_METHOD]` | interval≥10，method 有效；step%interval==0，含段内 step0；与 NHC/BDP/NPT 组合明确拒绝 |

输入未提供 vel 时，rank 0 默认按 300 K 初始化速度并广播。随机初始化若未指定 seed，多次运行可能给出不同结果。显式 `velocity` 命令也在 rank 0 按输入行序生成速度，随后作全局动量修正。

每段 `run` 先求初始力。完成的步数 `step+1` 达到输出间隔才会输出，段内 step 0 没有初始快照。同一段重复声明 dump_thermo 或 dump_restart 会报错。作业结束时若还有没有后续 run 的 dump 声明，也会报错。

NHC、BDP 和两种 NPT 在写科学数据前预检整份输入。分区、重叠、调度和 trace 须使用[系综标准规定的默认值](ensembles.md#并行限制与通信)。

### dump_xyz 选项

| token | 参数 / 行为 |
| --- | --- |
| group | METHOD ID，有效非负分组/ID，只输出所选组；重复拒绝 |
| precision | single（默认 `%.9g`）或 double（`%.17g`）；重复拒绝 |
| mass / charge | 输出 mass:R:1 / charge:R:1 |
| velocity / force / potential | vel:R:3 / forces:R:3 / energy_atom:R:1 |
| unwrapped_position | unwrapped_position:R:3 |
| virial | 每原子 virial:R:9 |
| group_labels | 每个 grouping method 一列；无 group 拒绝 |
| bec | 当前普通 NEP runtime 明确 unsupported |

参数可混排，列固定按 mass、charge、velocity、force、potential、unwrapped、virial、group
排列，不按参数先后。未知参数报错。若 FILE 以 `*` 结尾，程序把星号换成 `step+1`，每帧分别覆盖对应文件。其他名称采用追加写入。整体系按输入行的全局 ID 顺序输出，分组也保留相对行序。

## 5. 多段 run 状态

| 状态 | DMG-MD 后续 run |
| --- | --- |
| position/velocity/type/mass/group、box、global_time | 保留；NPT 在步末同步更新盒与 wrapped position |
| potential | 保留同一实例，不支持参考程序的多势追加 |
| timestep / ensemble | 保留最近配置，直到覆盖 |
| NHC 链 / BDP 与 SCR RNG、Gaussian cache | 每段新建；NHC 质量固定于该段 T1；固定 seed 的 BDP/SCR 在新段重新开始注册流 |
| dump / correct_velocity / maximum_distance | 段末清空，后段须重新声明 |
| ownership/cache | 两种模式都延续 owner/cache。局部域分解段首会先处理待更新的盒或布局，再计算初始力。有效缓存可以复用 |
| 文件 | thermo/单文件 XYZ append，restart 覆盖 |

参考原程序的 fix/move/deform 状态不属于当前支持合同，见[GPUMD 审计](../reference/gpumd-runtime-audit.md)。

## 6. 输出文件合同

### 6.1 `thermo.out`

每一段启用 `dump_thermo I` 时，程序先追加以下表头。

```text
# dump_thermo I
# format_version 1
# num_atoms N
# dt_output <I*dt> fs
# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz
```

格式由 `src/runtime.cu` 的 `write_thermo_header` 和 `write_thermo_row` 写入。历史上游锚点是 `src/measure/dump_thermo.cu:49-72`。每个数据行有 18 个 `%20.10e` 字段。

| 列 | 单位/语义 |
| --- | --- |
| `T` | K，`Σ_owned m*v²/(3N*kB)`；无 fixed/move group，不扣质心 |
| `KE` | eV，`1.5*N*kB*T` |
| `PE` | eV，全体系逐原子势能之和 |
| `sxx syy szz syz sxz sxy` | GPa，virial 与动能项求和后除以体积，再乘 160.2177 |
| `a,b,c` 共 9 列 | Å，晶格向量逐个输出 |

热力学量在速度 Verlet 后半步（VV2）结束时采样，此时还没有缩放速度或盒长。同一步 NPT 输出中，压力和 virial 属于缩放前的状态。盒矩阵和 XYZ/restart 的 wrapped positions 属于缩放后的状态。速度只由恒温器缩放，控压器不改变速度，也不缩放 `unwrapped_position`。输出温度按 3N 定义，统计实验另行记录有效自由度。[控压器和输出时点](barostat.md)

### 6.2 `dump_xyz`

每一帧均为扩展 XYZ 格式。第一行给出全体系或所选组的原子数。第二行从 `Time=%.8f` 开始，时间单位 fs，随后写 pbc、Lattice、总能量、总 virial、stress 和 Properties。每行原子默认写 `species pos_x pos_y pos_z`，可选属性按前述固定顺序追加。

第二行的能量和 virial 用 eV，应力用 eV/Å³。XYZ 不使用 thermo 写 GPa 时的转换因子。virial 和 stress 均以对称 3×3 矩阵的 9 个值输出。逐原子 virial 按内部索引 `0,3,4,6,1,5,7,8,2` 写出。上游依据为 `src/measure/dump_xyz.cu:184-238`。

整体系输出沿用 `Atom` 原数组行序。分组使用 `Group::cpu_contents`，它按同一顺序扫描原数组。MPI 输出通过稳定的全局 ID 恢复这些顺序。

### 6.3 `restart.xyz`

- 固定名字 `restart.xyz`，匹配步以 `w` 覆盖。
- 第一行写原子数，第二行写 `pbc`、`Lattice` 和 `Properties`。
- 当前 L20 工作树使用 `%.17g` 写晶格、位置、质量和速度，与 GPUMD 5.9.1 的
  `src/measure/dump_restart.cu` 一致。历史短程 golden 的旧格式为 `%g`。
- 无 group 时使用 `species:S:1:pos:R:3:mass:R:1:vel:R:3`。
- 有 group 时追加 `:group:I:M` 并输出每种 grouping method 的 label。
- 速度从内部单位除以 10.18051，单位为 Å/fs。
- time、force、PE、virial、unwrapped position、charge、显式 global ID 及控制器/RNG 状态均未存盘。

将 `restart.xyz` 用作新的 `model.xyz` 可以在另一 rank 数下从保存的字段启动。此操作需要
配套的 `run.in` 和势文件。新作业重新生成原子 ID、力、并行布局和控制器状态。
轨迹连续性不在 restart 合同内。NPT Berendsen 单长 run、分段 run 和文件 restart 的实际
差异见 [B2 报告](../status/validation/npt-validation.md#npt-ber-20261103)。
关于随机状态，见[系综标准](ensembles.md#分段输出与-restart)和[控压器标准](barostat.md)。

### 6.4 隐式 neighbor.out

普通大盒 NEP 每 1000 次求力向 `neighbor.out` 追加一条邻居统计，首次调用（call0）也记录。在全量复制模式下，rank 0 写可见文件，其他 rank 写本机私有临时目录。局部域分解模式在本次类型对邻居表生成后，对依赖中心的两个局部最大值分别执行 MPI_MAX，仅 rank 0 写出。空域贡献零。分区验证的重放不会重复写日志（O2）。[I/O 隔离和通信计数](mpi-runtime.md#io-与-nep_multigpu)

## 7. 单位汇总

| 量 | 用户输入/输出 | GPUMD 内部值或转换方法 |
| --- | --- | --- |
| length/cutoff | Å | Å |
| time/timestep | fs | 除 `10.18051` |
| velocity | Å/fs | 输入乘、输出除 `10.18051` |
| temperature | K | K，`kB=8.617343e-5 eV/K` |
| mass | amu | 与内部自然时间单位配套使用 |
| energy | eV | eV |
| force | eV/Å | eV/Å |
| virial | eV | eV |
| thermo stress | GPa | 内部 eV/Å³ 乘 `160.2177` |
| XYZ stress | eV/Å³ | 不转换 |

常数证据：`src/utilities/common.cuh`。

## 8. 支持声明的变更

新增支持须有参数/错误、多 run、单位/文件/格式/触发步、唯一 owned 贡献及 reference golden
验证；比较层级见[Golden Test](golden-test-standard.md)。当前已执行范围见[状态](../status/current.md)，
malformed/边界缺口见[待办](../plans/risk-and-backlog.md)。
