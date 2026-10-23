# HybridSED 逐事件计时与无绑核测试

本报告保留发送线程唤醒优化前的基线。当前已默认使用 Windows Event，最新对照与回退方式见 [发送线程唤醒报告](SENDER_WAKE_TESTING.md)。

2026-10-23。当前机器无触摸屏、无 MCU；末端 HID 为显式 mock。这里只报告主机软件路径。

## 结果

C# 构建无警告、无错误，55 项测试通过；C++ 3 项测试通过；合成输入、实际 OSK DLL、Service 控制/恢复/状态模式回归均通过。另运行下面六个实际进程 profile，每个测量 1,400 次，合计 8,400 次。所有 profile 计数匹配、无额外 KeyUp PLAY、Service dropped=0。计算负载只使用普通优先级进程，没有限制任何测试进程的 CPU affinity。

| profile（logs/real-pipeline- 后缀） | SED trace | 普通计算进程数 | 全链路 p50 / p99 / 最大，ms |
|---|---|---:|---:|
| sed-stages-idle-20261023 | 开 | 0 | 0.145 / 1.746 / 10.340 |
| sed-stages-load4-20261023 | 开 | 4 | 0.120 / 2.264 / 14.488 |
| sed-stages-load8-20261023 | 开 | 8 | 0.127 / 0.484 / 3.017 |
| sed-stages-gc-load4-20261023 | 开，增加 GC 统计 | 4 | 0.124 / 0.496 / 15.073 |
| sed-trace-off-load4-20261023 | 关 | 4 | 0.123 / 0.496 / 17.298 |
| sed-stages-enqueue-boundary-20261023 | 开，增加入队前/返回点 | 4 | 0.134 / 2.595 / 11.670 |

结果没有单调的“计算进程越多越慢”关系；不能凭这些极值确定电源管理或调度器的具体原因。最后两个带 GC 统计的 trace profile 从开始到最后事件的各代 GC 次数之和保持 0，报告的累计暂停时间为 0。这为这两轮排查提供证据，不能代替其他轮次的 GC 追踪。

本轮未复现历史 42 ms，但复现了多条 10–15 ms 等待。关闭 trace 的 profile 也有 17.298 ms 样本，因此性能结论不能只依据“诊断开启时出现过长尾”。默认关闭新增 SED trace。

## 一个已精确拆开的慢事件

`sed-stages-enqueue-boundary-20261023/results.json` 中 sequence=681，完整软件时长 11,670 μs：

| 段 | 时长 |
|---|---:|
| DLL 调用入口 → SED hook 入口 | 88 μs |
| hook 入口 → Publisher 创建事件时间戳 | 13 μs |
| Publisher 时间戳 → sender 取出事件 | 11,524 μs |
| sender 取出 → 原生管道 WriteFile 入口 | 12 μs |
| 原生管道 WriteFile 入口 → Service submit 入口 | 22 μs |
| Service submit → mock PLAY 完成 | 11 μs |

进一步拆解队列段：trace 写入等入队前准备约 2 μs，`TryAdd` 调用约 2 μs，**TryAdd 成功返回后到 sender 取出仍有 11,520 μs**。前一个事件 sequence=680 的写入早已结束，本轮 GC 统计也为 0。这条样本的主要等待已定位到 SED 发送线程等待/唤醒/调度区间；当前数据尚不能区分它是在等待原语中滞留，还是已经 ready 而没有得到 CPU。

这一事件的原生 `WriteFile` API 调用本身约 10 μs。API 调用/完成等待可以与管道接收和 Service 执行重叠，不能将它们再次加到上表总时长中。这个管道 WriteFile 与 Service 向物理 HID 的 WriteFile 是两个不同的接口；mock 没有执行后者。

## 实现和边界

- `Dispatcher:HapticLatencyTraceName` 默认为空。测试传入唯一 `Local\\...` 名称才创建共享内存；默认不记录逐事件诊断数据。
- 固定 4096 项，不循环覆盖。满后继续正常处理输入，只停止新增 trace；长测应分轮重启诊断进程。
- 记录 hook、发布、入队调用前/返回、sender 取出、写入准备、原生管道 API 前/返回、写入函数完成和终态。固定 RAM 写入，不逐键写文件/控制管道，不改变生产单字节 1/2/3 协议。
- trace 开启会增加少量诊断调用和共享内存写入。带 trace 与默认关闭的 profile 应分开解读，不能宣称测量零开销。
- 入队返回可能晚于 sender 取出，因为两个线程可同时执行。可确认的“入队返回后等待”下界取 `max(0, dequeue - enqueue_return)`。旧版只记录 publish 到 dequeue 的区间，也包含入队前准备/抢占，不能全部认定为 sender 唤醒慢。
- Service submit 位于管道读取与字节解析之后。物理触摸、HID/I²C 驱动传输和马达启动仍不在本测试范围。
- 失败 profile 会保留已注入时间戳、Service 状态和包含丢弃/未完成记录的原始 SED snapshot，不把缺失事件当作成功样本。

## 复测

在 cpp 目录运行，逐个执行输入注入测试：

```powershell
# 精细计时，4 个无绑核普通计算进程
python tests/check_real_pipeline.py --with-osk --sed-trace --no-sed-native-wake --load-workers 4 --run-label stages-repeat --quiet

# 默认关闭 SED trace 的相同负载对照
python tests/check_real_pipeline.py --with-osk --no-sed-native-wake --load-workers 4 --run-label default-repeat --quiet

# 离线核对关联及最长事件，不注入新输入
python tests/analyze_latency.py logs/real-pipeline-stages-repeat logs/real-pipeline-default-repeat --output logs/stages-repeat-summary.json
```

历史 `--cpu-load` 绑核模式已经禁用。使用 `--load-workers` 不更改 affinity；计算进程最多运行120秒，测试结束时清理自己创建的进程。

后续若要区分等待原语、ReadyThread 调度与驱动/DPC，应结合同一事件的 Windows 线程调度追踪。目前的非管理员验证没有采集内核调度 ETW，不能把等待进一步归因。当前不增加 Realtime 优先级、全局定时器或长期忙等来掩盖这个区间。
