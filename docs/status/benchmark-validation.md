# Benchmark 功能验证（2026-09-22）

类别：状态与实测备忘。这里只记录已执行内容，不发布多卡性能结论。

## 代码与环境

- DMG-MD 基线 revision：`945b6442d284478ab9c1b5a8f7a09dc707b37182`；本次增加测试/文档的工作树
  修改未提交，生产 runtime 与 NEP 未修改。
- GPUMD：`9d23496e41319b9e2af5221a7df6285387401d1e`，参考仓库保持干净。
- candidate SHA-256：`13da7a81c9c77f46b53dd45ada81d7a593634800c30b2714c13d2fa3bc93b80d`。
- reference SHA-256：`3ab365cc8fccdb979697d5a9b28ff9fe1cb14c89d1e08d2063ac1e5b7786e414`。
- 主机 n4；CUDA 12.9.86、Open MPI 5.0.10、UCX 1.22.0；source `../env/md-mpi.sh`。
- GPU 2，RTX 4090，UUID `GPU-8264a4fc-58ff-d92c-b45c-1ae435b9574d`。
  运行前其他 7 卡有计算进程驻留，本次未使用那些 GPU。
- 每次真实 GPU 运行均先通过现有 MPI/CUDA 环境门槛（1 rank CudaAware collective/p2p probe）。
  GPU 验证在沙箱外执行；沙箱内 nvidia-smi 的驱动不可见不作为失败结论。

## 精确命令与结果

从 DMG-MD 仓库根目录运行：

```bash
source ../env/md-mpi.sh
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
ctest --test-dir build --output-on-failure -E domain_neighbor_cuda
python3 tests/benchmark/test_benchmark.py
ctest --test-dir build --output-on-failure -R benchmark_analysis
bash -n scripts/build_benchmark.sh scripts/md-mpi.sh.example
```

CPU CTest 共 6 项通过；后续补充监测修复后，benchmark 8 个单元测试与对应 CTest 重新通过。
测试覆盖计时解析、强弱扩展公式、无效样本剔除、GPU 绑定与后端回退拒绝、thermo 注释头，以及
子进程自建 session 时的超时清理。所有四种 profile 的 `--dry-run` 通过，分别生成
12/36/216/72 个 trial；最大样例 4,915,200 原子。Shell 语法检查、文档链接与 diff 检查通过。

49,152 原子冒烟：

```bash
python3 tests/benchmark/run_benchmark.py --profile smoke --devices 2 --ranks 1 \
  --output /tmp/dmgmd-benchmark-smoke-final-20260922 --timeout 180
```

GPUMD、DMG-MD HostStaged、DMG-MD CudaAware 三项均 status=ok；每项预热 2 步、测量 5 步。
首次冒烟发现 thermo 注释头解析问题，修复后以上命令重跑通过。

1,048,576 原子容量与长一些的监测验证：

```bash
python3 tests/benchmark/run_benchmark.py --profile pilot --cases carbon_1m \
  --devices 2 --ranks 1 --warmup 10 --steps 100 \
  --output /tmp/dmgmd-benchmark-million-final-20260922 --timeout 300
```

三项均 status=ok，最终 thermo 有限。此前试运行发现 Open MPI rank 自建进程组会被误判为外来
任务，改为 `/proc` 父子关系追踪，并补充独立 session 子进程清理测试；以上 final 命令验证修复。
正式计时数据完整，但预热短、只有一次重复，不能作为可靠的两引擎性能排名。

本地日志副本（生成物已被 `.gitignore` 排除，未复制大模型；完整原始目录保留于上述 `/tmp`）：

- [单卡 smoke 汇总](../../dmgmd-benchmark-validation-20260922/smoke/summary.md)
- [百万原子单卡汇总](../../dmgmd-benchmark-validation-20260922/million/summary.md)
- 对应目录含 metadata/二进制与输入哈希、原始 stdout/stderr、telemetry、JSON/CSV；源码生成器
  hash 以各次 metadata 为准，较早 smoke 的脚本版本先于 MPI 监测修复。

## 尚未验证的边界

- 未执行真正 2/4/8 卡 benchmark、完整 standard/capacity 矩阵，也未验证 3–5 百万原子在
  4090 上一定能放入显存。矩阵已定义和脚本单卡通过不等于多卡性能已通过。
- 未在另一台服务器实际部署；新增构建脚本通过语法检查，迁移手册按本机工具链/源码约束编写，
  目标服务器必须重新构建并执行环境及数值门槛。
- 未重跑生产数值 GPU 矩阵，因为本次没有修改物理/runtime；正式发布性能时仍应关联当前
  正确性验收记录。

后续按 [benchmark 操作说明](../../tests/benchmark/README.md) 先独占设备 smoke/pilot，再正式测量。
