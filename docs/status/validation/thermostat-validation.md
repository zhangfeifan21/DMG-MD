# NHC/BDP 恒温器验收

## 目标与阶段索引

本文件按同一验收目标整合日期阶段报告。日期按原记录的执行/复核口径列示；若实施和执行跨日，单独注明。各阶段原始身份、命令、结果、失败尝试、边界与产物证据均保留在对应章节。
| 阶段 / 目标 | 执行日期或阶段 | 结论与边界 | 章节 |
|---|---|---|---|
| A3 / NHC | 2026-11-02 | DONE，限报告验收的 NEP 产品路径；harmonic 轴向诊断 INCONCLUSIVE | [报告](#nhc-20261102) |
| A4 / BDP | 2026-11-02 | DONE，限已验收 NEP 路径 | [报告](#bdp-20261102) |
| A5 / 双路径、MPI 与后端矩阵 | 2026-11-02 | DONE；G-A 仅在已测 NEP/单节点范围通过 | [报告](#thermostat-matrix-20261102) |
| A6 / 文档与终检 | 2026-11-02 | DONE；未启动 B/C、跨节点、release 长程或 D4 | [报告](#thermostat-docs-20261102) |
| 跨引擎 harmonic 对照 | 2026-11-03 | 完整验收 INCONCLUSIVE；保留 K PASS 与轴向边界 | [报告](#thermostat-reference-controls-20261103) |



<a id="nhc-20261102"></a>

<a id="nhc-20261102--a3nhc-实施与验收2026-11-02"></a>

## 2026-11-02：A3 NHC


<a id="nhc-20261102--结论"></a>
### 结论

**A3 DONE，限本报告已验收的 NEP 产品路径。** 四节 NHC 链、owned 动能归约和 root
因子协议已接入 M1/M2a。P1 GPUMD 200-step 对照、同二进制重复、全部 11 个 NEP KE
统计组及实际 NEP 速度分量检查通过。G-A 的整体结论见
[A5 矩阵](thermostat-validation.md#thermostat-matrix-20261102)。额外无相互作用谐振子的轴向均值仍为
**INCONCLUSIVE**，详见下文；不宣称普遍遍历性。

revision `f06284408f12b6d89ed5a19434c44c958307af7b`，分支 main、dirty；未提交、未推送。
原始证据根为 `/tmp/dmgmd-a3-a6-20261102/`，下文路径相对于该目录。
A3 开工时 21 tracked dirty、7 untracked，253 文件的初始哈希与副本见 `initial.json`、`before/`。
既有 A0–A2 和用户修改保留；最终保护核验统一见 A5/A6 记录。

<a id="nhc-20261102--实施数据所有权与兼容边界"></a>
### 实施、数据所有权与兼容边界

- `src/gpumd_compat/thermostat_nhc.hpp/.cpp` 复现 pinned `ensemble_nhc.cu` 的构造与 `nhc`，
  保留 GPL/Origin。链长 4，位置零、动量 `[1,-1,1,-1]`，SY7 × RESPA4。
  仅安全适配上游 m=0 时结果被覆盖的负索引读取，先分支再读取；有效数值表达式不改。
- `include/dmgmd/ensemble_control.hpp`、`src/ensemble_control.cpp` 每段建立 root 链；
  `tau=dt_internal*Tdamp`，`Q_i=kB*T1*tau²`、`Q0*=3N`，ramp 质量固定于 T1。
  非 root 不分配隐藏状态，NVE/Berendsen 保持无链/RNG 分配。
- `src/runtime.cu`、`src/domain_runtime.cu` 在 VV1 **之前**调用现有 thermo kernel 的
  quantity-0 block；M1 仅 owned 索引、M2a 仅 owned prefix，归约一个 double。
  root 前半步 → 广播 → owned 缩放；VV2/thermo 后 root 后半步 → 广播 → owned 缩放。
  两处旧速度缩放 kernel 保持原样；未修改 NEP 数学、布局、所有权或通信半径。
- `src/runtime_internal.hpp` 捕获 root 更新错误，先广播状态，再分发错误并统一退出。
  `src/run_parser.cpp` 在完整输入预检中拒绝新 NVT 与 CV/自适应步长、非正 dt 的组合；
  实验分区/重叠/调度/trace 非默认值拒绝。NPT 仍拒绝。
- 两份 CMake、controller/parser tests 和 `tests/thermostats/` 新增 fixture/物理统计/协议脚本。
  生产构建不引用、编译或链接外部 reference 源码。

用户语法为 `ensemble nvt_nhc T1 T2 Tdamp`，温度 K、Tdamp 步数；温度仍按 3N。
每段与新作业重建链，restart 不连续；thermo 是末次缩放前，XYZ/restart velocity 是缩放后。
完整现行合同见[系综标准](../../standards/ensembles.md)。

<a id="nhc-20261102--构建与运行身份"></a>
### 构建与运行身份
| 项 | 身份 |
| --- | --- |
| A3 冻结候选 | `build-a3/dmg-md`，SHA `d22060a9463f6a324a06876ffd01a5659942f27a6bc95a92c8b36739b989afae` |
| A4 整合候选 | `build-a4/dmg-md`，SHA `0a51d5add72a454bab2dc5cd4bd19733abe1fb610365c7f9359d8b7f48549ff7` |
| 源码绑定 | `a3-source-frozen/source-sha256.json`、`a4-source-frozen/source-sha256.json` |
| 工具链 | Release，GNU 11.4.0 / CUDA 12.9.86 / CMake 3.28.3，C++17/CUDA17，`-O3 -DNDEBUG`，架构 75/80/86/89/90 |
| MPI / UCX | Open MPI 5.0.10 / UCX 1.22.0；PML ucx、排除 HCOLL；各新 shell source `../env/md-mpi.sh` |
| 主机 / GPU | n4，8 × RTX 4090，driver 580.173.02；后续矩阵用 devices 2/4/5/7，早期少量短程使用 0–3 |
| 环境记录 | `environment-final.json`；完整工具版本、GPU UUID、ldd、环境脚本 SHA |

A3 统计使用已封存 A3 候选。整合 BDP 后，重新运行完整 NHC 短矩阵；30 stage 的 120 个科学
输出与 A3 **SHA 全相同**（`nhc-a3-final-bitwise.json`），并经 host 标量逐位 oracle 与代码审查
确认相同 NHC 数值顺序，未将两个二进制笼统称为同一个。
并行作业曾受 Open MPI 默认 CPU 绑定竞争影响；后续仅将本轮候选分配到不同 CPU，
逐 PID/thread 的前后 affinity 见 `affinity-adjustments.jsonl`。无性能采用结论。

<a id="nhc-20261102--确定性单元和错误矩阵"></a>
### 确定性、单元和错误矩阵
| 验收 | 实际结果 / 证据 |
| --- | --- |
| 完整 CTest | A3 host 22/22；最终整合 host 23/23、0 skip，沙箱 22 pass + 1 CUDA skip；`a4-ctest-*` |
| 标量 oracle | 三 case × 四个连续 half-step，全链与 factor 逐位一致；独立重生成相同，`independent-oracle-audit.json` |
| controller/parser | 最终 controller 133 案例、parser ensemble 105 案例；完整 `Testing/Temporary/LastTest.log` |
| NEP4 短矩阵 | 2016/5184 原子，200 step，P1/P2/P4、两后端；28 candidate stage + 2 GPUMD，22 比较全通过；`a5-nhc-final-short/` |
| 同配置重复 | 11 个配置各一次重复，thermo/trajectory/restart byte-identical；repeat 哈希在 `results.json` |
| 负例 | CV、自适应 dt、partition/overlap/schedule、零 KE × P1/P2/P4 共 18，120 s 内非零退出，无最终 XYZ/restart；`a3-negative/` |
| 协议/账本 | 12 个几何/rank/backend 组合、24 stage；每 rank 每步两次相同 factor 位模式；`a3-protocol/protocol-results.json` |
| restart | 原矩阵 HostStaged smoke，加 A5 所有 52 配置与两次 P1→P4 的新作业；不比较连续性；`a5-restarts/` |

P1 对 pinned GPUMD 的首个超差步/列：**无**。两 case 的 T/KE/PE/thermo stress、position、force、
virial 最大绝对差为 0；velocity 最大 `1.734723475976807e-18 Å/fs`；XYZ stress 最大
`2.439454888092385e-19 eV/Å³`；restart 字段相同。详细逐字段次数、最大差及 worst 位置在
`a5-nhc-final-short/results.json` 的两个 `*-gpumd` 比较中，未改容差。

每步相对 Berendsen 的账本增量为 calls +3、MPI input `32+8P`、output `40P` B；
HostStaged staging D2H/H2D 各 +8P B，CudaAware staging +0。M2a p2p 字段无额外变化。
额外 host 标量读取不计作 MPI staging，计数含义见[MPI 合同](../../standards/mpi-runtime.md)。

<a id="nhc-20261102--预注册-nep-统计"></a>
### 预注册 NEP 统计

`manifest.json` 在正式采样前冻结，SHA
`84d3ea6fc94e6860eab730e0bcd0e3106e75954ed12ff43bcf3ab69233e60c9f`。
432 原子相互作用 cluster，显式独立 Gaussian 初速，seed 101/102/103，不去质心/不做 CV；
3N=1296，target 300 K。预算 20k/40k/80k，丢弃前 10%，原 block 500；减半 dt 同步加倍
步数、Tdamp 和块长度。误差带均值 ±2%、方差 ±15%，最低 ESS=1000、完整块≥32。
三个 planned looks 使用单次约 98.33% CI；不宣称整个矩阵的联合置信度。

早期分析只用 block CI，发现 seed 间离散未充分计入后，修正为 block 与 seed 间 Student-t
CI half-width 的较大值。**这项分析修正发生在先导结果之后，不能称为全部方法事先冻结。**
所有初速、seed、误差界、置信概率、burn、block 和最大预算不变；旧报告保留，全部 11 组
以更保守方法复算（`a3-statistics-conservative-final.json`）。P2 fallback 双后端从 20k/40k
INCONCLUSIVE 延长至 80k，half-dt 从实际 40k 延长至 80k；下表只列最终预算，前序全部保留。
| 统计组 | 实际 steps/seed | mean T / CI (K) | Var(K)/target / CI | ESS K/variance | 结果 |
| --- | ---: | --- | --- | ---: | --- |
| `cluster_domain-p1-HostStaged` | 20000 | 299.72368 [297.71436, 301.73300] | 0.99969 [0.90729, 1.09208] | 1646/2262 | PASS |
| `cluster_domain-p1-CudaAware` | 20000 | 299.72368 [297.71436, 301.73300] | 0.99969 [0.90729, 1.09208] | 1646/2262 | PASS |
| `cluster_fallback-p2-HostStaged` | 80000 | 300.08576 [299.57228, 300.59925] | 1.02644 [0.95561, 1.09728] | 6659/8783 | PASS |
| `cluster_fallback-p2-CudaAware` | 80000 | 300.08576 [299.57228, 300.59925] | 1.02644 [0.95561, 1.09728] | 6659/8783 | PASS |
| `cluster_domain-p2-HostStaged` | 20000 | 299.72368 [297.71436, 301.73300] | 0.99969 [0.90729, 1.09208] | 1646/2262 | PASS |
| `cluster_domain-p2-CudaAware` | 20000 | 299.72368 [297.71436, 301.73300] | 0.99969 [0.90729, 1.09208] | 1646/2262 | PASS |
| `cluster_fallback-p4-HostStaged` | 20000 | 300.01490 [298.63623, 301.39357] | 1.00458 [0.86439, 1.14476] | 1670/2246 | PASS |
| `cluster_fallback-p4-CudaAware` | 20000 | 300.01490 [298.63623, 301.39357] | 1.00458 [0.86439, 1.14476] | 1670/2246 | PASS |
| `cluster_domain-p4-HostStaged` | 20000 | 299.96464 [299.00760, 300.92169] | 0.99480 [0.88234, 1.10726] | 1535/2082 | PASS |
| `cluster_domain-p4-CudaAware` | 20000 | 299.96464 [299.00760, 300.92169] | 0.99480 [0.88234, 1.10726] | 1535/2082 | PASS |
| `cluster_domain-p1-HostStaged-half-dt` | 80000 | 299.95350 [299.19222, 300.71478] | 0.97764 [0.85381, 1.10148] | 3400/4590 | PASS |

normal P1 与 half-dt 的 T 点差约 0.230 K、方差 ratio 差约 0.0220；两者 CI 均在各自预设带内。
这支持本例在已测步长下未检出超过目标精度的偏差，不证明任意 dt 无偏。

实际 NEP 速度分量另用预先冻结的 `velocity-manifest.json`，8 seed × 40000 步，
前 10% 丢弃，每 50 步输出；每帧统计 `A_axis=mean_i(m*v_axis²)`，目标 mean=kBT、
variance=`2(kBT)²/N`。20k 轮 x variance CI 偏宽，按原预算延长后全部通过。
| 轴 | mean A / CI (eV) | Var(A)/target / CI | ESS A/variance | 结果 |
| --- | --- | --- | ---: | --- |
| x | 0.02587227 [0.02578717, 0.02595738] | 1.01121 [0.94019, 1.08223] | 3148/5017 | PASS |
| y | 0.02587586 [0.02580016, 0.02595156] | 1.03610 [0.98649, 1.08571] | 2711/5282 | PASS |
| z | 0.02578901 [0.02569510, 0.02588292] | 0.99540 [0.93664, 1.05416] | 2456/5161 | PASS |

原第一轮 GPU 数据完整，但分析器曾错误读取 block_length_frames 字段而失败；修正映射后
只读重分析原数据（`a3-velocity/velocity-reanalysis.json`），原 FAIL 不覆盖；正式延长证据为
`a3-velocity-continuation/velocity-results.json`。

<a id="nhc-20261102--额外可解谐振子诊断与限制"></a>
### 额外可解谐振子诊断与限制

这部分为独立 CPU harmonic sampler，对 24 个无约束、无相互作用模式检查可解正则矩，
不是生产 NEP 势组合或 M1/M2a 矩阵项。pilot 为 8 seed × 200k，确认轮在新 manifest
`harmonic-confirmation.json` 中固定为同 8 seed × 2M、burn 200k、99% CI，不替换 seed。
确认 manifest SHA `013561edaca8d52b0c9bca8e994ea70c8752e5a9dec194e1ab3a3a88b943e590`。
| campaign | K mean / CI (eV)，target=0.31022435 | K variance ratio / CI | 轴向 mean |
| --- | --- | --- | --- |
| pilot | 0.31001118 [0.30874674, 0.31127561] | 0.99881 [0.97605, 1.02156]，PASS | x/y/z INCONCLUSIVE |
| confirmation | 0.31039229 [0.30978956, 0.31099503] | 1.00564 [0.99492, 1.01636]，PASS | x/y/z INCONCLUSIVE |

confirmation 的轴均值 x/y/z 为 0.02613007 / 0.02500481 / 0.02646319 eV；
当前 chain-aware CI 为 [0.02447869,0.02778145] / [0.02345161,0.02655801] /
[0.02424288,0.02868350]，目标 0.025852029，允许带 [0.02533499,0.02636907]。
方差 ratio 均通过，但均值区间太宽；整体 harmonic campaign **没有 PASS**。
旧 block-only 确认报告曾给 y FAIL，修正后因 seed 间离散扩大为 INCONCLUSIVE；原报告保留。
复算文件为 `dmgmd-a3-current-statistics-recalc-a3-harmonic*-20261102-6.json`，包含完整旧/新结果；
每 seed 均值文件同时保留。总 K、逐位相容都不能替代逐方向混合证据，也不能据此断言数学上
永不收敛。有限最大 lag 与有限独立 seed 的 CI 仍是近似；不保证普遍遍历性。

<a id="nhc-20261102--实际命令与非通过记录"></a>
### 实际命令与非通过记录

每个执行 shell 先 `source ../env/md-mpi.sh`、`export PYTHONDONTWRITEBYTECODE=1`。
以下为实际参数，均由 `capture_command.py <记录名> -- <命令>` 保存开始/结束、返回码、环境和 stdout/stderr；
GPU 命令在沙箱外执行相同环境。构建的 configure/build 完整日志见 `configure-a3.log`、
`build-a3*.log`、`configure-a4.log`、`build-a4.log`。

```bash
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7
ctest --test-dir /tmp/dmgmd-a3-a6-20261102/build-a4 --output-on-failure
python3 tests/thermostats/fixtures/generate_nhc.py --reference-root ../gpumd-reference --output /tmp/dmgmd-a3-a6-20261102/nhc-oracle-regenerated.json
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --devices 0,1,2,3 --ensembles nvt_nhc --sections short negative --work-root /tmp/dmgmd-a3-a6-20261102/a3-short-r2
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --devices 2,4,5,7 --ensembles nvt_nhc --sections negative --work-root /tmp/dmgmd-a3-a6-20261102/a3-negative
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --devices 2,4,5,7 --ensembles nvt_nhc --sections statistics --timeout 1800 --work-root /tmp/dmgmd-a3-a6-20261102/a3-statistics
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a3/tests/dmgmd_autocorrelation python3 /tmp/dmgmd-a3-a6-20261102/reanalyze_kinetic.py --work-root /tmp/dmgmd-a3-a6-20261102/a3-statistics --output /tmp/dmgmd-a3-a6-20261102/a3-statistics-conservative-final.json
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a3/tests/dmgmd_autocorrelation python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --devices 2,4,5,7 --ranks 2 --parallel-seeds 2 --ensembles nvt_nhc --sections statistics --statistics-filter cluster_fallback --minimum-statistics-steps 40000 --timeout 1800 --work-root /tmp/dmgmd-a3-a6-20261102/a3-statistics-p2-continuation
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a3/tests/dmgmd_autocorrelation python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --devices 2,4,5,7 --ranks 1 --backends HostStaged --ensembles nvt_nhc --sections statistics --statistics-filter half-dt --minimum-statistics-steps 40000 --parallel-seeds 3 --timeout 1800 --work-root /tmp/dmgmd-a3-a6-20261102/a3-half-dt-continuation
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a3/tests/dmgmd_autocorrelation python3 tests/thermostats/check_velocity_statistics.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --devices 2,4,5,7 --minimum-steps 40000 --work-root /tmp/dmgmd-a3-a6-20261102/a3-velocity-continuation
python3 tests/thermostats/check_protocol.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --probe /tmp/dmgmd-a3-a6-20261102/build-a3/tests/libdmgmd_ensemble_factor_probe.so --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a3-protocol
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_nhc --sections short --timeout 180 --work-root /tmp/dmgmd-a3-a6-20261102/a5-nhc-final-short
python3 /tmp/dmgmd-a3-a6-20261102/compare_frozen_candidates.py
```

`a3-short-host-r2` 退出 1 是后续 CV 负例用了非法 interval=1，之前所有短程比较均 PASS；
改为合法但不支持的 interval=10 后，独立 `a3-negative-host` 18/18 PASS。
最初 GPUMD oracle 暴露多张 GPU，触发其小盒限制；修正为单可见 GPU 后正式重跑。
沙箱环境门的 UCX/CUDA 初始化失败与 host PASS 分别记录。harmonic 两轮命令非零退出及旧 CI
报告均保留，未把它们计作 NEP 统计 PASS 或 SKIP。

<a id="nhc-20261102--回滚与后续"></a>
### 回滚与后续

如需回退 A3，按 `before/` 和逐文件差异撤去 NHC compat、controller 链分支、两处 pre-force
hook 及 parser 对应放行；保留 A2 旧控制器行为和既有用户修改，不执行整库 reset。
BDP 与 NHC 共用 header/root 协议，回退时应审核共享依赖，不能直接覆盖整文件。
后续 A4/G-A 结论见 [A5](thermostat-validation.md#thermostat-matrix-20261102)，当前下一步以
[逐 Step 进度](../../plans/dmg-md-v1.0-progress.md)为准；A7、NPT、跨节点和 release 长程矩阵不由本报告放行。
未提交、未推送、未修改任何 reference；未运行范围不声称通过。


<a id="bdp-20261102"></a>

<a id="bdp-20261102--a4bdp-随机速度重标定实施与验收2026-11-02"></a>

## 2026-11-02：A4 BDP


<a id="bdp-20261102--结论与身份"></a>
### 结论与身份

**A4 DONE。** root 单一 RNG、实例 Gaussian cache、默认固定 seed 与严格跨 rank seed
字符串校验已实现。NEP4 双路径/后端重复、19 项 seed 合同、12 组八 seed KE 分布、
实际 NEP 三个速度方向及减半步长全部通过。BDP 对 pinned GPUMD 比较分布，不比较随机轨迹。
独立 harmonic K oracle 也通过，但额外轴向诊断整体 INCONCLUSIVE，不能称该诊断全部通过。
完整 G-A 矩阵见[A5](thermostat-validation.md#thermostat-matrix-20261102)，实现语义见[系综标准](../../standards/ensembles.md)。

revision `f06284408f12b6d89ed5a19434c44c958307af7b`、main dirty，未提交、未推送。
原始根 `/tmp/dmgmd-a3-a6-20261102/`；下文路径相对该目录。候选 `build-a4/dmg-md` 的
SHA-256 为 `0a51d5add72a454bab2dc5cd4bd19733abe1fb610365c7f9359d8b7f48549ff7`，
生产源码封存在 `a4-source-frozen/`，独立标量实施 worktree 为 `/tmp/dmgmd-a4-scalar-20261102/`。
构建 Release、GNU11.4/CUDA12.9.86/CMake3.28.3、C++17/CUDA17、`-O3 -DNDEBUG`、
架构 75/80/86/89/90；Open MPI5.0.10/UCX1.22.0；n4/RTX4090、driver580.173.02。
每个 shell 均 source `../env/md-mpi.sh`，GPU 用 2/4/5/7；环境/UUID/ldd 在 `environment-final.json`。
沙箱 CTest 22 pass + 1 GPU skip，host **23/23、0 skip**。CPU affinity 竞争的操作记录与
不作性能结论的说明见 NHC/A5 报告和 `affinity-adjustments.jsonl`。

<a id="bdp-20261102--代码与数值顺序"></a>
### 代码与数值顺序

`src/gpumd_compat/thermostat_svr.hpp/.cpp` 独立复现 pinned `src/integrate/svr_utilities.cuh`
的四个 host 函数，保留 GPL/Origin；上游注明算法来自 Bussi 网站及
G. Bussi 等 J. Chem. Phys. 126,014101 (2007)。原函数静态 `iset/gset` 改为 `SvrState`
成员，`gamdev(ia<1)` 的空错误占位改为抛错；有效输入公式、uniform draw 顺序和
`taut<=0.1` 分支保持不变。没有修改 NEP 数学或编译/链接 reference 源码。

`EnsembleController` 每段仅 root 分配 `mt19937` 和 cache。BDP 无 pre-force hook，
两 runtime 仍执行 VV1 → 位置/halo/force/ownership → VV2 → owned 全局 thermo → root
`resamplekin` → 广播状态/因子 → owned 速度缩放 → 输出。`ndeg=3N`、
`kk=T_inst*ndeg*kB*0.5`、`sigma=ndeg*kB*T_target*0.5`、`taut=Tdamp`，
factor=`sqrt(new_KE/old_KE)`；顺序与 pinned `ensemble_bdp.cu` 一致。

改动集中在 controller 头/实现、SVR compat、两 runtime 的已有 post-force hook、
`runtime_internal.hpp` 的 seed 预检和 root 因子协议、parser 放行、两份 CMake 和相应测试。
温度仍为 3N，ghost 不参与 KE。新 NVT 拒绝 CV/自适应 dt/非默认实验开关；old/new KE、
factor、温度和计数保护均在缩放前执行，root 失败经状态广播让 peers 统一退出。
NVE/Berendsen 的无状态分支、公式和通信不变；精确回归见 A5。

默认 seed=12345678，与上游 DEBUG 分支一致，有意偏离 release 的 wall-clock seed。
`DMGMD_ENSEMBLE_SEED` 为数字字符串、uint32 范围，逐 rank 比较原始 presence+字符串，
`01` 与 `1` 不能混用；不接收空串、符号、空白、后缀或溢出。每个 run 重置 RNG 和 cache，
故同 seed 的多段会重复随机序列；restart 不保存隐藏状态，是非连续新作业。
初始化的 seed 字符串通信不是逐步 RNG 状态广播；每成功步只多一次 2-double Bcast。

<a id="bdp-20261102--逐位-oracle确定性与错误合同"></a>
### 逐位 oracle、确定性与错误合同
| 验收 | 实际结果 / 证据 |
| --- | --- |
| SVR 独立 fixture | 16 case 全部 double 位模式一致；seed=12345678/0/42/UINT32_MAX、ndeg=3/6/9/48、taut=0.1/0.01/1/100；`fixtures/svr.json` |
| 独立再生成 | 只读提取 pinned 原函数，单独进程避免静态 cache 跨 case；全部 numerical cases 与冻结 fixture 相同；`independent-oracle-audit.json` |
| controller 单测 | 最终 133 案例；同 seed 重建/不同 seed/交错 controller cache/odd N/zero/nonfinite guard/非 root 无 RNG；host CTest 日志 |
| NEP4 短矩阵 | 11 配置，P1/P2/P4、fallback/domain、两后端；各两次 byte-identical，跨 rank 原 golden 容差通过；`a4-mpi-short-negative/` 的 short 记录 |
| seed 合同 | 19 检查，18 个普通作业加 4 个 rank-wrapper 作业，全部通过；`a4-seed-contract/bdp-contract-results.json` |
| 失败关闭 | 18 个基本负例，加 A5 trace 3 个；120 s 内 launcher 返回、非零退出、无最终快照；`a4-negative-fixed-fixture/`、`a5-trace-guard/` |
| 广播/账本 | 12 组合、24 stage、每 rank 每步仅一次相同因子位模式；calls+1、input+16 B、output+16P B；staging 和 M2a p2p 不增；`a4-protocol/protocol-results.json` |
| 多段/restart | 两段各 3 step 与新作业通过；A5 再完成各 variant/backend/rank 的新作业 smoke 与 P1→P4，均不作连续轨迹比较 |

seed 合同验证 unset 与显式默认值的 thermo/trajectory/restart 哈希一致；seed1/2 首个
缩放后 XYZ velocity 不同。第一行 thermo 是缩放前值，理应相同；第二行温度才检查差异。
非法输入为 `""`, `-1`, `+1`, ` 1`, `4294967296`, `1x`，各 P1/P2；
不同数字和 `01`/`1` 在 P2/P4 失败。全部有超时、launcher 返回和无最终输出的证据。
未用进程未挂起代替数值正确性，factor 位模式另由 MPI 探针验证。

<a id="bdp-20261102--多种子统计与步长检查"></a>
### 多种子统计与步长检查

正式数据使用已冻结 `manifest.json`（SHA
`84d3ea6fc94e6860eab730e0bcd0e3106e75954ed12ff43bcf3ab69233e60c9f`）。
8 seed=101..108，432 原子相互作用 cluster，显式独立 Gaussian 初速，不去质心/不做 CV。
每个 look 丢前 10%；预算 20k/40k/80k，dt=0.25 fs、Tdamp=40，block500，
mean±2%、Var±15%、ESS≥1000、complete blocks≥32。减半 dt 同步加倍 steps/Tdamp/block。
block 和独立 seed 间 Student-t half-width 取较大者；单 look 约 98.33% CI，三次 planned
looks 校正，不声称整个多矩阵的联合 95% 覆盖率。该保守分析修正在正式 BDP 采样前已生效；
NHC 先导旧报告和修正经过见 [A3](thermostat-validation.md#nhc-20261102)，未改误差带或 seed。
| 统计组 | 实际 steps/seed | mean T / CI (K) | Var(K)/target / CI | ESS K/variance | 结果 |
| --- | ---: | --- | --- | ---: | --- |
| `cluster_domain-p1-HostStaged` | 20000 | 299.91018 [298.97747, 300.84289] | 1.01140 [0.94823, 1.07458] | 2809/7936 | PASS |
| `cluster_domain-p1-CudaAware` | 20000 | 299.91018 [298.97747, 300.84289] | 1.01140 [0.94823, 1.07458] | 2809/7936 | PASS |
| `cluster_fallback-p2-HostStaged` | 20000 | 299.91066 [298.97754, 300.84377] | 1.01136 [0.94836, 1.07436] | 2807/7933 | PASS |
| `cluster_fallback-p2-CudaAware` | 20000 | 299.91066 [298.97754, 300.84377] | 1.01136 [0.94836, 1.07436] | 2807/7933 | PASS |
| `cluster_domain-p2-HostStaged` | 20000 | 299.91018 [298.97747, 300.84289] | 1.01140 [0.94823, 1.07458] | 2809/7936 | PASS |
| `cluster_domain-p2-CudaAware` | 20000 | 299.91018 [298.97747, 300.84289] | 1.01140 [0.94823, 1.07458] | 2809/7936 | PASS |
| `cluster_fallback-p4-HostStaged` | 20000 | 299.91082 [298.97737, 300.84426] | 1.01161 [0.94902, 1.07420] | 2808/7938 | PASS |
| `cluster_fallback-p4-CudaAware` | 20000 | 299.91082 [298.97737, 300.84426] | 1.01161 [0.94902, 1.07420] | 2808/7938 | PASS |
| `cluster_domain-p4-HostStaged` | 20000 | 299.91039 [298.97727, 300.84351] | 1.01145 [0.94832, 1.07458] | 2808/7933 | PASS |
| `cluster_domain-p4-CudaAware` | 20000 | 299.91039 [298.97727, 300.84351] | 1.01145 [0.94832, 1.07458] | 2808/7933 | PASS |
| `cluster_domain-p1-HostStaged-half-dt` | 40000 | 300.42874 [299.52566, 301.33183] | 1.02593 [0.95963, 1.09224] | 1897/7514 | PASS |
| `cluster_domain-p1-HostStaged-gpumd` | 20000 | 300.13794 [299.44921, 300.82668] | 1.03973 [0.98870, 1.09075] | 1870/6286 | PASS |

GPUMD 行同样使用锁定模型和独立初速；release RNG seed 由上游时钟生成，环境变量不能
覆盖它。原始命令、开始时间及全量输出保留，未把输入初速 seed 冒充其 thermostat seed。
两实现各自对 Gamma(3N/2,kBT) 的 mean/variance 门通过，统计对照不等于逐帧相同。

独立速度分量采样为 8 seed × 40000 step，前 10% 丢弃，每 50 step 输出；目标
`E[mean_i(m*v_axis²)]=kBT=0.025852029 eV`，variance=`2(kBT)²/N`。
20k 先导有 CI 较宽的 INCONCLUSIVE，按同 manifest 延长，没有替换初速或 seed。
| 轴 | mean A / CI (eV) | Var(A)/target / CI | ESS A/variance | 结果 |
| --- | --- | --- | ---: | --- |
| x | 0.02588237 [0.02571329, 0.02605146] | 1.00063 [0.91293, 1.08834] | 1842/4441 | PASS |
| y | 0.02593148 [0.02575608, 0.02610688] | 1.03385 [0.97985, 1.08786] | 1920/4274 | PASS |
| z | 0.02582984 [0.02574041, 0.02591926] | 1.00714 [0.95998, 1.05431] | 2235/4932 | PASS |

全量结果在 `a4-statistics/results.json`、`a4-velocity/velocity-results.json`，包含
burn、块数、每 seed 自相关时间、CI、所有 look，以及逐作业输入/输出 SHA。
全局重标定不随机化所有动量方向；本例通过不能证明所有零作用力/低维体系都遍历。
不以 force-free fixture 的短程 KE 结果单独证明 BDP 相空间混合。

独立解析参照采用原 manifest 的 24 个无约束 harmonic 模式，8 seed×200k，丢前20k，
固定单次95% CI及原误差带；正式采样前的范围记录在 `preregistered-bdp-harmonic.json`。
CPU sampler 使用 production controller 及独立可解势/VV，K target由解析正则矩给出，
不是以GPUMD为唯一科学oracle。`a4-harmonic/results.json` 中：
| 量 | mean / CI (eV) | variance ratio / CI | 结果 |
| --- | --- | --- | --- |
| K，target=0.310224348 | 0.31170930 [0.30896819,0.31445041] | 0.98572 [0.96083,1.01060] | PASS |
| mean_mv2_x | 0.02770440 [0.02321168,0.03219712] | 1.13063 [0.87055,1.39071] | INCONCLUSIVE |
| mean_mv2_y | 0.02306313 [0.02010222,0.02602404] | 0.81729 [0.64582,0.98877] | INCONCLUSIVE |
| mean_mv2_z | 0.02715980 [0.02551758,0.02880201] | 1.00748 [0.89581,1.11915] | INCONCLUSIVE |

K ESS为8753/14592（mean/variance）。轴mean target=0.025852029；它们的区间未完整落入
误差带，整体CPU harmonic命令退出1、INCONCLUSIVE，记录保留，未声称轴向普遍充分混合。
实际NEP分量的独立表格已通过，未用这个小模型替代它。
扩展sampler的测试代码加入BDP选项，默认NHC的200-step CSV与冻结A3 helper SHA相同
（`harmonic-helper-compatibility.json`）；仅重建CPU测试target，生产二进制SHA未变。

<a id="bdp-20261102--实际命令及失败尝试"></a>
### 实际命令及失败尝试

下列为实际命令，由 `capture_command.py` 保留原始命令/环境/返回码。
各 shell 先加载同一 `../env/md-mpi.sh`；GPU 命令在 host 执行。

```bash
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7
ctest --test-dir /tmp/dmgmd-a3-a6-20261102/build-a4 --output-on-failure
python3 tests/thermostats/fixtures/generate_svr.py --reference-root ../gpumd-reference --output /tmp/dmgmd-a3-a6-20261102/svr-oracle-regenerated.json
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ranks 1 --ensembles nvt_bdp --sections short --work-root /tmp/dmgmd-a3-a6-20261102/a4-p1-short
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_bdp --sections short negative --timeout 120 --work-root /tmp/dmgmd-a3-a6-20261102/a4-mpi-short-negative
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_bdp --sections negative --timeout 120 --work-root /tmp/dmgmd-a3-a6-20261102/a4-negative-fixed-fixture
python3 tests/thermostats/check_bdp_contract.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a4-seed-contract
python3 tests/thermostats/check_protocol.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --probe /tmp/dmgmd-a3-a6-20261102/build-a4/tests/libdmgmd_ensemble_factor_probe.so --ensemble nvt_bdp --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a4-protocol
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a4/tests/dmgmd_autocorrelation python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_bdp --sections statistics --parallel-seeds 4 --timeout 1800 --work-root /tmp/dmgmd-a3-a6-20261102/a4-statistics
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a4/tests/dmgmd_autocorrelation python3 tests/thermostats/check_velocity_statistics.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --ensemble nvt_bdp --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a4-velocity
python3 tests/thermostats/check_restarts.py --sources /tmp/dmgmd-a3-a6-20261102/a5-nhc-final-short /tmp/dmgmd-a3-a6-20261102/a4-mpi-short-negative /tmp/dmgmd-a3-a6-20261102/a5-variants --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a5-restarts
cmake --build /tmp/dmgmd-a3-a6-20261102/build-a4 --target dmgmd_harmonic_sampler -j8
python3 /tmp/dmgmd-a3-a6-20261102/verify_harmonic_compatibility.py
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a4/tests/dmgmd_autocorrelation python3 tests/thermostats/run_harmonic.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/tests/dmgmd_harmonic_sampler --ensemble nvt_bdp --work-root /tmp/dmgmd-a3-a6-20261102/a4-harmonic
```

`a4-mpi-short-negative-host` 所有 short 矩阵与前五个负例通过，之后零 KE fixture 用了
产品拒绝的 N=1，整体退出1；修为两个相距30 Å的静止原子后，单独负例矩阵18/18通过。
保留原失败文件，未改生产 guard 来迁就测试。restart 源选择器最初把比较记录误当作执行记录，
在 CPU 阶段失败；补齐 cwd/input guard 后54个新作业全部通过。均未覆盖原 FAIL。

<a id="bdp-20261102--未覆盖回滚与声明"></a>
### 未覆盖、回滚与声明

本轮仅单节点 RTX4090/固定工具链；跨节点、其他架构、NPT/非NEP、CV/自适应/实验 overlap
组合不由本报告放行。BDP restart 与分段随机流不连续，跨工具链 RNG 位序列不保证。
性能 trial 与 release 长程矩阵未执行，保留 PENDING-D4 与原 release 待办。

回退 A4 时按 `before/`/A3 封存差异移除 SVR、BDP controller 分支和其 seed 放行，保留
A3 链与 root 公共协议；不要整文件覆盖共享 controller 或用户文档。下一阶段由 G-A 决定，
本轮不启动 B/C。未提交、未推送、未修改任何 reference；未运行范围不冒充 PASS。


<a id="thermostat-matrix-20261102"></a>

<a id="thermostat-matrix-20261102--a5恒温器双路径mpi-与后端矩阵2026-11-02"></a>

## 2026-11-02：A5 双路径、MPI 与后端矩阵


<a id="thermostat-matrix-20261102--结论与-gate-范围"></a>
### 结论与 Gate 范围

**A5 DONE，G-A 在本次 NEP/单节点验收范围内通过；A6 文档核对见
[A6 记录](thermostat-validation.md#thermostat-docs-20261102)。** A0–A2 基线和共享控制器证据由原报告保持；
[A3 NHC](thermostat-validation.md#nhc-20261102)与[A4 BDP](thermostat-validation.md#bdp-20261102)的实现/统计逐项追溯。
本轮未启动 B/C，也未放行完整 v1.0 发布、NPT、非 NEP 或跨节点。

必需的 NEP 矩阵无 FAIL/SKIP：52 个短程配置、同配置52次重复、54个新作业 restart，
NHC11组和BDP12组 KE、两种恒温器各三轴速度矩、两份协议矩阵、失败关闭、旧路径回归通过。
额外独立 harmonic 诊断中两种恒温器总 K PASS、轴向 mean INCONCLUSIVE 单列于 A3/A4，不把它计作矩阵
通过或普遍遍历性证明。原先导/脚本失败记录全部保留，详见下文。

revision `f06284408f12b6d89ed5a19434c44c958307af7b`、main dirty；未提交/推送。
原始根 `/tmp/dmgmd-a3-a6-20261102/`，下文证据路径相对该目录。
整合候选 `build-a4/dmg-md` SHA `0a51d5add72a454bab2dc5cd4bd19733abe1fb610365c7f9359d8b7f48549ff7`。
NHC长期数据属于冻结 A3 `d22060a9463f6a324a06876ffd01a5659942f27a6bc95a92c8b36739b989afae`；
最终候选对其 30 stage、120 科学文件 SHA 全同，host 位模式 oracle 和代码审查确认相同链表达式。

工具链 Release/GNU11.4/CUDA12.9.86/CMake3.28.3、C++17/CUDA17、O3、NDEBUG、
架构75/80/86/89/90；Open MPI5.0.10/UCX1.22.0；n4、8×RTX4090、driver580.173.02。
使用同一 `../env/md-mpi.sh`，完整环境和设备UUID见 `environment-final.json`。
后续矩阵设备为2/4/5/7；host环境门P4真实device collectives/p2p通过。
CPU默认绑定竞争的调整限定为本次二进制 PID/thread，分配128–175中的不同核，完整时间/前后
affinity在 `affinity-adjustments.jsonl`；没有性能 trial/采用结论，D4仍PENDING。

<a id="thermostat-matrix-20261102--输入与路由"></a>
### 输入与路由

NEP4：2016-C（24.99×21.42×21.42 Å）用于自然 M1 fallback；5184-C
（64.26×21.42×21.42 Å）用于 M2a。`d_dep=8`、`d_coord=16`，P2/P4 大盒 slab 分别
32.13/16.065 Å，小盒不满足 M2a。每条日志必须有 `DMGMD_DOMAIN mode`、实际 backend、
GPU绑定和 layout 证明，没有强制路由测试开关。

NEP5 使用 long-NVE 锁定的 NEP4→5 等价转换（势 SHA
`685fcc` 前缀，完整值在 `a5-variants/results.json`），小盒复用既有 carbon_crystal，
大盒复用锁定5184-C输入。BaTiO3-ZBL复用现有5000原子fixture，P2为M2a、P4为fallback，
据实际判据记录；并非所有P>1均算域分解。NEP5/ZBL补充矩阵的P1采用HostStaged，
P2/P4覆盖双后端；本轮没有将其扩写为所有变体/参数的完整 C5 联合门。
输入/势SHA由 thermostat、baseline、long-NVE manifest锁定，既有golden与容差不变。

<a id="thermostat-matrix-20261102--实际短程矩阵"></a>
### 实际短程矩阵

下表每配置执行两次，thermo/XYZ/restart科学文件SHA一致，另用原baseline容差比较P1与MPI。
NHC两组NEP4 P1和一组NEP5 P1另与独立GPUMD短轨迹比较；BDP随机轨迹只在自身固定seed
之间比较，GPUMD只做统计。`mode_detail`、axis、slab、每rank owned/ghost和详细哈希在各
`results.json` 的执行记录中，不由此表推测。
| case | ensemble | potential | P | data plane | backend | steps | seed | 结果 | 首次执行证据 |
| --- | --- | --- | ---: | --- | --- | ---: | ---: | --- | --- |
| fallback | nvt_nhc | NEP4 | 1 | m1-fallback | HostStaged | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-fallback-p1-HostStaged-a` |
| fallback | nvt_nhc | NEP4 | 1 | m1-fallback | CudaAware | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-fallback-p1-CudaAware-a` |
| fallback | nvt_nhc | NEP4 | 2 | m1-fallback | HostStaged | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-fallback-p2-HostStaged-a` |
| fallback | nvt_nhc | NEP4 | 2 | m1-fallback | CudaAware | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-fallback-p2-CudaAware-a` |
| fallback | nvt_nhc | NEP4 | 4 | m1-fallback | HostStaged | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-fallback-p4-HostStaged-a` |
| fallback | nvt_nhc | NEP4 | 4 | m1-fallback | CudaAware | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-fallback-p4-CudaAware-a` |
| domain | nvt_nhc | NEP4 | 1 | m1-fallback | HostStaged | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-domain-p1-HostStaged-a` |
| domain | nvt_nhc | NEP4 | 2 | m2a | HostStaged | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-domain-p2-HostStaged-a` |
| domain | nvt_nhc | NEP4 | 2 | m2a | CudaAware | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-domain-p2-CudaAware-a` |
| domain | nvt_nhc | NEP4 | 4 | m2a | HostStaged | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-domain-p4-HostStaged-a` |
| domain | nvt_nhc | NEP4 | 4 | m2a | CudaAware | 200 | 1 | PASS | `a5-nhc-final-short/short-nvt_nhc-domain-p4-CudaAware-a` |
| fallback | nvt_bdp | NEP4 | 1 | m1-fallback | HostStaged | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-fallback-p1-HostStaged-a` |
| fallback | nvt_bdp | NEP4 | 1 | m1-fallback | CudaAware | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-fallback-p1-CudaAware-a` |
| fallback | nvt_bdp | NEP4 | 2 | m1-fallback | HostStaged | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-fallback-p2-HostStaged-a` |
| fallback | nvt_bdp | NEP4 | 2 | m1-fallback | CudaAware | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-fallback-p2-CudaAware-a` |
| fallback | nvt_bdp | NEP4 | 4 | m1-fallback | HostStaged | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-fallback-p4-HostStaged-a` |
| fallback | nvt_bdp | NEP4 | 4 | m1-fallback | CudaAware | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-fallback-p4-CudaAware-a` |
| domain | nvt_bdp | NEP4 | 1 | m1-fallback | HostStaged | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-domain-p1-HostStaged-a` |
| domain | nvt_bdp | NEP4 | 2 | m2a | HostStaged | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-domain-p2-HostStaged-a` |
| domain | nvt_bdp | NEP4 | 2 | m2a | CudaAware | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-domain-p2-CudaAware-a` |
| domain | nvt_bdp | NEP4 | 4 | m2a | HostStaged | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-domain-p4-HostStaged-a` |
| domain | nvt_bdp | NEP4 | 4 | m2a | CudaAware | 200 | 1 | PASS | `a4-mpi-short-negative/short-nvt_bdp-domain-p4-CudaAware-a` |
| nep5-carbon-fallback | nvt_nhc | NEP5 | 1 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_nhc-p1-HostStaged-repeat-a` |
| nep5-carbon-fallback | nvt_nhc | NEP5 | 2 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_nhc-p2-HostStaged-repeat-a` |
| nep5-carbon-fallback | nvt_nhc | NEP5 | 2 | m1-fallback | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_nhc-p2-CudaAware-repeat-a` |
| nep5-carbon-fallback | nvt_nhc | NEP5 | 4 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_nhc-p4-HostStaged-repeat-a` |
| nep5-carbon-fallback | nvt_nhc | NEP5 | 4 | m1-fallback | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_nhc-p4-CudaAware-repeat-a` |
| nep5-carbon-domain | nvt_nhc | NEP5 | 1 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_nhc-p1-HostStaged-repeat-a` |
| nep5-carbon-domain | nvt_nhc | NEP5 | 2 | m2a | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_nhc-p2-HostStaged-repeat-a` |
| nep5-carbon-domain | nvt_nhc | NEP5 | 2 | m2a | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_nhc-p2-CudaAware-repeat-a` |
| nep5-carbon-domain | nvt_nhc | NEP5 | 4 | m2a | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_nhc-p4-HostStaged-repeat-a` |
| nep5-carbon-domain | nvt_nhc | NEP5 | 4 | m2a | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_nhc-p4-CudaAware-repeat-a` |
| nep-zbl-batio3 | nvt_nhc | NEP-ZBL | 1 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_nhc-p1-HostStaged-repeat-a` |
| nep-zbl-batio3 | nvt_nhc | NEP-ZBL | 2 | m2a | HostStaged | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_nhc-p2-HostStaged-repeat-a` |
| nep-zbl-batio3 | nvt_nhc | NEP-ZBL | 2 | m2a | CudaAware | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_nhc-p2-CudaAware-repeat-a` |
| nep-zbl-batio3 | nvt_nhc | NEP-ZBL | 4 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_nhc-p4-HostStaged-repeat-a` |
| nep-zbl-batio3 | nvt_nhc | NEP-ZBL | 4 | m1-fallback | CudaAware | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_nhc-p4-CudaAware-repeat-a` |
| nep5-carbon-fallback | nvt_bdp | NEP5 | 1 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_bdp-p1-HostStaged-repeat-a` |
| nep5-carbon-fallback | nvt_bdp | NEP5 | 2 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_bdp-p2-HostStaged-repeat-a` |
| nep5-carbon-fallback | nvt_bdp | NEP5 | 2 | m1-fallback | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_bdp-p2-CudaAware-repeat-a` |
| nep5-carbon-fallback | nvt_bdp | NEP5 | 4 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_bdp-p4-HostStaged-repeat-a` |
| nep5-carbon-fallback | nvt_bdp | NEP5 | 4 | m1-fallback | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-fallback-nvt_bdp-p4-CudaAware-repeat-a` |
| nep5-carbon-domain | nvt_bdp | NEP5 | 1 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_bdp-p1-HostStaged-repeat-a` |
| nep5-carbon-domain | nvt_bdp | NEP5 | 2 | m2a | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_bdp-p2-HostStaged-repeat-a` |
| nep5-carbon-domain | nvt_bdp | NEP5 | 2 | m2a | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_bdp-p2-CudaAware-repeat-a` |
| nep5-carbon-domain | nvt_bdp | NEP5 | 4 | m2a | HostStaged | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_bdp-p4-HostStaged-repeat-a` |
| nep5-carbon-domain | nvt_bdp | NEP5 | 4 | m2a | CudaAware | 100 | 1 | PASS | `a5-variants/nep5-carbon-domain-nvt_bdp-p4-CudaAware-repeat-a` |
| nep-zbl-batio3 | nvt_bdp | NEP-ZBL | 1 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_bdp-p1-HostStaged-repeat-a` |
| nep-zbl-batio3 | nvt_bdp | NEP-ZBL | 2 | m2a | HostStaged | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_bdp-p2-HostStaged-repeat-a` |
| nep-zbl-batio3 | nvt_bdp | NEP-ZBL | 2 | m2a | CudaAware | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_bdp-p2-CudaAware-repeat-a` |
| nep-zbl-batio3 | nvt_bdp | NEP-ZBL | 4 | m1-fallback | HostStaged | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_bdp-p4-HostStaged-repeat-a` |
| nep-zbl-batio3 | nvt_bdp | NEP-ZBL | 4 | m1-fallback | CudaAware | 100 | 1 | PASS | `a5-variants/nep-zbl-batio3-nvt_bdp-p4-CudaAware-repeat-a` |

输出均按稳定global ID恢复N条；ghost不输出、不积分。覆盖owned KE的证据包括P1对照、
跨rank温度/轨迹、空owned rank的协议fixture及生产kernel写域审查。

<a id="thermostat-matrix-20261102--统计错误和-restart-门"></a>
### 统计、错误和 restart 门
| 类别 | 结果 | 证据 |
| --- | --- | --- |
| NHC KE | 11组最终PASS：P1、M1 P2/P4、M2a P2/P4双后端+half-dt；3初始条件；预定20k/40k/80k | A3统计表及 `a3-statistics-conservative-final.json`、两个continuation目录 |
| BDP KE | 12组最终PASS：同矩阵+half-dt+GPUMD；每组8seed，最少20k | A4统计表、`a4-statistics/results.json` |
| velocity moments | NHC/BDP均8seed×40k，x/y/z mean与variance CI全部PASS | `a3-velocity-continuation/`、`a4-velocity/` |
| 基本负例 | 各18：CV/adaptive/partition/overlap/schedule/zeroKE ×P1/2/4；全rank有界退出、无最终快照 | `a3-negative/`、`a4-negative-fixed-fixture/` |
| trace负例 | 两种恒温器×P1/2/4共6，全PASS | `a5-trace-guard/` |
| BDP seed合同 | 19检查PASS，含格式错误、不同数值/前导零的rank不一致 | `a4-seed-contract/bdp-contract-results.json` |
| 新作业restart | 52同配置+NHC/BDP各一次P1→P4，共54个5-step作业PASS；不主张连续性 | `a5-restarts/results.json` |
| 最终host CTest | 23/23、0skip；沙箱GPU1skip另列 | `a4-ctest-host.*`、`a4-ctest-sandbox.*` |

统计fixture与物理量定义、CI修正时间、全部先导及更保守CI的方法限制见A3/A4；不同动量方向
与3N独立自由度不混淆。单组每个moment的三次looks校正不等于整个矩阵联合95%覆盖率。
没有以Berendsen涨落或零作用力BDP短轨迹证明正则采样。步长减半保持物理耦合/采样时间。

<a id="thermostat-matrix-20261102--实际通信协议与旧路径零变化"></a>
### 实际通信协议与旧路径零变化

两种恒温器各12个几何/rank/backend组合、各24stage，MPI_Bcast探针观察所有rank因子
double位模式；NHC每步2次、BDP1次，状态均成功且因子一致。
相同force-free输入与Berendsen逐步账本之差：
| 系综 | collective calls/step | MPI input B | MPI output B | HostStaged staging D2H/H2D B |
| --- | ---: | ---: | ---: | ---: |
| NHC | +3 | +32+8P | +40P | 各+8P |
| BDP | +1 | +16 | +16P | 0 |

CudaAware对应额外device collective staging为0；新host scalar读取不算MPI staging，
详见[MPI合同](../../standards/mpi-runtime.md)。M2a逐rank p2p、halo/migration/control无
额外差异。seed一致性是启动控制通信；成功步不广播RNG/链状态。
这套探针独立观察Bcast，账本仍是生产API buffer字节，不冒充物理网络流量或独立全量PMPI。

旧路径最终验证：
| 回归 | 实际结果 |
| --- | --- |
| 单卡committed golden | 四case全PASS；相对A2的20个科学文件SHA全同 |
| M1 differential | P1/P2/P4、HostStaged/CudaAware全PASS；NVE max excursion=1.746927e-6 eV/atom，slope=3.238564e-6 eV/(atom fs) |
| M2a selected regression | lattice/crossings/partition_nvt ×P2/P4×两后端12主配置，加runner固定P1/错误/缓存/计时检查；全PASS |
| A2/最终精确A/B | 50stage、181科学文件、231全局逐步、160rank逐步、30段摘要全同；同输入/同rank，无排除项 |
| long-NVE smoke | 七配置、70stage，实际sections=short,long,replay,restart,nvt；兼容势按manifest仅static/short，三主物理势执行适用全部section；不是nightly/release通过声明 |

精确比较结果为 `legacy-golden-a2-final-bitwise.json`、`legacy-mpi-a2-final-bitwise.json`；
M1 work=`/tmp/dmgmd-mpi-differential-ucse0cw2`，golden work=`/tmp/dmgmd-gpumd-baseline-c9pbm__j`，
domain work=`a5-legacy-domain-final/`，long-NVE work=`/tmp/dmgmd-long-nve-smoke-3eznhowi`。
两个新增host compat模块已与独立上游提取oracle重算一致；未触碰既有NEP kernel。

<a id="thermostat-matrix-20261102--实际命令"></a>
### 实际命令

每个shell先 `source ../env/md-mpi.sh`、`export PYTHONDONTWRITEBYTECODE=1`。
下列为实际命令；相应 `.json/.stdout/.stderr` 保存开始/结束、精确argv、环境和返回码。
较长统计命令的逐seed作业另在 results 中保存独立命令和输入/输出SHA。

```bash
python3 tests/mpi/check_environment.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7
ctest --test-dir /tmp/dmgmd-a3-a6-20261102/build-a4 --output-on-failure
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_nhc --sections short --timeout 180 --work-root /tmp/dmgmd-a3-a6-20261102/a5-nhc-final-short
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_bdp --sections short negative --timeout 120 --work-root /tmp/dmgmd-a3-a6-20261102/a4-mpi-short-negative
python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_bdp --sections negative --timeout 120 --work-root /tmp/dmgmd-a3-a6-20261102/a4-negative-fixed-fixture
python3 /tmp/dmgmd-a3-a6-20261102/check_trace_guard.py
python3 tests/thermostats/check_bdp_contract.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a4-seed-contract
python3 tests/thermostats/check_protocol.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a3/dmg-md --probe /tmp/dmgmd-a3-a6-20261102/build-a3/tests/libdmgmd_ensemble_factor_probe.so --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a3-protocol
python3 tests/thermostats/check_protocol.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --probe /tmp/dmgmd-a3-a6-20261102/build-a4/tests/libdmgmd_ensemble_factor_probe.so --ensemble nvt_bdp --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a4-protocol
python3 tests/thermostats/check_variants.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a5-variants
python3 tests/thermostats/check_restarts.py --sources /tmp/dmgmd-a3-a6-20261102/a5-nhc-final-short /tmp/dmgmd-a3-a6-20261102/a4-mpi-short-negative /tmp/dmgmd-a3-a6-20261102/a5-variants --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a5-restarts
python3 tests/baseline/run_baselines.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --device 5 --keep-work
python3 tests/mpi/run_mpi_differential.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --ranks 1,2,4 --backends HostStaged,CudaAware --devices 2,4,5,7 --keep-work
python3 tests/mpi/run_mpi_domain.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --ranks 2,4 --backends HostStaged,CudaAware --devices 2,4,5,7 --cases lattice,crossings,partition_nvt --partition-modes off --schedule split --work-root /tmp/dmgmd-a3-a6-20261102/a5-legacy-domain-final
python3 /tmp/dmgmd-a3-a6-20261102/compare_frozen_candidates.py
python3 tests/long_nve/run_long_nve.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 5 --profile smoke --sections short,long,replay,restart,nvt --retries 0 --report /tmp/dmgmd-a3-a6-20261102/a5-long-nve-final-report.json --keep-work --ui plain
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a4/tests/dmgmd_autocorrelation python3 tests/thermostats/run_thermostats.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --devices 2,4,5,7 --ensembles nvt_bdp --sections statistics --parallel-seeds 4 --timeout 1800 --work-root /tmp/dmgmd-a3-a6-20261102/a4-statistics
env DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a4/tests/dmgmd_autocorrelation python3 tests/thermostats/check_velocity_statistics.py --candidate /tmp/dmgmd-a3-a6-20261102/build-a4/dmg-md --ensemble nvt_bdp --devices 2,4,5,7 --work-root /tmp/dmgmd-a3-a6-20261102/a4-velocity
```

<a id="thermostat-matrix-20261102--非通过尝试保护与回滚"></a>
### 非通过尝试、保护与回滚

原失败尝试没有删除：沙箱CUDA/UCX隔离；首轮GPUMD暴露多GPU；NHC CV负例interval=1；
BDP零KE fixture N=1；velocity分析字段映射；restart选择器将comparison误当execution；
long-NVE用不同section续跑被checkpoint合同拒绝；旧harmonic block-only非通过。
前六类修正仅涉及测试调用/fixture/分析，均用新命名记录重跑。统计CI改为seed-aware后
NEP不确定组按预定预算延长，未放宽golden/误差带，未换seed。harmonic仍INCONCLUSIVE。

最终逐文件保护、三个reference及旧二进制不变的核验见 `final-ownership.json`；所有执行与
输出的封存索引见 `evidence-manifest.json`、`evidence-sha256.json`。这些是在完成统计与文档后
生成的终检，不能用当时尚未生成的索引声称先行通过。
回滚使用开工 `before/`、A3/A4源码封存和逐文件差异，保留原用户修改；共享controller/协议须
按分支审核，不能整库reset。下一步为B0压力oracle，B1/G-B等仍按主计划推进。
未提交、未推送、未修改reference；跨节点、其他GPU栈、release长程和性能门均未执行。


<a id="thermostat-docs-20261102"></a>

<a id="thermostat-docs-20261102--a6恒温器文档与终检2026-11-02"></a>

## 2026-11-02：A6 文档与终检


<a id="thermostat-docs-20261102--结论"></a>
### 结论

**A6 DONE。** 文档与最终 NHC/BDP 实现及 A3/A4/A5 实测一致；新测试 CLI/语法和本轮
Markdown 本地链接终检通过。G-A 只放行已记录的 NEP/单节点范围，下一步为 B0；
未执行 B/C、跨节点、release 长程或 D4 性能门。

revision `f06284408f12b6d89ed5a19434c44c958307af7b`、main dirty；未提交、未推送。
原始根 `/tmp/dmgmd-a3-a6-20261102/`。A6 未更改生产运行时；最终二进制和13个生产构建
输入与 `a4-source-frozen/source-sha256.json` 一致。A3/A4 的实现、CMake 和辅助测试变更
分别由对应阶段记录，不把它们混作 A6 文档改动。

<a id="thermostat-docs-20261102--文档与事实源"></a>
### 文档与事实源
| 文件 | 固定的现行语义 |
| --- | --- |
| [ensembles.md](../../standards/ensembles.md) | 四种系综语法；T1/T2 K、Tdamp步数；先算fraction再乘温差；3N；NHC质量固定于T1；BDP seed/cache/root；输出时点、分段和restart |
| [compatibility-matrix.md](../../standards/compatibility-matrix.md) | NHC/BDP移入支持白名单；正固定步长、CV/自适应/实验选项限制；M2a不支持NPT/非NEP；restart明确非连续 |
| [mpi-runtime.md](../../standards/mpi-runtime.md) | NHC前置owned KE及两次root因子广播、BDP一次广播；成功步载荷/启动seed/错误路径分别说明 |
| [thermo-demand.md](../../standards/thermo-demand.md) | 非NVE每步反馈；NHC额外前置KE不属于可跳过的8-double post-force thermo |
| [architecture-decisions.md](../../standards/architecture-decisions.md) | 同步显式系综白名单，保留原用户修改和其他架构合同 |
| [golden-test-standard.md](../../standards/golden-test-standard.md) | 新标量/统计CTest与独立GPU验收入口；23项注册，阈值仍以manifest为准 |
| [测试README](../../../tests/thermostats/README.md) | 环境、构建、短程/错误/协议/variants/restart/分布/CPU oracle命令；所有复现命令明确标注“示例” |
| [主README](../../../README.md)、[文档索引](../../README.md)、[当前状态](../current.md) | 用户能力、证据、入口和限制可追溯 |
| [开发流程](../../plans/dmg-md-v1.0-development-workflow.md)、[逐Step进度](../../plans/dmg-md-v1.0-progress.md) | A3–A6完成摘要、G-A、下一步B0；已完成重复实施细节迁入标准/status |

MPI、thermo、架构和测试标准的窄范围同步用于清除与新实现冲突的旧白名单/调用计数，
没有改动 O 系列默认调度或其他用户文档内容。统计证据分别见
[A3](thermostat-validation.md#nhc-20261102)、[A4](thermostat-validation.md#bdp-20261102)、[A5](thermostat-validation.md#thermostat-matrix-20261102)，
不维护第二套测试参数或容差。

以下关键核对均基于实际源码和结果：NHC在VV1前而不是后执行前半步；两路径只计owned；
输出温度保持3N；ramp末步使用steps-1；NHC质量不跟随ramp；BDP没有pre-force KE；
只有root取样；seed原始字符串/presence必须一致；unset与显式默认值的独立作业相同；
每段重置RNG/cache会重复噪声；thermo在末次缩放前、XYZ/restart velocity在缩放后。
restart只启动新作业，不承诺隐藏状态连续性。

<a id="thermostat-docs-20261102--统计结论与限制"></a>
### 统计结论与限制

NHC11组、BDP12组NEP KE及各自8seed×40k的NEP三轴矩均通过注册门；独立harmonic
K oracle也通过。附加harmonic轴向mean在固定预算内INCONCLUSIVE的结果保留，
两份报告均醒目标注；不以总K、逐位oracle或短轨迹把它升级成遍历性证明。
NHC先导的block-only CI修正为更保守的seed-aware CI后，所有NEP组复算，预定预算内延长；
原误差界、置信概率、seed、burn和block不变。方法修正的时间和旧报告均公开，
不声称所有分析细节都在先导前冻结。多矩阵联合95%覆盖率没有被声称。

<a id="thermostat-docs-20261102--检查与实际命令"></a>
### 检查与实际命令

`a6-cli-audit.json` 记录受支持环境中的实际命令；`test-cli-audit.json` 保存 Python 版本、
14个Python源文件AST和13个CLI help的参数/返回码/输出，全部退出0。
CPU sampler增加BDP选项后，默认NHC 200-step CSV与冻结A3逐字节相同，生产候选SHA不变；
`a4-harmonic-helper-compatibility.*` 保存该检查。

`local-link-audit.json` 逐条检查本轮新增/修改Markdown的本地inline链接和heading fragment，
全部存在；外部URL没有在该检查中作网络可达性承诺。`git diff --check` 通过。
初轮检查器未识别既有 `o10` HTML anchor而误报两条链接；补齐显式HTML anchor识别后
重新检查，保留 `local-link-audit-initial.json` 和原非零capture，未修改用户已有链接。
本地链接在所有报告、进度和索引更新后复核，CLI示例不冒充已运行的精确命令；实际GPU/MPI
命令完整保存在A3/A4/A5记录及原始capture/result文件中。

```bash
source ../env/md-mpi.sh
export PYTHONDONTWRITEBYTECODE=1
python3 /tmp/dmgmd-a3-a6-20261102/check_test_cli.py
python3 /tmp/dmgmd-a3-a6-20261102/check_document_links.py
git diff --check
python3 /tmp/dmgmd-a3-a6-20261102/final_audit.py
python3 /tmp/dmgmd-a3-a6-20261102/seal_evidence.py
```

上面是本轮实际终检脚本；它们创建新证据文件并拒绝覆盖，不能原位重复运行来覆盖原报告。
文档复现示例使用新的/tmp路径；既有参考源和golden不应作为输出目的地。

<a id="thermostat-docs-20261102--文件保护回滚与声明"></a>
### 文件保护、回滚与声明

`final-ownership.json` 核对开工253文件：仅获准实现/测试/相关文档叠加修改，其他用户内容
哈希保持；A0–A2记录、原build/A2候选和冻结A3候选不变。三个reference的revision、Git状态
及15,461个既有可读文件（含原GPUMD ignored产物）哈希/路径集合相同，未新增输出。
另4个原有tracked断链符号链接的链接文本与pinned Git blob相同，不混入可读文件哈希数。
最终源码、命令、manifest与科学输出的SHA索引在 `evidence-sha256.json`，汇总在
`evidence-manifest.json`；完整性索引在终检后生成，报告不嵌入其hash形成自引用。

回滚文档时以开工 `before/` 的逐文件差异撤销本轮段落，保留原dirty用户修改；不要用整库
reset。代码回滚边界见A3/A4。没有提交、推送、更新golden或放宽容差，也没有修改reference。


<a id="thermostat-reference-controls-20261103"></a>

<a id="thermostat-reference-controls-20261103--harmonic-恒温器跨引擎对照2026-11-03"></a>

## 2026-11-03：跨引擎 harmonic 对照


状态：**实验与独立审计完成；完整 harmonic 验收仍为 INCONCLUSIVE。**

<a id="thermostat-reference-controls-20261103--结论"></a>
### 结论

维护者要求用 GPUMD/LAMMPS 做同一实验；若任一有效对照完整 PASS 而 DMG-MD 未 PASS，就修改 DMG-MD 并按原门槛重测。本轮按这个较严格的条件执行。

结果没有出现这种差异：DMG-MD、锁定 GPUMD 的独立源码 driver、GPUMD 原生 GPU 积分器测试接口和原生 LAMMPS，在原定 NHC/BDP 初轮及 NHC 确认轮中，总动能均值/方差均 PASS，三个方向的完整统计均为 INCONCLUSIVE。没有任何一组完整 harmonic PASS，也没有单个矩被判 FAIL。**对照的一致非通过不能改写为 harmonic PASS。**

因此未修改 DMG-MD 生产恒温器、NEP 或 MPI 实现。新增的是可复现对照工具、测试并行入口和证据；原 2026-11-02 非通过记录保留。现有 G-A 的 NEP/单节点限定范围不因本轮外推。

<a id="thermostat-reference-controls-20261103--身份环境与原始证据"></a>
### 身份、环境与原始证据

- 主仓库 HEAD：`f06284408f12b6d89ed5a19434c44c958307af7b`，沿用既有 dirty workspace；未提交、未推送。

- GPUMD：`9d23496e41319b9e2af5221a7df6285387401d1e`；LAMMPS：`611ca3b7f8525ba802373b04f9e3d632d515f7e3`。三个 reference 均按只读合同保护。

- GNU C++ 11.4.0、CMake 3.28.3、CUDA 12.9；GPU 为单节点 RTX4090。每个 shell 先加载 `../env/md-mpi.sh`，host Open MPI 5.0.10 / UCX 1.22.0 环境门 P2 通过。LAMMPS 用无源补丁的 CPU-only Release、MPI STUBS，启用 EXTRA-FIX、关闭 Kokkos/OpenMP。此处不做性能比较。

- 原始证据根：`/tmp/dmgmd-reference-controls-20261103/`。`comparison-registration.json` 固定本轮对照条件；`comparison.json` 保存 12 组全部矩、96 份 CSV 的 SHA/样本数及修复触发判定。`audit_comparison.py` 独立检查所有原始 SHA、样本数、目标、CI/ESS/块条件，并重新判定 PASS/FAIL/INCONCLUSIVE。
| 可执行文件 | SHA-256 |
| --- | --- |
| 当前源码独立编译 DMG-MD CPU sampler；与冻结 A4 二进制完全相同 | `6f681f95ea200aeee29e8f823886d123c960426d0793cf16594c280459206c13` |
| GPUMD 原版独立 CMake 构建 | `c2d8bf12d82226fae17dd6bb0175102862954da6b7bbf1f6d9a991761c46c26c` |
| GPUMD 原生 GPU 测试适配器 | `923a005815d0c113abf491fa03ffacdd3e6c7678ef040a9a6ec2f9f9cecb2da4` |
| LAMMPS CPU / EXTRA-FIX | `8c86af295450c5f32493b7160179f236f2408c6dabc571e3bcc26d0cf687d3ac` |

GPUMD 源码 driver 的实际抽取 C++ SHA 为 `cbb88feac4952813419a1bf7f44311da1d6c22ca3334c455b5940e23b812d321`；函数体从锁定源码抽取，未链接 DMG-MD controller。源码 driver 是独立算法对照，不冒充完整 GPUMD runtime。

<a id="thermostat-reference-controls-20261103--固定实验合同"></a>
### 固定实验合同

第一事实源仍是 [manifest.json](../../../tests/thermostats/manifest.json) 和 [harmonic-confirmation.json](../../../tests/thermostats/harmonic-confirmation.json)，没有改动它们或分析器：
| 文件 | SHA-256 |
| --- | --- |
| 初轮 manifest | `84d3ea6fc94e6860eab730e0bcd0e3106e75954ed12ff43bcf3ab69233e60c9f` |
| NHC 确认 manifest | `013561edaca8d52b0c9bca8e994ea70c8752e5a9dec194e1ab3a3a88b943e590` |
| sample_statistics.py | `0b85ecee1b7ec7aee19d5b350ec5bdc7a5e23d635874843e68a50aa5faee6b67` |
| native ACF helper | `ffa85c62195c6886c40510024ffd60ecea30531fc0851242208f0fc796db9761` |

模型为 8 个质量 1 的虚拟原子、24 个无约束独立模式，`omega_m=0.15+0.01*m`、`F_m=-omega_m²*q_m`。mode `m` 对应原子 `m//3`、方向 `m%3`。固定初态 seed 101–108；每 mode 按原 C++ `mt19937_64` 和 `normal_distribution` 顺序先取 q、再取 v，使用解析正则初态，不去质心。

共享初态已物化并核验：LAMMPS 各轮同 seed 的输入逐值一致；24 份 GPUMD 原生输入的位置与其逐值一致，速度在必要单位往返后至多差 1 ULP。见 `shared-initial-state-audit.json`。每个引擎的 thermostat RNG 保持各自实现；相同整数 seed 不被解释为跨 RNG 的相同噪声。

内部 `dt=0.01`，Tdamp=100 步，T=300 K，`kBT=0.025852029 eV`。GPUMD 输入步长为 `0.1018051 fs`、速度除以 `10.18051`；LAMMPS 的 `units lj` 使用相同数值的 dt、mass=1、T=kBT、物理 Tdamp=1。LAMMPS thermostat 和 thermo 都用 `extra/dof 0`，保留 24 个自由度。

初轮 NHC/BDP 各 8×200000 步，丢前 20000，95% CI；NHC 确认轮同 8 seed 各 2000000 步，丢前 200000，99% CI。块长 2000、最低 ESS=1000、最低块数=32；均值相对带 ±2%、方差带 ±15%。取块间及 seed 间 Student-t 区间中较宽者，整条区间位于允许带且达到精度门槛才 PASS。

每步只取最后恒温缩放后的 K 和 `A_axis=mean_i(m_i*v_axis²)`。解析目标 `E[K]=3N*kBT/2=0.310224348 eV`、`Var[K]=3N*(kBT)²/2`；`E[A]=kBT`、`Var[A]=2*(kBT)²/N`。保留所有种子和所有矩，未替换 seed、延长本轮预算、放宽门槛或只保留有利方向。确认轮复用相同初态/前缀，不称为独立新种子重复实验。本轮没有新增 harmonic half-dt 验证。

LAMMPS NHC 为链长 4、其自身默认链积分/初始热浴状态；GPUMD 为原 SY7×RESPA4 和原初始链变量。两者的算法实现差别保留并记录，比较分布而非轨迹逐步一致。CSVR 使用 LAMMPS 原生 `fix temp/csvr` + `fix nve`；参见 [LAMMPS 官方说明](https://docs.lammps.org/fix_temp_csvr.html)和[NHC 说明](https://docs.lammps.org/fix_nh.html)，锁定版本源码决定本轮具体行为。

<a id="thermostat-reference-controls-20261103--gpumd-原生对照为什么需要测试接口"></a>
### GPUMD 原生对照为什么需要测试接口

首次 stock FCP 的 200-step smoke 暴露 `Force::compute` 对 FCP 无条件减去平均力（锁定 `src/force/force.cu:610–632,963–985`），使 onsite 模式耦合。4800 个力分量相对独立模型最大差 0.0109684；相对减均值后的模型仅约 7.0e-9。该运行没有作为等价实验计入结果，见 `stock-fcp-model-validation.json`。FCP 的势定义见 [GPUMD 官方文档](https://www.gpumd.org/potentials/fcp.html)。

从只读 checkout 导出新源码和新构建目录；完整 patch SHA `3ccbf486b5cf77dfbff07b2815bfc8e5b988b64566c878360d46c79cb2db64d1`，只允许三类改动：

1. 显式测试环境变量存在时，为此外加 onsite 势绕过 FCP 净力投影。
2. BDP `initialize_rng` 接收固定测试 seed；随机数算法、Gaussian cache 和重标定公式不改。
3. `integrate.compute2` 后读取速度，写 K/三个方向矩；不参与动力学反馈。

NHC、SVR 函数和 GPU 积分器源码未改。FCP 的系数、位移和力计算仍走原生 float 路径，不能称为 double/逐位相同力场；系数最大相对舍入误差 5.2421e-8。原生 smoke 的最大力/每原子能量误差约 1.03e-8 / 1.26e-8；观测量与 XYZ 后缩放速度计算相差至多 1.67e-16。

独立 CMake 原版与关闭测试接口的适配器，NHC 200-step XYZ 逐字节相同；打开只读 observer 前后，NHC 和固定 seed 的 BDP XYZ 也逐字节相同。见 `adapter-invariance/results.json`。这些是适配器的有效性检查，不是 harmonic 分布 PASS。

<a id="thermostat-reference-controls-20261103--结果"></a>
### 结果

表中的 K 与每个方向均同时包含均值和方差。每组 8 个固定 seed。
| 阶段 / 系综 | 引擎 | K | x | y | z | 完整验收 |
| --- | --- | --- | --- | --- | --- | --- |
| 200k 初轮 / nvt_nhc | DMG-MD | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 200k 初轮 / nvt_nhc | GPUMD-source | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 200k 初轮 / nvt_nhc | GPUMD-native-adapter | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 200k 初轮 / nvt_nhc | LAMMPS | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 200k 初轮 / nvt_bdp | DMG-MD | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 200k 初轮 / nvt_bdp | GPUMD-source | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 200k 初轮 / nvt_bdp | GPUMD-native-adapter | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 200k 初轮 / nvt_bdp | LAMMPS | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 2M 确认 / nvt_nhc | DMG-MD | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 2M 确认 / nvt_nhc | GPUMD-source | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 2M 确认 / nvt_nhc | GPUMD-native-adapter | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |
| 2M 确认 / nvt_nhc | LAMMPS | PASS | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE | INCONCLUSIVE |

NHC 确认轮的轴向均值除以 kBT（括号为原 99% CI），允许带为 `[0.98,1.02]`：
| 引擎 | x / CI | y / CI | z / CI |
| --- | --- | --- | --- |
| DMG-MD | 1.01075 [0.94688, 1.07463] | 0.96723 [0.90715, 1.02731] | 1.02364 [0.93776, 1.10953] |
| GPUMD-source | 0.98514 [0.88664, 1.08365] | 0.99153 [0.94093, 1.04214] | 1.02324 [0.94676, 1.09972] |
| GPUMD-native-adapter | 1.02379 [0.99938, 1.04819] | 1.00152 [0.92998, 1.07305] | 0.97476 [0.91022, 1.03930] |
| LAMMPS | 1.01672 [0.92288, 1.11056] | 0.99599 [0.93783, 1.05415] | 0.98650 [0.91246, 1.06055] |

这些 CI 均未完整进入允许带；点估计接近目标也不能替代通过判定。有限预算内的方向混合/精度不足与上述结果相符，但这不是永不收敛或普遍非遍历性的数学证明。ACF 的最大 lag=2048 和仅 8 个独立种子仍限制区间的解释。

<a id="thermostat-reference-controls-20261103--复核失败记录与证据边界"></a>
### 复核、失败记录与证据边界

- 有效正式数据为 96 份完整 CSV、合计 76800000 积分步。独立审计重新核验每份 SHA、精确行数和 12 组所有矩的分类，`repair_required_by_reference=false`；没有用总体 K PASS 掩盖方向非通过。

- DMG-MD 从当前源码新编译 sampler，二进制与冻结 A4 相同；新三轮共 24 份 CSV 与 2026-11-02 封存样本逐字节相同，见 `dmgmd/frozen-campaign-comparison.json`。仅增加独立 seed 并行，不改变数值顺序。workers=1/2 的小型 CLI smoke 和完整 analysis 也完全相同。

- LAMMPS 的 `run 1; install fix print; run N-1` 只解决初始输出时刻；两种 thermostat 均与单段 1000-step 连续运行的全部观测量逐值相同，见 `lammps/split-validation/results.json`。

- focused CTest：EnsembleController、NHC/SVR fixture、统计器 4/4 PASS；host MPI/CUDA 环境门 PASS。没有以这次测试工具变更声称重新完成完整 M1/M2a 发布矩阵。

- GPUMD 原先的 source driver 曾有链初始化/Tdamp 设置错误及 NHC 温度往返顺序遗漏；这些测试 driver 已修正，旧 pilot/v1–v7 和原 CSV 保留，不进入有效结果。有效轮为 `gpumd/pilot-v8/run.json`、`gpumd/confirmation-v2/run.json`。实际顶层命令/exit 1 来自原工具事件并保存在 `gpumd/effective-top-level-commands.json`；旧工具 f6d993d 的精确源码已按哈希保存。真实 GPUMD 往返顺序下，NHC seed102 短程首差约 8.33e-17（3 ULP），不能把 200-step 相近误说为长轨迹逐位一致。BDP 八组短程逐位一致。

- 原预留 LAMMPS 缺 EXTRA-FIX，CSVR 作业在积分前拒绝。这是构建包缺失，不是分布 FAIL。独立 CPU 构建后，两种 thermostat 的全部正式轨迹成功。早期输入/输出 smoke 和缺包记录保留；正式 `pilot-extrafix-cpu` 和 `confirmation-extrafix-cpu` 未换种子。

- LAMMPS 确认轮先生成完整 8 份 CSV，再中断内存开销较大的分析，改为逐列流式分析。原始 CSV 完整，原 INCOMPLETE JSON 被重分析覆盖；原顶层中断返回码未留存，未从统计状态推造退出码，见 `lammps/effective-top-level-commands.json`。主代理另在新进程、新目录对完整 CSV 独立重分析，所有矩的 CI/ESS 等完整字段与原结果完全相同；实际退出 1 如实反映 INCONCLUSIVE。精确命令、快照与结果见 `lammps-independent-reanalysis/`。

- 主代理第一次汇总审计因旧 LAMMPS 记录不含 `samples_path` 在读取元数据时退出；改为从已记录 cwd 定位同一 CSV 后，`comparison-audit-v2` 完整通过，原失败日志保留，未改科学数据。

- CLI/provenance 收尾未重跑或改写有效样本/CI；执行版本、最终版本、抽取 C++ 和分析器均按各 sidecar/源码快照区分。三个只读 reference 的全文件/已有 ignored 产物前后核验，以及本轮文件所有权清单，见根目录 `reference-integrity.json`、`final-ownership.json`。

- 归档例外：本轮 focused CTest 在旧 `build-a4` 目录执行，刷新了旧归档中的 `Testing/Temporary/CTestCostData.txt` 和 `LastTest.log`。未改写旧哈希清单来隐藏差异；原科学样本、可执行文件及其他封存文件另按旧清单核验，例外的预期/当前 SHA 记录在 `prior-evidence-integrity.json`。本轮完整 CTest stdout/stderr 已另存新证据目录。

<a id="thermostat-reference-controls-20261103--执行与复现"></a>
### 执行与复现

所有命令从仓库根开始，先 `source ../env/md-mpi.sh`；GPU 命令在能访问驱动的 host 执行。各 batch 的真实 argv/cwd/env、返回码和逐 seed 输入/输出 SHA 位于根目录 `*.command.json`、GPUMD/LAMMPS `effective-top-level-commands.json` 与各原始报告；缺失项在 sidecar 中显式记为未知。复现入口见 [thermostat README](../../../tests/thermostats/README.md)。

```bash
export PYTHONDONTWRITEBYTECODE=1
export DMGMD_ACF_HELPER=/tmp/dmgmd-a3-a6-20261102/build-a4/tests/dmgmd_autocorrelation
python3 tests/thermostats/run_harmonic.py --candidate /tmp/dmgmd-reference-controls-20261103/dmgmd/build/dmgmd_harmonic_sampler --ensemble nvt_nhc --parallel-seeds 8 --manifest tests/thermostats/harmonic-confirmation.json --work-root /tmp/dmgmd-harmonic-confirm-replay-new
python3 tests/thermostats/reference_gpumd_harmonic.py --stage confirm --ensemble nvt_nhc --workers 8 --work-root /tmp/gpumd-source-confirm-replay-new
python3 tests/thermostats/reference_lammps_harmonic.py --campaign confirmation --lmp /tmp/dmgmd-reference-controls-20261103/lammps/build-out/lmp --work-root /tmp/lammps-confirm-replay-new
python3 tests/thermostats/run_gpumd_native_harmonic.py --candidate /tmp/dmgmd-reference-controls-20261103/gpumd-harmonic-native-build/gpumd-harmonic-test-adapter --devices 0,1,2,3,4,5,6,7 --campaign confirmation --work-root /tmp/gpumd-native-confirm-replay-new
```

新目录示例不能用于覆盖本轮原始证据。初轮 NHC/BDP、构建步骤和 smoke 入口见同一 README。生产支持范围、restart 语义和发布门均未改变。
