# 三种公开材料的平衡输入

此目录实现[实施工单 01](../../../.scratch/v1-release-implementation/issues/01-public-equilibrated-inputs.md)。输出依次包括公开初态、独立校准快照、预平衡记录、SCR 末帧和四引擎静态核验表。后续作业用交接目录中 `manifest.json` 登记的 `model.xyz` 和 `nep.txt`。

制备和分析共用一份清单，登记输入哈希、模拟参数、采样计划和验收阈值。[制备参数清单](manifest.json)

## 取用已冻结的三套输入

工单 09 和计时作业可直接读取各材料目录中的 `model.xyz`、`nep.txt` 和 `manifest.json`。选定的 SCR 末帧分别为碳 20 ps、水 110 ps、铜 30 ps。清单中的 `production_output` 给出输出频率和末帧字段，`replay` 指向四种引擎从同一末帧启动的记录。[制备和终验结果](../../../docs/status/validation/npt-validation.md#public-inputs-20261127)

在 L20 的仓库根目录执行以下命令。三个材料入口都是相对符号链接。迁移时，从 L20 用 `rsync -aL "$PUBLIC_INPUTS/" "$DEST/"` 将链接指向的实际文件一并复制到新的交接目录（`DEST` 可设为 `主机:绝对路径`）。在目的端把 `PUBLIC_INPUTS` 指向该目录。校验九个输入文件，以及水的五份补充证据。[冻结文件 SHA-256](frozen-inputs.sha256) · [水的双门槛证据 SHA-256](water-engine-qualification.sha256)

```bash
PUBLIC_INPUTS="$HOME/DMG-MD-Project/scratch/public-inputs-20261125/water-continuation-20261127/accepted-inputs"
scripts="$PWD/tests/npt/public_inputs"
(cd "$PUBLIC_INPUTS" && sha256sum -c "$scripts/frozen-inputs.sha256" "$scripts/water-engine-qualification.sha256")
```

输入身份与引擎身份分别核对。`replay/<引擎>/result.json` 里的 `binary_sha256` 标识通过重放的程序，`binary` 是当时使用的路径。碳、铜使用的 DMG-MD 哈希以 `672994ab` 开头，该共享构建路径后来已被重编。水包使用 scratch 中固定的 `fe03d38e` 构建。若需复现相同构建，先用 `sha256sum` 核对可执行文件。下游使用新候选时沿用冻结快照和势，并为新候选记录其校准、留出帧复验及适用卡数的运行结果。

从任一包的 `evidence/static-verification.json` 可读取留出帧的逐项误差和判定。复算命令见本页末尾。水包内的复算使用重建引擎的校准表。统一入口下的 `water-engine-qualification/` 另含原校准、原末帧测量与判定、重建引擎身份和 `dual-verification.json`。后者列出重建引擎测量对新、旧两张表的逐项误差、上限与判定。

## 从公开起点制备

在仓库根目录用 Bash 执行。需要普通 GPUMD 5.9.1、已构建的 DMG-MD、GPUMD-WPE、带 MatPL/KOKKOS CUDA 的 LAMMPS，以及可写的作业目录。以下变量在同一 shell 内使用。将 `GPUMD`、`DMGMD`、`LAMMPS`、`WPE` 设为实际可执行文件的绝对路径，`GPU_UUID` 设为一张空闲 GPU 的完整 UUID。

```bash
source ../env/md-mpi.sh
scripts=tests/npt/public_inputs
work="$PWD/../scratch/public-inputs"
mkdir -p "$work"
material=water
case_dir="$work/$material"
```

| 材料参数 | 初始结构 | 原子数 | dt（fs） | 输入体模量（GPa） |
| --- | --- | ---: | ---: | ---: |
| `carbon` | 金刚石碳，36 × 98 × 36 常规胞 | 1,016,064 | 1.0 | 425 |
| `water` | 53 × 145 × 53 个水分子，沿用仓库致密水生成器 | 1,221,915 | 0.5 | 2.2 |
| `copper` | FCC 铜，48 × 131 × 48 常规胞 | 1,207,296 | 1.5 | 140 |

碳和水的势随仓库分发。铜使用 GPUMD 5.9.1 势库的原始 16 元素模型。将下载的文件路径放入 `COPPER_POTENTIAL`，脚本核对原始 SHA-256。铜使用的 Cu 原子类型在转换时映射到相同元素。[固定版本的铜势](https://raw.githubusercontent.com/brucefan1983/GPUMD/11ba6fbdb4d71b2cdb351b39972fa057521585b3/potentials/nep/Song-2024-UNEP-v1-AgAlAuCrCuMgMoNiPbPdPtTaTiVWZr.txt)

从头制备时执行以下命令。已有原协议作业时，使用下文的归档复算步骤。

```bash
args=(--material "$material" --output "$case_dir" --gpumd-bin "$GPUMD" --device "$GPU_UUID")
if [[ $material == copper ]]; then args+=(--copper-potential "$COPPER_POTENTIAL"); fi
python3 "$scripts/prepare.py" generate "${args[@]}"
python3 "$scripts/prepare.py" calibration "${args[@]}"
python3 "$scripts/prepare.py" ber "${args[@]}" && python3 "$scripts/prepare.py" scr "${args[@]}"
```

`source.json` 登记生成命令、几何来源、所用势和哈希。三条 100 步独立试跑各用一个初速度种子，末帧位于 `calibration-{401,402,403}/restart.xyz`。预平衡从公开初态出发，由普通 GPUMD 以种子 301 产生速度。100,000 步 Berendsen 分两段连续积分。第一段每 1,000 步写 thermo，第二段每 100 步写 thermo。全程等间隔的 100 点参与体积相对标准差检查，最后 50 点参与漂移拟合。

`scr` 复核 Berendsen 判据，读取通过检查的末帧，接着运行 20,000 步 SCR，每 100 步记录一次。各阶段的 `analysis/summary.json` 给出判定，`thermo.csv` 和 `thermo.svg` 保存数据与曲线。SCR 检查均温、均压和体积相对标准差。体积有效样本量用于描述平均体积的统计精度。结果同时列出温度、压力和体积平方偏差的有效样本量。2026-11-26 之前的制备报告按当时的 ESS >100 条件保留原判定，复算写入新的证据目录。每阶段的 `execution.json` 记录进程退出码、输入输出哈希和制备用时。进程执行结果和物理判定分别读取。

复用 2026-11-25 的作业时，先把 `archive` 设为原证据根目录的绝对路径，`work` 设为另一个绝对路径。在新根下执行下列步骤，每种材料使用一个新的子目录。原始数据和执行记录保持同一身份，新协议的分析写到新根。新制备的作业可跳过这一步。

```bash
(
    set -e
    mkdir -p "$work"
    dst="$work/$material"
    src="$archive/$material"
    mkdir "$dst"
    mkdir "$dst/scr"
    for item in source.json public-model.xyz nep.txt ber calibration-{401,402,403}; do
        ln -s "$src/$item" "$dst/$item"
    done
    for item in run.in execution.json model.xyz nep.txt restart.xyz thermo.out; do
        ln -s "$src/scr/$item" "$dst/scr/$item"
    done
    case "$material" in carbon) dt=1.0 ;; water) dt=0.5 ;; copper) dt=1.5 ;; esac
    python3 "$scripts/analyze_preparation.py" scr --thermo "$dst/scr/thermo.out" \
        --time-step-fs "$dt" --output-dir "$dst/scr/analysis"
    python3 "$scripts/prepare.py" check --material "$material" --output "$dst" --through-stage scr
)
```

从上面两种制备路径之一取得 20,000 步 SCR 记录后，将 `case_dir` 指向实际使用的材料目录。碳和铜的候选为该目录的 `scr/restart.xyz`。对于水，在同一目录从原 SCR 末帧继承盒、坐标和速度，沿用 0.5 fs 步长及相同的 SCR 参数再跑 100,000 步（50 ps）。续段输出在 `scr-extension/`。

```bash
case_dir="$work/$material"
if [[ $material == water ]]; then
    python3 "$scripts/prepare.py" scr-extension --material water --output "$case_dir" \
        --gpumd-bin "$GPUMD" --device "$GPU_UUID"
fi
```

水续段结束后，先执行分段诊断，再阅读输出。原 0～10 ps 和续段 10～60 ps 按时间顺序每 10 ps 一组，另比较续段前后各 25 ps 的平均值。检查体积和每原子势能是否仍朝同一方向变化，并结合均温、均压与结构判断。将 `decision` 设为这次分析所得的 `select` 或 `reject`，`reason` 写明所依据的窗口与观察。选择登记在独立静态计算前一次性写入，拒绝时保留轨迹和理由。

```bash
python3 "$scripts/water_selection.py" diagnose --root "$case_dir"
# 阅读分段结果并设置 decision、reason 后执行
python3 "$scripts/water_selection.py" record --root "$case_dir" --decision "$decision" --reason "$reason"
```

本单的 60 ps 水末帧仍在松弛。第二次续跑从这张末帧继承盒、坐标和速度，再做 50 ps。保留首轮选帧记录，在另一证据根执行下列命令。`prior_water` 指向含 `scr-extension/` 的原水目录，`next_work` 指向本次作业的绝对路径。

```bash
(
    set -e
    mkdir -p "$next_work"
    mkdir "$next_work/water"
    for item in source.json public-model.xyz nep.txt ber scr scr-extension calibration-{401,402,403}; do
        ln -s "$prior_water/$item" "$next_work/water/$item"
    done
    python3 "$scripts/prepare.py" scr-extension-2 --material water --output "$next_work/water" \
        --gpumd-bin "$GPUMD" --device "$GPU_UUID"
    python3 "$scripts/water_selection.py" diagnose --root "$next_work/water"
)
```

在新目录阅读 60～110 ps 的五个 10 ps 窗口和前后各 25 ps 均值。填写本轮的 `decision` 和 `reason` 后，显式核对第二次续段，再登记决定。

```bash
python3 "$scripts/prepare.py" check --material water --output "$next_work/water" --through-stage scr-extension-2 && \
    python3 "$scripts/water_selection.py" record --root "$next_work/water" --decision "$decision" --reason "$reason"
```

选中 110 ps 后，若继续使用原校准的四份可执行文件，以下片段将原校准、WPE 诊断和容量证据接入新根。`prior_work` 指向原校准与容量证据根，`prior_direction` 指向原控压方向检查的 `pressure-direction.json`。将静态终验的新测量写到 `$HOLDOUT`，使用 `$case_dir/scr-extension-2/restart.xyz` 和同目录 `execution.json`。

```bash
work="$next_work"
material=water
case_dir="$work/water"
CALIBRATION="$work/static/water/calibration.json"
HOLDOUT="$work/static/water/holdout"
HOLDOUT_RESULT="$work/static/water/holdout-verification.json"
(
    set -e
    mkdir -p "$work/static/water"
    cp "$prior_work/static/water/calibration.json" "$CALIBRATION"
    for trial in calibration-{401,402,403}; do
        ln -s "$prior_work/static/water/$trial" "$work/static/water/$trial"
    done
    cp "$prior_work/static/wpe-diagnosis.json" "$work/static/"
    cp -aL "$prior_work/static/wpe-diagnosis" "$work/static/"
    ln -s "$prior_work/capacity" "$work/capacity"
)
```

使用下文静态核验命令，逐项通过后执行 `handoff.py --root "$work" --material water --direction "$prior_direction" --device "$GPU_UUID" --output "$work/frozen/water"`。

三种材料均沿用校准阶段冻结的逐项静态容差。终验和交接时，程序从水的 `water-diagnostics.json`、`water-selection.json` 和原始 thermo 复核末帧选择。

## 核对控压方向与静态量

在新目录运行两次单步 Berendsen 试验。碳和铜采用默认的两档盒长。水的扩盒起点可使用种子 401 的短程松弛结果，放大盒子时保留分子内 O–H 矢量。脚本用实际内压和实际盒体积变化判定两种方向。

```bash
direction="$work/direction/$material"
extra=()
if [[ $material == water ]]; then
    extra=(--contract-restart "$case_dir/calibration-401/restart.xyz" --expanded-scale 1.06)
fi
python3 "$scripts/analyze_pressure_direction.py" prepare --material "$material" \
    --source-dir "$case_dir" --output-dir "$direction" "${extra[@]}"
python3 "$scripts/analyze_pressure_direction.py" run --output-dir "$direction" \
    --gpumd-bin "$GPUMD" --device "$GPU_UUID"
python3 "$scripts/analyze_pressure_direction.py" check --output-dir "$direction"
```

每张独立校准帧都在四个引擎中求力。先核对 WPE 后端、误差来源和既有分量上限，再冻结每种材料、每对引擎、每个物理量的容差。在独立末帧上验证。具体命令和单位映射见[四引擎静态核验](static-observables.md)。

## 容量与交接

`replay.py` 将快照和势复制到新作业目录，复读 SHA-256 后，以材料的步长和控制参数运行两步 Berendsen。每个作业的 `result.json` 记录实际 GPU、每张卡采样到的最高显存、进程和分区检查结果。用完整的 `nvidia-smi` 时间序列解释短作业的显存读数。单卡 WPE 的 `--engine` 为 `wpe`，其他三个入口分别为 `gpumd`、`dmgmd`、`lammps`。容量探测使用校准快照，独立终验预留 SCR 末帧。

```bash
trial="$case_dir/calibration-401"
model_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["snapshot"]["sha256"])' "$trial/execution.json")
nep_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["potential_sha256"])' "$case_dir/source.json")
python3 "$scripts/replay.py" --material "$material" --engine gpumd --binary "$GPUMD" \
    --model "$trial/restart.xyz" --model-sha256 "$model_sha" \
    --potential "$case_dir/nep.txt" --potential-sha256 "$nep_sha" \
    --devices "$GPU_UUID" --output "$work/capacity/$material-gpumd-p1"
```

LAMMPS 容量作业使用与静态校准相同的程序和几何选项。复验修复构建时，传入 `--engine lammps --binary "$LAMMPS" --lammps-geometry gpumd_5_9_1`。交接程序从静态记录读取该模式，核对容量作业并传给重放作业。

逐材料完成普通 GPUMD、DMG-MD 和 LAMMPS 的 1/2/4 卡记录及 WPE 的单卡记录。目录名使用 `capacity/<材料>-<引擎>-p<卡数>`。P2/P4 的 `--devices` 传逗号分隔的设备列表。LAMMPS 用 Berendsen 检查容量，SCR 算法的可比范围见[对标矩阵](../../../docs/status/performance/npt-benchmarks.md#performance-matrix)。

四引擎静态终验和该材料各制备阶段的物理判据通过后，运行交接命令。程序检查原始证据，在四个全新的单卡作业中重新读入同一末帧，最后发布输入清单。后续工单用清单复核原子顺序、盒、质量、速度、势及输出频率。

```bash
python3 "$scripts/handoff.py" --root "$work" --material "$material" \
    --direction "$direction/pressure-direction.json" --device "$GPU_UUID" \
    --output "$work/frozen/$material"
```

交接目录移动后，可用 `python3 "$scripts/static_observables.py" verify --bundle "$work/frozen/$material" --output "$work/$material-static-recheck.json"` 从包内证据复算静态核验表。该命令按 SHA-256 找到原始测量、制备执行记录和 WPE 诊断证据。`evidence/` 另保存各容量作业的输入与运行日志，以及每张校准帧和留出帧的 LAMMPS 输入和模式确认日志，可逐项复核。
