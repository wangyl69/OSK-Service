# OSK_Prototype → HybridSED → C++ Haptic：从零操作指南

核查日期：2026-10-23。适用于本工作区的发布包与修改后的 SED、独立 C++ Service。
当前机器没有触摸面板和 MCU；本文的触摸验收步骤需在目标硬件执行，尚未实测通过。

## 1. 文档来源与重要区别

已阅读 HybridSED 的 README 及 docs 下 architecture、configuration、wire-contracts、
known-limits、osk-voice-integration、cpp-haptic-integration 六份文档。
本次检查时 OSK_Prototype 没有原作者 README/Docs，只有发布程序、配置、资源和运行日志。
OSK 说明来自其 AppConfig.json、osk.bin，以及 OskDemoApp.dll/BAML 的反编译核查；
不意味着已经取得 OskWin.dll 原始算法源码或触摸硬件适配规范。

旧 SED 文档中两边沿都发触觉、在 hook 线程写管道/WMI、启动示例 Consumer 的内容
是历史基线。当前运行应使用独立 C++ Service，不同时启动 HapticPipeConsumer、
旧 Haptic_Service 或其他占用设备/同名管道的程序。

## 2. 你看到的两个界面是什么

| 界面 | 如何识别 | 用什么操作 | 用途 |
|---|---|---|---|
| MainWindow 键盘窗口 | 大张键盘/触控板图案，按键会高亮 | 兼容触摸面板的真实输入 | 识别触摸并向 Windows 注入键盘事件 |
| ControlWindow 控制面板 | 带 Overlay、FVL、Update、Save、Load、Exit 等文字 | 鼠标或触摸 | 调整识别参数、显示诊断层、回放日志 |

键盘图案是显示层。它还有 osk.bin 中的按键布局、原生触摸识别和高亮反馈；
但没有普通鼠标点击位置转换为键盘键值的托管适配代码。不要用“能点 Update”推断
“能用鼠标点击字母键”。Windows 自带的 osk.exe 也不是此 OSK_Prototype。

## 3. 首先确认硬件和显示条件

1. 在目标电脑的 Windows 设置 → 系统 → 屏幕，记录目标屏幕的分辨率、缩放比例、
   横/竖方向、是否主屏。多屏先确认手指触摸确实落在目标屏幕，而非另一块屏。
2. 先在普通 Windows 应用中验证手指点击、拖动有效。这只能证明 Windows 触摸驱动
   工作，不能证明原型能解析该 Panel 的原始 HID 格式。
3. 查看设备管理器中该面板的硬件 ID、厂商和驱动。原型注册 touchscreen Raw Input，
   附带的诊断代码按 Wacom 类报告的固定字节位置读取 ID、X/Y、容量等字段；
   不能承诺支持所有 HID-compliant touch screen。
4. 保持交付配置先建立基线。若目标机没有 2880×1800 显示模式，不要强制设成不支持
   的模式；记录差异，按后面的映射检查判断是否需要适配。

### 当前不是自动适配任意分辨率的版本

核查到 MainWindow.xaml 的 Width 直接写死为 2880；Height/Left/Top 来自 AppParams。
AppConfig.json 的 AppParams.Width=2880、Height=1800，并不代表改 Width 即可让
主窗口、图片和触摸识别全部一起缩放。WPF 的窗口尺寸是 DIP；DPI 缩放也会影响
实际物理像素占用。100% 缩放是较易排查的测试基线，不是兼容性保证。

另有三个坐标空间必须一致：触摸报告原始坐标、键盘命中区域、显示图像坐标。
本包 Resources/osk.bin 中实际存有：

| 表项 | X 范围 | Y 范围 |
|---|---|---|
| TouchScreen 原始范围 | 0..30182 | 0..18864 |
| KeyboardArea 原始范围 | 1802..28369 | 838..11329 |
| VL 显示布局范围 | 172..2707 | 80..1081 |

这些不是通用 Panel 参数。控制面板的容量/尺寸/速度阈值不会自动重新生成该映射。
若屏幕大小或原始坐标范围不同，需要原始 OSK 工程、匹配的 blob 生成工具/说明，
统一改图像布局、命中区域、原始报告解析和坐标变换；当前目录未提供该完整流程。
不能靠随意改变 TcMin 或把窗口缩小来保证匹配。

这不表示窗口区域不能修改：有可构建工程时可以按目标区域固定宽高，同时统一图片、
布局和原始触摸坐标变换。把目标区域（Left/Top/Width/Height，注明物理像素或逻辑单位）、
屏幕缩放/方向及 Panel 型号一并提供。可先运行同级 C++ 的 build/PanelProbe.exe 保存
元数据；具体命令和需要补充的原始报告资料见 C++ 目录 INPUT_BRANCHES_AND_CAPTURE.md。
已增加 `PanelProbe.exe --capture-seconds 30` 定向采集原始触摸数据；操作顺序见
[原始触摸采集指南](../../HapticServiceforHybridSED/cpp/RAW_TOUCH_AND_LONG_IDLE.md)。

只读检查本包配置与 blob：

```powershell
Set-Location D:\work\OSK-MCU
py -3 .\HapticServiceforHybridSED\cpp\tools\inspect_osk_package.py .\OSK_Prototype
```

该工具不安装驱动、不修改配置，也不自动校准硬件。

## 4. 检查软件环境

在 PowerShell 输入：

```powershell
dotnet --info
dotnet --list-runtimes
```

OSK_Prototype 的 runtimeconfig 指定 .NET 10，并需要 Microsoft.WindowsDesktop.App 10.x。
SED 目标是 .NET 8；示例 HapticPipeConsumer 是 .NET 10，本流程不运行它。
本机使用较新 SDK 构建 .slnx，验证时允许 net8 程序向较新 runtime 前滚；部署时
优先安装对应 .NET 8 runtime。C++ Service 本身不需要 .NET 或 Python。

需要重新构建时，可从工作区执行现有验证入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File D:\work\OSK-MCU\HapticServiceforHybridSED\cpp\validate.ps1
```

它构建并测试软件，不刷写 MCU，也不等价于真实面板/振动验收。

## 5. 按顺序启动，先用 mock 验证软件链路

准备三个 PowerShell 窗口，不要把命令全部粘在同一前台窗口里。

### 窗口一：C++ 接收端

```powershell
Set-Location D:\work\OSK-MCU\HapticServiceforHybridSED\cpp
.\build\HybridHaptic.exe --console --mock --status-mode interrupt
```

保留窗口。mock 是虚拟 MCU，能观察 PLAY，不能产生实际振动。有真实 MCU 时，
去掉 --mock；应出现已确认 WARM，不能拿 mock 的 WARM 证明设备连接成功。
明确使用 --status-mode interrupt 才选用当前异步状态反馈；默认 auto 优先 Feature。

### 窗口二：SED

```powershell
Set-Location 'D:\work\OSK-MCU\HybridSedPrototype_ExtraInfo\src\SemanticEventDispatcher'
dotnet --roll-forward Major .\bin\Debug\net8.0\SemanticEventDispatcher.dll
```

从此目录运行以加载 appsettings.json。前滚参数是本机环境的已验证用法，仍应确认
实际安装的运行时。检查 Keyboard hook thread started、Voice key VK=135、
Haptic sender wake Mode=NativeEvent。不要开启全局逐键 Debug 来做最终性能测量。
SED 必须在 OSK 所在的登录桌面会话中运行；不能直接把键盘 hook 放到 Session 0
就期待它观察用户桌面。

### 窗口三：OSK

```powershell
Set-Location D:\work\OSK-MCU\OSK_Prototype
.\OskDemoApp.exe
```

必须从 OSK_Prototype 启动，程序使用 ./Resources 等相对路径。程序自动初始化，通常
同时出现 MainWindow 和 ControlWindow，无须先点击回放 Play。
在 OSK_Prototype/logs 下新产生的 log.txt 中确认 OSK Initialized:true，并检查是否有
OSK DLL Init failed/AppError。初始化成功只表示程序启动，不代表触摸兼容性通过。

## 6. 在目标 Panel 上逐项验收

1. 打开记事本，用鼠标点击文字输入区使其获得焦点。
2. 如果控制面板遮住键盘，用鼠标拖动控制面板标题栏移开。必要时从任务栏切换窗口。
3. 用手指在键盘区域轻点一个字母，再松开：分别观察键盘高亮、记事本文字和窗口一
   的 MOCK PLAY。这三项是验收观察项，不是串行依赖；SED hook 快速入队并继续放行
   输入后，目标应用处理文字和 Service 处理 PLAY 可以并发推进，谁先显示/完成无固定顺序。
   OSK 高亮也有自己的重绘调度。按下生成触觉，松开不应额外生成一次。
4. 在键盘左、中、右以及不同高度各选几个键，重复测试。触点与高亮应落在同一键，
   不能只测中间一个键就认为整块 Panel 已匹配。
5. 测 Shift、Space、Enter、Backspace。短按/松开与长按分开记录；Key repeat 开启
   时长按可能产生多个 Down，所以也会产生多个触觉请求。
6. 最后再测手掌放置、多指、快速连续输入和触控板区域。不能从单指成功推断这些
   识别模式已通过。当前保留类别 4 不触发键盘触觉。

### 如何判断失败发生在哪一层

| 观察结果 | 下一步 |
|---|---|
| Windows 普通应用也不能触摸 | 先处理面板连接、驱动和系统触摸映射 |
| Windows 能触摸，OSK 没高亮也没 KeyDown 日志 | 查 Raw HID 兼容、初始化和识别，不要先调 Haptic |
| 高亮落在别的键或各处偏差不同 | 查分辨率/DPI、blob 坐标映射及布局，阈值不能修正整体几何问题 |
| 有高亮和 OSK KeyDown，记事本没输入 | 查目标焦点、输入注入、权限层级、是否处于 LogReplay |
| 文字正常但没有 PLAY | 查 SED 标记/Down/类别过滤、管道连接和丢弃统计 |
| MOCK PLAY 正常，真实设备不振 | mock 未覆盖 HID、MCU、驱动芯片和实际振动；切实机检查 |

短时定位时，可以重启 SED 并在命令末尾加 --Logging:LogLevel:Default=Debug，查看
Osk=true、Down=True、分类和路由。定位结束恢复正常日志级别，避免影响性能。

## 7. 配置框应该怎么调

首次匹配先保持交付参数，不要求把每个滑块都改一遍。它是算法调试面板，
没有一个“自动识别我的 Panel 并校准全部参数”的按钮。

| 控件/参数组 | 含义与使用边界 |
|---|---|
| Overlay / FVL / MVL / H_MVL | 显示诊断布局；不是 OSK 开机开关。具体算法层含义缺少原作者说明 |
| Tc / Touch Capacitive | 传感器容量阈值；单位及合适数值依赖实际报告，不能套用到任意面板 |
| Ts / Touch Size | 触点尺寸过滤；不是屏幕宽高 |
| Td / Touch Distance、Tv / Touch Velocity | 识别的距离/速度阈值；不是 DPI 或屏幕定位 |
| Typing / Finger Rest 等时间和阈值 | 输入状态识别；没有硬件日志时不能给出通用最优值 |
| Key repeat | 控制长按重复；不是触摸兼容开关 |
| FR Cariblate（原界面拼写） | 打开原生 finger-rest calibration；不能当作屏幕坐标自动校准，完整动作流程未随包提供 |
| Update | 把当前识别参数应用到原生 DLL |
| Save | 弹出保存 JSON 的对话框；不是自动覆盖启动配置 |
| Load | 弹出选择 JSON，对当前识别参数加载并应用；不重建窗口布局 |
| Replay 区 Load / Play | 加载指定格式的传感器日志并回放，不是启动实时键盘 |

若已确认报告格式和几何匹配，再一次只调整一个参数，点击 Update，记录输入/误触
变化，然后决定保留或恢复。对没有定义单位或算法含义的字段不要凭名称猜数值。

### 已发现的配置持久化限制

启动 AppConfigs.Load(isInit:true) 后会调用 DllParams.SetDefault，多项 Value 被 Default
覆盖。例如 FrIntervalShort 文件 Value=20、启动 Default=35；FrIntervalLong 是 60→200；
TsMin 是 0→10；TsMax 是 100→512。因此仅修改这些字段的 Value 不会改变启动值。
有意修改启动默认值时应备份后核对 Default 和 Value，并以实际控制面板及 GetParams
结果为准；运行中的 Load 路径不会调用同一个 SetDefault。

保存代码只给新配置对象赋 DllParams，其 AppParams 会取模型默认值；运行中的 Load
也只读 DllParams。故 Save 文件不能视为完整的 Panel 几何配置备份，不能直接用它
覆盖已经手工调整过的主 AppConfig.json。推荐首次运行前复制整个 OSK_Prototype 目录作为
备份；运行中另存的 JSON 用于 Load 识别参数。原作者工程未提供，未修改该二进制。

LogReplay 会重放识别和高亮，但跳过向 Windows 注入 KeyDown/Up，因此一般的回放
Play 不会验证完整 SED→Service 链路。启动 OSK 不会自动进入 LogReplay。

## 8. Haptic 的类别与维护命令

SED 标记匹配且 KeyDown 是上游前提；发布器只发送类别 1/2/3，C++ 再次只接受
1/2/3。它们是 NormalKey/ModifierKey/SpecialKey 的类别，不是虚拟键值 1/2/3；
4 是保留 TouchPad，不启用。Service 不能从单字节重新验证标记、Down 或原始键值。

从另一个 PowerShell 查询（运行中的窗口一不要关闭）：

```powershell
Set-Location D:\work\OSK-MCU\HapticServiceforHybridSED\cpp
.\build\HybridHaptic.exe --control status
.\build\HybridHaptic.exe --control diagnose
```

status 返回缓存；diagnose 是用户/程序明确要求刷新 MCU 状态。最多每秒一次是
限频上限，不是系统必须每秒查询。未请求时不会凭空每秒诊断。

独立的周期健康验证：约 10 秒且空闲时验证，会话持续输入时约 20 秒强制开始一次，
避免永远不验证；这里不是额外每 10/20ms 查询。interrupt 模式异步等状态响应，
PLAY 可继续；超时 250ms 进入恢复。Feature 路径仍同步，配置变更也仍可等待确认。
空闲轻量 keepalive 默认 250ms，只发无逐次 ACK 的心跳；它与完整状态读取不同。

恢复时清理旧输入，3/6/12/24/30 秒退避重试，等待不占 CPU 忙转；恢复确认会话和
波形/强度后才处理新输入。持续缺少设备继续受限重试，其他连续失败 6 次进入 FAULT；
显式 recover 或模式变更可重新尝试，Session Close/Open 不绕过 FAULT。
这些控制不要求每批输入重新开会话。已知故障时放弃触觉是避免播放积压旧反馈，
不是追求低延迟而假装设备可用。恢复流程不是每次都硬复位 MCU。

异步等待消除线程坐等响应；维护 Output 请求、设备反馈的真实 I2C 事务、配置操作
及 Windows 调度仍可能延迟 PLAY。不要把 mock 测得的软件延迟当成实际振动延迟。

## 9. 正常退出

先停止输入，再用 OSK 控制面板 Exit 退出；SED 的 PowerShell 窗口按 Ctrl+C；
最后让前台 C++ Service Ctrl+C 正常退出。正式 SCM 后台服务用服务控制命令停止。
正常退出会尝试关闭设备会话；强制结束或主机故障需要 MCU 会话超时兜底。

完整异步与长尾验证另见同级 C++ 目录 ASYNC_STATUS_TESTING.md 和
PANEL_AND_TAIL_REVIEW.md。文档步骤可复现，但未声称当前无硬件机器已经完成触摸验收。
