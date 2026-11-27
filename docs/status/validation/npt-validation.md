# 各向同性 NPT 数值验收

<a id="pressure-oracle-20261103"></a>
<a id="box-contract-20261103"></a>
## 压力、盒和积分顺序

2026-11-03 的 B0/B1 验证了压力和 virial 单位、六个应力分量，以及盒缩放和输出次序。CPU 合成输入经过生产 writer；五组单卡零步长构型与锁定 GPUMD 逐项对照。压缩和拉伸近零压参考态时，压力按相反方向变化。[控压器标准](../../standards/barostat.md)

<a id="npt-ber-20261103"></a>
<a id="npt-output-20261103"></a>
<a id="npt-regression-20261103"></a>
<a id="npt-regression-20261104"></a>
## 短程、重启与错误处理

Berendsen 通过 200 步单卡 GPUMD 对照、P1/P2/P4 全量复制验收，以及负压和大体模量边界检查。B4 另覆盖盒输出、跨 rank 新作业 restart、设置不一致、错误输入和旧 NVE/NVT 回归。NEP5、含 ZBL 模型及水的短程结果各有对应测试。

Berendsen 的体积涨落不能证明平衡态 NPT 分布。执行方法见[测试手册](../../../tests/npt/README.md)。

<a id="npt-scr-20261103"></a>
<a id="npt-scr-final-20261103"></a>
## 2026-11-03 初次 SCR 统计

18 个预注册分析节点的总体判定为 INCONCLUSIVE，各节点的科学结论和样本身份继续保留。后续独立确认另采新的完整链。[18 次分析结果](npt-scr-formal-results.json)

<a id="npt-scr-confirmation-20261104"></a>
<a id="npt-matrix-20261104"></a>
## 2026-11-04 SCR 独立确认与 G-B

独立确认固定使用 216 原子 NEP-C、300 K 和 0 GPa，在单节点全量复制模式完成 160 条新链。最终分析按预注册执行一次。

主比较是单卡 HostStaged 候选与普通 GPUMD。基准步长和半步长各有 32 条候选链及 32 条参考链；基准步长另比较 P2/P4 的 HostStaged 和 CudaAware。主比较和 MPI 对照的均值、方差、体积累积分布及有效样本量在该范围通过。

两个主比较的体积方差比区间为 [0.93544, 1.02032] 和 [0.93370, 1.02895]。完整配对分析保留估计量、置信区间、20,000 次 bootstrap 设置和逐项判定。[原始配对分析](evidence/npt-scr-gb/paired-analysis.json) · [统计协议](../../../tests/npt/independent-confirmation-protocol.md)

基准和半步长的理论动能目标诊断均为 `THEORY_TARGET_INCONCLUSIVE`。这一诊断与同条件 GPUMD 相容性分别判定，原始配对分析保留两者。

实验局部 NPT 的大盒统计另有独立证据，不能将本节全量复制结论扩展过去。[局部 NPT 验收](domain-npt-validation.md#dn4-final-20261116)

<a id="public-inputs-20261127"></a>
## 2026-11-27 三套公开 NPT 共同起点

普通 GPUMD 5.9.1 在单张 L20 上分别制备三套体系。每套先运行 100,000 步 Berendsen，再从 SCR 轨迹选取末帧。下表给出冻结起点及 LAMMPS/MatPL 相对普通 GPUMD 的逐原子最大分量绝对差。[输入和 SHA-256 校验方法](../../../tests/npt/public_inputs/README.md)

| 材料 | 原子数 | 累计 SCR（ps） | 最大力差（eV/Å） | 最大 virial 差（eV） |
| --- | ---: | ---: | ---: | ---: |
| 金刚石碳 | 1,016,064 | 20 | 1.86742 × 10⁻⁵ | 1.46752 × 10⁻⁵ |
| 致密水 | 1,221,915 | 110 | 1.35773 × 10⁻⁵ | 8.25820 × 10⁻⁶ |
| FCC 铜 | 1,207,296 | 30 | 3.69532 × 10⁻⁶ | 4.29947 × 10⁻⁶ |

每种材料从三次独立短程试跑取得校准帧。在选定的留出末帧上，DMG-MD、普通 GPUMD、GPUMD-WPE、LAMMPS/MatPL 的静态量通过冻结的容差，四种引擎随后在新作业目录从同一张快照重放。MatPL 在 L20 scratch 构建时启用双精度推理，求力时用 `geometry gpumd_5_9_1` 复现参考程序对周期位移的舍入。各材料包的 `evidence/static-verification.json` 给出力、势能、virial 的逐项误差及上限。[MatPL 模式及核验步骤](../../../tests/npt/public_inputs/static-observables.md)

水在累计 60 ps 时体积和每原子势能仍有持续变化。再运行 50 ps 后，前后各 25 ps 的平均体积相差 −0.00160%，末两个 10 ps 窗口的每原子势能回升 0.00754 meV，结构检查通过。110 ps 末帧在静态测量前选定。水通过原冻结引擎的终验；交接时对同源码重建的 DMG-MD 重做三帧校准、同一末帧验证、1/2/4 卡容量检查及重放，逐项同时满足新旧两组容差。碳、铜的交接清单登记原构建。体积 ESS 在本单用于诊断制备轨迹。工单 09 将使用独立轨迹检验 SCR 的体积涨落及统计精度。[工单 01 的决议与交付](../../../.scratch/v1-release-implementation/issues/01-public-equilibrated-inputs.md) · [SCR 科学验收规程](../../../.scratch/v1-release-readiness/issues/03-scr-science-protocol.md#answer)

制备和引擎测量使用 DMG-MD 源码基线 `52266e0807d49e7d3fffb0d0deee08d4c72246a0`。L20 环境由 `source ../env/md-mpi.sh` 加载。普通 GPUMD 使用 `11ba6fb`，WPE 使用 GPUMD 宿主 `a0e55bc` 和 Wisevolve 1.0.0，LAMMPS 使用 `611ca3b7`、MatPL `21fabf34` 及上述周期几何补丁。每包的 `evidence/static-measurement.json` 保存静态测量使用的 GPU、引擎命令行和二进制哈希，`replay/<引擎>/result.json` 保存重放命令和退出状态。水二次资格核验的实参保存在本轮 L20 证据根的 `revalidated-engine/logs/`，其中 `measure-holdout.log` 和 `verify-holdout.log` 首行分别记录复验测量和判定命令。`water-engine-qualification/dual-verification.json` 随三材料一起迁移，保存原表、新表及两者逐项取严的结果。

2026-11-27 在隔离 checkout 的仓库根目录执行以下复算。按[取用说明](../../../tests/npt/public_inputs/README.md)设置 `PUBLIC_INPUTS`，将 `OUT` 指向新建的可写证据目录。碳、水、铜的包内复算均通过，三份输出分别与已冻结结果逐字节一致。

```bash
(
    set -e
    for material in carbon water copper; do
        python3 tests/npt/public_inputs/static_observables.py verify \
            --bundle "$PUBLIC_INPUTS/$material" --output "$OUT/$material-static-verification.json"
        cmp "$OUT/$material-static-verification.json" "$PUBLIC_INPUTS/$material/evidence/static-verification.json"
    done
)
```
