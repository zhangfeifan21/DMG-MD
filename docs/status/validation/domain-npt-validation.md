# 局部域 NPT 验证状态

<a id="dn4-final-20261116"></a>
## 2026-11-16 DN0–DN4 最终验收

[最终只读审核](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v8/final-audit.json)（SHA-256 `e52a56979a6d0afad747814ea23a12ee5e59f8fb1eb8278482233ced8eb7a909`）报告完整性 PASS、科学分类 PASS；[审核执行收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v8/final-audit-execution-receipt.json)记录实际退出 0。唯一分析及原 ledger 对应的 152 条完整链均已核验。151 个注册区间全部生成并通过：14 个并行比较、3 个步长比较、16 个独立响应条件、32 个 SCR 温度/压力绝对均值和 3 个 NVT 温度条件。通过依据是冻结的区间及精度规则，不是点估计。

统计支持严格限于单节点 NEP4-C、5760 原子、300 K/0 GPa、各向同性正交全周期体系：base 为 P1/HostStaged/off 与 P2/P4 的 HostStaged/CudaAware 各自 off、rebuild、affine；half 仅 P1/HostStaged/off、P2/HostStaged/affine、P4/CudaAware/affine。P1 为全量复制；P2/P4 局部样本要求真实局部路由。与 216 原子全量复制 G-B 是不同的冻结结果；NEP5/ZBL 的短程证据不构成统计支持。默认仍 off，DN5 性能与采用及跨节点仍未验收。

[独立结果解释](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v8/independent-result-interpretation.json)中跨 seed 聚合均值 ESS 最低为 1035.814；单 seed 最低 93.564 是不同口径。SCR 温度和压力目标最大点偏差分别为 0.008094 K 和 0.008599 GPa，仅作描述。区间采用注册的近似同时覆盖、块与 bootstrap 假设；不证明普遍严格采样或遍历性。

唯一分析的精确 argv 和实际 bootstrap argv 见[分析启动记录](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v8/analysis-start.json)，[结果](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v8/analysis.json) SHA-256 `0db29a9e5d176744fe844625705c937d920d3ab080971724fc08f5ab6617c36e`，实际子进程 wait 结果见[退出记录](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v8/analysis-exit.json)。最终审核的精确 argv、环境入口与源码哈希见上述审核执行收据。原代码基线为 `d1b933c05c857e536ffb0e71e2212bc02939f5bc` 加未提交修改，冻结候选 SHA-256 `5a460ad845597f62623e33657abd7e1a81af6b94e908b98a5768ae5e5b88158e`；构建身份和运行环境见下方 2026-11-06 记录。

原第 143 条 wrapper/旧 driver 外层退出码未观察；用户授权删除的第 144/148 条失败输出不再可重读，删除时哈希由原收据和日志绑定。最终分析 launcher 实际记录子进程退出 0，其外层 OS 退出码未观察，原工具 session 12703 已消失；不补造。原停止、OOM、授权恢复及旧参考结论均保留在下文历史记录。审核未重算估计量、ESS 或 bootstrap。

[30 项需求完成审查](../../../artifacts/domain-npt/final-v1/requirements-completion-final.json)记录实现和测试范围全部 PROVEN；[文档收口复核](../../../artifacts/domain-npt/final-v1/documentation-closure-review.json)记录其后的文档检查，最终目标审核由 root 另行完成。以下 2026-11-06 起的状态、PENDING 和运行描述均为各自时间点历史快照。


2026-11-06：DN0–DN4 的实验实现和验收工具已集成，验收仍在进行。确定性合同及矩阵见 [标准](../../standards/domain-npt.md) 和 [manifest](../../../tests/npt/domain-manifest.json)。已执行的结果列于下文；未完成的门不得视为通过。现有 SCR 统计支持范围不变。

CPU 几何诊断包含固定种子 typewise 两跳枚举、周期像、单 ULP cutoff、负预算、周期 cell 边界、整数溢出和扩张时小 cutoff 的反例。生产舍入条件另见 [实现级证明](../../standards/domain-npt-rounding-proof.md)，不能由理想几何枚举替代。`prepare_domain_npt_fixture.py` 已生成并哈希 5760 原子短程 fixture。

统计执行和一次最终分析管线已实现：输入/可执行文件、ACF helper、链接库、环境与 RNG/完整确定性矩阵及原始文件绑定；缺失或更改记录将拒绝。大盒 [注册](../../../tests/npt/domain-statistics-registration.json) 已批准并冻结，正式采样已启动；尚无最终科学结果。CPU 测试包括小规模合成编排（科学比较刻意 stub）、独立有限温响应数学例子、共同 T/P 偏移、错误体积方差和证据篡改负例，不是科学门通过。正式门包括并行与步长等价、各条件对 300 K/0 GPa 的绝对检验、独立 NVT 响应温度及体积均值/涨落，共预注册 151 项置信区间校正。

## 2026-11-06 实施与候选验收进展

代码基线 `d1b933c05c857e536ffb0e71e2212bc02939f5bc` 加本任务未提交修改。
最终候选暂存于 `artifacts/domain-npt/final-v1/candidate/dmg-md`，SHA-256
`5a460ad845597f62623e33657abd7e1a81af6b94e908b98a5768ae5e5b88158e`；同目录保存
build receipt、46 个生产源码/头文件构建身份及编译命令。环境为 n3 单节点 RTX 4090，
CUDA 12.8、Open MPI 5.0.10、UCX 1.22.0，设备顺序 7,0,2,6；每个 shell 使用
`source ../env/md-mpi.sh`。共享设备仅作正确性验证，不据此声明性能。

实际构建命令为 `cmake -S . -B build-dn -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=89`
和 `cmake --build build-dn -j 6`，未启用 fast-math。最终候选环境自检、40 项 CTest
（无 skip）、4 组 P1 golden、P2/P4 owned preflight 故障测试、P1/P2/P4 SCR 固定标量流均通过。
随后补入 CTest 的缓存 runner、证据完整性及串行批处理测试也通过，当时总注册数为 43；
当前十项局部 NPT CPU CTest 已整体重跑通过。候选及生产源码哈希未变，测试注册与
后续扩展分别记录在 `candidate/test-registration-followup.json` 和
`candidate/test-registration-followup-2.json`。
精确命令与原始日志保存在 `artifacts/domain-npt/final-v1/` 各 runner 报告及 `logs/`。
统计 ACF helper 已按实际编译/链接命令独立重建，新旧二进制及目标文件哈希均一致；
源码、工具链、命令与构建日志身份保存在 `acf-helper-build-audit/receipt.json`。
正式采样前的文件身份与磁盘容量审查也已完成，记录在 `formal-preflight-review.json`；
这些运维检查不替代尚未执行的科学统计门。

逐项需求审计后，补入仅删除一个 dependency 边就必须令完整两跳 oracle 失败的断言，
几何 CPU 测试 9/9 通过；变盒 manager 重放器 CPU 测试 6/6 通过，现 CTest 总注册数为 44，
记录在 `candidate/test-registration-followup-3.json`。真实 thermo ghost 填毒与 uint32 OR
单 rank 失败测试已完成 arch89 Release 编译，6+4 个 GPU/MPI 配置全部通过；
实际变盒下的微跨界、多 slab/PBC、反复迁移及无迁移重建矩阵已在 GPU 执行前冻结，
72 个配置全部通过。代码、构建及输入审批分别位于 `thermo-preflight-followup/` 和
`migration-approval.json`，结果为 `thermo-preflight-followup/runs/results.json` 和
`migration-npt/results.json`。迁移 oracle 根据完整转移日志和各 rank 计数重放唯一 manager map，
并与同 P 的 off 输出对照；它不直接读取 device gid 数组。

最终候选 NEP-C 5760 原子 Berendsen 和 SCR 各 12 组短程对照均通过，覆盖 P2/P4、
HostStaged/CudaAware 及 off/rebuild/affine；局部配置均验证了实际 `m2a` 和 NPT 路径标记。
多段、旧力、NPT/NVE/NVT 切换、late-unwrapped 和跨 rank restart 共 180 项通过。
双后端负测各 11 项通过，包括初始回退、候选几何失败、设置不一致、多盒位移与空 rank。
负测初次失败来自 harness 错误要求 `axis_length` 字段；实际 runtime 已输出三轴盒长、
axis、slab_width 和 required 并一致终止。修正 harness 后完整重跑，初次报告保存在
`negatives-{HostStaged,CudaAware}-initial-harness-fail/`，没有修改生产候选。
NEP5、typewise、ZBL、flexible ZBL 和 typewise ZBL 的 Berendsen/SCR 短程变体矩阵
各 60 项通过。这些人工构型只验证代数与运行合同，不扩展 SCR 科学统计支持。
36 项形变缓存矩阵通过：P2/P4 双后端在弱耦合和扩张场景均为 20 次复用、0 次重建；
强压缩场景均为 18 次复用、2 次 affine budget 重建，且与 off/rebuild 逐字段对照通过。
PMPI 的双系综、双后端、P2/P4、rebuild/affine 共 16 组均通过；4 步与 6 步的差额
证明每个持续变盒步只有一次 8-double thermo SUM、两次 uint32 OR，以及 BER 一次或
SCR 两次 2-double 广播。其他 Allreduce 与 gather/allgather 增量均为零。
原始结果经全量深验生成 `proofs/rng-proof.json`（SHA-256
`4b2d889c6cb4d664f0da4c81a1638e0c494d106808ae064bcf2c531611d40931`）和
`proofs/deterministic-proof.json`（SHA-256
`4146430241c232fcc02beb74d82b4b0c48976dab262cd9f14e4480cd42c13d8e`）。

既有 halo lifecycle 68 项、完整默认 MPI domain 矩阵及 replicated migration 双后端回归
均通过。既有 replicated BER 完整短程矩阵通过，SCR short 为 23 PASS 与 1 项预期失败。
旧 BER harness 对两个初始盒错误仍期待旧的 `npt_ber` 前缀，而 HEAD 已使用共享 `NPT`
前缀；仅修正这两条断言并保留具体原因，完整重跑后通过，初次证据另存。

原锁定 GPUMD 可执行文件在 n3 驱动上报 PTX error 222。既有隔离 CUDA 12.8 参考用于
短程回归时保留了其历史身份补充；nightly 另从锁定 source commit 全新隔离构建并保留
全部 285 个源码、归档与构建产物，未改数值源码或 Makefile。新参考 SHA-256 为
`9c1735c0ba8d0150eb2d3fef28d57afbae6cadf6d556a9b718a4fc04f30cc1bd`，凭据在
`reference-cuda12.8-fresh/provenance.json`。其四个 baseline 和 216/2016 原子 BER golden
资格均通过；nightly 使用显式且可核验的参考身份入口，原 manifest 与容差保持不变，
42 个配置及 12 组 restart 全部通过。独立只读核查在
`final-evidence-audit.json` 中确认全部 572 个阶段记录（candidate 336、reference 236）、
输入/输出哈希、实际路由、NVE/NVT/restart 数值摘要及上述 10+72 项补测通过。
其 nightly 数值摘要和直方图未从大轨迹重新计算；逐字段差分仍以已执行原 runner 的
完整检查及输出哈希为依据。外部三个 reference checkout 均保持只读。

需求逐项复核确认，默认 MPI 与 nightly 的 NVT 用例覆盖 `nvt_ber`；最终候选另以原
fixture、seed 和容差完成 `nvt_nhc`/`nvt_bdp` 六套回归，串行队列终端退出码均为 0。
短程及负例报告有 142 条记录，其中 22 个短程配置、42 个负例和 42 个比较；两种系综的
协议各 12 配置，BDP seed 合同 19 项，NEP5/ZBL 变体 30 配置和 67 项比较，新作业
restart 为 52 个同 rank 加两个跨 rank 检查。参考入口使用显式 provenance 校验；旧锁定
参考默认身份和数值门保持不变。独立只读审核 [v2 结果](../../../artifacts/domain-npt/final-v1/thermostat-regression-audit-v2.json)
为 PASS（SHA-256 `8984b20bb891704241f8a132b661ddd574e875f19d548150befb86a4ba31c7c9`）。

首次只读审核将来源数静态估为 50、检查数估为 52，因而报告 FAIL；逐项核对后发现 NEP5
P1 的 `domain` 和 `fallback` 是不同来源签名，虽然两者实际均走 `m1-fallback`。实际为
52 个来源、54 项检查；[纠错收据](../../../artifacts/domain-npt/final-v1/thermostat-restart-count-correction.json)
及[首次 FAIL 报告](../../../artifacts/domain-npt/final-v1/thermostat-regression-audit.json)均保留，
仅修正静态计数，未改变输入、命令或数值门。原 short/protocol runner 未逐作业哈希 stdout/stderr；
复审重新读取路由和协议日志并核对队列级日志哈希，但未重做完整非精确轨迹比较。P1→P4
restart 的原 runner 选源通常是 fallback，该单项不证明局部跨 rank 恢复。需求覆盖表现为
29 项 PROVEN、1 项 PENDING；剩余正式统计科学门仍在运行，尚未通过。

先前 DN1 和 DN2 候选的 NEP-C 5760 原子 BER/SCR 短程各 12 作业通过；DN2 弱耦合
20 步实际 cache_reuses=20、cache_rebuilds=0，P2/P4 双后端一致。旧 DN2 的多段/跨 rank
restart 矩阵 148 作业通过。旧候选证据保留在 `tests/npt/artifacts/domain-dn/` 和
`artifacts/domain-npt/contracts-dn2/`，二进制身份不同，不能替代最终候选的正式门。

最终候选压力 oracle 最初采用理想周期晶格时的 P1 前门为 FAIL：5 点各两次重复及压力方向通过，
原定 log-scale 步长 0.001/0.0005 的体积导数误差分别约 0.0152/0.0645 GPa，
大于预设约 0.0100 GPa 的界。原始记录保留在 `final-v1/pressure-reference/`。
后续仅从 P1 差分步长和数值分辨率诊断选择新参考构型，没有将原失败改成通过或放宽门。
四个更大步长的 P1 重复扫描已完成，仍无稳定收敛区间。另预先固定 seed=20261106、
逐坐标 ±0.01 Å 扰动的参考构型，以打破理想晶格的等价原子环境；在 log-scale 中心
0、±0.008 各检验原步长 0.001/0.0005，保留原容差。该独立 P1 检验 30/30 通过，
近零中心误差约 0.0000931/0.0008041 GPa，全部六项最大误差约 0.0016334 GPa。
该结果支持采用新快照方法，不构成对原理想晶格浮点误差的严格上界。方法与身份记录见
[压力差分注册](../../../tests/npt/domain-pressure-oracle.md)。完整压力矩阵现已 150/150 通过；
P1 与八个局部配置各自的六项体积导数、方向和逐快照字段对照全部通过。独立离线审计
重新读取 1,350 个文件哈希、150 个解析压力和全部 54 个导数，结论 PASS，报告为
`artifacts/domain-npt/final-v1/pressure-full-audit.json`。最大导数误差为
0.001633415233 GPa（对应门限 0.029742544907 GPa），最接近门限的一项使用其 7.9514%；
直接压力公式最大差异为 2.60e-10 GPa。结果限于已冻结的扰动构型和完整矩阵。
正式大盒 SCR 批处理已启动，但独立有限温 NVT 响应和 SCR 多种子链的最终分析尚未执行；
科学门仍为 PENDING，未通过范围保持未支持。

## 2026-11-06 正式统计启动快照

2026-11-06 17:20（Asia/Shanghai）核对时，原 DRAFT 与批准提案均已保留，注册与 manifest
仅作批准元数据更新，数值协议未变。批准注册 SHA-256 为
`d0b2e97e5d768d772f14887ec87d9791f51a29ba610b545b30ba45f33cd22801`，
manifest 为 `fc9cc13a5eac211db86362bccaa3af4f4b85e13795bd7c7f9eddb0f8bf4fd67e`，
输入库存为 `5ee032f722a42b5d58e9244600b1a1bed943b6c8eeb54be2a6c4eef2a5d8ef7c`，
唯一执行 [freeze](../../../artifacts/domain-npt/statistics-final-v1/freeze.json) 为
`c58ee10484280be8983d55596d1d96d1b535a53e74b56d9ed0760b684365e176`；
[批处理批准](../../../artifacts/domain-npt/statistics-final-v1/root-batch-approval.json) SHA-256 为
`349365896a672515a4dc11994375b66e2dd2da8b42f162757edc661bb1e9845b`。

按预注册执行 152 条链、合计 28,160,000 步、151 个区间与 200,000 次 bootstrap；全部链
完成后只做一次最终分析，不做中途科学查看。[精确启动命令](../../../artifacts/domain-npt/statistics-final-v1/batch-command.txt)、
[技术进度](../../../artifacts/domain-npt/statistics-final-v1/batch/progress.json)和启动日志均保留。
上述时间点观察为批处理 RUNNING、首作业 `nvt-vol0.99-seed401` RUNNING、`analysis=null`；
这是当时的状态快照，不保证此刻进程仍存活，也不是科学通过。批处理退出后才会生成
`batch.exit-code`；最终分析前不报告采样结果。


## 2026-11-09 正式统计技术停滞快照

2026-11-09 09:41（Asia/Shanghai）诊断快照中，116/152 条链技术完成并记录 PASS；第 117
条 `scr-base-p4-HostStaged-off-seed305` 进程仍存活，但输出停在 130002 个完整换行，文件
mtime 自 11-08 15:17 起无增长。rank 596220 主线程处于 TCP `recvfrom` 等待；连接用途
和停滞根因未知。[诊断摘要](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-stall-117/summary.json)
SHA-256 `08a7dbb0366e3c26185e8dbd6bc23940ae5b66dbe57da7ad777179c96eb026ee`，
[连接归属补充](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-stall-117/socket-attribution-addendum.json)
SHA-256 `2036a6b1822438c0cdd18fced2420070efe27f8b8d374c78d8c38835d23d938c`。
这是当时的技术快照，并非持续存活保证。

10:06（Asia/Shanghai）单次有界 GDB 观察后已 detach，未读取科学变量或数组；rank 596220
主线程调用栈位于 Open MPI TCP 接收与 progress 路径，见 [GDB 收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-stall-117/gdb-bounded/capture-receipt.json)。
[握手审查](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-stall-117/openmpi-tcp-handshake-review.json)
结合版本源码与已安装二进制反汇编，发现新接受的 TCP BTL 连接在未完成 24 字节握手时，
接收超时产生的 `EAGAIN` 会重试而不是形成总截止时间；该机制与当前停滞相符，但未证明
对端身份、实际 errno 历史或这是唯一原因。源码与已安装构建的完整对应关系也未证明。
[EOF 与 RMA 补充](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-stall-117/openmpi-handshake-eof-rma-addendum.json)
说明短读分支及其限制；[超时清理审查](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-stall-117/timeout-review/review.json)
指出独立进程组 rank 不由 launcher 的 `killpg` 直接覆盖。

当时预计原 24 小时 timeout 在 14:45:23（Asia/Shanghai）附近触发。用户随后明确授权技术
恢复例外；原批次已于 10:27 停止，session 89025 退出码为 1，七个原 PID 均已退出。旧进度
为 116 条技术 PASS、第 117 条中断 FAIL、批次 STOPPED、analysis 为 null；不再等待原 timeout。
[停止证据索引](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-stall-117/authorized-stop/evidence-sha256.json)
及[用户授权](../../../artifacts/domain-npt/statistics-final-v1/user-authorized-recovery.json)
（SHA-256 `7ac87a136f0b4863cf170acc9cce8a7738499e464a359816632b26d48c76827b`）保留。

计划保留已完成的 116 条链，以相同输入和 seed 在干净目录完整重跑第 117 条，再完成剩余
35 条；不拼接轨迹、不做科学中途查看。第 117 条第二次完整尝试于 10:44:49 启动，使用相同
candidate、输入、seed 和环境；旧 FAIL claim 与 8 个文件已归档，[归档收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/attempt-1/archive-receipt.json)
SHA-256 `4a80243fc5e7b5412ff5b3cada8e89037f55a6318381393e43f7b4ca5b80393c`。旧 116 条保留，
旧 batch progress 仍为 STOPPED；新尝试状态应以 [restart-start](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/restart-start.json)、
[launch claim](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/restart-launch-claim.json)和进程为准，不能用旧 batch 监视器判断。
第 117 条已 exit 0、技术 PASS；[完成收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/restart-completion-receipt.json)
SHA-256 `4b81c0fa93a10e039ed3a4058c46c2cedc8b1e90d4661bcb679570c34ee1c67b` 记录六个互异身份退出，
[root 技术完整性检查](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/root-chain117-integrity.json)
SHA-256 `d51395877f6eb9ab99972a02d70323921b31ed14377df09df2b439a90afbd6ac` 为 PASS。

剩余 35 条续跑曾启动：以下为 2026-11-10 的历史快照，不代表当前进程存活。2026-11-10 16:01:15（Asia/Shanghai）root 直接核验宿主 driver
PID 1629280（start_ticks 1455570499）及当前进程的身份、父子关系、UID 和可执行文件；
原 session 47962 于 16:01:20 活跃。续跑 25/35 技术 PASS，总计 142/152；第 143 条双 rank
半步长 affine seed307 RUNNING（wrapper PID 1473020，MPI PID 1473209，rank PID
1473228/1473229），输出 9938122 字节，analysis 为 null、stderr 为 0 字节且尚无 exit
receipt。该快照不是持续存活保证，也不是科学 PASS。

第 142 条较慢时，07:24:22–07:24:42 UTC 的只读 schedstat 显示 rank 128169 在 20 秒中
运行 1.227 秒、排队 18.644 秒，CPU 0/96 繁忙。合同审查未发现 CPU 亲和属于冻结参数；
root 审阅并 dry-run 后，于 07:39:17 UTC 将该 rank 六个线程一次性调整到同 node0 的
CPU 12/108，另一 rank 的 1/97 及 driver/MPI 绑定保持原样。07:39:48–07:40:08 UTC 该
rank 运行 19.999 秒、排队 0.000389 秒；原进程、命令、环境、GPU 和采样参数沿用。第
142 条正式 record 技术 PASS、elapsed_seconds 21947.28062943369；在 15:43 UTC+8 快照时第 143 条绑定尚未调整；后续操作如下。
[观察收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain142-observation-receipt.json)
SHA-256 `f446b2d76ab9e7e18f463e3d4a6985285eb7cb43611442ba9d9d3ecead81e627`，[执行收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain142-execution-receipt.json)
SHA-256 `8748595097d2e579ad570355c90cb65b4bc9f8fe25e6d522f1dd33733e09ec07`；
[操作记录](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain142-operations.jsonl)
SHA-256 `7ac1ef1d6737eff6bc1b7def2524f8e4ce30821a250059808b1e5521da7e249e`，
[脚本](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain142.py)
SHA-256 `234b96a4a6b20990e22339d636348d43983b9560c96615b03a5d413ed33bc541`。
观察只说明当时调度竞争缓解，不构成 DN5 性能或科学 PASS。

第 143 条于 07:48:09–07:48:39 UTC 主线程处于 recvfrom 睡眠且输出停在 664754 字节；
07:51:44–07:51:59 UTC 自主恢复运行，输出由 948861 增至 1059327 字节，没有重启、重试、
信号或网络干预。端点未取得，不能认定与第 117 条同因。恢复后 07:53:17–07:53:35 UTC 慢
rank 运行 1.118 秒、排队 16.818 秒，CPU 0/96 繁忙、同 node0 的 12/108 空闲；root 审阅
专用脚本后于 07:57:51 UTC 将该 rank 六个线程一次性由 0/96 调整到 12/108，其他绑定及
原进程、命令、环境、GPU、seed 和步数沿用。07:58:21–07:58:41 UTC 该 rank 运行 19.9992
秒、排队 0.000522 秒，输出由 3543007 增至 4249845 字节。第 142、143 条的调整分别限于
各自已核验 rank，未来新作业不自动继承；这不构成 DN5 性能或科学 PASS。
[143 观察收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain143-observation-receipt.json)
SHA-256 `b4170c47aefac4f1d3230ff5fbf34ab83ba62f75e05ea4f89efc63098eab4bea`，[脚本](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain143.py)
SHA-256 `e010b2a6209e01bb9fb9f365929cdf7a959a1cb5e847ae4575d444d2fc2952b3`，
[意图](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain143-intent.json)
SHA-256 `ae5eb62880bd43f05abafc21c6200b122dccd2b6d339becd0a7921c01a6b2894`，
[操作记录](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain143-operations.jsonl)
SHA-256 `d1d9b49b2557a134d6013a8e92d9ec562af4fe7af12919c45decc869d275020d`。此前自动审批拒绝在补充原目标
及冻结矩阵范围后已解决；[启动上下文](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/continuation-launch-context.json)、
[启动观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/root-continuation-start-observation.json)、
[命令](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/continuation-command.txt)和
[root 续跑批准](../../../artifacts/domain-npt/statistics-final-v1/recovery-continuation-v1/continuation-root-approval.json)保留。

<a id="statistics-recovery-20261111"></a>

## 2026-11-11 多段恢复状态

2026-11-10 08:50:31 UTC（16:50:31 北京时间），第 143 条原始 formal record 和原 ledger
claim 均为技术 PASS，record SHA-256 `7ba84040a4d5cc6c57bf2264611efdb4fac7c0bcd2aee2366a19addf721f14f3`。
旧续跑进度最后的 RUNNING 只是过期快照，旧 wrapper 与 driver 的退出码未观测；不能补写为
exit 0 或推断其 identity_errors。[143 只读检查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v2/inspection/receipt.json)
SHA-256 `418380d85dc3db4f7b9b5aa0c409421d00eb2dc12f0654d1baf2ce3e36eed2a2`，核对了原 record、claim 与输出哈希。

2026-11-11，新恢复段经 root 审阅、批准、宿主预检和 10 项 CPU 测试后单次启动。
[启动命令收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v2/root-launch-command.json)
SHA-256 `c28b4efae6fcbebc8caaedfe58dcd9f887c96e407c35f6e2c9b5e0a1065952ea` 中的 shell exit 0
仅表示启动命令返回，不是运行成功。第 144 条首次尝试报告 CUDA out of memory；其原 record 和 stderr 已按用户要求删除，不留备份。wrapper 和 driver
退出 1；[supervisor 退出收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v2/supervisor-exit.json)
SHA-256 `591e9126026a834e043f7d7e6ebb1e81e7678104451684da04d9be1e0181e7e5` 记录监督器真实 wait 到的
driver returncode 1；该字段不是监督器自身的退出码。监督器随后在宿主观察中已不存在，
其自身 OS 退出码未由外部 wait 观察。
2026-11-11 01:45:40 UTC（09:45:40 北京时间）的历史宿主观察（文件已按用户要求删除）
SHA-256 `07f29afbca8585e24ce30b6bc192a2b1766d3eedb82664e093a1256381ada4a8` 确认记录的六个进程身份均不存在；
[新进度](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v2/progress.json)
SHA-256 `b140d741e95aaf5b5fce51ae6be126905dd02c6c99cd23e6510e49a529549341` 为 STOPPED。
第 145–152 条未尝试；该宿主观察时尚无第 144 条重跑授权，也未进行最终分析或科学中途查看。显存不足是技术失败，
不是科学 FAIL，不能据此认定 DN4 完成。

多段分析适配的[独立静态审查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v2/analysis-adapter-static-review.json)
SHA-256 `b8a8a538c1493afc862eeccae894b9d2cd2529be6b262c39dc03fb701fc77bc8` 和
[9 项 CPU 测试收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v2/analysis-adapter-cpu-test-v2.json)
SHA-256 `45a0d511c130c750f67dbb28e2920f2bcaeaba4f15bb3f4e9085d8502ed1b88b` 已有；root 仅读协议和收据，
尚未逐一审阅三个源码，也未批准或执行该适配。R2 STOPPED 且链不完整，不满足其运行条件。
科学门仍为 PENDING，需求覆盖仍为 29 PROVEN / 1 PENDING，技术 PASS 和运行状态不等于科学 PASS。

2026-11-11 02:03:59 UTC（10:03:59 北京时间）的 [R3 准备审查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v3/root-preparation-review.json)
SHA-256 `a427c70ad8c90eedd1992a277836b36c71b56c5e4d66477e872a18be168db002` 记录 root 对三个候选源码和协议的完整审阅；
[9 项 CPU 测试](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v3/cpu-test-receipt-v4.json)
SHA-256 `3837554d149a25e7ffa05316773f89912c25dd5f66fc31d58375ab05755d2e29` 为 PASS，
[独立审查范围补充](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v3/static-review-scope-addendum.json)
SHA-256 `8209ed09a7bea302f520caf67e8ff51679658d637957296ad263f9ef3d0a309a` 保留原条件及保护边界。
02:00:21 UTC（10:00:21 北京时间）的[修正终止检查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v3/terminal-inspection-corrected.json)
SHA-256 `6805aaa541247d4290ec231731048ea7d4570564fd6417c2bd2d0f48c0f58801` 为 PASS；
[初次检查 FAIL](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v3/terminal-inspection.json)
SHA-256 `4364ad55a2dec111ef1e9c05e28aeda7eb6c35e644dba3f3851ab682fe4e9798` 因误匹配检查器的父 shell，原记录保留。
02:03:08 UTC（10:03:08 北京时间）的[真实宿主资源检查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v3/resource-gate-check.json)
SHA-256 `223621521a6ca8f30d5057e0cb240e84e0828976cc96ff33ba18616ad30db9f8` 仅检查资源；GPU 7 可用显存
低于 3072 MiB，结果为 RESOURCE_GATE_REFUSED。当时尚未清理失败输出，也没有创建新 archive、run 或 launch claim。
root 已通过异步输入工具询问用户是否允许第 144 条单次重跑例外；
[补充登记草案](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v3/supplemental-registration.draft.json)
SHA-256 `c46c5c1d6c75e55066acb434e57a6a838f2654e2339a0caa7b159abc02c9e143` 保持 PROPOSED_NOT_AUTHORIZED。
用户随后明确授权按原参数重跑，并要求不保留失败记录。用户认为失败由共用服务器拥堵导致；
该说明不等于已排除所有代码问题。在该历史时点尚未清理失败输出或启动重跑，GPU 7 的资源检查
未通过。原 R3 归档保留方案和多段分析适配须按新指示修订；上述 root 准备审查是旧方案的历史记录，
不表示已批准修订方案。上述草案的状态不代表用户的新授权未发生，也不是已安装的补充登记。

2026-11-11 02:23:01.205633 UTC（10:23:01 北京时间），用户授权的 R4 清理已完成：第 144 条失败 stage、claim 及含失败输出的日志和副本已删除，没有备份；前 143 条保持。
[清理收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/cleanup-receipt.json) SHA-256 `4f1e9986411266fa88d9c38219c1a37732b931b62eb8cc16a718c4c5f32e1587`，
[执行收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/root-cleanup-execution.json) SHA-256 `0a7d20324f5abde810d4cc9256c0e4c548a18d6e97dadf283dc76fe7b5c7da00`；
[资源摘要](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/gpu-resource-summary.json) SHA-256 `a603ddd5a341dcdf1e6ceb2b7cb2dea2bdd0ccad2a6079170d6b0037acbc4dbc`。
[清理后宿主预检](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/post-cleanup-preflight.json) SHA-256 `b2d6485b2d48e962c5c72f9ac55b1d98c862e7538ff8e19b21f4d92ede1128d0` 核验来源及身份通过，但 GPU 7 空闲 746 MiB，低于 3072 MiB 操作门；状态为 CLEANED_AWAITING_RESOURCES。该数值是当时快照，并非显存预留。该历史快照时未创建新 run 或 supervisor claim。
R4 [补充登记](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/supplemental-registration.json) SHA-256 `49a845dab9bc651da35d8658245ca3cc080279df4011a6242cfc01a5ffc4032a`，[批准](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/approval.json) SHA-256 `8f7e84fccf26c7381a3f81ac1865a2e9204ef74357d7d967d968c4f12299e703`，[root 审查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/root-review.json) SHA-256 `080e88930d635e05a6216229dc792ecf96737b6128206f5e32c8b89d49d9d46b`。

2026-11-11 02:33:03.331881 UTC，第 144 条依用户授权以原 source、参数、seed 和设备启动唯一重跑；随后依次首次执行 145–152。root 启动 shell session 76873 exit 0 仅证明 shell 启动命令返回；[启动命令](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/root-launch-command.json)、[启动前检查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/launch-preflight.json)及[宿主启动观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/root-host-start-observation.json)保留。
02:44:07 UTC，root 将 rank 801343 六个线程从 CPU 0/96 调整到 12/108。[后观察收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-chain144-observation.json) SHA-256 `2cea9998d589b7085816628761a2b7ad097a5a14c24c31150a39a2aea182c9b3` 在 02:50:43–02:51:03 UTC 核验 supervisor 800712、driver 800993、wrapper 801250、MPI 801340 和两个 rank 存活，六线程掩码正确，输出由 25585667 增至 26695020 字节。该 rank 运行约 20.003 秒、排队 0.000447 秒；此项仅记录当时技术调度，不作科学或性能验收。143/152 已技术完成，144 运行中，145–152 尚未尝试；这只是带时间的宿主快照。
[R4 最终分析准备审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/analysis-preparation-root-review.json) SHA-256 `84def698bf59b64027a69e4b90a2027e59e409b30cb136c0acc6b67282efc1a0` 记录 12 项 CPU 检查及独立和 root 审阅；唯一分析尚未执行，DN4 统计仍 PENDING。

2026-11-11 第 144 条重跑已技术 PASS：原 record SHA-256 `c11fd28fcf40cc2b8ddd30e0fad6e3d3148a51d227bde082b2aa74b4db3c81c9`，原 claim SHA-256 `5a0e3dff3bda2b381e89e4b2b8214d4aa9b256d4bd49bd4af774715d1e9f613d`，wrapper return 0、identity_errors 为空，耗时 2067.192223628983 秒，thermo 文件 115520153 字节。第 145 条按原协议启动，见 [启动观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/job145-start-observation.json)。
root 审阅通用工具与真实身份并 dry-run 后，于 03:12:12.364201 UTC 将目标 rank 1024631 的六线程从 CPU 0/96 调整至 12/108；[执行收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r4-p4-seed301-execution-receipt.json) SHA-256 `7cef5e38e92fd94f67b9844e8968f0ed01122f4df1b54718bcaa7a781c420741`。
[后观察](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r4-p4-seed301-observation.json) SHA-256 `9c9d628ebb8fcd04c89d0f052188ae81ac405e3d6f4a75b319007e01ced5ae83`：03:13:09.453728–03:13:29.455171 UTC 八个身份与 argv 匹配、目标六线程掩码符合预期，其他 rank 和父进程绑定不变，第 144 条旧身份均已退出；四个 rank 各运行约 20 秒、排队约 0.00004–0.00103 秒，输出由 2400442 增至 3074068 字节。该技术快照为 144/152 完成、第 145 条运行、146–152 未尝试；唯一分析未执行，DN4 统计仍 PENDING，不作科学或性能验收。

2026-11-11 第 145 条技术 PASS：原 record SHA-256 `677e669a75ae209c9030e40ddfcd2af58666603e5b01aae84d9635d258b3d29f`，claim SHA-256 `039f33a135be9a1f8fb9086b70731069904614a68d961a94b6b96fe342a5a196`，return 0、identity_errors 为空，耗时 3958.9193071760237 秒，thermo 文件 115520153 字节。第 146 条按原协议启动，[启动观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/job146-start-observation.json) SHA-256 `3d1e141c52fe761c538bbc9ac72434e1aa7441f06779b369a3a49f4a0b4c4add`。
root 在 fresh dry-run 后于 04:17:45.766934 UTC 将 rank 1483210 六线程从 CPU 0/96 调整至 12/108；[执行收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r4-p4-seed302-execution-receipt.json) SHA-256 `aed64bc5b00dc3790dd210a8cafcccb9ec6996299205d506c33e6f4157ed6617`。[后观察](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r4-p4-seed302-observation.json) SHA-256 `9ead30efe1d64d1f51cb2f19483d834bf0a9afe7c2689f917c9e1b10ea1e9f6f`：04:18:45.911–04:19:05.912 UTC 八个身份及 argv 匹配、目标六线程掩码正确、其他 rank 与父链绑定不变，第 145 条六个旧身份均已退出；四 rank 主线程各运行约 20 秒、排队最多 0.000440 秒，输出由 2631843 增至 3209443 字节。该技术快照为 145/152 完成、第 146 条运行、147–152 未尝试；analysis 为 null，DN4 统计仍 PENDING，不作科学或性能验收。

2026-11-11 第 146 条技术 PASS：原 record SHA-256 `ba91f7c40852dfc346285edb3138518e72e6ffe7272df39444b7e45a9a91c885`，claim SHA-256 `15d66d11e06425d42a620c0e324dba048dcf5c7c8d87de46f00ec891a4fab17a`，wrapper return 0、identity_errors 为空，耗时 3892.684729715809 秒，thermo 文件 115520153 字节；六个旧身份在宿主均已退出。第 147 条按原协议启动，[启动观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v4/job147-start-observation.json) SHA-256 `e1ca1bf45b44a7f1079b52a37e27d16ded58353ad53f600104e5ebb28965b7f2`。
root 核验真实身份和 dry-run 后，于 05:22:15.606606 UTC 将 rank 1928744 六线程从 CPU 0/96 调整至 12/108；[执行收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r4-p4-seed303-execution-receipt.json) SHA-256 `d90bf0ecd8266d7ad6d462259a7282c8500a4223cc657c1f2e9aa0378c79092f`。[后观察](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r4-p4-seed303-observation.json) SHA-256 `98e9a48d2268b065476fd5b554b24cf6d2766b55908319e802d56f86129e535e`：05:23:10.393658–05:23:30.395106 UTC 八个身份及 argv 匹配，目标六线程掩码正确，其他绑定不变，第 146 条六个旧身份均已退出；四 rank 主线程各运行约 20 秒，目标排队 0.000416210 秒，输出由 2155323 增至 2854580 字节。该技术快照为 146/152 完成、第 147 条运行、148–152 未尝试；analysis 为 null，DN4 统计仍 PENDING，不作科学或性能验收。

2026-11-11 06:26:48.419600 UTC [R5 宿主终态检查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/terminal-inspection.json) SHA-256 `23903340c8f5afba5946800b551095ff40988e72f6b3301355476b73c9145c91`：第 147 条原 record/claim 技术 PASS，SHA-256 分别为 `03e986e2e6b5921b287c0151592fc86feecf31fa889e4e290d606b86b43a62bc` 和 `5128f642f46699e3a7fa62472851698e79f740cd2221ebbe53eb4dd9fc6ef31e`。第 148 条首次尝试 CUDA OOM；R4 为 STOPPED，监督器实际 wait 到 driver returncode 1，监督器自身 OS 退出码未由外部 wait 观察，记录的八个身份均不存在。累计 147/152 技术完成，149–152 未尝试。该时点第 148 条尚无重跑授权，任何重跑需要新登记；analysis 为 null，DN4 统计仍 PENDING。

2026-11-11 用户明确授权第 148 条以原参数和 seed 单次重跑、失败输出与日志不备份删除；[授权](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/user-exception.json) SHA-256 `f6029d07fa2e9623ae076ada700dcd136e1d4c85a6cf6a989b0f3979af68f71a`，[补充登记](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/supplemental-registration.json) SHA-256 `b7129939159170051e41ace5fb68ac4f5c477a7256e4940eeff4cf53d56df7c3`，[批准](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/approval.json) SHA-256 `d2d050c3a90a7c63fa37b28d371cd2228b1882c9ed2ed4a78419e9c47c92b6d6`。07:03:44 UTC 宿主回读：10 个失败文件和空目录已删除、无备份，147 条完成 claim 保留；[清理收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/cleanup-receipt.json) SHA-256 `6ab24d5da51ce93f8ec093cfac272243e21512f37d182bff537ace4c904fe4fb`，[执行收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/root-cleanup-execution.json) SHA-256 `63ce521020935fb4a311873f440ede12e33622e32b15db888cb04db7cc7d04e0`。
07:03:30 UTC [资源观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/gpu-resource-observation.json) SHA-256 `fbc41646f572b74895b3bc85c196d85a904bfb1da5f8c92a39ea02357527a575`：原设备 7、0、2、6 分别空闲 816、14242、18004、18044 MiB，UUID/driver 匹配；每卡 8192 MiB 操作门未满足，R5 尚未启动。该值是时间快照，不是显存预留或峰值保证。
R5 执行候选已完成 9 项 CPU 检查；[R5 最终分析准备审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/analysis-preparation-root-review.json) SHA-256 `d9cd5c93f8fe6bdf5fa44b4ae840a491c0299372341c999fd4f4a55792e0a176` 记录独立及 root 审阅和 18 项 CPU 检查。分析与最终审核均未执行，需求覆盖 29 PROVEN / 1 PENDING，DN4 科学统计仍 PENDING。

2026-11-11 07:08:03 UTC [清理后预检](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/post-cleanup-preflight.json) SHA-256 `fa923f332f6395f58b277e9e1dfc7fb4803fbe52736ea53ed0e2a366ca1d2c48` 完成 147 条来源、清理、no-look 和宿主验证，到最后资源门因 GPU 7 显存不足拒绝启动。07:08:22–07:13:38 的八次[只读资源观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/resource-wait-observations.jsonl) SHA-256 `d99805c092b813e6b1ba4e2b1784800c221a8e58351dac77ceaba6580714e56b`，观察器 session 15381 实际 exit 0，该退出码不属于 MD 作业。
07:17:29 UTC [最终资源快照](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/resource-wait-final-snapshot.json) SHA-256 `beb46c41c731fdedf1c6421c5c1b783a51ea297a6ae17fb00fef4e5f3726fbf3`：设备 7、0、2、6 分别空闲 1804、14242、17996、18036 MiB；八个旧 R4 身份仍不存在，147 条 claim 保留，失败路径及 R5 启动文件均不存在。状态仍为 WAITING_FOR_ORIGINAL_GPU_MEMORY；[启动命令](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/root-launch-command.json) SHA-256 `33b76480be2d7232899dbae68e5ce6e1aad11edb7e911f8342f9c0e4c44c89b0` 已准备但未执行。
可选 R5 CPU 亲和工具已获独立及 [root 审阅](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/r5-affinity-root-review.json)，审阅 SHA-256 `0a7e8d06f02a29fd8c3e814785e55c3e6735d964f94b351c7b9700e96e372f61`，源码 SHA-256 `829b322cb9fc84a1eb61ffdc0d80e321bc8ada845e15320ce89bbc780c2e5410`；尚未 dry-run 或 apply，也无预设未来 PID。使用时仍需运行后 fresh 身份及负载检查。唯一科学分析未执行，DN4 统计仍 PENDING。

2026-11-11 07:25:28 UTC [资源快照](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/resource-wait-continuation-2.json) SHA-256 `4d58d16cad1cdacdd94f09ff07a989666b2d3e88ec0a6752b4d04bc65f0b3b70`：原 GPU 7、0、2、6 分别空闲 1804、14242、17980、18018 MiB，UUID/driver 匹配，R5 未启动。同一外部阻碍连续三轮，[阻塞审计](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/goal-blocker-audit-turn3.json) SHA-256 `95a6bb64df730d383b0e2eb37addb8b35f4b7d4aa6ddd577c5e211a02e53fe29`；goal 实际状态 blocked。已完成清理不重复，第 148 条原授权仍有效；恢复须原四卡各至少 8192 MiB 并通过 fresh 全预检。147/152 技术完成，第 148 条重跑、149–152 首次运行及唯一科学分析未启动，DN4 统计仍 PENDING。

用户随后恢复目标，`get_goal` 两次确认 active；原三轮 blocked 审计保留为历史，恢复后重新计数。2026-11-11 07:39:00 UTC [首轮资源快照](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/resource-wait-resumed-1.json) SHA-256 `89ee9ca37b035f9f51fdec8b3fa150081e77374b86e97d322163f8bb61bd3997`：原 GPU 7、0、2、6 分别空闲 246、14242、17972、18010 MiB，UUID/driver 匹配；R5 启动和分析均未开始。恢复后相同阻碍计数为 1；147/152 技术完成，第 148 条授权仍有效，已完成清理不重复，DN4 统计仍 PENDING。

2026-11-11 07:54:59 UTC 原设备 7、0、2、6 空闲分别为 19270、14242、17964、18002 MiB，满足资源门；[完整启动预检](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/launch-preflight.json) SHA-256 `f6406cf3e8698eb9af21b5dfe92c444491b782ddbf0b1f1520e3f1a6bec889b8`。R5 对第 148 条已实际单次启动；shell session 7735 exit 0 仅是启动命令退出，不是 MD 退出码。[root 启动观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/root-launch-observation.json) SHA-256 `edc618cc56f7a8a6102b7d8a6cbf381b453adedea8bc874ba2cece0ca7a68e2b` 绑定实际进程身份。
08:09:43–08:09:49 UTC root 对拥堵的 rank 3015946 六线程单次调整 CPU 0/96→12/108，[执行收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r5-p4-seed304-execution-receipt.json) SHA-256 `459ea989e7fa2094af8d4f066af8fcf8a31662c4cc239e1f4bcf6d1aa6f9bcff`。[后观察](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r5-p4-seed304-observation.json) SHA-256 `98ba41bb40d6ec66b6983431d6a78ab76cec274ae1d7d90de9b2d38fa83aaa94`：08:14:21.055002–08:14:41.057435 UTC 八身份匹配、目标六线程掩码正确、其他绑定不变；四 rank 主线程各运行约 20 秒，最大排队 0.000708 秒，输出由 9534163 增至 10267715 字节。该宿主快照为 147/152 技术完成、第 148 条运行、149–152 未尝试，analysis 为 null；goal active、资源阻碍已解除，唯一分析未执行，DN4 统计仍 PENDING。新第 148 条 stage 正在使用，不能重复清理；本观察不作科学或性能验收。

2026-11-11 09:18:18 UTC 宿主终态快照：第 148 条技术 PASS，wrapper return 0、identity_errors 为空；record SHA-256 `3586c127bb3b03b138da7d75cb264fd2c097918f7480c4db0f723307d0093b56`，claim SHA-256 `eb68db571da47e7f064b7f63bfd5e18534d86a2b97a57177c9f6ee964c5d4d03`。R5 在第 149 条启动前因显存低于 8192 MiB 停止，监督器实际 wait 到 driver returncode 1；监督器自身外部 wait 退出码未观察。supervisor、driver 及其记录身份已不存在，[终态检查](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v5/terminal-resource-stop-inspection.json) SHA-256 `e37a1808cccb26cd4c5269ee3fcdc4f734f73d2cbdb7098acc631c9c0d9a11e6`。148/152 技术完成，149–152 均未尝试；R6 原首次任务续跑准备中，不重跑第 148 条，唯一科学分析未执行，DN4 统计仍 PENDING。

2026-11-11 09:28:37.480134 UTC [R6 宿主启动观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/root-launch-observation.json) SHA-256 `639c5c7ffed832757e16ebc2ff3a5b4ce07a1207443167db7c7ab2fbdcb65f0d`：supervisor PID 3558349/start_ticks 1475107678 与 driver PID 3558743/start_ticks 1475109556 的 UID、exe、argv 和进程关系匹配且当时存活。R6 progress 为 WAITING_FOR_RESOURCES，等待首次执行第 149 条，149–152 均未尝试；09:28:16 GPU 7 空闲 1804 MiB，analysis 为 null。启动 shell session 47591 exit 0 只代表启动 shell 退出，并非 MD 或 driver 退出；[完整 148 前缀预检](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/launch-preflight.json) SHA-256 `0fc50a673950e86c358aa5649cd292292969c926a54232aba0072d0bd72c255f`。这是带时间的真实宿主观察，不保证持续存活。
[v6 分析准备审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/analysis-preparation-root-review.json) SHA-256 `097973808ce0aac8515858f403dc48b2b3b3c0cfc73007a24016333f2b1e4737`：独立和 root 审阅、22 项 CPU 检查完成；真实最终 gate、唯一分析及最终审核均未执行。[R6 可选亲和工具审阅](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/r6-affinity-root-review.json) SHA-256 `6daa6427f583a75d2c9978a7194bfdadd099b2e8ccf64a48c42988f183ce0a4a`，工具尚未执行。148/152 技术完成；goal active，本轮 progress，DN4 统计仍 PENDING，第 148 条已完成且不可重复清理。

2026-11-11 09:42:16.259067 UTC 宿主快照：R6 supervisor 3558349 和 driver 3558743 身份匹配且存活，progress 为 WAITING_FOR_RESOURCES、jobs 为空；原 GPU 7、0、2、6 空闲分别为 1804、14242、17860、17898 MiB。相同资源条件连续三个 goal turn，[阻碍审计](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/goal-blocker-audit-turn3.json) SHA-256 `b320751e7d1fe2098d6d6899d1c669c31cfcbd169794ab516bed13ed71b0306c`；[goal 工具结果](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/goal-blocked-tool-result.json) SHA-256 `9d2d015fd13086ffd477ecd652d1d1e60872427ffae84cdaeba8483c2a9582d9` 实际返回 blocked。目标状态与后台队列分开：队列未停止，仍对每个未启动任务最多等待 24 小时；原四卡各 ≥8192 MiB 后按原参数首次执行 149–152。当前没有 MD 计算，148/152 技术完成，analysis 为 null，DN4 统计仍 PENDING。

用户已恢复 goal 为 active，先前 R6 blocked 审计保留为历史，恢复后阻碍计数重置。2026-11-12 02:26:13 UTC root 宿主快照：R6 supervisor 3558349 和 driver 3558743 身份匹配且存活，第 150 条 `scr-half-p4-CudaAware-affine-seed306` 运行中，wrapper 3151487、MPI 3151701、四 rank 3151714–3151717；thermo 文件当时 75848780 字节，仅检查文件大小。149/152 条技术完成，151–152 尚未尝试，analysis 为 null。
第 149 条 [技术完整性收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/job149-completion-observation.json) SHA-256 `96a696e324473fb5507c00b2b35a9514a5bec0e8340734a258f6d95a5d33a0b4`：record SHA-256 `a99884baf1e9416b7fea5d3c1df5a71ec6ad6a2379f565cc804eaa719fcb2c09`，claim SHA-256 `9d55b2e106e66a3687f61b84ed232062b12773f41ac81ebd8e3820fdbf2cf6da`，与 progress 一致；wrapper return 0、identity_errors 为空，冻结元数据、输入、raw 和日志哈希通过，02:27:41.559112 UTC 宿主检查的七个已记录身份均已退出。该检查未解析科学数据，也不声称 R6 supervisor 已退出；唯一分析未执行，DN4 统计仍 PENDING。

2026-11-12，第 150 条 rank 3151714 的六线程经 [dry-run 收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r6-p4-seed306-dryrun-receipt.json) SHA-256 `4efb2ed0a41e049c2a566b1f6de7768cfefdd9c745b73db9bb0f19dff230c817` 后，于 02:30:42.243433–02:30:48.319935 UTC 单次从 CPU 0/96 调整至 12/108；[执行收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r6-p4-seed306-execution-receipt.json) SHA-256 `cc9cd695372340219faf2a50fc2bb2e97754e404c3d7c73c0925b876711b3762`。
[独立后观察](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r6-p4-seed306-observation.json) SHA-256 `ec9d57da86a101f9079f4f9d699906c112f76b931d280dacd74ce45d37584a3a`：02:31:45.212–02:32:05.214643 UTC 八个身份、argv 与实际 OMPI rank 匹配，目标六线程掩码正确，其他 rank 全部线程与父链绑定不变；四主线程各运行约 20.003 秒，最大排队 405354 ns，输出由 78180479 增至 78738224 字节。该时点 149/152 技术完成，第 150 条运行，151–152 未尝试，analysis 为 null；goal active、资源阻碍已解除。此为技术快照，不是科学或性能验收。

2026-11-12 02:53:10.548095 UTC 第 150 条 [技术完整性收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/job150-completion-observation.json) SHA-256 `fee844c0f227d761e430cafb9f71ba48820b6d9f1c0006f011e4f7e102e3b1f8`：record SHA-256 `5816bd62723532430b29c73115af5edd863973f0ca0fef25c0fbd87ed93fc933`，claim SHA-256 `a9a70689730cd0b13f3a37c517b759d85d56cd52d26a1ac345e85dd482fae8ca`，与 progress 一致；wrapper return 0、identity_errors 为空，冻结元数据、输入、raw 与日志哈希通过，六个已记录身份经宿主核验均已退出。该检查未解析科学数据，不代表科学 PASS。R6 进度显示 WAITING_FOR_RESOURCES，等待首次启动第 151 条，151–152 均未尝试；root 于 02:53:13 UTC 宿主核验 R6 两个父进程身份存活；02:52:48 GPU 7 空闲 2480 MiB。此前资源阻碍已解除且第 150 条完成，本次资源等待重新从第 1 轮计数，goal 仍为 active。累计 150/152 技术完成，唯一分析未执行，DN4 统计仍 PENDING。

2026-11-12 [第 151 条资源等待观察](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/resource-wait-job151-observation.json) SHA-256 `2e021d4ce970161ece4e666a395285122a84fa5c6d16bb188609ca16b3d5e4ff`：root 的有界观察器 session 6463 于 02:58:13 UTC 正常 exit 0，此码仅属于观察器，并非 MD 或 driver。root 于 03:03:31.625795 UTC 宿主复核 R6 两个父进程身份匹配且存活、progress 为 WAITING_FOR_RESOURCES，等待首次启动 seed307，supervisor exit 收据不存在；03:03:21.092679 UTC 原 GPU 0、2、6、7 分别空闲 14242、16856、17042、2480 MiB。当前 150/152 技术完成、151–152 未尝试、analysis 为 null；本轮已完成第 150 条，goal active，本次新资源等待计数为 1，DN4 统计仍 PENDING。以上是带时间的技术快照，不是科学验收。

2026-11-12 03:08:54.749821 UTC [第 151 条等待第 2 轮](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/resource-wait-job151-turn2.json) SHA-256 `8ac95fba2391706b867768d866bfce4cb83482e0dcd16ca965c413c78af9d3c6`：root 宿主观察 R6 supervisor 3558349/start_ticks 1475107678 和 driver 3558743/start_ticks 1475109556 身份匹配且存活；progress 为 WAITING_FOR_RESOURCES，等待 seed307，150/152 技术完成，151–152 未尝试，analysis 和 supervisor exit 均为空。03:08:52.407865 UTC 原 GPU 0、2、6、7 分别空闲 14242、16848、17034、2480 MiB，每卡门槛 8192 MiB。上一 goal turn 为 progress，本轮为 verified_wait；新阻碍连续第 2 轮，goal 仍 active，后台为资源等待而非 MD 计算。

2026-11-12 03:13:48.186684 UTC [第 151 条阻塞工具结果](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/goal-blocked-job151-tool-result.json) SHA-256 `ebd330d76c921ba29d3096cc58e8f77a32b2230d88efff4f062ca7842a2ea432`：root 宿主核验 R6 两个父进程身份匹配且存活，progress 为 WAITING_FOR_RESOURCES；03:13:23.634926 UTC GPU 7 空闲 2480 MiB。相同资源阻碍连续第 3 个 goal turn，[阻塞审计](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/goal-blocker-job151-turn3.json) SHA-256 `ce2381f34dbd5c7e39f28c8ea3d0f8c0e0bc4a4586088b6799b21ce58a23a834`；goal 工具实际返回 blocked。目标状态与后台队列分开：R6 未停止，继续有界等待原 GPU 资源；当时没有 MD 计算，150/152 技术完成，151–152 未尝试，analysis 为 null，DN4 统计仍 PENDING。

用户恢复 goal 为 active；原第 151 条 blocked 审计保留为历史，本次资源阻碍计数清零。2026-11-12 06:53:02.860372 UTC root 宿主快照：R6 supervisor 3558349/start_ticks 1475107678 与 driver 3558743/start_ticks 1475109556 身份匹配且存活，第 151 条 seed307 的 wrapper 1698698、MPI 1698854 和 rank 1698857–1698860 均存活，rank 掩码分别为 0/96、1/97、2/98、3/99；thermo 文件 8014714 字节，仅检查文件大小。150/152 技术完成，第 152 条未尝试，analysis 为 null。此前 06:25:10.700158 UTC 启动前资源快照中原 GPU 0、2、6、7 分别空闲 14242、16752、17002、24092 MiB；该数据不是当前资源保证。第 151 条尚未完成，唯一分析未执行，DN4 统计仍 PENDING。

2026-11-12，第 151 条 rank 1698857 六线程经 [dry-run 收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r6-p4-seed307-dryrun-receipt.json) SHA-256 `93bf9d05b2683d3e74148e811b90d3c24bfb4094a0964d28726e8dd39add1c0b` 后，于 06:59:03.046297–06:59:09.129350 UTC 单次从 CPU 0/96 调整至 12/108；[执行收据](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r6-p4-seed307-execution-receipt.json) SHA-256 `11cbacf8bbcea984b38d0eb1bb570a03b930e660d40fa9ed469c51f31f88d7ac`。
[独立后观察](../../../artifacts/domain-npt/statistics-final-v1/diagnostics-cpu-affinity-v1/adjust-r6-p4-seed307-observation.json) SHA-256 `6d3e87b1e5f498b1302bac8e10462008e60711947687d54bb4d9e31d76417a0d`：07:00:32.908515–07:00:52.910963 UTC 八个角色、完整 argv 与实际 OMPI rank 匹配，目标六线程掩码正确，其余 rank 所有线程与父链掩码不变；四主线程各运行约 20.003 秒，最大排队 307377 ns，输出由 11618216 增至 12174156 字节。该时点 150/152 技术完成，第 151 条运行，152 未尝试，analysis 为 null；goal active、资源阻碍已解除。这是技术调度观察，不是科学或性能验收。

2026-11-12 07:59:32.290919 UTC 第 151 条[技术完成收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/job151-completion-observation.json) SHA-256 `3fcb77830ca7b53e3bc260640b1c0cdc4b645f885e48a66cca060aa0713acb2d`：record SHA-256 `3480d351a20adeed3e00e2e9f976f62ad5394774e9d4469ff1de1e5a156488cf`，claim SHA-256 `d85970e58f4d79d64d7c61d04d0d51e57423454e0e2dbd91e1a0bffcf24b64e2`，wrapper return 0、identity_errors 为空，冻结输入、raw 和日志哈希通过，七个已记录身份经宿主核验均已退出；不构成科学 PASS。
08:01:02.431566 UTC root 宿主快照：R6 父进程 3558349/3558743 身份匹配且存活，progress 为 WAITING_FOR_RESOURCES，seed305/306/307 PASS，等待首次启动 seed308，无 active MD、analysis 和 supervisor exit 均为空；151/152 技术完成，第 152 条未尝试。08:00:58.275559 UTC 原 GPU 0、2、6、7 分别空闲 14242、16656、16922、2480 MiB；新的第 152 条资源等待从第 1 个 goal turn 计数，goal active，本轮因第 151 条完成为 progress。唯一分析未执行，DN4 统计仍 PENDING。

2026-11-12 08:11:23.779712 UTC [第 152 条阻塞工具结果](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/goal-blocked-job152-tool-result.json) SHA-256 `a9831e098672b25d3de3698bd756077f945616e034d024dea74eb42c7bebbd57`：root 宿主核验 R6 两个父进程身份匹配且存活，progress 为 WAITING_FOR_RESOURCES；08:11:00.735968 UTC GPU 7 空闲 2480 MiB。第 152 条新资源阻碍连续第 3 个 goal turn，[阻塞审计](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v6/goal-blocker-job152-turn3.json) SHA-256 `475bde9f847d7d6f55f8fb8e59de49b6ab9c31e7b33e36d6a47b154f1141726e`；goal 工具实际返回 blocked。目标状态与后台队列分开：R6 未停止，继续有界等待原 GPU 资源；当时没有 MD 计算，151/152 技术完成，第 152 条未尝试，analysis 为 null，DN4 统计仍 PENDING。

2026-11-12 08:55:48.697576 UTC，[R6 安全交接收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/handoff-result.json) SHA-256 `da8a87b84ffcf77339182b9e7c7f771a994011a85087a3800bc298de67b87135` 为 PASS：R6 progress 保持 STOPPED，原 supervisor 实际 wait 到 driver returncode 1，supervisor 自身外部 wait 退出码未观察；root 宿主核验原父进程已退出。151/152 技术完成，第 152 条未尝试，analysis 为 null。按用户要求，R7 拟对最后首次任务使用 3,0,2,6；最新单次资源观察 GPU 3 空闲 660 MiB，设备短测尚未启动。短测和最终科学分析均不能据交接 PASS 推断为通过，DN4 统计仍 PENDING。

2026-11-12 09:05:08.403195 UTC，[R7 设备验证器启动收据](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/device-validation/validation-root-launch.json) SHA-256 `c0dd0e1ee6bf4321ad17e30ba8f2304a695aa4c324c1b23528bf0fc712807eb1`：独立 session 中验证器 PID 2121424/start_ticks 1483615682，root 监督父 PID 2121414/start_ticks 1483615678；验证器当前等待设备资源。此项仅为原候选的新设备技术验证，正式第 152 条尚未尝试；151/152 技术完成，唯一科学分析未执行，DN4 统计仍 PENDING。

2026-11-12 09:16 UTC root 宿主快照：R7 设备验证器 2121424/start_ticks 1483615682 及其父进程 2121414/start_ticks 1483615678 身份匹配且存活，stdout 为 `WAITING_FOR_RESOURCES before-claim`，stderr 为空；设备验证 claim、proof、golden、differential 和 summary 均尚不存在。原设备 0、2、3、6 分别空闲 14242、16576、3074、16858 MiB，GPU 3 未满足 8192 MiB 操作门；正式第 152 条 stage/claim、MD 审批和 analysis claim 均不存在，151/152 技术完成。这是带时间的资源快照，goal active，新资源等待为第 1 轮。
[R7 执行准备 root 审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/root-preparation-review.json) SHA-256 `7092b1e5df6e5a7f950d2a02d8fcfd188d118b0fd16a64d0efd58a108a098360`，以及[独立审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/execution-independent-review.json) SHA-256 `f862c3019f6e0fbe50a1d345f99ba5d90e75bb161078a910ca517d9831b5b7c8` 和[第二独立审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/static-review-v14-independent.json) SHA-256 `64fa414964a797844e19cf37d9d536b2b22ebfaa206760df8af045b057ec3268`，绑定 14 项 CPU 检查；仅准备通过，并非执行批准。
[R7 最终分析准备 root 审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/analysis-adapter-root-preparation-review.json) SHA-256 `4c29be3f01dde3db69465c6432b7002b1b272f5e26ecd816579e5c5f02fecc30` 与[独立审阅](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/analysis-adapter-independent-static-review.json) SHA-256 `e099952c8186d0ce6ebe698cd119a3ee767a0e762e6ccb62d4d55493a1aa9e2a` 绑定 28 项 CPU 检查；真实最终 gate、唯一分析及最终审核均未执行。DN4 统计仍 PENDING。

2026-11-12 09:29:14.527926 UTC [设备验证资源阻碍第 3 轮审计](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/goal-blocker-device-validation-turn3.json) SHA-256 `e1345951cd87e27d8b9c37c6baa7612185f72d07e77c7e3cc9b87e47aecdcd56`：root 宿主核验验证器 2121424/start_ticks 1483615682 及父进程 2121414/start_ticks 1483615678 身份匹配且存活，原 session 5312 仍运行；设备 0、2、3、6 分别空闲 14242、16568、2728、16850 MiB，GPU 3 低于 8192 MiB 操作门。验证 claim、proof、各阶段及 summary 均无，正式第 152 条 stage/claim/审批和 analysis claim 均无；151/152 技术完成，DN4 统计仍 PENDING。同一新资源条件连续第 3 个 goal turn，建议 blocked 但须等待 root 实际工具结果；当前 goal 仍 active，后台验证器未停止。可选 R7 亲和工具已完成独立及 root 准备审阅，尚未执行。

2026-11-12 09:32:16.352764 UTC root 再次宿主核验设备验证器及父进程身份匹配且存活、仍在 claim 前等待，stderr 为空；GPU 3 空闲 2728 MiB，低于 8192 MiB。验证阶段、正式第 152 条及 analysis 尚未开始。[goal 工具原始结果](../../../artifacts/domain-npt/statistics-final-v1/recovery-resume-v7/goal-blocked-device-validation-tool-result.json) SHA-256 `79116bd3f182ca467df89171152b4d6c8f95961891c768c3c568f7759d3600b4` 实际返回 blocked；前述阻塞审计保持不变。目标 blocked 与后台验证器状态分开：验证器未停止，继续既有最多 24 小时的有界资源等待，没有正式 MD 或科学分析，151/152 技术完成、DN4 统计仍 PENDING。

[最终审核器 v2](../../../artifacts/domain-npt/statistics-final-v1/audit-statistics-final-v2.py)
SHA-256 `efaf2e3573bb4978a8ae8a2dabe72e75bbbb99008225852b80567edde30afc7f`，
[静态审阅收据](../../../artifacts/domain-npt/statistics-final-v1/final-auditor-review.json)
SHA-256 `35a60c821faf0c12ba50a4ca1261cf057eb8fff99247a6e3a7bdcfacb61a2c71`；该原批次审核器尚未执行。R4、R5 和 R6 最终审核准备均保留为历史，由已审阅的 R7 设备例外最终分析及审核方案接续；实际执行批准仍待设备验证、152 条完整证据、R7 监督器实测 driver 退出 0、相关身份退出及唯一最终分析完成，审核不重算统计量。

2026-11-13: The user resumed the goal. The original R7 device validator completed environment and rank-probe commands with return code 0, then its field parser rejected the digit in the P2P key. Its parent OS exit was not observed. The golden and differential checks remain unattempted; a hash-bound continuation and compatible evidence gates are in preparation. Formal job 152 and the single scientific analysis have not started; 151/152 chains are technically complete and DN4 statistics remains PENDING.

2026-11-13 01:51 UTC: The technical device-validation continuation was started under its recorded supervisor and is waiting for resources before its claim. This is a point-in-time status: 151/152 chains are technically complete, formal job 152 remains unattempted, and the single scientific analysis has not started. DN4 statistics remains PENDING.

2026-11-13 01:54 UTC: The authorized conditional queue is live and waiting for completion of the technical device validation. If the reviewed evidence and resource checks pass, it will start the first attempt of job 152 once. At this snapshot, 151/152 chains are technically complete, job 152 has not started, and scientific analysis remains unexecuted; DN4 statistics is PENDING.

2026-11-13 02:00 UTC: The goal was marked blocked after the resumed device validation again encountered insufficient GPU memory (GPU 3: 2106 MiB free against the 8192 MiB gate). The existing validation continuation and conditional queue remain live with bounded 24-hour waits; if the technical and resource checks pass, the queue can start the first attempt of job 152. At this snapshot 151/152 chains are technically complete, job 152 is unattempted, and the unique scientific analysis has not started. This November 13 blocker is separate from the earlier November 12 history; DN4 statistics remains PENDING.

2026-11-13 06:12 UTC: Root confirmed the first R8 job 152 running on the original registered devices 7,0,2,6 with CudaAware/affine and seed 308; the actual MPI ranks and domain route were observed. The earlier R7 technical validation failure remains historical. At this snapshot 151/152 chains are technically complete, the last chain is running, and the single scientific analysis has not started. The goal tool still reports blocked from the earlier resource wait; user-directed execution has resumed, and DN4 statistics remains PENDING.

2026-11-13 06:22 UTC: Root confirmed the one-time rank 0 CPU affinity adjustment for running R8 job 152: six threads moved from CPUs 0/96 to 12/108 on node 0, while GPU 7 remained on node 1. The 10-second follow-up observed active rank execution and growing output; this is a scheduling observation, not a benchmark or scientific acceptance. The v8 final-analysis adapter has root and independent preparation reviews and 29 passing CPU checks; its sole scientific analysis remains unapproved and unexecuted pending actual chain and driver completion. DN4 statistics remains PENDING.

2026-11-13 09:18 UTC: All 152 registered chains have completed technically. The last R8 record, claim, frozen inputs, raw output and logs passed identity checks; the supervisor recorded driver return code 0 and all eight recorded process identities had exited. This is not a scientific result. The user resumed the goal; the sole final analysis and DN4 statistical acceptance remain pending, with 29 requirements PROVEN and one PENDING. Historical limits, including unobserved outer process exits, remain in force.
