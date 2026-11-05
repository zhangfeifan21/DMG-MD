# DMG-MD v1.0 开发进度

更新日期：2026-11-05（Asia/Shanghai）
流程来源：[DMG-MDv1.0开发流程](dmg-md-v1.0-development-workflow.md)  
状态：**IN PROGRESS：A0–A6/G-A、B0–B4/G-B 已验收；C/D 尚未启动，v1.0 尚未发布**

## 当前 Step

**最近完成验收 Step：B4，G-B PASS（2026-11-04）。** n3 上160条全新独立确认链完整结束，唯一一次冻结分析在19:39（Asia/Shanghai）完成并判PASS。Primary与MPI bridge的均值、variance、体积CDF及ESS门均通过；源码/reference与执行身份审计、独立科学复核、分析后6项CPU测试和最终文档整合完成。原18 looks仍为INCONCLUSIVE；旧确认曾有63条completed、6条interrupted、91条planned；仅保留这些历史计数摘要，未并入新数据。证据及执行身份按 2026-11-05 文档治理规则保留，过程快照与逐作业记录未保留；见[NPT/G-B最终报告](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)、[新结果索引](../status/validation/npt-scr-n3-confirmation-index.json)和[NPT/G-B矩阵](../status/validation/npt-validation.md#npt-matrix-20261104)。
维护者已确认 G0-D1–G0-D10 全部默认建议；此前 A3–A6 授权已落实。NHC 独立冻结后完成统计，
BDP 标量小块由独立 worktree 实施，统筹者审查整合并执行最终矩阵。C 阶段尚未启动。
额外 harmonic K PASS、轴向 mean INCONCLUSIVE 保留，不作普遍遍历性保证。
2026-11-03 按维护者要求完成[同模型跨引擎对照](../status/validation/thermostat-validation.md#thermostat-reference-controls-20261103)：
原初轮及 NHC 2M 确认预算下，GPUMD 源码/原生 GPU 测试接口、原生 LAMMPS 与 DMG-MD
均为 K PASS、三轴 INCONCLUSIVE；未出现任一对照完整通过而 DMG-MD 未通过。
完整 harmonic 门槛仍未通过，未改生产算法、seed 或容差；补充对照不启动 B/C。

## Revision 与工作区

- 当前迁移 revision：`80c57680664d5e8b4d355946907222da009177ed`；B0–B4 的既有未提交修改与 checkpoint 完全核对后保留。本次迁移没有修改生产源码，也未提交、未推送。下方 A 阶段工作区记录属于当时的历史快照。
- A1 实施前工作区已有用户文档修改；代理于 22:14 刷新快照后才编辑 A1 文件。统筹者在根文档状态更新前独立比较刷新快照与 A1 执行后状态，确认原有 14 个 tracked 文档和 2 个 untracked A0/进度文件哈希均未改变。随后仅在主计划与 `docs/README.md` 增补 A1 验收状态和索引，未覆盖其余用户修改。
- A3 开工前 dirty workspace 共 21 个 tracked、7 个 untracked 文件；完整冻结清单在 `/tmp/dmgmd-a3-a6-20261102/initial.json`。A2 代理相对其开工快照仅改动两份 CMake 和两处 runtime，并新增控制器头/实现、单测和验收记录；本轮在此基础上叠加 A3–A6 改动，其他用户修改保留。所有内容未提交、未推送。
- A1 只修改 5 个批准的源码/测试文件；未改变仓库 `build/`、A0 构建、golden、容差或 reference。独立构建与原始证据在 `/tmp/dmgmd-a1-20261030/`；A0 原始证据在 `/tmp/dmgmd-a0-20261030/`。A2 独立构建与封存证据在 `/tmp/dmgmd-a2-20261030/`。

## 已完成证据

| Step | 状态 | 验收证据 |
| --- | --- | --- |
| A0 | DONE | [A0 基线冻结记录](../status/validation/ensemble-baseline.md#ensemble-baseline-20261030)：当前源码独立 Release 构建；宿主机 CTest 19/19；candidate 和锁定 GPUMD baseline 各 4/4；long-NVE smoke 7/7 配置、70 stage；P1/P2/P4 CudaAware 环境门通过。统筹者复算 808 个封存文件哈希及 18 个代表输出哈希，均匹配。 |
| A1 | DONE | [A1 parser/IR 验收](../status/validation/ensemble-infrastructure.md#ensemble-parser-20261030)：独立 Release 构建；parser 113 案例；宿主机 CTest 19/19、沙箱 18/19（CUDA 无设备 skip）；A0/A1 旧 NVE/`nvt_ber` 对照 36/36；113 案例与 247 个封存文件哈希由统筹者复核一致。NHC/BDP parser 可接受，动力学明确 fail closed；NPT 仍 unsupported。 |
| A2 | DONE | [A2 共享系综控制器/Berendsen 验收](../status/validation/ensemble-infrastructure.md#ensemble-control-20261102)：Release 构建；focused 128 controller cases；宿主机 CTest 20/20、0 skip；A1/A2 golden 20 对文件 SHA 相同；全量复制模式/局部域分解模式 A/B 共 50 stage、181 个科学文件、231 条全局和 160 条 rank 逐步记录完全一致。统筹者独立重算输入/输出哈希与通信账本，并核验 1123 项封存文件；A1 420 个 build 文件及既有用户文档哈希不变。NHC/BDP 动力学仍未实现、运行时 fail closed。 |
| A3 | DONE | [NHC 验收](../status/validation/thermostat-validation.md#nhc-20261102)：SY7×RESPA4、四节链独立逐位 oracle；P1 GPUMD 200-step 对照；11 组 NEP KE（含 P2/P4 双后端与 half-dt）及 8 seed×40k 的三个速度方向通过。整合候选对冻结 A3 的 30 stage、120 科学文件 SHA 全同；额外 harmonic 总 K PASS、轴向均值 INCONCLUSIVE 单列，不作普遍遍历性承诺。 |
| A4 | DONE | [BDP 验收](../status/validation/thermostat-validation.md#bdp-20261102)：16组SVR逐位oracle、19项seed合同、root单一RNG/实例cache、12组八seed KE（含GPUMD与half-dt）及实际NEP三轴速度矩通过；独立harmonic K通过，额外轴向INCONCLUSIVE保留。 |
| A5 | DONE | [双路径矩阵](../status/validation/thermostat-validation.md#thermostat-matrix-20261102)：52短程配置/重复、54新作业restart、两种协议矩阵与负例通过；host CTest23/23；旧NVE/Berendsen对A2的50stage、181科学文件、231全局/160rank记录完全相同；long-NVE smoke七配置70stage通过。 |
| A6 | DONE | [文档核对](../status/validation/thermostat-validation.md#thermostat-docs-20261102)：系综/兼容矩阵/MPI/thermo/测试合同、README/状态/索引/本计划同步，CLI与本地链接检查通过；完成步骤细节迁入标准与验收记录。 |
| B0 | DONE | [压力 oracle](../status/validation/npt-validation.md#pressure-oracle-20261103)：生产 writer、GPUMD 六应力列/virial 布局、解析压力、压缩/扩张和剪切检查通过；范围限冻结 NEP 单 rank fixture。 |
| B1 | DONE | [全量复制模式盒合同](../status/validation/npt-validation.md#box-contract-20261103)：preflight/mutation 原子性、double/float inverse、缩放位置、ownership hash、neighbor 通知及缓存刷新测试通过；不接入 局部域分解模式。 |
| B2 | DONE | [NPT Berendsen](../status/validation/npt-validation.md#npt-ber-20261103)：GPUMD 200-step golden、P1/P2/P4 全量复制模式双后端路由、负压力、B>2000、错误门与 restart 分类通过；不证明严格 NPT 采样。 |
| B3 | DONE / PASS | [n3最终独立确认](../status/validation/npt-validation.md#npt-scr-confirmation-20261104)：160条全新完整链，固定20,000次bootstrap及唯一最终look；P1 base/half和P2/P4双后端bridge全部通过。原18 looks的INCONCLUSIVE保留，旧中断确认不混入。 |
| B4 / G-B | DONE / PASS | [NPT/G-B矩阵](../status/validation/npt-validation.md#npt-matrix-20261104)：既有[输出/restart/负例短合同](../status/validation/npt-validation.md#npt-output-20261103)与[旧路径回归](../status/validation/npt-validation.md#npt-regression-20261103)、最终身份/科学复核、分析后6项CPU测试及证据/文档整合通过；限定范围由root验收。 |

性能 trial 未执行，按 A0 任务卡允许项记录 `PENDING-D4`，不阻塞 A0；状态和受控测量计划见 A0 记录。

Gate 0：维护者已确认 G0-D1–G0-D10 的全部默认建议，无例外调整；批准日期为 2026-10-30。

## 当前状态

- NHC/BDP 已在 全量复制模式 P1/P2/P4、局部域分解模式 P2/P4 与双后端所列矩阵通过；单位、root所有权、分段重置、seed和输出时点已固定。`npt_ber`和`npt_scr`已通过所列全量复制模式/单节点矩阵，G-B PASS；SCR统计限216原子NEP-C，NEP5/ZBL仅短合同，非NEP未实现。
- NHC实际NEP速度分量8seed×40k全部通过；BDP同样通过。额外harmonic两种恒温器的K矩通过、轴向mean固定预算内INCONCLUSIVE；原非通过记录保留，未放宽阈值。
- A阶段的A3与当时最终候选分别位于 `/tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md`、`build-a4/dmg-md`；最终SHA为 `0a51d5add72a454bab2dc5cd4bd19733abe1fb610365c7f9359d8b7f48549ff7`，仓库原build/A2/三个reference保持不变。
- 初始253文件与用户修改的保护、15,461个reference文件校验、执行命令/输入输出哈希和封存索引见同目录 `final-ownership.json`、`evidence-manifest.json`。未提交、未推送；性能门PENDING-D4，跨节点及release长程未执行。

## 当前下一步

**本次 B0–B4 授权工作已完成。** C/D仍为计划中的后续阶段，未在本轮启动；性能门PENDING-D4、跨节点与release长程仍未放行。NPT支持限已验证全量复制模式范围，不外推到局部域分解模式 NPT、非NEP或普遍严格采样。
