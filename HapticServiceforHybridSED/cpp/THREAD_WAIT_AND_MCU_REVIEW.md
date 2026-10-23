# 线程等待与 MCU 播放路径复核（2026-10-23）

后续实施结果见 [等待优化与复测](WAIT_OPTIMIZATION_TESTING.md) 和 [MCU 单板验证说明](../../Athena_Haptic/docs/hostlatency_standalone_validation_20261023_zh.md)。下文保留为实施前复核记录。

本次为源代码复核和现有 CTest 回归；没有修改固件播放行为，没有烧录，没有新增硬件延迟测量。

## Host：先区分锁等待与调度等待

`src/runtime.cpp` 的 submit 在获取 mutex_ 之前记录 receivedUs。因此现有 submit→dequeue 指标同时包含：提交端获取锁、入队、通知、工作线程恢复运行及获取锁。不能将整个区间归因于 CPU 调度。

下一轮诊断应采用固定容量内存记录，增加 submit 获取锁后、入队后、通知后、worker wait 返回后、worker 获取队列锁后的时间点。记录线程 ID、事件诊断序号及 QPC，关闭时不增加热路径时间戳，不逐事件写磁盘。现有 mockTrace 只覆盖模拟设备路径；真实 HID 调试需另外提供显式开启的同类诊断，不能把 mock 结果当真实驱动测量。

内核轨迹用于补足应用记录无法看到的 Ready→Running：

| 证据 | 判断 | 对应处理 |
|---|---|---|
| submit 获取锁耗时长 | 生产者竞争共享状态锁 | 缩短临界区，管理状态采用快照，避免锁内日志与大对象复制 |
| worker 已 Ready，迟迟没有 Running | 调度/抢占 | 对照抢占线程、DPC/ISR、优先级和电源状态；仅针对确定来源调整 |
| worker 因同步对象继续 Waiting | 锁持有者或唤醒路径阻塞 | 查调用栈和持有者；验证是否通知遗漏、管理操作占锁 |
| worker Running，但到 trigger 耗时长 | 实际执行或其间再次阻塞 | 对照状态处理、诊断锁及日志路径 |
| HID 写入本身长 | 驱动、总线或设备路径 | 单列真实 WriteFile 提交/完成，配合 MCU 接收计时 |

上述情况可能混合出现。`status()` 当前在 metricsMutex_ 下复制四组统计数组，排序已经在锁外；需测量复制锁竞争，不能仅凭存在互斥锁认定其是长尾来源。

已有 SED 原生事件唤醒和异步状态反馈保留。暂不更换无锁队列、不引入忙等、不绑核、不使用实时优先级。此前 deadline 等待 A/B 没有证明更优，保持默认配置。

管理员 PowerShell 执行现有采集器：

```powershell
Set-Location D:\work\OSK-MCU\HapticServiceforHybridSED\cpp
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Capture-Scheduling.ps1 -RequireKernelTrace
```

在 WPA 以异常事件的 QPC 时间窗口定位 SED sender、Service input 和 HID worker，分析 CPU Usage (Precise)、ReadyThread/CSwitch 以及 DPC/ISR。当前普通进程此前被拒绝内核采集；已有应用层记录不能替代内核证据。权限详情见 KERNEL_CAPTURE_PERMISSIONS.md。采集开/关也应对照，报告采集开销。

## MCU：已具备的优化

* SESSION_OPEN 发起预热，Host 保持会话；正常热播放无需逐键开关会话。
* app_haptic.c 的 RuntimeStartWarmEvent 对相同配置跳过寄存器刷新，直接进入 TX_START。
* app_wave.c 已缓存播放缓冲；配置未变时复用。
* 成功播放在 FIFO 排空后完成热播放，不经过正常冷/失败分支的 POST_DELAY。配置中的 5/15ms 不能逐键直接相加。
* app_haptic_recovery.c 已将每 32 次播放到期的健康检查延后到队列空闲。
* 接收端支持不请求 ACK 的 PLAY 和 heartbeat；不必每键等待反馈。
* 已有微秒级 hid_rx_to_dma 统计，位置在 DMA 启动调用之前；它不是振动起点，也不是 DMA 成功后的硬件时刻。

## MCU：下一步候选，按优先级验证

1. **主循环的工作次序与预算。** app_runtime.c 先 UART，再 EC/HID，再控制台，再播放。APP_I2CHID_Poll 又一次排空整个原始报告队列。需要分别记录这些阶段最大用时；考虑每轮控制台字符/命令预算、原始报告处理数量预算，以及有 PLAY 待处理时优先推进播放。必须保留控制报告的 FIFO 顺序，不能让 PLAY 越过 SESSION_CLOSE 或配置变更。
2. **空闲检查的到达竞争。** 队列空时开始健康检查后，新的 HID 中断可以入队，但主循环仍要完成当前同步 AW 寄存器访问。将检查拆成每轮一个电机/寄存器、每步前检查待处理 PLAY，可减少剩余检查阻塞；已经开始的传输不能被安全地瞬间抢占。先测量正常和错误超时，再决定是否值得改为异步 I2C。保留过压保护和故障恢复。
3. **入睡前事件竞争。** app_runtime.c 的 WFI 前没有与生产者配套的原子“无待处理工作”检查。如果中断在本轮处理完成后、WFI 前已经完成，新事件可能等下一次中断/SysTick 才被主循环处理。这是代码上的候选窗口，尚非实测根因。改造需建立可证明的条件检查/睡眠协议并覆盖边界中断，不能简单在 WFI 前加一次普通判断就认为问题消失。
4. **播放吞吐与队列策略。** 当前播放状态机在上一段 DMA/FIFO 完成后取下一次事件，触发队列深度 4、丢旧及 50ms 期限不能创造播放吞吐。需要测量真实波形时长、最小有效触发间隔与累计排队，再决定保留完整播放、缩短键盘波形或设计受控合并/重启。重启可能截断波形并改变机械效果，不能直接当作默认优化。
5. **诊断与故障路径隔离。** 审核启动输出转储、控制台和恢复日志是否占用主循环；正常播放只做内存计数。状态读取继续低频，在安全静默阶段访问 AW。不要为缩短数字移除电源稳定延时、FIFO 排空或保护检查。

## 验证边界

无 MCU 时可验证报文契约、Host 排队/唤醒、异步反馈、断线与恢复策略；无法验证总线完成、MCU 调度、DMA 首样本和机械起振。

接硬件后按同一事件序号记录：MCU 原始报告入队→校验/派发→播放队列取出→DMA 启动前；另外用 GPIO/逻辑分析仪观测 I2C 报文结束及实际 SAI/DMA 时刻。序号不代表 Host 与 MCU 时钟同步；跨时钟绝对延迟需外部同步。报告冷/热首键、持续输入、突发、健康检查恰好到期、总线错误、Host 异常退出分别统计 P50/P95/P99/最大值及丢弃原因。

本次执行现有 CTest：core、windows_io、panel_raw_input、firmware_contract，4/4 通过。此结果不是 MCU 运行验证；本次没有重新生成性能样本。
