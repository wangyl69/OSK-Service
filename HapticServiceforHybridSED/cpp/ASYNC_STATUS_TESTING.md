# 异步状态反馈与 SED 等待验证（2026-10-23）

## 本轮处理

`src/runtime.cpp` 的运行时 interrupt 状态读取改为异步：发送状态请求，保留
Input ReadFile 的 OVERLAPPED 和缓冲区，检查完成时不等待 MCU；等待响应期间
仍可处理 PLAY。当前 ACK 无事务序号，因此同时只允许一个状态请求，其他
配置命令等待其完成。初始化、会话打开和配置确认保留原来的同步确认路径。
Feature 路径也未改成异步，不应把这次结果理解为所有 HID 操作都不阻塞。

显式 diagnose 请求合并，最多每秒开始一次。status 查询只读缓存。
健康验证仍是空闲时约 10 秒、持续输入时最多推迟至约 20 秒；运行时 interrupt
验证使用异步状态确认。250ms 内没有有效 ACK 则进入恢复，不能绕过已知故障。
等待期间约每 10ms 检查完成；新 PLAY 通过条件变量唤醒线程，不必等满该周期。
取消读取必须等待操作终止，再释放 OVERLAPPED、缓冲区和设备句柄。

SED 的 `HapticPipePublisher.Consume` 补充了 11.52ms 样本的定位备注。当前默认
已经是 AutoResetEvent 唤醒、先排空队列再等待；旧 BlockingCollection 等待路径
只作为可配置的 A/B 回退。该变更位于 C# SED，不是 C++ Service。
它是现有测量支持的优化，不能据此断言原等待原语是唯一根因或长尾永久消失。

## 为什么是 4 个场景加 10 批

这些是测试脚本的人为覆盖设计，不是产品的四种输入状态：每项 100 次，目标
间隔分别是 50ms、10ms、5ms、5ms 加并发诊断/状态请求；实际间隔受调度影响。
随后 10 批各 100 次，目标间隔 10ms，交替加入查询。每批重置性能统计，保持
同一会话和进程；没有每批 Session Close/Open。100 条还使每批记录可放进
Service 的 128 条有界追踪环。正式统计 1400 次，另有一次不计入的预热。

## 已运行验证

运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\validate.ps1`：

* C# 构建零警告、零错误，57 项测试通过。
* C++ 构建及 core、windows_io、firmware_contract 三项测试通过。
* 实际 Windows overlapped 管道读取：无数据时非阻塞检查、完成读取、取消后
  再读、销毁时取消未完成读取，通过。它验证 Win32 生命周期，不代替 HID 驱动。
* 标记/Down-Up 过滤、重复 Down、缺少 WMI 类、持续输入及接收端重启通过。
* 新增 `tests/check_async_status.py`：模拟 ACK 延迟 200ms 时，PLAY 在 ACK
  仍未完成期间已经处理；诊断限频、周期验证、挂起响应期间关闭/重开通过。
  模拟 ACK 延迟 500ms，250ms 超时进入 RECOVERING，通过。

完整链路另运行：

```powershell
py -3 tests/check_real_pipeline.py --with-osk --sed-trace --load-workers 4 --status-mode interrupt --mock-status-delay-ms 200 --run-label async-interrupt-sed-20261023 --quiet
```

1400 次全部通过，零丢弃，最终 generation=1，PLAY=1401（含预热），运行时
状态请求共 8 次，其中周期验证 2 次。4 个计算进程均未绑核。

| 测量段 | P50 | P99 | 最大 |
|---|---:|---:|---:|
| DLL 入口至 mock PLAY 完成 | 0.125ms | 0.418ms | 1.074ms |
| SED 发布至出队 | 0.014ms | 0.140ms | 0.456ms |
| 入队返回后至出队的等待下界 | 0.012ms | 0.136ms | 0.454ms |
| Service 提交至 mock PLAY 完成 | 0.012ms | 0.231ms | 0.972ms |

原始证据：`logs/real-pipeline-async-interrupt-sed-20261023/results.json`；分析：
`logs/async-interrupt-stage-summary-20261023.json`；专项状态测试：
`logs/async-status/results.json`。分段最大值不应相加，它们未必属于同一事件。
此轮开启诊断追踪，结果不与关闭追踪的生产配置直接等同。

## GC 与测试范围

此轮 GC 代际次数之和开始/结束都是 0，累计 GC 暂停增量也是 0。
这表示测量窗口内没有观察到托管垃圾回收及其暂停，不表示 CPU 没有竞争、
线程没有等待锁、事件唤醒后立即获得时间片，或整个机器没有其他暂停来源。
旧 11.52ms 样本定位于入队与出队之间；未采集到相应内核调度轨迹，不能继续
把它精确归因于某个驱动、中断、锁或 Windows 调度步骤。

本轮使用真实 OSK DLL、Windows hook、SED 和 Named Pipe，HID 为 mock。
没有验证实际触摸识别、鼠标点击键盘区、MCU、物理 HID-over-I2C 或振动。
桌面点击所需的 computer-use node_repl/sky 入口当前不可用；不能用 DLL 注入
冒充点击。实际硬件仍需验证 Input read 与 Output write 并发、拔插/睡眠恢复、
ACK 超时及总线抓取。配置过程及已开始的 Output 写入仍可能延迟新 PLAY。
