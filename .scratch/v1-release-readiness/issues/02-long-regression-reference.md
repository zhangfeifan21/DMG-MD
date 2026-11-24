# 长程回归怎样衔接 GPUMD 5.9.1 短程基线

Status: resolved
Type: grilling
Label: wayfinder:grilling
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)

## Question

短程 manifest 已锁定 GPUMD `11ba6fb`，长程仍锁定 `9d23496e` 及旧二进制。哪些参考侧对照、输入与产物差异审阅、版本和哈希记录，足以批准长程基线迁移并重开 smoke？明确哪些历史结果仍归原版本，以及新候选从 smoke 到 nightly、release 的验收顺序。保留参考身份检查与已有数值容差。

现行门槛见[长程正确性测试](../../../tests/long_nve/README.md)和[短程 manifest](../../../tests/baseline/manifest.json)。

## Answer

2026-11-24 与维护者逐项确认（grilling）。依据：服务器 HEAD `f12852c`；`scratch/gpumd-comparison/SUMMARY.txt`（2026-11-20，5.9.1 与 `9d23496e` 的短程 golden 除 `restart.xyz` 精度外逐字节相同）；`gpumd-reference` 与 `/opt/envdeploy` 的 GPUMD 均为 `11ba6fb`、SHA-256 `6ab97ed2…`。`9d23496e` 的二进制（`3ab365cc…`）在 L20 上已不存在。

1. **数值参考锁定身份，性能计时只记录版本。** 长程 manifest 改用与短程相同的 GPUMD 5.9.1 `11ba6fb` 及二进制哈希，`validate_manifest()` 的长短一致检查保留。旧 commit `9d23496e` 不再锁定。性能计时沿用已改文档：记录实际版本、后端和卡数，不锁定。
2. **迁移证据。** 2026-11-20 的短程逐字节对比作为参考侧证据，不再补跑旧二进制。长程输入由生成器确定性产生，90 个模型的 SHA-256 与参考版本无关，不需重审。唯一要审阅的差异是 restart 段改读 17 位 `restart.xyz`：重启边界的比较方式和容差保持不变。
3. **校准证据。** 在 L20 上用 `11ba6fb` 重跑 30 次短程校准，生成新的 `calibration.json`。它是参考可重复性的证据，不产生容差。观测值须远小于现有容差，否则停下来报告。manifest 的容差数值不变。
4. **验收顺序。** 新校准 → 长程 smoke（单卡，HostStaged）→ nightly（独占 4 卡，1/2/4 rank，两后端）→ release。前一级通过才进入下一级。L20 的 release 只到 P4，并如实报告覆盖范围；8 rank 归[八卡发布环境](05-eight-gpu-release-site.md)。失败时先区分是候选还是参考的问题，不放宽门槛。
5. **旧结果归档。** 新校准完成、smoke 和 nightly 通过后，把 `9d23496e` 时期的结果（旧 `calibration.json`、`scratch/goldens-9d23496e-backup/` 等）移入仓库内的 `archive/gpumd-9d23496e/`。`.gitignore` 加 `/archive/`，用 `git rm --cached` 停止跟踪，文件只留在服务器上。在此之前，旧锁和旧数据都不动。
6. **文档只写当前参考。** 删去 `docs/status/validation/baseline-results.md:6`、`tests/baseline/README.md:32` 这类旧版本结果的叙述，不在同一张表里并列多版本结果。`tests/baseline/README.md` 留一句归档位置。改写 `spec.md:9,64`、`docs/status/current.md`、`docs/plans/dmg-md-v1.0-development-workflow.md:36`、`baseline-results.md` 长程入口一节和 `tests/long_nve/README.md:5` 中"长程受版本锁阻断"的表述。
7. **不改的 `9d23496e`。** `src/gpumd_compat/` 文件头、`AGENTS.md:47`、`kernel-inventory.md`、`compatibility-matrix.md`、`ensembles.md` 记录的是兼容源码的上游出处；`tests/thermostats/` fixtures 与 `tests/npt/freeze_b*.py` 是独立冻结的测试；`tests/baseline/README.md:36` 记录势文件来源。这些都保留。
8. **以后升级 GPUMD。** 先重新生成短程 golden，并与当前参考逐字节对比；再在同一提交里同时更新长短两份 manifest，重跑校准，依次通过 smoke 和 nightly。此流程写进 `tests/long_nve/README.md`。

实施（manifest、文档、校准和各级回归）不在本 map 内执行，按仓库开发计划另行安排。
