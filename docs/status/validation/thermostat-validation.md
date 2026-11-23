# 恒温器数值验收

<a id="nhc-20261102"></a>
## 2026-11-02 NHC

四节 NHC 链使用 SY7 × RESPA4，与独立标量实现逐位对照。P1 与 GPUMD 的 200 步短程和 11 组 NEP 总动能检查通过；总动能矩阵覆盖指定 P2/P4、两种 MPI 后端及半步长配置。

另用八个种子检查三个速度方向矩，在 P1/HostStaged 下通过，历史 NHC 方向矩运行未指定半步长。第一轮分析器曾误读 `block_length_frames` 字段而失败，修正后只读重分析原数据，再按原预算延长。工具失败与后续科学结论分别保留。

同日另做了独立 CPU 可解谐振子诊断。旧 block-only 确认曾给出 y 方向 FAIL；计入种子间离散后，区间扩大，判定为 INCONCLUSIVE。两份记录分别保留，整个附加谐振子诊断未获 PASS。这与实际 NEP 的速度方向矩分别判断。

<a id="bdp-20261102"></a>
## 2026-11-02 BDP

16 组随机速度重标定对照、19 项种子规则和 12 组八种子动能检查通过，包含 GPUMD 和半步长参考。根进程持有随机状态，实际 NEP 的速度分量矩在登记范围内通过。

<a id="thermostat-matrix-20261102"></a>
<a id="thermostat-docs-20261102"></a>
## 双路径与重启

A5 完成 52 项短程配置与重复、54 个新作业 restart，以及错误输入检查。与 A2 的 50 段、181 个 NVE/Berendsen 科学文件一致。A6 核对了输入、输出、热力学反馈和 restart 文档与运行时。

NHC/BDP 读取 restart 时重新初始化隐藏状态，不能据新作业快照重启声称轨迹连续。[系综标准](../../standards/ensembles.md)

<a id="thermostat-reference-controls-20261103"></a>
## 2026-11-03 谐振子诊断

GPUMD、LAMMPS 和 DMG-MD 在相同模型下均通过总动能矩检查。预定样本量下，轴向均值仍为 INCONCLUSIVE。这一附加物理问题与实际 NEP 已通过的范围分别判断。

<a id="l20-thermostats-20261120"></a>
## 2026-11-20 单节点复验

4 × L20 工作树完成 NHC/BDP 短程、负例和 24 个新作业重启检查。长程统计支持仍限于原来冻结的材料及运行条件。[执行入口](../../../tests/thermostats/README.md)
