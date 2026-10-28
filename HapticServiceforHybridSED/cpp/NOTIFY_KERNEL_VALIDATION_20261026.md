# 通知/等待时间记录与同轮内核采集

已补齐 opt-in Service 时间记录，并完成正常负载同一会话三遍 cadence 共 1,410 次实际 DLL 软件链路验证。最终 play_sent=1,411（含一次预热）、dropped=0，WPR 启动/测试/停止退出码均为 0。无 >5ms 事件，不代表之前 36ms 长尾已解决。

## 实现

* 每次成功入队，记录调用 SetEvent 前后时间，使用 submit 的 QPC 微秒时间戳关联。单独固定容量环保存，避免工作线程先取出事件时读取仍在修改的队列元素。
* 工作线程保存最近一次等待的入口、返回、实际超时参数以及返回原因（通知/超时），随 PLAY 的取锁、取出和完成时间一起导出。
* 原有字段顺序保留，新字段追加；Python 兼容旧版无这些字段的结果，新增字段存在时验证时间顺序和通知关联。
* 诊断默认关闭，仅 `--latency-trace` 或 mock trace 开启。开启时有额外 QPC、诊断环短锁开销，不能称为完全零开销。没有在关闭诊断的路径增加文件日志。
* `analyze_wait_timeline.py` 输出前十个最慢事件及所有 >5ms 事件，保留输入/工作线程 ID 与全部 QPC 时间供同轮 ETL 对齐。

通知可合并，不保证每次 SetEvent 对应一个等待返回；最近一次等待可能早于本键入队。工作线程也可能在 SetEvent 返回之前开始处理。分析不把这些正常并发顺序当错误。通知写入诊断环在 SetEvent 之后，不把诊断环写入时间误当成通知实际完成时间。

## 权限处理

`Capture-Scheduling.ps1` 新增 `-Elevate`，通过标准 Windows UAC 为此次采集启动管理员 PowerShell；父进程等待并返回子进程退出码，子进程使用独立 WPR 实例并在 finally 中停止/收尾。

这次已经实际成功：is_admin=true、trace_started=true。无需关闭 UAC、修改长期用户权限或让生产 Service 提升为实时优先级。后续普通 PowerShell 直接运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:/work/OSK-MCU/HapticServiceforHybridSED/cpp/tools/Capture-Scheduling.ps1" -Elevate -RequireKernelTrace -ScenarioSet cadence -CadenceCycles 3 -LoadWorkers 0 -IdleWakeCount 0
```

出现系统 UAC 时需用户确认；若策略拒绝或取消，脚本报错，不能绕过。若已在管理员终端运行，直接执行采集，不重复提升。采集时保持桌面解锁；不要同时运行其他按键注入压测。

## 本轮数据

本地目录：`logs/real-pipeline-schedule-20261026-142450-e938924e/`。`scheduler.etl` 884,998,144 字节，包含同一轮 CSwitch、ReadyThread、DPC、Interrupt 及配置中的线程栈。`capture.json` 保存 QPC 频率和录制起止 QPC；应用时间单位为 QPC 微秒。对齐时需按频率换算，不能直接把应用微秒当原始 QPC tick。

| 区间 | P99 ms | 最大 ms |
|---|---:|---:|
| Pipe 写入→Service 提交 | 0.128 | 1.151 |
| Service 提交→mock 完成 | 1.795 | 3.741 |
| Pipe＋Service | 1.830 | 3.777 |
| DLL→mock 完成 | 2.754 | 4.130 |
| 入队记录→通知入口 | 0.001 | 0.001 |
| 通知调用 | 0.021 | 0.180 |

有内核录制和增加诊断的本轮开销与此前不同，不直接混池计算 P99，也不据此判断生产性能回归。本轮没有捕获 >5ms 事件；尚未完成对每个线程 Ready/CSwitch 栈的逐事件归因，不能宣布已从 ETL 找到此前长尾根因。

结果文件：`results.json`（全部事件）、`wait-timeline.json`（通知/等待时间线及最慢十条）、`stage-summary.json`、`capture.json`、`scheduler.etl`。原始 logs 为 Git 忽略目录，未自动上传。

`tracerpt` 摘要转换成功：处理 844 个缓冲、9,182,504 个事件，记录的事件丢失数为 0；ReadyThread 1,200,905 个、DPC 79,613 个。Thread opcode 36/version 5 在本机摘要中仍以编号显示，未据此伪称完成 CSwitch 栈解析。`etl-summary.txt` 为摘要；采集成功和事件完整性检查不等于已定位某条事件的调度根因。

## 验证和保留的失败

C++ 构建与 CTest 5/5、Python 编译检查、PowerShell AST 和 git diff 空白检查通过。初次管理员采集 `schedule-20261026-142333-5d7875d7` 成功启动内核录制，但测试在第一批因新增代码提前写尚未创建的结果字典而 KeyError。修复写入位置后完整重跑通过；旧失败、ETL、停止成功记录保留，未计入成功样本。

后续应在此记录版本上继续采集发生慢事件的同轮轨迹，才可区分通知调用前被抢占、等待内核唤醒、Ready 后等待 CPU，以及其他工作占用；当前证据不支持继续盲目调整队列锁。
