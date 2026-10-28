# 解锁后的真实 DLL 链路复测（2026-10-26）

## 结论与范围

六轮共完成 **5,282 次测量按键**，每次实际调用 OSK 随附 DLL 的 KeyDown 和 KeyUp，经过 Windows hook、HybridSED、真实 Named Pipe 和 C++ Service。全部轮次无 Service 丢弃、无额外 PLAY，generation=1，结束时 ready=true；松键没有多发触觉。

入口是随 OSK 发布的 `LrSendInput64.dll`，OSK 程序启动并确认初始化。未覆盖面板触摸识别、OSK 键盘区域的鼠标点击、原始 typing 算法或显示刷新。终点为显式启用的 mock PLAY 完成，未测真实 HID/I²C、MCU 或机械起振。每轮另有一个连通性预热按键，不计入上述样本。

正常负载这轮 Pipe＋Service 最大 1.052ms。压力首轮仍有 **18.390ms** 长尾，复测较快不能替代或删除该记录。本轮没有修改 Host 或 MCU 的生产逻辑，也没有提交或推送新改动。

## 六轮结果

单位 ms，表内为 **P99 / 最大值**。各阶段的分位数和最大值不能直接相加。

| 场景 | 次数 | Named Pipe | Service | Pipe＋Service | DLL 入口→mock 完成 |
|---|---:|---:|---:|---:|---:|
| 快输入/暂停交替、匹配速率下诊断并发、250ms/1s 间隔 | 470 | 0.075 / 0.221 | 0.897 / 1.405 | 0.930 / 1.424 | 1.483 / 2.862 |
| 正常负载，持续输入及突发 | 1,600 | 0.077 / 0.210 | 0.301 / 1.020 | 0.327 / 1.052 | 0.524 / 1.311 |
| 4 个计算进程压力 | 1,600 | 0.051 / 0.209 | 0.344 / 18.305 | 0.373 / 18.390 | 0.503 / 18.536 |
| 相同压力，完整重复一轮 | 1,600 | 0.053 / 0.095 | 0.118 / 0.524 | 0.137 / 0.538 | 0.357 / 0.700 |
| 5 秒输入空闲后首键 | 8 | 最大 0.046 | 最大 0.411 | 最大 0.430 | 最大 0.702 |
| 15 秒输入空闲后首键 | 4 | 最大 0.041 | 最大 0.034 | 最大 0.056 | 最大 0.374 |

空闲样本很少，只报告观测最大值，不据此估算低概率长尾。输入空闲期间仍有正常心跳和健康验证；没有逐键重开会话。

正常/压力完整轮次覆盖目标 20/100/200/500Hz、四键突发、诊断并发和十批持续输入。目标速率不是实际达到速率的保证，原始 JSON 保留注入用时和逐事件时间戳。计算进程为普通优先级，由 Windows 调度，没有绑核。Hook、SED sender、Service input/HID worker 的 AboveNormal 优先级与 Normal 进程优先级均由测试检查。

## 输入频率与验收范围

20/100/200/500Hz 的目标 KeyDown 间隔分别是 50/10/5/2ms；每次同时发送对应 KeyUp，后者不能额外产生 PLAY。实际注入间隔仍受 Windows 调度影响。

500Hz 和四键突发是额外的过载/积压检查，不作为正常打字的验收频率。以上历史 1,600 次轮次保留了这 200 次额外输入，不能把混合轮次的慢样本比例当成日常打字的发生概率。四个超过 5ms 的样本全部来自首轮四进程压力的 `rapid_500hz` 阶段；重复压力轮没有再出现。

以 Pipe 和 Service 各 5ms 的延迟预算评估时，主要报告 20Hz、100Hz 和空闲首键，200Hz 作为更高频边界场景。预算不是固定处理耗时，单次延迟也不等同于流水线的最小输入间隔。过载测试发现的 Service 等待仍应保留并定位，不能因为它发生在 500Hz 阶段就认定同步/调度路径没有问题。

## 正常负载的细分

以下为正常负载 1,600 次的独立统计，单位 ms。

| 阶段 | P50 | P95 | P99 | 最大 |
|---|---:|---:|---:|---:|
| Pipe：WriteFile 入口→Service submit | 0.022 | 0.045 | 0.077 | 0.210 |
| Service：submit→mock 完成 | 0.014 | 0.048 | 0.301 | 1.020 |
| Pipe＋Service | 0.037 | 0.092 | 0.327 | 1.052 |
| DLL 入口→mock 完成 | 0.157 | 0.324 | 0.524 | 1.311 |

Pipe 段包含接收线程唤醒和解析，Service 段包含排队；这些不是单纯复制或构造报文的耗时。时间戳取整到微秒，显示为 0 不代表零执行时间。

## 18ms 长尾：一次等待造成的积压

首轮压力测试的 `rapid_500hz` 目标速率阶段出现 4 个超过 5ms 的样本。时间戳显示它们集中在同一次工作线程等待中。恢复后在几微秒内连续取出了 5 个事件，其 Service 排队时间依次约为：

| 连续事件 | Service 排队 |
|---|---:|
| 第 1 个 | 18.304ms |
| 第 2 个 | 14.763ms |
| 第 3 个 | 8.926ms |
| 第 4 个 | 5.936ms |
| 第 5 个 | 2.920ms |

最慢事件 DLL→mock 为 18.536ms，其中 SED 队列约 0.020ms，Pipe 约 0.085ms，Service 排队约 18.304ms。提交端获取队列锁约 1µs；worker 随后显式获取队列锁不足 1µs。该事件的条件变量等待返回时间约在 submit 之后 18.302ms。

这将主要耗时定位到 Service 的等待/恢复区间，但显式 worker 锁计时不包含条件变量内部重新获取锁的等待。仅凭当前应用时间戳，仍不能区分线程已 Ready 却未获 CPU、条件变量内部等待或其他同步问题。trace 报告的 SED GC 次数和暂停增量为 0，也不能替代调度证据。

六轮总计 4 个 DLL→mock 超过 5ms、2 个超过 10ms、0 个超过 20ms；均来自上述同一组积压。相同压力重复轮没有超过 5ms 的样本，说明需按偶发问题继续定位，不能认定已修复。更高负载下某轮更快也不是负载改善性能的证据。

## 内核轨迹：采集入口已覆盖问题场景

本轮 WPR 仍返回 `Access denied / 0x80070005`，未产生内核 ETL。没有改动系统权限、没有自动提权；确认 WPR 没有遗留录制会话。

已给 `tools/Capture-Scheduling.ps1` 增加 `-ExtraScenarios`，确保管理员采集时会包含本次出现长尾的目标 500Hz 和突发阶段；参数写入 capture.json。`-RequireKernelTrace` 启动失败时明确终止，不再提示会继续注入。

在具备采集权限的管理员 PowerShell 中，从工作区根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File HapticServiceforHybridSED/cpp/tools/Capture-Scheduling.ps1 -RequireKernelTrace -LoadWorkers 4 -ExtraScenarios -IdleWakeCount 0
```

此命令仍使用 mock HID，并不接管或烧录 MCU。成功后将生成 QPC、线程信息和 scheduler.etl，以便在 WPA 对照 ReadyThread、CSwitch、等待栈及 DPC/ISR。现有普通权限只验证了采集器参数和失败退出路径，不能声称成功采到了调度轨迹。更多权限说明见 [KERNEL_CAPTURE_PERMISSIONS.md](KERNEL_CAPTURE_PERMISSIONS.md)。

## MCU 与其他检查

本轮串口枚举仍为空，没有板上计时或新振动数据。实际 MCU 健康检查函数的 1,000 轮、2,000 次模拟探测重新通过；C++ CTest 5/5 通过，其中休眠边界测试使用 CPU 指令替身。C# 构建为 0 警告、0 错误。这些结果不能作为 MCU 实际 latency。

工作区保持 OSK-Service debug 基线 `7476e7c`，MCU debug 为 `b0d581d`；本轮只更新测试报告、CSV 和采集脚本。

## 证据与导出

可导出的全部分段表见 [UNLOCKED_CHAIN_STAGES_20261026.csv](UNLOCKED_CHAIN_STAGES_20261026.csv)。原始完整 JSON 和逐事件时间戳保存在本地、被 Git 忽略的 `cpp/logs/`：

- `unlocked-chain-20261026.json`：六轮分析、各阶段尾部计数和最慢事件。
- `unlocked-chain-20261026-versions.json`：提交版本及关键文件哈希。
- `real-pipeline-unlocked-cadence-20261026/results.json`。
- `real-pipeline-unlocked-standard-20261026/results.json`。
- `real-pipeline-unlocked-load4-20261026/results.json`。
- `real-pipeline-unlocked-load4-repeat-20261026/results.json`。
- `real-pipeline-unlocked-idle5s-20261026/results.json`。
- `real-pipeline-unlocked-idle15s-20261026/results.json`。
- `real-pipeline-schedule-20261026-094447-a173c577/`、`real-pipeline-schedule-20261026-094810-453778d9/`：权限失败记录，后者验证新参数。

2026-10-23 被 LockApp 阻断的记录仍保留，见 [此前 debug 复测](DEBUG_LATENCY_FOLLOWUP.md)。本页是实际 DLL/Hook 链路的新数据，没有把此前绕过 OSK/Hook 的发布器测试混入统计。
