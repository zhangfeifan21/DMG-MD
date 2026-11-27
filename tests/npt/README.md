<a id="npt-acceptance-suite"></a>

# NPT 验收

按测试目标选择入口。需要复现历史结果时，从对应记录取得该批次的程序、参考身份和输入冻结文件。

| 要验证的行为 | 执行入口 |
| --- | --- |
| 工单 09 及计时作业取用碳、水、铜共同起点 | [冻结输入、哈希及复算方法](public_inputs/README.md) |
| Berendsen NPT 与普通 GPUMD 的短程数值一致性 | [短程基线（B2）](#比较-berendsen-短程输出b2) |
| 输出字段、SCR 重复性、错误输入与更换 rank 数重启 | [短程专项（B4）](#检查输出错误处理和重启b4) |
| 全量复制模式的 SCR 采样 | [原注册与独立确认](#scr-统计原注册b3) |
| 局部域分解下的变盒、缓存、迁移与统计 | [局部 NPT](#检查局部-npt) |

运行 GPU 作业前，从仓库根目录加载 `../env/md-mpi.sh`，按[设备预检](../mpi/README.md)验证环境。

`CANDIDATE` 和 `GPUMD_REF` 分别指向待测程序与冻结要求的 GPUMD 程序。`BUILD_LOG` 指向候选构建日志，`ACF_HELPER` 指向原生自相关函数（ACF）辅助程序。

`WORK_ROOT` 选可写位置，例如工作树旁的 `../scratch/npt-checks-new`。各输出和冻结文件应使用新路径。`GPU_IDS` 填现场分配的 4 张不同设备，`GPU_IDS_2` 取其中 2 张。

## 预检输入和统计工具

可以先检查输入清单和生成器。`--write` 将生成结果写入空目录，检查 `manifest.json` 定义的用例和输入身份。

```bash
source ../env/md-mpi.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/npt/test_manifest.py
python3 tests/npt/generate_inputs.py --output "$WORK_ROOT/fixture-check" --write
```

本目录的 [manifest](manifest.json) 是 B2/B3 原预注册的参数来源。历史独立确认按[原协议与参数](independent-confirmation-protocol.md)核对，公开副本与原冻结的区别写在协议内。回放需要保留对应冻结证据和参数原文。模式名称见 [MPI 标准](../../docs/standards/mpi-runtime.md)。

## 比较 Berendsen 短程输出（B2）

两端使用完全相同的显式速度 `model.xyz`、`run.in` 和 NEP 势。按短程 [golden 容差](../baseline/manifest.json) 检查 200 行 thermo、6 列应力、9 列盒数据、稀疏 XYZ 和重启快照。保留已有 golden 和容差。短程矩阵用 P1/P2/P4 及 HostStaged、CudaAware。5,184 原子用例达到局部资格的尺度，原 B2 的 NPT 作业仍须报告全量复制模式。

B2 的历史数值冻结工具记录候选、参考程序、生成输入、容差 manifest、脚本哈希及当时的三份源码工作树状态。重做该冻结需要原有布局。冻结文件独立于运行结果。先复核其身份并记录 SHA-256，再开启正式结果。

```bash
python3 tests/npt/freeze_b2.py --candidate "$CANDIDATE" --reference "$GPUMD_REF" \
  --build-log "$BUILD_LOG" --input-root "$WORK_ROOT/b2-inputs" \
  --freeze "$WORK_ROOT/b2-freeze.json"
python3 tests/npt/run_npt_ber.py --candidate "$CANDIDATE" --reference "$GPUMD_REF" \
  --freeze "$WORK_ROOT/b2-freeze.json" --devices "$GPU_IDS" \
  --work-root "$WORK_ROOT/b2-results"
```

驱动串行启动用例，保留命令、环境、输入输出哈希、日志、路由、容差、首个数值差异、重启与输出检查。该历史驱动还会对照冻结时的源码状态。负例应在时限内非零退出，打印指定诊断，且不生成轨迹或重启文件。比较失败时保留原结果，按原判据排查。

## SCR 统计原注册（B3）

原 B3 的 8 种子矩阵在基准步长下覆盖 P1 HostStaged 及 P2/P4 的两种后端。半步长只做 P1 HostStaged。每步记录温度、压力和体积，稀疏 XYZ 与重启由另外的 Ber 测试覆盖。

B3 另冻一份输入和执行身份。冻结包含原生 ACF 辅助程序（Geyer 估计器）、GPUMD 随机数初始化的源码及 `nm`、`objdump` 证据、输入哈希和该次科学注册的源码状态。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/npt/freeze_b3.py \
  --candidate "$CANDIDATE" --reference "$GPUMD_REF" --build-log "$BUILD_LOG" \
  --input-root "$WORK_ROOT/b3-inputs" --freeze "$WORK_ROOT/b3-freeze.json" \
  --acf-helper "$ACF_HELPER"
python3 tests/npt/run_npt_scr.py --candidate "$CANDIDATE" --reference "$GPUMD_REF" \
  --freeze "$WORK_ROOT/b3-freeze.json" --acf-helper "$ACF_HELPER" \
  --mode short --devices "$GPU_IDS" --work-root "$WORK_ROOT/b3-short"
```

短程重复性、种子、路由和负例先通过，保留结果目录。正式运行用 `--mode condition` 选择注册条件。分批调度时，合并前须拒绝重复键。候选记录按 `(profile, condition, seed)` 唯一，参考按 `(profile, seed)` 唯一。同一候选 SHA-256、MPI/UCX/CUDA 指纹、物理 GPU 顺序和 UUID、驱动及 CudaAware 探测状态全部相符时，才复用四 rank 预检结果。各作业保留证明路径和哈希。

GPUMD 用单进程、单段和完整预算。同一初态种子在一个步长配置下只启动一次参考，对应产物供多个候选条件使用。参考按冻结的启动登记串行分配不同 UTC 秒，并记录实际启动时间和 PID。这是隔离措施。已锁定参考的初始化取自 `system_clock` 的纪元计数，内部种子未直接观测。带启动登记的运行使用 `freeze_b3_execution.py` 生成的批次执行冻结以及 `--reference-launch-registry` 指定的登记表。

候选的 `DMGMD_ENSEMBLE_SEED` 等于该份初始模型的注册种子。延长采样按原初态、原种子完整重跑，并逐字节核对已有前缀。续跑快照或拼接的数据不能用于正式分析。`build_records.py` 根据带哈希的执行证据生成分析输入。分析时用 `DMGMD_ACF_HELPER` 指向冻结的原生程序。Python 的 `O(N×lag)` 实现供小规模检查使用。种子、预算、阈值和 bootstrap 次数在看结果前固定。

<a id="b4-output-and-restart-contracts"></a>

## 检查输出、错误处理和重启（B4）

本节复核特定输入的短程合同。为每个专项指定与候选和输入匹配的冻结文件。下列变量分别对应输出冻结 `OUTPUT_FREEZE`、负例冻结 `NEGATIVE_FREEZE`、NEP5 冻结 `NEP5_FREEZE` 和跨 rank 重启冻结 `RESTART_FREEZE`。2026-11-03 批次的冻结目录依次为 `freeze-v7`、`negative-freeze-v6`、`nep5-freeze-v2` 和 `crossrank-p1-p2-freeze-v1`。历史批次的身份与结果见 [B4 记录](../../docs/status/validation/npt-validation.md#npt-output-20261103)。

**输出与跨 rank 重启。** 检查稠密水和 BaTiO₃-ZBL 的 P2/P4 输出、SCR 重复性与 P2↔P4 重启。

```bash
python3 tests/npt/run_npt_contracts.py --run --candidate "$CANDIDATE" \
  --reference "$GPUMD_REF" --freeze "$OUTPUT_FREEZE" \
  --work-root "$WORK_ROOT/b4-output" --devices "$GPU_IDS" \
  --ranks 2,4 --backends HostStaged,CudaAware --timeout 900
```

**失败关闭。** P1 输入和环境负例使用匹配的负例冻结。下例只跑 P4 的种子和分区设置不一致。冻结须含按 rank 设置变量的包装器哈希，作业用普通 `mpirun -n 4` 启动。检查 P1 负例时去掉 `--seed-mismatch-only`，按测试目标选设备和其余开关。

```bash
python3 tests/npt/run_npt_contract_negatives.py --run --candidate "$CANDIDATE" \
  --reference "$GPUMD_REF" --freeze "$NEGATIVE_FREEZE" \
  --work-root "$WORK_ROOT/b4-negatives" --devices "$GPU_IDS" \
  --collective-ranks 4 --seed-mismatch --env-mismatch --seed-mismatch-only --timeout 120
```

**NEP5。** 采用注册的 `nep5_equivalent` 转换，记录 NEP4 和 NEP5 势的哈希。在短程矩阵中另以 P1 HostStaged 比较 Berendsen 与 GPUMD。

```bash
python3 tests/npt/run_npt_nep5_contract.py --run --candidate "$CANDIDATE" \
  --reference "$GPUMD_REF" --freeze "$NEP5_FREEZE" \
  --work-root "$WORK_ROOT/b4-nep5" --devices "$GPU_IDS" \
  --ranks 1,2,4 --backends HostStaged,CudaAware --timeout 900
```

**两种系综的跨进程数续跑。** 稠密水专项分别检查 P1→P2 和 P2→P1。共启动 4 个新作业，每个运行 5 步。核对快照形状、有限 thermo 和全量复制路由。`%.17g` restart 数值应与最终轨迹帧在 6 位有效数字上一致。本项检查文件能否恢复物理状态，跨作业轨迹连续性有独立语义。

```bash
python3 tests/npt/run_npt_crossrank_restart.py --run --candidate "$CANDIDATE" \
  --freeze "$RESTART_FREEZE" --work-root "$WORK_ROOT/b4-restart" \
  --devices "$GPU_IDS_2" --timeout 600
```

## SCR 独立确认

原 18 个分析节点的总体结论是 `INCONCLUSIVE`。2026-11-04 对另起的 160 条完整链执行一次最终分析。在注册范围内通过。记录包含[科学结论](../../docs/status/validation/npt-validation.md#npt-scr-confirmation-20261104)、[配对分析](../../docs/status/validation/evidence/npt-scr-gb/paired-analysis.json)与[原 18 次分析](../../docs/status/validation/npt-scr-formal-results.json)。

[独立确认协议](independent-confirmation-protocol.md)记录配对与统计方法、原批次批准和冻结流程。新采样仍须使用本轮批准的冻结文件。以下命令只检查配对分析代码。

```bash
python3 tests/npt/test_paired_confirmation_math.py
python3 tests/npt/test_paired_confirmation_integration.py
```

<a id="experimental-local-domain-npt"></a>

## 检查局部 NPT

[局部 NPT 标准](../../docs/standards/domain-npt.md)与 [domain manifest](domain-manifest.json) 定义变盒合同。DN0–DN4 已在[最终验收记录所列条件](../../docs/status/validation/domain-npt-validation.md#dn4-final-20261116)内通过。

### 几何与短程差分

先做 CPU 独立几何与路由检查。如果需要 GPU 冒烟测试，准备大盒输入。Berendsen 和 SCR 分别用新目录，后者把示例的 `npt_ber` 换成 `npt_scr`。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/npt/test_domain_geometry_oracle.py
PYTHONDONTWRITEBYTECODE=1 python3 tests/npt/test_domain_npt_runner.py
python3 tests/npt/prepare_domain_npt_fixture.py --output "$WORK_ROOT/domain-ber-fixture" \
  --ensemble npt_ber --steps 20
python3 tests/npt/run_domain_npt.py --candidate "$CANDIDATE" \
  --fixture "$WORK_ROOT/domain-ber-fixture" --work-root "$WORK_ROOT/domain-ber-smoke" \
  --devices "$GPU_IDS" --ranks 2,4 --backends HostStaged,CudaAware
```

`--fixture` 需要固定的 `model.xyz`、`nep.txt` 和 `run.in`。`run.in` 要求输出 thermo 及含力、势能和 virial 的轨迹。脚本逐一执行 `off`、`rebuild` 和 `affine`，按既有容差并核查实际路由。结果保留日志及输入输出哈希。短程诊断数据与正式统计分别保存。

### 迁移、压力和变盒提交

[迁移用例](domain-npt-migration-cases.json)定义 72 个短作业。对 Berendsen/SCR、P2/P4、两种后端和三种域模式，按每帧当前盒重放迁移记录。检查小幅越界时的缓存复用、重复迁移、跨条带与周期边界迁移，以及无迁移的重建。

```bash
python3 tests/npt/run_domain_npt_migration.py --candidate "$CANDIDATE" \
  --work-root "$WORK_ROOT/domain-migration" --devices "$GPU_IDS"
```

`dmgmd_domain_owned_thermo_poison_tests` 直接调用生产局部代码。它检查边界副本中的污染值、非全局 stride、空 rank 和多次归约修补。P1/P2/P4 均覆盖两种后端。`dmgmd_owned_box_preflight_tests` 在 P2/P4 验证生产 uint32 OR 一致拒绝候选盒，且无部分提交。压力及独立能量导数走[压力检查](domain-pressure-oracle.md)，保留原始理想晶格参考失败。

### 采样试跑与正式统计

试跑先生成输入，再检查成本。`pilot.json` 记录最慢 rank 的运行循环时间和端到端墙钟，标记为诊断。分析器拒绝把 pilot 轨迹纳入正式数据。

```bash
python3 tests/npt/domain_statistics.py prepare --output "$WORK_ROOT/domain-stat-inputs"
python3 tests/npt/domain_statistics.py pilot --inputs "$WORK_ROOT/domain-stat-inputs" \
  --candidate "$CANDIDATE" --output "$WORK_ROOT/domain-pilot" \
  --devices "$GPU_IDS_2" --ranks 2 --backend HostStaged --mode rebuild --steps 1000
```

确定性报告齐全后，使用 `proof` 核验原始产物和独立准备的压力扰动参考。`DETERMINISTIC_EVIDENCE` 指向完整报告目录，`PRESSURE_FIXTURE` 指向该压力试验的输入。生成的证明供正式冻结使用。

```bash
python3 tests/npt/domain_statistics.py proof \
  --evidence "$DETERMINISTIC_EVIDENCE" --candidate "$CANDIDATE" \
  --pressure-fixture "$PRESSURE_FIXTURE" --output "$WORK_ROOT/domain-proofs"
```

`freeze` 拒绝未批准的注册，把候选、输入清单、软件、作业列表、四卡预检、随机数和确定性证明、原生 ACF 程序和链接库身份一并锁定。注册、源码和分析器一起冻结。`run` 按证明中的设备身份和环境执行一个注册作业，写入新目录。具体参数见 `domain_statistics.py --help` 与子命令帮助。

正式冻结在 `artifacts/domain-npt/statistics-claims/` 按注册文件 SHA-256 登记。每个作业先登记输出路径，单次最终分析也须登记。失败或中断后保留原记录，改目录或复制冻结文件仍不得重跑同一作业。记录依靠操作权限约束，手动改写登记文件会破坏证据。

`batch` 串行执行同一冻结的作业，参数沿用 `run`，省略 `--job` 并以新目录作为 `--output`。`progress.json` 记录各作业命令与状态。脚本逐个核验完成记录和输入输出哈希，完整矩阵结束后只启动一次分析。作业或分析出错即停，原目录保留，批处理无续跑入口。`COMPLETE` 表示执行与分析结束，科学结论见 `PASS`、`FAIL` 或 `INCONCLUSIVE`。

### DN4 结果的适用范围

[大盒统计注册](domain-statistics-registration.json)冻结了 128 条 SCR 链和 24 条独立 NVT 响应链。152 条链和一次最终分析已完成，冻结的完整性与科学判据均通过。适用范围为 5,760 原子 NEP4-C 单节点矩阵。基准和半步长的配置详见 [DN4 验收](../../docs/status/validation/domain-npt-validation.md#dn4-final-20261116)。热力学体积响应由独立的有限温 NVT 作业测得。控压器的输入体模量与零温静态状态方程另有用途。

151 个同时区间包括原先的 116 个、16 种 SCR 条件各自的绝对温度和压力均值，以及 3 个 NVT 体积的温度均值。精度与完整块条件沿用原注册，界限为 `300 K ± 6 K` 和 `0 GPa ± 0.1 GPa`。同时覆盖为近似值，依赖块的独立程度、截断自相关估计和百分位 bootstrap。恒定采样或恒定的平方偏差无法估计有效样本量，判 `INCONCLUSIVE`。

精度达标且区间完全越界判 `FAIL`，区间跨界判 `INCONCLUSIVE`。经验累积分布直接越限判 `FAIL`。区间精度不足或单侧曲率上界越限判 `INCONCLUSIVE`。任一项 `FAIL` 则整体 `FAIL`。其余情形若有 `INCONCLUSIVE`，整体仍为 `INCONCLUSIVE`。
