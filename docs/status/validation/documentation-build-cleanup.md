# 文档整理与构建清理验收

## 目标与阶段索引

本文件按同一验收目标整合日期阶段报告。日期按原记录的执行/复核口径列示；若实施和执行跨日，单独注明。各阶段原始身份、命令、结果、失败尝试、边界与产物证据均保留在对应章节。
| 阶段 / 目标 | 执行日期或阶段 | 结论与边界 | 章节 |
|---|---|---|---|
| O5 后续文档整理与构建清理 | 2026-10-21 | PASS；仅文档与本机构建产物维护 | [报告](#documentation-build-cleanup-20261021) |



<a id="documentation-build-cleanup-20261021"></a>

<a id="documentation-build-cleanup-20261021--o5-后续文档整理与构建中间文件清理2026-10-21"></a>

## 2026-10-21：O5 后续文档整理与构建清理


类别：文档与本机构建产物维护。状态：**PASS**。
HEAD 保持 `6b8efc505f19d539d4eaa916fe2c4793bf9b9c79`；保留进入本轮时已有的
O5 工具、测试与文档修改。本轮没有改生产源码、构建新二进制或执行新的 GPU 实验。
没有 stage、commit 或 push。

<a id="documentation-build-cleanup-20261021--文档结果"></a>
### 文档结果

- 将 full/serial/wait/poll、4.60 ms 拆分增量、诊断证据与未知原因写入
  [O5 后续分析](../performance/communication-computation-overlap-results.md#o5-cost-20261021-report)。
- 将下一阶段分成 [O6–O11](../../plans/communication-computation-overlap.md) 六个独立 goal，
  各自包含目标、依赖、范围与正确性/实测完成条件。状态均为 PROPOSED，尚未执行。
- 同步根 README、AGENTS、文档索引、当前状态、风险登记及相关计划，清除
  “O4/O5 待执行”“DMG-MD 统一绑定入口未实现”等过期描述。
  跨引擎绑定、完整 C2/C3、跨节点及精确 R 等真实缺口保留。
- O5 正式报告与冻结交付快照不改写；报告 SHA-256 仍为
  `b3e1ccb2473e226049f26478e2b315e1462374c55edc7e1f56d83a7e0a0adbab`。

<a id="documentation-build-cleanup-20261021--清理范围"></a>
### 清理范围

只删除下表目录 `CMakeFiles/` 内已核对的 `.o` 和 `.o.d` 普通文件，
不递归删除目录、不清理 reference、不使用 `git clean`。
文件均未被 Git 跟踪，也不在本机 SHA-256 证据清单中。
| 构建目录 | 删除文件数 | 释放的已分配空间 MiB |
| --- | --- | --- |
| `build/` | 25 | 7.43 |
| `build-lammps-nep-kk-preserved/` | 900 | 152.43 |
| `dmgmd-overlap-o5-20261016/build_o1/` | 25 | 7.00 |
| `dmgmd-overlap-o5-20261016/build_o2/` | 25 | 7.36 |
| `dmgmd-overlap-o5-20261016/build_o3/` | 25 | 7.38 |
| 合计 | 1000 | 181.60 |

逻辑大小 188514783 bytes，已分配空间 190423040 bytes（按 `st_blocks × 512` 统计）。
删除前逐文件保存路径、大小、SHA-256，并在删除前重新核验内容未变。
126 个受保护的可执行文件、库、CMake cache、编译/链接参数文件哈希清理后全部一致。
当前 `build/dmg-md`、O1/O2/O3 锁定二进制、O4 副本及 LAMMPS `lmp` 保留；
O1–O5 原始 trial、输入、源码归档、科学输出和 trace 均保留。

后续正常编译会重新生成缺少的 object。保留的 LAMMPS cache 仍含旧绝对路径，
不能当作可迁移重建目录；需要重建时按 AGENTS 从只读 reference 生成独立工作源码。

<a id="documentation-build-cleanup-20261021--核验与重算"></a>
### 核验与重算

仓库根目录、同一受支持环境下实际执行：

```bash
source ../env/md-mpi.sh
python3 dmgmd-maintenance-20261021/verify-maintenance.py
bash dmgmd-overlap-o5-20261016/recompute.sh
git diff --check
```

维护核验 PASS：本轮修改/新增 Markdown 的本地链接、O6–O11 标识、
引用的主样本/阶段/分类/短 trace 数值均核对通过；三个 reference 的 HEAD、Git 状态
及既有 ignored 文件路径/大小/mtime/mode 前后一致。126 个保留文件哈希一致。

O5 离线重算退出码 0：分析工具与原始证据哈希通过，初始五重复的 summary/ratios
与冻结副本一致，最终 `analysis-status.json=PASS`，主/历史/退化准入 870/870、
独立确认 40/40、诊断 384 rank 行/88 组、20 组 profile 保持完整。
本轮不涉及数值代码改动，未重新运行无关 GPU 正确性矩阵。

本机审计保存在忽略 Git 的 `dmgmd-maintenance-20261021/`：
`before.json`、`cleanup-review.json`、`cleanup-result.json`、`verification.json`、
`verify-maintenance.py` 及 `o5-recompute.log`。删除清单和原始 O5 产物均不随 Git 自动分发。
