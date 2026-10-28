# debug 工作区延迟验证（2026-10-23）

2026-10-26 更新：解锁后已补齐真实 DLL/Hook 链路测试，结果见 [完整 Host 链路报告](UNLOCKED_CHAIN_TESTING_20261026.md)。本页保留为此前测试和阻断记录。

## 工作区与版本

`D:\work\OSK-MCU` 现已直接跟踪 OSK-Service 的 `debug` 分支，基线提交 `7476e7c`。原发布副本仍保留。此前本地脚本中的旧目录名与远端适配结果逐一比较，仅同步等价的路径/空白差异；原文件备份在本地 `.workbuddy/debug-alignment-20261023/`。

Athena_Haptic 仍是独立仓库的 `debug` 分支，提交 `b0d581d`；其已有未提交目录删除保留。根仓库通过本地 exclude 排除 MCU、旧 Service、历史 ZIP 和工作区工具。

本轮只增加或调整验证程序，没有修改 Host/MCU 生产逻辑，也没有提交、推送或烧录本轮新增测试。

## Host：完成的测试

本轮四类完整成功记录共 2,826 个测量事件，均无丢失、无多余 PLAY，服务保持 generation=1。不同入口的数据分开报告，不能当成同一端到端分布。

| 测试入口 | 数量 | Pipe＋Service P99 | Pipe＋Service 最大 | 整个测试入口→mock 最大 |
|---|---:|---:|---:|---:|
| Python overlapped WriteFile → 实际 Named Pipe → C++ Service | 1,600 | 0.217ms | 2.112ms | 2.112ms |
| 实际 C# HapticPipePublisher → Pipe → C++ Service | 1,100 | 0.514ms | 2.479ms | 2.516ms |
| 最终测试工具回归，同上发布器入口 | 100 | 0.521ms | 2.868ms | 2.900ms |
| 发布器入口，1 秒/5 秒输入空闲后首键 | 26 | 小样本不据此估计稀有长尾 | 0.423ms | 0.916ms |

所有终点均为显式启用的 mock PLAY 完成；未测真实 HID/I²C、MCU 或机械起振。Pipe 段起点为 WriteFile 入口，终点为 Service submit 入口，包含接收线程唤醒及解析，不是纯内存复制时间。

### 真实发布器测试的范围

新增 `tests/PublisherLatencyProbe` 直接引用生产 C# 工程，调用实际 `HapticPipePublisher.Publish()`，使用真实原生事件唤醒、队列和 NativePipeWriter。注入的是预先分类的 1/2/3 事件，绕过 OSK、键盘钩子、SED_OSK/KeyDown 来源判定，不对这些上游环节作验证结论。合成事件的 hook 时间戳保留为 0，不冒充真实 OSK 的时间。

覆盖 20Hz、200Hz、诊断并发、三个 5ms 间隔与一个 200ms 间隔交替、持续输入，以及 1 秒/5 秒输入空闲。没有重开会话来制造每次输入；空闲测试在统计设置完成后再等待，期间保留正常心跳和周期健康验证。26 次样本不足以评价低概率长尾。

1,100 次发布器记录中，SED 队列 P99/最大为 0.264/0.740ms；Service 总处理 P99/最大为 0.434/1.864ms。最慢的整个入口样本为 2.516ms，其中 Service 排队约 1.863ms，而显式记录的提交端与工作线程取锁均不足 1µs。它不能排除条件变量内部重新取锁或其他位置的等待，仍需要内核轨迹判断 Ready→Running。

本轮没有观察到此前 25.516ms 的 Pipe＋Service 极值，不能据此声称它已被消除。每个场景的诊断查询样本有限，查询组更快也不能说明开启查询会优化产品。

### 尚未完成的真实 OSK 重测

新增真实 OSK cadence 场景在预热按键通过后，首个测量按键的 DLL 调用失败。随后读取的前台进程为 `LockApp.exe`；虽然输入桌面名为 Default，这仍不足以证明当时处于可正常注入的普通桌面。失败保存于本地 `logs/real-pipeline-debug-cadence-20261023/results-failed.json`，没有拿上述绕过钩子的测试替代它，也没有把未完成轮次并入成功样本。

真实 OSK 脚本已加入 DLL KeyDown/KeyUp 的 Win32 last-error 诊断，并增加 `--scenario-set cadence`。恢复普通桌面后运行：

```powershell
py -3 HapticServiceforHybridSED/cpp/tests/check_real_pipeline.py --with-osk --quiet --sed-trace --status-mode interrupt --mock-status-delay-ms 200 --scenario-set cadence --run-label debug-cadence-unlocked
```

该计划为 470 次测量，包含快输入/空闲交替、匹配速率下有/无诊断查询，以及 250ms/1000ms 输入间隔；原 standard 场景保持不变。

## MCU：完成的逻辑验证

当前串口枚举为空，因此没有板上 UART/HID/I²C 时序或振动数据，未执行自动烧录。

新增 `tests/check_firmware_idle_health.py` 从当前 MCU 源码提取三个函数体并原样编译，读取实际配置宏（当前两路 AW），以寄存器与输入替身执行实际健康检查代码，而非用 Python 重写算法。验证通过：

- 前 31 次播放不访问健康寄存器，第 32 次仅标记到期。
- 存在 PLAY 或原始 HID 报告时延后，检查进度不丢失。
- 每次协作步骤最多探测一个已选择电机，期间新 PLAY 到达时不继续探测下一电机。
- 跳过未选择电机，成功/失败后恢复原电机选择；故障向调用者返回失败，保留恢复处理。
- 1,000 个健康检查轮次、2,000 次模拟寄存器探测，没有重复或丢失检查进度。

扩展 `firmware_runtime_tests.cpp`，直接执行生产 `APP_Runtime_TryIdle`：128,000 组检查/睡眠边界组合，其中 72,000 次模拟中断到达，覆盖关闭中断前、原子检查期间、WFI 附近、恢复中断前后，以及已有待处理输入、DMA 快速轮询和调用者已屏蔽中断的情况。

上述结果验证协作逻辑和临界区顺序；真实 WFI 指令、NVIC、中断延迟、寄存器访问耗时、DMA 起点和保护电路均未在板上测量。测试工具的 x64 执行时间没有当作 MCU latency。

## 其他回归与复现

57 项 C# 测试、5 项 C++ CTest、延迟 ACK 期间继续 PLAY、诊断限频、会话关闭/重开与超时恢复均通过。固件构建为最新，无需重新编译；旧真实链路记录仍可由更新的分析器读取。新增测试工具构建无警告。

在根目录运行以下命令可复现不依赖解锁的验证；健康检查测试需要相邻的 Athena_Haptic 源码和 GNU 风格 C++ 编译器，默认读取 Host 的 CMake 编译器路径。

```powershell
dotnet build HapticServiceforHybridSED/cpp/tests/PublisherLatencyProbe/PublisherLatencyProbe.csproj
py -3 HapticServiceforHybridSED/cpp/tests/check_publisher_latency.py --run-label debug-publisher-repeat
py -3 HapticServiceforHybridSED/cpp/tests/check_publisher_latency.py --idle-only --run-label debug-idle-repeat
py -3 HapticServiceforHybridSED/cpp/tests/check_firmware_idle_health.py
ctest --test-dir HapticServiceforHybridSED/cpp/build --output-on-failure
```

原始 JSON、逐事件记录与编译输出保存在本地 `cpp/logs/`，目录不进入 Git：

- `pipe-stages-summary-debug-followup-20261023.json`
- `publisher-latency-debug-20261023/results.json`
- `publisher-latency-debug-idle-recheck-20261023/results.json`
- `publisher-latency-debug-smoke-20261023/results.json`
- `firmware-idle-health-debug/result.json`（包含被测源文件与配置哈希）
- `debug-followup-context.json`

新增工具调试时的未完成记录也保留；空闲参数上限及响应超时修正后已重新验证。不同轮次没有以新结果覆盖此前长尾报告。
