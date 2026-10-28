# 管理员调度采集复核（2026-10-26）

本次查找的最新采集目录是 `logs/real-pipeline-schedule-20261026-110241-e3c4e113/`。capture.json 记录本地 11:02:41 开始、11:03:19 完成，管理员运行，四个计算进程，standard 场景，未启用 500Hz 扩展。当前没有找到晚于该目录的 schedule 采集结果。

## 成功项

* WPR 启动、测试、WPR 停止退出码全部为 0；原始 scheduler.etl 大小 277,872,640 字节。
* 测量 1,400 次，另有一次预热。最终 ready=true、dropped=0、generation=1、play_sent=1401，没有多余松键 PLAY。
* dispatcher 日志的 QueueFull、Expired、WriteFailed、TimedOut、Dropped 计数均为 0。
* 本轮新增执行 `tracerpt scheduler.etl -o NUL -summary etl-summary.txt -y`，成功读取 2,591,108 个事件、265 个缓冲区，报告 Events Lost=0。保留原 ETL，仅增加派生摘要。
* 摘要中有 305,962 条 ReadyThread、26,486 条 DPC、1,294 条 TimerDPC，证明实际记录了相关内核事件，并非仅创建空文件。

tracerpt 同时警告部分事件不匹配本机 schema；其中 Thread opcode 36 version 5 仅显示数字。Events Lost=0 是采集丢失计数，不代表所有事件已完整解码。因此本轮只完成录制完整性和应用阶段复核，未声称已解析全部 CSwitch/ReadyThread 调用栈或定位内核根因。

## 分段结果

单位 ms，基于同一事件的 QPC 时间戳。不能相加各列分位数/最大值。

| 阶段 | P50 | P95 | P99 | 最大 | >5ms |
|---|---:|---:|---:|---:|---:|
| DLL→Service 接收/解析后 submit | 0.126 | 0.248 | 0.382 | 0.778 | 0 |
| SED 发布→发送线程取出 | 0.021 | 0.042 | 0.132 | 0.219 | 0 |
| Pipe：WriteFile 入口→Service submit | 0.025 | 0.058 | 0.104 | 0.565 | 0 |
| Service submit→mock 完成 | 0.019 | 0.057 | 0.169 | 0.484 | 0 |
| Pipe＋Service | 0.046 | 0.115 | 0.227 | 0.587 | 0 |
| DLL→mock 完成 | 0.151 | 0.294 | 0.453 | 0.800 | 0 |

这些数字不包含真实 HID/I2C、MCU 和电机；本轮带内核采集，也不应与没有采集的轮次直接合并估计尾部概率。

## 下一步

当前轨迹没有复现此前 7–29ms 的慢事件，不能据此证明长尾已经修好。11,220 次扩展验证中，正常负载的 cadence 节奏更容易复现；应优先改变复现场景，同时保留同样的调度采集，而不是继续只增加四进程压力或盲目修改生产逻辑。

在管理员 PowerShell、保持桌面解锁时运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:/work/OSK-MCU/HapticServiceforHybridSED/cpp/tools/Capture-Scheduling.ps1" -RequireKernelTrace -ScenarioSet cadence -LoadWorkers 0 -IdleWakeCount 0
```

每次自动生成独立目录，不覆盖本次轨迹。此场景为 470 次：短串 200Hz 后暂停 200ms、100Hz 有/无诊断并发、250ms/1 秒短空闲首键；没有 500Hz。该采集命令尚未在管理员环境中由本轮分析执行。

捕获到 >5ms 的事件后，用 results.json 的 QPC/线程 ID 对齐同一 ETL，分别判断：SED sender 是否 Ready 却未运行；Pipe receiver 是否迟迟没有恢复；Service worker 是否在同步等待/内部重新取锁；对应时间窗有无 DPC/ISR 抢占。再针对实际阻塞点做最小修改和相同输入的前后对照。若该轮未复现，保留并重复相同场景，不能拿另一轮慢事件的时间戳与本轮 ETL 拼接。

原始证据：目录内 capture.json、results.json、stage-summary.json、dispatcher.txt、scheduler.etl、etl-summary.txt。此前扩展结果见 [DLL_CHAIN_VALIDATION_20261026.md](DLL_CHAIN_VALIDATION_20261026.md)。本轮未改生产代码、未提交推送、未烧录。
