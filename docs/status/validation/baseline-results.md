# GPUMD 短程数值基线

<a id="baseline-20260909"></a>
## 2026-09-09 初版 golden

初版普通 GPUMD 冻结实现为 `9d23496e`。回归覆盖小盒静态求值、大盒短 NVE、含 ZBL 的边界，以及多段 NVT/restart。相同输入用于核对输出文件结构和科学字段。[测试方法与容差来源](../../../tests/baseline/README.md)

<a id="l20-baseline-20261120"></a>
## 2026-11-20 GPUMD 5.9.1 与候选工作树

当日在 NVIDIA L20 上，用 Release/sm_89、CUDA 12.9.86 和普通 GPUMD 5.9.1（`11ba6fb`）重新生成短程 golden。三个 restart 改用 17 位有效数字，续跑段的 thermo 和轨迹随输入精度改变。其余 15 个科学输出逐字节一致；将旧 restart 输入新参考时，续跑输出仍与旧 golden 一致。

`0e8360f` 加工作树修改的候选和新版普通 GPUMD，各通过 4/4 项。manifest 记录实际参考二进制身份。以下为仓库根目录下的复验入口：

```bash
source ../env/md-mpi.sh
python3 tests/baseline/run_baselines.py --reference ../gpumd-reference/src/gpumd --device 0
python3 tests/baseline/run_baselines.py --candidate ./build/dmg-md --device 0
```

输入、参考身份和容差见[基线配置](../../../tests/baseline/manifest.json)。

<a id="l20-long-nve-reference-gate-20261120"></a>
## 长程入口

长程 NVE 仍锁定前一参考版本。长短 manifest 不一致时，运行器在启动 case 前退出。因此，短程迁移通过后，长程参考身份和 release 规模回归仍需单独处理。[长程操作说明](../../../tests/long_nve/README.md)
