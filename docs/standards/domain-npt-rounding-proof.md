# 局部 NPT affine 缓存的有限精度证明

本文是可审计的条件性证明，不是 GPU 验收或统计结论。它针对正对角、全周期、NEP large-box 的 affine 路径；普通路径和 NEP 公式不变。实现若不满足下列任一前提，不能据本文复用；建表前的内存安全条件失败时，必须在建表前拒绝或选择安全 builder，重新建表本身并不能修复它。

## 记号和前提

所有存储的 box、位置、cutoff 以其二进制值为精确输入。`u=2^-53`、`v=2^-24`，`γk(t)=kt/(1-kt)`。假设 CPU 和 CUDA 的 double、float 为 IEEE 754 最近舍入，除法、平方根正确舍入，允许逐次运算或 FMA；GPU 对 float 次正规数 flush-to-zero 也在下界内。不得启用破坏这些界的 fast-math 或近似除法。若生成代码不满足此假设，此证明不适用。

参考和当前各轴 `L` 都在 `[10^-3,10^6]`，`Lmax` 是这六个长度的最大值，`R*` 是所有实际单边 force 和 dependency cutoff 的上界，在 `[10^-3,10^6]`，`s=skin=1`。实际 build 的二进制半径记为 `B=fl(rc+s)`，其中 `rc` 是 NEP builder 实际的 `rc_radial_max`；要求 `1≤B≤10^6`，不能把不同的理论 cutoff 当作 `B`。`R*=state.d_dep-s` 反算可能有 1 次舍入，其差包含在下面的 `E_force`。各保存位置和参考位置在对应 `[0,L]`，integer image 绝对值严格小于 `10^6`。`Lmin/2 > R*+s+E`，`E=256 ε32 Lmax=512 v Lmax`，且乘加比较的主机运算取向下保守结果；实际实现另应保守处理此 guard 的舍入。下列仅为普通尺度与索引证明，不替代运行时对非有限值、通信、布局代次及溢出的检查。

必须在**首次及每次**调用 cell builder 前确认其整数表达式安全，并对将提交的每个盒做同样预检。令 `h=B/2`，对每轴以向上保守值计算 `Nhat=ceil(2L/B)+2`，要求 `Nhat` 和三个 `Nhat` 的乘积不大于 `INT_MAX/4`；计算本检查自身也不得溢出。这个条件保证实际 `n` 不超过 `Nhat`，且 `neighbor.cu` 中 `cell_id + k*nx*ny + j*nx + i`、随后单次周期修正及线性索引的各中间 int 表达式均安全（`|i|,|j|,|k|≤2`，各 `n≥4`）。仅要求 bin 乘积不超过 `INT_MAX` 不够：`(nx,ny,nz)=(2000,2000,536)` 的乘积为 2,144,000,000，但最大 cell 加 `2*nx*ny` 即越界。内存分配失败仍须报告，不能将其视为复用许可。

## Box、cell 和 ±2 扫描

`Box::get_inverse` 对正对角盒实际上先乘两个对角元，再用按 `Lx*(Ly*Lz)` 计算的行列式作除法；`get_volume`、`get_area` 和 `get_num_bins` 分别再算体积、面对角元乘积的平方、开方、厚度除法和 `floor(thickness/h)`。长度范围使乘积、面积平方及其逆的所有非零中间值为正常 double，且远离溢出。零的非对角项精确为零。逐操作计数给出如下宽松界；FMA 合并零项不增加次数：

| 数量 | 相对精确值的界 |
|---|---|
| 体积 `Lx*(Ly*Lz)` | `γ2(u)` |
| 面积 `sqrt(fl(fl(La*Lb)^2))` | `γ3(u)` |
| inverse 对角元 `(La*Lb)/volume` | `γ4(u)` |
| 厚度 `volume/area` | `γ6(u)` |
| `thickness/h`，及 `rc_inv=1/h=2/B` | 分别 `γ7(u)`、`γ1(u)` |
| cell 运算 `fl(fl(fl(inv*x)*thickness)*rc_inv)` | 相对 `x/h` 不超过 `γ14(u)`，`x=0` 时恰为零 |

因 `h=B/2` 是精确二进制缩放、处于正常范围，builder 两处计算的 `1/h` 与 `2/B` 是同一实数的正确舍入，故 count、contents 和 scan 使用相同 cell。令实际整数 bin 数 `n=floor(fl(thickness/h))`，`z(x)` 为实际 cell 运算，以上界给出 `|z(x)-x/h| h ≤ γ14 L`，以及 `|n-L/h| < 1+γ7 L/h`。guard 给 `γ14 L/h < 1`。每轴 floor 之后按 n 模折叠；包括 x=L、余段折回 0 和舍入到 n 的情形。

精确算术下，令 `n=floor(L/h)`，末尾余段和 cell 0 相连，任意环向 cell 索引距离大于 2 的两点，其最短周期距离至少 `2h`。有限精度下把每个 cell 边界按 `|z-x/h|h ≤ γ14 L` 平移，并把周期接缝处的 bin 数不确定性按 `γ7 L` 扩张，即使 `floor(thickness/h)` 在整数附近多一或少一，也只会使这个下界减少不超过 `2γ14 L+2γ7 L`；取更宽松的 `64u L`。因此真实单轴周期距离严格小于 `B-64u L` 必然被 ±2 扫描覆盖。floor 的不连续性在这里通过边界位置包络处理，不能直接把 floor 本身当成连续运算。各轴分别应用；欧氏距离小于该阈值蕴含每一轴满足阈值。

## float 比较和连续 lift

在 `[0,L]` 的一轴上，double 相减误差至多 `uL`；转换为 float、float 盒长转换、MIC 一次加减及其舍入合计小于 `8vL`。当前和参考的半盒隔离使 cutoff 附近不会选择错误的 MIC 像。三轴距离的扰动小于 `8√3 v Lmax`；三次平方、两次加法、cutoff 的 float 平方和比较，以 `γ5(v)` 和一次 cutoff 转换计算，折回距离单位小于 `16v Lmax`。若 float 次正规数被 flush，其绝对距离余项小于 `sqrt(6*2^-126)`；由 half-box guard 和 `s=1`，它小于 `v Lmax`。包括 `R*=fl(d_dep-s)` 与 builder/halo 半径的几次 double 加减，单次比较所需距离预算小于 `32v Lmax + 64u Lmax`。这不是独立测试拟合出的系数。

每个 owned 原子当前保存位置为 `x`、参考位置为 `q`、整数 image 为 `m`，定义精确参考坐标系位移 `U=L0*(x/L+m-q/L0)`。GPU 用两个除法、两次加减和一次乘法得到每轴 U，再用三平方、求和和主机平方根归约；`|x/L|,|q/L0|≤1`、`|m|<10^6`，中间绝对值上界 `10^6+2`。对这些操作逐项应用绝对误差和 `γ12(u)`，再对三轴求欧氏范数，误差小于 `128u Lmax(10^6+2)`；次正规数绝对项也被该值覆盖。实现的 `lift_error=1024 ε64 Lmax(10^6+2)=2048u Lmax(10^6+2)` 因而足够。sqrt 后加该值再向上取下一 double 得到 `δ`，是全 rank owned 位移模长的上界。image 与实际 wrap 必须在同一 kernel 中执行同一比较分支；缩放和 wrap 的舍入作为保存位置的真实变化计入 U，不能将其假设为精确操作。image 饱和、跨越多盒、非有限数及检查不到的 owned 原子均不允许复用。

## 主机 slab、halo 与两跳闭包

layout 的 slab 端点由 rank/P、乘 L 和一次宽度相加组成，`distance_to_slab` 最多涉及两次加减和 `min`。`P` 是正 int 且 rank 精确转 double，端点相对精确 `rank*L/P` 的绝对误差小于 `4uL`；比较坐标、周期候选距离及迁移时 inverse×position 与乘 P 各取宽松界后，**端到端 host slab / owner 边界位置差小于 `64u Lmax`**。在端点舍入不同导致分类不同之处，此界用于扩大需要的参考距离；不能声称判断分支恒相同。`d_dep=fl(Rforce+s)`、`d_coord=fl(max(fl(Rforce+Rdep))+2s)` 的下舍入及 `slab_width>=d_coord` 的舍入再各按 `8uLmax` 计入。两跳跨 slab 只发送相邻 face 的推理还要求每次 box 预检使用当前固定分解轴、slab 宽不小于 d_coord；其数值比较的不足包含在上述余量中。P=2 两面同一 peer 时发送去重且单一 wrapped 槽服务两个方向。

令实数参考边长度为 `r0`，当前 kernel 消费的边长为 `r`，每端位移不超过 δ，`α` 是实际三轴 `L/L0` 的向下界。三角不等式对任意周期像同时成立：`r0 ≤ r/α+2δ`。由于各边半径 `R≤R*`，且无论压缩还是膨胀都有

`(R+E_force)/α - R ≤ max(0,1/α-1) R* + E_force/min(α,1)`，

逐项向上舍入计算下式，严格小于 skin 时可复用：

`2δ + max(0,1/α-1) R* + E_force/min(α,1) + E_build < s`。

此式对不同 typewise 半径逐条成立。不能在 α>1 时把 `(R+E)/α-R` 直接用 R* 端点代替：该表达式在 R 较小时更大。独立 oracle 的 `α=2, R*=10, R=0.01, E_force=0.2, 2δ=0.98, s=1` 给出旧表达式通过但实际所需预算超限的精确有理数反例。

`E_force=E_build=512vLmax`。每条边的 cell 漏扫余量 `64uLmax`、build 的 float 距离余量 `32vLmax+64uLmax`、slab/owner 及半径舍入总计 `128uLmax` 可全部由独立的 `E_build` 支付，合计仍远小于 `512vLmax`；force 的 float 误差由独立 `E_force` 支付。参考 builder 因而包含对应边；参考 `d_dep` 内有首跳中心。第二条边同样各有一份严格 skin 预算，三角不等式给出两边参考长度和严格小于相应 typewise 半径和加 `2s`，而 slab/发送/分类误差已由两条边的 E 预留，因此参考 `d_coord` 包含第二跳候选。当前 owned 继续由参考 manager 积分，重建时才迁移；证明不要求普通步的几何 owner 保持不变。

这些推论依赖 `R*` 确实覆盖所有 NEP radial-first、angular 和 ZBL 的实际消费，并依赖 halo 更新每次送达对应 global ID、缓存 row 构建成功、CPU/GPU 浮点模式符合前提。任何这些条件未证实都应保持未验收；数值差分不能替代源码和运算语义审计。

## 独立检查

`tests/npt/test_domain_rounding_cell_oracle.py` 用 Python `Fraction` 将所选二进制输入当精确有理数，覆盖 cell 精确宽度反例、整数中间式反例、实际 box 运算树、floor 边界两侧和 slab 端点。它不调用生产 helper，且不是 CUDA 执行或所有输入的穷尽证明；编译器指令语义与上述运行时前提仍须另行验证。
