# 长程回归怎样衔接 GPUMD 5.9.1 短程基线

Status: open
Type: grilling
Label: wayfinder:grilling
Parent: [DMG-MD V1.0 发布前的开发与验收](../map.md)

## Question

短程 manifest 已锁定 GPUMD `11ba6fb`，长程仍锁定 `9d23496e` 及旧二进制。哪些参考侧对照、输入与产物差异审阅、版本和哈希记录，足以批准长程基线迁移并重开 smoke？明确哪些历史结果仍归原版本，以及新候选从 smoke 到 nightly、release 的验收顺序。保留参考身份检查与已有数值容差。

现行门槛见[长程正确性测试](../../../tests/long_nve/README.md)和[短程 manifest](../../../tests/baseline/manifest.json)。
