# 新版连续会话与同轮长尾定位

本轮只使用当前新 SED＋新 C++ Service，正常负载（未额外启动CPU压力进程）连续六遍 cadence，共2,820次真实DLL按下/松开测试，持续约5分钟。零丢弃、零额外/松开PLAY，最终generation=1、play_sent=2,821（含预热）。过程中没有重复Session Open/Close，不绑核、不改生产算法或优先级。该“正常负载”仍包含桌面后台活动与采集开销，不代表系统完全空闲。

## 结果

| 阶段 | P99 ms | 最大 ms | >5ms次数 |
|---|---:|---:|---:|
| DLL→Service提交 | 1.175 | 3.045 | 0 |
| SED队列 | 0.816 | 2.822 | 0 |
| Pipe写入入口→Service提交 | 0.127 | 0.852 | 0 |
| Service提交→取出 | 1.232 | 28.421 | 3 |
| Service提交→mock完成 | 1.234 | 28.422 | 3 |
| Pipe＋Service | 1.344 | 28.465 | 3 |
| DLL→mock完成 | 2.222 | 28.849 | 3 |

新增慢事件3/2,820，为该批观察比例，不是长期概率。三个事件来自两个1秒首键与一个250ms首键，均有同轮ETL，未拿旧轨迹解释新事件。未测真实HID/I²C/MCU/振动。

## 同轮调度证据

| 场景 | Pipe＋Service ms | Notify→Ready µs | Ready→首次实际运行 ms | 工作线程取锁 µs |
|---|---:|---:|---:|---:|
| 第2遍1秒首键 | 6.221 | 2.2 | 6.1625 | 1 |
| 第2遍另一个1秒首键 | 6.336 | 2.2 | 6.2715 | 1 |
| 第4遍250ms首键 | 28.465 | Ready早于本键通知 | 72.3652 | 2 |

前两条通知入口到返回分别11、13µs；线程约2.2µs后Ready，主要延迟出现在Ready后等待执行。

28.465ms事件的时间线（QPC微秒）：

* 等待入口469169196450，超时参数10ms。
* 工作线程切出为Wait：469169196452.4。
* ReadyThread：469169211608.6。
* 本键入队：469169255581；通知入口相同、返回469169255584（3µs）。
* CSwitch切入工作线程：469169283973.8。
* 等待函数返回：469169283995，返回原因为超时；获取队列锁469169283998，出队469169283999。

线程在本键到达之前约43.972ms就已Ready，从Ready到实际运行总计约72.365ms；按键只承担其中后半段等待，不能把72ms当成本键端到端延迟。没有Ready发生在本键通知之后并不代表通知丢失：线程此前已Ready。分析窗口已扩展到最近一次等待入口，避免只看通知后的窗口漏掉这次状态转换。

这些事件支持“工作线程等待CPU执行是主要耗时”的结论，不支持“Pipe传一个字节花了28ms”或“队列锁耗时28ms”。固定10ms等待超时不能保证线程10ms内实际恢复执行。

## 竞争进程上下文与尚未证明的部分

慢事件附近存在Code.exe、NVIDIA Overlay.exe、System、桌面及测试进程活动；线程切入前在该CPU运行的线程也分别映射到Code.exe或NVIDIA Overlay.exe。工具保存事件时刻已知的线程→进程映射，未知映射保留空值，不用录制结束时映射倒推。

由于线程不绑核，附近有某进程活动、或者它恰好在切入前被切出，都不足以证明该进程独占/阻塞了工作线程整个等待区间。尚未完成动态优先级、全部CPU可用性、DPC/ISR占用与符号化栈的联合归因；没有擅自关闭后台进程、修改电源策略或切换实时优先级。

本轮目标是捕获新版慢事件的同轮证据，已经达到；暂不以持续追加相同压测代替原因分析。后续针对动态优先级、CPU可用性和中断分析，或者在受控后台活动下做配对验证，比继续优化微秒级队列锁更有意义。当前不作“长尾已消除”的结论。

## 复现和文件

实际命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File HapticServiceforHybridSED/cpp/tools/Capture-Scheduling.ps1 -Elevate -RequireKernelTrace -ScenarioSet cadence -CadenceCycles 6 -LoadWorkers 0 -IdleWakeCount 0
```

同轮目录：`logs/real-pipeline-schedule-20261026-145114-4772c632/`。管理员启动、pipeline、WPR停止退出码均0；TraceEvent解码4,801,448个CSwitch、2,467,203个ReadyThread，ETL报告丢失事件0。

离线命令：

```powershell
dotnet run --project HapticServiceforHybridSED/cpp/tools/EtlSchedule -- HapticServiceforHybridSED/cpp/logs/real-pipeline-schedule-20261026-145114-4772c632
py -3 HapticServiceforHybridSED/cpp/tests/correlate_kernel_waits.py HapticServiceforHybridSED/cpp/logs/real-pipeline-schedule-20261026-145114-4772c632
```

`kernel-thread-events.json`保存工作线程/输入线程状态，以及慢事件窗口内所有CPU切换；`kernel-correlated-events.json`保存对齐结果和附近进程活动计数（这些计数不是阻塞时长或因果证明）。窗口包含最慢20个事件与全部>5ms事件。ETL及JSON为本地logs文件，未自动上传。

[分段CSV](NEW_ONLY_STAGES_20261026.csv)单位µs。解析工具构建通过，保留原始QPC换算用途对应的编译器提示；Python语法与时间线校验通过。本轮没有修改MCU或生产等待策略，也未提交/推送。
