# OSK_Prototype 无输入：触摸与坐标采集

本工具只读采集，不修改驱动、屏幕设置、OSK 配置或 MCU 固件。目标是区分 Windows 触摸、Raw HID 报告、坐标映射和 OSK 按键注入的问题。普通用户运行即可；设备信息可能因权限或驱动限制不完整。

## 在 Deployment 目标机运行

1. 退出 OSK_Prototype、Touch Keyboard 测试启动器和其他触摸测试程序。可运行 `Stop-OSK.bat` 停止本套程序。
2. 在 Deployment 仓库根目录执行 `git pull --ff-only`，取得新的诊断工具；不刷 MCU，不修改原有屏幕缩放。
3. 双击根目录 `Collect-PanelInput.bat`。
4. 工具准备元数据后停在 `STEP 1`。确认 OSK 已退出，按回车开始第一轮。
5. 主屏出现 30 秒全屏测试界面，红色目标依次移动到左上、右上、中心、左下、右下，每处约 6 秒。用手指在每个目标上点几次、短距离移动后松开。目标会自行移动，不依赖触摸是否成功。Esc 可取消；取消后的部分数据也保留。
6. 询问 `Did normal Windows touch work...` 时，输入 `yes`、`no`、`offset`（位置偏差）或 `unknown`，回车。
7. 工具停在 `STEP 2`。保持采集窗口不关，另行运行 `Start-OSK.bat`，打开记事本并露出 A/S/D。回到采集窗口按回车。
8. 接下来的 20 秒，切到记事本，用鼠标点击编辑区，然后手指点 OSK 的 A/S/D，每次间隔约 1 秒。不要点击日志回放的 Play。
9. 回到采集窗口，输入观察结果，例如 `no highlight; no text`，回车完成打包。
10. 将屏幕显示的 `logs\panel-input-时间-随机号.zip` 发回。保留 ZIP 中全部文件；文件可能包含设备标识和目录路径。

第一轮固定测试主屏。多屏机器先确认待测 Panel 是 Windows 设置中的主显示器；若不是，先不要更改系统映射，说明实际屏幕情况后再决定采集方式。第二轮若没有检测到 OskDemoApp，会明确记录跳过，不当成输入成功。

开发仓库也可运行 `tools\Collect-PanelInput.bat`，默认使用根目录的 OSK_Prototype 和同级 build/PanelProbe.exe。`-OskDirectory`、`-ProbePath`、`-OutputRoot` 支持显式路径；`-Mode CollectOnly` 只收集已有日志和元数据，不打开五点界面、不采集实时触摸。

## 包含哪些证据

- HID 设备及相关父设备的服务、枚举器、硬件 ID、驱动版本。可用于判断系统报告的是不是 hidi2c/I2C 路径；不等价于抓到了总线电气波形。PnP 查询超过 30 秒保留部分结果并继续，不把超时当成硬件故障。
- 显示器物理分辨率、方向和位置，以及 OSK/记事本的窗口物理矩形与 DPI。
- 第一轮的五个已知屏幕物理坐标、触摸的 Windows PT_TOUCH 指针坐标和 Raw HID 原始报告。每条记录带主机接收时间和当时目标编号。
- 按系统提供的 HID 预解析描述数据读取标量 X/Y、Contact ID 等字段，以及 Tip Switch 等按钮状态。保留 usage page、usage、link collection 和解析状态。逻辑坐标不直接当成物理像素，不预设 X/Y 字节偏移；值数组和异常格式会记录无法解析/跳过，原始十六进制报告仍保留。
- 第二轮原始触摸、当时的前台窗口句柄，以及最新 OSK 会话的 log.txt/debug.txt。
- OSK 配置与 osk.bin 副本，以及 EXE/DLL/config/blob 的 SHA256，便于与同事已验证的包对比。采集脚本不更改这些文件。
- evidence-hashes.json 记录打包前采集文件的散列。

## 能判断与不能保证的事项

| 结果 | 可以继续判断 |
|---|---|
| Windows PT_TOUCH 指针与 Raw HID 都有数据 | 检查五点坐标、设备来源及 OSK 接入 |
| Windows 有 PT_TOUCH，Raw HID 为零 | 系统触摸输入存在，但此 Raw Input 采集路径未收到报告；不能说 Panel 没有触摸 |
| 两者都为零 | 核对所测屏幕、设备状态、驱动和采集限制；仍不能只凭零样本判定硬件坏 |
| Raw HID 有数据，描述解析失败 | 保留格式证据，进一步要原始 Report Descriptor/厂商说明 |
| 五点坐标与屏幕不匹配，或 OSK 高亮错位 | 检查方向、屏幕映射、缩放和 osk.bin 布局 |
| OSK 有 KeyDown 日志但记事本无字符 | 检查焦点、权限、回放模式与输入注入 |

每条时间戳都是 Windows/主机观测时间，不是 Panel 采样时间。最多保留 10000 条 Raw HID 和 10000 条 Windows 触摸指针事件，触及 Raw HID 上限会提前结束并显式标记。

没有 OskWin.dll 算法源码仍能分析报告、坐标与日志，提出适配方向；无法承诺直接修改闭源识别算法。仅靠坐标范围相同不能证明按键映射正确，需要结合已知五点位置和实际 OSK 日志。

解析依据：微软 [HidP_GetUsageValue](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidpi/nf-hidpi-hidp_getusagevalue) 和 [HID 预解析数据说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/preparsed-data)。

## 验证边界

本地完成原生编译、Raw HID 边界/有符号值测试、无界面采集与 ZIP/JSON 检查；本机无目标触摸 Panel，五点触摸、实际坐标解析与 OSK 输入仍需目标机采集验证。不会将零样本的本地检查描述成硬件测试通过。

本次只新增诊断入口：OSK、SED、HybridService 和 Python 键盘生产程序不变，MCU/CubeIDE/IAR/firmware_mcu、源码交付包及 Japan Demo 均无需更新或重刷。Deployment Git 版包含工具；旧 Release ZIP 不会自动包含此次诊断更新。
