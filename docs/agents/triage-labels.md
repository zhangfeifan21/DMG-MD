# 工单分流状态

普通工单在 `Status:` 行使用下表取值。Wayfinder 工单的认领和完成状态另见[工单约定](issue-tracker.md#wayfinder-探索工单)。

| 技能角色 | 工单取值 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者判断处理方式 |
| `needs-info` | `needs-info` | 等待报告人补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格明确，可由代理独立实施 |
| `ready-for-human` | `ready-for-human` | 需要人工实施 |
| `wontfix` | `wontfix` | 维护者决定不处理 |
