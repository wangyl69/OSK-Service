# Athena Touch 链路测试小应用

用途：绕开尚未适配 Athena Panel 的 OSK_Prototype，通过 Windows 标准触摸事件验证
`TouchHapticTest → 带 OSK 标记的 Windows 键盘输入 → HybridSED → HybridHaptic → I²C HID → MCU → Driver IC → LRA`。
小应用不解析厂商 Raw HID、不直接写 SED 管道，也不直接访问振动设备；因此不会绕过待验证的 SED。

## 在另一台机器运行

保留当前已验证的 MCU 固件。先正常退出 Python Touch Keyboard/传统 OSK/自绘诊断键盘的触觉启动器，运行 Deployment 的 `Stop-OSK.bat` 退出旧的 native 链路。若安装的 SCM 服务正在运行，脚本会提示先停止它；本工具需要在登录用户桌面运行。

在已下载的 Deployment/debug 仓库根目录打开 PowerShell：

```powershell
git pull --ff-only origin debug
.\Start-TouchTest.bat
```

首次下载则执行：

```powershell
git clone --branch debug https://github.com/wangyl69/OSK-Haptic-Deployment.git
cd OSK-Haptic-Deployment
.\Start-TouchTest.bat
```

启动器先启动 HybridHaptic 和 HybridSED，并等待键盘钩子及 SED 管道预连接成功，再打开小应用。预连接只建立连接，不发送虚假播放事件。诊断小应用为 Windows x64 原生静态程序，无需目标机编译或另装 Python/.NET Desktop Runtime；同包 SED 使用已有自带运行时。原 OSK_Prototype 的运行库要求不变。

窗口可拖动、缩放和关闭，按屏幕 DPI 缩放，不会固定占满整个 Panel。默认蓝色区域只接收真实 Windows Touch；鼠标和笔不触发。每个触点只在按下时发一对带原有 OSK 标记的 KeyDown/KeyUp，移动、抬起不额外发播放。A/S/D 分区决定发送哪个字符；长按不自动重复，双指按下会分别产生请求。

## 推荐验证顺序

1. 确认标题下显示“真实 Touch / WM_POINTER”，不是“鼠标诊断模式”。默认真实 HID；启动时若设备未 WARM，脚本明确警告，不能把窗口打开当成硬件就绪。
2. 用手指依次点击蓝色区域的 A、S、D，每次间隔 1 秒。第一轮不要连按：现有 170ms 波形及 MCU 50ms 过期策略没有改变。
3. 检查“按下”计数是否 1、2、3 增加。
4. 检查“Windows注入成功”同步增加、失败为 0，下方收到的标记键盘输入回显 ASD。回显来自实际收到的标记键盘 Down，不是直接把触摸位置写成字母。本诊断窗口局部禁用 IME 组合输入，避免候选词吞掉 A/S/D；不改变系统输入法设置。此步骤无需另外打开记事本。
5. 检查 LRA 是否每次按下实际振动。强度沿用 Start-OSK 的中档 2；播放波形选择为 0，跟随 MCU 当前全局默认，不会强制切为 TS01 或 6db。串口 s 可确认选中的波形、HID 收包与播放计数。
6. 使用 `Stop-OSK.bat` 结束整套链路。仅关闭小应用窗口会保留已启动的后端，便于检查日志；完整退出使用停止脚本。

## 结果判读

| 表现 | 后续定位 |
|---|---|
| 手指点击，“按下”一直为 0 | Windows 未向此窗口交付预期 Touch；先试下方 LegacyTouch，再查 Panel 驱动/触摸映射 |
| “按下”增加，但注入失败或不回显 | 检查日志中的 focus_failed、modifier_held、send_input 和实际收到的键值；松开 Ctrl/Alt/Shift，并保持窗口在前台 |
| 字符回显正常，但不振动 | 输入前端已收到并注入；对照 SED、HybridHaptic、MCU 的接收和播放计数定位 |
| 点击和振动都正常 | 本次标准 Touch → SED → native service → 实际硬件链路通过，不等价于 OSK_Prototype 厂商识别算法适配完成 |

原 OSK_Prototype 不要同时运行。输入与 SED 钩子需处于同一用户桌面及兼容权限级别，正常使用不要求管理员。应用只向自身已获得焦点的窗口注入 A/S/D，切出本窗口后不触发测试。

如果默认模式计数为 0，先停止，再尝试标准 Windows 旧式 Touch 消息入口：

```powershell
.\Stop-OSK.bat
.\Start-TouchTest.bat -LegacyTouch
```

LegacyTouch 仍是实际触摸，不是鼠标模拟。若两种模式都没有触摸计数，工具不能替代缺失或异常的 Windows 触摸驱动。

仅检查软件且没有 MCU 时，可显式使用鼠标 + Mock：

```powershell
.\Stop-OSK.bat
.\Start-TouchTest.bat -Mock -MouseTest
```

此时黄色区域明确标为鼠标诊断，`MOCK PLAY` 只证明软件接收，不证明 Touch、I²C HID 或 LRA。切换触摸/鼠标/Mock 模式前先停止旧实例，脚本会拒绝复用不匹配模式。

## 保存日志

将 Deployment/logs 下本次生成的以下文件打包提供，并注明是否实际振动：

- `touch-test-*.jsonl`：模式、真实 Touch/鼠标来源、触点/坐标、注入结果、实际标记键盘接收与退出统计。
- `sed-*.log`、对应 `sed-*.err.log`：VK、Osk 标记、分类/路由和管道发送统计。此入口启用输入 Debug，正式性能测试应另行关闭调试日志。
- `haptic.log`：设备状态和 native 后端日志；需要补充状态时可执行 `Apps\Haptic\HybridHaptic.exe --control status` 保存输出。
- `launcher-state.json`（停止后会删除，运行期间可复制），以及 MCU 完整串口日志。

“注入成功”不代表 SED 已发送或 LRA 已振动。SED 当前向 native 服务发送语义类别，native 日志中的 key=0 不表示丢失了 A/S/D；对应原始 VK 可在前端和 SED 日志中查看。

## 源码、构建与同步范围

- GUI/输入门控：OSK-Service 仓库 `HapticServiceforHybridSED/cpp/tools/touch_test.cpp`、`touch_test_input.hpp`。
- native 测试：`tests/touch_test_tests.cpp`、`tests/check_touch_frontend.py`。
- SED 可选预连接：`HybridSedPrototype_ExtraInfo/src/SemanticEventDispatcher/Configuration/DispatcherOptions.cs` 和 `Publishers/HapticPipePublisher.cs`。默认关闭，仅该测试入口显式开启；原键盘分类、按下过滤、单字节协议和每事件超时策略不变，不补发过期事件。
- Deployment：`Apps/TouchTest/TouchHapticTest.exe`、`Apps/SED/SemanticEventDispatcher.dll`、`Start-TouchTest.bat`、共用 `Start-OSK.ps1`/`Stop-OSK.ps1`。`Apps/TouchTest/build.json` 记录源码提交和校验值。
- 开发机从 cpp 目录执行 `cmake --build build --target TouchHapticTest TouchHapticTests`，运行 `ctest --test-dir build -R touch_test_input --output-on-failure`。

此次不修改 OSK_Prototype 发布程序、Python 三种键盘、HybridHaptic 实现、MCU/CubeIDE/IAR、firmware_mcu、MCU 源码交付压缩包或 Japan Demo，也不新增默认桌面快捷方式。使用已更新 Deployment 的用户无需单独拉取开发源码或重刷 MCU。旧 Release ZIP 不包含本次新入口，应更新 Git 版本或完整复制本次 Deployment 文件。

本地已验证 59 项 SED 测试、触摸门控/标记输入测试、实际 SED + native service + Mock 的 3/3 播放、无标记输入零额外播放、启动预连接零额外播放、GUI 鼠标诊断 ASD 回显以及默认 Touch 模式忽略鼠标。尚无目标 Athena Panel/MCU，实际 Touch、I²C HID、Driver IC 和 LRA 需目标机验收。
