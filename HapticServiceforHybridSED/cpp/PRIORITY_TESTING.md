# 关键线程提优、优化与无硬件对照测试（2026-10-22）

> 历史对照：旧 `--cpu-load` 绑核命令已禁用。当前使用无绑核 `--load-workers`，默认 Event 唤醒及最新数据见 [发送线程唤醒报告](SENDER_WAKE_TESTING.md)。

本轮默认启用AboveNormal关键线程，保留Normal关闭开关。进程级别不主动提升；不修改系统其它服务、注册表、电源计划或全局计时器，不在正式运行中绑核。原Haptic_Service、Athena_Haptic和OSK配置未修改。

## 提优前先减少无用工作

- 在Worker创建语义对象前判断：标记OSK KeyDown或任意来源语音键才进入分类。普通KeyUp和未标记非语音输入直接CallNextHookEx；保留最终HapticInputGate，语音Down/Up规则不变。
- Debug关闭时不构造逐键日志的params数组和装箱参数。不能将此称作整个按键路径零分配；正常语义对象仍会创建。
- 保留已有专用发送线程、原生完成事件、有界队列8、50ms过期、不重放策略。没有放宽TTL或超时来提高通过率。
- 未采用忙轮询、进程High/Realtime、强制GC模式、生产绑核或MMCSS。当前先用幅度较小、可撤销的线程提优，依据对照样本评估。

## 当前配置与核对

| 位置 | 默认设置 |
|---|---|
| SED键盘钩子线程 | ThreadPriority.AboveNormal |
| SED触觉发送线程 | ThreadPriority.AboveNormal |
| C++管道接收线程 | THREAD_PRIORITY_ABOVE_NORMAL |
| C++HID执行线程 | THREAD_PRIORITY_ABOVE_NORMAL |
| WMI、日志、控制查询 | 不提优 |
| 进程 | 不主动修改优先级类别；本机验证为Normal |

默认启动命令与原来相同。恢复普通线程优先级可分别使用：

```powershell
# C++，启动参数；安装SCM时也会保存该参数
.\build\HybridHaptic.exe --console --mock --thread-priority normal
# SED，从对应工作目录运行
dotnet run --project src/SemanticEventDispatcher -- --Dispatcher:LatencyThreadPriorityBoost=false
```

C++允许`normal|above-normal`，默认above-normal；SED的`Dispatcher:LatencyThreadPriorityBoost`默认true，支持appsettings、环境变量或CLI。只在专用线程入口设置，设置失败记录Warning，保留当前优先级继续运行。恢复AboveNormal时改回对应参数并重启。

SED启动日志包含Role、Win32Id、Requested和Effective。C++日志包含相同用途的信息，status新增requested_thread_priority、input_thread_priority、hid_thread_priority、query_thread_priority、process_priority_class。线程字段是启动时实读值，不是配置成功的推测；外部程序事后修改需另外核对。测试还通过OpenThread/GetThreadPriority实读SED关键线程。

本机实读关键线程相对优先级1、查询线程0、进程类别32（NORMAL_PRIORITY_CLASS）。正常进程下相对Normal/AboveNormal对应基本优先级8/9，动态boost可能进一步影响实际调度。参考[Windows调度说明](https://learn.microsoft.com/en-us/windows/win32/procthread/scheduling-priorities)与[SetThreadPriority](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadpriority)。

## 测试方法

调用OSK实际使用的LrSendInput64.dll，经过真实Windows低层钩子、SED、命名管道和C++进程，末端HID使用mock。每个完整profile计量1400次按下，另1次预热；覆盖20/100/200Hz目标间隔、状态/诊断并发和持续输入。OSK程序也实际启动并确认Initialized；没有模拟物理触摸识别。

正常和提优方案使用相同的早期过滤及日志优化，因此对照隔离的是线程优先级。空闲profile各一次；受控CPU争用各两次，顺序为提优1、普通1、普通2、提优2。测试只把自己启动的SED/Service/注入器限制到两个允许的逻辑CPU，再启动两个Normal优先级计算进程竞争同一CPU集合，结束清理并恢复注入器亲和性。正式软件不做该限制；这属于人为构造的争用条件，不能当作整机日常负载。

注入频率是目标sleep间隔，并非严格实时；JSON的actual_injection_elapsed_ms记录循环及查询线程收尾耗时，不用于计算延迟分位数。延迟从调用原生KeyDown前到mock PLAY完成，QPC同一时钟；每轮保存1400个微秒样本，表中p50/p95/p99为整轮合并统计。未完成的profile不报告缺失事件被排除后的延迟分位数。

## 对照结果

| 场景 | 计量触发 | p50(ms) | p95(ms) | p99(ms) | 最大(ms) |
|---|---:|---:|---:|---:|---:|
| Normal，空闲 | 1400/1400 | 0.162 | 2.151 | 13.063 | 53.942 |
| AboveNormal，空闲 | 1400/1400 | 0.150 | 0.574 | 1.549 | 25.403 |
| AboveNormal，CPU争用1 | 1400/1400 | 0.207 | 1.244 | 7.734 | 42.427 |
| Normal，CPU争用1 | 64/首批100，失败停止 | — | — | — | — |
| Normal，CPU争用2 | 89/首批100，失败停止 | — | — | — | — |
| AboveNormal，CPU争用2 | 1400/1400 | 0.193 | 1.061 | 1.880 | 16.452 |

失败profile没有继续剩余1300次或剔除失败后算通过。普通方案争用1：SED过期35、Service过期1；争用2：SED过期11、Service丢弃0。SED最大排队等待分别约121ms、158ms，连接/写入失败、权限错误和队列满均为0。完整profile均满足准确计数、无额外KeyUp PLAY、Service dropped=0和有序trace断言。

当前样本支持保留温和线程提优，但没有证明所有Windows长尾均解决：提优争用1仍有42.427ms最大值。各端50ms是本地队列过期策略，计时还包含进入SED之前的注入/调度，因此不构成整体50ms上限。

之后完整Extended回归在默认提优下再计量1400次，通过且无额外PLAY或丢弃，合并p50/p95/p99/max为0.146/0.558/3.622/41.649ms。本轮默认提优累计5600次计量按键全部匹配；Normal空闲1400次通过，两轮争用各首批100次失败分开保留。完整回归退出码0，包括53项C#、3套CTest及实际3/6/12/24/30s故障退避/FAULT锁存恢复。

## 回归和证据

完整回归入口：

```powershell
powershell -ExecutionPolicy Bypass -File .\validate.ps1 -Extended
# 单个空闲profile
py -3 .\tests\check_real_pipeline.py --with-osk --thread-priority above-normal --run-label boost-idle-repeat --quiet
# 两逻辑CPU受控争用；改normal即可对照，失败返回非零并保留原因
py -3 .\tests\check_real_pipeline.py --with-osk --thread-priority above-normal --cpu-load --run-label boost-load-repeat --quiet
```

请逐个运行输入注入测试。CPU竞争进程最多运行120秒，正常结束由脚本清理；仅在带--cpu-load的profile启动。测试默认关闭额外Python hook，关闭逐PLAY日志；原生trace只在mock启用。整轮需要Windows、OSK发布文件、.NET SDK/相应runtime、C++构建工具和Python标准库，部署C++EXE不依赖Python/.NET。

- [完整构建和回归日志](logs/priority-validation.txt)：53项C#、3套CTest、真实SED联调、各模式/会话/维护测试；额外覆盖未标记语音键的Down/Up仍送WMI而不送触觉。
- [六个profile汇总](logs/priority-comparison.json)，包含成功及失败、实读优先级、原始结果目录和SED原因计数。
- [空闲Normal](logs/real-pipeline-priority-normal-idle/results.json)、[空闲提优](logs/real-pipeline-priority-boost-idle/results.json)。
- [提优争用1](logs/real-pipeline-priority-boost-load-1/results.json)、[提优争用2](logs/real-pipeline-priority-boost-load-2/results.json)。
- [Normal争用1失败](logs/real-pipeline-priority-normal-load-1/results-failed.json)、[SED原因](logs/real-pipeline-priority-normal-load-1/dispatcher.txt)；[Normal争用2失败](logs/real-pipeline-priority-normal-load-2/results-failed.json)、[SED原因](logs/real-pipeline-priority-normal-load-2/dispatcher.txt)。
- [默认提优Extended压力结果](logs/real-pipeline-extended/results.json)、[完整FAULT退避结果](logs/fault-latch/results.json)。

普通优先级的两轮争用失败是本轮对照结果，并未宣称所有profile通过；默认提优方案的功能及压力测试结果与它们分开列出。

## 当前无法验证的部分

机器没有touch设备和外接MCU，未验证真实触摸/typing识别、HID/I²C驱动行为和机械起振。当前非管理员，未安装/启停SCM服务或采集需要管理员权限的内核调度ETW；不能仅凭排队时间断定每段停顿来自CPU Ready、GC、DPC或锁。优先级无法直接消除这些不同等待，目标机仍需WPR/WPA及实际触觉测量。也未设置高优先级进程或修改其它后台Service。
