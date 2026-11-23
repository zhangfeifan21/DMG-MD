# 领域文档

DMG-MD 使用单一领域上下文，术语和架构决策从源码仓库根目录读取。

探索源码前，先读已有的 [GLOSSARY.md](../../GLOSSARY.md)，再查 `docs/adr/` 中与任务相关的记录；目录不存在时继续工作。术语明确后，由 `domain-modeling` 更新词汇表。新的架构决策只有在取舍难以逆转、未来读者需要理解原因时才单独记录。

现有 D 编号以[架构决策](../standards/architecture-decisions.md)为准。修改并行模式或原子所有权时，同时读取 [MPI 运行时](../standards/mpi-runtime.md)和[数据布局](../standards/data-layout.md)。

工单、设计和测试共用已有术语。提案与已生效决策冲突时，写明决策编号、冲突内容和重新讨论的理由；当前规则继续由 `docs/standards/` 维护。
