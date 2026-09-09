# DMG-MD Golden Test 计划

## 1. 目标和基线纪律

目标不是让一条长轨迹逐步重合，而是分层证明：parser/文件合同一致、单点NEP数学一致、owned/ghost通信没有漏项或重复项、全局observable一致、积分/温控顺序一致、长时数值质量不退化。

权威参考：

- GPUMD commit `9d23496e41319b9e2af5221a7df6285387401d1e`；
- 单GPU ordinary `NEP` 路径，基线进程必须只看见一张GPU，防止自动进入 `NEP_MULTIGPU`；
- DMG-MD MPI 测试只使用 source `../env/md-mpi.sh` 后的 Open MPI+UCX；任何 numerical case
  之前先通过 `tests/mpi/check_environment.py`，确认链接、CUDA transports 和实际 device
  collectives；
- 每个case在新的空临时目录运行，因为 `thermo.out`、XYZ和`neighbor.out`会append；
- 保存GPUMD executable hash、编译器/CUDA版本、build flags、GPU型号/driver、输入文件hash和环境变量。

GPUMD已有的 `tests_pytest/` 是重要起点。`tests_pytest/conftest.py:49-59` 明确记录了float累加/非结合顺序带来的噪声，并给出经验容差；`test_io_dump_commands.py` 已覆盖多项XYZ/restart格式。DMG-MD应复用fixture思想和期望，不能只看仓库旧的手工测试目录。

## 2. fixture 最小集合

### 2.1 potential

至少锁定以下原始文件，不做格式转换：

1. 单元素 `nep4`；
2. 双元素 `nep4`，包含不同 type-pair；
3. `nep5`；
4. universal `nep4_zbl`；
5. flexible ZBL；
6. typewise radial/angular cutoff；
7. 专门构造的 cutoff/ZBL 边界文件（可能是invalid/UNKNOWN case）。

所有potential保存SHA-256。rank 0读取后应记录参数buffer hash；各rank上传前host bytes/hash必须完全相同。

### 2.2 model

- 大正交全周期box，保证走large-box路径；
- 单元素和混合元素；
- Properties分别覆盖仅species/pos、显式mass、charge、vel、一个及多个group、未知但有宽度的property；
- 显式velocity用于确定性动力学；随机velocity只在RNG专项测试使用；
- triclinic/nonperiodic输入作为parse成功但runtime明确unsupported的MVP negative case；
- small-box作为future/experimental case，不混入首个通过门槛。

### 2.3 静态单点 run

当前GPUMD接受并在自身I/O测试中使用 `time_step 0`。可用以下形式在不改变位置的情况下导出单点量：

```text
potential nep.txt
time_step 0
ensemble nve
dump_thermo 1
dump_xyz 1 static.xyz force potential virial precision double
run 1
```

GPUMD仍会在run前和step内各计算一次力，但坐标不变；`static.xyz` 的每原子force/PE/virial可作为单点golden。温度/velocity会经历half step，因此静态force测试不使用thermo中的KE作为oracle。

## 3. 八级验证阶梯

### G1 — 原 GPUMD 单GPU基线

目的：冻结参考行为并测噪声。

- 对每个fixture运行静态单点、短NVE、`nvt_ber`、多段run和输出case。
- 同一输入重复至少30次；记录每字段pairwise最大差、分位数和是否bitwise稳定。
- 强制一张可见GPU；另做一次多GPU可见性negative test，证明它会切到 `NEP_MULTIGPU`，但该输出不作为权威golden。
- parser invalid cases记录exit code、错误类别、关键message substring和实际生成/未生成的文件。
- 输出完整保存在版本化golden目录，manifest中绑定reference commit。

通过条件：基线重复性统计稳定；所有golden可由脚本从干净目录重建，更新golden必须显式flag并review diff。

### G2 — 新 runtime 单rank

目的：在没有MPI域分解变量时验证兼容解析、数据布局、复用kernel和积分顺序。

- `owned=N, ghost=0`。
- 静态单点逐global ID比较type、PE、3-force、9-virial。
- 若能暴露测试接口，逐中心比较radial/angular neighbor count和排序后的global-ID adjacency。
- 比较1、2、10步NVE；前几步逐字段，较长轨迹比较能量指标。
- 比较`nvt_ber`每步target T、instant T、缩放后的velocity。
- 比较多段run状态、输出触发和文件append/overwrite。

通过条件：per-atom静态量至少达到经验NEP容差；所有结构性字段完全一致；没有unsupported命令被静默接受。

### G3 — MPI复制数据原型

目的：先验证MPI lifecycle、device binding、collective和rank0 I/O，不引入domain decomposition。

方案：每 rank 持有完整输入并执行 ordinary NEP scratch；balanced center range 唯一拥有积分、
thermo 和 per-atom output。每步 Allgatherv owned position/velocity，thermo 只归约 owned local
sum，输出只 Gatherv owned records。直接设置 NEP `N1/N2` 已由 kernel 证据判定为不完整，不能
作为本原型实现。

检查：

- 每rank只绑定一张唯一GPU；不会构造 `NEP_MULTIGPU`；
- 所有rank parser IR和potential hash一致；
- rank-local静态结果一致；
- rank0输出恰好一份，不发生多rank append；
- collectives错误路径不会hang；
- HostStaged 与 CudaAware 运行同一组数值 case，并做 cross-backend direct differential；
- 1、2、4 rank的rank0输出结构相同。

复制原型绝不能被当成domain-decomposed性能结果。

### G4 — MPI domain decomposition

目的：证明owned/ghost和NEP多层依赖正确。

- 1、2、4 rank；至少沿x和二维划分，不能只测试与 `NEP_MULTIGPU` 相同的单轴slab。
- 首先用保守两跳position halo作为oracle。
- 再实现/测试一跳position + `Fp` + directed-partial分阶段exchange。
- 对每个owned atom按global ID比较sorted neighbors、descriptor中间场（测试构建）、PE/force/virial。
- 验证所有global ID恰好一个owner；ghost不参与VV/thermo/output。
- 每次migration前后强制比较邻居cache失效/重建行为。

通过条件：内部原子、边界原子和跨周期原子均达到同一per-atom容差；两种通信协议相互等价；全局PE/virial无rank数比例错误。

### G5 — 分区边界与周期边界

每个几何case要移动分区面或改变rank数，使同一物理原子分别成为内部/边界/ghost依赖中心：

| case | 构造 | 专门捕获 |
| --- | --- | --- |
| interior | 所有相互作用原子离rank面 `>2Rmax` | 本地kernel基础正确性 |
| near split | `i=x_split-ε`, `j=x_split+ε` | 一跳position halo |
| exact split | atom精确在half-open分区面 | owner唯一性/重复或遗漏 |
| two-hop radial | owned i—remote j在R内；k在j的R内但在i的R外 | 缺失neighbor `Fp(j)` |
| reverse angular | i-j跨rank，j还有决定descriptor的k | 缺失 `f12(j,i)` |
| periodic pair | `i≈0`, `j≈L` 且MIC距离小 | 周期ghost/image |
| periodic two-hop | i-j跨周期，j-k继续向相邻domain | 周期两层halo |
| ZBL cross-rank | 极近pair跨rank/周期 | ZBL list、0.5能量和力号 |
| typewise | 不同type组合跨rank | pair cutoff平均与halo精确界 |
| migration | owned atom一个step跨过rank面 | stable ID、state pack、cache失效 |

`ε`应覆盖精确0、若干ULP、`1e-12 Å`、`1e-6 Å`和skin/cutoff边界两侧。cutoff判据代码使用严格 `< rc²`，正好等于cutoff应与GPUMD一致地排除。

### G6 — 不同rank数 restart

流程：

1. GPUMD单GPU运行A步，输出 `restart.xyz`；复制为新作业 `model.xyz`，运行B步，形成参考。
2. DMG-MD用P ranks运行A步并写兼容 `restart.xyz`。
3. 用Q ranks（P≠Q，包括1↔2↔4）读取该文件、重新按input行global ID分区并运行B步。
4. 比较restart文本结构、读回后的species/mass/group、position/velocity和第一步重新计算的force。

要求：

- restart中恰好N条record，顺序按原input global ID；
- 不把ghost写入文件；
- P与Q不改变读回global ID；
- 文件本身不含global time/RNG/thermostat state，因此只承诺GPUMD保存字段的连续性；不能宣称bitwise NVT延续。

`nvt_ber`没有chain state，可作为后续测试；随机thermostat的restart只能比较统计性质，除非产品另加不破坏主文件的sidecar且明确语义。

### G7 — NVE 能量漂移

至少对晶体、液体/无序结构、NEP-ZBL近碰撞三类体系使用 manifest 固定的
保守 dt 并测试多个 rank 数。当前 suite 不宣称 dt 收敛；替换或转为科学生产体系前，
另行做 GPUMD-only dt 扫描并重新审查输入哈希。

对 `E(t)=KE(t)+PE(t)` 计算：

```text
offset_per_atom = (E(t)-E(0))/N
max_excursion   = max_t |offset_per_atom|
drift_slope     = slope(linear_fit(E(t)/N versus physical_time))
rms_fluctuation = RMS(detrended E(t)/N)
```

比较策略：

- 极短轨迹在混沌分叉前可以逐step数值比较。
- 长轨迹不要求坐标逐step相同；比较上述漂移指标相对GPUMD的分布。
- 至少10组显式初始velocity/微扰，报告中位数和95%区间。
- 检查migration或neighbor rebuild时刻是否有能量跳变；这类相关尖峰即使总体斜率小也判失败。
- 1/2/4 ranks的漂移不能随rank数系统恶化。

当前已实现的执行入口为 `tests/long_nve/run_long_nve.py`，与短程 committed golden 分离。
`release` profile 固定 100000 steps、十组显式初态和 1/2/4/8 ranks；`smoke`/`nightly` 分别为
100/10000 steps。当前 fixture 是 4096-atom diamond C、12288-atom 确定性扰动水体系和
5000-atom BaTiO3/ZBL 体系。生成器输出的每个完整 `model.xyz` 均由 manifest 锁定 SHA-256。

实现补充以下约束：

- `E(0)` 来自同一 model 的独立 `time_step 0` 静态作业，不再把第一个动态输出点误当初态；
- `nightly`/`release` 的前 100 步仍逐帧与 GPUMD 比较；`smoke` 为降低编排延迟只
  比较前 10 步；独立的长段只比较守恒与构型分布统计；
- GPUMD 与 DMG-MD 各自产生的长期快照互相交给另一个 executable 做静态回放，在相同坐标上
  比较逐原子 force/PE/virial；
- 首个所选 seed 在半程 restart，并按最小→最大、最大→最小 rank 数继续；
- manifest 预先固定长期 metric 的相对非劣 margin、absolute floor 和元素对距离直方图门槛；
  candidate 失败后不得原地调宽；
- 当前水与 ZBL fixture 是数值压力输入，不宣称是生产科学用的已平衡系综。若替换为预平衡
  样本，先用 GPUMD-only dt 扫描并审查全部输入哈希。

### G8 — 输出格式与兼容错误

对 `thermo.out`、单文件XYZ、`*`分帧XYZ、`restart.xyz` 逐层比较：

- 文件名、存在/不存在、帧数、append/overwrite完全一致；
- header文本、Properties列、列顺序、pbc、N、group行数完全一致；
- single/double precision significant digits符合 `%.9g/%.17g`；
- 时间、dump step、lattice、单位转换数值比较；
- XYZ atom record按global ID恢复GPUMD顺序；
- per-atom virial和comment total六分量自洽；
- thermo stress GPa、XYZ stress eV/Å³不能混淆；
- 多段run每段thermo header重复append，measure property按段清除；
- `dump_position/velocity/force/exyz` 给出迁移错误；`neighbor`及所有未支持命令明确unsupported；未知命令不被忽略。

## 4. 比较等级

### 4.1 必须逐字段完全比较

- exit成功/失败分类；
- 文件名、文件个数、frame数、输出step编号；
- N、type index、species string、group label、global ID排序；
- pbc布尔值、Properties名称/type/width、header关键字与列顺序；
- parser IR中的命令类别、参数数量、整数和枚举；
- potential版本/type数/symbol顺序、参数数量及host float buffer bit pattern；
- owned/ghost身份不变量和neighbor global-ID集合；
- unsupported命令不能产生MD输出。

对于同一formatter和完全相同数值bit pattern，文本应byte-identical；但不能倒过来要求所有浮点输出天然byte-identical。

### 4.2 需要数值容差

- position、velocity、PE、force、virial；
- thermo T/KE/PE/stress；
- XYZ comment energy/virial/stress；
- Berendsen缩放因子和短轨迹；
- halo两方案的descriptor/partial中间场；
- NVE短期能量和长期drift metric。

比较公式统一为：

```text
abs(test-ref) <= atol + rtol * abs(ref)
```

总能量同时报告每原子误差，stress同时报告自然单位和输出单位，避免系统规模放大掩盖问题。

### 4.3 只能比较统计性质

- 未固定或仍用GPUMD libc `rand()`的随机初速；
- Langevin/BDP/BAO等未来随机ensemble；
- 超过轨迹分叉时间后的坐标/速度；
- NVE长期drift在多初态/多硬件下的分布；
- 跨GPU架构或不同MPI reduction tree的低位波动。

基础MVP应优先用显式model velocity和确定性 `nvt_ber`，尽量把更多测试留在前两级。

## 5. 容差如何确定

### 5.1 可作为启动上限的参考值

GPUMD自己的 `tests_pytest/conftest.py:49-59` 使用：

| 量 | rtol | atol | 备注 |
| --- | ---: | ---: | --- |
| energy | `1e-5` | `1e-8 eV` | same-configuration GPU NEP |
| force | `1e-4` | `1e-6 eV/Å` | 包含接近0分量的绝对floor |
| virial/stress | `1e-4` | `1e-6` | 该测试经ASE stress convention比较 |

这些只能作为首轮“不能比它更松而没有解释”的上限，不是自动接受的DMG-MD合同。复用相同kernel、保持每中心排序neighbor时，单rank和per-atom量应争取明显更紧，甚至bitwise；MPI global sum通常不能bitwise。

### 5.2 实验校准步骤

1. 对每个fixture/硬件同一GPUMD binary重复30次，得到run-to-run noise floor。
2. 在支持矩阵中的GPU架构、CUDA/compiler/build flags上重复，分开记录“同构”和“跨构建”容差。
3. 对每字段计算最大absolute差、相对差、99.9%分位；near-zero分量单独决定atol。
4. 把输出格式量化误差纳入：single XYZ最多9 significant digits，不能用double内存容差要求其文本回读值。
5. 初始候选取 `max(格式误差, 5~10×观测noise, 数值分析下界)`，然后用有意注入的错误验证它仍能捕获：漏一个neighbor、力号翻转、ghost重复计能、virial列交换。
6. 对总量按N归一化再校准；不能因大系统总能量大而只用宽松相对误差。
7. 将最终阈值与manifest/硬件范围一起review并版本化。只有新基线实验能改变阈值，不能为单个失败随手放宽。

### 5.3 通过门槛的优先级

- 身份/neighbor集合不一致：无论浮点容差多小都失败。
- per-atom边界误差集中于rank面：判通信错误，不以全局总量相消通过。
- 全局量按rank数成倍变化：判ghost ownership错误。
- 只有均匀低位变化且落在基线noise：才按数值容差处理。
- 长轨迹分叉但静态力、短轨迹、drift统计均通过：可接受，不要求轨迹重合。

## 6. 自动化与产物

建议每个case目录包含：

```text
inputs/                 # 原始model/run/potential，只读hash
gpumd/                  # 单GPUgolden原始输出
dmgmd-r1/               # 单rank输出
dmgmd-replicated-rN/    # 复制原型
dmgmd-dd-rN/            # 域分解
manifest.json           # commit/build/GPU/hash/rank/decomposition
comparison.json         # 每字段误差与判定
```

CI分层：parser/format 和 `dmgmd.long_nve_analysis` CPU tests 每次运行；单GPU静态/短MD在 GPU
CI；2-rank同节点每个合并请求；`long_nve` smoke 用于完整编排验证，nightly 为 10000 steps，
release/阶段门槛才运行十初态 100000-step 及 1/2/4/8 rank 矩阵。多节点、sanitizer和MPI错误
注入仍定期运行。CUDA buffer/halo改动应增加compute-sanitizer和MPI错误注入case。

## 7. 实现切口的最小验收集

第一个实现切口只需通过但必须完整通过：

1. 大正交全周期box、model显式velocity、单元素NEP4；
2. `time_step 0` 静态PE/force/virial单rank对比；
3. 10步NVE单rank对比；
4. 2-rank保守两跳halo的interior/near split/two-hop/periodic case；
5. owned-onlythermo all-reduce；
6. rank0 `dump_thermo` 与double `dump_xyz force potential virial`；
7. 所有其他已知命令明确unsupported；
8. 1/2 rank输出按global ID一致。

NEP5、ZBL、`nvt_ber`、restart、分阶段intermediate exchange依次作为后续小切口，不在第一次提交中同时展开。
