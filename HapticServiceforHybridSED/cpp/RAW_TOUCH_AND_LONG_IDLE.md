# 原始触摸采集与长空闲验证（2026-10-23）

## 收集目标 Panel 的实际数据

PanelProbe 默认只输出设备与显示元数据。新增显式 --capture-seconds 参数可额外
采集 touchscreen Raw Input，仍不向设备发送 Output/Feature 命令。无需先启动 OSK。
默认不记录普通键盘、鼠标或触控板输入；注册 usage page 13、usage 4，并复核实际
数据来源集合。现新增 --capture-source touchpad，可改为注册 usage 5 单独采触控板。
结果是普通 JSON 文件，不需要安装 Python 或 .NET。

在连接目标 Panel 的电脑运行：

```powershell
Set-Location D:\work\OSK-MCU\HapticServiceforHybridSED\cpp
.\build\PanelProbe.exe --capture-seconds 30 | Out-File .\panel-touch.json -Encoding utf8
```

命令开始后，在目标屏幕依次做几个短动作，动作之间松手并间隔约一秒：

1. 单指在中心按下、短距离移动、松开。
2. 在已知位置依次轻点：左上、右上、中心、左下、右下，记录所用位置。
3. 双指按下、移动、松开。

可将单指、定位、双指分成三个单独的 30 秒文件，便于明确每段数据对应的动作。
时间范围 1..120 秒；最多保留 2000 条报告，达到上限会提前结束并标记
report_limit_reached。不要将本工具同时挂在最终 latency 基准上：采集本身有开销。

提供 JSON、Panel 型号和驱动版本、Windows 缩放/方向、目标窗口区域，即可先做
报告 ID/长度、原始字段与坐标范围的对照。厂商原始 HID Report Descriptor/协议说明
仍有价值；能力数组不是完整 descriptor，不能仅靠元数据断言兼容。

### 如何读采集结果

touch_capture 内包含：

* registered/unregistered：Windows 注册与注销调用是否成功。
* sample_count、read_errors、ignored_messages：实际记录数和异常/忽略计数。
* reports：每条包含 VID/PID、运行内的设备索引、原始字节 hex、报告长度及 host_qpc。
* timestamp_scope：时间戳是 Host 收到消息时刻，不是触摸传感器采样或总线时刻。

注册成功而 sample_count=0，表示没有观察到符合条件的报告，不是兼容性通过。
多条报告处于同一个 WM_INPUT 时保留各自边界，共用该消息接收时间。
程序不对未知格式擅自猜测 X/Y 字节或将数据重新注入 OSK。

### 触控板也可以单独尝试采集

```powershell
.\build\PanelProbe.exe --capture-seconds 30 --capture-source touchpad | Out-File .\touchpad-raw.json -Encoding utf8
```

在触控板上做单指移动、抬起、双指操作。它采的是触控板自身触点坐标，不是鼠标
光标坐标；不能直接代替触摸屏坐标或验证 OSK 图片上的按键命中。旧式只报告鼠标
事件的触控板不提供这类 PTP 原始触点。即使有 PTP 集合，也需驱动/Windows 实际
向该进程交付 Raw Input；注册成功且零样本不能断言兼容或不兼容。
本机已尝试 3 秒 touchpad 采集，注册/注销成功，样本 0、读错误 0；没有声称实际
手势采集已通过。记录见 logs/touchpad-capture-smoke-20261023.json。
采集工具的 source 选择不改变 SED 或 HapticService，类别 4 仍然不触发键盘触觉。

## 本机验证范围

本机没有 touchscreen。已运行 --capture-seconds 2：注册/注销成功，持续约 2000ms，
sample_count=0、read_errors=0；保存 logs/panel-capture-smoke-20261023.json。
这验证无输入时的消息循环及退出，不代表真实硬件采集已经验证。

新增 panel_raw_input 原生测试检查多报告边界、截断、非 HID/鼠标排除、零长度及
异常长度；与 core、windows_io、firmware_contract 共 4 项 C++ 测试通过。
普通元数据模式仍兼容原命令；没有修改生产 Service、SED、原 OSK 或固件。

## 更长输入空闲后的首键

新增 tests/check_long_idle.py，跳过重复的吞吐基准，只测：

* 10 次，每次相隔约 5 秒输入空闲。
* 6 次，每次相隔约 15 秒输入空闲。

```powershell
py -3 .\tests\check_long_idle.py
```

使用真实 OSK DLL、Windows hook、SED、Named Pipe 和默认 fixed 模式 C++ Service，
HID 仍为 mock；interrupt 状态 ACK 故意延后 200ms。检查零丢弃、generation=1、
周期验证仍发生且会话保持 ready。不是休眠唤醒测试，也不是 MCU 冷启动测试。

修正了旧首键测试的准备顺序：先完成统计重置/状态查询，再等待完整空闲区间，
再注入第一键。旧实现先等待再做准备，可能预热批次第一个样本；旧数据仍保留，
但不能作为完全同条件的首键基线。多次按键之间不发送诊断/状态查询，正常生产
心跳与健康验证继续运行。最终结果为 logs/long-idle-summary-20261023.json。

样本很少，P99 接近最大值；本轮用于覆盖维护周期与空闲恢复行为，不能估算罕见
长尾概率。内核 ETL 仍受当前权限限制，不会把应用分段数据当作内核调度根因证据。

### 本轮实测结果

| 输入空闲间隔 | 样本数 | DLL→mock 完成中位数/最大 | SED 发布→出队最大 | Service 提交→mock 完成最大 |
|---|---:|---:|---:|---:|
| 5 秒 | 10 | 0.243 / 0.379ms | 0.089ms | 0.050ms |
| 15 秒 | 6 | 0.420 / 0.501ms | 0.192ms | 0.062ms |

两组均零丢弃、无多余松开 PLAY，generation=1；周期验证分别发生 5/10 次，会话始终
保持可用。只表明这些采样中未出现 >1ms，不代表罕见长尾已经消除。没有改生产默认
等待方式或绑核，也没有把首键测量称作振动起始延迟。两组原始数据分别保存于
logs/real-pipeline-long-idle-5000ms-20261023 和 logs/real-pipeline-long-idle-15000ms-20261023。
