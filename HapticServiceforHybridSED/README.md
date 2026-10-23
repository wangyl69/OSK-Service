# HapticServiceforHybridSED

当前推荐并验证的实现是 [C++ Windows Service](cpp/README.md)。

链路：OSK → C# HybridSED（OSK 标记且 KeyDown）→ HybridSedHaptic → C++ Service → HID PLAY → MCU。

不再运行额外 Agent，不把 C++ DLL 嵌入 C#，也不关联两套键盘事件。C++ 实现位于 cpp/，包含 EXE、SCM 入口、构建和验证脚本。

根目录 Python 文件是先前“不改 HybridSED”约束下的历史兼容原型，依赖 Down/Up 关联，**不适用于当前已过滤 KeyUp 的正式通路**。保留供参考，不要与新版 C++ 后端同时启动。
