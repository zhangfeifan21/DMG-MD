<a id="dn4-大盒压力和体积导数-oracle"></a>

# DN4 大盒的独立压力与体积导数检查

## 压力怎样计算

使用 5,760 原子的 NEP-C 盒，运行固定盒 Berendsen NPT。输入参数为 `B=2001 GPa`、`Tdamp=1e30` 步、`dt=1e-8 fs`。温控分支跳过，逐帧核对其公式因子精确为 1，也检查盒长和原子的微步位移。

从输出取得所有负责原子的质量、速度和 9 个 virial 分量。独立计算 `(2K+trace(W))/(3V)`，其中 `K` 是总动能，`W` 是总 virial，`V` 是盒体积。按现有压力参考容差比较 thermo 压力。能量与 virial 的符号关系为 `dE/dln(scale)=-trace(W)`。

## 理想晶格失败与扰动参考

初始理想周期晶格的 P1 参考扫描未通过预先固定的有限差分判据，保留原始 FAIL。任何局部域候选结果出现前，已冻结位移扰动。使用 `random.Random(20261106)`，逐原子逐轴从 `[-0.01,0.01] Å` 均匀独立取样。盒、速度和势保持原样。

三个中心为 `log(scale)=-0.008,0,0.008`，每个取 `h=0.001,0.0005`。有限差分阈值沿用 `0.01 GPa + 0.002 |Pvirial|`。扰动打破晶格中心的相关单精度舍入，误差仍取决于构型。参考的原始失败与后续判据见 [domain-manifest.json](domain-manifest.json)。

## 验证参考与全矩阵

脚本使用同一候选和同一套输入目录。`CANDIDATE` 指向候选可执行文件，`PRESSURE_FIXTURE` 指向已准备的输入，`GPU_ID` 与 `GPU_IDS` 分别取自一张及四张已分配的设备，`WORK_ROOT` 指向可写的结果位置。每个 `--work-root` 指向不存在的目录。

```bash
source ../env/md-mpi.sh
python3 tests/npt/run_domain_pressure_oracle.py \
  --candidate "$CANDIDATE" --fixture "$PRESSURE_FIXTURE" \
  --devices "$GPU_ID" --work-root "$WORK_ROOT/pressure-reference" --jitter-reference
python3 tests/npt/run_domain_pressure_oracle.py \
  --candidate "$CANDIDATE" --fixture "$PRESSURE_FIXTURE" \
  --devices "$GPU_IDS" --work-root "$WORK_ROOT/pressure-full" --jitter-full \
  --reference-proof "$WORK_ROOT/pressure-reference/results.json"
```

参考阶段运行 15 个尺度，每个尺度用 P1 的 `off` 模式重复 2 次。6 项导数和压力方向都要通过。全矩阵启动前，脚本重算参考结果，核对报告身份、30 条记录、输入输出哈希、压力和导数。

全矩阵重跑 30 个 P1 任务，再用 P2/P4、HostStaged/CudaAware、`rebuild`/`affine` 在全部 15 个尺度运行 120 个局部任务。逐原子能量、力、9 个 virial 分量和压力均与 P1 按现有 baseline 判据比较。每个局部配置还用自身 15 个尺度的能量和 virial 独立计算 6 项导数及方向，使用同一阈值。报告记录参考 SHA、命令、环境、输入输出哈希及逐项结果。

`--calibration` 与 `audit_domain_pressure_calibration.py` 用于只读参考诊断。原始晶格的 cutoff 计数和输出精度只能解释现象，无法给出严格的浮点误差上界。P1 通过只说明该参考通过自身门槛，P2/P4 仍须分别核验。
