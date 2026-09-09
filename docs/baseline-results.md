# GPUMD NEP MD differential/golden baseline 结果

## 1. 结论与范围

本次已经建立并实际运行 GPUMD NEP MD 的单进程、单 GPU baseline harness。它只增加
`tests/baseline/` 下的原生输入、golden 输出和 Python 比较器；没有实现 MPI，没有修改
DMG-MD 或 GPUMD 的生产物理内核。

锁定的 reference 为：

- GPUMD commit：`9d23496e41319b9e2af5221a7df6285387401d1e`；
- executable：`../gpumd-reference/src/gpumd`；
- executable SHA-256：
  `3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`；
- 基线 GPU：NVIDIA GeForce RTX 4090，compute capability 8.9；
- driver：580.173.02；CUDA compiler：12.9 / nvcc 12.9.86；
- 子进程环境固定为单个 `CUDA_VISIBLE_DEVICES=0`，因此 reference 走 ordinary `NEP`，
  不会因节点可见 8 张 GPU 而自动进入 `NEP_MULTIGPU`。

沙箱内确实没有可用 GPU，运行得到 CUDA error 100
`no CUDA-capable device is detected`。随后在沙箱外的上述单张 GPU 上完成 golden 生成、
独立全量复跑和 30 次重复校准；最终全套通过。

## 2. 一条重复运行全部 baseline 的命令

在 DMG-MD 仓库根目录运行：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py
```

该命令依次验证 reference commit/二进制哈希、全部输入哈希、势类型、small/large-box
触发条件、输出文件集合和所有结构/数值结果。当前锁定 reference 在当前基线硬件上还要求
每个输出文件与 golden **逐字节一致**。

未来比较 DMG-MD 可执行程序时使用：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md
```

candidate 模式仍严格比较结构字段，但物理数值按第 5 节的混合精度容差比较。脚本不需要
NumPy、ASE 或 calorine，只依赖 Python 标准库。

## 3. 固定输入与覆盖矩阵

所有 case 都把提交到 `tests/baseline/inputs/` 的 `model.xyz`、`run.in`/`resume.in` 和
NEP 文件逐字节复制到新的空工作目录。脚本不会把输入转换成其他格式，也不会从网络下载
资源。势文件来源和 SHA-256 如下：

| 测试势 | 仓库内已审查来源 | SHA-256 |
| --- | --- | --- |
| `nep_C.txt` | `gpumd-reference/potentials/nep/C_2024_NEP4.txt` | `add6b3f64fdd3cecebb3aae511816fe4183e4c4a22b058f108f3f0ea1c531623` |
| `nep_water.txt` | `gpumd-reference/tests_pytest/fixtures/models/nep_water.txt` | `8638300c8c6ba7eca589fa2fdc111d2e6a1f6ad9c88cc522ea9db220538a66e3` |
| `nep_BaTiO3_zbl.txt` | `gpumd-reference/tests_pytest/fixtures/models/nep_BaTiO3.txt` | `d9d5801eb267772294deee6eebd2e1e40f83908f867b5bf69467d1826034aa02` |

没有找到较小的普通 MD flexible-ZBL 势；本次没有生成或下载替代文件，使用仓库已有的
universal `nep4_zbl`。所有输入文件的逐文件哈希由 `manifest.json` 锁定。

| case | 覆盖 | 已证明的实际路径 | 关键输出 |
| --- | --- | --- | --- |
| `single_small_static` | 单元素 NEP4、初始 force/PE/virial/thermo | box thickness 8 Å；`8 <= 2.5*(7+1)=20`，因此 small-box；`neighbor.out` 的 radial actual=19，大于 7 个其他原子，亦显示多周期 image 被枚举 | 1 帧 `initial.xyz`、1 行 thermo、1 行 neighbor |
| `single_large_nve` | 单元素 NEP4、初始状态、NVE、两个连续 `run`、短轨迹、restart 写出 | thickness 24 Å > 20 Å，因此 large-box；radial/angular actual=7/7 | 静态 1 帧；0.1–0.6 fs 共 6 帧；thermo 段行数 1+6；step 3/6 覆盖 restart，最终保存 step 6 |
| `multi_nvt_restart` | O/H 双元素 NEP4、`nvt_ber`、三个连续 `run`、不同输出周期、restart 写出并作为下一作业的 `model.xyz` 读回 | thickness 24 Å > `2.5*(6+1)=17.5` Å，因此 initial/resume 都走 large-box | 静态 1 帧；NVT 帧时间 0.2、0.4、0.5、0.6、0.7 fs；thermo 段行数 1+2+3；restart 后新作业时间重新从 0 开始，输出 0.1、0.2 fs |
| `nep_zbl_boundary` | 三元素 universal NEP4-ZBL、原子靠近周期边界、初始 force/PE/virial/thermo | thickness 24 Å > 17.5 Å，因此 large-box；atom 0 Ba 位于 x=0.25 Å，atom 1 Ti 位于 x=23.75 Å，MIC 距离精确为 0.5 Å，进入 ZBL 区间 | 1 帧静态 XYZ、1 行 thermo、1 行 neighbor |

所有动力学 model 都显式提供 `vel:R:3`，因此本矩阵没有把默认随机速度初始化混入数值
baseline。基础 NVT 明确选择审计计划中的 `ensemble nvt_ber`，不把它描述为严格 canonical
sampler。

## 4. 每项测试的通过标准

### 4.1 所有 case 的共同标准

1. reference 仓库必须干净，commit、executable hash 和输入 hash 完全一致；
2. 单个 GPUMD 子进程只能看见一张 GPU；exit code 必须为 0；
3. 实际输出文件集合必须与 manifest 完全相同，缺文件或额外文件都失败；
4. XYZ/restart 的帧数、每帧 N、comment field 顺序、`Properties` schema、列数和每行 token
   数完全一致；
5. `thermo.out` 每段必须有 5 行固定 header、18 列数据，header 重复次数和每段数据行数
   必须匹配；
6. `neighbor.out` 当前按文本逐字节比较；
7. species、整数列及原子行顺序完全一致；同 species 原子的换序也会由逐行 position/observable
   比较捕获；
8. output time 和输出周期必须与 manifest 的显式时间/段行数一致；
9. reference 自检要求所有输出文件逐字节等于 committed golden；candidate 的结构字段仍是
   exact，只有浮点物理量允许第 5 节容差；
10. 任一 NaN/Inf 不对等、势路径断言失败、输入被 executable 修改或 restart 续跑失败均直接
    判失败。

### 4.2 case 专属标准

- `single_small_static`：必须由触发公式判为 small-box，静态逐原子 force/PE/9-virial 及
  全局 thermo 通过；neighbor 的多 image 计数被锁定。
- `single_large_nve`：必须判为 large-box；静态初始量通过；6 步短 NVE 的 position、velocity、
  force、PE、virial 和逐步 thermo 通过；两个 run 产生两个 thermo header；restart 是第 6 步
  覆盖结果且保持 8 行原子顺序。
- `multi_nvt_restart`：势必须是 2 types 普通 `nep4`；三个 run 的 measurement 清理/重新声明、
  NVT 温度和 thermo 输出周期通过；restart schema 必须是
  `species:S:1:pos:R:3:mass:R:1:vel:R:3`；runner 不转换该文件，直接将它复制为下一进程的
  `model.xyz`，续跑 2 步并再次比较输出。
- `nep_zbl_boundary`：势必须是 3 types `nep4_zbl`；脚本在启动前验证指定 Ba/Ti pair 的
  species 和 0.5 Å MIC 距离；跨边界 ZBL force 符号、能量和 virial 由静态逐原子结果锁定。

没有把长时间轨迹逐原子相等列为通过条件。本 harness 只逐原子比较 6 步以内的短轨迹。
长 NVE 已作为独立 `tests/long_nve/` suite 实现，不改变本目录的短程 committed goldens。
它比较真正 `E(0)` 的每原子总能量 offset、max excursion、drift slope、detrended RMS 及
十初态分布，并通过双向静态构型回放检查长期访问到的坐标。长期轨迹仍不要求逐步重合。

## 5. 文件合同、单位和数值容差

比较使用统一公式：

```text
abs(test - reference) <= atol + rtol * abs(reference)
```

结构字段没有数值容差。candidate 物理字段的当前阈值为：

| 类别 | rtol | atol | 单位/说明 |
| --- | ---: | ---: | --- |
| time | 0 | `5e-9` | fs；对应 XYZ `Time=%.8f` 量化 |
| lattice | 0 | `1e-12` | Å |
| position | `2e-8` | `2e-9` | Å；double XYZ 短轨迹 |
| velocity | `2e-7` | `2e-9` | Å/fs；double XYZ 短轨迹 |
| force | `1e-4` | `1e-6` | eV/Å |
| energy | `1e-5` | `1e-8` | eV；逐原子和总量均使用，但系统规模另由固定 N 锁定 |
| virial | `1e-4` | `1e-6` | eV；逐原子和 comment total |
| XYZ stress | `1e-4` | `1e-8` | eV/Å³ |
| temperature | `2e-6` | `2e-5` | K |
| thermo stress | `1e-4` | `1e-5` | GPa |
| restart position | `5e-6` | `5e-6` | Å；包含 reference `%g` 的 6 位有效数字量化 |
| restart velocity | `5e-6` | `5e-9` | Å/fs；包含 `%g` 量化 |
| restart mass | `5e-6` | `5e-6` | amu；包含 `%g` 量化 |

这些阈值不是从某个失败反向放宽的。当前路径中 model/积分/最终 force、PE、virial 是 double，
NEP 参数、descriptor gradient `Fp`、angular sums 和 directed partial 是 float；small-box 最终
scatter 对 double buffer 使用 atomic add。对于 future candidate，energy/force/virial 上限沿用
GPUMD 自己 `tests_pytest/conftest.py` 对当前 mixed-precision NEP 路径的经验上限；position、
velocity、thermo 和 restart 则按本 harness 的短时间尺度及文本量化单独收紧。若后续失败超出
阈值，必须先定位 neighbor、累加顺序、单位、列映射或 restart 量化原因，不能直接改大阈值。

单位合同同时被 header/schema 与数值比较锁定：

- XYZ：Time fs，Lattice/pos Å，velocity Å/fs，force eV/Å，energy/energy_atom/virial eV，
  stress eV/Å³；
- thermo：T K，KE/PE eV，stress GPa，lattice Å；
- restart：pos Å，mass amu，velocity Å/fs；它不保存 global time、force、PE、virial、RNG
  或 thermostat state。

## 6. 重复性实测

执行命令：

```bash
python3 tests/baseline/run_baselines.py --calibrate 30
```

一次完整 repetition 包含 4 个 case、5 个独立 GPUMD 进程（restart case 含 initial 和
resume），因此共实际执行 150 个进程。`calibration.json` 记录了 executable/GPU/toolchain、
比较次数、各类别最大绝对差和相对差。

结果：所有结构字段在 30 次中一致；全部已比较数值类别的 observed max absolute difference
和 max relative difference 都为 0。覆盖量包括 12,528 个 force 数、42,021 个 virial 数、
5,655 个 energy 数、12,528 个 position 数以及 thermo/restart 字段。这个结果允许锁定
“同一 binary、同一 GPU 架构、相同单卡选择”的 reference 输出为 byte-exact；它不证明换
GPU 架构、换编译器或未来 MPI reduction tree 后仍可 byte-exact。

## 7. 当前原 GPUMD 的非确定性来源

本矩阵通过显式 velocity 和确定性 `nvt_ber` 排除了实际观测到的 run-to-run 差异，但原
GPUMD 仍有以下潜在或范围外来源：

1. `src/main_gpumd/velocity.cu` 在非 DEBUG 构建中用当前时钟为 libc `rand()` 播种；没有
   `vel` 的 model 且没有稳定 seed 时，初速度跨运行变化。即使给 seed，libc `rand()` 序列也
   是平台实现相关，并按当前数组 index 逐原子播种。
2. `src/force/nep_small_box.cuh` 的 radial/angular/ZBL force 和 virial 使用并发 double
   `atomicAdd`；浮点加法不满足结合律，线程调度、GPU 架构或 launch 形状变化可能改变低位。
   本次 RTX 4090 的固定 8-atom small-box 在 30 次内没有观测到变化。
3. `src/integrate/ensemble.cu` 的 thermo 和 `src/measure/dump_xyz.cu` 的 virial total 使用
   固定 CUDA reduction tree。相同 launch 下本次完全稳定，但 atom reorder、N、block/patch
   划分、编译器或架构变化会改变求和顺序。
4. NEP 的 float parameter/intermediate 与 double atom/output 混合路径会放大不同 neighbor
   排序或不同硬件指令融合造成的低位差；短轨迹随后可能放大为轨迹分叉。
5. 只要一个进程可见多张 GPU，`Force::parse_potential()` 会选择 `NEP_MULTIGPU`；它的分区、
   pack/scatter 和累加顺序不是本 baseline 的 ordinary single-GPU 路径。harness 因此强制
   单张可见 GPU。
6. MPI 未来的 collective reduction tree、rank 数、local atom reorder 和 migration 都会改变
   非结合求和顺序；这些不是当前 GPUMD 单进程实测的一部分，不能用本次“30 次差为 0”推断
   MPI byte-exact。
7. `neighbor.out` 的计数器是进程内 `static int num_calls`，输出与 potential 调用次数/顺序
   相关；它在同一固定输入中确定，但增加 potential 或改变多段 run 调用顺序会改变其 step
   标签。

当前 case 没有 stochastic thermostat、random force、NHC chain 或 MPI，因此不把这些范围外
来源混进阈值校准。

## 8. 产物索引与更新纪律

- runner：`tests/baseline/run_baselines.py`；
- case/input manifest：`tests/baseline/manifest.json`；
- 原生输入：`tests/baseline/inputs/`；
- committed GPUMD 输出：`tests/baseline/goldens/`；
- 30 次实测：`tests/baseline/calibration.json`；
- 使用说明：`tests/baseline/README.md`。

只有在明确审查 reference 升级或 baseline 意图变化后，才运行
`--update-goldens`。更新必须同时审查 reference commit/二进制哈希、输入哈希、golden diff、
30 次 calibration 和本文档；比较失败本身不是更新 golden 或放宽容差的理由。
