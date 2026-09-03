# 尚未解决的问题与决策队列

本文件保留初始审计快照和后续 domain-decomposition 决策队列；其中“审计开始时”的构建/测试
描述不是当前操作指南。当前 replicated-data 实现、固定 Open MPI+UCX 环境和验证命令以
`docs/progress.md`、`docs/replicated-mpi.md` 与 `AGENTS.md` 为准。

## 1. 仓库与构建审计记录

审计开始时的目录结构是两个独立Git仓库：

| 仓库 | branch/commit | 初始状态 | 处置 |
| --- | --- | --- | --- |
| `newmd/` | `main`, `ea3afff4e74aebefd06da81d9677d34ab621a90d` | 用户已有未跟踪 `docs/data-layout.md` | 保留原内容，只追加本次审计章节；生产源码未改 |
| `gpumd-reference/` | `master`, `9d23496e41319b9e2af5221a7df6285387401d1e` | clean | 全程只读 |

审计开始时的旧 NewMD：

- `CMakeLists.txt` 要求 CMake 3.18，C++17/CUDA17；显式构建 `newmd_core`、`newmd` 和tests。
- 配置：`cmake -S . -B build -DCMAKE_BUILD_TYPE=Release`。
- 构建：`cmake --build build -j`。
- 测试：`ctest --test-dir build --output-on-failure`。
- 已有测试覆盖 `DeviceBuffer`、Atom SoA、SimulationBox、CPU CSR neighbor、CPU/GPU PBC和device query；尚无parser、NEP、integrator、MPI测试。
- 本次在已有 `build/` 上执行了完整CTest：9项中CPU-only的 `newmd.simulation_box`、`newmd.neighbor_list_cpu` 通过；其余7项均在测试启动时因环境无CUDA-capable device失败（包括3项compute-sanitizer包装测试），没有出现生产代码编译或数值断言失败的证据。

GPUMD reference：

- 顶层 `CMakeLists.txt` 要求 CMake 3.24，C++17/CUDA17，glob多个源码目录并链接cuBLAS/cuSOLVER/cuFFT。
- `src/makefile` 是传统NVCC/C++14构建路径，生成 `src/gpumd` 和 `src/nep`。
- `tests/` 主要为样例/手工回归；`tests_pytest/` 是当前GPU-only pytest suite，包含共享fixture、golden和经验容差。
- 不能把“GPUMD能编译全部模块”当作DMG-MD最小依赖证据；新runtime应显式列源文件。
- 本次执行 `cmake --build build -j 2`，现有NewMD全部target构建成功；文档和 `AGENTS.md` 修改不参与产物链接。

## 2. 阻塞第一个实现切口的问题

| ID | 问题 | 代码证据/当前判断 | 建议决策或实验 | 开发者决策 |
| --- | --- | --- | --- | --- |
| Q1 | 可否直接复制/链接GPUMD GPLv3 kernel？ | 每个参考源码有GPLv3 header；产品要求优先复用 | 维护者先确认DMG-MD许可证/分发方式。若不兼容，不能通过“重新实现公式”规避产品约束，需法律/架构决策 | 可以直接复制/链接GPUMD，目前阶段不考虑GPL许可证的问题 |
| Q2 | MVP reference是否永久锁定此commit？ | 语法已随GPUMD演化，例如旧dump被删除、NEP3入口矛盾 | 将commit写入compat version；升级只能经差异审计和golden更新 | 同意建议决策 |
| Q3 | 第一个GPU neighbor布局用GPUMD ELL还是NewMD CSR/SlicedELL32？ | verified NEP kernels硬编码 `slot*N+center`；当前NewMD CSR在host | 第一切口建议ELL以最少改kernel。CSR优化需独立数值/性能提案 | 直接使用GPUMD ELL，将原NewMD项目中的CSR/SlicedELL作为后续优化方向 |
| Q4 | 第一版halo协议选择深位置halo还是分阶段交换？ | kernel证明两跳；`NEP_MULTIGPU`用2rc坐标窗口 | 先实现保守两跳oracle，再以其验证 `Fp`+partial staged protocol；不要直接只做一种 | 同意建议决策 |
| Q5 | edge partial如何跨rank唯一匹配？ | many-body按local整数邻居row查reverse edge；迁移会换index | 设计 `(center_gid,neighbor_gid,image)` 键及接收后local slot map；先在2-rank边界fixture证明 | 同意建议决策 |
| Q6 | 第一切口支持哪些box？ | NewMD仅正交全周期；GPUMD支持triclinic和逐方向PBC | 建议执行层首切口只接受大正交全周期，其他box parse后明确unsupported；不能静默转换 | 原NewMD考虑重写项目，初版仅支持正交全周期。新设计考虑复用GPUMD内核，故不影响MPI功能开发可以跟随GPUMD，如果影响则先开发正交全周期MVP版本 |
| Q7 | rank-to-GPU绑定API和MPI能力目标？ | 当前GPUMD按visible device count选`NEP_MULTIGPU` | 固定 Open MPI+UCX；shared local rank 绑定唯一 GPU；默认 HostStaged，CudaAware 经 MPIX query 与数值自检；NEP core不得枚举设备 | 已按建议实现并由 1/2/4-rank 双后端矩阵关闭 |
| Q8 | parser是逐rank读文件还是rank0广播？ | 独立读取可遇到非共享FS/文件变化；GPUMD模块还会二次扫描run.in | 建议rank0读原始bytes、parse typed IR并广播；所有rank校验hash。确定potential大文件广播策略 | rank0读原始文件并广播，potential由使用者自行确定每一个rank上都有相同拷贝，在初始化时验证potential文件的哈希值是否相同。

## 3. NEP格式与kernel未决问题

| ID | 问题 | 为什么UNKNOWN | 关闭方法 |
| --- | --- | --- | --- |
| Q9 | NEP3是否要支持？ | `Force::parse_potential()`接受nep3名字，但`NEP::NEP()`拒绝并声称只支持NEP4 | pinned executable negative test；MVP先明确unsupported，不声称GPUMD泛版本兼容 |
| Q10 | `Ra>Rr`是否合法？ | loader允许任意值；global list用max radial，filter先radial后angular | 构造合成potential并运行reference；决定忠实截断、复现错误或加载时拒绝 |
| Q11 | ZBL outer cutoff大于angular cutoff时语义？ | ZBL kernel收到angular list，不是独立ZBL list；无校验 | 合成NEP-ZBL实验；halo计算前必须锁定实际有效半径 |
| Q12 | typewise halo的最紧上界？ | pair cutoff取两type平均，两跳由i-j-k组合决定 | loader后枚举type triples计算几何上界；用mixed-type边界case验证 |
| Q13 | neighbor capacity overflow行为？ | large ELL按输入MN×1.25分配，kernel未见显式越界保护；small固定2000 | sanitizer/高密度case；DMG-MD应安全报错，但错误文字兼容需定义 |
| Q14 | 是否保留skin=1 Å及相同rebuild判据？ | `Neighbor`固定skin=1，任一原子位移>0.5重建；MPI需要global OR | 首版保持数值语义；性能调参只能作为显式未来选项并有neighbor golden |
| Q15 | neighbor row排序键用local index还是global ID？ | GPUMD按当前全局数组index排序；MPI local index随rank/reorder变化 | 为rank数稳定的累加顺序，建议按global ID/image排序；先量化与GPUMD的数值差 |
| Q16 | small-box何时支持？ | 它是O(N²×images)、atomic scatter并需要reverse force；与large通信协议不同 | MVP parse model后若触发small-box明确unsupported；以后单独milestone和golden |
| Q17 | `NEP_MULTIGPU`能否复用任何源码？ | 数值kernel与单GPU重复，但参数布局/实现有分叉；编排全局GPU0 | 不复用编排。diff kernel版本并用单GPUcore统一后再决定是否移植其局部cell builder |
| Q18 | 每1000次 `neighbor.out` 是否兼容必需？ | `NEP::compute_large_box()`隐式append，非命令控制 | 调研用户/测试是否依赖；若不保留，compat matrix明确例外；若保留只能rank0聚合 |
| Q19 | NEP parameter parser要复现哪些宽松/异常行为？ | 多个keyword文本不校验、参数行只读首token、未知元素Z=0 | 建立malformed corpus跑reference；区分“兼容接受”与“安全必须拒绝”的产品决策 |

## 4. `run.in` 与产品兼容未决问题

| ID | 问题 | 当前证据 | 建议 |
| --- | --- | --- | --- |
| Q20 | `neighbor`命令来自哪个GPUMD版本/用户输入？ | 当前dispatcher无此命令，只有`run.cuh`陈旧声明 | 收集真实run.in/目标GPUMD版本；当前MVP识别后明确unsupported/不存在 |
| Q21 | 旧 `dump_position/velocity/force/exyz` 是否要兼容旧GPUMD？ | pinned reference明确报已删除并提示dump_xyz | 对本reference复现迁移错误。若产品要跨旧版本，另建versioned compatibility profile |
| Q22 | `time_step 0` 和负值是否要原样接受？ | parser没有正值校验；GPUMD pytest用0做静态输出 | 0应纳入compat golden；负值/run 0/负steps用executable锁定。若安全策略更严，必须公开为差异 |
| Q23 | 多个potential/多段run是否属于MVP？ | potential vector跨run保留；只有全NEP可多potential；model type从run.in最后一个potential预读 | 第一MVP建议只允许一条potential并对第二条明确unsupported；不能静默替换。未来需精确复现相加与symbol检查 |
| Q24 | 未显式`ensemble`时的行为？ | `run`直接调用`Integrate::initialize`，默认type状态需运行确认 | negative golden；MVP应明确要求ensemble，错误类别与reference比较 |
| Q25 | `potential FILE x|y|z`在DMG-MD中的处理？ | 单GPUreference实际忽略第三token；多GPU把它作为slab方向 | 一rank一GPU中该选项没有同义语义。建议parser识别并报unsupported，而不是误用为MPI decomposition hint |
| Q26 | basic NVT最终选择？ | `nvt_ber`最简单且确定，但不是严格canonical；NHC更物理但全局chain/状态复杂 | 第一阶段选`nvt_ber`并如实命名；不要宣传为严格canonical sampler |
| Q27 | `fix`是否真实MVP需求？ | 代码支持group固定并改温度DOF；用户语料未知 | 收集目标工作负载。当前parse-only unsupported；若高频，优先于随机thermostat加入 |
| Q28 | `correct_velocity`是否真实MVP需求？ | 周期命令interval>=10；默认初始化本身已做一次全局修正 | 建议MVP第二切口实现；先用global reduction/global ID重现，避免每rank各自修正 |
| Q29 | velocity seed宽松语法是否复现？ | `velocity T ANY SEED`中ANY不检查，seed也未检查>0 | malformed/seed 0/负seed golden；typed parser可以保存原行为但输出warning与否需决策 |
| Q30 | unsupported错误是否要求逐字一致？ | 产品要求明确错误且支持功能错误校验一致；未明确未支持命令message | 建议测试error code/category和关键token；对已删除dump复用reference迁移文案 |

## 5. MPI数值、I/O和restart未决问题

| ID | 问题 | 当前判断 | 关闭方法 |
| --- | --- | --- | --- |
| Q31 | 跨rank数的可重复性承诺 | MPI reduction非结合，轨迹混沌；per-atom若按global ID排序可能很接近 | 规定：结构字段exact；静态per-atom容差；global sums容差；长轨迹统计。是否提供deterministic mode另议 |
| Q32 | 初始随机速度是否必须与GPUMD逐原子相同？ | GPUMD用libc rand和原数组index；跨平台本就可能不同 | 优先承诺同seed/rank数无关的DMG结果，而非逐bit GPUMD RNG；但这与严格默认兼容冲突，需产品确认 |
| Q33 | thermostat RNG跨rank数/重启 | MVP Berendsen无随机数；未来Langevin state按local index会变 | counter-based global-ID RNG；restart epoch/counter可能需要sidecar |
| Q34 | restart是否允许sidecar？ | GPUMD `restart.xyz`不含ID/time/RNG/thermostat | 主文件必须兼容；可选隐藏/显式sidecar是否破坏用户预期需决定。无sidecar不能承诺完整NVT连续性 |
| Q35 | rank0 gather的规模上限 | 兼容formatter天然需要全局顺序；一次gather可能OOM | MVP定义最大N或分块Gatherv；长期可parallel write但必须保持严格record顺序 |
| Q36 | global ID是否写入用户可见文件？ | GPUMD格式没有ID；增加Properties列会破坏byte/schema兼容 | 内部ID默认不输出；restart读取后以文件行号重建。调试ID用单独诊断输出 |
| Q37 | unwrapped position如何跨迁移维护？ | GPUMD Dump_XYZ构造时复制wrapped位置，Integrator更新独立array；restart不保存它 | 定义image counter和多段run lifecycle；跨restart不能恢复旧unwrapped history，需与reference测试 |
| Q38 | group dump的global顺序/size | GPUMD `cpu_contents`按原数组扫描；MPI需要跨rank过滤 | 按global ID排序后过滤label；全局group size归约，禁止rank局部ID解释 |
| Q39 | error传播策略 | GPUMD到处`exit(1)`；MPI单rankexit会让其他rank挂在collective | typed error先广播/allreduce；统一打印rank0上下文后`MPI_Abort`。精确退出码需测试 |
| Q40 | CUDA-aware MPI与CUDA graph/stream | replicated runtime 已实现同步的 blocking collectives | 固定 Open MPI+UCX；保留显式 CUDA synchronize、MPIX query、四类数值自检和 HostStaged fallback；未来引入非默认 stream/graph 时重新审计 event 协议 |

## 6. 物理/输出语义待确认

| ID | 问题 | 原因 |
| --- | --- | --- |
| Q41 | per-atom virial归属是否对所有NEP4/5/ZBL fixture一致 | large path按center写，small path把pair virial atomic到neighbor；输出总量可同而per-atom分配不同 |
| Q42 | `thermo.out` KE在move group存在时与temperature DOF不一致是否需忠实复现 | thermo kernel减fixed和move，dump KE只减fixed。fix/move非MVP但未来必须决定兼容bug |
| Q43 | XYZ charge单位和普通NEP语义 | 代码只搬model float，未提供本地单位声明；需要manual或测试/权威说明，当前UNKNOWN |
| Q44 | `global_time`跨restart语义 | restart不保存time，新GPUMD作业从0开始；DMG是否另存time会改变XYZ可见语义 |
| Q45 | 自适应 `time_step DT MAX_DISTANCE` 是否MVP | 需要全局最大速度reduce，且每段结束max_distance变0的行为古怪 | 第一MVP可parse-only unsupported；若宣称time_step完整支持，必须实现并锁定多段状态 |

## 7. 建议立即确认的维护者决策

在开始生产实现前，只需先确认以下最小集合：

1. GPLv3源码复用方式；
2. reference commit是否固定；
3. 第一切口为“正交全周期large-box、单NEP4、显式velocity、NVE、ELL”；
4. 保守两跳position halo作为正确性oracle；
5. rank0 parse/broadcast和64-bit input-row global ID；
6. 非范围命令/box/small-box一律明确unsupported；
7. `time_step 0`作为reference已接受行为保留；
8. 第一个切口不承诺random velocity、fix、correct_velocity、NVT和restart，随后逐项加入。

这些决策不会要求修改GPUMD，也不要求一次性实现完整MPI架构；它们只是防止首个切口同时引入过多不可分辨变量。

开发者确认：

1. GPLv3源码可以直接复用，先不考虑这方面问题
2. 固定reference commit，不考虑宽版本兼容

其余均确认同意提议
