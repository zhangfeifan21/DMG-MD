# 文档整理与构建清理验收

## 目标与阶段索引

本文件按同一验收目标整合日期阶段报告。日期按原记录的执行/复核口径列示；若实施和执行跨日，单独注明。各阶段原始身份、命令、结果、失败尝试、边界与产物证据均保留在对应章节。
| 阶段 / 目标 | 执行日期或阶段 | 结论与边界 | 章节 |
|---|---|---|---|
| G-B 后历史产物与原始证据清理 | 2026-11-05 | build、checkpoint、overlap 三分区完成；旧公共服务器 raw 已从原目录清除，G-B 最终结果与审计结论摘要可查询 | [报告](#artifact-cleanup-20261105) |
| O5 后续文档整理与构建清理 | 2026-10-21 | PASS；仅文档与本机构建产物维护 | [报告](#documentation-build-cleanup-20261021) |


<a id="artifact-cleanup-20261105"></a>

## 2026-11-05：G-B 后历史产物与原始证据清理

类别：历史产物迁移、忽略 Git 的本机构建清理与旧实验归档清理。三分区状态：**总体完成**。G-B 已通过后，维护者授权清理历史数据；公共服务器的旧 benchmark 结果作为历史探索记录，后续性能参考和采用判断以专用服务器重跑为准。没有修改生产代码、测试 fixture/golden 或只读 reference checkout；没有构建、运行 GPU 或重跑统计，没有 stage、commit 或 push。

本日清理改变了历史原始数据的保存位置和范围。2026-10-21 小节中“原始 trial、输入、源码归档、科学输出和 trace 均保留”只描述当日状态，不代表当前仍完整保存。旧公共服务器历史 raw/archive 已从外层 checkpoints（相对项目根目录为 `../checkpoints`）和 `dmgmd-overlap*` 目录移除；用户要求只保留结果，本次仍保留 G-B 结论、稳定 O 系列结果文档，以及支撑正式结论所需的结构化结果和必要审计信息。第一轮 NPT SCR 迁移时，24 项正式 artifact SHA 经核验与源端一致；随后按“只留最终结果”要求删除中间材料。O 系列旧数据不用于重新统计或推广为新 benchmark 结论。

| 分区 | 处理与结果 |
|---|---|
| build | 删除 5 个完整历史构建目录及两个目录中的旧对象/源码副本；删除文件合计占用 316,264,448 bytes（按 `st_blocks × 512`）。 |
| checkpoint | 第一轮迁移时将 13,343 个文件（21,543,811,621 logical bytes）迁至仓库；原外层 `checkpoints` 已不存在且无 symlink。第一轮正式小型 evidence 为 1,036 文件（24,089,684 B），位于 `docs/status/validation/evidence/npt-scr-gb/`；raw/诊断等按用途存于 ignored `artifacts/validation/npt-scr-gb/`，共 12,309 文件（21,519,860,264 B）。以上数量是第一轮迁移时的记录，不代表第二轮精简后的当前文件数。 |
| overlap | 移除 15 个顶层 `dmgmd-overlap*` 目录；清理前目录 `du` 合计 300,267,298,816 bytes（约 279.65 GiB）。该数表示清理前规模，不等同于当前全盘可用空间净增量。保留稳定结果文档与 `assets/o5-net-speedup-20261016.png`（SHA-256 `63fa215387039c17eb62327472a6bb3f670b0bb7524f1a61ce1b1e1a566f0aca`）。 |

G-B NPT SCR 结果入口为 [NPT SCR 确认索引](npt-scr-n3-confirmation-index.json) 和 [NPT SCR 验证报告](npt-validation.md#npt-scr-confirmation-20261104)；正式结果和各 O1–O11 阶段结论仍由现有稳定主题报告与 O2/O3 validation 报告查询。G-B 的正式统计结论不变；历史公共服务器 O 系列数据不构成新的统计或性能采用判断。

### 结果保留精简（第二轮）

第二轮精简完成：从 `docs/status/validation/evidence/npt-scr-gb/` 删除 1,035 个中间文件，逻辑大小 23,769,480 B；目录内仅保留最终结果 `results/n3/paired-analysis.json`（320,204 B；SHA-256 `ddc4b004888706b2915d60f494a1590869dda6f637b35acc0a67e2aa5c565461`）。live index 现仅列 4 项实际 artifact（报告、manifest、protocol 和旧 18-look 索引）；科学数值、适用范围、G-B PASS 与日期字段保持不变，index SHA-256 为 `2e2e516f89674003247378e1b992532f6b0b9a841e4f72436252b63295ad1f10`。最终结果及迁移、环境、执行与审计结论整合在现有 NPT 报告/index；不保留中间源/输入快照、日志、恢复脚本、receipt 或逐作业 records。保留 artifacts 原始数据、测试源码、注册 manifest/protocol、reference 与 build；未另行归档或打包复制中间材料。

build 分区删除了完整目录 `build-o65/`、`build-o85/`、`build-o86/`、`build-tests-layout/` 和 `build-tests-layout-o65/`，共 2,424 个普通文件。保留唯一默认构建 `build/`，其 `dmg-md` SHA-256 仍为 `dc484bfb6a29bbf93fffdc3a415c886ca9ec23220ca3fdc7d3583dff08c4db32`。

`build-gpumd-reference-cuda12.8-n3/` 仅保留 `source/src/gpumd`、`build-provenance.json` 与 `source-manifest.json`，删除其余 399 个普通文件；三项保留物哈希如下。保留的 GPUMD 可执行身份与 NPT SCR 报告及确认 manifest 绑定一致：

| 保留文件 | SHA-256 |
|---|---|
| `source/src/gpumd` | `045d1edbf893d6dcb8e18cff0bff27eea70070e078b7c4950cbe1c5cc37ac5f7` |
| `build-provenance.json` | `f534f46c876cbd284a017617340b706c51c3d969565d687ccf211c9ec4af65fd` |
| `source-manifest.json` | `3ce8ff52f98421e1dc38c37a2f3266844e4d712a52f291578a78c1a7df21a730` |

`build-lammps-nep-kk-preserved/` 仅保留可执行文件 `lmp`，删除其余 842 个普通文件。保留二进制 SHA-256 为 `3c9fb06b0489ae93ad58c5d2680d8818dc9cbe1275e5c7bf2c6e176b301005e6`。在先加载支持环境 `source ../env/md-mpi.sh` 后，`ldd` 检查的动态依赖全部解析；因此该目录无需保留库或旧构建树。

本 build 分区共删除 3,665 个普通文件，删除文件合计占用 316,264,448 bytes（按 `st_blocks × 512`）。五个完整历史目录与两个收缩目录在删除前的分配空间为 427,200,512 bytes，保留项仍占 110,936,064 bytes。Git 索引对所有删除目标均无跟踪文件。此次工作只整理历史产物，不更改当前默认构建、科学结果或生产默认设置。

第一轮迁移的只读核验记录：当时维护的 65 份 Markdown（排除冻结 evidence 副本）共 720 个本地文件/片段链接，断链 0；当时 live-index 中 24 项正式 artifact SHA 与原索引快照 SHA 均一致；三个只读 reference 的 HEAD、Git 状态和 ignored 路径清单摘要与清理前一致；当时 evidence 与 artifacts 迁移目录无 symlink。这些是第一轮迁移时的核验结果，不表示第二轮精简后相关文件仍全部存在。



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
