# GPUMD 输入/输出兼容性矩阵

类别：现行标准。实际支持行为以生产代码和自动化测试为准。

## 1. 兼容性声明边界

本文记录 DMG-MD 当前已经实现的兼容合同，数值和文件行为以
`../gpumd-reference` commit `9d23496e41319b9e2af5221a7df6285387401d1e` 为唯一参考。
表中未特别注明 DMG-MD 的源码位置时，`src/...` 均指该锁定参考树；当前实现入口位于
`src/run_parser.cpp`、`src/model_parser.cpp`、`src/runtime.cu` 和 `src/gpumd_compat/`。声明为支持的路径由
`tests/baseline`、`tests/mpi` 和 `tests/long_nve` 分层验证；仍为 `UNKNOWN` 的行为不构成兼容承诺。

状态含义：

- `SUPPORTED`：当前实现并纳入 golden/MPI 验证；表内明确排除的子选项除外。
- `PARSE_ONLY_UNSUPPORTED`：必须识别命令/格式并给出明确 unsupported 或迁移错误，不能忽略。
- `FUTURE`：合理的后续功能；当前 parser 明确拒绝。
- `IRRELEVANT`：不属于 DMG-MD 产品方向或当前范围，仍要识别并拒绝。
- `UNKNOWN`：代码证据不充分、入口互相矛盾或需要运行实验。

## 2. 通用文本规则

### 2.1 `run.in`

- 文件名固定为工作目录下 `run.in`；`Run::execute_run_in()` 直接打开该名字 (`src/main_gpumd/run.cu:175-207`)。
- 一行一条命令；`get_tokens()` 仅按 C++ stream 空白切分 (`src/utilities/error.cu:124-141`)。
- 从第一个首字符为 `#` 的 token 起丢弃余下 token；`abc#def` 不是注释，`#comment` 是。
- 命令和选项均大小写敏感。
- 没有 shell 引号/转义语义；带空格的文件名无法作为一个 token。
- 非空命令行最多 32 个 token；实际检查允许正好 32 个，错误文字却写“less than 32” (`run.cu:339-348`)。
- 未知首 token 由 `PRINT_KEYWORD_ERROR` 终止。`kspace`、`dftd3` 在当前 dispatcher 中是静默占位，但 DMG-MD 禁止复制这种行为。

### 2.2 `model.xyz` 第二行

第二行使用 `read_xyz.cu` 自己的解析路径，而不是通用 run tokenizer：它删除 `=` 周围空白、处理双引号分组，并把整行转为小写 (`src/model/read_xyz.cu:155-310` 和 `src/utilities/error.cu:51-122`)。因此 schema 名称不保留大小写；atom 数据行本身不转小写。

### 2.3 NEP 文件

NEP parser 按行调用 `get_tokens()`，不执行 `run.in` 的 `#` 截断。固定头部每项参数在一行中；之后每个 ANN/descriptor/scaler/ZBL 标量各占一行，代码只读取该行的第一个 token (`src/force/nep.cu:352-375`)。

## 3. `model.xyz` 实际语法

### 3.1 最小形式

```text
N
pbc="T T T" Lattice="ax ay az bx by bz cx cy cz" Properties=species:S:1:pos:R:3
<species> <x> <y> <z>
... exactly N atom lines
```

证据主入口：`initialize_position()` (`src/model/read_xyz.cu:482-529`)。

### 3.2 第一行

| 项 | 实际规则 | 错误 |
| --- | --- | --- |
| `N` | 恰好一个 token，可解析为 int | token 数不是1、不是int、`N<2` 均终止 (`read_xyz.cu:141-153`) |

GPUMD 使用 int 保存 N；超大 N 的上限和溢出行为为 `UNKNOWN`。

### 3.3 第二行字段

| 字段 | 语法/数量 | 默认值 | 语义/校验 |
| --- | --- | --- | --- |
| `pbc` | `pbc="T|F T|F T|F"`，引号内3个单字母 token | 三方向 `T` | 第二行先转小写，随后代码只接受 `t`/`f`；每项对应晶格 `a,b,c` 方向。缺项的稳定错误行为需运行测试 |
| `Lattice` | `Lattice="9 reals"` | 无 | 必需；外部顺序 `ax ay az bx by bz cx cy cz`。内部转置为列存 `h[0,3,6,1,4,7,2,5,8]` (`read_xyz.cu:202-239`) |
| `Properties` | `name:type:width` 三元组串 | 无 | 必须至少最终找到 `species` 和 `pos`；未知属性允许存在并贡献列宽 |

代码调用 `Box::get_inverse()`，但未在本次审计中发现对零体积/奇异 lattice 的明确输入错误；该错误行为标记 `UNKNOWN`。

### 3.4 Properties

支持名称及使用方式：

| property | 期望物理列 | 是否必需 | host类型/单位 | 缺省 |
| --- | ---: | --- | --- | --- |
| `species` | 1 | 是 | string；必须匹配 potential 首行 symbol，顺序定义 type ID | 无 |
| `pos` | 3 | 是 | double，Å | 无 |
| `mass` | 1 | 否 | double，amu；必须 `>0` | 由内置 `MASS_TABLE.at(symbol)` |
| `charge` | 1 | 否 | float；单位从代码本身不能完整证明，`UNKNOWN` | 0 |
| `vel` | 3 | 否 | 输入 Å/fs；乘 `TIME_UNIT_CONVERSION=10.18051` 变为内部速度 | 由温度初始化 |
| `group` | `M` | 否 | 每列一个 grouping method 的 int label | 无 grouping method |

重要的“实际而非理想”行为：

- parser 读取 schema 的 `type` 字符和 `width`，但没有验证 `species:S:1`、`pos:R:3` 等标准 type/width 是否恰当；它按声明 width 计算后续列偏移。
- atom 数据行必须恰好有 schema 所有 property width 的总 token 数；未知 property 的列也必须出现，但值不解析。
- reader只循环读取N条atom行；N条之后的额外行是否被用户视为错误没有显式检查，实际会被忽略。
- species 匹配区分大小写，必须与 potential 第一行完全相同。
- group label 必须是 int，且 `0<=label<N`；group 数由 `max(label)+1` 得出 (`read_xyz.cu:370-400`)。它没有要求 label 连续，缺失 ID 会形成 size 0 group。
- 默认质量查询未知 symbol 时使用 `std::map::at` 的异常可观察行为，而不是专门格式化的 GPUMD input error；需 executable test 才能锁定。
- `has_velocity_in_xyz` 是整个作业状态；只要 model 有 `vel`，后续 `velocity` 命令不会重新随机初始化。

## 4. 普通 NEP/NEP-ZBL 文件

### 4.1 头部语法

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

### 4.2 版本和 type

| 项 | 实际行为 |
| --- | --- |
| version token | `NEP::NEP()` 接受普通 `nep4`, `nep4_zbl`, `nep5`, `nep5_zbl`；参考代码还接受不在当前产品范围的 NEP4 temperature/dipole/polarizability (`src/force/nep.cu:108-143`) |
| `num_types` | int；第一行 token 总数必须正好 `2+num_types` |
| symbols | 定义 model species→type 映射；同时在内置元素表查 Z。未知 symbol 的 Z 保持 0，代码不报错 (`nep.cu:157-166`) |

入口矛盾：`Force::parse_potential()` 的白名单还列出 `nep3*` (`src/force/force.cu:128-138`)，但 `NEP::NEP()` 随即拒绝这些版本。DMG-MD 只承诺经过 golden test 的 NEP4/5 普通和 ZBL；NEP3 状态为 `UNKNOWN/PARSE_ONLY_UNSUPPORTED`。

### 4.3 ZBL 行

- 仅 `_zbl` version 有此行。
- token 数必须是 3 或 4；代码读取 token 1/2/3，但**不验证 token 0 真的是 `zbl`**。
- `rc_inner==0 && rc_outer==0` 选择 flexible ZBL，随后在所有常规模型参数后读取每个无序 type pair 的 10 个标量。
- 第四项启用 covalent-radius typewise outer cutoff factor；没有第四项为固定 inner/outer。
- 本次代码未发现 inner/outer 正值、顺序或 outer 不超过 angular cutoff 的校验。
- ZBL kernel 实际遍历 angular neighbor list，因此合法通信/候选范围不能仅从 `rc_outer` 推断。

### 4.4 cutoff 与容量

- uniform 行正好 5 tokens；typewise 行正好 `2*T+3` tokens。
- 代码不验证首 token 是 `cutoff`。
- cutoff 单位为 Å；type-pair cutoff 在 kernel 中取两个 type 值的算术平均。
- `rc`/global neighbor build 使用最大的 **radial** type cutoff；filter 又先拒绝 radial 外邻居才判断 angular，隐含 angular 不大于 radial但未显式校验。
- `MN_radial>819` 明确报错；`MN_angular` 上限以及二者正值未见完整校验。
- device 容量取 `ceil(MN_input*1.25)`，`neighbor.out` 打印放大后的最大值。

### 4.5 descriptor/ANN 参数布局

- `n_max`、`basis_size` 各必须正好 3 tokens，但不验证 keyword。
- `l_max` 至少 4 tokens；代码读取到第8个参数，额外 token 被忽略。各 `has_q_*` 作为 int读取，未验证只能0/1。
- `ANN` 必须正好3 tokens，但不验证 keyword或第三项为0。
- `dim=(nmaxR+1)+(nmaxA+1)*num_L`。
- NEP4 ANN 参数数：`(dim+2)*num_neurons*num_types+1`。
- NEP5 ANN 参数数：`((dim+2)*num_neurons+1)*num_types+1`。
- descriptor 参数数：`T^2*((nmaxR+1)*(basisR+1)+(nmaxA+1)*(basisA+1))`。
- 随后读取 `num_para+dim` 行（ANN+descriptor+q_scaler），每行只用第一个 token并转换到 float GPU buffer。
- 对缺行/空行/额外字段的稳定错误文字尚未用 executable test 证明，标记 `UNKNOWN`。

## 5. 当前支持的 `run.in` 命令语法

### 5.1 命令矩阵

| 命令 | 状态 | 实际语法 | 默认/单位/错误 |
| --- | --- | --- | --- |
| `potential` | `SUPPORTED` | `potential FILE [x|y|z]` | total token必须2或3。参考程序的方向选项具有进程内多GPU含义；DMG-MD一rank一GPU runtime 会识别并明确拒绝该选项 |
| `velocity` | `SUPPORTED` | `velocity T` 或参考实际接受 `velocity T ANY_TOKEN SEED` | T real `>0` K；DMG-MD 使用全局原子顺序生成并广播速度。model已有vel时命令不重生成 |
| `time_step` | `SUPPORTED` | `time_step DT [MAX_DISTANCE]` | DT real，输入fs并除10.18051；MAX_DISTANCE real `>0` Å，启用自适应上限 |
| `ensemble nve` | `SUPPORTED` | `ensemble nve` | 不带参数 |
| `ensemble nvt_ber` | `SUPPORTED` | `ensemble nvt_ber T_INITIAL T_FINAL TAU_T` | 温度K且均`>0`；coupling real `>=1`；每步 target按 `step/number_of_steps` 插值。release 矩阵比较温度统计、时间平均RDF和MSD |
| `run` | `SUPPORTED` | `run STEPS` | 恰好一个int；立即执行并清空该段 measurement/correct状态 |
| `dump_thermo` | `SUPPORTED` | `dump_thermo INTERVAL` | int `>0`；固定文件 `thermo.out` append |
| `dump_xyz` | `SUPPORTED` | `dump_xyz INTERVAL FILE [group METHOD ID] [precision single|double] [QUANTITY ...]` | interval int `>0`；options/quantities可混排；重复 group/precision报错；详见下文 |
| `dump_restart` | `SUPPORTED` | `dump_restart INTERVAL` | int `>0`；固定 `restart.xyz`，每帧覆盖 |
| `correct_velocity` | `SUPPORTED` | `correct_velocity INTERVAL [GROUPING_METHOD]` | interval int `>=10`；method int且有效。每段run结束重置；step 0即满足 `%interval==0` 因而立即修正 |

默认状态：

- 若 `model.xyz` 没有 velocity，`Run::Run()` 在解析任何命令前以 300 K 初始化速度，无显式 seed (`run.cu:155-160`)。
- 默认 timestep 对象值为内部 `1/10.18051`，即用户单位 1 fs (`src/main_gpumd/run.cuh:57-73`)。
- 必须先有可用 ensemble 再 `run`；未显式 `ensemble` 的错误路径/默认值未在本次审计中完整证明，标记 `UNKNOWN`。
- 每段 `run` 前先计算一次初始 force；dump 只在完成的 step `(step+1)` 达到 interval 时触发。

### 5.2 `dump_xyz` options/quantities

| token | 参数 | 作用/错误 |
| --- | --- | --- |
| `group` | `METHOD ID` | 只输出一个有效 group；method/ID必须存在，不允许负ID |
| `precision` | `single` 或 `double` | 默认 single；single格式 `%.9g`，double `%.17g` |
| `velocity` | 无 | 输出 `vel:R:3`，内部速度除10.18051为 Å/fs |
| `force` | 无 | 输出 `forces:R:3`，eV/Å |
| `potential` | 无 | 输出 `energy_atom:R:1`，eV |
| `unwrapped_position` | 无 | 输出 `unwrapped_position:R:3`，Å |
| `mass` | 无 | 输出 `mass:R:1`，amu |
| `charge` | 无 | 输出 `charge:R:1`；普通NEP取model charge，单位代码证据不足 |
| `bec` | 无 | 普通NEP报错，仅NEP-charge可用；当前 parser 明确 unsupported |
| `virial` | 无 | 输出每原子 `virial:R:9` |
| `group_labels` | 无 | 每 grouping method 一列；没有group时报错 |

旧语法 `dump_xyz METHOD ID INTERVAL ...` 会被特判并报迁移错误。未知 quantity立即报错。argument出现顺序不决定输出列顺序；输出按固定顺序 mass, charge, bec, velocity, force, potential, unwrapped, virial, group。

如果 `FILE` 以 `*` 结尾，每帧使用去掉星号后的 prefix 加 `(step+1)` 并覆盖该独立文件；否则以 append 打开一个文件 (`src/measure/dump_xyz.cu:70-116`)。

## 6. 命令分类全集

### 6.1 `SUPPORTED`

```text
potential
velocity
time_step
ensemble nve
ensemble nvt_ber
run
dump_thermo
dump_xyz
dump_restart
correct_velocity
```

`correct_velocity` 不是力/积分正确性的必要条件，但默认随机速度初始化本身会执行全局线/角动量修正，且长 NVE 用户常用周期修正；当前 replicated runtime 已以全局归约/广播语义实现。

### 6.2 `PARSE_ONLY_UNSUPPORTED`

| token/子类型 | 参考版本行为或理由 |
| --- | --- |
| `neighbor` | **当前 dispatcher中不存在**；`run.cuh` 有陈旧声明但没有实际分派。DMG-MD先识别并提示“本参考版本无该命令/本runtime不支持”，语义 `UNKNOWN` |
| `dump_position` | 当前GPUMD明确报已移除，提示 `dump_xyz <interval> <filename>` |
| `dump_velocity` | 当前GPUMD明确报已移除，提示 `dump_xyz ... velocity` |
| `dump_force` | 当前GPUMD明确报已移除，提示 `dump_xyz ... force` |
| `dump_exyz` | 当前GPUMD明确报已移除，提示 `dump_xyz ... velocity force potential` |
| `fix` | 实际语法 `fix [GROUPING_METHOD] GROUP_ID`；需要group、影响积分和温度DOF。真实用户需求尚无语料证据，当前先拒绝；是否升为FUTURE由工作负载调查决定 |
| `nep3*`, NEP dipole/polarizability/temperature/charge | 非普通NEP/NEP-ZBL当前范围；入口存在不代表构造可用 |

### 6.3 `FUTURE`

```text
replicate, change_box, move, deform
ensemble nvt_nhc, nvt_lan, nvt_bdp, nvt_bao
ensemble npt_ber, npt_scr, nvt_mttk, npt_mttk, nph_mttk
dump_netcdf
compute_msd, compute_rdf, compute_adf, compute_orientorder,
compute_angular_rdf, compute_chunk, compute
```

这些功能可能有产品价值，但本审计没有逐项证明全部参数语法；除上面明确列出的部分外均标记 `UNKNOWN`，当前 parser 只按首 token/ensemble subtype 识别后拒绝。

### 6.4 `IRRELEVANT`（第一阶段及当前产品方向）

dispatcher 实际识别的其余命令应进入明确拒绝表：

```text
minimize
compute_phonon compute_cohesive compute_elastic
active compute_extrapolation
compute_dos compute_sdc compute_ic compute_dpdt compute_es
compute_hac compute_viscosity compute_hnemd compute_hnemdec
compute_shc compute_gkma compute_hnema compute_lsqt
dump_cg dump_beads dump_observer dump_shock_nemd
dump_dipole dump_polarizability
electron_stop add_random_force add_force add_spring add_efield
mc kspace dftd3 plumed
ensemble heat_nhc heat_lan heat_bdp heat_nhc_power heat_ttm ttm heat_hybrid
ensemble rpmd trpmd pimd msst ti_spring wall_piston nphug ti
ensemble wall_mirror ti_rs ti_as wall_harmonic ti_liquid npt_qtb
```

命令全集证据是 `src/main_gpumd/run.cu:339-571`；ensemble subtype全集证据是 `src/integrate/integrate.cu:406-566`。未列参数细节的命令不是“兼容”，只是 lexer/dispatcher 必须知道其类别。

## 7. 多段 `run` 状态矩阵

| 状态 | 后续 run 是否保留 | 代码证据/说明 |
| --- | --- | --- |
| position/velocity/type/mass/group | 是 | 同一 `Atom` 对象持续存在 |
| box | 是 | 同一 `Box`；change/deform可修改 |
| potential列表 | 是 | `Force::finalize()`不清 vector；后续potential继续append |
| timestep | 是 | `Run`成员；除非新 `time_step` 覆盖 |
| `global_time` | 是 | 每步加当前dt，用于XYZ Time |
| ensemble类型/参数 | 以最后一次 `ensemble` 解析状态为准；每段创建/初始化执行对象 | `Integrate::initialize/finalize` |
| dump/measurement property | 否 | `Measure::finalize()`后清空；每段需重新声明 |
| fix/move/deform | 否 | `Integrate::finalize()`重置 |
| correct_velocity | 否 | `Velocity::finalize()`重置 |
| `max_distance_per_step` | 第一段后变0 | `Run::perform_a_run()`末尾无条件设0；初值-1 |
| output files | thermo/单文件XYZ append；restart覆盖 | 各dump preprocess/process |

重复 `dump_thermo` 等同名 property在同一段 run 的 `Measure::initialize()` 会因重复名报错；`dump_xyz` 和 `dump_netcdf` 是允许重复的例外 (`src/measure/measure.cu:26-89`)。

## 8. 输出文件合同

### 8.1 `thermo.out`

每段启用 `dump_thermo I` 时 append header：

```text
# dump_thermo I
# format_version 1
# num_atoms N
# dt_output <I*dt> fs
# columns T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz
```

代码见 `src/measure/dump_thermo.cu:49-72`。每个数据行 18 个 `%20.10e` 字段：

| 列 | 单位/语义 |
| --- | --- |
| `T` | K，瞬时 kinetic temperature |
| `KE` | eV，`1.5*(N-N_fixed)*kB*T`；dump代码没有再减move group |
| `PE` | eV，全局每原子PE之和 |
| `sxx syy szz syz sxz sxy` | GPa，virial+kinetic stress / volume 后乘160.2177 |
| `a,b,c` 9列 | Å，晶格向量逐个输出 |

### 8.2 `dump_xyz`

每帧 extended XYZ：

1. 第一行：输出原子数（全体系或选定group）。
2. 第二行固定从 `Time=%.8f` 开始，时间单位fs；随后 pbc、Lattice、总energy、总virial、stress和Properties。
3. 原子行默认 `species pos_x pos_y pos_z`，再按固定quantity顺序。

第二行量纲：energy/virial 为 eV；stress 是 eV/Å³，**不**乘 thermo 的 GPa conversion (`src/measure/dump_xyz.cu:184-238`)。virial/stress以对称3×3的9值输出。每原子virial的输出映射为内部索引 `0,3,4,6,1,5,7,8,2`。

全体系原子行按 `Atom` 原数组顺序；group输出按 `Group::cpu_contents`，它也是扫描原数组所得顺序。MPI兼容输出必须按 stable global ID恢复这一顺序。

### 8.3 `restart.xyz`

- 固定名字 `restart.xyz`，匹配步以 `w` 覆盖。
- `%g` 格式，第一行N，第二行 `pbc`, `Lattice`, `Properties`。
- 无group时：`species:S:1:pos:R:3:mass:R:1:vel:R:3`。
- 有group时追加 `:group:I:M` 并输出每种 grouping method label。
- velocity由内部单位除10.18051为 Å/fs。
- 不保存 time、force、PE、virial、RNG、thermostat state或global ID。

因此它能作为新的 `model.xyz` 状态输入，但代码不会自动重命名/读取。不同rank数恢复只可承诺状态字段兼容；NVT/RNG的连续性为 `UNKNOWN`。

### 8.4 隐式 `neighbor.out`

ordinary large-box NEP 每1000次 `compute_large_box()`（包括第一次调用，计数从0）把最大 radial/angular neighbor count D2H，并 append `neighbor.out` (`src/force/nep.cu:1007-1025`)。它不是 `run.in` dump命令。

DMG-MD 当前保留 rank 0 的这一兼容副作用；非零 rank 的同名内部输出进入本机私有 scratch 目录
（`mkdtemp` 原子创建、mode 0700、普通文件 neighbor.out），不得竞争用户作业目录。该隔离不广播
任何文件系统路径，各节点 `/tmp` 互不可见亦可运行；合同见
[replicated-mpi.md](./replicated-mpi.md) 的“I/O 与 NEP_MULTIGPU”一节；尚未完成的严格双节点
验收见 [multi-node-io.md](../plans/multi-node-io.md)。

## 9. 单位汇总

| 量 | 用户输入/输出 | GPUMD内部/转换 |
| --- | --- | --- |
| length/cutoff | Å | Å |
| time/timestep | fs | 除 `10.18051` |
| velocity | Å/fs | 输入乘、输出除 `10.18051` |
| temperature | K | K，`kB=8.617343e-5 eV/K` |
| mass | amu | 与内部自然时间单位配套使用 |
| energy | eV | eV |
| force | eV/Å | eV/Å |
| virial | eV | eV |
| thermo stress | GPa | 内部eV/Å³乘 `160.2177` |
| XYZ stress | eV/Å³ | 不转换 |

常数证据：`src/utilities/common.cuh`。

## 10. 兼容实现验收规则

任何命令只有同时满足以下条件才能从 unsupported 升为 supported：

1. 参数数量、类型、大小写、默认值和错误路径有测试；
2. 多段run状态有测试；
3. 单位和文件名有测试；
4. header、列顺序、format precision和step触发有测试；
5. MPI下只从owned数据产生一次物理贡献；
6. 与锁定GPUMD executable的golden结果达到 [golden-test-standard.md](./golden-test-standard.md) 定义的比较级别。
