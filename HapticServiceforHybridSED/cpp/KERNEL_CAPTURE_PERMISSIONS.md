# 内核调度采集的用途与权限

内核采集用于解释应用计时留下的空档：线程是在 Waiting（等待事件/锁）、Ready
（已可运行但未获 CPU），还是 Running 中被其他线程/驱动中断抢占。当前 WPR 配置
收集 CSwitch、ReadyThread、DPC、Interrupt 和关联栈，按日志中的 Win32 线程 ID
与 QPC 时刻对齐。它是诊断工具，不会自动提高优先级或降低 latency。

## 推荐的正规开启方式

在目标机开始菜单找到 PowerShell，选择“以管理员身份运行”，在该窗口执行：

```powershell
Set-Location D:\work\OSK-MCU\HapticServiceforHybridSED\cpp
whoami /priv
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\Capture-Scheduling.ps1 -LoadWorkers 0 -IdleWakeCount 40 -RequireKernelTrace
```

RequireKernelTrace 表示内核采集启动失败就停止，不把应用 trace 当作内核 ETL。
成功时结果目录应含 scheduler.etl 和 capture.json；用 WPA 按日志中的线程 ID 分析
CPU Usage (Precise)、Ready 延迟及 DPC/ISR。采集会有额外开销，最终性能基准应单独
运行。脚本只停止/取消自己成功创建的唯一实例，不处理别人的录制。

如果管理员窗口仍失败，由管理员/组织 IT 检查：

* 本地安全策略或组策略 → 计算机配置 → Windows 设置 → 安全设置 → 本地策略 →
  用户权限分配 → Profile system performance（SeSystemProfilePrivilege）。
* whoami /priv 中是否存在该权限；账号有管理员组成员身份不等于当前进程已经提升。
* 组织策略及对应 ETW 会话授权。仅加入 Performance Log Users 不保证所有内核
  profile 都可用；不要把它视作管理员录制的等价替代。

该用户权限修改在下次登录时生效，通常不需重启；域策略可能覆盖本地配置。
推荐先提升采集窗口，不为测试向所有普通用户放开系统分析权限。

当前代理进程没有 SeSystemProfilePrivilege，命名 WPR 会话启动报 Access is denied /
0x80070005；先前默认 profile 也报过 0xc5585011。因此本会话没有取得内核 ETL。
这与本轮一次临时 SendInput WinError 5 是不同诊断问题；普通桌面复核后键盘注入
集成已重试通过，不能将它说成已经修改了 WPR 权限。

参考：[微软权限说明](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/profile-system-performance)、
[微软调度分析](https://learn.microsoft.com/en-us/windows-hardware/test/wpt/cpu-analysis)。
