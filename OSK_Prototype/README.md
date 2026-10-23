# OSK_Prototype 发布包操作入口

本文件是 2026-10-23 基于当前发布包核查后补充的说明，不是原作者随包提供的手册。
从零启动、Touch Panel 匹配、分辨率/DPI、配置框、日志回放、SED 和 C++ Haptic
操作步骤见 [完整操作指南](../HybridSedPrototype_ExtraInfo/docs/panel-first-run.md)。

核心边界：MainWindow 键盘图案使用原生触摸识别；ControlWindow 是可鼠标操作的
调试面板。当前不支持任意触摸屏自动适配，也没有普通鼠标点击键盘图案的适配路径。
不要仅修改 AppConfig.json 的 Width/Value 就认为几何和识别参数都已生效；指南列出
了固定窗口宽度、blob 坐标表、启动 Default 覆盖 Value，以及 Save/Load 的限制。

从本目录启动 `OskDemoApp.exe`。需要 .NET 10 Windows Desktop Runtime。
本机没有 Touch Panel 和 MCU；已做软件链路测试，真实触摸和振动仍需目标机验收。
