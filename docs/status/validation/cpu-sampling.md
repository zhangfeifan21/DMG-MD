# CPU 采样验证

<a id="cpu-sampling-c1-20261009"></a>
## 2026-10-09 单节点 C1

当次检查覆盖同一 Linux PID namespace 中的 MPI rank 识别、CPU-only 缺失数据处理，以及启用和关闭采样的 GPU smoke。工具能够记录进程和线程的 CPU 使用情况。

缺少可靠阶段边界，或看不到上级 cpuset 限制时，对应统计留空。采样扰动、跨引擎固定绑定和跨节点仍须分别验收。[采样标准](../../standards/cpu-sampling.md) · [后续研究](../../plans/cpu-usage-study.md)

本页保留当次范围和结论，未附完整源码身份、环境和执行命令。
