# 为完整长程发布矩阵确认可用的八卡环境

Status: open
Type: task
Label: wayfinder:task
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)

## Question

取得一套可核验的八 GPU 作业分配或维护者确认的资源窗口，记录硬件拓扑、每个 rank 的设备绑定、MPI/CUDA 通信后端及其与现有启动器的适配条件，使发布测试能决定何时排入完整矩阵。现有 L20 单机有四张卡，可以先承担 nightly。

发布 profile 要求三种体系、五个种子、1/2/4/8 rank 和两个后端，具体步数见[长程 manifest](../../../tests/long_nve/manifest.json)。跨物理节点声明另有[双节点计划](../../../docs/plans/multi-node-io.md)。
