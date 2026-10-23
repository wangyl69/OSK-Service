# SED 发送线程 Event 唤醒对照

2026-10-23。本轮针对入队成功后十几毫秒才被 sender 取出的现象，比较旧 BlockingCollection 阻塞等待与 Windows Event 唤醒。没有 touch/MCU，末端明确为 mock，没有设置 CPU affinity。启动日志同时核对了实际 wake 模式和 AboveNormal 线程优先级。

## 改动与选择

保留容量8的队列、50ms新鲜度门限和单字节1/2/3协议。现在默认 `Dispatcher:HapticSenderNativeWake=true`：producer 入队后 Set 一个 AutoResetEvent；sender 零等待取完队列再 WaitAny 等待新事件或停止信号。没有定时 Sleep 或长期忙等。新事件在 sender 尚未等待时到达，Event 会保留信号；多次信号可以合并，因为 sender 会取完所有队列项。[AutoResetEvent 行为](https://learn.microsoft.com/en-us/dotnet/api/system.threading.autoresetevent?view=net-8.0)

设置 false 可以恢复原先的 GetConsumingEnumerable 阻塞等待。两种方式都保持独立发送线程，未将写管道搬回 hook，也未修改 C++ Service 的硬件协议。

## 对照结果

同样的4个普通计算进程、AboveNormal、SED trace 开启，交错执行 A1、B1、A2、B2、B3、A3。每轮1400个正式按键，另有1个预热；每次按键注入 Down+Up。正向调用 OSK 真实 LrSendInput64.dll 的 F23（0x86），不通过 GUI/touch 判定。测试总数来自14批各100次：4个基础输入场景，加10批持续输入；不是系统容量限制。

| 方式 | 样本数 | 全链路 p50 / p95 / p99 / 最大，ms | publish→dequeue 最大，ms |
|---|---:|---:|---:|
| 原等待，3轮合并 | 4200 | 0.116 / 0.264 / 0.545 / 17.252 | 16.659 |
| Event，3轮合并 | 4200 | 0.116 / 0.272 / 0.483 / 2.057 | 1.204 |

每轮结果均计数匹配，Service dropped=0，无额外 KeyUp PLAY。本组 Event 的中位数没有增加，常见分位数并未全面变快：队列段 p99 从0.106ms变成0.133ms，全链路 p95 从0.264ms变成0.272ms。采用它主要依据是这组观测中大幅缩小的最坏样本；有限样本不能证明旧等待原语有缺陷，也不能保证未来不再发生 Windows 调度长尾。

各轮全链路最大值：A1=0.930ms、A2=6.690ms、A3=17.252ms；B1=1.602ms、B2=2.057ms、B3=1.577ms。Event 路径中，Set 返回到取出事件仍出现过1.194ms，说明换等待方式也没有消除所有调度等待。

然后测试当前默认配置、关闭 SED trace、4个普通计算进程：1400次全部通过，p50=0.113ms、p99=0.354ms、最大1.144ms。Service submit→mock完成 p50=11μs、p99=214μs、最大528μs。这个默认配置对照是另一轮运行，不应当当作与开启诊断的样本严格同条件比较。

再做当前默认配置、无额外计算进程、SED trace关闭的1400次：全部通过，p50=0.137ms、p99=1.438ms、最大3.950ms；Service段p50=15μs、p99=828μs、最大2.391ms。这也说明不能把前一轮的1.144ms当成通用上限，或认定增加负载必定使延迟变大/变小。

构建无警告/错误，57项C#测试与3项C++测试通过；合成输入、实际DLL、Service控制/恢复/日志/状态模式回归通过。新增测试覆盖两种等待方式的共享内存计时、Event突发队列取完和空闲时停止唤醒。

另扩展并重跑真实75秒退避测试：FAULT期间向Service送入1/2/3各一条，观察到play_sent=0、unavailable=3、dropped=3；close/open后保持FAULT。显式修改为可用模式恢复，再送一条新事件，得到play_sent=1、dropped仍为3、queue_depth=0，确认故障期间的旧输入没有恢复后重放。结果保存在 `logs/fault-latch/results.json`。

数据：`logs/sender-wake-comparison-20261023.json`；各轮目录为 `logs/real-pipeline-wake-classic-a[1-3]-20261023`、`logs/real-pipeline-wake-event-b[1-3]-20261023`，以及 `logs/real-pipeline-wake-default-traceoff-20261023`。

## 复测与回退

```powershell
# 原等待方式
python tests/check_real_pipeline.py --with-osk --sed-trace --no-sed-native-wake --load-workers 4 --run-label classic-repeat --quiet

# 当前默认 Event 方式
python tests/check_real_pipeline.py --with-osk --sed-trace --load-workers 4 --run-label event-repeat --quiet

# 当前生产路径对照，SED trace 默认关闭
python tests/check_real_pipeline.py --with-osk --load-workers 4 --run-label default-repeat --quiet
```

SED 本身可在启动参数中加 `--Dispatcher:HapticSenderNativeWake=false` 回退，或修改 appsettings 对应值后重启；启动日志应显示 `Mode=BlockingCollection`。当前默认应显示 `Mode=NativeEvent`。

根据用户要求，历史 `--cpu-load` 绑核模式已经禁用，脚本遇到它会直接报错退出，未创建测试进程。`--load-workers` 和计算进程脚本均不调用设置 CPU affinity 的 API。

## 尚待实机处理

这次修改处理 SED 的队列唤醒路径，没有把真实 HID interrupt ACK 的等待改成异步。当前 Service 维护操作仍可能占用唯一 HID worker：平时优先 PLAY、空闲时维护，距上次确认达到20秒会优先验证。改进方向是单个在途控制请求的异步 ACK 状态机，使 PLAY 不等待读回完成；I²C 总线和驱动中的在途事务仍无法凭空抢占。此项需要真实 MCU 或专门的延迟 ACK 设备模拟验证，不能用立即返回的 mock 状态读取声称通过。
