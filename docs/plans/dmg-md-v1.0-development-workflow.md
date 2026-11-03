# DMG-MDv1.0开发流程

> 类别：实施计划（可拆分为 AI Agent 任务卡）。状态：**IN PROGRESS**。原稿日期：2026-10-29。审计编排日期：2026-10-30。
> Gate 0：维护者于 2026-10-30 确认第 5 节全部默认决策；A0–A6 已完成，G-A 已通过；逐 Step 进度见 [`dmg-md-v1.0-progress.md`](dmg-md-v1.0-progress.md)。
>
> 适用基线：原稿基线 DMG-MD `4457c3c`；A0 实际执行 revision 为 `f062844`（工作区含未提交修改）。上游兼容基准：GPUMD
> `9d23496e41319b9e2af5221a7df6285387401d1e`（**只读**，不得在其中构建或产生输出）。
>
> 原稿引用 `MD-ensemble-thermostat-barostat-survey.md`（v1.1，2026-10-28），本次未提供且仓库未找到；
> 其章节引用仅保留为来源线索，不作为已核验依据。可访问的一手论文与源码证据见 §0.5。
>
> 本计划本身不修改代码。它定义执行顺序、每个 Agent 的边界、验收证据与停止条件；任何实现都必须
> 另开变更，并按 `AGENTS.md` 的流程执行。

---

## 0. 使用方式

### 0.1 两类读者

- **维护者**：先确认[第 5 节 Gate 0 决策](#5-gate-0-决策默认建议)；然后按[第 4 节路线图](#4-总体路线图)
  逐个派发 Step；每个 Gate 通过后才进入下一阶段。
- **执行 Agent**：一次只执行一个 Step。开工前阅读 `AGENTS.md`、本 Step 指定的可用文档与源码（缺失的原调研报告不作为开工依赖，见 §0.5），
  先用 `rg` 找到符号（本计划的文件行号都限定在基线 commit，后续会漂移），再给出改动计划；
  完成后按[第 10.6 节模板](#106-完成回报模板)用中文回报。

### 0.2 派发提示词模板

把下面的模板连同 Step 编号一起发给执行 Agent。一次只填一个 Step。

```text
你是 DMG-MD 仓库的 AI 执行 Agent。仓库根目录：<workspace>/newmd（本地目录名；产品名为 DMG-MD）。
本轮只执行 docs/plans/dmg-md-v1.0-development-workflow.md 中的 Step <编号>。

开始前必须：
1. 阅读 AGENTS.md、docs/README.md、本 Step 列出的标准/源码/测试；
2. 执行 git status，记录实际 revision 与既有修改；以 A0 冻结的身份为准，不覆盖用户工作；
3. 用 rg 核对符号，而不是直接信任计划中的行号；
4. 回复：你对目标的理解、预计修改的文件/符号、所有权/MPI 影响、测试计划、任何不确定点。

硬约束：
- 不提交、不推送、不做破坏性 Git 操作；不修改 ../gpumd-reference、../lammps-reference、
  ../lammps-matpl-reference；不把参考源码链接进构建；
- 只改本 Step 范围内的文件；不改变 NVE/nvt_ber 的既有数值与输出；
- 未运行的测试、skip、子集通过必须分别标注，不得写成“全部通过”；
- 遇到本 Step 的停止条件立即停止并说明，不得自行扩大范围。

完成后按计划第 10.6 节模板回报。不要写“已完成”而没有证据。
```

### 0.3 编号与状态词汇

- Step 编号：`A0`–`A7`（恒温器与共享模块）、`B0`–`B4`（控压器）、`C0`–`C5`（势函数）、
  `D1`–`D5`（产品化收尾）。
- 优先级：**P0** = v1.0 发布门槛；**P1** = v1.1 候选；**P2** = 明确暂缓。
- 状态：`PROPOSED` → `IN PROGRESS` → `DONE`（附证据）或 `BLOCKED`（附阻塞原因）。
- 证据位置：总结写入 `docs/status/validation/` 或 `docs/status/performance/` 的带日期文档；
  原始日志放本机 `artifacts/`（被忽略）或 `/tmp`，不提交大文件。

### 0.4 每个 Step 的任务卡字段

每张卡固定包含：目标、依赖、主责文件、必读、实施要点、测试/验收、证据、停止/回滚。
“主责文件”是允许修改的上界；需要越界时先问维护者。

---


### 0.5 可行性审计与修订结论（2026-10-30）

**审计结论：有条件可行，适合作为独立的正式版产品化主线；审计结束时尚不能视作已经批准、实现或通过验收。**
保留原稿 A/B/C/D 任务卡体系与 P0 范围，修订会导致错误实现或过度承诺的要求。
审计轮只处理文档，没有执行开发 Step 或 GPU/MPI 验收；维护者随后于 2026-10-30 确认了 Gate 0 全部默认决策，A0 验收结果见基线记录。
O 系列负责并行性能优化，本流程负责从测试版走向可推广使用的正式版；两者无阶段门依赖，
仅共享既有代码与回归合同。正式版默认路径仍以冻结时的当前合同为准。

#### 审计依据与限制

- 原稿：`DMG-MD-thermostat-barostat-potential-rollout-plan.md`；SHA-256：`37213bb07bb72c2d7a5ca64bc6436f36a83c239df2c77b3a3627d357a4c57711`。
- DMG-MD HEAD：`4457c3c1ff56e3c326508bf3075fa4ffcd7cf96c`；本次开始时已有 7 个 tracked 修改、
  4 个 untracked O11 文件，不能沿用“工作区干净”。未覆盖或提交这些既有工作。
- GPUMD：`9d23496e41319b9e2af5221a7df6285387401d1e`。三个 reference 的 tracked/untracked
  状态均干净；GPUMD 已有 208 个被忽略文件（旧构建产物等），另外两个为 0；本轮只读核对。
- 核对方式：`git status --short`、`git rev-parse HEAD`、`rg -n` 符号及分段源码阅读；
  重点为 parser → dispatcher → VV/thermo/输出、上游系综初始化/RNG/邻居路径。
  下列为静态审计证据，**不是数值试验通过声明**。
- 原稿所引调研报告未随附，章节无法独立核验。科学方法参考
  [BDP 原始论文](https://arxiv.org/abs/0803.4060)与
  [SCR 原始论文](https://arxiv.org/abs/2006.09250)。前者给出正则速度重标定方法，后者强调正确体积涨落；
  这支持方法选择，不证明锁定实现及其任意有限步长自动满足目标分布。

#### 必须处理的问题

| ID / 程度 | 证据、影响 | 本流程处理 / 验收落点 |
| --- | --- | --- |
| F01 / P0 | `src/run_parser.cpp::parse_run_file` 不限制全文件单次 ensemble；M1 按命令执行。原稿全局禁重会破坏既有脚本 | A1 保留旧行为；新增系综 guard 与 parser 同步落地，纳入 runtime 主责范围 |
| F02 / P0 | `runtime.cu::run_segment`、`domain_runtime.cu` 当前在各 rank 对全局温度算 Berendsen 因子；改为 root 广播会新增 collective | A2 共享纯函数，旧 Berendsen 不新增通信；新链/RNG 才采用 root 状态 |
| F03 / P0 | 上游 `main_gpumd/run.cu::perform_a_run` 每段调用 `Integrate::initialize`；后者重建 NHC/BDP/SCR。原稿“跨段沿用”不是锁定上游行为 | G0-D9 明确段边界；A2–A4 单段/多段分开验收，固定 seed 逐段重用需明示 |
| F04 / P0 | 上游 `ensemble_npt_scr.cu::cpu_pressure_isotropic` 的 RNG 按值传参，`svr_utilities.cuh::gasdev` 有静态缓存 | G0-D10 决定持续流与兼容偏离；B3 做独立噪声 fixture/定量统计，不能只复刻上游就宣称严格采样 |
| F05 / P0 | 当前 M1 每段起始重算力；NPT 拟在步末缩放后保留缩放前力。重启时重算力与连续下一步所用缓存可能不同 | B2/B4 取消恒温即连续的保证；明确 snapshot 中坐标/力/virial 时点；若要求轨迹连续，另立力状态/步序方案 |
| F06 / P0 | GPUMD `Neighbor::find_neighbor_global` 以位移检查复用表；LJ/EAM 都使用它，原稿盒合同未覆盖非 NEP 邻居缓存 | C0 增盒变更失效接口；B1/C2/C3 测收缩后新邻居，不允许仅刷新 Box 就沿用旧表 |
| F07 / P0 | 上游 `neighbor.cu` 的邻居写入 `NL[N*count++ + n1]` 未传容量；照抄“溢出行为”可能越界 | C2/C3 必须写入前检查容量并失败关闭；安全适配与容量内数值兼容分别验收 |
| F08 / P0 | “体积有限/不塌缩”“2% 或 2SE 取宽”不能证明正确涨落；8 seeds/20k 步不是充分采样保证 | A5/B4 使用预注册误差界、有效样本量和步长收敛；证据不足为 INCONCLUSIVE，不通过发布门 |
| F09 / P0 | 原稿 G-C 失败可发子集、C2 的 NPT 组合可选、C5 暗示 NEP M2a 支持全部系综，彼此冲突 | G-C 全部 P0 必须通过；NEP M2a 仅 NVE/三种 NVT；势×系综联合门不可省略 |
| F10 / P1 | A1 主责文件不含其要求的 runtime guard；C1 反依赖尚未实现的 C2/C3；C4 可选却串在 C5 前 | 修订主责范围和依赖图；G-A 后接 B2/C0；C1 元数据可识别但未实现拒绝；C4 独立 |
| F11 / P1 | NHC 质量需 `dt*Tdamp`，与术语表“不乘 dt”矛盾；LJ 构造端 double 计算后存 float | 修订单位与精度说明；不得把源表达式都降为 float |
| F12 / P1 | 任意压缩不必为正压，负目标压力不一定导致扩张；剪切不保证应力非对称 | B0/B2 用已知参考态和实际压力差判方向；非对称布局用合成数据测 |
| F13 / P1 | 标量广播也有延迟；按需 NVE 与每步反馈 NVT 的成本不等；3 次中位数不足以稳健裁定小幅回归 | D4 匹配反馈需求与基线，配对测量并报 CI；5%/10% 仅规划目标 |
| F14 / P1 | 缺少原稿引用调研文件、D6 不存在、原稿路径指向 Pres/DMG-MD | 删除失效链接，D6 改指 B4/D3；以本计划为唯一流程入口 |

全文“严格正则/NPT”指算法目标，不是对未实现程序或任意步长的保证；以本节定量验收为准。

#### 统计验收补强（A3/A4/B3/A5/B4 必须共同落实）

1. 在看正式结果前，将体系/输入哈希、seed、丢弃段、块长度规则、目标误差界、置信度、
   最低有效样本量和最大采样预算写入测试 manifest。8 seeds、20k 步和 10% 丢弃只作为先导建议。
   不采用“2% 或 2SE 取宽”作为正式门；CI 太宽则延长至预定预算或记 INCONCLUSIVE。
2. 在无约束且确有相应独立动量自由度的 fixture 上检查 `K` 的正则均值/方差。
   必须分清输出温度仍按 `3N` 定义，与采样 fixture 的有效自由度是否相同；
   初始去质心、`correct_velocity`、全局缩放不能随机化所有动量方向等限制都要记录。
   不为通过分布检验修改既有温度定义，也不以零相互作用 BDP 体系单独证明遍历性。
3. SCR 不能只验证 `<P>`/`<T>`。至少比较体积均值、方差与分布，并用独立可解模型或独立
   验证的 NPT sampler 作参照；涨落-响应检验中的压缩率应来自独立响应测量，不能把用户
   输入的耦合参数 B 直接当成材料真实模量。锁定 GPUMD 仅是兼容对照，不能作为唯一科学 oracle。
4. 固定温度/压力、固定步长的平衡采样与 ramp/自适应步长功能分开；做减半步长的收敛检查，
   保持物理耦合时间一致（步数型 damping 随 dt 调整）。算法有正确目标分布不等于有限 dt 无偏。
5. MPI 负例必须有超时和全 rank 终止证据：root 采样/校验失败时不得令其他 rank 永久等广播；
   种子不一致、输入错误、邻居溢出和 GPU 错误均不得生成看似有效的最终输出。

#### 发布覆盖与实施节奏

- 先完成 A0/G0 与许可来源盘点，再完成 G-A；随后依次做 B0/B1/G-B、C0–C3/C5，最后 D3–D5。
  文档/CLI/来源清单可提前准备，但不能替代相应数值门。此处不承诺固定工期；A0 后根据设备、
  oracle 可用性、统计有效样本量和责任人分别估算，不能将任务卡数当作工作日数。
- P0 矩阵：NEP/LJ/EAM 在 M1 的六种系综；NEP 在 M2a 的 NVE、nvt_ber、NHC、BDP；
  各自覆盖声明支持的 P1/P2/P4 和后端。所有组合至少短程/错误/输出/restart smoke；
  新方法另有代表体系的长期统计与 NVE 漂移验收。NEP4/5/ZBL 分别列 fixture，不能以一种代替全部。
- M1 非 NEP / NPT 的 P>1 是全系统复制计算，增加 GPU 不代表力计算加速；在指南中明确规模、
  显存及伸缩性边界。多节点能力只按已实际验收的范围宣传，不由本机 MPI 通过推导。
- D4/D5 还须明确受支持的 GPU 架构、驱动/CUDA/MPI/UCX 组合、源码包/容器/二进制选择、
  干净安装与重建步骤、长程稳定性 profile、CI/人工验收入口、已知问题和问题报告路径。
  长程 profile/步数由 manifest 固定；已有未执行的 release 长程矩阵不能用 smoke 代替。
- 本轮未判定许可证法律兼容性、未签署 G0、未宣称 v1.0 支持已生效。D2 保留逐文件来源/分发清单与
  人工许可复核；不直接把根许可证改成某种许可证。待确认项仅影响未来实现/发布，不阻塞本轮文档交付。

## 1. 产品目标与非目标

### 1.1 目标

把 DMG-MD 从“NEP 专用 NVE/nvt_ber 运行时”推进为可对外发布的经典 MD 软件：

1. **恒温器模块**：在保留 `nvt_ber` 的前提下，提供严格正则采样器 `nvt_nhc`（确定性）与
   `nvt_bdp`（随机），共享一套目标温度调度和速度缩放实现；M1 与 M2a 两条数据面行为一致。
2. **控压器模块**：提供各向同性 `npt_ber`（兼容 GPUMD）与 `npt_scr`（以正确 NPT 采样为目标的采样器（须通过统计门）），
   正确处理变盒、缓存失效、所有权刷新、输出与 restart；v1 限定 M1。
3. **势函数支持**：把 NEP 专用力计算抽象为 `ForceField`，新增 LJ、EAM/alloy（P0）与
   Tersoff 1989（P1）；非 NEP 势 v1 只支持 M1，M2a 明确拒绝而不是静默降级。
4. **产品化**：版本/CLI、错误契约、restart 语义、文档、许可证、性能回归门、打包与发布清单。

### 1.2 v1.0 范围（P0）

| 模块 | v1.0 必须支持 | 明确不做 |
| --- | --- | --- |
| 系综 | `nve`、`nvt_ber`（保持原语义）、`nvt_nhc`、`nvt_bdp`、`npt_ber`、`npt_scr` | NPH、MTTK、Parrinello–Rahman、Gaussian/DPD、Andersen |
| 控压模式 | 各向同性；正交盒；全周期 | 3 个对角分量、6 分量三斜、非周期控压 |
| 并行 | M1（含 P>1 replicated fallback）上的控压器与非 NEP 势；M2a 上的 NHC/BDP | M2a 上的 NPT、M2a 上的非 NEP 势 |
| 势函数 | NEP4/5/ZBL（不变）、LJ、EAM/alloy | Tersoff 1988/mini、ADP/FCP、DP/NNAP/ILP、charge/dipole/polarizability、多势混合 |
| 状态 | `restart.xyz` 与 GPUMD 兼容；明确记录未保存的系综状态 | 默认写额外 restart 状态文件 |

### 1.3 P1 / P2 / 非目标

- **P1（v1.1 候选）**：`tersoff_1989`；`nvt_lan`/`nvt_bao`；`npt_ber`/`npt_scr` 的 3 个对角
  分量；LAMMPS 动力学（NVT/NPT）横向验证；可选 restart sidecar。
- **P2（暂缓）**：NPH；MTTK；Parrinello–Rahman；三斜 NPT；M2a NPT；M2a 非 NEP；ADP/FCP；
  多势混合/`potential` 方向参数；`fix`/`move`/`deform`/`change_box`/`minimize`；PLUMED/kspace。
- **非目标**：不改变 NEP 数学与既有 golden；不在此计划中做 O10/O11 性能优化；不承诺跨节点
  I/O；不把 GPUMD 全功能搬进来。

### 1.4 成功的定义

- 每个 Gate 的验收矩阵**实际执行**并留下环境、命令、结果与限制；skip/子集单独标注。
- 现有 `nve`/`nvt_ber` 的 golden 在各层（P1、M1 P>1、M2a、两后端）保持通过；新增功能不改变
  默认路径。
- 新系综的物理验收区分“确定性 golden”和“随机采样统计”，绝不用 Berendsen 的涨落去证明
  正则采样（见调研报告 §0.5、§4.9）。
- 发布物含版本、许可证、第三方声明、用户文档、已知限制与可复现的测试入口。

---

## 2. 当前基线（Agent 的事实起点）

> 行号对应 DMG-MD `4457c3c`。执行时先 `rg` 符号；不要把行号当成稳定接口。

### 2.1 已实现能力

- 两条数据面：**M1 replicated-full**（`src/runtime.cu`；P=1 与 M1 fallback，逻辑 slab 所有权
  `RuntimeOwnership`）与 **M2a local-domain**（`src/domain_runtime.cu`；owned/dependency ghost、
  两跳 halo、p2p migration）。
- 系综只有 `nve` 与 `nvt_ber`；`nvt_ber` 的目标温度在每个 `run` 段内按 `step/steps` 线性插值，
  耦合系数为 `1/TAU`，每次 VV 第二步后缩放 owned 速度。
- 势只有普通 NEP4/5 与 NEP-ZBL；每个作业恰好一个 `potential`。
- 输出：`thermo.out`（18 列）、`dump_xyz`（位置/速度/力/势能/virial/group 等可选量）、
  `restart.xyz`（N、pbc、Lattice、species/pos/mass/vel/group）。
- M2a 的 `need_thermo = ensemble.kind != nve`；NVE 时不计算 thermo，除非有输出消费。
- M1 的 `RuntimeOwnership` 缓存 `inverse_box_` 与 `partition_axis_`；每步 `recompute_and_commit`
  验证 owner map 并在变化时迁移 velocity/unwrapped。
- M2a 的 eligibility 由 NEP 的 radial/angular cutoff 与盒子几何决定；非正交/非全周期在 P>1 明确报错。

### 2.2 必须保持的数值/语义合同

| 合同 | 当前事实 | 影响 |
| --- | --- | --- |
| 温度自由度 | `T = Σ m v² / (3N k_B)`，不减质心、不减固定原子 | NHC/BDP 必须沿用，否则温度/压力统计漂移 |
| 目标温度调度 | 每段 `T = T1 + (T2-T1)·step/steps`，`step` 为段内 0 基 | 多 `run` 段与 restart 会重置调度 |
| 耦合常数单位 | `nvt_ber` 的 TAU 实际是“步数”（GPUMD 打印为 time_step；实现不乘 dt） | NHC 质量、NPT 的 Pdamp 必须同单位 |
| 内部应力 | `(virial + kinetic)/V`，自然单位 eV/Å³ | 输出才乘 `PRESSURE_UNIT_CONVERSION=1.602177e+2` 得 GPa |
| virial 布局 | `xx yy zz xy xz yz yx zx zy`；thermo 内部顺序 `T PE sxx syy szz sxy sxz syz` | barostat 取 `[2..4]`；输出列顺序是 `sxx syy szz syz sxz sxy` |
| thermo 列 | `T KE PE sxx syy szz syz sxz sxy ax ay az bx by bz cx cy cz` | NPT 的盒列必须与 `restart.xyz` 一致 |
| 位置缩放 | GPUMD 绕原点缩放位置，不做质心平移；盒子同步缩放后 `get_inverse()` | 与质心约定、unwrapped 语义相关 |
| restart | 只有盒/位置/速度/质量/group；无系综链变量、无 RNG、无盒速度 | NHC/BDP/SCR 的 restart 连续性必须单独记录 |
| 力 | NEP 的 M1 路径每步 wrap + 清空 + 全量计算；M2a 有自己的 halo/cache | 变盒必须让 M1 与 M2a 的缓存假设显式失效 |

### 2.3 缺口清单

| 缺口 | 位置（基线） | 风险 |
| --- | --- | --- |
| 只有 `nve`/`nvt_ber`；parser 直接拒绝其他 ensemble | `src/run_parser.cpp:199`、`include/dmgmd/run_ir.hpp:24` | 无法提供严格 NVT/NPT |
| 恒温器代码在两条 runtime 重复 | `src/runtime.cu:724`、`src/domain_runtime.cu:1927` | 新系综两份实现必然漂移 |
| 无压力控制；盒子加载后不可变 | `src/runtime.cu:646` 的 `run_segment` 使用固定 `Box&` | barostat 需要盒变更合同 |
| 内部应力不在 kernel 中换算为 GPa | `src/runtime_internal.hpp:546`、`src/runtime.cu:1086` | barostat 单位/符号易错 |
| 只有 NEP；metadata parser 只认 NEP 头 | `src/model_parser.cpp:257` | 非 NEP 势无法进入 |
| 无非 NEP 的 `ForceField` 抽象；`NepForce` 绑定 `NEP` | `src/runtime.cu:527` | 新势会复制运行时分派 |
| M2a 依赖 NEP cutoff 做 eligibility | `src/runtime.cu:1310` 附近 | 非 NEP / NPT 需要显式拒绝或降级理由 |
| 无系综状态输出/恢复 | `src/runtime.cu:1208` | NHC/BDP/SCR restart 连续性不可承诺 |

### 2.4 当前代码锚点

| 路径 | 符号/位置 | 用途 |
| --- | --- | --- |
| `src/runtime.cu` | `RuntimeOwnership`（约 118） | M1 slab 所有权、迁移、plan |
| `src/runtime.cu` | `find_owned_thermo_sums`（约 412） | 8 分量 thermo 归约，含 kinetic 应力项 |
| `src/runtime.cu` | `compute_thermo`（约 484） | allreduce + volume/T 归一化 |
| `src/runtime.cu` | `NepForce`（约 507） | NEP 专用 force/workspace |
| `src/runtime.cu` | `run_segment`（约 646） | M1 步序：VV1 → gather → force → ownership → VV2 → thermo → thermostat |
| `src/runtime.cu` | Berendsen（约 724） | 目标插值、`1/tau`、`scale_owned_velocity` |
| `src/runtime.cu` | `write_thermo_row`（约 1086） | GPa 换算与列顺序 |
| `src/runtime.cu` | `write_restart`（约 1208） | restart 字段 |
| `src/runtime.cu` | `run_replicated`（约 1250） | 模式决策、NEP 解析一次、M1/M2a 路由 |
| `src/domain_runtime.cu` | `compute_domain_thermo`（约 1408） | M2a thermo |
| `src/domain_runtime.cu` | `need_thermo`（约 1725） | M2a 按需 thermo |
| `src/domain_runtime.cu` | Berendsen（约 1927） | M2a 重复实现 |
| `src/domain_runtime.cu` | `run_local_domain`（约 2148） | M2a 状态机与步序 |
| `src/runtime_internal.hpp` | `normalize_global_thermo`（约 546） | 除以 `3N k_B` 与体积 |
| `include/dmgmd/run_ir.hpp` | `EnsembleKind`（约 24） | 需要扩展的 IR |
| `src/run_parser.cpp` | `ensemble` 分支（约 199） | 语法扩展点 |
| `include/dmgmd/spatial_ownership.hpp` | `slab_owner_of_fractional`（约 28） | 分数坐标→owner |
| `src/gpumd_compat/box.cu` | `get_inverse`（104）、`get_num_bins`（143）、`set_is_orthogonal`（164） | 变盒后必须刷新 |
| `src/gpumd_compat/common.cuh` | `K_B`（60）、`PRESSURE_UNIT_CONVERSION`（63） | 单位常数 |
| `src/model_parser.cpp` | `load_potential_metadata`（257） | 势格式扩展点 |
| `tests/CMakeLists.txt` | CTest 注册 | 新 CPU 测试入口 |

### 2.5 上游证据（只读 GPUMD，已由源码阅读确认）

> 下面是本计划的工程依据，不是对 DMG-MD 现状的声明。执行 Agent 仍须用可执行 oracle 复核
> 压力符号；行号均指锁定 commit。

| 事实 | 上游锚点 | 对计划的含义 |
| --- | --- | --- |
| `nvt_nhc`/`nvt_bdp`/`nvt_lan`/`nvt_bao` 都是 5 token：`T1 T2 Tdamp` | `src/integrate/integrate.cu:425-444` | parser 语法与校验 |
| `npt_ber`/`npt_scr` 接受 8/12/18 token；各向同性 8 token 顺序为 `T1 T2 Tdamp P B τ_p`（P/B 为 GPa，τ_p 为步数） | `src/integrate/integrate.cu:455-463`，参数读取 631-712 | v1 只开放 8（各向同性） |
| NPT 输入压力/模量为 GPa；内部 `target_p /= 160.2177`，`pc *= 160.2177` | `src/integrate/integrate.cu:1152-1153`、1189-1190 | 不能把 GPa 直接与自然单位应力比较 |
| 内部压力 = `(virial + kinetic)/V`；`find_thermo` 用 3N；应力数组顺序 `xx yy zz xy xz yz` | `src/integrate/ensemble.cu:434-633`、636-673 | 与 DMG-MD thermo kernel 对齐 |
| Berendsen：VV2 → thermo → 温度缩放 → 盒/位置缩放；位置绕原点；缩放后 `box.get_inverse()` | `src/integrate/ensemble_ber.cu:195-280`，`npt_utilities.cuh` | NPT 的步序与输出时点 |
| NHC：链长 4；初始 pos=0、vel=[1,-1,1,-1]；质量用 `T1` 与构造时 dt；前后各半步；CPU Suzuki–Yoshida（7 项 × 4 RESPA） | `src/integrate/ensemble.cuh:24,106-111`，`ensemble_nhc.cu:30-50,105-236,402-476` | 需要 pre-force hook；质量不随 T ramp 更新 |
| BDP：host `mt19937`，release 用 wall-clock 种子；`resamplekin` 产生全局缩放因子 | `src/integrate/ensemble_bdp.cu:29-35,89-100` | 需要确定性种子策略与 root 广播 |
| SCR：Berendsen 缩放 + `sqrt(2/3·pc·k_B·T/V)·gauss`（各向同性）；温度用 BDP resample | `src/integrate/ensemble_npt_scr.cu:140-170,216-280` | 真正的 NPT 采样器，但必须统计验收 |
| `restart.xyz` 仅 N/pbc/Lattice/species/pos/mass/vel/group | `src/measure/dump_restart.cu:75-130` | NHC/BDP/SCR 重启非轨迹连续 |
| 势分发表包含 `lj`、`tersoff_1989`、`eam/alloy` 等；`eam/alloy` 可选第三 token `max_neighbor`（1..1024，默认 400） | `src/force/force.cu:93-190` | v1 势范围与 `potential` 第三 token |
| LJ 文件：首行 `lj T symbols…`，随后 T×T 行 `epsilon sigma cutoff`；无 shift | `src/force/lj.cu:30-70` | LJ 格式与 cutoff |
| NPT 各向同性要求全周期；各向异性 3 分量要求正交盒；三斜要求三斜盒 | `src/integrate/integrate.cu:655-693` | v1 明确拒绝非各向同性 |

### 2.6 架构结论

1. **先有共享系综模块，再有新系综**。当前 M1/M2a 各有一份 Berendsen；直接加 NHC 会变成四份。
2. **NHC 需要“力计算前”的全局 KE**；BDP 与 Berendsen 只需要力计算后的 thermo。步序 hook 必须在
   A2 就设计好，而不是 A3 临时插。
3. **NPT 的本质是盒变更**，不是多一个缩放 kernel。盒变更涉及 `Box` 的逆/float 副本、M1 所有权缓存、
   M2a 的邻居/halo 缓存、输出与 restart；v1 把范围收窄到 M1 是降低风险的关键决策。
4. **非 NEP 势不能复用 NEP 的 M2a 域分解**；先用 M1 打通物理正确性与格式/金标，再评估 M2a。

---

## 3. 硬约束（每个 Agent 必须遵守）

### 3.1 仓库与流程

- 先读 `AGENTS.md`；它优先于本计划。不要直接调用 GPUMD 代码；需要逐字复制的只有
  `src/gpumd_compat/` 中带 Origin 头的最小内核，且必须保持数学、布局、launch 参数不变。
- `../gpumd-reference`、`../lammps-reference`、`../lammps-matpl-reference` 严格只读：不打补丁、
  不复制源码进构建、不在其中构建、不生成日志/输出、不清理。
- 不提交、不推送、不做破坏性 Git 操作，除非维护者明确要求。不要因为测试失败就更新 golden 或放宽容差。
- 最小改动；不做与本 Step 无关的重构/格式化；新增文件必须被构建或测试引用。
- 用中文回报；文件/符号/所有权/MPI 影响/测试/证据/限制逐项写清。未运行 = 未运行，skip = skip。

### 3.2 数值不变量

- **NVE 与 `nvt_ber` 的既有输出必须保持**：同输入、同 rank/backend 下，`thermo.out`、`dump_xyz`、
  `restart.xyz` 与基线一致（按现有 manifest 容差；A2 重构目标是 byte-identical）。
- 温度自由度始终 `3N`；不得引入质心扣除、固定原子扣除或“更物理”的自由度修正，除非另立决策并
  重做所有 golden。
- 不得改变 NEP 的数学、邻居构建、float/double 精度或累加顺序；非 NEP 势必须逐字复刻参考内核的
  浮点行为（包括 float 累加）。
- 输入耦合参数单位是**步数**；Berendsen/BDP/SCR 的步尺度表达式不额外乘 dt，
  NHC 质量必须使用 `tau = dt_internal * Tdamp`，不能重复换算时间单位。
- 压力换算只在明确的边界做一次：输入 GPa→自然单位、耦合系数乘 `PRESSURE_UNIT_CONVERSION`；
  内部比较全部用自然单位。
- 位置缩放绕原点；不引入质心平移；barostat 不缩放速度（Berendsen 语义）。
- 随机系综必须 root 拥有标量状态并广播因子；不得让各 rank 用各自的 RNG 独立采样。

### 3.3 失败关闭

- 新 parser 接受的组合，若运行时尚未实现，必须在任何动力学前以带来源位置的错误退出，不能
  静默退化为 NVE 或忽略参数。
- 自动模式中的 NPT/非 NEP 显式路由到 M1 并打印原因；强制 M2a（若提供此选项）或直接 domain
  调用必须拒绝。其他不支持组合（非正交/非周期 NPT、12/18 token NPT、目标压力非有限、
  体积模量非有限/非正）必须明确报错；不能“尽力而为”。
- 新系综只与默认调度/overlap 关闭组合验收；实验性 `DMGMD_*` 开关与新系综同时使用时，要么有
  专项验证，要么失败关闭。

### 3.4 证据纪律

- 每个 Step 的验收结果写入带日期的 status 文档；保留 revision/dirty 身份、GPU/驱动/CUDA、
  MPI/UCX、精确命令、case/seed/ranks、原始输出位置与限制。
- 确定性系综（NHC、`npt_ber`）用 GPUMD reference 短轨迹 golden；随机系综（BDP、`npt_scr`）
  不得声称 byte-exact，使用分布/均值/涨落/自相关与多种子置信区间。
- 跨 rank/backend 不默认 byte-exact；同二进制重复运行与同工具链前后版本的零变化重构对照分别记录，后者不是“同二进制”。
- 性能数字必须标明机器、GPU、rank/backend、重复次数与统计口径；不与正确性门混用。

---

## 4. 总体路线图

### 4.1 依赖图

```text
G0 + A0 ─ A1 ─ A2 ─ (A3 + A4) ─ A5 ─ A6 = G-A
A0 ─ B0 ─ B1；G-A + B1 ─ B2 ─ B3 ─ B4 = G-B
G-A ─ C0 ─ C1 ─ C2 ─ C3 ─ C5 = G-C；C4 为独立 P1，不阻塞 C5
A2 + B1 + C0 ─ D1
G0-D7 ─ D2
A5 + B4 + C5 ─ D3
A0 + D1 + D3 ─ D4
D1 + D2 + D3 + D4 ─ D5
```

- **A**：先建立 IR 与共享模块，再逐个接入 NHC/BDP；A5 是双路径/MPI 验收门。
- **B**：B0/B1 是可独立验证的地基；B2/B3 依赖 G-A 与 B1（B3 还依赖 A4）。B0 可与 A2 并行，
  但 B2 之后不能绕过共享 thermostat 再实现一份。
- **C**：C0/C1 只做抽象与格式分发，不改 NEP 数值；C2/C3/C4 各自独立，可并行，但都改
  `ForceField` 工厂/构建，建议串行或在隔离 worktree 中执行后合并。
- **D**：产品化收尾；D2（许可证）可最早启动，因为 GPL 义务会影响分发方式。

### 4.2 并行执行建议

- A3 与 C0 **不要**并行修改 `src/runtime.cu`（A3 改步序，C0 改 force 实例化）。
- B2 与 C2 可以并行（不同文件），但 B2 需要 G-A/B1；C2 需要 C1；共有 dispatcher/工厂修改仍应串行。
- 任何并行都使用独立 worktree/分支；Agent 不得自行合并或提交。

### 4.3 阶段门

| Gate | 通过条件 | 失败处理 |
| --- | --- | --- |
| G0 | 维护者确认第 5 节决策 | 未确认不开始 A1 |
| G-A | A0–A6 完成；NHC/BDP 在 M1/M2a、P1/P2/P4、两后端通过；确定性 golden 与统计证据入库；文档更新 | 不进入 B2/B3；先修 A |
| G-B | B0–B4 完成；压力 oracle、NPT 短程 golden、restart、P>1 fallback、M2a 拒绝、涨落统计全部通过 | 不进入 v1.0 发布 |
| G-C | C0–C3/C5 完成；LJ/EAM 静态与短程 golden、P 不变性、联合系综矩阵、M2a 防护、文档更新 | 未过不发布既定 v1.0；缩减范围须先修订范围和验收清单 |
| G-D | D1–D5 完成；许可证/文档/打包/性能门/发布清单通过 | 不发布 |

---

## 5. Gate 0 决策（默认建议）

维护者逐项确认；若改变，括号内列出受影响 Step。

| 编号 | 决策 | 默认建议 | 改变的影响 |
| --- | --- | --- | --- |
| G0-D1 | v1.0 恒温器集合 | NHC + BDP；保留 `nvt_ber`；Langevin/BAOAB 放 P1 | A3/A4/A7 |
| G0-D2 | NPT 范围 | `npt_ber` + `npt_scr`，仅各向同性，仅 M1（含 P>1 M1 fallback）；M2a 明确拒绝 | B1–B4；M2a 变盒另立计划 |
| G0-D3 | 非 NEP 势优先级 | v1.0：LJ → EAM/alloy；Tersoff 1989 为 P1 | C2–C4；发布矩阵 |
| G0-D4 | restart 策略 | 严格 GPUMD：`restart.xyz` 不含系综状态；NHC/BDP/SCR 不承诺轨迹连续；恒温 `npt_ber` 也须按 §0.5 验证，写入文档 | A3/A4/B3/D3 |
| G0-D5 | 随机数策略 | rank 0 拥有 `mt19937`，固定默认种子 + `DMGMD_ENSEMBLE_SEED` 覆盖；每步广播因子 | A4/B3；这是对 GPUMD wall-clock 种子的**有意偏离**，必须记录 |
| G0-D6 | NPT 下 unwrapped 位置 | 严格 GPUMD：barostat 不缩放 unwrapped；文档标为已知限制 | B1/B2；若选择修正，需要额外状态与跨引擎证据 |
| G0-D7 | 许可证 | 采用 GPLv3 兼容分发；发布前人工法务复核；保留 GPUMD 版权/来源声明 | D2；若不接受 GPL，必须重做非 NEP 势与 compat 的许可路径 |
| G0-D8 | 系综标量状态归属 | rank 0 持有 NHC 链/BDP RNG/盒缩放标量；按需广播；既有 nvt_ber 无新增广播 | A3/A4/B2/B3；影响可复现性与通信记账 |
| G0-D9 | 多 run 状态生命周期 | 默认每段初始化以对齐上游对象生命周期；固定 seed/cache 的重置及序列重复须明示；若选跨段持续状态，记录兼容偏离 | A1–A4/B3，多段与 restart 测试 |
| G0-D10 | SCR 科学实现与上游 RNG 差异 | 推荐 root 持续随机流 + 实例缓存；明确偏离按值 RNG，独立统计门通过前不宣称正确 NPT 采样 | B3/B4；兼容矩阵与独立 oracle |

上述默认建议已由维护者于 2026-10-30 在本会话逐项确认，G0-D1–G0-D10 无例外调整。
若后续要求严格逐轨迹重启或更完整的多节点支持，须另行将相关状态保存/长程与部署矩阵提升为 P0，
不得在发布说明中事后补充。

> 推荐理由：NHC 与 BDP 覆盖“确定性严格采样”和“随机严格采样”两种主流需求；Berendsen 保留是
> 兼容合同。SCR 是相对便宜且真采样的 NPT；MTTK/PR 的复杂度与验证成本不适合首版。非 NEP 先做
> M1 能把风险限制在力/格式层，不牵动 halo/所有权。root 广播标量使状态归属清楚；字节数虽小，同步延迟仍须 D4 实测。

## 6. Phase A：恒温器与共享系综模块（A0–A7）

> 目标：先把“目标温度调度、速度缩放、随机数所有权、步序 hook”收进一个共享模块，再接入 NHC
> 与 BDP；任何新系综都不得在 M1/M2a 各写一份。A2 的 Berendsen 迁移必须是零数值变化的。

| Step | 内容 | 优先级 | 依赖 | 关键产出 |
| --- | --- | --- | --- | --- |
| A0 | 基线冻结与环境证据 | P0 | — | 可复现的 baseline manifest |
| A1 | IR/parser 扩展（NVT 部分） | P0 | A0 | 新语法与失败关闭 |
| A2 | 共享 `EnsembleController` + Berendsen 迁移 | P0 | A1 | 两条 runtime 共用一份控制逻辑 |
| A3 | `nvt_nhc`（确定性严格 NVT） | P0 | A2 | M1/M2a 的 NHC |
| A4 | `nvt_bdp`（随机严格 NVT） | P0 | A2（root RNG 接口） | 可复现的 BDP |
| A5 | 双路径/MPI/后端验收矩阵 | P0 | A3、A4 | G-A 证据 |
| A6 | 文档、统计验收与限制 | P0 | A5 | 用户可见合同 |
| A7 | `nvt_lan`/`nvt_bao`（可选） | P1 | A5；维护者另行批准 | 不阻塞 v1.0 |

---

### Step A0：基线冻结与环境证据

- **状态**：DONE｜**优先级**：P0｜**依赖**：无（Gate 0 确认前也可做）
- **实际验收记录**：[`A0 系综开发前基线冻结`](../status/validation/ensemble-baseline.md#ensemble-baseline-20261030)；性能项按任务卡记为 `PENDING-D4`。
- **目标**：在动手改代码前，冻结“什么算没坏”的基线：revision、构建环境、现有测试结果、
  代表性输出哈希与性能基线。后续 A2/B2/C0 的“零变化”都以本 Step 的 manifest 为准。
- **主责文件**：只新增/修改证据文档与测试清单：
  `docs/status/validation/ensemble-baseline-<YYYYMMDD>.md`；如需脚本，放 `tests/baseline/` 或
  `tests/benchmark/` 并纳入 CTest。**本 Step 不修改运行时、parser、compat 或 NEP 数学。**
- **必读**：`README.md` 的构建/测试/MPI 章节；`docs/standards/golden-test-standard.md`；
  `docs/standards/compatibility-matrix.md`；`tests/baseline/README.md`；`tests/long_nve/README.md`；
  `AGENTS.md`。
- **实施要点**：
  1. 记录 `git rev-parse HEAD`、`git status --porcelain`、未跟踪文件；若工作区不干净，记录每个
     文件的哈希并说明是否影响基线。计划基线是 `4457c3c`，但以实际执行时的仓库为准。
  2. 记录构建身份：OS、CPU、GPU 型号/显存、驱动、CUDA Toolkit、host compiler、CMake、Open MPI、
     UCX、`../env/md-mpi.sh` 的 revision/哈希；记录 `build/dmg-md` 的 SHA-256。
  3. 按 README 执行：完整 `ctest --test-dir build --output-on-failure`、baseline runner
     （`tests/baseline/run_baselines.py`）、long_nve smoke、MPI 环境检查；单卡与可用的 P2/P4
     分别记录。没有条件执行的项写 `SKIPPED` 与原因，不能省略。
  4. 对至少一个 NEP NVE case 记录 `thermo.out`、`dump_xyz`、`restart.xyz` 的 SHA-256，以及
     每步 thermo 的列数与首尾值；对 `nvt_ber` 同样记录。后续 A2 先比哈希，再比数值。
  5. 记录一个可重复的性能基线（建议 NEP NVE，P=1 与可用的 P4/M2a，固定步数、warmup、3 次取中位数）。
     若当前机器不适合长时间基准，记录 `PENDING-D4`，不要编造数字。
  6. 把“baseline 的已知 flaky/skip/环境限制”单列，不要用它们解释新功能的失败。
- **测试/验收**：
  - 现有 CTest 全部通过（或逐项列出失败/跳过并证明与本次改动无关）；
  - baseline runner 的 4 个 case 通过；long_nve smoke 通过；
  - MPI 环境检查通过并记录 backend；
  - manifest 中的命令可被另一个 Agent 原样复现（至少同机同环境）。
- **证据**：上述 status 文档 + 原始日志（放 `/tmp/dmgmd-a0-<date>/` 或仓库忽略的 `artifacts/`）。
  文档中必须写清 revision、dirty 状态、GPU/驱动/CUDA、MPI/UCX、命令、case/ranks、结果与限制。
- **停止/回滚**：基线本身不干净或现有测试失败时停止，先报告；不要顺手修无关失败，也不要把
  失败测试标成 skip 来“通过” A0。

---

### Step A1：IR 与 parser 扩展（NVT 部分）

- **状态**：DONE｜**优先级**：P0｜**依赖**：A0
- **实际验收记录**：[`A1 NVT IR/parser 验收`](../status/validation/ensemble-infrastructure.md#ensemble-parser-20261030)；NHC/BDP 语法已接受，动力学仍由运行时预检拒绝。
- **目标**：让 `run.in` 能表达 `nvt_nhc` 与 `nvt_bdp`，并把参数校验、错误来源和重复命令规则
  固定下来；NPT 的语法留到 B2/B3，不在本 Step 放开，避免 parser 接受运行时未实现的组合。
- **主责文件**：`include/dmgmd/run_ir.hpp`（`EnsembleKind` 与 `EnsembleCommand`）、
  `src/run_parser.cpp`、`tests/unit/run_parser_tests.cpp`；`src/runtime.cu` / `src/domain_runtime.cu`
  仅增加动力学前的未实现系综 guard 与负例，不改数值路径。
- **必读**：`docs/standards/compatibility-matrix.md` 的 run.in 语法；`src/run_parser.cpp` 的
  `ensemble`/`known_unsupported` 分支；上表 §2.5 的 5-token 事实；`run_parser_tests.cpp` 现有风格。
- **实施要点**：
  1. `EnsembleKind` 增加 `nvt_nhc`、`nvt_bdp`；同时预留 `npt_ber`、`npt_scr` 的枚举值但
     **本 Step 不解析**。保留 `nve`/`nvt_ber` 的现有枚举值和所有既有行为。
  2. `EnsembleCommand` 用带默认值的字段表达三种 NVT：初始温度、最终温度、温度耦合；不要用
     一个含义随 kind 变化的“万能参数”。保持现有 `nvt_ber` 字段名与调用点兼容，优先新增字段而不是重命名。
  3. 语法严格镜像上游：`ensemble nvt_nhc T1 T2 Tdamp` 与 `ensemble nvt_bdp T1 T2 Tdamp`
     都是 5 token。温度必须有限且 >0；`Tdamp >= 1`（单位是步数，与现有 `nvt_ber` 的 TAU 相同）；
     非有限值、额外 token、缺 token 都报带文件和行号的错误。
  4. 保留现有 NVE/`nvt_ber` 的重复命令与分段换系综行为；当前 parser 不限制全文件只出现一次。
     对新增系综在预检阶段扫描整个程序并校验组合；新增状态在每段 `run` 初始化（§0.5、G0-D9）。
     不得为简化新控制器而全局禁止旧脚本中的多条 `ensemble`。
  5. parser 接受 NHC/BDP 后，`run_replicated` 在 A3/A4 落地前必须显式抛出“not implemented”
     类型错误（带输入来源；用户错误不暴露内部 Step 编号），不能静默按 NVE 跑。A3/A4 完成时只移除对应 guard。
  6. 保持 `known_unsupported` 行为：`nvt_lan`、`nvt_bao`、`npt_*`、`heat` 等仍报 unsupported，
     错误信息明确“当前版本不支持”，不要把它们误标为未知命令。
  7. 更新 `docs/standards/compatibility-matrix.md` 的语法表时，只写“parser 已接受、运行时待 A3/A4”
     会误导用户；本 Step 可暂不更新用户矩阵，A6 统一更新。
- **测试/验收**：
  - 新增 parser 正例：NHC/BDP 的合法 5-token 行；温度/耦合边界（`Tdamp=1`、较大值）。
  - 新增失败例：4/6 token、T<=0、非有限值、`Tdamp=0`、既有重复 `ensemble` 回归、新系综未实现 guard、NPT 仍 unsupported；
    错误包含文件与行号。
  - 现有 `run_parser_tests` 与完整 CTest 通过；NVE/`nvt_ber` 的解析结果与基线逐字段一致。
- **证据**：status 文档中列 parser 用例表（输入、期望、实际），并给出测试命令；不需要 GPU 证据。
- **停止/回滚**：任何现有 `nvt_ber` 语法/校验被改变；或为了“顺手支持 NPT”扩大 parser 范围。
  回滚只回退本 Step 的文件，不影响 A0 记录。

---

### Step A2：共享 `EnsembleController` 与 Berendsen 零变化迁移

- **状态**：DONE｜**优先级**：P0｜**依赖**：A1
- **实际验收记录**：[`A2 EnsembleController/Berendsen 验收`](../status/validation/ensemble-infrastructure.md#ensemble-control-20261102)。统筹者于 2026-11-02 独立复核 diff、测试、科学输出哈希、通信账本与文件保护；A/B 输出和通信记录一致。
- **目标**：建立 M1/M2a 共用的系综控制层：目标温度调度、速度缩放因子、NHC 的 pre-force hook、
  BDP/SCR 的 root RNG 边界、每步标量广播；先把现有 `nvt_ber` 迁移进来，证明输出逐字节不变。
- **主责文件**：新增 `include/dmgmd/ensemble_control.hpp`、`src/ensemble_control.cpp`（host 控制逻辑；
  如必须设备端缩放，只复用现有 kernel，不复制 NEP/compat 数学）；修改 `src/runtime.cu`、
  `src/domain_runtime.cu`、`CMakeLists.txt`、`tests/CMakeLists.txt` 与新增单元测试。**不得修改
  `src/gpumd_compat/`。**
- **必读**：`src/runtime.cu` 的 `run_segment`/Berendsen 段（基线约 646–760）、`src/domain_runtime.cu`
  的 Berendsen 段（约 1927）与 `run_local_domain`（约 2148）；`src/runtime_internal.hpp` 的
  `normalize_global_thermo`；`docs/standards/mpi-runtime.md` 的 collective 记账；`AGENTS.md`。
- **实施要点**：
  1. 控制层只拥有**标量/小状态**：当前 `EnsembleKind`、目标温度、耦合、NHC 链（4 个位置 + 4 个
     速度）、BDP/SCR 的 `std::mt19937` 与 `gasdev` 的 `iset/gset`、每步因子。有状态的新系综由 rank 0 持有，
     通过 `MpiRuntime` 的标量广播/归约分发；非 root 不维护第二份链或 RNG。
     无状态 `nvt_ber` 仍在各 rank 对已归约的相同全局温度调用共享纯函数，不增加广播。
  2. 提供明确的 hook，而不是让 runtime 自己判断 kind：
     - `before_force(time_step, kinetic_energy)`：只有 NHC 需要；在 VV1 之前产生速度因子；
     - `after_force(thermo, step, steps)`：Berendsen/BDP/NHC 的温度控制，以及后续 B 阶段的
       barostat 前置点；返回一个或多个标量因子；
     - `target_temperature(step, steps)`：严格复制现有 `T1 + (T2-T1)*step/steps`，`step` 是
       **段内 0 基**；不得把 `steps` 或 `step` 变成 double 之外的类型而改变舍入。
  3. Berendsen 迁移只抽取共享纯函数，不改公式与顺序：`coupling = 1/TAU`，仅当
     `coupling > 1.0e-5` 时 `sqrt(1 + coupling*(target/T - 1))`，在 VV2 + thermo 之后缩放
     owned 速度。M1 的 `scale_owned_velocity` 与 M2a 的对应 kernel 保持原样。
  4. 不为 NVE/`nvt_ber` 引入额外 collective：A2 后 NVE 不实例化 RNG/链；`nvt_ber` 的通信量与
     基线一致。若为了统一接口必须多一次归约，必须在卡内证明并得到维护者批准，否则视为失败。
  5. 状态生命周期：命令更新配置，每段 `run` 初始化控制器，与锁定 GPUMD 的
     `Run::perform_a_run → Integrate::initialize` 边界对齐。NHC 链/质量重建；BDP/SCR 的
     RNG/cache 初始化规则按 G0-D9 明示并测试。restart 是新作业，不保存隐藏状态。
     固定种子下逐段重置会重用随机序列，必须记录；跨段持续流属于另行明确的兼容偏离。
  6. 数值顺序保持：不要为了复用把 M1 的 `thermo.values[0]` 改成 M2a 的局部量再归约；两边都使用
     已归一化的全局温度/能量。所有全局标量必须在同一位置、同一顺序归约；记录通信量变化。
  7. 先只迁移 `nvt_ber`，NHC/BDP 分支在 A3/A4 才实现；A2 的测试必须能在没有新系综实现时通过。
- **测试/验收**：
  - 单元测试：目标温度调度在 `step=0`、`steps-1`、多段 `run` 边界的值；Berendsen 因子在
    `T<target`、`T>target`、`coupling<=1e-5` 三种情况下的位模式/相对误差；NVE 路径无状态分配。
  - A/B 对照：A2 前后两个独立二进制使用相同工具链/构建选项、同一输入、同 rank/backend；`thermo.out`、`dump_xyz`、
    `restart.xyz` 的 SHA-256 一致；若编译器导致不可比，必须先把 A/B 都固定到同一构建配置，
    再用 golden 容差解释，不能直接放宽容差。
  - M1 P=1、P=2 的 M1 fallback、M2a P=2 的 `nvt_ber` 均通过；通信记账与基线一致（或差异有
    明确解释并获批）。
- **证据**：status 文档给出 A/B 哈希表、通信量对比、单元测试输出；注明构建配置。
- **停止/回滚**：任何既有 `nvt_ber` 输出变化；M2a 行为与 M1 不一致；为抽象引入 NEP 数学改动；
  或新增了无法解释的 collective。回滚到 A1 的运行时，保留 A2 的测试设计供下次使用。

---

### Step A3：`nvt_nhc`（Nosé–Hoover 链）

- **状态**：DONE｜**优先级**：P0｜**依赖**：A2。
- **实际验收**：[NHC 记录](../status/validation/thermostat-validation.md#nhc-20261102)：四节链、SY7×RESPA4、
  owned KE/root 因子协议；独立逐位 oracle，GPUMD P1 200-step，双路径重复、11 组 KE 与
  实际 NEP 三轴速度矩通过。额外 harmonic K PASS、轴向 mean INCONCLUSIVE 单列，
  不作普遍遍历性保证。
- 已完成的实施细节迁入[系综标准](../standards/ensembles.md)与上述记录；质量固定于 T1、
  CV/自适应/实验选项限制和不连续 restart 均已明确。

---

### Step A4：`nvt_bdp`（Bussi–Donadio–Parrinello 随机速度重标定）

- **状态**：DONE｜**优先级**：P0｜**依赖**：A2（无 A3 RNG 依赖）。
- **实际验收**：[BDP 记录](../status/validation/thermostat-validation.md#bdp-20261102)：16 case 逐位 oracle、
  root RNG/实例 cache、默认 seed 与严格跨 rank 原始字符串校验；19 项 seed 合同、
  12 组八 seed KE（含 half-dt/GPUMD）和实际 NEP 三轴速度矩通过。
- 独立 harmonic K oracle PASS、额外轴向诊断 INCONCLUSIVE 保留；release GPUMD 只比分布。
  逐段重置与固定默认种子的有意偏离、restart 语义见[系综标准](../standards/ensembles.md)。

---

### Step A5：双路径、MPI 与后端验收矩阵（G-A）

- **状态**：DONE｜**优先级**：P0｜**依赖**：A3、A4。
- **实际验收**：[恒温器矩阵](../status/validation/thermostat-validation.md#thermostat-matrix-20261102)：
  52 短程配置及同配置重复、54 新作业 restart，NEP4/5/ZBL 的真实 M1/M2a 路由和后端逐项列出；
  正式 NEP 统计、root 因子位模式、逐步通信增量和错误/超时门通过，无必需项 SKIP。
- 旧 NVE/Berendsen 最终 golden、MPI 和七配置 long-NVE smoke 通过；对 A2 的
  50 stage/181 科学文件、231 全局/160 rank 逐步记录完全相同。
  G-A 仅按已验收单节点范围放行，不替代跨节点、release 长程、D4 或未来 C5 联合门。

---

### Step A6：恒温器文档与统计验收

- **状态**：DONE｜**优先级**：P0｜**依赖**：A5。
- **实际核对**：[恒温器文档验收](../status/validation/thermostat-validation.md#thermostat-docs-20261102)：
  [系综标准](../standards/ensembles.md)、[兼容矩阵](../standards/compatibility-matrix.md)、
  README/索引/当前状态、MPI/thermo/测试合同与 [测试入口](../../tests/thermostats/README.md)
  已更新；单位、ramp 浮点顺序、3N、种子/cache、分段和非连续 restart 与代码一致。
- 实际命令在带日期记录中；复现示例明确标注。CLI/语法与本轮 Markdown 本地链接终检通过。
  原始非通过统计与脚本失败保留，未改 golden、容差或参考树。

---

### Step A7：`nvt_lan` / `nvt_bao`（P1，可选）

- **状态**：PROPOSED（可选）｜**优先级**：P1｜**依赖**：A5；维护者明确批准
- **目标**：在 v1.0 之外评估 Langevin 与 BAOAB；不纳入 G-A，不影响发布。若维护者不批准，保持
  parser unsupported，并保留本卡作为未来入口。
- **主责文件**：`src/ensemble_control.*`、`src/run_parser.cpp`、`src/runtime.cu`、
  `src/domain_runtime.cu`、测试与文档。**不得**把 BDP 的 RNG 语义直接套到逐原子 Langevin。
- **必读**：上游 `ensemble_lan.cu`/`ensemble_bao.cu` 与 `integrate.cu` 的 5-token 语法；调研报告
  的 Langevin/BAOAB 章节；Gate 0-D1。
- **实施要点**：先写设计说明，明确逐原子高斯数的所有权（root 生成 vs 每 rank 生成）、MPI 可复现性、
  BAOAB 的半步顺序与 NHC/BDP 的 hook 差异；再实现。默认不启用，不改变 v1.0 矩阵。
- **测试/验收**：同 A5 的矩阵子集；统计验收；不与 v1.0 发布门绑定。
- **证据/停止**：独立 status 文档；若随机数所有权无法在不增加每步 O(N) 广播的前提下证明一致，
  停止并保持 unsupported。

## 7. Phase B：控压器（B0–B4）

> 目标：先把“压力是什么、单位/符号/列映射是什么”做成可执行 oracle，再做盒变更合同，最后接入
> 各向同性 NPT。顺序不能倒：没有 B0 的 oracle，任何 barostat 的“看起来对”都不可信；没有 B1 的
> 缓存/所有权刷新，P>1 的变盒会静默算错。

| Step | 内容 | 优先级 | 依赖 | 关键产出 |
| --- | --- | --- | --- | --- |
| B0 | 压力 oracle、单位/符号/列映射 | P0 | A0 | 可复算的压力基准 |
| B1 | 盒变更合同与所有权刷新 | P0 | B0 | M1 安全变盒 |
| B2 | `npt_ber`（各向同性，M1） | P0 | G-A、B1 | 兼容 GPUMD 的 NPT |
| B3 | `npt_scr`（各向同性，M1） | P0 | B2、A4 | 以正确 NPT 采样为目标的采样器（须通过统计门） |
| B4 | NPT 验收、restart、文档 | P0 | B2、B3、A5 | G-B 证据与用户合同 |

---

### Step B0：压力 oracle、单位、符号与列映射

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：A0
- **目标**：在实现任何控压之前，建立一个可执行、可复算的压力基准，回答四个问题：
  内部应力如何归一化、输出如何换成 GPa、六个分量的列顺序、压缩/拉伸时符号是否正确。
- **主责文件**：测试与证据为主：`tests/thermo/`（新建压力 oracle/列映射测试）、
  `tests/CMakeLists.txt`、`docs/status/validation/pressure-oracle-<date>.md`。**不改运行时**；
  若发现现有实现有 bug，先记录并回到维护者，不在 B0 顺手改公式。
- **必读**：`src/runtime.cu` 的 `find_owned_thermo_sums`（基线约 412）、`compute_thermo`（约 484）、
  `write_thermo_row`（约 1086）；`src/runtime_internal.hpp` 的 `normalize_global_thermo`（约 546）；
  `src/gpumd_compat/common.cuh` 的 `K_B`/`PRESSURE_UNIT_CONVERSION`；上游 `src/integrate/ensemble.cu`
  的 `find_thermo`（434–673）；调研报告 §0.4；本计划 §2.2/§2.5。
- **实施要点**：
  1. 在 CPU 单元测试里固定一个合成 thermo 向量，验证内部顺序
     `[T, PE, sxx, syy, szz, sxy, sxz, syz]` 到输出列
     `T KE PE sxx syy szz syz sxz sxy` 的映射，以及 `×160.2177` 只在写文件时发生一次。
     用非对称的六个应力值（不要全相等）才能发现列交换。
  2. 做一个“理想气体”积分案例：NEP 模型、原子间距大于最大截断、给定 `vel`、`nve`、
     一个输出步；解析压力 `P = N k_B T / V`（自然单位），输出应为
     `P × PRESSURE_UNIT_CONVERSION` GPa。温度用 3N 定义；不要减质心。记录相对误差。
  3. 做一个“压缩/拉伸”符号案例：同一 NEP 构型，分别轻微缩小/放大盒子与位置（只做静态
     `nve` 一步，避免 thermostat），先选择并验证接近零压的稳定参考态，再检验压缩/拉伸的压力响应，不能对任意初态断言正负；与 GPUMD 的 NVE
     `thermo.out` 六列逐列比较。若无参考二进制，使用已提交的 GPUMD golden，不要用 DMG-MD
     自己的另一条路径当 oracle。
  4. 明确并记录：内部压力 = `(virial + kinetic)/V`，单位 eV/Å³；`thermo.out` 的应力列是
     GPa；`dump_xyz` 的 virial 是每原子 9 分量、布局 `xx yy zz xy xz yz yx zx zy`，不是 GPa。
  5. 用合成的九分量非对称 virial 测试缓冲布局；物理剪切 case 检验非零 off-diagonal，
     不要求其 `sxy != syx`（动力学应力可能对称）。二者分别验证布局与物理值。
- **测试/验收**：
  - 列映射单元测试通过；理想气体相对误差 <1e-10（CPU 合成）或记录浮点来源；
  - 压缩/拉伸符号与 GPUMD golden 一致；六列在 golden 容差内；
  - 文档中给出公式、单位、列名、布局和可复算命令。
- **证据**：`pressure-oracle-<date>.md`，含合成向量、解析值、六列对比、命令与环境。
- **停止/回滚**：参考 golden 缺失、符号无法判定、或发现现有输出列与标准不一致——停止并报告，
  不要在 B2 用“调参”掩盖。B0 无运行时回滚。

---

### Step B1：各向同性盒变更合同与所有权刷新

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：B0
- **目标**：实现一个可单独测试的“绕原点各向同性缩放”原语：同步更新 `Box`、位置、逆矩阵、
  float 副本与 M1 所有权缓存；明确 unwrapped/restart/输出语义；M2a 不走这条路径。
- **主责文件**：新增 `include/dmgmd/box_control.hpp`、`src/box_control.cpp`（或等价的最小
  host 原语）；`src/runtime.cu` 的 `RuntimeOwnership`（新增 `refresh_box`）与
  `src/runtime_internal.hpp`（如需要）；`tests/` 新增盒合同测试。**不修改
  `src/gpumd_compat/box.cu` 的数学**；只在调用侧使用 `get_inverse`/`set_is_orthogonal`。
- **必读**：`src/gpumd_compat/box.cuh` 的 `cpu_h[0..17]`/`float_h`/`thickness`；
  `src/gpumd_compat/box.cu` 的 `get_inverse`（104）、`get_num_bins`（143）、
  `set_is_orthogonal`（164）；`src/runtime.cu` 的 `RuntimeOwnership`（约 118）与
  `run_segment` 的 snapshot 顺序（约 646–780）；`docs/standards/cache-validity.md`；
  `docs/standards/mpi-runtime.md`。
- **实施要点**：
  1. 原语签名建议：`apply_isotropic_scale(Box&, DeviceAtoms&, RuntimeOwnership*, double factor)`；
     `factor`、候选盒矩阵/逆矩阵和体积必须有限且有效，在 mutation 前检查溢出/下溢。只允许正交盒；调用方必须已确认全周期，
     否则抛错，不“近似”三斜。
  2. 盒：对正交盒 `cpu_h[0] *= s; cpu_h[4] *= s; cpu_h[8] *= s;`，随后 `get_inverse()` 与
     `set_is_orthogonal()`（后者刷新 `float_h`）。`thickness_*` 由下一次 `get_num_bins(rc)` 刷新；
     任何绕过邻居构建的新力必须显式调用，不能读取旧厚度。
  3. 位置：M1 的 `atoms.position` 是复制的完整数组；所有 rank 对同一组全局槽位乘 `s`（以
     `counts.local_count()` 为 stride，按 `global_count` 遍历，padding 不参与）。这样保持了
     复制不变式，下一步 Allgather/force 看到一致坐标。不要只缩放 owned 后等下一次 Allgather，
     否则 barostat 与输出的时点会分裂。
  4. 速度不变；不引入质心平移/旋转；位置绕原点缩放，与上游一致。`unwrapped` 在 v1 **不缩放**
     （Gate 0-D6）：下一步的 unwrapped 增量只包含 VV 位移，不包含 barostat 的仿射缩放；
     这是上游行为，必须写测试和文档。若维护者不接受这个限制，B4 必须改为拒绝 NPT+unwrapped
     输出，不能默默输出错误坐标。
  5. `RuntimeOwnership`：新增 `refresh_box(const Box&)`，在任何下一次 `recompute_and_commit`
     之前把 `cpu_h[9..17]` 复制到 `inverse_box_`，并重新计算 `partition_axis_`（各向同性下理论上
     不变，但仍刷新，避免未来非各向同性扩展踩坑）。`host_position_` 由下一次 recompute 的
     `copy_to_host` 更新，不要在这里另建一份权威坐标。
  6. 时点：barostat 必须在 VV2 + thermo + thermostat **之后**、snapshot/`dump_xyz`/
     `dump_restart` **之前**执行。否则 restart 会写出“新盒子 + 旧位置”，这是静默数据损坏。
     `write_thermo_row` 的应力值仍是缩放前的 thermo，盒列是缩放后的 box；这个时点必须写进文档。
  7. B1 记录盒变化事件；待 C0/C2/C3 接入时，盒变化必须通知新势邻居缓存；已有 force/virial 的采样时点须记录。
     正常单段兼容步序不擅自增加重算；若为连续 restart 改步序，须另作兼容决策。
  8. M2a：`run_local_domain` 不提供 `RuntimeOwnership`；B1 的原语在 M2a 上必须不可达，并在
     B2 的路由层提前把 NPT 引到 M1。不要在 M2a 里复制一份盒缩放。
- **测试/验收**：
  - 纯几何：随机正交盒 + 位置，缩放 `s` 后体积乘 `s^3`；分数坐标 `r_i/L` 在容差内不变；
    再乘 `1/s` 回到原坐标/盒子（浮点容差）；速度逐位不变。
  - 所有权：P=1/P=2，缩放前后对同一组 wrapped 位置调用 owner map，hash 一致；缩放后
    `refresh_box` + 下一 step 的 recompute 不抛 ownership 错误；无原子丢失/重复。
  - 输出：缩放后 `dump_restart` 的 Lattice/pos 与 `dump_xyz` 的 wrapped 位置一致；盒列与
    `box.cpu_h` 一致；unwrapped 明确记录不缩放。
  - 负例：三斜/非周期/`factor<=0`/非有限 factor 必须报错；M2a 路径不可调用。
- **证据**：`box-contract-<date>.md`，含几何、所有权、restart 和负例结果。
- **停止/回滚**：需要改 `box.cu` 数学才能实现；或缩放后所有权/位置出现漂移；或为了“让 NPT
  先跑起来”绕过 snapshot 时点。回滚到 B0，不保留半成品变盒。

---

### Step B2：`npt_ber`（各向同性，M1）

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：G-A、B1
- **目标**：实现与 GPUMD 兼容的各向同性 Berendsen NPT：8-token 语法、GPa/自然单位换算、
  温度缩放后盒缩放、M1（含 P>1 fallback）路由、restart 与输出时点；M2a 明确不可达。
- **主责文件**：`include/dmgmd/run_ir.hpp`、`src/run_parser.cpp`、`src/runtime.cu`
  （dispatcher 与 `run_segment`）、`src/box_control.*`、`src/ensemble_control.*`、
  `tests/unit/run_parser_tests.cpp`、`tests/` 新 NPT 集成 case、`tests/CMakeLists.txt`。
- **必读**：上游 `src/integrate/integrate.cu` 的 8-token 解析（455–463、620–712）与单位换算
  （1152–1153、1190）；`src/integrate/ensemble_ber.cu` 的 `cpu_pressure_isotropic`（138–148）与
  `compute2`（195–280）；`src/gpumd_compat/common.cuh` 的常数；本计划 §2.5、§13；B0/B1 的结论。
- **实施要点**：
  1. 语法严格为 8 token：`ensemble npt_ber T1 T2 Tdamp P B tau_p`。`T1/T2>0`、`Tdamp>=1`；
     `P` 是有限的目标压力（可为负，表示拉伸）；`B` 是**体积模量**，必须有限且 >0；
     `tau_p>=1`（步数）。12/18 token（3/6 分量）在本 Step 明确报“v1 仅支持各向同性”，
     不要解析后忽略多出的分量。
  2. 单位换算只在边界做一次：
     `target_p_internal = P_GPa / PRESSURE_UNIT_CONVERSION`；
     `pc = 1/(3*tau_p*B_GPa); pc *= PRESSURE_UNIT_CONVERSION`。
     随后所有比较用自然单位（eV/Å³）。`B>2000` 时上游把 `pc=0`（固定盒子）；为兼容也实现
     该分支，并在文档说明它等价于“只控温、盒子不动”，不要把它当成错误。
  3. 每步顺序：VV1 → gather/force/ownership → VV2 → thermo → Berendsen 温度缩放（若
     `1/Tdamp>1e-5`）→ 用缩放前 thermo 的 `(sxx+syy+szz)/3` 计算
     `scale = 1 - pc*(P_target - P_inst)` → 广播 scale → B1 盒/位置缩放 → `refresh_box` →
     snapshot/输出。位置绕原点；速度不缩放；不要先缩放位置再算压力。
  4. 安全 guard：`scale` 必须有限且 >0，缩放后体积必须有限且 >0；否则在任何 mutation 前
     报错（带 step、P、P_inst、scale）。这是对上游静默产生负体积的**有意加固**，写入文档。
  5. 路由：在 `run_replicated` 计算 M2a eligibility **之前**扫描 program 的 ensemble；若为
     `npt_ber`，设置 `force_m1`（含 P>1 fallback），打印
     `DMGMD_DOMAIN mode=m1 reason=npt-m1-only`，不得进入 M2a。M1 仍需正交、全周期；
     P>1 若盒不满足则按现有规则报错。`run_local_domain` 加防御性 `if npt -> throw`。
  6. 目标温度 ramp 仍按段内 `step/steps`；NPT 的 `T1/T2` 与 `Tdamp` 语义与 `nvt_ber` 一致。
     连续多个 `run` 段重新初始化控制器；restart 是新作业，ramp 重置，必须文档化。
  7. restart：`npt_ber` 无链/RNG，但步末缩放后保存的坐标/盒与缓存力可能属于不同状态。
     新 `run`/restart 会重算力，不能直接承诺与单段长轨迹一致。用恒温 case 分别测试
     单长段、同进程多段、文件重启，记录首次分歧及截断精度；v1 默认承诺可重新平衡续算。
  8. 输出：`thermo.out` 的应力是缩放前 thermo，盒列是缩放后 box；`restart.xyz` 的 Lattice/
     pos 是缩放后状态；`dump_xyz` 的 wrapped 位置缩放、速度不变、unwrapped 不缩放（限制）。
  9. 禁止与未验证组合一起“尽力而为”：NPT + `correct_velocity`、NPT + 自适应步长、
     NPT + 实验性 `DMGMD_DOMAIN_*`/overlap 开关，要么有专项测试，要么在动力学前拒绝。
- **测试/验收**：
  - parser：合法 8-token；12/18 token 的明确拒绝；T/B/tau_p 负例；P 负值接受；`B>2000`
    接受且 `pc=0`；重复 ensemble 的既有行为回归。
  - P=1 M1 对 GPUMD `npt_ber` 的 100–500 步短轨迹：`thermo.out` 的温度/六应力/盒列与
    `dump_xyz` 在 golden 容差内；记录首个超差步/列。GPUMD 输入必须有显式 `vel`，避免随机初速度。
  - 压力跟踪：从压缩/拉伸初态出发，检查确定压力差下的单步响应方向；平均松弛单独检验，不要求瞬时压力单调。
    `P_inst > P_target` 时扩张，反之收缩；不能仅由目标压力为负推出扩张。
  - P=2/4 的 M1 fallback：stdout 必须显示 `mode=m1`；短轨迹/统计与 P=1 在容差内；缩放若干步后
    无所有权错误；restart 可启动且继续缩放。
  - restart：`T1==T2` 的单段/多段/重启对照必须实际执行并分类记录差异，不预设逐步连续；记录
    `restart.xyz` 与 `thermo.out` 的盒列一致性。
  - M2a：构造本来 eligible 的 case，NPT 仍走 M1；若直接调用 domain 路径必须报错。
- **证据**：`docs/status/validation/npt-ber-<date>.md`，含 GPUMD 对比、压力跟踪、P>1/restart、
  失败组合与输出时点。
- **停止/回滚**：P1 golden 超差；盒/位置/restart 不一致；P>1 所有权错误；或为了通过测试改变
  NEP/thermo 定义。回滚 B2 的 parser/runtime 路由，保留 B0/B1。

---

### Step B3：`npt_scr`（各向同性，M1）

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：B2、A4
- **目标**：实现随机细胞重标定（SCR）的严格 NPT：BDP 速度重标定 + 随机盒缩放；root 单一 RNG
  流、固定种子/环境覆盖；统计验收，不承诺轨迹 byte-exact。
- **主责文件**：`src/run_parser.cpp`、`include/dmgmd/run_ir.hpp`、`src/runtime.cu`、
  `src/box_control.*`、`src/ensemble_control.*`、`tests/` 新 SCR case、`tests/CMakeLists.txt`。
- **必读**：上游 `src/integrate/ensemble_npt_scr.cu` 的 `cpu_pressure_isotropic`（137–160）与
  `compute2`（240–280）、RNG 初始化（34–36）；`src/integrate/svr_utilities.cuh`；调研报告 §6.6；
  Gate 0-D5；B2 的盒/路由/时点。
- **实施要点**：
  1. 语法同样 8 token：`ensemble npt_scr T1 T2 Tdamp P B tau_p`；校验同 B2。12/18 token 拒绝。
  2. 每步顺序：VV1 → gather/force/ownership → VV2 → thermo → **BDP 速度重标定**（用当前 T 与
     全局 KE）→ 广播温度 factor → 缩放 owned 速度 → root 计算盒因子 →
     `scale = 1 - pc*(P_target - P_inst) + sqrt((2/3)*pc*k_B*T/V) * gaussian` → 广播 →
     B1 盒/位置缩放 → `refresh_box` → snapshot/输出。注意：随机项用的是**缩放前**体积与当前
     目标温度；BDP 先于 barostat；顺序不能交换。
  3. RNG：推荐与 BDP 共用持续推进的 root `std::mt19937` 流；`gasdev` 的 `iset/gset` 是 controller 成员。
     但锁定上游 `cpu_pressure_isotropic(std::mt19937 rng, ...)` 按值传参，且 `gasdev` 用静态缓存，
     因此本方案不等同于上游随机流。G0-D10 必须明确这项偏离；先温度后压力，建立独立单步
     噪声输入 fixture 与统计 oracle，不能用同 seed GPUMD 轨迹作为正确性证明。默认种子/`DMGMD_ENSEMBLE_SEED` 同 A4；种子/cache 的段边界按 G0-D9 初始化。
  4. 高斯分布：复刻上游 `gasdev` 的 Marsaglia polar 算法与 `std::uniform_real_distribution<double>`
     用法；不要换成 `std::normal_distribution`（实现可能随标准库变化），也不要用各 rank 独立
     RNG。同二进制/同标准库/同 rank 的重复运行可 byte-identical；跨编译器只承诺统计。
  5. 安全 guard：`scale` 有限且 >0、体积有限且 >0；否则报错。SCR 的随机项可能偶发大因子，
     但产品不能在负体积上继续跑。记录 P、P_inst、T、V、gaussian、scale。
  6. 路由/盒/输出/restart 时点全部复用 B2；NPT 强制 M1，M2a 防御性拒绝。`npt_scr` 的 restart
     不连续（RNG + BDP 状态不保存），只验证能启动，不做轨迹连续性承诺。
  7. 统计自由度 3N；NPT 的温度控制用 BDP，不用 NHC；不要混用 `npt_ber` 的 NHC 假设。
- **测试/验收**：
  - parser 与失败关闭同 B2；`B>2000` 时 `pc=0`，随机项也为 0（盒子不动），验证实现。
  - 同 seed、同二进制、同 rank 的重复运行：`thermo.out` byte-identical（至少短轨迹）；
    不同 seed 结果不同；默认种子与 env 覆盖均可复现。
  - 统计：至少 8 个 seed、≥20k 步（前 10% 丢弃）的 NPT；`<P_inst>` 接近 `P_target`，
    `<T>` 接近目标，体积均值/方差须通过 §0.5 定量统计门，不能只检查有限或不塌缩；给出 95% CI。与 GPUMD
    `npt_scr` 比均值/涨落（多次独立 wall-clock seed），不比单条轨迹。
  - P=2/4 M1 fallback：`mode=m1`、无所有权错误、统计合理；restart 可启动。
  - 负例：非正交/非周期/12/18 token/非有限 P/B/M2a 路径都必须报错。
- **证据**：`docs/status/validation/npt-scr-<date>.md`，含种子矩阵、均值/涨落/CI、GPUMD 统计对比、
  安全 guard 触发记录（若有）。
- **停止/回滚**：同 seed 不可复现；BDP 与 barostat 的 RNG 顺序被改变；体积出现负值/塌缩；
  或统计验收只靠单 seed。回滚 B3，保留 B2。

---

### Step B4：NPT 验收、restart 与文档（G-B）

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：B2、B3、A5
- **目标**：把 B2/B3 变成可发布合同：完整矩阵、restart/输出/限制、兼容矩阵、用户文档与证据；
  G-B 通过后才允许进入 v1.0 发布收尾。
- **主责文件**：`docs/standards/barostat.md`（新建）、`docs/standards/compatibility-matrix.md`、
  `docs/README.md`、`docs/status/current.md`、`README.md`、`docs/status/validation/npt-matrix-<date>.md`；
  测试 case 归入 `tests/`。本 Step 以文档/矩阵为主，发现缺陷回到 B2/B3。
- **必读**：B0–B3 证据；Gate 0-D2/D4/D5/D6；调研报告 §6.2、§6.6、§9；`docs/README.md` 的治理规则；
  `docs/standards/golden-test-standard.md`。
- **实施要点**：
  1. 文档写清语法、单位、符号、参数范围：P/B 为 GPa；B 是体积模量；P 可为负；tau_p/Tdamp 是
     步数；`B>2000` 的固定盒子分支；12/18 token 不支持。不要用“compressibility”混淆 B。
  2. 写清两种 NPT 的算法差异与适用性：`npt_ber` 兼容 GPUMD、非严格采样；`npt_scr` 是随机
     细胞重标定，需统计验收；两者都不是 MTTK/PR。不要宣称“任意系综”。
  3. 写清 M1-only 路由：P=1/P>1 M1 支持；M2a 不提供 NPT；dispatcher 的 `mode=m1 reason=...`
     日志；未来若要 M2a NPT，需另立盒/邻居/halo 计划。
  4. 写清时点：thermo 在 thermostat/barostat 前计算，盒列在缩放后；restart 在缩放后；
     unwrapped 不缩放（或按维护者决定拒绝 NPT+unwrapped）。给出用户可检查的列/文件对照。
  5. 写清 restart：`npt_ber` 恒温重启也不预先承诺逐步连续（见 §0.5）；`npt_scr` 不连续；
     ramp 的 split-run 与连续 run 不等价；不要把不连续写成“已知小问题”。
  6. 矩阵必须覆盖：P=1/P=2/P=4（M1，含 fallback）、M2a 路由拒绝/绕过、默认/CUDA-aware
     （可用时）、`npt_ber`/`npt_scr`、restart、负 P、B>2000、非正交/非周期错误、12/18 token 错误。
  7. 更新兼容矩阵的 supported/unsupported 列表、restart 表、thermo 列/盒列语义；更新
     `docs/status/current.md`；`docs/README.md` 加 `barostat.md` 链接；检查链接。
- **测试/验收**：发布支持范围内的 G-B 矩阵全 PASS；范围外 SKIP 明示且不计通过；文档命令可执行；兼容矩阵与实际 parser/runtime
  一致；所有限制有证据或明确标为未验证。
- **证据**：`npt-matrix-<date>.md` + 原始日志 + 文档链接检查输出。
- **停止/回滚**：矩阵缺项却声称 G-B；文档把 M2a/NPT 写成支持；restart 语义与实测不符；
  或 unwrapped 输出会误导用户却未警告。回滚文档到 B3 的已知事实，不发布。

## 8. Phase C：势函数抽象与 LJ/EAM/Tersoff（C0–C5）

> 目标：把“NEP 专用”改成“可插拔的力场”，但**不改变 NEP 的任何数值**；非 NEP 势 v1 只走 M1，
> 不做 M2a 的 halo/邻居分块。C0 只做抽象，C1 只做格式分发，C2/C3 才引入新物理。

| Step | 内容 | 优先级 | 依赖 | 关键产出 |
| --- | --- | --- | --- | --- |
| C0 | `ForceField` 抽象与 NEP adapter | P0 | G-A | 不改 NEP 的可插拔接口 |
| C1 | 势格式分发、metadata、`potential` 第三 token | P0 | C0 | LJ/EAM 可进入构建 |
| C2 | LJ | P0 | C1 | 解析/力/virial/GPUMD golden |
| C3 | EAM/alloy | P0 | C1、C2 的 M1 适配经验 | setfl + spline + golden |
| C4 | Tersoff 1989 | P1 | C3；G0-D3 批准 | 可选，不阻塞 v1.0 |
| C5 | 势能力矩阵、示例与文档 | P0 | C2、C3（C4 若启用） | G-C 证据 |

---

### Step C0：`ForceField` 抽象与 NEP adapter

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：G-A
- **目标**：定义 M1 全系统力计算的稳定接口，让 NEP 作为第一个实现；M2a 仍只接受 NEP，不因抽象
  改变 halo/所有权/数值。C0 结束时，所有既有 golden 必须一条不变。
- **主责文件**：新增 `include/dmgmd/force_field.hpp`、`src/force_field.cpp`（工厂/adapter）；
  `src/runtime.cu` 的 `NepForce`/`run_m1_replicated`/`run_replicated`；`src/domain_runtime.cu`
  只加编译期/运行期 guard；`CMakeLists.txt`、`tests/CMakeLists.txt`。
- **必读**：`src/runtime.cu` 的 `NepForce`（约 507）、`run_segment` 的 `force.compute(box, atoms)`
  （约 687/712）、`run_replicated` 的 dispatcher（约 1250–1420）；`src/gpumd_compat/potential.cuh`
  与 `NEP` 的 `compute` 签名；`docs/standards/architecture-decisions.md`；`AGENTS.md` 的
  “不改变 NEP 数学”约束。
- **实施要点**：
  1. 接口只暴露 M1 需要的操作：
     - `compute(Box&, DeviceAtoms&)`：读取 type/position，写 potential/force/virial；
     - `cutoff()`/`num_types()`/`requires_full_system()`（或等价能力查询）；
     - 析构与移动语义。
     如果 `DeviceAtoms` 目前是 `runtime.cu` 内部类型，不要为了“公共头干净”把它整体搬到公共 API；
     可以在 `runtime.cu` 内部定义 adapter，公共头只暴露不依赖该类型的工厂/描述。
  2. `NepForce` 成为第一个实现（或由 adapter 包装），保留它的 wrap + clear + `NEP::compute`
     顺序、workspace 大小、launch 参数与累加顺序。**不要**把 NEP 的 `compute` 重写成新的通用
     kernel；不要改 `src/gpumd_compat/`。
  3. M2a 仍然持有 `NEP`/`NepForce`，不通过新接口；`run_local_domain` 的签名可以不变。若为了
     编译必须改，只做类型擦除，不改变调用序、halo 或 thermo 需求。
  4. 工厂只识别“已实现格式”；C0 阶段仍只有 NEP。未知/未实现格式在构造前报错，不能返回空指针
     或静默退化为 NEP。
  5. 运行时分派保持：P=1 走 M1；P>1 若 M2a eligible 且格式为 NEP 走 M2a；否则 M1 fallback。
     C0 不改变这个决策，只把“M1 用哪个力”抽象出来。
  6. ForceField 必须预留盒 epoch/`on_box_changed`（或等价失效接口）；C2/C3 的邻居缓存不能只看原子位移。
     盒缩放后强制安全重建或证明复用，覆盖盒收缩、跨 cutoff 新邻居、近原点原子。
     不提前写 LJ/EAM 的 parser 或 compat 文件；C0 的测试应能在
     没有新势的情况下全绿。
- **测试/验收**：
  - 完整 CTest、baseline、long_nve smoke 与 A5 的 NHC/BDP 关键 case 全部通过；
  - NEP `thermo.out`/`dump_xyz`/`restart.xyz` 与 A5 基线在 golden 容差内（同构建配置优先哈希）；
  - M2a P=2/4 的 NEP case 与 C0 前一致；无新增 collective/内存峰值异常；
  - 代码审查确认 `src/gpumd_compat/` 无 diff、NEP kernel 无 diff。
- **证据**：`docs/status/validation/forcefield-c0-<date>.md`，含变更文件、NEP 对比、M2a 回归。
- **停止/回滚**：任何 NEP 数值变化；M2a 行为变化；为了抽象复制了 NEP kernel；或把 M2a 强行
  接入通用接口。回滚 C0，保留 A2。

---

### Step C1：势格式分发、metadata 与 `potential` 可选参数

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：C0
- **目标**：让 `potential` 能按文件头识别 NEP/LJ/EAM（Tersoff P1），把类型符号/metadata 传给
  model 解析与力工厂；支持 `eam/alloy` 的 `max_neighbor` 第三 token；非 NEP 强制 M1 并记录原因。
- **主责文件**：`include/dmgmd/model.hpp`（`PotentialMetadata`）、`src/model_parser.cpp`
  （`load_potential_metadata`）、`include/dmgmd/run_ir.hpp`（`PotentialCommand`）、
  `src/run_parser.cpp`、`src/force_field.cpp`、`src/runtime.cu`（dispatcher）、
  `src/dmgmd_main.cpp`（传参）、`tests/unit/model_parser_tests.cpp`、`tests/unit/run_parser_tests.cpp`。
- **必读**：`src/model_parser.cpp:257` 的当前白名单；`src/run_parser.cpp` 的 `potential`
  分支（约 163）；`src/dmgmd_main.cpp` 的 potential 提取；上游 `src/force/force.cu:75-190`
  （格式分发、`max_neighbor` 1..1024、默认 400）；上游 `lj.cu`/`eam_alloy.cu` 的文件头；
  本计划 §2.5/§13。
- **实施要点**：
  1. `PotentialMetadata` 增加格式枚举/字符串（NEP4/5/ZBL、LJ、EAM/alloy、Tersoff 1989）与
     symbols；保留现有 NEP 字段以减小改动。`load_potential_metadata` 只读第一行/必要头部，
     不要为取 symbols 把 GB 级 EAM 文件全部载入。
  2. 第一行规则：
     - NEP：现有 `nep4|nep5|nep4_zbl|nep5_zbl T symbols...`，token 数严格 `2+T`；
     - LJ：`lj T symbols...`，同样 `2+T`；
     - EAM/alloy：`eam/alloy T symbols...`；文件随后有**两行注释**，正文从第 4 行开始
       （`T` + symbols 再次出现，然后 `nrho drho nr dr rc`）。这是 GPUMD 包装格式，不是
       纯 LAMMPS setfl；不要按“标准 setfl 第 2 行就是元素”去读；
     - Tersoff 1989（P1）：`tersoff_1989 T symbols...`。
     未知第一 token、token 数不符、T<=0 都报带文件/行的错误。
  3. `potential <file> [max_neighbor]`：parser 允许 2 或 3 token；`max_neighbor` 只对
     `eam/alloy` 有效，默认 400，范围 `[1,1024]`；对 NEP/LJ/Tersoff 出现第三 token 必须报错，
     不能忽略。错误来源要指向 `run.in` 的行。
  4. model 解析继续用 metadata.symbols 建立 species→type；EAM 的 body 元素顺序必须与第一行
     symbols 一致，力工厂在构造时再校验 body；不要只信第一行。
  5. 路由：格式为 NEP 时保持现有 M2a eligibility；非 NEP 时在 eligibility **之前**设置
     `force_m1`，打印 `DMGMD_DOMAIN mode=m1 reason=non-nep-m1-only`（NPT 时再加
     `npt-m1-only`）。M1 在 P>1 仍要求正交、全周期；否则在动力学前报错，不得进入 M2a 或
     “自动降级”为非 NEP 的 NVE。
  6. 未知/未实现格式（如 `tersoff_1988`、`adp`、`eam_zhou_2004`）明确 unsupported；不要
     因为文件头像 setfl 就误判为 `eam/alloy`。C1 仅识别元数据并以未实现 guard 拒绝执行；C2/C3 分别开放 LJ/EAM，Tersoff 到 C4。
  7. 不在 C1 改 NEP 的 `NEP::NEP` 数学/参数解析；NEP 的 metadata 行为与现有错误文字保持。
- **测试/验收**：
  - parser：`potential file`、`potential file 400`（EAM）、`potential file 0/1025/abc`（错误）、
    非 EAM 带第三 token（错误）、缺文件/空文件；
  - metadata：NEP 合法/非法头、LJ 头、EAM 三行头、Tersoff 头；model species 与 symbols 匹配；
  - 路由：NEP P=1/P>1 与基线一致；C1 的 LJ/EAM 在 P>1 预检时明确报未实现；到 C2/C3 后验证 `mode=m1 reason=non-nep-m1-only`；
    三斜/非周期 P>1 报错；M2a 不被调用；
  - 完整 CTest + NEP baseline 不变。
- **证据**：`forcefield-c1-<date>.md`，含格式表、parser 用例、路由日志、NEP 回归。
- **停止/回滚**：EAM 文件头假设未用 fixture 验证；NEP metadata 行为改变；或非 NEP 静默进入
  M2a。回滚 C1 的 parser/工厂，保留 C0。

---

### Step C2：LJ 势（M1）

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：C1
- **目标**：按锁定上游实现 LJ：`lj T symbols` + T×T `epsilon sigma cutoff`，无 shift，float 参数
  与邻居容量 700；M1 全系统力/能量/virial；M2a 明确不支持。
- **主责文件**：`src/gpumd_compat/lj.cu`、`src/gpumd_compat/lj.cuh`（带 Origin 头，最小复制）、
  `src/force_field.cpp`（LJ adapter/工厂）、`CMakeLists.txt`、`tests/` 新增 LJ fixtures 与
  `tests/CMakeLists.txt`。**不改 NEP 数学**；新增 LJ/EAM 专用安全邻居适配可独立文件实现，
  若共用 compat 邻居设施必须改动，先给出最小安全方案与 NEP golden 设计，不能机械复制未检查写入。
- **必读**：上游 `src/force/lj.cu`（构造 30–70、`find_p2_and_f2`、force kernel、virial 累加）、
  `src/force/force.cu:180-182`；`src/gpumd_compat/neighbor` 的接口；`docs/standards/data-layout.md`；
  B0 的压力/列结论。
- **实施要点**：
  1. 文件格式严格：第一行 `lj T symbols...`；随后 T×T 行，每行 `epsilon sigma cutoff`（eV、Å、Å），
     第 i 行第 j 列对应 type i-j；`rc=max(cutoff)`。**不做能量 shift**（上游没有），截断处能量
     不连续是参考行为，文档要写清，不能“顺手加 shift”导致 golden 变化。
  2. 构造端按上游用 double 输入计算 `pow` 等表达式后存入 float 参数；不能全改成 float 运算。逐字复刻 `find_p2_and_f2` 的
     乘除顺序与 float 精度；virial 按上游 9 分量布局累加，再交给现有 thermo 归一化。
  3. 邻居：沿用上游 `neighbor.initialize(rc, N, 700)` 的容量及实际邻居构建分支；不要擅自
     提高容量或改成新的 cell list。容量必须在写邻居表前检查；不得沿用越界写/静默截断。新增安全适配要留 Origin，
     保持容量内的数学/排序不变，并跑 NEP 回归。
  4. 力计算对 M1 的完整复制位置/type 操作；不接 M2a、不做 ghost/halo。P>1 时仍由 M1 slab
     所有权决定积分/thermo，力本身是全系统计算。
  5. 工厂只接受 LJ 元数据；`max_neighbor` 第三 token 对 LJ 报错；未知类型/负 epsilon/非正
     sigma/cutoff 的处理按上游或明确失败关闭，不能产生 NaN。
- **测试/验收**：
  - 解析：合法单/双 type；T×T 行数不足/多；参数非有限；header 错；第三 token 错。
  - 静态二聚体：`U=4ε[(σ/r)^12-(σ/r)^6]`，力与有限差分/GPUMD 一致；virial 与 `r⊗f` 的
    9 分量布局一致；cutoff 内外行为；两种 type 的 epsilon/sigma 矩阵。
  - P=1 GPUMD LJ NVE 100–500 步短轨迹（thermo/dump）在 golden 容差内；P=2 M1 fallback
    不崩溃、无所有权错误；M2a 输入明确拒绝/不进入。
  - 与 B2/B3 组合为 P0：LJ + `npt_ber`/`npt_scr` 的短轨迹/统计及盒缓存失效测试；
    参考输入须匹配；缺 oracle 标阻塞，不能据此声明支持。C5 汇总全部系综 × 势组合。
  - 性能/内存记录：邻居容量 700 的实际最大邻居数；若溢出，记录失败行为。
- **证据**：`lj-<date>.md`，含解析、解析力、GPUMD 对比、P=1/P=2、M2a 拒绝与限制。
- **停止/回滚**：LJ 与参考不一致且无法归因；NEP golden 受影响；为了“更物理”加 shift 或改
  float 精度；M2a 被误接入。回滚 C2 的 compat 文件与工厂分支。

---

### Step C3：EAM/alloy（M1）

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：C1、C2 的 M1 适配经验
- **目标**：实现 GPUMD 包装的 EAM/alloy：三行头 + setfl 正文、Hermite 样条、嵌入能/电子密度/
  对势、`max_neighbor` 可选；M1 全系统；与 GPUMD 静态/短轨迹 golden 对齐。
- **主责文件**：`src/gpumd_compat/eam_alloy.cu`、`eam_alloy.cuh`（带 Origin 头）、
  `src/force_field.cpp`、`CMakeLists.txt`、`tests/` 的 EAM fixtures 与 `tests/CMakeLists.txt`。
- **必读**：上游 `src/force/eam_alloy.cu`（构造函数 116–165、`initialize_eamalloy` 165–260、
  spline/force kernel）、`src/force/force.cu:103-111`；`potentials/eam/CuNi.eam.alloy` 的头部；
  `docs/standards/data-layout.md`；C2 的邻居/浮点约定。
- **实施要点**：
  1. 文件格式必须按 GPUMD 包装：第 1 行 `eam/alloy T symbols...`；第 2–3 行注释；第 4 行
     再次 `T symbols...`；随后 `nrho drho nr dr rc`、晶格行、每个元素的 `F(rho)`/`rho(r)`、
     下三角对势块。不要把它当纯 LAMMPS setfl 从第 2 行读。
  2. 解析/样条逐字复刻：`compute_lammps_spline` 的中心差分与 Hermite 系数、float 存储、
     `read_section` 的行读取/丢弃规则、下三角镜像；任何“更稳”的样条都会改变 golden。
  3. 力：电子密度、嵌入能导数、对势导数、virial 累加顺序与上游一致；使用 float 参数/计算，
     除非上游明确 double。`max_neighbor` 默认 400、范围 1..1024；邻居容量按该值分配。
  4. 元数据/类型：第一行 symbols 必须与正文第 4 行一致、与 model species 映射一致；不一致
     在构造前报错。`rc` 用于邻居；不要用元素行里的晶格常数替代。
  5. M1 only；M2a 明确不可达；P>1 的 M1 fallback 仍走全系统力；NPT 组合遵循 B2 的 M1 路由。
  6. 失败关闭：文件截断、非有限值、非法 `nrho/nr`/网格间距、元素数不符、`max_neighbor` 越界
     都要报带文件/行的错误；不要用默认 0 填充后继续。
- **测试/验收**：
  - 纯 Cu fcc 与 CuNi 合金：静态能量/力/virial 与 GPUMD 一致；晶格常数/ cohesive energy
    在 golden 容差内；`max_neighbor` 400 与较小值的行为；截断/样条边界。
  - P=1 GPUMD EAM NVE 100–500 步短轨迹在 golden 容差内；P=2/4 M1 fallback 无所有权错误；
    M2a 不进入。
  - 解析错误矩阵：三行头缺失、元素数不符、nrho/nr 非法、截断、NaN、max_neighbor 0/1025。
  - 与全部 P0 系综联合验证；NPT 收缩必须让邻居缓存失效。表格边界/密度超出表域的处理按参考复核，
    不凭一般物理直觉拒绝所有负表项。
  - 邻居数恰好等于容量与超容量负例必须在任何越界写前处理；分配大小检查乘法溢出/OOM。
  - 内存/性能：按 `N*max_neighbor` 记录峰值；不因 golden 通过而忽略 OOM 风险。
- **证据**：`eam-<date>.md`，含格式说明、静态/轨迹对比、错误矩阵、P=1/P>1、内存。
- **停止/回滚**：EAM 与 GPUMD 不一致且无法归因；样条/浮点被“优化”；M2a 被误接入；
  或文件格式假设与 fixture 不符。回滚 C3，保留 C2。

---

### Step C4：Tersoff 1989（P1，可选）

- **状态**：PROPOSED（可选）｜**优先级**：P1｜**依赖**：C3；G0-D3 明确批准
- **目标**：在 v1.0 之外增加 `tersoff_1989`；不阻塞 G-C/发布。若未批准，保持 unsupported，
  不写半成品 parser。
- **主责文件**：`src/gpumd_compat/tersoff1989.cu/.cuh`、`src/force_field.cpp`、parser/metadata、
  CMake、tests、文档。**不包含** tersoff_1988/mini、ILP、SW。
- **必读**：上游 `src/force/tersoff1989.cu`、`force.cu:93-95`；Tersoff 1989 参数文件格式；
  `docs/standards/golden-test-standard.md`。
- **实施要点**：按上游逐字实现三体角度/截断/参数表与 virial；M1 only；文件头
  `tersoff_1989 T symbols...`；与 `potential` 第三 token 规则一致（不支持）。测试 Si/diamond
  静态与短 NVE golden；M2a 拒绝；文档标注 P1。
- **证据/停止**：独立 status 文档；若三体邻居/角度顺序无法与参考对齐，停止并保持 unsupported，
  不要把 Tersoff 放进 v1.0 矩阵。

---

### Step C5：势能力矩阵、示例与文档（G-C）

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：C2、C3（C4 若启用）
- **目标**：把“支持哪些势、文件格式、并行范围、限制、如何验证”写成发布合同；更新兼容矩阵、
  用户文档、示例与性能记录；G-C 通过后 v1.0 才可声明 NEP+LJ/EAM。
- **主责文件**：`docs/standards/potentials.md`（新建）、`docs/standards/compatibility-matrix.md`、
  `docs/README.md`、`README.md`、`docs/status/current.md`、`docs/status/validation/potential-matrix-<date>.md`、
  `docs/status/performance/`；示例输入放 `tests/` 或 `examples/`（小文件）。
- **必读**：C0–C4 证据；上游 force 分发表；B0 的压力/列结论；`docs/standards/golden-test-standard.md`；
  `docs/README.md` 的索引规则。
- **实施要点**：
  1. 能力矩阵按“格式 × 数据面 × 系综”写：NEP 在 M1 支持全部 v1 系综，在 M2a 仅支持 NVE、nvt_ber、NHC、BDP；LJ/EAM 支持 M1
     （含 P>1 fallback），不支持 M2a；Tersoff 标 P1。明确 NPT+non-NEP 也走 M1。
  2. 文件格式逐项写：NEP 头/ZBL；LJ 无 shift、T×T 顺序/单位；EAM 三行头 + setfl 正文 +
     `max_neighbor`；Tersoff（若启用）。给出最小示例与常见错误。
  3. 示例：NEP NVE、LJ NVE、EAM NVE 各一个可运行小 case；不要提交大轨迹/大势文件；
     说明从哪里获取测试势（不把参考仓库链接进构建）。
  4. 限制：LJ 截断不连续、EAM 邻居上限/内存、非 NEP 无 M2a、Tersoff 为 P1、NPT 的 M1 限制；
     不要用“支持 LJ/EAM”暗示支持所有 LAMMPS 势。
  5. 更新兼容矩阵的 supported/unsupported、`potential` 第三 token、restart/输出列不变；
     更新 `docs/status/current.md`；链接检查。
- **测试/验收**：发布支持范围内的 G-C 矩阵全 PASS；范围外 SKIP 明示且不计通过；示例命令实际执行；NEP baseline 不变；
  LJ/EAM golden 在库；文档与实际 parser/工厂一致。
- **证据**：`potential-matrix-<date>.md` + 示例日志 + 链接检查。
- **停止/回滚**：文档把 M2a/未实现势写成支持；示例不可运行；或为了“矩阵完整”把 Tersoff
  临时提升为 P0。回滚文档，不改代码。

---

## 9. Phase D：产品化收尾（D1–D5）

> 目标：把“能算”变成“能交付”：版本/CLI、许可证、用户文档、性能门、打包与发布清单。
> D2 可以最早启动（GPL 义务影响分发），但 D1–D5 全部完成才算 v1.0。

| Step | 内容 | 优先级 | 依赖 | 关键产出 |
| --- | --- | --- | --- | --- |
| D1 | CLI、版本、错误契约 | P0 | A2、B1、C0 | 可诊断的正式入口 |
| D2 | 许可证与第三方合规 | P0 | G0-D7 | LICENSE/NOTICE/审计 |
| D3 | 用户文档与快速开始 | P0 | A5、B4、C5 | 可照做的用户路径 |
| D4 | 性能门与打包 | P0 | A0、D1、D3 | 可回归的性能与发布包 |
| D5 | v1.0 发布清单 | P0 | D1–D4 | 可签署的发布证据 |

---

### Step D1：CLI、版本与错误契约

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：G-A、B1、C0
- **目标**：提供稳定的 `dmg-md --version`/`--help` 与可诊断错误；不改变正常 `dmg-md`（无参数）
  的输入/输出与 MPI 启动行为。
- **主责文件**：`src/dmgmd_main.cpp`、`CMakeLists.txt`、新增 `include/dmgmd/version.hpp.in`
  （或等价生成头）、`tests/` CLI 测试、`docs/operations/` 的入口说明。不改数值路径。
- **必读**：`src/dmgmd_main.cpp` 的 `--probe-mpi-environment`/`argc` 分支；`CMakeLists.txt` 的
  project/version；`docs/operations/deployment.md`；`README.md` 的调用方式。
- **实施要点**：
  1. `--version` 在初始化 MPI/GPU **之前**处理，打印产品名、语义版本、构建类型、git commit
     （若 CMake 注入）、CUDA/MPI 编译信息（可选）；不得要求 `run.in`/GPU 存在。`--help` 同样
     不初始化设备，列出用法、`--probe-mpi-environment`、支持的 ensemble/势的文档入口。
  2. 版本来源单一：CMake `project(VERSION ...)` 或显式 `DMGMD_VERSION` 生成头；不要在主文件
     手写版本字符串。`--version` 在 dirty 工作区是否加 `-dirty` 由维护者定，默认只在构建时
     注入，不在运行时调用 git。
  3. 正常入口保持 `dmg-md` 无参数、读 `run.in`/`model.xyz`；未知参数给 usage 并非零退出；
     `--probe-mpi-environment` 行为不变（仍需初始化 MPI/设备）。
  4. 错误契约：输入错误包含 `file:line: message`；运行期错误包含 ensemble/step/plane 上下文；
     rank 0 输出，MPI 下沿用现有 `report_error`/`abort`；不要打印堆栈或内部路径给最终用户。
     确保新增 parser 错误（A1/B2/B3/C1）都走同一通道。
  5. 在 `docs/operations/deployment.md` 写清版本检查、退出码、日志位置；不承诺未实现的
     `--config`/`--dry-run`。
- **测试/验收**：
  - `--version`/`--help` 在无 GPU/无 `run.in` 的临时目录可用；退出码 0；
  - 未知参数非零退出并含 usage；正常无参数路径与基线一致；`--probe` 行为不变；
  - 错误样例包含文件/行号；MPI 下只有一个 rank 打印用户错误（按现有约定）。
- **证据**：`cli-<date>.md`，含命令/输出/退出码；NEP 正常路径回归。
- **停止/回滚**：版本需要运行时 git；`--version` 触发 MPI/GPU 初始化；正常入口行为改变；
  或错误文字丢失来源。回滚 D1 的 main/CMake。

---

### Step D2：许可证与第三方合规

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：G0-D7（维护者确认 GPLv3 路线）
- **目标**：让发布物在法律上可分发：根 LICENSE、第三方声明、GPUMD 来源/版权、compat 文件
  审计、LAMMPS overlay 声明；不改变代码行为。
- **主责文件**：根 `LICENSE`（GPLv3 全文）、`NOTICE`/`THIRD_PARTY_NOTICES.md`、
  `README.md` 的 License 段、`src/gpumd_compat/*` 与新增 compat 文件的头注释审计、
  `third_party/lammps-matpl-overlay/` 的说明；打包清单。**不删除既有版权头**。
- **必读**：`src/gpumd_compat/box.cuh` 等文件头的 GPL/Origin 说明；上游 GPUMD 的 LICENSE；
  `third_party/lammps-matpl-overlay/manifest.json`/`cmake.patch` 的来源与许可；
  `AGENTS.md` 的只读/复制约束；Gate 0-D7。
- **实施要点**：
  1. 加入 GPLv3 全文与项目版权行；`NOTICE` 列出 GPUMD 版权、锁定 commit、被复制的文件/内核、
     修改说明；新增 LJ/EAM/Tersoff compat 文件必须有 Origin 头 + GPL 头 + “最小复制/适配”说明。
  2. 审计整个仓库的第三方代码：`src/gpumd_compat/`、`third_party/`、测试 fixture/势文件、
     Docker 基础镜像/依赖；逐项记录来源、许可证、是否随发布物分发。不要把参考仓库路径写进
     构建或发布包。
  3. 检查 `third_party/lammps-matpl-overlay` 的 patch/manifest 许可与归属；如果它包含 GPL/
     其他许可证代码，更新 NOTICE 并确认分发方式；若无法确认，停止并请维护者法务复核。
  4. README 增加“License”段，说明 GPLv3 义务、如何获取源码、第三方声明位置；发布包包含
     LICENSE/NOTICE/源码对应版本。不要声称与 GPUMD 官方背书。
  5. 本 Step 不做“许可证兼容性”的法律结论；只整理事实并标注需人工复核。若维护者不接受
     GPLv3，按 G0-D7 停止并重新规划非 NEP/compat 的许可路径（不能用“换 LICENSE 文件”解决）。
- **测试/验收**：许可证审计表 100% 覆盖随包文件；新增 compat 头齐全；README/NOTICE 链接有效；
  人工法务复核记录（姓名/日期/结论）附在 status 文档；发布包实际包含许可证文件。
- **证据**：`license-audit-<date>.md` + 审计表 + 法务复核记录。
- **停止/回滚**：任何随包文件来源/许可不明；参考仓库代码被误复制进构建；或试图在没有法务
  结论时发布。D2 无运行时回滚，但可回退文档变更。

---

### Step D3：用户文档与快速开始

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：A5、B4、C5
- **目标**：让一个新用户/新 Agent 按文档完成：构建、NEP NVE、NHC/BDP、NPT、LJ/EAM、MPI、
  restart 与故障定位；文档不得超出已验证能力。
- **主责文件**：`docs/user-guide/`（新建：quickstart、run-in-reference、ensembles、potentials、
  mpi-and-gpu、outputs-and-restart、troubleshooting）、`docs/README.md`、`README.md`；
  示例放 `examples/` 或 `tests/`（小文件）。不改运行时；发现文档暴露的缺陷回到对应 Step。
- **必读**：A5/B4/C5 的证据与兼容矩阵；`README.md` 现有构建/测试/MPI 章节；
  `docs/operations/deployment.md`；`docs/standards/golden-test-standard.md`；Gate 0 决策。
- **实施要点**：
  1. Quickstart 只给一条最短成功路径：环境、构建、最小 NEP 输入、运行、期望输出；每条命令
     可在干净目录执行。不要在第一页塞满所有系综。
  2. run.in reference：按命令列语法、参数单位、默认值、失败行为；与 parser/兼容矩阵逐项对齐。
     特别写清 Tdamp/tau_p 是步数、NPT P/B 是 GPa、`max_neighbor` 只对 EAM。
  3. 系综/势文档链接到标准页，不复制公式；给出“选哪个”的简短指南（NVE/NVT/NPT；NEP/LJ/EAM），
     明确 Berendsen 非严格采样、NHC/BDP 的 restart 限制、NPT 仅 M1。
  4. MPI/GPU：P=1 与 P>1 的差异、M1/M2a 路由、日志字段、常见 UCX/绑定问题；不要承诺跨节点
     I/O 或未验证的 GPU 拓扑。
  5. 输出/restart：18 列含义、盒列时点、restart 字段与不保存的系综状态、NPT unwrapped 限制；
     给出校验命令。
  6. 示例：至少 NEP NVE、NHC、NPT、LJ、EAM 各一个；输入小、可复现；不提交大势/轨迹；说明
     测试势的来源与许可。
- **测试/验收**：文档中的构建/运行命令在干净目录执行（至少 smoke）；示例可运行；链接检查；
  与兼容矩阵/parser 无矛盾；限制醒目。
- **证据**：`docs-user-<date>.md`，含命令/输出、示例列表、链接检查。
- **停止/回滚**：文档需要未实现功能才能跑通；示例依赖不可获取的数据；或把 P1/Tersoff 写成
  v1.0 必备。回滚文档，不改代码。

---

### Step D4：性能门与打包

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：A0、D1、D3
- **目标**：证明新功能没有把现有路径拖垮，并产出可从干净环境构建/运行的发布包；性能数字与
  正确性门分开记录。
- **主责文件**：`tests/benchmark/` 的新 case/脚本、`docs/status/performance/`、`CMakeLists.txt`
  的 install/package（如需要）、`Dockerfile`/`docker/`、`scripts/` 的发布脚本、发布清单。
- **必读**：A0 的性能基线；`README.md` 的 benchmark/MPI 命令；`docs/standards/golden-test-standard.md`
  的“性能不等于正确性”；`docs/operations/deployment.md`；D3 的示例。
- **实施要点**：
  1. 固定 benchmark case：NEP NVE P=1、NEP NVE P=4（M2a 与 M1 fallback 各一）、NHC/BDP、NPT、
     LJ/EAM P=1；固定原子数、步数、warmup、输出关闭或固定间隔；3 次仅作先导；正式采用配对/交错重复、预先冻结样本数与 CI 方法，记录
     GPU/驱动/CUDA/MPI/UCX/rank/backend。
  2. 门限默认（维护者可改，但必须书面）：现有 NEP NVE/`nvt_ber` 相对 A0 同机同配置中位数退化
     ≤3%；NHC/BDP 对同配置每步 thermo 的 NVT、NPT 对匹配恒温器的 NVT 记录增量；
     原稿 5%/10% 仅作规划目标，A0 先导后冻结预算，不因超出任意百分比判定算法错误。
     LJ/EAM 只记录，不设跨势比较；本门不依赖 O 系列候选采用。
     若测量噪声大于门限，报告 CI 并请维护者裁定，不能悄悄放宽。
  3. 内存：记录峰值显存/主机内存；EAM 的 `N*max_neighbor`、NPT 的额外状态、NHC 链可忽略；
     若接近上限，文档写清推荐规模，不把 OOM 当性能噪声。
  4. 打包：从干净 checkout/源码包按 README 构建；包含 LICENSE/NOTICE/docs/examples；检查
     `--version`、CTest 子集、baseline smoke、MPI P=2 smoke；记录 CUDA 架构/依赖版本/校验和。
     不引入新的包管理器，除非维护者批准。
  5. 记录“未测平台/未测后端/未测规模”；性能报告不得与正确性 PASS 混写。
- **测试/验收**：门限表全 PASS 或有维护者书面 waiver；干净构建/打包 smoke 通过；发布包内容
  清单完整；性能报告可复现。
- **证据**：`docs/status/performance/v1-candidate-<date>.md` + 打包清单 + 校验和。
- **停止/回滚**：现有路径退化超门限且无法解释；打包依赖参考仓库/本机绝对路径；或为了性能
  修改 NEP/golden。回滚性能相关提交，保留功能。

---

### Step D5：v1.0 发布清单

- **状态**：PROPOSED｜**优先级**：P0｜**依赖**：D1–D4
- **目标**：把 G-A/G-B/G-C/G-D 的证据汇总成可签署的发布记录；准备发布说明、已知问题与回滚
  方案。**Agent 不执行 tag/push/发布**，除非维护者明确授权；默认只准备物料。
- **主责文件**：`docs/status/current.md`、`CHANGELOG.md`（新建或现有）、`docs/status/release-v1.0-<date>.md`、
  `README.md` 的版本/许可/限制段；发布说明草案。不改代码；发现缺口回到对应 Step。
- **必读**：A0–D4 的全部 status；Gate 0 决策；`docs/README.md`；`AGENTS.md` 的提交/推送限制；
  `docs/operations/deployment.md`。
- **实施要点**：
  1. 逐项勾选 G-A/G-B/G-C/G-D 的门条件；每项附证据路径与执行日期；未执行的矩阵项写明原因，
     不能把“没跑”写成“通过”。
  2. 版本/变更：语义版本、支持矩阵、已知限制（NHC/BDP restart、NPT unwrapped、非 NEP 无 M2a、
     Tersoff P1、LJ 无 shift、EAM 格式）、破坏性变更（若有）；不要用“全面支持 GPUMD”描述。
  3. 发布物料：源码包/二进制、LICENSE/NOTICE、文档、示例、校验和、构建环境；确认 `--version`
     与包一致；确认未包含参考仓库/大轨迹/本机路径。
  4. 回滚：上一版本、数据/restart 兼容性、已知失败模式与联系路径；发布后 48h 的 smoke/监控
     （若适用）。
  5. 签署：维护者姓名/日期/决定；Agent 只提交证据与草案，不自行发布。若维护者要求提交/推送，
     按 `AGENTS.md` 的流程另行确认，且仍不做破坏性 Git 操作。
- **测试/验收**：发布清单 100% 有状态；关键 smoke 在候选包上重跑；文档/许可证/版本一致；
  维护者签署。
- **证据**：`release-v1.0-<date>.md` + 清单 + 候选包校验和 + 签署记录。
- **停止/回滚**：任一 P0 门未过；许可证未复核；发布包与源码不一致；或已知限制未写。停止发布，
  回到失败 Step；不要用“后续补文档”换取发布。

## 10. 测试与证据架构

### 10.1 测试分层

| 层 | 内容 | 运行条件 | 判定方式 |
| --- | --- | --- | --- |
| L0 | parser/IR、metadata、盒合同、列映射单元测试 | CPU，无 GPU | 精确断言/错误信息 |
| L1 | 单步静态力/能量/virial/压力 | 1 GPU | 解析值 + GPUMD 单步 golden |
| L2 | 确定性短轨迹：NVE、`nvt_ber`、`nvt_nhc`、`npt_ber` | 1 GPU（P=1） | GPUMD golden / 同二进制哈希 |
| L3 | 随机统计：`nvt_bdp`、`npt_scr` | 1 GPU，多种子 | 均值/方差/自相关/CI，不做轨迹比对 |
| L4 | MPI/后端矩阵：M1 P1/P2/P4、M1 fallback、M2a P2/P4、默认/CudaAware | 多 GPU | 同 rank/backend 可复现；跨 rank 容差/统计 |
| L5 | 合同：restart、输出列/时点、失败关闭、路由 | 1–4 GPU | 逐项断言/错误来源 |
| L6 | 性能/内存/打包 | 指定机器 | 中位数、阈值、干净构建 smoke |

> L1–L5 是正确性门；L6 不能替代其中任何一项。一个 case 通过 L2 不代表 L3 的采样正确；
> 一个 NVE 能量守恒也不代表 NVT/NPT 的系综正确。

### 10.2 常用命令

执行 Agent 必须先读 `README.md` 与脚本 `--help`，以下命令是基线模板，路径/参数以仓库实际为准：

```bash
source ../env/md-mpi.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
python3 tests/mpi/check_environment.py --candidate ./build/dmg-md --devices 0,1 --ranks 2
python3 tests/mpi/run_mpi_domain.py --candidate ./build/dmg-md --devices 0,1,2,3
python3 tests/long_nve/run_long_nve.py --candidate ./build/dmg-md --devices 0 --profile smoke
```

- 参考 GPUMD 二进制只用于**运行** oracle（工作目录放 `/tmp`，不在参考树内写文件）；若二进制/
  golden 不可用，标 `BLOCKED`，不要用 DMG-MD 的另一条路径替代。
- 运行前记录 `git status --porcelain`；运行后记录退出码、stdout/stderr、生成文件哈希。
- 不在参考树 `../gpumd-reference`、`../lammps-reference`、`../lammps-matpl-reference` 内构建、
  写日志、清理或应用补丁。

### 10.3 确定性与统计的判定规则

**确定性（NVE、`nvt_ber`、`nvt_nhc`、`npt_ber`）**

- 同二进制、同输入、同 rank/backend/路径：优先要求 SHA-256 一致；若因构建/环境不可比，按
  `docs/standards/golden-test-standard.md` 的层与容差比较，并记录原因。
- 跨 rank 或跨后端：默认不要求 byte-exact；比较 thermo/dump 的物理量在容差内，并说明是
  归约顺序/编译差异，不是“数值等价”。
- NHC 与 GPUMD P=1 的短轨迹是强证据；NPT 与 GPUMD P=1 的短轨迹同理。若参考不可用，至少要有
  解析/守恒/同二进制重复，不能只凭“趋势正确”。

**随机（`nvt_bdp`、`npt_scr`）**

- 同 seed、同二进制、同 rank/backend：要求可复现（短轨迹哈希或逐值一致）；不同 seed 必须产生
  不同样本。默认种子是产品行为，不是测试偶然。
- 统计验收至少包含：`<T>`/`<P>` 与目标；动能/体积方差；自相关时间；块平均与 95% CI；
  多个独立 seed。报告样本数、丢弃段、块大小与 CI 方法。
- 不得挑选 seed、丢弃不利样本或只报“均值看起来对”。不得用 Berendsen 的涨落证明正则/NPT；
  Berendsen 是兼容路径，不是严格采样器。

### 10.4 矩阵记录模板

每个 case 至少记录：

| 字段 | 示例 |
| --- | --- |
| case_id | `npt_ber-m1-p2-lj-001` |
| ensemble | `npt_ber T1 T2 Tdamp P B tau_p` |
| potential | `NEP4` / `LJ` / `EAM/alloy`（含 `max_neighbor`） |
| ranks / plane / backend | `2 / M1 / 默认` |
| steps / seed / output interval | `500 / 12345678 / 10` |
| 预期/判定 | `PASS` / `FAIL` / `SKIP`（原因、批准人） |
| 证据 | status 文档段落 + 原始日志路径 + 哈希 |

### 10.5 证据文档规范

- 位置：`docs/status/validation/<topic>-<YYYYMMDD>.md`；性能放
  `docs/status/performance/<topic>-<YYYYMMDD>.md`；不要把原始大日志提交进仓库。
- 必含：revision + dirty 文件、GPU/驱动/CUDA、MPI/UCX、构建类型、精确命令、case/seed/ranks、
  结果（含失败）、skip 与原因、已知限制、证据文件哈希。
- 结论分级：`PASS`（实际执行且满足）、`FAIL`、`SKIP`（未执行/环境不允许）、`UNKNOWN`
  （无法证明）。**不得把 SKIP/UNKNOWN 写成 PASS**。
- 文档变更也要有日期与责任 Step；过期结论标 `SUPERSEDED` 并链接新文档。

### 10.6 完成回报模板

执行 Agent 完成一个 Step 后，用中文按下面模板回报（不要只说“已完成”）：

```text
## Step <编号> 完成回报

- 状态：DONE / BLOCKED / PARTIAL（若 PARTIAL 说明缺什么）
- 基线：revision <sha>，dirty=<是/否，列出文件>；构建类型/编译器/CUDA/MPI/UCX
- 改动文件：<路径列表>；是否触碰 gpumd_compat/NEP 数学：<是/否，理由>
- 行为变化：<用户可见的语法/输出/默认值；没有就写“无”>
- 测试实际执行：
  - <命令> → <PASS/FAIL/SKIP>，<关键输出/退出码>
  - ...
- 数值/统计证据：<golden 对比、哈希、均值/方差/CI、矩阵表；附 status 文档路径>
- 未覆盖/限制：<平台/规模/组合/restart 语义；明确列出>
- 回滚方式：<回退哪些文件/如何恢复到本 Step 前>
- 下一步建议：<下一个 Step 或阻塞项>
- 声明：未提交、未推送、未修改参考仓库；未运行项已标 SKIP/UNKNOWN。
```

## 11. 风险登记

| ID | 风险 | 触发信号 | 缓解/验证 | 受影响 Step |
| --- | --- | --- | --- | --- |
| R1 | Berendsen 迁移改变既有数值 | A/B 哈希不一致、thermo 列漂移 | A2 先做零变化迁移；同构建 A/B；不碰 NEP | A2 |
| R2 | 压力单位/符号/列映射错误 | 压缩为负压、GPa 差 160 倍、sxy/syx 互换 | B0 oracle；非对称应力列测试；单一换算边界 | B0/B2/B3 |
| R3 | 变盒缓存/所有权失效 | P>1 变盒后原子丢失/重复、owner 报错 | B1 `refresh_box` + 缩放后 owner map 测试；M2a 不接入 | B1/B2 |
| R4 | NPT restart/unwrapped 语义错误 | split-run 不连续、unwrapped 与 box 不一致 | 缩放后 snapshot；restart 测试；B4/D3 文档或拒绝组合 | B1/B2/B4 |
| R5 | 随机系综不可复现/各 rank 独立采样 | 同 seed 不同结果、跨 rank 温度异常 | root `mt19937`；seed env；只广播 factor；统计多种子 | A4/B3 |
| R6 | M2a 的 NHC/BDP 与 M1 不一致 | M2a 温度漂移、ghost 计入 KE | owned-only KE 归约；A5 矩阵；不共用 M1 所有权假设 | A3/A4/A5 |
| R7 | 势抽象污染 NEP | NEP golden 变化、kernel diff | C0 adapter 不改 NEP；A/B/C0 回归；代码审查 | C0–C3 |
| R8 | EAM 文件格式/样条误解 | 能量/力不对、文件头读取失败 | 用 GPUMD 包装 fixture；逐字样条；静态/轨迹 golden | C1/C3 |
| R9 | GPL/第三方合规 | 发布包缺 LICENSE/NOTICE、来源不明 | D2 审计 + 法务复核；保留 Origin/版权头 | D2/D5 |
| R10 | 性能回归 | 现有路径超阈值、NPT 开销异常 | A0 基线；D4 固定 case/中位数/CI；不拿性能换正确性 | D4 |
| R11 | 范围蔓延（MTTK/PR/三斜/多势） | 卡内出现未批准功能、golden 被迫放宽 | 按 §1.3 范围拒绝；另立计划；Gate 0 决策 | 全阶段 |
| R12 | 误用参考仓库 | 构建链接参考源码、在参考树写文件 | AGENTS.md 只读；C0/C2/C3 审查；CI 检查 | 全阶段 |
| R13 | root/非 root 状态分歧 | 因子不一致、链/RNG 分叉 | 单一 root 状态 + 标量广播；失败关闭；矩阵验证 | A2–A4/B3 |
| R14 | 邻居容量/显存不足 | LJ 700 溢出、EAM 大 `max_neighbor` OOM | 复刻上游容量；测试失败行为；文档推荐规模 | C2/C3/D4 |
| R15 | 文档过度承诺 | 文档说支持但矩阵未过 | A6/B4/C5/D3/D5 文档门；以兼容矩阵为准 | 全阶段 |

## 12. Definition of Done 与检查清单

### 12.1 每个 Step 的 DoD

- [ ] 代码只改本 Step 主责文件范围；越界已获维护者同意。
- [ ] 新增行为有测试；失败路径有负例；未运行/跳过项明确标注。
- [ ] 既有 NVE/`nvt_ber` golden 未被放宽或重写；NEP 数学/compat 无未批准 diff。
- [ ] 所有权/MPI/通信影响已评估；无未解释的 collective 或数据竞争。
- [ ] 证据写入带日期的 status 文档；含 revision/环境/命令/结果/限制。
- [ ] 用户可见变化已更新兼容矩阵/文档；未完成的不写成支持。
- [ ] 回报按 §10.6 模板；代码未提交/未推送（除非维护者明确要求）。

### 12.2 阶段门清单

**G-A（恒温器）**

- [x] A0–A6 完成；A7 不阻塞。
- [x] NHC/BDP 在 M1 P1/P2/P4（含 fallback）与 M2a P2/P4 通过。
- [x] NHC P1 GPUMD golden；BDP 多种子统计；同 seed 可复现。
- [x] restart 不连续已文档化；兼容矩阵/用户文档更新。
- [x] 现有 NVE/`nvt_ber` golden 全过。

**G-B（控压器）**

- [ ] B0 压力 oracle、B1 盒合同、B2/B3 NPT、B4 矩阵/文档完成。
- [ ] `npt_ber` GPUMD P1 golden；`npt_scr` 统计验收。
- [ ] P>1 M1 fallback、M2a 路由/防御性拒绝、restart、负 P、B>2000、错误矩阵通过。
- [ ] 盒/位置/restart/输出时点一致；unwrapped 限制已处理。
- [ ] 现有全矩阵无回归。

**G-C（势函数）**

- [ ] C0/C1 不改 NEP 数值；C2 LJ、C3 EAM 静态/短轨迹 golden 通过。
- [ ] P=1/P>1 M1、M2a 拒绝、`max_neighbor`/格式错误矩阵通过。
- [ ] C4 若启用则独立验收，不阻塞 v1.0；C5 能力矩阵/文档完成。
- [ ] NEP+LJ/EAM 的既定 P0 支持组合全部通过；缺项不得以“子集可发布”绕过。

**G-D（产品化）**

- [ ] D1 CLI/版本/错误、D2 许可证/合规、D3 用户文档、D4 性能/打包、D5 发布清单完成。
- [ ] 干净环境可构建/运行；发布包含 LICENSE/NOTICE/docs/examples/校验和。
- [ ] 性能门通过或有书面 waiver；已知限制在发布说明中。
- [ ] 维护者签署；未授权不 tag/push。

### 12.3 v1.0 发布 DoD

- [ ] 版本号、`--version`、CHANGELOG、README 的安装/许可/限制一致。
- [ ] 兼容矩阵覆盖 NEP/LJ/EAM、NVE/nvt_ber/nvt_nhc/nvt_bdp/npt_ber/npt_scr、M1/M2a 范围。
- [ ] 所有 P0 测试在候选包上实际执行；P1（Tersoff/Langevin）不伪装成 P0。
- [ ] restart/输出/错误契约有测试与文档；没有“静默降级”。
- [ ] 许可证/第三方声明/法务复核完成。
- [ ] 性能基线/门限/环境记录完整；无未解释回归。
- [ ] 发布说明列已知问题、回滚方式、支持范围；维护者签署。

### 12.4 不允许的“完成”

- 未运行测试却写“全部通过”；把 skip 写成 pass；只跑单卡就宣称 MPI 支持。
- 为了让新功能通过而更新 golden、放宽容差、改变 NEP 数学或温度自由度。
- 用 Berendsen 的涨落证明 NHC/BDP/SCR 的正确性；用单 seed 证明随机采样。
- 在 M2a 上静默降级 NPT/非 NEP；在 NPT 中静默缩放/不缩放 unwrapped 而不记录。
- 在参考仓库内构建/写文件，或把参考源码链接进构建。

## 13. 上游证据附录（只读 GPUMD 锁定 commit）

> 锁定 commit：`9d23496e41319b9e2af5221a7df6285387401d1e`。以下路径/行号只用于阅读与
> 工程核对；执行 Agent 必须用 `rg` 在当前参考树复核。**不得复制源码进构建，除非是
> `src/gpumd_compat/` 中带 Origin 头的最小内核。**

### 13.1 系综语法与参数

| 事实 | 上游锚点 | 计划用途 |
| --- | --- | --- |
| `nvt_nhc`/`nvt_bdp`/`nvt_lan`/`nvt_bao` 均为 5 token：`T1 T2 Tdamp` | `src/integrate/integrate.cu:415-444` | A1/A3/A4 parser |
| `nvt_ber` 5 token；`Tdamp>=1`（V3.0 起单位为步数） | `src/integrate/integrate.cu:419-424`、572-610 | 保持既有语义 |
| `npt_ber`/`npt_scr` 接受 8/12/18 token；各向同性 8 token 为 `T1 T2 Tdamp P B tau_p` | `src/integrate/integrate.cu:455-463`、620-712 | B2/B3；v1 仅 8 |
| 各向同性 NPT 要求正交盒 + 全周期；3/6 分量有额外盒限制 | `src/integrate/integrate.cu:655-693` | B2 失败关闭 |
| 输入 P/B 为 GPa；内部 `P/=160.2177`、`pc*=160.2177` | `src/integrate/integrate.cu:1152-1153`、1189-1190 | B0/B2 单位边界 |
| `pc=1/(3*tau_p*B)`；`B>2000` 时 `pc=0` | `src/integrate/integrate.cu:710-716` | B2 兼容分支 |
| 温度 ramp：`T1+(T2-T1)*step/steps` | `src/integrate/integrate.cu:341-344`、387-390 | A2 统一调度 |

### 13.2 NHC / BDP / SCR

| 事实 | 上游锚点 | 计划用途 |
| --- | --- | --- |
| NHC 链长 4；`pos=0`、`vel=[1,-1,1,-1]`；`Q_i=k_B*T*tau^2`，`Q_0*=3N`，`tau=dt*Tdamp` | `src/integrate/ensemble_nhc.cu:30-50`；`ensemble.cuh` 链长 | A3 |
| NHC 每步：前 half（KE→链→缩放）→ VV1；VV2→thermo→后 half | `src/integrate/ensemble_nhc.cu:175-236` | A3 hook 顺序 |
| NHC `nhc` 使用 Suzuki–Yoshida 7 项 × 4 RESPA、`Ek2` 更新与 factor 累乘 | `src/integrate/ensemble_nhc.cu:105-236` | A3 逐字复刻 |
| BDP `resamplekin` + `gasdev`/`gamdev`；`taut=Tdamp`、`ndeg=3N` | `src/integrate/ensemble_bdp.cu:89-100`；`svr_utilities.cuh:28-126` | A4 |
| BDP release 用 wall-clock 种子，DEBUG 用 12345678 | `src/integrate/ensemble_bdp.cu:29-35` | A4 有意偏离 + 文档 |
| SCR 各向同性因子 = Berendsen 项 + `sqrt((2/3)*pc*k_B*T/V)*gauss` | `src/integrate/ensemble_npt_scr.cu:137-160` | B3 |
| SCR 顺序：VV2→thermo→BDP 重标定→盒/位置缩放 | `src/integrate/ensemble_npt_scr.cu:240-280` | B3 |
| `gasdev` 使用 Marsaglia polar + `uniform_real_distribution`，带静态 `iset/gset` | `src/integrate/svr_utilities.cuh:28-50` | A4/B3：改为控制器成员 |

### 13.3 压力、盒与输出

| 事实 | 上游锚点 | 计划用途 |
| --- | --- | --- |
| 内部压力 `(virial+kinetic)/V`；`find_thermo` 用 3N；应力数组 `xx yy zz xy xz yz` | `src/integrate/ensemble.cu:434-673` | B0 |
| Berendsen 各向同性：`scale=1-pc*(P0-P)`；盒对角乘 scale；位置同 scale；`get_inverse()` | `src/integrate/ensemble_ber.cu:138-148`、195-280 | B2 |
| NPT 顺序：VV2→thermo→温度缩放→盒/位置缩放 | `src/integrate/ensemble_ber.cu:195-280` | B2/B3 |
| 位置绕原点缩放；barostat 不缩放速度 | `src/integrate/ensemble_ber.cu`、`npt_utilities.cuh` | B1/B2 |
| `restart.xyz` 仅 N/pbc/Lattice/species/pos/mass/vel/group | `src/measure/dump_restart.cu:75-130` | D4/B4 restart 限制 |
| unwrapped 更新在 compute1 周围；NPT 的盒缩放不进入 unwrapped 增量 | `src/integrate/integrate.cu:325-380` | B1/B4/D3 文档 |
| thermo 输出列/应力顺序由 DMG-MD 现有 `write_thermo_row` 定义 | `src/runtime.cu:1086`（DMG-MD） | B0 映射测试 |

### 13.4 势文件格式

| 事实 | 上游锚点/示例 | 计划用途 |
| --- | --- | --- |
| `potential` 命令 1 或 2 个参数；第一 token 决定格式 | `src/force/force.cu:75-190` | C1 |
| `eam/alloy` 可选第二参数 `max_neighbor`，范围 1..1024，默认 400 | `src/force/force.cu:103-111` | C1/C3 |
| LJ 文件：`lj T symbols`，随后 T×T `epsilon sigma cutoff`，无 shift | `src/force/lj.cu:30-70` | C2 |
| EAM/alloy 文件：`eam/alloy T symbols` + 两行注释 + 第 4 行 `T symbols` + setfl 正文 | `potentials/eam/CuNi.eam.alloy`；`src/force/eam_alloy.cu:130-165` | C3 |
| EAM 样条：中心差分 + Hermite 系数 + float 存储/读取规则 | `src/force/eam_alloy.cu:35-110`、165-260 | C3 |
| Tersoff 1989 文件头与参数 | `src/force/tersoff1989.cu`、`force.cu:93-95` | C4（P1） |

### 13.5 只读与复制边界

- 允许：阅读参考源码/文档、运行参考二进制做 oracle（在参考树外的工作目录）。
- 不允许：在参考树内构建、写日志/输出、清理、打补丁；把参考源码链接进 DMG-MD 构建；
  直接调用参考库。
- 允许复制到 `src/gpumd_compat/` 的只有最小内核，且必须保留 GPL/Origin 头、说明适配；
  C2/C3/C4 的新 compat 文件同样适用。复制后必须重跑 NEP golden 与新增势 golden。

## 14. 术语表

| 术语 | 含义 |
| --- | --- |
| M1 | replicated-full 数据面：位置/类型等全量复制，逻辑 slab 所有权；P=1 与 fallback 路径 |
| M2a | local-domain 数据面：owned/dependency ghost、两跳 halo、p2p migration；仅 NEP |
| owned / ghost | 当前 rank 拥有/仅用于依赖的原子；温度/KE 只计 owned，输出按所有权规则 |
| NHC | Nosé–Hoover 链恒温器；确定性、严格正则（在遍历/参数合适前提下） |
| BDP | Bussi–Donadio–Parrinello 随机速度重标定；严格正则、随机 |
| SCR | Stochastic Cell Rescaling；BDP 温度 + 随机盒缩放的 NPT |
| Berendsen | 一阶松弛恒温/控压；兼容 GPUMD，但涨落不是严格系综 |
| NPT | 恒温恒压；v1 仅各向同性、正交全周期、M1 |
| Tdamp / tau_p | 输入单位是**步数**；NHC 质量使用 `dt_internal * Tdamp`，其他步尺度公式不额外乘 dt |
| P / B | NPT 目标压力/体积模量，输入 GPa；内部换算为 eV/Å³ |
| 内部压力 | `(virial+kinetic)/V`，自然单位 eV/Å³；输出才乘 160.2177 得 GPa |
| unwrapped | 跨周期边界累积的位置；NPT 下 v1 不随盒缩放（上游行为/限制） |
| golden | 参考实现产生的确定性轨迹/输出基准；不是“随便跑一次的结果” |
| 统计验收 | 多种子/长轨迹的均值、方差、自相关、置信区间；不要求轨迹一致 |
| fail-closed | 未实现/未验证组合在动力学前报错，不静默降级或忽略参数 |
| Gate / Step | 阶段门/可独立派发的任务卡；一次只执行一个 Step |

---

> 本计划的状态是 **IN PROGRESS**。维护者已于 2026-10-30 确认第 5 节 Gate 0 决策；A0、A1、A2 已完成，
> 当前唯一下一步为 B0（压力 oracle）。每个 Gate 的证据入库并通过后，才进入下一阶段。计划本身不替代代码审查、法务复核或
> 真实的 GPU/MPI 验收。
