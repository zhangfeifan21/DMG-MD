# GPUMD、LAMMPS、DMG-MD 横向对照标准

类别：现行测试标准。状态：**ACTIVE**（静态物理量与 benchmark 编排）；固定 CPU/NUMA
绑定下的正式性能验收仍属于 P0 计划。

## 比较对象与等价输入

三软件使用同一物理体系、NEP 模型、原子元素与顺序、质量、初始坐标和速度、周期盒、
积分系综与物理时间步。GPUMD 和 DMG-MD 继续读取 GPUMD 格式；LAMMPS 从同一
`model.xyz` 生成 `data.lmp`，以 `pair_style matpl/nep/kk` 运行。`units metal`
下时间为 ps、速度为 Å/ps，所以 0.1 fs 写为 `timestep 0.0001`，输入速度
Å/fs 乘 1000；邻域 skin 显式设为 1 Å。原子 ID 等于模型行号，元素类型按势文件头顺序。

**跨软件通过条件是物理量等价，不是文件内容或 SHA-256 相同。** 允许有可证明等价的
输入格式转换；转换器须保存原势、LAMMPS 所用势、变换规则与各自哈希供追溯。
当前 MatPL 不识别逐元素 NEP `cutoff` 行，转换器仅在所有元素的径向 cutoff 完全相同、
且所有角向 cutoff 完全相同时，将它折叠成统一格式；任一值不同便报错，不选择
近似半径。`dense_water_typewise_cutoff` 正好满足此条件，其两种写法对 O–O、O–H、
H–H 的径向和角向截断均分别为 6 Å 与 4 Å。此 case 的 GPUMD/DMG-MD 运行仍用
原 typewise 文件，LAMMPS 用归一化文件；结果可用于物理等价测试，但不能证明
MatPL 支持 typewise 语法。typewise ZBL cutoff 是独立功能，不受本转换影响。

转换前后比较结构字段、单位和逐原子数值；任何无法证明等价的转换、势加载失败或
pair style 回退都使该 case 不可用于三软件速度比。文件哈希用来追溯输入版本，
不作为三软件横向通过条件。现有 DMG-MD 对 GPUMD 的 committed golden 兼容性回归
仍独立执行，其字节结构要求不转嫁到 LAMMPS。

## 物理正确性与误差

先在相同初态比较原子数、元素、坐标、速度、总势能/每原子势能、每原子力与 virial；
再比较短程 NVE 的有限值、能量趋势及长程守恒/统计量。跨引擎浮点精度和归约顺序
允许误差，门槛以 `tests/lammps_reference/manifest.json` 为唯一机器可读事实源，
由实测误差和所选 MatPL 推理精度校准，不能为了单个失败任意放宽。当前静态门槛的
验证入口是 `tests/lammps_reference/compare_static.py`；长程 NVE/NVT 的三引擎正式
统计门槛仍须在后续 profile 验收中建立。温度可能因自由度定义不同而有小差异，
应同时检查动能，不以原始温度列字节相等作判定。

LAMMPS 的 `compute stress/atom NULL virial` 六列顺序为 xx、yy、zz、xy、xz、yz，
符号与 virial 相反，`metal` 单位为 bar·Å³。转换到本项目逐原子 virial（eV）时
乘 `-1/1602176.5`；该因子取自锁定 LAMMPS 的 `src/update.cpp`。比较器按原子 ID
与 GPUMD/DMG-MD 的输入行顺序对齐后再比较，不能用未排序的 MPI dump 直接逐行比较。

## 性能比较

只有相应物理 case 通过正确性门槛后才作正式性能比较。三者共享同一 case、seed、
GPU 集合、预热步数、正式步数与重复方案；GPUMD 是单进程 P GPU，LAMMPS 和
DMG-MD 是 P MPI rank、每 rank 一 GPU。LAMMPS 启动包装器给每个 rank 唯一 GPU UUID，
再以 `-k on g 1 -sf kk` 运行。三者的实际计时分别采用 GPUMD 第二段
`Time used for this run`、LAMMPS 第二段 `Loop time` 和 DMG-MD 第二段
`DMGMD_TIMING seconds_max`；它们代表各自完整 MD run 段，边界并非逐指令相同，
不能解释为 NEP kernel 微基准。

正式速度比还需固定且核验每 rank CPU 核数、NUMA 放置、独占节点、相同 GPU 设备、
重复波动及编译精度，并保留原始日志与二进制/源码身份。当前 benchmark runner 已能
编排三引擎，但固定 CPU/NUMA 接口尚未实现，因此现阶段 smoke/pilot 只验证流程，
不得将其数值作为正式效率结论。精确 case/profile、步数和重复次数以
`tests/benchmark/manifest.json` 为准。

外部单位和张量语义依据：[GPUMD typewise cutoff](https://gpumd.org/dev/nep/input_parameters/cutoff.html)、
[LAMMPS `units metal`](https://docs.lammps.org/units.html)、
[LAMMPS `compute stress/atom`](https://docs.lammps.org/compute_stress_atom.html)。
