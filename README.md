# OSK-Service

OSK → HybridSED → 独立 Haptic Service 集成工程。

| 目录 | 内容 |
|---|---|
| [OSK_Prototype](OSK_Prototype/README.md) | OSK 发布程序、原生 DLL、布局及资源；当前不包含原始 WPF 源码 |
| [HybridSedPrototype_ExtraInfo](HybridSedPrototype_ExtraInfo/README.md) | C#/.NET HybridSED 源码、配置、测试和文档 |
| [HapticServiceforHybridSED](HapticServiceforHybridSED/README.md) | 独立 Python/C++ 触觉服务；实际使用以 cpp 目录为准 |

Service 的实际目录名是 `HapticServiceforHybridSED`，本文沿用该名称。

## 构建与运行入口

1. 阅读 [Panel 与 OSK 首次操作指南](HybridSedPrototype_ExtraInfo/docs/panel-first-run.md)。OSK 发布件需要 .NET 10 Windows Desktop Runtime 和原生 DLL 所需的 x64 VC Runtime；触摸兼容性需在目标 Panel 验证。
2. 使用 .NET 8 SDK 构建 HybridSED。完整解决方案中的部分 Consumer 项目需要 .NET 10 SDK，可使用 .NET 10 SDK 执行 `dotnet build HybridSedPrototype_ExtraInfo/HybridSedPrototype.slnx`。
3. 使用 Windows x64 C++ 工具链、CMake 和 Ninja，参照 [C++ Service 指南](HapticServiceforHybridSED/cpp/README.md) 构建。先运行 Service，再启动 SED，最后启动 OSK。
4. 没有 MCU 时使用 C++ Service 的 `--console --mock` 验证软件链路。mock 不代表 HID/I²C 或实际振动测试通过。

本仓库不包含 Athena_Haptic 固件或旧版 Haptic_Service。C++ 固件契约和休眠握手测试仅在相邻位置存在兼容的 Athena_Haptic 头文件时启用；独立下载本仓库可以构建普通 Host 测试。

2026-10-23 的 [Host 优化与完整复测报告](HapticServiceforHybridSED/cpp/WAIT_OPTIMIZATION_TESTING.md) 保留慢样本与硬件验证边界。报告中的历史日志以及独立 MCU 仓库文件不包含在本仓库中。

上传保留 OSK 所需 EXE/DLL、配置和资源，排除构建产物、日志、IDE 缓存、虚拟环境及嵌套 Git 元数据。测试脚本及说明中的旧目录名已适配本仓库布局。

发布副本已通过 57 项 C# 测试、3 项独立 C++ 测试，以及 3 次实际 OSK DLL → SED → Service 的 mock 链路检查。
