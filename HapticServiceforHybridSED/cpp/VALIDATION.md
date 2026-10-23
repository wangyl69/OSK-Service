# 无MCU复查与验证记录：2026-10-22

后续已增加关键线程提优及早期过滤优化：当前53项C#测试、优先级A/B和CPU争用结果见[优先级测试报告](PRIORITY_TESTING.md)。下文保留提优前45项测试与三轮压力验证的基线，日志路径可能被一键脚本的新运行更新；当时样本数以此处记录为准。

完整`validate.ps1 -Extended`退出码0，随后两轮实际OSK注入DLL压力复测也通过。三轮共4200次计量按键全部匹配。修正前失败记录仍保留，未通过放宽阈值或重放事件消除失败。

## 本轮发现和修正

| 问题 | 当前处理 |
|---|---|
| WMI同步COM调用阻塞键盘钩子 | 独立有界后台队列，过载保留最新状态及最终Stop，正常关闭有限等待 |
| 每键日志影响响应 | 常态逐键日志改Debug；性能mock关闭逐PLAY打印 |
| 发送等待托管异步完成，偶发积压过期 | 专用线程+NativePipeWriter直接等待Windows完成事件；复用固定缓冲区及OVERLAPPED，取消后等待内核终结再复用 |
| 失败无法区分原因 | 5s汇总队列满、过期阶段、连接/写入、权限和超时，记录最大排队/写入等待时间 |
| Feature字段错位会拒绝合法MCU | caps_id在18、config_id在19，分别检查0x10/0x11；修正同源mock，新增直接引用固件协议头的独立契约测试 |
| interrupt未提供的计数误显示0 | accepted/I²C等未知字段返回null，失效快照也标未知 |
| 暂停/恢复可绕过FAULT | 独立锁存，仅recover或切换模式解除 |
| 原生I/O零等待仍产生轮询延迟 | 按实际deadline等待、零等待排空旧ACK；取消后确认终结，保护内存生命周期 |
| 非法输入逐条刷写日志 | 原子计数并限频汇总；验证10001个非法字节之后有效输入仍通过 |
| 状态查询争用输入提交锁 | 指标独立锁，复制后再计算分位数 |
| hook启动/退出竞态、权限错误 | 提前创建消息队列、检查退出信号与停止；权限错误限流记录，后续新事件继续尝试 |

正式链路仍为OSK标记且KeyDown → 单字节1/2/3 → C++ Service。两端队列仍各8项、过期仍50ms，失败不重放。C#连接及写入共用默认20ms预算；取消收尾为内存安全需等内核终态，不能当作绝对墙钟上限。

## 实际执行结果

| 检查 | 结果 |
|---|---|
| C#构建及测试 | 0警告/错误，45项通过，包括真实hook、WMI隔离/过载、权限恢复及原生管道超时/取消/复用 |
| C++ Release及CTest | core、windows_io、firmware_contract三套全部通过 |
| 标记Down/Up、无标记、重复Down | 分别1次、0次、2次PLAY |
| F24 WMI失败 | Down仍触发，Up不触发；缺失测试WMI类的错误日志是预期现象 |
| 合成注入与实际OSK DLL联调 | 两组独立通过；各含40次持续输入、接收端重启、非法类别及批量读取 |
| 控制及模式 | 动态配置/回读、暂停/恢复、generation、recover、各模式、模式切换通过 |
| 日志及维护 | 2MiB轮转、保活关闭/启用、10s验证、并发诊断输入及非法字节洪泛通过 |
| 完整故障退避 | 实际等待3/6/12/24/30s，6次故障进入FAULT，session close/open不绕过，显式恢复成功 |
| OSK实际程序 | 三轮均启动OskDemoApp，确认新日志OSK Initialized:true，测试期间保持运行 |
| 软件链路压力 | 连续三轮各1400次计量按键匹配，无KeyUp额外PLAY；每轮另1次预热，最终play_sent=1401、Service dropped=0 |

每轮含20Hz、100Hz、200Hz、200Hz并发状态/诊断，以及10组100Hz持续输入。正向输入调用OSK实际使用的`Resources/LrSendInput64.dll`，经过真实Windows钩子、SED、管道和C++进程；仅末端HID/MCU为mock。性能测试没有额外Python键盘钩子；trace只在mock显式启用，固定128项内存记录。

## 延迟及准确范围

使用同一QPC时钟，从原生KeyDown调用前测到Service的mock PLAY完成。包含注入、Windows hook、SED和IPC调度，不包含原始触摸识别、实际HID驱动/I²C、AW和机械起振。

每组100次分别统计：p50范围为该轮14组中位数的范围，最差p99为各组p99最大值，不能当作合并1400点的p99。

| 运行 | 各组p50范围(ms) | 最差组p99(ms) | 全轮最大(ms) |
|---|---:|---:|---:|
| Extended | 0.114–0.238 | 8.710 | 24.999 |
| 复测2 | 0.145–0.225 | 12.351 | 13.643 |
| 复测3 | 0.135–0.278 | 26.793 | 34.159 |

三轮最后一次SED汇总的MaxWriteUs分别1390/1629/1665，MaxQueueWaitUs分别8586/12181/32427。汇总每5s一次，这些是最后已记录快照；Service最终计数及每批trace断言覆盖全部计量按键。

修正前仅换专用线程仍出现MaxWriteUs=35416、MaxQueueWaitUs=54034、ExpiredAtDequeue=1。写入时间包含等待托管完成通知，不能据此认定字节抵达Service花了35ms。改为原生事件后上述三轮未复现丢弃，但Windows调度/GC等仍可能产生长尾，不能承诺每次1–2ms，或宣称物理触觉延迟已无回退。

失败证据：[原线程池样本](logs/real-pipeline-serial-1/dispatcher.txt)、[专用线程但仍托管写入](logs/real-pipeline-final-1/dispatcher.txt)、[第一次全量复查失败](logs/audit-validation-initial-failure.txt)。

## 复现与证据

在此目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\validate.ps1 -Extended
# 单独复测，label将每轮结果分开保存
py -3 .\tests\check_real_pipeline.py --with-osk --run-label repeat-1 --quiet
```

测试注入无打印字符的F23/F24。请顺序运行键盘注入测试，避免共享Windows输入流互相干扰。脚本使用独立管道，清理自己启动的进程；不注册WMI、不安装SCM、不访问物理HID、不烧录MCU。运行实际OSK程序会显示其窗口。

- [完整Extended日志](logs/audit-validation.txt)
- [合成输入](logs/validation-synthetic/dispatcher.txt)、[实际OSK DLL联调](logs/validation-osk-dll/dispatcher.txt)
- [功能结果](logs/features/results.json)、[完整FAULT结果](logs/fault-latch/results.json)
- [Extended压力](logs/real-pipeline-extended/results.json)、[复测2](logs/real-pipeline-native-event-2/results.json)、[复测3](logs/real-pipeline-native-event-3/results.json)

环境：Windows x64、LLVM-MinGW20261006、CMake/Ninja、.NET SDK10.0.402及Core/Desktop Runtime10.0.13。net8使用验证进程的Major roll-forward，本机没有原生.NET8 runtime；未修改目标框架或系统设置。测试编排使用Python标准库，C++部署EXE不依赖Python/.NET。固件契约测试仅引用相邻Athena_Haptic协议头，不构建/修改固件；独立移走固件目录时该项不启用。

## 实机边界

本轮没有点击/触摸OSK按键，未验证原始触摸到typing的识别；实际程序初始化加真实注入DLL不能替代它。没有外接MCU，WARM/回读及状态模式为模拟结果。SCM安装启停、真实HID/I²C/AW振动、Feature同步调用最坏阻塞、断电/复位、锁屏和睡眠恢复未实测。超时/取消可与实际交付竞争，Host Sent/Dropped不等于MCU执行次数，继续禁止重放。

同步Feature一旦进入驱动不能被后来按键抢占。通常10s验证、20s强制验证和空闲维护降低竞争，没有消除真实驱动阻塞。接入MCU后仍需同机同固件测首键、持续输入p95/p99及物理起振。原Haptic_Service和Athena_Haptic本轮未修改。
