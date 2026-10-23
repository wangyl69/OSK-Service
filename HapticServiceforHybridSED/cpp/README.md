# C++ HybridSED 触觉服务：直接接收版

2026-10-23 后续更新：Host 固定容量队列、锁外唤醒、真实 HID 可用的等待诊断，以及新一轮 16,000 次样本见 [等待优化与复测](WAIT_OPTIMIZATION_TESTING.md)。该报告包含仍然出现的 25.516ms Pipe＋Service 长尾，不能只取较快轮次判断已经解决。

MCU 已实现协作调度优化并完成构建；单板串口 64 次热播放、所需硬件和验证范围见 [MCU 单板操作说明](../../Athena_Haptic/docs/hostlatency_standalone_validation_20261023_zh.md)。当前未发现 COM 端口，尚未板上实测。

按 T0～T5 整理的最新 [延迟分段表](HOST_LATENCY_TABLE.md) 与 [CSV](HOST_LATENCY_STAGES.csv) 包含本轮 9600 次请求、Named Pipe 和 Service 细分统计。内核诊断用途及正规开启方式见 [采集权限说明](KERNEL_CAPTURE_PERMISSIONS.md)。

目标 Panel 的原始触摸采集命令及 5/15 秒空闲首键验证见 [原始触摸与长空闲](RAW_TOUCH_AND_LONG_IDLE.md)。

输入与文字/PLAY 分支、Panel 元数据采集、减少空闲唤醒及 WPR 调度采集入口见 [输入与采集说明](INPUT_BRANCHES_AND_CAPTURE.md)。

首次在 Touch Panel 操作 OSK 的步骤见 [从零操作指南](../../HybridSedPrototype_ExtraInfo/docs/panel-first-run.md)；类别 4 过滤、配置适配限制与最新 5600 次测试见 [Panel 与长尾复核](PANEL_AND_TAIL_REVIEW.md)。

运行时 interrupt 异步状态反馈、诊断限频、SED 等待备注及最新 1400 次验证见 [异步反馈测试报告](ASYNC_STATUS_TESTING.md)。OSK 界面输入与回放边界见 [OSK 与 HID 路径核查](OSK_UI_AND_HID_FEEDBACK.md)。

无绑核实测、Service 分段耗时，以及 mock、FAULT、退避与 HID 模式的解释见 [延迟与故障说明](LATENCY_DIAGNOSTICS.md)。

2026-10-23 新增默认关闭的 SED 内部逐事件 trace；早期8,400次无绑核实测与11.52ms等待定位见 [SED 分段计时报告](SED_STAGE_TESTING.md)。

当前默认使用 Windows Event 唤醒发送线程；57项C#测试、三轮对照结果及回退开关见 [发送线程唤醒报告](SENDER_WAKE_TESTING.md)。

**最新功能和操作入口见 [本轮功能补齐](SERVICE_UPGRADE.md)**：已加入运行时控制、状态模式、性能统计、日志轮转、PLAY优先和空闲保活。早期“未迁移”记录仅作历史参考。

新旧功能边界、会话保温、MCU reset 分层和环境要求见 [运行与恢复核对](OPERATIONS_AND_RECOVERY.md)。

哪些旧能力需要补回、心跳对延迟的影响及故障定义见 [功能重新评估](FEATURE_REASSESSMENT.md)；该文明确区分当前实现与待实施建议。

正式链路只包含 C# HybridSED 和 C++ Service 两个进程，无 Agent、第二条管道或额外键盘钩子。

```text
OSK → C# HybridSED（标记匹配且 KeyDown）
              ↓ HybridSedHaptic，单字节 1/2/3
      C++ Windows Service → HID PLAY → MCU
```

Service不依赖Python、hidapi、.NET或原Haptic_Service；HybridSED仍使用其自己的.NET运行时。独立进程隔离硬件连接故障，不需要将C++Service嵌入C#或使用C++/CLI；SED通过P/Invoke调用系统Win32 API完成原生管道I/O。

当前关键线程默认使用AboveNormal，进程保持原级别；启动日志和status可核对实际结果。优先级对照、早期过滤优化与实测边界见[优先级测试报告](PRIORITY_TESTING.md)。恢复普通优先级：C++加`--thread-priority normal`，SED加`--Dispatcher:LatencyThreadPriorityBoost=false`。SCM安装会保存C++的该参数。没有在生产设置CPU绑核、Realtime、全局计时器或电源计划。

## 启动

先启动 C++ 后端，再启动修改后的 HybridSED，最后运行 OSK。关闭示例 HapticPipeConsumer、旧 Python 版及其它 HID 程序，避免占用管道或硬件。

```powershell
cd D:\work\OSK-MCU\HapticServiceforHybridSED\cpp
# 无硬件验证
.\build\HybridHaptic.exe --console --mock --log .\logs\backend.log
# 实际硬件：去掉 --mock
.\build\HybridHaptic.exe --console --waveform 1 --intensity 1
```

上述两个命令二选一。waveform 1..4 对应 TS01..TS04；intensity 1/2/3 对应 Strong/Medium/Weak。另开 PowerShell：

```powershell
cd 'D:\work\OSK-MCU\HybridSedPrototype_ExtraInfo'
$env:DOTNET_ROLL_FORWARD='Major'
dotnet run --project src/SemanticEventDispatcher/SemanticEventDispatcher.csproj
```

本机只有 .NET 10 时使用临时 roll-forward 验证；安装 .NET 8 后不需要该设置。管道在第一条触觉请求时连接，未按键前没有 connected 日志是正常的。Ctrl+C 停止调试程序。

## 安装 SCM 服务

将 EXE 放入稳定安装目录，在管理员终端执行：

```powershell
.\HybridHaptic.exe --install --waveform 1 --intensity 1
sc.exe start HybridSedHapticService
sc.exe query HybridSedHapticService
```

以 LocalSystem 自动启动，日志位于 `%ProgramData%\HybridSedHaptic\service.log`。桌面只需运行 OSK、HybridSED，无需 Agent。`--install --mock` 可用于 SCM 无硬件验证。

```powershell
sc.exe stop HybridSedHapticService
# 等 query 显示 STOPPED
.\HybridHaptic.exe --uninstall
```

`--sed-pipe NAME` 可修改监听管道，两端须一致；`--log FILE` 指定控制台日志。

## 构建与验证

需要 Windows x64、C++20、CMake、Ninja，支持 MSVC/Windows SDK 或 LLVM-MinGW。本机工具链已准备在 LOCALAPPDATA，无需修改 PATH：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Compiler "$env:LOCALAPPDATA\HybridSedCppTools\llvm-mingw-20261006-ucrt-x86_64\bin\clang++.exe"
# 一键构建及 C#/C++/真实进程 mock 联调
powershell -ExecutionPolicy Bypass -File .\validate.ps1
# 加上实际 OSK 程序启动、原生注入 DLL 压测和完整 FAULT 退避测试
powershell -ExecutionPolicy Bypass -File .\validate.ps1 -Extended
```

MSVC 从 x64 开发者终端运行 build.ps1，不传 Compiler。测试脚本使用 Python 标准库，部署 EXE 不依赖 Python。

验证日志在`logs/validation-synthetic`、`logs/validation-osk-dll`；Extended压力结果在`logs/real-pipeline-extended`，最新复查结果见[验证记录](VALIDATION.md)。联调注入无打印字符的F23/F24，使用不存在的测试WMI类验证失败隔离，不注册或修改系统WMI。只终止自己创建的mock进程，不安装服务、不访问物理HID、不烧录MCU。

已验证：KeyUp 不触发、无标记不触发、重复 Down、WMI 失败仍发送触觉、40 次持续输入、接收端重启、未知字节及批量读取；另有 C# 单元测试和 C++ HID 编码/参考字节测试。

## 实现和边界

- HybridSED 仅路由带标记的 KeyDown；语音保留两条边沿。每个语义事件单独捕获异常，WMI 失败不再跳过后续触觉。
- C#管道I/O使用专用后台线程和原生Windows完成事件，队列上限8、50ms过期，失败不重放；语音WMI使用独立有界后台队列，COM调用不阻塞键盘钩子。
- 类别 1=NormalKey、2=ModifierKey、3=SpecialKey；每个有效字节请求一次 PLAY，三类使用同一波形/强度。未知字节不触发。
- 每个重复 KeyDown 均可触发，不判断 touch/typing；分类键集合应避免重叠。未增加 KeyDown 过滤的旧 HybridSED 不可直接配此服务，否则松开也会振动。
- C++ 单线程管理 HID，队列 8 项、接收后 50ms 过期、退避重连、10s验证和可配置空闲保活；PLAY优先。SCM电源和控制台锁屏通知暂停会话，恢复重新预热；控制台调试模式不接收这些SCM通知。
- HID v2报文固定32字节、CRC8，支持auto/feature/interrupt和显式write-only；无回读时标记DEGRADED。PLAY不等待逐条ACK。
- 字节协议没有 VK、源时间、序号，因此 HID key_id=0，age_us 只表示服务接收后的排队时间，不能用于证明端到端延迟或去重。断线边界可能丢请求，不补振。
- 只接收活动物理控制台会话，未支持 RDP/多用户并行；管道 ACL 允许 SYSTEM、管理员、交互用户，拒绝远程连接，不提供同用户恶意进程的认证。
- 当前终端非管理员，尚未实际执行SCM安装/启停。真实I²C/AW振动、回读、物理触摸至振动p95/p99、断电及睡眠恢复仍需目标机验证；同步HidD_GetFeature最坏阻塞时间未实测。日志已加入2MiB及3份历史轮转。

根目录 Python 文件保留为历史参考，不再用于当前正式通路。原 Haptic_Service、Athena_Haptic 本轮未修改。
