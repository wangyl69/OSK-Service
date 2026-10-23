# OSK 到 Haptic Service 延迟与故障说明

更新：2026-10-23；下表实测于 2026-10-22。本文对应当前 C# HybridSED 与独立 C++ `HybridHaptic`。这轮没有 MCU，也没有触摸屏硬件；凡是 mock 数据都只代表主机软件路径。

新增的 SED 内部 hook/入队/写入逐事件计时、默认关闭方式及后续 8,400 次无绑核结果见 [SED 分段计时报告](SED_STAGE_TESTING.md)。后续记录到了入队成功返回后约 11.52ms 才取出的具体样本，尚未将它细分为等待原语或 Windows ReadyThread 调度。

之后已对照并默认采用 Event 唤醒，最新57项C#测试与三轮A/B结果见 [发送线程唤醒报告](SENDER_WAKE_TESTING.md)。本文旧表与历史样本保留原始数值。

## 本轮实测

连续运行三轮，每轮 1,400 次。OSK 应用启动并确认初始化；正向事件调用 OSK 附带的 `LrSendInput64.dll`，经过真实 Windows 键盘钩子、HybridSED 分类/Named Pipe 和 C++ Service。只在最后用 `--mock` 代替 HID 设备。测试没有设置 CPU affinity，也没有人为限制可用 CPU。

| 轮次 | 事件数 | DLL 注入调用到 mock PLAY 完成 p50 / p95 / p99 / 最大 | Pipe 收到到 mock PLAY 完成 p50 / p95 / p99 / 最大 | Service 丢弃 |
|---|---:|---:|---:|---:|
| user-stage-measure-1-20261022 | 1,400 | 0.288 / 0.710 / 1.084 / 3.720 ms | 0.029 / 0.149 / 0.535 / 1.900 ms | 0 |
| user-stage-measure-2-20261022 | 1,400 | 0.205 / 0.446 / 1.073 / 1.642 ms | 0.025 / 0.152 / 0.669 / 1.131 ms | 0 |
| user-stage-measure-3-20261022 | 1,400 | 0.212 / 0.547 / 1.416 / 2.292 ms | 0.024 / 0.174 / 0.716 / 1.440 ms | 0 |
| 三轮合并 | 4,200 | 0.234 / 0.628 / 1.148 / 3.720 ms | 0.026 / 0.158 / 0.658 / 1.900 ms | 0 |
| 普通优先级对照，单轮 | 1,400 | 0.216 / 0.781 / 2.051 / 4.259 ms | 0.019 / 0.135 / 0.498 / 1.773 ms | 0 |

三轮 HybridSED 汇总均未出现队列满、事件过期、管道连接失败或写入失败。C++ Service 保持 WARM，报告 dropped、stale、overflow、unavailable、play_write_failed 均为 0。实测记录位于 `logs/real-pipeline-user-stage-measure-1-20261022`、`logs/real-pipeline-user-stage-measure-2-20261022` 和 `logs/real-pipeline-user-stage-measure-3-20261022`。

这轮没有复现此前的 34–42 ms 长尾，因此不能据此认定长尾已消除。三轮只描述本次 Windows 负载、OSK DLL 注入路径和 mock Service 的结果。最大值仍受 Windows 调度、驱动、其他进程与电源状态影响。

另做一轮普通线程优先级、无 CPU 绑核的对照，1,400 次也全部通过，最大端到端 4.259 ms。它与 AboveNormal 的三轮差异不足以单独证明哪种优先级更快；先保留当前默认设置，不再靠绑核或提升到 Realtime 追逐少量样本的极值。

## 事件来源与 mock 的边界

`mock` 是 C++ Service 的显式 `--mock` 选项：它跳过 HID 设备枚举、`WriteFile` 与 HID 读回，模拟设备状态和 PLAY 接收。它不会在找不到 MCU 时自动启用。去掉 `--mock` 而设备不存在时，状态会是 `NO_DEVICE`，Service 不会伪造振动成功；HybridSED 自己找不到 Named Pipe consumer 时则会记连接失败并丢弃该次触觉事件。

这次端到端测试里，OSK DLL、Windows hook、SED、Named Pipe、C++ worker 都是真实执行的。只有最后的 HID 设备部分是 mock。因此本轮端到端数据不包含 MCU、I²C、马达启动或机械振动时间。

`LrSendInput64.dll` 提供分开的 KeyDown 和 KeyUp 注入入口；测试也是分别调用两者。KeyDown 表示注入了按下边沿，不证明它来自屏幕触摸，也不证明 OSK 已把动作判定为文本输入。当前下游策略是“带 OSK marker 的 KeyDown 才进入触觉路由”；OSK 自己是否把 touch 解释为 typing，属于上游决定。普通未标记键仍放行给系统，KeyUp 不触发 PLAY。

准确标记名是 `SED_OSK`，整数为 `0x5345445F4F534B`，写在 KEYBDINPUT.dwExtraInfo 并由 hook 比较；不是字符串 `OSK_SED`。每轮1400次来自4个基础场景各100次加10批持续输入各100次，另1次预热不计入。性能测试直接从测试进程调用 DLL 的 KeyDown(0x86)/KeyUp(0x86)，即F23；它生成实际进入Windows的注入事件，不会经过OSK界面的触摸识别，也不需要鼠标点屏幕。

管道字节的取值与输入来源是两个维度：

| 取值 | 类别 | 当前状态 |
|---:|---|---|
| 1 | NormalKey | 普通键 |
| 2 | ModifierKey | Shift/Ctrl/Alt等修饰键 |
| 3 | SpecialKey | Space/Enter/Backspace及配置的语音键 |
| 4 | TouchPad | 预留，无生产者；当前C++ Service不接收 |

目前Service对1/2/3均使用同一全局波形、强度发送PLAY，尚未按类别配置不同反馈。3不代表TouchPad；仅凭这一个类别字节也无法恢复原始touch/typing判断、具体键值或OSK触摸区域。

Computer-use 技能在有相应桌面控制工具时可以截图、移动鼠标、点击和输入。点击 OSK 上的键只能验证鼠标/UI 点击路径，不能代替真实触摸面板对 touch 的识别。本会话没有可调用的 `node_repl`/桌面控制运行时，所以本轮只启动了 OSK 并确认初始化，没有实际点击屏幕。

## F12、F24 与 WMI

F12 和 F24 都是 Windows 虚拟键值。HybridSED 的代码属性默认值是 F12（123），保留开发流程兼容；当前 `appsettings.json` 设置的是 F24（135），用于 OSK 的语音专用键，避免真实键盘 F12 被意外解释为语音控制。WMI 本身不要求 F12 或 F24；HybridSED 只把配置的 voice key 转成语音 WMI 事件。按下发布开始状态，松开发停止状态。F24 是当前配置选择，不表示物理键盘必须有 F24 键。

OSK中的语音功能按钮可发送虚拟F24，界面不必显示一颗写着F24的键；F24也不是OSK专属，其他软件或可编程键盘同样可以发送。当前语音映射为Down→Mode=0开始、Up→Mode=1停止。

## interrupt 模式与 PLAY 延迟

这里的 `interrupt` 是 HID 输入报告反馈方式：主机从设备的 HID Input report 读取控制命令 ACK/状态。它不是提高 CPU 中断优先级，也不是触摸中断。`auto` 先用 Feature report；设备不支持时才尝试 interrupt；write-only 是显式降级且没有确认回读。

interrupt 主要影响打开 session、设置参数、读状态和维护验证。普通 PLAY 不逐键等待 MCU ACK，因此不会平白增加每个按键一次往返。若某条控制/读操作已在唯一 HID worker 上执行，后续 PLAY 仍要等它结束；实际设备和驱动上这段时间需要接 MCU 后测。

## Service 延迟应如何读

当前 mock 的 Service trace 起点是管道读取、字节解析之后的 `Worker::submit()` 入口，终点是 Service worker 完成 mock `device.trigger()`。这轮 4,200 个样本的 p50 为 26 μs、p99 为 658 μs、最大 1.900 ms。`queue_time` 对应 submit 到 worker 开始，`hid_write_time` 对应 worker 执行 trigger，`host_time` 对应 submit 到 trigger 完成，包含排队。mock 的 `send()` 立即返回，**不经过 HID `WriteFile`，所以不是 I²C 命令延迟**。

`tests/analyze_latency.py` 可读取已保存的逐事件样本，将同一个事件的总时长减去 Service 时长，得到 DLL 入口到 Service submit 的上游时长。它按线程优先级分别合并数据，输出每段分位数和最长的十个事件；各段的 p99 不能直接相加。上游段仍包含 Windows hook、SED 排队与管道读取，不能单凭此结果区分它们。调用示例：

```powershell
python tests/analyze_latency.py logs/real-pipeline-user-stage-measure-1-20261022 logs/real-pipeline-user-stage-measure-2-20261022 logs/real-pipeline-user-stage-measure-3-20261022 logs/real-pipeline-user-normal-unpinned-20261022 --output logs/latency-stage-summary-20261023.json
```

该分析的 AboveNormal 上游段 p50 / p99 / 最大为 0.195 / 0.773 / 2.068 ms；Service 段为 0.026 / 0.658 / 1.900 ms。普通优先级的上游段为 0.191 / 1.931 / 3.918 ms；Service 段为 0.019 / 0.498 / 1.773 ms。AboveNormal 最慢的同一个事件是 3.720=2.068+1.652 ms；普通优先级最慢的同一个事件是 4.259=3.022+1.237 ms。这些样本无法解释历史 42 ms 的具体来源。新生成的 profile 用 `service_queue`、`service_trigger`、`service_host_total` 明确区分三个统计；旧日志里的 `service_processing` 实际引用的是包含排队的 host_time。

接上 MCU 后，C++ `Device::send()` 会把 32 字节输出报告交给 Windows HID `WriteFile`。当前代码的 HID I/O 单次超时为 250 ms；这只是失败上限，绝不代表正常延迟会接近 250 ms。Windows 文档说明用户态通过 `WriteFile` 发送 HID output report；HID-over-I²C 文档也说明同步输入/输出报告会映射为 I²C Get/Set Report 操作。最终应分开记录 Named Pipe receipt、HID `WriteFile` 进入/完成、MCU 接收/排队、I²C 芯片事务、马达开始响应。

当前固件 `APP_SOC_HID_I2C_RATE` 选为 400 kHz。只按 32 字节、每字节 9 个时钟估算，线上数据本身约 0.72 ms；这个估算还没算 I²C 地址/寄存器与 HID framing、Windows/控制器调度、固件解析以及马达启动。它只是传输下界量级，不是系统实测。应在 MCU 上用固件时间戳或逻辑分析仪验证。[Microsoft：发送 HID 报告](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/sending-hid-reports)、[Microsoft：HID-over-I²C](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/plug-and-play-support-and-power-management)

## 队列、过期与 35 ms 写等待

HybridSED 发送队列容量为 8，发送专用线程取出事件后才连接/写管道。队列等待超过 50 ms 会丢弃该事件，避免很晚才振动。只有发送速度持续低于事件到达速度、发送线程被长期抢占、连接/写管道等待或 Service 不及时读取时，才会逐渐积压、队列满或超时；不是因为普通打字本身快就必然发生。

Named Pipe 写超时/访问拒绝/断管会让当前事件失败并丢弃，连接会关闭，后续新事件再尝试连接，不会重放旧按键。Service 也有自己的有界队列；例如 HID worker 被设备 I/O 卡住、设备离线或维护读取长时间不返回时，队列可能溢出或事件超过 Service 的 50 ms 新鲜度门限。

“Service 收到的数量少于注入量”不等于 Windows 丢了按键。例如保留的旧压力日志 `logs/real-pipeline-priority-normal-load-2/dispatcher.txt` 显示 Published=101、Sent=90、Expired=11，包含一次预热；对应 Service dropped=0。这个缺口明确是 SED 发送队列中过期丢弃的 11 次，未进入 Service。它来自之前限制 CPU 的压力测试，本轮不再执行该方式。对早期没有完整计数的个别缺口，现有证据仍不足以精确归因。

此前约 35 ms 是原来整段托管写入等待的统计值，包含内核 I/O、完成回调与线程调度，现有数据不能把其中每一项精确拆开。现在专用发送线程直接等待 Windows OVERLAPPED 完成事件，减少了对托管 continuation/线程池调度的依赖。它仍不能阻止 Windows 把线程暂停或延迟唤醒，所以不能保证没有长尾。早先三轮无绑核复测的 SED 最大队列等待约 2.4 ms、最大管道写约 1.3 ms；汇总日志每 5 秒输出一次，这些最大值取自最后已记录快照，不能视为完整逐事件追踪。

## 3/6/12/24/30 秒退避与 FAULT

75 秒是模拟设备持续报告 Feature 不可用时，前五次重连尝试之间的等待总和：3+6+12+24+30 秒。第六次连续非设备缺失故障才锁定 FAULT。它不是每个按键的延迟，也不是一个线程占 CPU 睡 75 秒；HID worker 使用条件变量等待并释放锁/CPU，其他进程和线程照常调度。测试 `check_fault_latch.py` 用 `--mock-feature-unavailable` 让失败可重复，等待六次尝试后检查 FAULT，再验证 session close/open 不清除计数，显式切到 interrupt 后恢复。

FAULT 是 Service 对连续协议/状态/传输错误的保护锁定，避免错误配置或坏设备导致永无止境的重开重试。`NO_DEVICE` 单独处理，不会被当作第六次硬故障锁死。session close/open 只管暂停/打开 MCU session；它不等于修复故障。如果它也清除 FAULT，外部反复开关 session 就能绕过退避上限并让坏通道持续重试。应使用可审计的 `recover`，或修正模式/配置后再恢复。Service 的 FAULT 是 Host 状态，不等于固件已经报告 MCU 的硬件 Fault 位。

## 下一步有 MCU 时的测法

主机侧同时记录 Named Pipe 收到时间、worker 开始、HID `WriteFile` 开始与完成；MCU 在收到 PLAY 报告及提交给 haptic 队列时记录单调计时器；用逻辑分析仪抓 I²C SCL/SDA，并用 GPIO/示波器标记马达 enable 或驱动器启动。这样可把 Windows 调度、驱动传输、固件排队和马达响应分开。当前无 MCU 时，继续重复 mock 只能估计主机链路，不会给出真实 I²C 延迟。
