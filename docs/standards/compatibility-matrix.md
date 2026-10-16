# 输入/输出兼容矩阵

类别：现行标准。核对日期：2026-10-16。

GPUMD 参考 commit 为 `9d23496e41319b9e2af5221a7df6285387401d1e`。本页声明 DMG-MD 当前
支持范围；标为参考证据的 `src/force`、`src/measure` 等路径均在锁定 GPUMD 树中，行号只
适用于该 commit。当前入口为 `src/{run_parser.cpp,model_parser.cpp,runtime.cu,domain_runtime.cu}`
和 `src/gpumd_compat/`。无法证明的边界记 UNKNOWN，不构成兼容承诺。

## 1. 产品与并行范围

支持普通 NEP4/5 与 NEP-ZBL、单 bead 经典 MD、NVE/nvt_ber 及下表命令。
P1 恒走 M1；P>1 只支持正交全周期，满足 large-box/slab 半径条件才走 M2a，否则在已支持
范围内 M1 fallback。详细条件见[MPI runtime](mpi-runtime.md)。

每作业恰好一个 potential。GPUMD 的多 potential 追加及方向参数不在 DMG-MD 范围；
NEP3、temperature/dipole/polarizability/charge 模型均拒绝。白名单以外的命令/ensemble
（包括 fix/move、replicate、变盒、其他 thermostat、minimize/phonon/PIMD/MC、其他势、
完整 measurement、kspace/dftd3/PLUMED、旧 dump 名称等）都报带来源位置的 unsupported。
未知命令附 unknown 标记，不静默忽略；未来候选不等于支持。

## 2. 文本与 model.xyz

- `run.in` 一行一命令、空白分词、大小写敏感、无 shell 引号/转义。首字符为 # 的 token
  起截断，abc#def 不截断；每条非空命令最多 32 tokens。当前 parser 先解析完整 IR 再执行。
- model 第一行恰一 int，N≥2；第二行 pbc 默认为 T/T/T，显式时恰三个 T/F（忽略大小写）；
  Lattice 必须九个有限值。奇异盒的稳定错误行为未完成专项验证。
- 第二行接受等号周围空白与引号分组；schema/property 名忽略大小写，原子 species 区分大小写。
- Properties 为 name:type:width 三元组，width>0，必含 species/pos；atom 行恰好匹配总列宽。
  额外属性占列但不解析；只读 N 行，后续额外行忽略。标准 type/width 组合未被严格校验，
  malformed schema 不视为支持，安全边界列入 backlog。

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
| mass | 1 | 可选；double amu、>0，否则查 GPUMD 元素表；无默认质量时明确 InputError |
| charge | 1 | 可选；存 float，缺省 0；普通 NEP charge 单位仍 UNKNOWN |
| vel | 3 | 可选；Å/fs 输入乘 10.18051；存在 vel 时 velocity 命令不重生成 |
| group | M | 可选；M 个 int labels，0≤label<N；允许缺失 group ID，形成空组 |

参考语法锚点为 `src/model/read_xyz.cu`；当前错误来自 model_parser.cpp，不承诺复制参考
std::map::at 异常文字。输入上限、奇异盒与恶意 schema 的完整 corpus 尚未建立。

## 3. 普通 NEP/NEP-ZBL 文件

### 3.1 头部语法

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
| version token | `NEP::NEP()` 接受普通 `nep4`, `nep4_zbl`, `nep5`, `nep5_zbl`；参考代码还接受不在当前产品范围的 NEP4 temperature/dipole/polarizability (`src/force/nep.cu:108-143`) |
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
- `rc`/global neighbor build 使用最大的 **radial** type cutoff；filter 又先拒绝 radial 外邻居才判断 angular，隐含 angular 不大于 radial但未显式校验。
- `MN_radial>819` 明确报错；`MN_angular` 上限以及二者正值未见完整校验。
- device 容量取 `ceil(MN_input*1.25)`，`neighbor.out` 打印放大后的最大值。

### 3.5 descriptor/ANN 参数布局

- `n_max`、`basis_size` 各必须正好 3 tokens，但不验证 keyword。
- `l_max` 至少 4 tokens；代码读取到第8个参数，额外 token 被忽略。各 `has_q_*` 作为 int读取，未验证只能0/1。
- `ANN` 必须正好3 tokens，但不验证 keyword或第三项为0。
- `dim=(nmaxR+1)+(nmaxA+1)*num_L`。
- NEP4 ANN 参数数：`(dim+2)*num_neurons*num_types+1`。
- NEP5 ANN 参数数：`((dim+2)*num_neurons+1)*num_types+1`。
- descriptor 参数数：`T^2*((nmaxR+1)*(basisR+1)+(nmaxA+1)*(basisA+1))`。
- 随后读取 `num_para+dim` 行（ANN+descriptor+q_scaler），每行只用第一个 token并转换到 float GPU buffer。
- 对缺行/空行/额外字段的稳定错误文字尚未用 executable test 证明，标记 `UNKNOWN`。

## 4. 当前 run.in 命令

| 命令 | 支持语法 | 校验 / 语义 |
| --- | --- | --- |
| potential | `potential FILE` | 必须在 run 前；第二条 potential 或第三方向参数拒绝 |
| velocity | `velocity T [ANY_TOKEN SEED]` | T>0 K；四 token 形式读取最后的 int seed，第三 token 不校验 |
| time_step | `time_step DT [MAX_DISTANCE]` | DT 为有限实数 fs，默认 1 fs；可选最大距离>0 Å，按全局 owned 最大速度限步；负 DT 边界未验收 |
| ensemble | `ensemble nve` / `ensemble nvt_ber T1 T2 TAU` | T1/T2>0 K，TAU≥1；目标按 step/steps 插值；run 必须已有 ensemble |
| run | `run STEPS` | int；0/负步数 parser 未拒绝，未作为验证过的动力学兼容行为 |
| dump_thermo | `dump_thermo INTERVAL` | int>0；thermo.out append |
| dump_xyz | `dump_xyz INTERVAL FILE [OPTIONS/QUANTITIES]` | interval>0；见下表；同段可多条 |
| dump_restart | `dump_restart INTERVAL` | int>0；restart.xyz 覆盖 |
| correct_velocity | `correct_velocity INTERVAL [GROUPING_METHOD]` | interval≥10，method 有效；step%interval==0，含段内 step0 |

无输入 vel 时 root 默认以 300 K 初始化并广播；无 seed 的随机初速不保证重复运行相同。
显式 velocity 按全局输入顺序生成并做全局动量修正，不是每 rank 独立 RNG。
每段 run 先计算初始 force；dump 在完成的 step+1 达 interval 时触发，无 step0 dump。
同段重复 dump_thermo/restart 拒绝；没有后续 run 的 dump 在结束时报错。

### dump_xyz 选项

| token | 参数 / 行为 |
| --- | --- |
| group | METHOD ID，有效非负分组/ID，只输出所选组；重复拒绝 |
| precision | single（默认 %.9g）或 double（%.17g）；重复拒绝 |
| mass / charge | 输出 mass:R:1 / charge:R:1 |
| velocity / force / potential | vel:R:3 / forces:R:3 / energy_atom:R:1 |
| unwrapped_position | unwrapped_position:R:3 |
| virial | 每原子 virial:R:9 |
| group_labels | 每个 grouping method 一列；无 group 拒绝 |
| bec | 当前普通 NEP runtime 明确 unsupported |

参数可混排，列固定按 mass、charge、velocity、force、potential、unwrapped、virial、group
排列，不按参数先后。未知参数报错。FILE 尾部为 * 时去掉星号加 step+1，每帧独立覆盖；
其他文件 append。输出 global ID 顺序等于输入行顺序；group 也保持该相对顺序。

## 5. 多段 run 状态

| 状态 | DMG-MD 后续 run |
| --- | --- |
| position/velocity/type/mass/group、box、global_time | 保留；当前不支持动态变盒 |
| potential | 保留同一实例，不支持参考程序的多势追加 |
| timestep / ensemble | 保留最近配置，直到覆盖 |
| dump / correct_velocity / maximum_distance | 段末清空，后段须重新声明 |
| ownership/cache | M1 owner 与 M2a epoch 延续；M2a 段首 force 不强制 rebuild |
| 文件 | thermo/单文件 XYZ append，restart 覆盖 |

参考原程序的 fix/move/deform 状态不属于当前支持合同，见[GPUMD 审计](../reference/gpumd-runtime-audit.md)。

## 6. 输出文件合同

### 6.1 `thermo.out`

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

### 6.2 `dump_xyz`

每帧 extended XYZ：

1. 第一行：输出原子数（全体系或选定group）。
2. 第二行固定从 `Time=%.8f` 开始，时间单位fs；随后 pbc、Lattice、总energy、总virial、stress和Properties。
3. 原子行默认 `species pos_x pos_y pos_z`，再按固定quantity顺序。

第二行量纲：energy/virial 为 eV；stress 是 eV/Å³，**不**乘 thermo 的 GPa conversion (`src/measure/dump_xyz.cu:184-238`)。virial/stress以对称3×3的9值输出。每原子virial的输出映射为内部索引 `0,3,4,6,1,5,7,8,2`。

全体系原子行按 `Atom` 原数组顺序；group输出按 `Group::cpu_contents`，它也是扫描原数组所得顺序。MPI兼容输出必须按 stable global ID恢复这一顺序。

### 6.3 `restart.xyz`

- 固定名字 `restart.xyz`，匹配步以 `w` 覆盖。
- `%g` 格式，第一行N，第二行 `pbc`, `Lattice`, `Properties`。
- 无group时：`species:S:1:pos:R:3:mass:R:1:vel:R:3`。
- 有group时追加 `:group:I:M` 并输出每种 grouping method label。
- velocity由内部单位除10.18051为 Å/fs。
- 不保存 time、force、PE、virial、RNG、thermostat state或global ID。

因此它能作为新的 `model.xyz` 状态输入，但代码不会自动重命名/读取。不同rank数恢复只可承诺状态字段兼容；NVT/RNG的连续性为 `UNKNOWN`。

### 6.4 隐式 neighbor.out

ordinary large-box NEP 每 1000 次 force 调用（含 call0）append 一条邻居统计。
M1 保留 root 副作用，非 root 写本机私有 scratch。M2a 在本次 typewise 表生成后对
各 dependency 域 local max 做两个 MPI_MAX，仅 root 写；空域贡献零。O2 replay 不重复记录。
完整隔离/记账见[MPI runtime](mpi-runtime.md#io-与-nep_multigpu)。

## 7. 单位汇总

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

## 8. 支持声明的变更

新增支持须有参数/错误、多 run、单位/文件/格式/触发步、唯一 owned 贡献及 reference golden
验证；比较层级见[Golden Test](golden-test-standard.md)。当前已执行范围见[状态](../status/current.md)，
malformed/边界缺口见[待办](../plans/risk-and-backlog.md)。
