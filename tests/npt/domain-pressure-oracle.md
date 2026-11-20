# DN4 大盒压力和体积导数 oracle

本门使用 5760 原子 NEP-C 盒、固定盒 NPT Berendsen（B=2001 GPa、Tdamp=1e30 步、dt=1e-8 fs）。温控分支跳过，逐帧验证其公式因子精确为 1；盒长和原子微步位移均受检。以输出的全部 owned 原子质量、速度和九分量 virial 独立计算 `(2K+trace(W))/(3V)`，与 thermo 压力按现有压力 oracle 容差比较。能量与 virial 的符号关系为 `dE/dln(scale)=-trace(W)`。

初始理想周期晶格的 P1 参考扫描未通过预先固定的有限差分门，其失败结果须保留。位移扰动在任何候选 local-domain 结果出现前冻结：`random.Random(20261106)`，逐原子逐轴独立 uniform [-0.01,0.01] Å；盒、速度和势不变。三个中心 log(scale)=-0.008、0、0.008，各取 h=0.001、0.0005；有限差分使用原有固定阈值 0.01 GPa + 0.002 |Pvirial|。这一构型打破晶格中心的相关单精度舍入；不宣称消除所有数值误差，也不将此参考结果外推到其他构型。

先在独立目录执行 `--jitter-reference`，要求 15 个尺度的两次 P1 off 运行、六项导数及压力方向全部通过。正式运行 `--jitter-full --reference-proof <P1 results.json>`，运行前重算并核验参考报告身份、30 条记录、输入输出哈希、压力和导数；相同 candidate 与 fixture 是必需条件。随后重新运行 30 个 P1 任务及 P2/P4、HostStaged/CudaAware、rebuild/affine 在全部 15 尺度的 120 个任务。全体逐原子能量、力、九分量 virial 和压力与 P1 按已有 baseline 门比较。每个 local 配置还用其自身 15 个尺度的能量和 virial 独立计算六项导数与方向，使用同一固定门。报告记录参考 SHA、命令、环境、输入输出哈希与逐项数值结果。

`--calibration` 和 `audit_domain_pressure_calibration.py` 是只读的参考诊断，不生成验收通过结论。原始晶格 cutoff 计数及输出精度的分析并不能给出严格的浮点误差上界。完整 P1 参考通过也不自动授权或代表 P2/P4 全矩阵通过。
