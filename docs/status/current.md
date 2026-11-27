# DMG-MD 当前状态

## 当前能力与限制

V1.0 尚未发布。当前实现包含 NEP 局部域分解和实验局部 NPT。含 NPT 的作业默认使用全量复制，局部 `affine` 或 `rebuild` 需要显式开启。多卡命令和进程数本身，不能证明各卡只计算局部原子的力。[如何检查日志](../reference/multi-gpu-implementation.md#怎样判断一次作业是否真正分了区)

生产分派、三材料 NPT 的正式性能矩阵和完整 V1.0 发布仍待完成。首版逐项报告同卡数普通 GPUMD、同卡数 LAMMPS/KOKKOS/NEP 和固定单卡 GPUMD-WPE，并列出 DMG-MD 自身强扩展。科学验收独立判定，性能报告如实记录结果。长程 NVE 数值回归仍需协调参考身份。NVE 计时可直接使用站点安装的引擎。[L20 已测版本](../standards/cross-engine-reference.md#benchmark-engines) · [对标矩阵与可比条件](performance/npt-benchmarks.md#performance-matrix) · [后续任务](../plans/dmg-md-v1.0-progress.md)

2026-11-27，工单 01 已交付百万原子碳、水和铜的共同起点。三种材料均通过独立留出帧的四引擎静态终验及新目录短程重放，可供工单 09 和后续计时使用。[输入与校验方法](../../tests/npt/public_inputs/README.md) · [终验结果](validation/npt-validation.md#public-inputs-20261127)

2026-11-27，工单 03 完成显式局部 NPT 的双卡和四卡验收。过窄初始盒在首份科学输出前退出，积分中收缩失格时保留最后有效步。1、2、4 卡间六种方向的重启目标通过 64/64 个。[验证结果与复跑入口](validation/domain-npt-validation.md#local-box-restart-20261127)

<a id="l20-v1-audit-20261120"></a>
## 2026-11-20 单节点核查

代码基线为 `0e8360f`。当日工作树另含 GPUMD 5.9.1 短程 golden 和 17 位有效数字的 restart 输出修改。下表各结论只覆盖对应构建、体系和配置。

| 检查 | 已验证的范围 |
| --- | --- |
| 单节点 4 × NVIDIA L20 回归 | CTest 44/44；普通 GPUMD 与 DMG-MD 短程 golden 各 4/4；MPI 域分解主矩阵 68/68。NHC/BDP 覆盖短程、错误输入和 24 个新作业重启。[短程基线](validation/baseline-results.md) · [恒温器](validation/thermostat-validation.md#l20-thermostats-20261120) |
| 两套 NPT 历史统计核对 | 当日核对了 2026-11-04 的 G-B（216 原子碳全量复制）和 2026-11-16 的 DN4（5,760 原子碳指定配置）。后者含 152 条链和 151 个注册区间，未在 L20 重跑或重算。两项分别通过各自范围，均限于单节点、300 K、0 GPa、各向同性正交全周期盒。[G-B](validation/npt-validation.md#npt-scr-confirmation-20261104) · [DN4](validation/domain-npt-validation.md#dn4-final-20261116) |
| 碳、水和铜短程 | 各材料分别比较单卡与四卡局部的 SCR、Berendsen，六组逐步 thermo 对照通过。额外的四卡默认输入使用全量复制。[条件与结果](performance/npt-benchmarks.md#目前有哪些实测) |
| GPUMD-WPE 单卡 | 官方 v1.0.0 在三个模型和两种控压器上完成短程运行；另完成三个相同快照的逐原子量对照。[引擎身份和最大绝对差](performance/npt-benchmarks.md#gpumd-wpe-基线的实机检查) |
| 原生构建 | 从 `0e8360f` 干净副本完成 Release/sm_89 CMake 构建，44 项 CTest、单卡和四卡环境探针通过。单卡碳示例写出 7 行科学数据，以及轨迹和 restart。[复现步骤](../operations/deployment.md) |
