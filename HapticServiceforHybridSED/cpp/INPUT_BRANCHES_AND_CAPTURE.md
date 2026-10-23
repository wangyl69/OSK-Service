# 输入分支、目标 Panel 数据与调度采集（2026-10-23）

## 类别过滤测试的意思

故意向 SED 发布器输入 [TouchPad=4, SpecialKey=3]，预期管道输出 [3]。
这表示 4 被忽略、3 正常发送，不是把 4 转换成 3，也不是优先级重排。
1/2/3/4 是语义类别而非键盘虚拟键值。正常键盘输入不会先发一个 4。

## 文字、触觉与高亮

OSK 识别回调在正常模式下调用 LrSendInput 的 KeyDown。Windows 低级键盘 hook
发生在输入即将送入目标线程队列之前；SED 在 hook 中快速入队、随后 CallNextHookEx，
专用发送线程独立发送触觉请求。目标应用的消息处理与绘制不属于触觉调用链。

```text
OSK 识别 → 注入 KeyDown → SED hook
                            ├─ 快速入队 → 发送线程 → Pipe → Service → PLAY
                            └─ 继续放行 → 目标应用消息处理 → 文字绘制

OSK 自身另行更新按键高亮并安排重绘
```

两条输出有公共输入前置处理，但不互相等待完成。文字可先显示，也可 PLAY 先完成。
mock PLAY 完成更不能等同于电机已起振。原指南的三项观察不是时间顺序，已修正文案。

## 能否直接改成需要的窗口大小

可以在可构建的 OSK 工程中固定目标窗口区域。当前 Width=2880 是 XAML 固定值，
Height 来自当前 JSON 值 1800，两个维度的设置路径并不相同。只改窗口可能改变
图片缩放或裁切，却不改变 osk.bin 中原始触摸到按键的映射；因此还必须统一
图像、布局、命中、触摸原始坐标、屏幕方向与 DPI 的变换。

请给出目标区域的坐标体系，例如“目标屏幕物理像素中 Left=0、Top=400、
Width=1920、Height=800，Windows 缩放 125%”。多屏还需说明在哪一块屏幕。
当前只有发布包及反编译结果，不能把改 JSON 当作完整源码重建和 Panel 适配。

## 需要提供哪些 Panel 数据

先在目标电脑 PowerShell 执行：

```powershell
Set-Location D:\work\OSK-MCU\HapticServiceforHybridSED\cpp
.\build\PanelProbe.exe | Out-File .\panel-info.json -Encoding utf8
```

PanelProbe 是本轮新增的只读工具，不需要 Python/.NET，不改设备设置，不读写触觉
命令，不收集序列号，默认不采集实时输入。它枚举显示模式、digitizer 顶层集合、VID/PID、
报告 ID/长度、字段 Usage、X/Y 逻辑范围、字段位宽与 Contact ID 等能力信息。
Usage page 13 下 usage 4 是 touchscreen、5 是 touchpad；不能把触控板当作触摸屏。
无可访问的 touchscreen 集合时不能宣称没有任何触摸硬件，需结合枚举失败计数和驱动状态。

请先提供：

1. panel-info.json；Panel 型号/厂商及驱动版本，连接方式（I2C-HID/USB-HID 等）。
2. Windows 显示设置中的缩放比例、方向、目标屏幕；分辨率已尽可能由工具采集。
3. 希望 OSK 占据的 Left/Top/Width/Height，以及这些数值是物理像素还是逻辑单位。

这足以做第一轮几何和报告能力对照，但不能保证字节布局完全一致。进一步确认原生
解析兼容，需要厂商的 HID Report Descriptor/协议说明和少量真实原始报告样本：
单指按下/移动/松开、双指，以及屏幕已知位置（至少角部和中心）。能力数组不是完整
原始 descriptor。现可通过 --capture-seconds 显式启用定向 touchscreen Raw Input 捕获，
命令和动作顺序见 [原始触摸采集与长空闲验证](RAW_TOUCH_AND_LONG_IDLE.md)，不需要先盲目调滑块。

本机实际采集结果：显示模式 3072×1920，枚举到 digitizer touchpad/配置集合，
没有在可访问集合中发现 usage=4 的 touchscreen。数据保存在 logs/panel-probe-20261023.json。
这验证工具能读取本机元数据，不是目标 Panel 兼容性测试。

## 本轮实际优化

Service 原本即使没有 ACK/工作，也每 10ms 定时唤醒。新增可选 --idle-wait deadline，
由 idleWaitMs 计算下一次 keepalive、配置、诊断限频或健康验证期限，无待响应时等到最近期限。
生产者在同一互斥锁下入队并通知条件变量，所以新 PLAY 立即唤醒等待，不需要等
维护计时器到期。待 ACK 时仍以 10ms 上限检查完成，且新输入同样可以提前唤醒。
不改 CPU affinity、优先级、电源计划、系统计时器或固件。

默认仍为 --idle-wait fixed，因为更长的空闲等待也可能改变 CPU 空闲状态和首键唤醒表现，
需要对照验证；不能仅凭减少后台唤醒就宣布更适合低延迟。
可选改动减少无必要的空闲唤醒；不声称自动减少每个按键的延迟，也没有改变既有
会话故障保护。原生 Output、同步 Feature/配置确认和 Windows 调度仍有影响。

## 可复用的内核调度采集

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Capture-Scheduling.ps1 -LoadWorkers 0 -IdleWakeCount 40
```

tools/Scheduling.wprp 记录 ProcessThread/Loader、CSwitch、ReadyThread、DPC、Interrupt，
并为 CSwitch/ReadyThread 采栈。已由本机 WPR 解析确认配置有效。
脚本创建唯一实例名，只有成功启动自己实例后才停止/必要时取消该实例，不动其他录制。
同时保存 UTC、QPC 时间、频率、SED/Service 启动日志里的线程 ID、软件逐事件时序。
WPR 发到 stderr 的提示按退出码处理，不再把正常的模式提示误判为失败。

当前用户无 SeSystemProfilePrivilege，命名会话启动报告 Access is denied，0x80070005；
因此此次未获取内核 ETL。没有修改策略或自动提权。脚本按设计退回应用分段计时，
metadata 中 trace_started=false，不会把该结果说成内核调度实测。
可采集环境运行同一脚本时，应在结果目录得到 scheduler.etl；在 WPA 用线程 ID 和
事件时刻检查等待原因、Ready 延迟、抢占及 DPC/ISR。采集有额外开销，须与无追踪
性能数据分开判断。加 -RequireKernelTrace 可要求启动失败就停止，不继续软件测试。

初轮 deadline 复测包括原有 1400 次与附加 40 次“空闲 1 秒后的首键”，同一会话，
1440 次零丢弃，P99=1.250ms、最大=3.541ms，>5ms 为 0；并未证明其延迟优于 fixed。
原始记录：logs/real-pipeline-schedule-20261023-143306-d9a06304。
随后按相同参数运行 fixed/deadline A/B，各 1420 次（各含 20 次空闲后首键），
入口 tests/check_idle_wait_comparison.py，汇总 logs/idle-wait-comparison-20261023.json。

| 模式 | 全体 P99 | 全体最大 | 空闲后首键最大 |
|---|---:|---:|---:|
| fixed（保留默认） | 1.300ms | 3.942ms | 0.611ms |
| deadline（可选） | 1.228ms | 4.939ms | 0.981ms |

两组均零丢弃。deadline 未展示一致的低延迟优势，因此没有将其设为默认；
这不等于单轮已证明它在所有机器都更差。当前默认生产调度仍是原来的 fixed，
新诊断工具和可选模式不增加逐键日志或更改 CPU 绑核。
本轮合计新增 4280 次，其中 80 次空闲一秒后的首键；没有观测到 >5ms，
但有限、相关的样本不能构成 Windows 调度上限保证。
真实硬件、长时间系统休眠唤醒与电机起振仍未覆盖。
57 项 C#、3 项 C++ 以及状态/生命周期/标记边沿集成验证通过。
