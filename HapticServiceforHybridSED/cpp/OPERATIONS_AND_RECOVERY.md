# 会话、恢复与运行环境核对（2026-10-22）

> 当前功能和恢复操作见本文及 [功能补齐](SERVICE_UPGRADE.md)，最新测试证据与边界见 [验证记录](VALIDATION.md)。

本说明基于当前 C++ 源码、旧 Haptic_Service/hid、Athena_Haptic 固件，以及 HybridSED README 和 docs。功能存在、mock 验证通过、实机验证通过是三个不同层次，不互相替代。

## 1. 新旧功能对照

| 功能 | 当前 C++ 版 |
|---|---|
| HID PLAY、TS01..TS04、Strong/Medium/Weak | 已实现，启动参数选择 |
| SESSION_OPEN、预热、SESSION_CLOSE | 已实现，Feature 确认会话和初始配置 |
| 心跳、保持传输活跃、设备重新枚举/重连 | 已实现 |
| 有界队列、过期丢弃、不补振旧事件 | 已实现 |
| SCM 服务入口、电源/锁屏恢复通知 | 已实现，安装启停及系统恢复尚未实测 |
| 原 Windows osk.exe/TabTip 来源检测和启动器 | 不迁移；由 OSK_Prototype + HybridSED 代替 |
| ping、状态查询、动态设置等 | 已提供独立控制管道及CLI，返回JSON；与旧HapticFeedback接口不兼容 |
| interrupt/feature/write-only 模式 | auto优先Feature、不可用时尝试interrupt；write-only需显式选择，不能确认反馈 |
| 能力与队列/延迟统计、连接代次、日志轮转 | 已实现；诊断使用CLI，未迁移旧GUI |
| 自动复位 STM32 整颗 MCU | 未实现，不应当作常规恢复操作 |

因此，新版保留核心执行和会话链路，不是旧 Service 的全部功能替代品。

## 2. SESSION_OPEN 不等于 MCU 热启动

Session 是触觉业务会话：打开后固件预热并保留 AW/播放资源，减少下一次 PLAY 的初始化开销。不是重新启动 STM32，也不是持续振动。SESSION_CLOSE 结束触觉会话、释放预热资源，STM32 主程序仍运行。

当前 C++ 策略：

1. 服务运行且设备可用、未暂停时，打开 HID，发送 SESSION_OPEN。
2. 读取 Feature 0x11，等待 OPEN+WARM；设置并回读波形/强度。
3. HID空闲约250ms才发送轻量HEARTBEAT，可关闭；通常10s通过选定模式验证会话存活，持续输入可延后至20s。两者使用同一HEARTBEAT控制码。已进入驱动的I/O不能被随后到达的PLAY抢占。
4. 停止、挂起或受支持的锁屏通知使会话关闭；恢复时重新打开和预热。设备故障进入重连。
5. Host 异常退出、无法发送 CLOSE 时，固件约在最后一次 OPEN/HEARTBEAT 后 30s 执行 fail-safe 关闭。

这些间隔是调度目标，不保证精确实时。控制台模式不接收 SCM 的电源/会话通知。

**当前即使 OSK 或 HybridSED 没有运行，Service 仍会预热和保活。** 管道断开只重置连接，不关闭硬件会话。不能把服务进程常驻等同于已经实现“OSK 使用期间才保温”。

固件 APP_HapticKeyboardSession_IsWarm 仅在会话打开、预热有效且 runtime 为 KEEPALIVE_WAIT 时返回真。播放过程中 WARM 可能暂时为 0；日常心跳不应要求它一直为 1。本轮已将周期检查改为验证 OPEN，初次打开仍等待 WARM，避免正常播放触发重连。

## 3. MCU reset 的层次

| 操作 | 含义与当前状态 |
|---|---|
| 重新打开 HID 句柄 | Host 通道恢复；C++ 已实现，不重启 STM32 |
| CLOSE/OPEN、重新下发配置 | 恢复业务会话；C++ 已实现 |
| 固件恢复 I²C 外设 | 固件 app_i2chid.c 已有 listen watchdog 和 DeInit/Init 恢复；不等于 MCU 重启 |
| AW 驱动芯片复位/重新初始化 | app_haptic_recovery.c 已有选中马达恢复和有限重试；不等于 STM32 重启 |
| HID-over-I²C 协议 RESET | 当前固件处理传输状态与复位应答；不是 NVIC_SystemReset |
| STM32 NRST/系统复位/断电 | 整机 MCU 重启；新 Service 未提供此操作 |

建议后续按以下顺序设计故障升级，而不是一次超时就 reset：

```text
禁止新 PLAY，丢弃旧队列，记录故障
  → 重开 HID，恢复 SESSION_OPEN 和配置
  → 有限次数重试并退避
  → 持续故障进入 FAULT，收集 Host/MCU 状态
  → 经平台确认后才执行有次数限制的整颗 MCU 复位
  → 重新枚举、预热、回读确认，恢复接收新事件
```

当前已有3/6/12/24/30s退避、连续6次非设备缺失故障进入FAULT、连接代次和重连时清空队列。FAULT需要recover或切换模式解除，session close/open不能绕过。尚需平台设计的是整颗MCU复位的控制权、物理路径、原因记录和复位后的验收。

如果 MCU 整颗失去响应，不能依赖同一条已失效的 I²C 通道可靠地命令它自救；需评估 MCU watchdog 或独立 NRST/EC 控制路径。单次写失败、一次没有逐条 ACK、普通空闲、OSK 关闭、正常锁屏/睡眠均不应直接触发整颗 MCU reset。

## 4. 上游文档实际提供的建议

HybridSED 的 README、architecture、wire-contracts 给出的接入方式是独立 pipe consumer：消费者是服务端，SED 是客户端；管道 HybridSedHaptic，字节模式，1=NormalKey、2=ModifierKey、3=SpecialKey、4=TouchPad 预留，保持已有编号。示例只打印，不操作 MCU。若扩展为其它输出，注册 ISemanticEventPublisher；本轮直接实现现有管道消费者，无需再加输出通道。

它还提醒：hook 要快速返回，管道缺失应丢事件，OSK 标记需要 64 位进程，语音和触觉分别走 WMI 与管道。原文没有给出 STM32/HID 协议、SESSION 生命周期或 MCU reset 策略，这部分来自本地固件和旧 Service。

README/docs 主体包含早期“Down/Up 都发布”“pipe 在 hook 同步写”等描述；当前入口已标注历史基线，以 cpp-haptic-integration.md 和当前源码为准。

**OSK_Prototype 目录和 OSK_Prototype.zip 未发现 README 或部署文档。** 当前只能从发布件确认：net10.0 Windows Desktop、AppConfig.json、Resources/OskWin.dll、LrSendInput64.dll 和 osk.bin。原生 DLL 导入表均包含 VCRUNTIME140.dll，因此部署时还需可用的 x64 VC runtime；具体触摸设备/驱动的完整前置条件尚无原作者说明，需在目标机确认。

## 5. 环境清单

| 部件 | 运行已构建程序 | 开发/验证额外需要 |
|---|---|---|
| OSK_Prototype | Windows x64、.NET 10 Windows Desktop Runtime、x64 VCRUNTIME140.dll 依赖、完整 Assets/Resources/AppConfig | 当前无 OSK 源码，无法重编 |
| HybridSED | Windows x64、.NET 8 Runtime、正确 appsettings.json | 当前完整 solution 含 net10 项目和 .slnx，建议 .NET 10 SDK + .NET 8 Runtime；还需 NuGet 还原 |
| C++ Service | Windows x64、可用的 06CB:CF00 HID/I²C 设备与兼容 v2 固件；mock 不需要设备 | MSVC/Windows SDK 或 LLVM-MinGW，CMake、Ninja |
| 语音分支 | root\wmi 的 VoiceKeyModeSwitchEvent | 若使用示例 listener，再安装 Python、wmi、pywin32；MOF 注册需管理员 |
| 一键 mock 验证 | 不访问真实 MCU、不需要注册 WMI | .NET SDK、C++ 工具链、Python 标准库 |

仅触觉不需要Python语音监听器或MOF注册；但F24仍会尝试WMI并可能记录错误。WMI发布已移至独立有界后台队列，COM调用不再位于键盘钩子线程。

本机已核对安装 .NET SDK 10.0.402 和 Core/Desktop Runtime 10.0.13，尚无原生 .NET 8 runtime；现有验证临时使用 Major roll-forward，不能当作已经验证 .NET 8 原生运行环境。最终目标机建议准备对应 runtime，并保留 runtimeconfig 要求。

## 6. 现场顺序与待设计事项

1. 先运行 C++ `--console --mock`，再启动当前 HybridSED，再从 OSK_Prototype 工作目录启动 OskDemoApp.exe。
2. 确认dispatcher日志Voice key VK=135、Keyboard hook started。需要逐键检查Osk=true时临时启用Debug日志；正式使用默认不逐键打印。Extended测试会启动OSK并调用它实际使用的LrSendInput64.dll，经真实Windows钩子/SED/Service验证；不等于验证原始触摸识别。
3. mock 通过后关闭旧 Service/诊断工具，去掉 --mock，确认实际 HID 枚举、OPEN、WARM 和配置回读，再测试振动。
4. 最后使用管理员终端安装 SCM 服务，并专项测试停止、锁屏、睡眠、MCU 复位与断电恢复。

启动命令和安装步骤见 [README](README.md)。OSK 的 AppConfig 当前为 2880×1800，换屏需检查窗口和触摸板位置。

后续优先补充两套状态：常驻 Service 的连接/故障状态，与 OSK 触觉会话的 ACTIVE/IDLE 状态。建议由明确的启停通知或可配置空闲策略控制预热，而不是仅凭 Service 存活。单字节协议目前只有按键请求，无 OSK 打开/关闭消息，不能精确推断 OSK 可见性。功耗和首键延迟需要目标机一起验证后再决定超时参数。

本轮未增加自动 MCU reset，也未自动修改会话保温策略。
