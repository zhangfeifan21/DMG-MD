# 局部域分解 thermo 消费与执行合同

局部域分解模式的 NVE 只在需要写热力学量或 XYZ 时计算本步温度、能量和应力。NVT 与 NPT 每步都需要反馈，因此每步计算。局部路径由 `src/domain_runtime.cu` 的 `run_domain_segment` 执行。全量复制模式（包括 P1）沿用 `src/runtime.cu` 的 `run_segment`，每步计算。

## 消费者与步号

| 消费者 | 读取位置与内容 | 需要的段内步号（从 1 开始） |
| --- | --- | --- |
| `dump_thermo I` | `write_thermo_row`：温度、动能、势能和 stress | `step % I == 0` |
| `dump_xyz I` | `write_xyz`：无条件写入头部 energy/stress；与所选逐原子 quantities、group 或输出精度无关 | `step % I == 0` |
| NVT Berendsen | VV2 后的温度反馈，随后缩放 owned velocity；输出仍使用原先缩放前 thermo | 每步，包括耦合小到不触发缩放的配置 |
| NVT NHC / BDP | VV2 后每步反馈全局温度；root 更新链后半步或取样，再缩放 owned velocity | 每步；NHC 还在 VV1 前另归约一次 owned KE，不属于按需跳过的 8-double thermo |
| NPT Berendsen / SCR | VV2 后每步计算温度、压力与体积；root 控压更新使用同一步缩放前样本，SCR 随机调用顺序由 BDP 状态推进后接压力 `gasdev` | 每步；不得因弱耦合或输出周期跳过反馈 |
| `dump_restart I` | `write_restart` 只读取 box、identity 和 gathered owned snapshot | 不消费 thermo |
| snapshot gather | gid、position、velocity、force、potential、virial 和可选 unwrapped | 不消费 thermo；XYZ formatter 的需求单独计算 |
| adaptive timestep | owned velocity 最大值及独立全局 MAX | 不消费 thermo |
| `correct_velocity` | gathered mass/velocity/position，CPU 修正后 scatter | 不消费 thermo |
| NEP、cache 判定、重建/迁移 | 位置、连续位移、类型、gid 和 force/NEP 工作区 | 不消费 thermo |
| 段首 force、段末同步/计时、neighbor 输出 | 各自的 force/cache、CUDA readiness 或 occupancy 数据 | 不消费 thermo |
| 多 run/restart | 每段 measurement 列表在 run 后清空；ensemble 保留；restart 重新 bootstrap | 按新段的消费者重新判断；没有隐含的段末 thermo |

上述判断只对 NVE 生效。每个 rank 根据同一份已解析的系综与输出列表独立作出相同决定，空 rank 和根 rank 遵守同一规则。若一个步号同时要写热力学量和 XYZ，只计算一次。尾段不足一个输出周期时不追加输出。即使耦合很弱，NVT 仍按每步反馈。

## 数值、所有权与就绪

需要热力学量时，程序先对负责原子完成速度 Verlet 第二半步（VV2），再执行 `compute_domain_thermo`，最后运行恒温器和输出。计算只累加负责原子的贡献，对 8 个 double 做 SUM 归约，按已有公式归一化并将结果拷回主机（D2H）。原子读写权限、SoA 跨度、浮点表达式和累加顺序沿用现有实现。XYZ 写本步算出的 energy/stress，restart 沿用原快照和写出步号。

每步创建一个空的 `optional<ThermoState>`，只在实际计算时赋值。输出和 NVT 控制器通过 `value()` 取得本步数据。局部 NVE 无需求时，跳过局部求和内核、全局 SUM、归一化内核和结果 D2H。积分、NEP 求力、边界交换、缓存操作、重建迁移和已请求的输出仍照常执行。

删除辅助 D2H 不引入新的生产等待。VV2 后的下一步 GPU 工作仍在原 stream 排序；
cache 检查、通信、snapshot 及原有段末 device synchronization 保留各自就绪职责。
空 owned/local rank 与所有其它 rank 遵循相同 collective 顺序。

## 通信与诊断

普通步总要用一次原因按位或归约确认缓存决议（4P B，P 为 rank 数）。需要热力学量时，再做一次 SUM 归约（64P B）。MPI 逻辑输入和输出各为 `4P + 64P * needs_thermo` B，集合调用次数为 `1 + needs_thermo`。自适应时间步、邻居周期记录、输出、重建和迁移的额外通信另计。`HostStaged` 也只在计算热力学量时增加相应的 D2H/H2D 拷贝。

`DMGMD_DOMAIN_SUMMARY` 的 `thermo_steps`/`thermo_skipped_steps` 是每 rank、每段的
实际计算/跳过次数。它们不新增 collective，不代替独立 PMPI 调用计数验收。

详细计时不能依赖已被跳过的 thermo D2H 使 event 就绪。`DMGMD_DOMAIN_TIMING=1`
为各步保存独立 CUDA event 与 trace/通信记录，在原有段末 device synchronization 后读取，
不新增逐步 barrier、device/event synchronize 或后台线程。此诊断占用 O(段步数) host/event
资源；初始化、段末读取和输出也有成本，不能用其墙钟作为正式性能样本。
未计算 thermo 的步不读取未记录的 thermo event，其 device stage 贡献为零。
trace 的 `thermo_end_ns` 表示需求判断/可选计算的 host 结束边界；跳过时不代表 GPU 已就绪。
详细 trace 在段末补齐设备时间后输出，原步号与 monotonic 时间戳保留。

## 验证入口

`tests/mpi/run_thermo_demand.py` 对原 O7 与 O8 做科学文件逐字节比较，并用独立
`libdmgmd_thermo_demand_probe.so` 观测实际 8-double SUM 和 reason OR。
它覆盖无输出、互素输出周期、XYZ-only、输出周期超过段长、段尾、多 run、restart-only/
跨 rank resume、NVE/NVT 切换、弱耦合、adaptive timestep/修速、重建/迁移、空 rank，
以及 full/serial/wait/poll、详细计时、P1/全量复制回退路径。
`tests/mpi/run_mpi_domain.py` 的逐步通信 oracle 按消费者步号精确记账；golden 和 long-NVE
容差仍由原 manifest 管理。运行方式见 [MPI README](../../tests/mpi/README.md) 与
[O8 benchmark](../../tests/benchmark/README.md#诊断入口)。
