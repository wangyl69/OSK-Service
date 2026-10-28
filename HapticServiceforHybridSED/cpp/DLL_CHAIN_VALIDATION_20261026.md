# DLL 到 Haptic Service 扩展验证（2026-10-26）

## 结果与边界

新增 12 轮，共 11,220 次测量按键，全数完成；每次包含真实 OSK DLL KeyDown/KeyUp，经过 Windows Hook、HybridSED、Named Pipe、独立 C++ Service。每轮另有一次连通性预热，不计入次数。Service 无丢弃、没有松键额外 PLAY，各轮 ready=true、generation=1。没有启用 500Hz/四键过载，没有绑核，没有修改 Host 或 MCU 生产逻辑。记录开启诊断时间戳，有测量开销。

终点为显式 mock PLAY 完成，不包含实际 HID/I2C、MCU、DMA 或振动；DLL 入口也不是手指接触/OSK typing 判定时刻。无额外 CPU 压力表示不主动增加计算进程，不表示 Windows、代理和其他后台活动停止。

长尾仍存在。DLL→Service 接收超过 5ms 共 17 次；DLL→mock 完成超过 5ms 共 26 次；Pipe＋Service 超过 5ms 共 7 次。不同阶段慢样本可能重叠，不能相加。超过 5ms 是逐事件阈值，不是丢事件/超时次数。

## 方案与轮次

1. 标准场景：正常负载与 4 个普通优先级计算进程交替，各重复 3 轮，每轮 1,400 次。每轮 20/100/200Hz、200Hz 诊断并发各 100 次，再 10 批 100Hz 各 100 次，后者交替开启诊断并发。每批不重开会话。
2. 节奏场景：正常/四进程压力各先一轮，再因发现慢样本各追加两轮，每轮 470 次，共 2,820 次。包括三个 5ms 间隔后暂停 200ms 的输入节奏（有/无诊断并发各 100 次）、100Hz 有/无诊断并发各 100 次、250ms 间隔 50 次、1 秒间隔 20 次。短串内 200Hz 是边界场景，不是 500Hz 过载。
3. 分段：DLL 入口→Service submit（接收/解析后）；SED 发布→发送线程取出；WriteFile 入口→Service submit；Service submit→mock 完成；DLL→mock 完成。各阶段按同一事件关联，共享 QPC 时钟。
4. 5/15 秒空闲首键沿用本日单独的新增 60 次结果，不重复算进本页 11,220 次，见 [空闲首键补测](IDLE_FIRST_KEY_TESTING_20261026.md)。

## 分组统计

单位 ms；每格为 P99 / 最大值，使用各组原始事件重新计算，不平均分位数。各列最大值/分位数不能直接相加。

| 场景 | 次数 | DLL→Service 接收 | Pipe | Service | Pipe＋Service | DLL→mock 完成 | DLL→mock >5ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 标准，正常负载，3 轮 | 4,200 | 0.583 / 7.455 | 0.086 / 7.313 | 1.262 / 17.759 | 1.302 / 17.785 | 1.567 / 17.914 | 3 |
| 标准，四进程压力，3 轮 | 4,200 | 0.292 / 0.980 | 0.083 / 0.868 | 0.302 / 2.441 | 0.349 / 2.475 | 0.481 / 3.019 | 0 |
| 节奏，正常负载，3 轮 | 1,410 | 5.759 / 27.888 | 0.863 / 7.166 | 3.106 / 9.341 | 3.531 / 9.631 | 7.889 / 28.893 | 22 |
| 节奏，四进程压力，3 轮 | 1,410 | 0.361 / 6.117 | 0.134 / 0.788 | 0.675 / 3.003 | 0.732 / 3.025 | 0.924 / 6.335 | 1 |

各阶段超过 5ms：DLL→Service 接收 17 次（0.152%）；Pipe 2 次；Service 4 次；Pipe＋Service 7 次（0.062%）；DLL→mock 26 次（0.232%）。以上是本次混合场景的观测比例，不是日常使用的长期故障概率，事件之间也可能相关。

## 具体慢事件

* 标准正常负载第二轮 `soak_0`，100Hz 并发诊断：DLL→mock 7.566ms，其中 Pipe 7.313ms、Service 0.111ms；原生 WriteFile 调用本身只有 14µs。因此不能把 7ms 解释为复制一个字节的执行时间。
* 同轮 `soak_1`，100Hz 无并发诊断：DLL→mock 5.693ms，Service 排队 5.526ms。
* 同轮 `soak_9`，100Hz 无并发诊断：DLL→mock 17.914ms，Service 排队 17.759ms。因此慢样本不只发生于高频、诊断查询或 CPU 压力。
* 第一轮正常节奏 `alternating_fast_idle`：DLL→mock 最大 28.893ms，其中 SED 队列 27.697ms、Pipe 0.119ms、Service 1.005ms。此轮 DLL→mock >5ms 15 次；后续正常节奏重复轮仍分别观察到 2、5 次。不是仅一次不可复现的记录。

时间戳把主要等待定位到相应阶段，但不能区分线程 Ready 未获得 CPU、条件变量内部重获锁、同步或驱动中断等原因。SED GC 计数为 0 不能证明没有调度等待。四进程轮更快也不能推出负载可以治好长尾，更不能以忙等或绑核代替诊断。

## 已成功的内核采集与下一步

用户在管理员窗口运行的 `logs/real-pipeline-schedule-20261026-110241-e3c4e113/` 已实际产生 277,872,640 字节的 `scheduler.etl`，capture.json 记录启动/停止/测试退出均为 0。该轮 1,400 次作为单独的采集实验，不混入上表。DLL→Service 最大 0.778ms，DLL→mock 最大 0.800ms，没有复现本页慢事件，因此不能凭这份轨迹声称已找到慢样本的内核原因。本轮没有进一步解码 ETL 的 Ready/CSwitch 栈。

采集脚本新增 `-ScenarioSet cadence`，将测试节奏与问题复现条件对齐；通过 PowerShell AST 语法检查，尚未实际执行这个新采集参数。可在管理员 PowerShell、桌面解锁时运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:/work/OSK-MCU/HapticServiceforHybridSED/cpp/tools/Capture-Scheduling.ps1" -RequireKernelTrace -ScenarioSet cadence -LoadWorkers 0 -IdleWakeCount 0
```

优先捕获慢事件对应的 Ready/CSwitch/DPC 轨迹，再决定优化接收线程、等待同步或其他阻塞点；避免凭有限样本直接增加优先级或引入忙等。带内核采集的时间需单独报告其记录开销。

## 复现与原始证据

```powershell
# 首批 9,340 次：六轮标准及两轮节奏
py -3 HapticServiceforHybridSED/cpp/tests/check_dll_latency_campaign.py
# 追加 1,880 次：四轮节奏；使用新的时间戳目录保存
py -3 HapticServiceforHybridSED/cpp/tests/check_dll_latency_campaign.py --cadence-only --rounds 2
```

脚本顺序运行，保留失败日志，不覆盖已有活动/失败结果；每次生成独立时间戳目录。新增脚本已通过 py_compile 和上述完整运行。仅开启默认受控 mock，不会在设备缺失时偷偷跳过硬件发送并宣称硬件通过。

本地原始记录在被 Git 忽略的 `cpp/logs/`：

* `dll-campaign-20261026-110824/manifest.json`：首批 8 轮命令、退出码及结果路径。
* `dll-campaign-20261026-111307/manifest.json`：追加 4 轮。
* 两目录内 `stages.csv`、`slow-events.json`：逐轮/逐场景分段统计与全部慢事件时间戳。
* 首目录内 `combined-summary.json`、`combined-slow-events.json`：合并原始事件后统计，共 26 个慢事件。
* 首目录内 `binary-hashes.json`：实测 DLL、SED DLL、Service EXE 的 SHA256。

可提交的完整分场景表见 [DLL_CHAIN_STAGES_20261026.csv](DLL_CHAIN_STAGES_20261026.csv)。本轮 Host 工作区分支 debug、基线提交 7476e7c，保留已有未提交改动；MCU debug 为 5c66fc5，本轮未改动 MCU。新增脚本、采集选项、报告尚未提交/推送，没有烧录。
