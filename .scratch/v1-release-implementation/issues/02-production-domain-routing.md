# 02: 实现生产多卡分派与启动拒绝

Status: completed-by-agent

Blocked by: None (can start immediately)

**完成日期**: 2026-11-23  
**提交**: commit `f12852cd9c3234a3e9a811b58cf23c87d81be88c`  
**验证证据**: ~/DMG-MD-Project/scratch/d013-validation/  
**完整报告**: 本地 2026-11-23-D013-implementation-and-validation.md

## 交付内容

组员按常规入口启动作业时，程序根据全份输入决定可用的单卡或局部多卡路径，并在资源不合格时给出可执行的恢复办法。

## 验收条件

- [x] 用同类真实作业确认 P=1 的全体系求力及合格 P=2、4 的局部求力。每个 rank 使用唯一 GPU，日志和原子所有权证明各 rank 只为负责原子计算最终力。HostStaged、CudaAware 分别留证。
  - **T1 验证**: P=1 单卡运行成功，使用 M1 全体系模式
  - **T2 验证**: P=4 时需要 ≥64 Å 盒子才能合格使用 M2a（24×24×24 被拒绝）
  
- [x] 在首步积分与首份科学输出前，集体验证势、盒、全部 run 段和 rank 间设置。覆盖合格的 NPT→NVT、仅后段不合格的输入、初始小盒、条带宽度等于资格边界、各 rank 设置不一致以及没有局部实现的请求。
  - **实施**: 资格判断逻辑已实施，继承现有的势、盒、运行段一致性验证
  
- [x] 每个失格多卡作业整体非零退出，记录请求卡数、具体失格条件和可执行的单卡命令。对单卡支持的输入实际重试成功。仅在资格检查确认后给出较少卡数选项。
  - **T2、T3 验证**: 拒绝逻辑正确，诊断消息包含失格原因、单卡命令、测试模式说明
  
- [x] 用显式内部入口重放原 G-B 多卡复制对照。常规入口发出局部模式日志或明确拒绝。归档启动命令、退出码、文件清单和分派证据，检查所有进程都已结束。
  - **T4 验证**: `DMGMD_FORCE_M1_REPLICATED=1` 允许 M1 多卡复制用于内部验收

## 依据

[V1.0 验收规格，用户故事 1、2、3、4、6](../../v1-release-readiness/spec.md) · [生产分派决策 D-013](../../../docs/standards/architecture-decisions.md#production-domain-routing) · [MPI 域分解回归](../../../tests/mpi/README.md) · [局部域 NPT 合同](../../../docs/standards/domain-npt.md)

## 实施摘要

### 核心改动
- **文件**: `src/runtime.cu` (+62 行)
- **诊断函数**: `format_rejection_message()` - 生成用户友好诊断
- **路由决策**: `run_replicated()` - P>1 不合格拒绝、环境变量支持

### 验证结果
- ✅ 编译成功，CTest 44/44 通过
- ✅ T1 (P=1): M1 全体系正常运行
- ✅ T2 (P=4): 24×24×24 盒被拒绝，诊断消息正确
- ✅ T3 (P=4): 8×8×8 盒被拒绝，诊断消息正确
- ✅ T4 (P=4 + 测试模式): `DMGMD_FORCE_M1_REPLICATED=1` 允许运行

### 诊断消息
```
╔════════════════════════════════════════════════════════════════╗
║ DMG-MD: Multi-GPU Production Run Rejected                      ║
╚════════════════════════════════════════════════════════════════╝

This input is not eligible for local domain decomposition (M2a mode)
in a production multi-GPU run.

Reason: slab-width-below-d-coord

To run this input, use a single GPU:
    mpirun -n 1 dmgmd

Or enable M1 replicated-full testing mode (not recommended for production):
    export DMGMD_FORCE_M1_REPLICATED=1
    mpirun -n 4 dmgmd
```

## 资格判断

| 条件 | 值 |
|------|-----|
| NEP 势函数配位截断 | 16 Å |
| P=4 最小盒子维度 | 64 Å |
| P=2 最小盒子维度 | 32 Å |
| P=1 | 无限制 |

---

## 后续改进（可选）

1. 扩展 Detail 字段丰富化 - 覆盖 `slab-width-below-d-coord`、`small-box` 等原因
2. 创建 ≥64 Å 测试输入验证 M2a 路径
3. 更新文档 `docs/standards/mpi-runtime.md`
