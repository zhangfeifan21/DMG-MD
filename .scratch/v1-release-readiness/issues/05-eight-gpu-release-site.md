# 为完整长程发布矩阵确认可用的八卡环境

Status: resolved
Type: task
Label: wayfinder:task
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)

## Question

取得一套可核验的八 GPU 作业分配或维护者确认的资源窗口，记录硬件拓扑、每个 rank 的设备绑定、MPI/CUDA 通信后端及其与现有启动器的适配条件，使发布测试能决定何时排入完整矩阵。现有 L20 单机有四张卡，可以先承担 nightly。

发布 profile 要求三种体系、五个种子、1/2/4/8 rank 和两个后端，具体步数见[长程 manifest](../../../tests/long_nve/manifest.json)。跨物理节点声明另有[双节点计划](../../../docs/plans/multi-node-io.md)。

## Answer

2026-11-25，维护者确认第 1–5 问。本单以维护者确认的资源窗口作答，八卡机器的实测拓扑、GPU 身份、逐 rank 绑定与后端探针还没有取得，由实施工单 [21 完成八卡 100,000 步 release 矩阵](../../v1-release-implementation/issues/21-long-release-eight-gpu.md) 的第一条验收框在机器到位后记录。查核时的源码提交为 `837371d`。

1. **资源窗口。** 八 GPU 机器预计 2026-12 下旬到位，大概率为 8 × RTX 5090 的单机，共 1–2 台。型号、台数和到位日期以实际交付为准。与此不符时（例如不足 8 卡），重开本单。
2. **完整 release 矩阵的站点。** [长程 manifest](../../../tests/long_nve/manifest.json) 的 `release` profile（7 个用例、种子 0–4、1/2/4/8 rank、HostStaged/CudaAware）全部在同一台 8 卡机上用同一份二进制运行，跨 rank 重启和回放不混入两种硬件的差异。L20 继续承担四卡 nightly，以及按[长程回归怎样衔接 GPUMD 5.9.1 短程基线](02-long-regression-reference.md)第 4 条只到 P4 的 release，各自登记覆盖范围，不与八卡结果合并成一张覆盖表。
3. **到位后、启动完整矩阵前须记录的内容。** 按[CPU/NUMA 启动与核验](../../../docs/standards/cpu-binding.md)保存 GPU PCI/NUMA 拓扑（`nvidia-smi topo -m`）、驱动与 CUDA 版本、8 张卡的 UUID 和逐 rank 绑定。用 `tests/mpi/check_environment.py` 做 8 rank 两后端预检，CudaAware 须记录 `cuda_aware_p2p_self_test=passed`，自检失败回退到 HostStaged 时如实报告。两种后端都可用之后，再启动完整矩阵。
4. **已知的适配条件。** 以下为源码可确认或待验证的事项，由工单 21 在到位后逐项核实：
   - 源码可确认：`CMakeLists.txt` 的默认 `CMAKE_CUDA_ARCHITECTURES` 为 `75;80;86;89;90`，不含 RTX 5090 的计算能力 12.0。L20 上 CUDA 12.9 的 `nvcc --list-gpu-arch` 列出 `compute_120`。在 5090 上构建时须显式加入 `120`，并在 provenance 中记录实际取值。
   - 待验证：长程参考程序按工单 02 锁定为普通 GPUMD 5.9.1 `11ba6fb`。新站点要有可在 5090 上运行的同提交参考构建，并记录其二进制哈希。
   - 待验证：消费级 GPU 之间的 CUDA 点对点访问、UCX `cuda_ipc` 能否工作，以及 8 卡的 PCIe/NUMA 分布，只能实测得出。
   - 站点的软件栈（MPI、UCX、CUDA、驱动）与 L20 不同时，按实际版本登记，不沿用 L20 的环境脚本身份。
5. **适用范围与性能站点。** 跨物理节点的正确性、重启、I/O 和性能仍不在 V1.0 范围内。到位两台时，也只在单机内运行 release 矩阵，双节点按[多节点 rank I/O 隔离验收计划](../../../docs/plans/multi-node-io.md)另立后续工作。V1.0 的三项对标和 DMG-MD 自身强扩展只在 4 × L20 上取得，结论限于该站点。5090 的性能另行实测，不从 L20 外推。L20 上的先导计时用于确定下一阶段的开发目标。
