# 工单与规格

以下路径均相对 L20 源码仓库根目录。从本地协调目录发起任务时，通过 SSH 读写服务器上的同一份工单。

## 功能工单

每项功能使用 `.scratch/<feature>/`，规格放在 `spec.md`。实施工单逐张保存为 `issues/NN-<slug>.md`，从 `01` 编号。

工单开头用 `Status: <状态>` 记录分流结果，讨论按时间追加到 `## Comments`。[状态取值](triage-labels.md)

技能要求发布规格或工单时，使用以上路径。读取时给出完整路径，或先确定功能目录再按编号查找。

## Wayfinder 探索工单

`.scratch/<effort>/map.md` 记录笔记、已有决策和待解问题。子工单放在同目录的 `issues/NN-<slug>.md`，正文写明要回答的问题。

`Type:` 为 `research`、`prototype`、`grilling` 或 `task`。`Blocked by: 01, 02` 表示依赖同目录的两张工单。

按编号选择首张未认领、未解决且依赖已解决的工单，开始时写入 `Status: claimed`。完成后将答案追加到 `## Answer`，改为 `Status: resolved`，并在 `map.md` 的决策部分补上摘要和链接。
