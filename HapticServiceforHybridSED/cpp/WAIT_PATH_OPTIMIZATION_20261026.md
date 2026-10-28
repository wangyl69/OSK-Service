# Host 等待路径优化与验证（2026-10-26）

## 当前结论

已修改 SED 和 C++ Service 的三个等待/资源管理环节，并修复控制客户端并发连接竞争。C# 58/58、C++ CTest 5/5、会话/配置/反馈/恢复检查通过。修改后不依赖桌面的 5,000 次软件链路测量全数完成，无 Service 丢弃或多余 PLAY；其中发布器测试仍有一次约 6.468ms 的 Pipe＋Service 长尾，不能声称已经消除长尾。

解锁后已完成修改后 DLL 完整链路的八轮共 9,340 次测量：零丢弃、零多余 PLAY，所有分段和 DLL→mock 全程均无 >5ms 样本。四进程压力 cadence 曾在 250ms 空闲首键环节遭遇 LockApp，DLL 返回 WinError 5；该失败现场保留且不混入成功统计。再次解锁后，独立重跑该轮 470 次通过，本次计划已补齐。未更改 MCU，未烧录，未提交/推送；保留工作区既有改动。

## 用户新完成的采集

`logs/real-pipeline-schedule-20261026-112904-066d663a/` 是修改前的正常负载 cadence 采集；管理员启动/测试/停止均成功。470 次完成，ready=true、generation=1、dropped=0、play_sent=471（含一次预热）。DLL→Service 接收最大 1.156ms；SED 队列最大 0.997ms；Pipe 最大 0.270ms；Service 最大 1.331ms；Pipe＋Service 最大 1.368ms；DLL→mock 最大 1.605ms，没有 >5ms 事件。该轮也未捕获先前长尾，因此没有声称从 ETL 找到其调度根因；本轮未解码这份 ETL 的逐线程栈。

## 实现与约束

1. **SED 队列**：原生事件路径使用 8 槽固定环形队列，取代其内部 BlockingCollection/ConcurrentQueue，去掉与外部 AutoResetEvent 重复的阻塞通知和队列分段分配。短 monitor 保护多生产者，不假定只有一个生产线程，不声称无锁；锁内不做 I/O、日志或等待。旧 `HapticSenderNativeWake=false` 路径保留用于对照。
2. **Pipe 读取**：接收线程复用一个独占的手动复位完成事件，避免每次 ReadFile 创建/销毁句柄。上一次请求必须成功完成或取消并等待收尾，才 ResetEvent/复用。断开后也可复用，未留下挂起 OVERLAPPED 或缓冲区。
3. **Service 唤醒**：改用单消费者 AutoResetEvent，先检查/排空队列再等待。通知可合并，队列才是工作数量的依据。检查后、真正等待前到达的通知会被保留；不使用 PulseEvent、手动清除通知或生产路径忙等。等待时释放队列锁，返回时间戳在下一次显式取锁之前记录；移除了循环入口重复的 active() 取锁，便于区分等待与锁竞争。
4. **故障退避**：新的事件可被诊断/控制请求唤醒，但恢复等待使用原始绝对截止时间，避免每次通知提前重试。FAULT 锁定、Session 生命周期、异步 ACK 和 PLAY 优先策略保留。
5. **控制连接竞争修复**：本轮测试暴露 WaitNamedPipe 成功后被另一客户端抢占实例、CreateFile 返回 ERROR_PIPE_BUSY。控制客户端在原有 2 秒总期限内重试该竞争；不把该重试加到独立触觉数据管道，不重放 PLAY。

保持类别 1/2/3、上游 OSK marker/KeyDown 门控、队列容量 8、50ms 过期保护和状态限频。线程 AboveNormal、进程 Normal；不绑核，不使用实时优先级或修改系统电源策略。固定 10ms 的后台等待上限继续保留，但数据通知可立即唤醒，不是每个按键固定等待 10ms。

这些改动具有明确的结构性减负，但原生事件仍可能遭遇系统调度延迟，短锁也仍可能因持有线程被抢占而等待。它们没有消除跨进程传递与 Service 队列的一次线程交接；本轮未做风险更大的合并 Pipe 接收/HID 所有权线程或驱动改造。

## 验证

* 修改前 DLL/Hook/SED/Pipe/Service 基线 3,740 次（两轮 standard 1,400＋两轮 cadence 470），完成且无 >5ms 样本。目录 `logs/dll-campaign-20261026-113321/`；旧 EXE、SED 及依赖保存在其 `baseline-binaries/`。
* C# 构建 0 警告、0 错误，58 项测试通过；包括 4 个并发生产者、4,000 项环形队列跨越回绕顺序/唯一性测试、原生通知突发排空与空闲关闭，以及原有过滤/过期/连接失败测试。
* CTest 5/5；Windows I/O 测试增加 1,000 次事件复用/取消后读取和 1,000 次先通知再等待握手，验证没有残留完成信号、取消后误吞后续数据或漏唤醒。
* `check_service_features.py`、`check_async_status.py` 通过：配置、会话、状态降级、日志、心跳、重连、延迟 ACK 期间 PLAY、限频、ACK 超时恢复。
* `check_fault_latch.py` 初轮暴露控制客户端 ERROR_PIPE_BUSY；日志显示实际退避总时序仍正确。修复客户端后重跑通过，诊断通知持续每 250ms 一次，总退避实测 75.161 秒，FAULT 不能被 Close/Open 绕过，显式恢复后 PLAY 成功。没有把首轮失败隐去或当通过。
* 修改后 Publisher→SED 队列→真实 Pipe→Service→mock：两轮各 1,100 次，共 2,200 次；不经过 DLL、Hook 或触摸识别。
* 修改后直接 Pipe→Service→mock：正常与四进程压力各 1,400 次，共 2,800 次；不经过 DLL、Hook 或 SED。
* 另用保存的旧二进制做 2,800 次直接 Pipe 和 1,100 次发布器对照。源代码变更不会改变这组保存二进制；测试脚本可显式指定 EXE、Dispatcher/Probe 路径。

## 耗时对照

以下均为 ms，不包含实际 HID/I2C/MCU；每格 P99 / 最大值。不同轮次的后台活动不完全相同，不能当作随机化实验或硬实时保证。

| 测量 | 每轮次数 | 旧二进制 | 修改后 | 修改后重复 |
|---|---:|---:|---:|---:|
| 直接 Pipe＋Service，正常 | 1,400 | 0.236 / 0.582 | 0.249 / 1.576 | — |
| 直接 Pipe＋Service，四进程压力 | 1,400 | 0.114 / 9.086 | 0.120 / 1.168 | — |
| 发布器路径中的 SED 队列 | 1,100 | 0.491 / 2.440 | 0.235 / 2.331 | 0.405 / 2.141 |
| 发布器路径中的 Pipe＋Service | 1,100 | 0.566 / 4.331 | 0.468 / 6.468 | 0.486 / 1.129 |
| Publisher 入口→mock 完成 | 1,100 | 0.747 / 4.367 | 0.866 / 6.487 | 0.578 / 2.183 |

修改后直接 Pipe 两轮 >5ms 均为 0；发布器第一轮 1 次 >5ms，重复轮 0 次。第一轮慢事件主要是 Service 排队（6.439ms），显式 worker 锁等待的整轮最大 0.118ms；没有同一轮内核 Ready 轨迹，仍不足以把该等待完全归因于 CPU 调度。

可以确认减少了队列冗余机制和每读句柄分配，不能确认整个链路已经显著提速，更不能证明长尾风险降低到某个固定概率。特别是旧版直接 Pipe 正常轮最大值比新版小，而旧版压力轮最大值比新版大；应保留所有结果。

完整阶段表：[WAIT_PATH_COMPARISON_20261026.csv](WAIT_PATH_COMPARISON_20261026.csv)。

## 解锁后的 DLL 完整链路复测

记录目录：`logs/dll-campaign-20261026-130325/`，补测目录：`logs/real-pipeline-native-ring-cadence-load4-unlocked-20261026-final/`。六轮 standard（正常/四进程压力各三轮，每轮 1,400）以及两轮 cadence（正常/四进程压力各 470）成功，总计 9,340。每轮运行时均确认 SED 为 `FixedRing`、Service 为 `native-event`，等待时间戳采用 `wait_returned_before_lock`；Service 最终 `play_sent=测量数+1`（一次预热），`dropped=0`、`queue_depth=0`。真实 OSK DLL 注入按下/松开，经 Windows Hook、SED、真实 Named Pipe 和 C++ Service 到 mock PLAY；不等同于触屏点击或 MCU 测试。

20/100/200Hz 对应目标间隔 50/10/5ms；standard 含并发状态/诊断查询及十批持续输入。cadence 含快输入与 200ms 暂停交替、同速带/不带查询对照，以及 250ms/1 秒空闲首键。无 500Hz 过载，无绑核；模拟 interrupt ACK 延迟 200ms，验证异步反馈期间 PLAY 仍可执行。

以下为八个成功运行全部逐事件样本合并后的统计，单位 ms，不能对各阶段最大值求和来构造一次全链路延迟。

| 阶段 | P50 | P99 | 最大值 | >5ms 次数 |
|---|---:|---:|---:|---:|
| DLL 入口→Service 接收/提交 | 0.106 | 0.542 | 3.105 | 0 |
| SED 发布→发送线程取出 | 0.015 | 0.175 | 2.967 | 0 |
| Pipe 写入入口→Service 接收/提交 | 0.016 | 0.107 | 1.237 | 0 |
| Service 接收/提交→工作线程取出 | 0.013 | 0.791 | 3.505 | 0 |
| Service 接收/提交→mock PLAY 完成 | 0.013 | 0.792 | 3.506 | 0 |
| Pipe＋Service | 0.031 | 0.848 | 3.530 | 0 |
| DLL 入口→mock PLAY 完成 | 0.127 | 1.104 | 4.459 | 0 |

Pipe 区间包含接收线程唤醒、读取及解析；Service 区间包含排队，mock 不含真实 HID/I²C 发送。没有用微秒级报文构造耗时替代整个 Service 延迟。

| 场景 | 成功测量数 | Pipe＋Service P99 / 最大 | DLL→mock P99 / 最大 |
|---|---:|---:|---:|
| 正常 standard，三轮 | 4,200 | 0.996 / 3.530 | 1.336 / 4.459 |
| 四进程压力 standard，三轮 | 4,200 | 0.357 / 2.515 | 0.535 / 2.757 |
| 正常 cadence，一轮 | 470 | 1.070 / 1.938 | 1.620 / 2.413 |
| 四进程压力 cadence，补测一轮 | 470 | 0.389 / 1.994 | 0.580 / 2.249 |

修改前紧邻基线 3,740 次的 SED 队列 P99 为 0.435ms，新版为 0.175ms；但 Pipe＋Service P99 从 0.737ms 变为 0.848ms，全链路 P99 从 1.111ms 变为 1.104ms。样本构成/数量及后台活动不同，不能据此声称整体显著加速。新版本次未重现 >5ms，并不抹去此前新版发布器路径的 6.468ms 长尾，也不意味着真实长期概率为零。

完整阶段数据：[WAIT_PATH_DLL_STAGES_20261026.csv](WAIT_PATH_DLL_STAGES_20261026.csv)。最终八轮清单及原失败记录引用在 campaign 的 `completed-manifest.json`；逐轮、逐场景统计为 `completed-stages.csv`，慢样本列表为 `completed-slow-events.json`（空），版本/计数核对和合并统计为 `completed-summary.json`。原七轮 `manifest.json`、`stages.csv`、`slow-events.json` 和 `verified-summary.json` 保留不覆盖，便于追溯中断现场。

原 `cadence1-load4` 的 `results-failed.json` 保留了已完成的前四批各 100 次，DLL→mock 最大 1.261ms；这些部分测量没有混入上述 9,340 次。该次 250ms 首键批次未完整结束，也未完成后续 1 秒首键。失败原因为 DLL KeyDown 拒绝（WinError 5），随后前台确认 LockApp.exe PID 27964；没有把该失败归因于 Service 丢弃。补测重新执行完整 470 次，包含两个空闲首键批次，全部通过。

## 剩余验证与复现

先前首次锁屏失败也保留在 `logs/dll-campaign-20261026-114506/` 及其对应 `real-pipeline-.../results-failed.json`。本次软件验证计划已补齐；真实 HID/I²C、MCU 和机械振动耗时仍需连接硬件实测。

已执行的补测命令（未来复现时改用新的 run-label，保留本次现场）：

```powershell
py -3 HapticServiceforHybridSED/cpp/tests/check_real_pipeline.py --with-osk --quiet --sed-trace --status-mode interrupt --mock-status-delay-ms 200 --load-workers 4 --scenario-set cadence --run-label native-ring-cadence-load4-unlocked-20261026-final
```

如再次复现慢事件，再在管理员窗口为同一修改版本运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "D:/work/OSK-MCU/HapticServiceforHybridSED/cpp/tools/Capture-Scheduling.ps1" -RequireKernelTrace -ScenarioSet cadence -LoadWorkers 0 -IdleWakeCount 0
```

只分析同一轮时间戳和 ETL；不把未捕获慢事件的旧轨迹拼接到新的慢事件上。

本轮原始结果（本地 logs，Git 忽略）：

* `publisher-latency-native-ring-wake-20261026/results.json`
* `publisher-latency-native-ring-wake-repeat-20261026/results.json`
* `publisher-latency-saved-baseline-20261026/results.json`
* `pipe-stages-load0-native-ring-wake-20261026/results.json`
* `pipe-stages-load4-native-ring-wake-20261026/results.json`
* `pipe-stages-load0-saved-baseline-20261026/results.json`
* `pipe-stages-load4-saved-baseline-20261026/results.json`
* `features/results.json`、`async-status/results.json`、`fault-latch/results.json`
* `native-ring-wake-binary-hashes-20261026.json`：新二进制 SHA256。

调度事件采用微软的保留通知语义，队列为空检查与等待之间的 SetEvent 不会丢失；合并通知必须依靠队列排空保证工作不丢。[Microsoft Event Objects](https://learn.microsoft.com/en-us/windows/win32/sync/event-objects)。
