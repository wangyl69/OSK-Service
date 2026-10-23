# 本轮功能补齐与低延迟策略

此文描述当前实现，优先于早期评估中“尚未迁移”的记录。没有修改原 Haptic_Service、MCU、OSK，也没有恢复额外 Agent。按键仍为 HybridSED → 原单字节管道 → C++ Service → HID。

## 已补齐

| 能力 | 当前实现 |
|---|---|
| PLAY 优先 | 已排队 PLAY 优先于普通心跳、配置和诊断 |
| 空闲保活 | 仅距离最近一次 HID I/O 达到配置间隔才发送，PLAY 也更新该时间；默认250ms，可关闭 |
| 防止心跳饿死 | 通常10s验证，在持续输入下可延后，但到20s优先执行，保留30s fail-safe余量 |
| 低频维护 | 配置、主动诊断等待至少100ms的HID空闲；普通10s验证等待50ms空闲 |
| 状态和能力 | 缓存报告模式、能力、连接代次、会话状态、配置版本、故障阶段和原因；Feature提供MCU accepted/rejected/I²C错误和队列计数 |
| 延迟统计 | 固定4096样本环，记录Service队列、HID写入、接收至写完和后台I/O耗时，查询时才排序计算p50/p95/p99 |
| 日志轮转 | 每文件约2MiB，保留3个历史文件；真实硬件不逐键打印 |
| 动态控制 | 独立低频控制管道，运行时修改波形、强度、保活间隔，暂停/恢复会话，查询和恢复 |
| 模式兼容 | auto先Feature，Feature不可用时尝试有ACK的interrupt；write-only只允许显式选择并标记DEGRADED |
| 故障升级 | 3/6/12/24/30s退避，连续6次非“设备缺失”故障进入FAULT，人工recover或切换模式再尝试；不自动reset整颗MCU |

旧默认OSK窗口检测、诊断GUI和逐键JSON往返未回迁：前者由新架构替代；GUI由CLI提供操作入口；每键不增加JSON成本。控制面返回JSON，按键通路仍只传类别字节。

## 使用

```powershell
# 默认：auto状态模式，250ms空闲保活
.\build\HybridHaptic.exe --console --mock
# 真实硬件：去掉mock，关闭轻量保活用于A/B；10s会话验证仍保留
.\build\HybridHaptic.exe --console --keepalive-ms 0 --status-mode auto
```

另开终端执行控制命令，不要再启动第二个后端：

```powershell
.\build\HybridHaptic.exe --control "status"
.\build\HybridHaptic.exe --control "diagnose"
.\build\HybridHaptic.exe --control "set waveform 3"
.\build\HybridHaptic.exe --control "set intensity 2"
.\build\HybridHaptic.exe --control "set keepalive 0"
.\build\HybridHaptic.exe --control "session close"
.\build\HybridHaptic.exe --control "session open"
.\build\HybridHaptic.exe --control "recover"
.\build\HybridHaptic.exe --control "mode interrupt"
.\build\HybridHaptic.exe --control "mode write-only"
.\build\HybridHaptic.exe --control "metrics reset"
```

`set keepalive`允许0或100..10000ms；0关闭轻量保活。waveform范围1..4、intensity范围1..3。`mode auto|feature|interrupt|write-only`会重新建立会话。

配置响应 `queued:true` 表示已接受，不代表MCU已应用。查询status中的requested_revision/applied_revision及MCU回读值确认完成。配置与诊断合并待办、等待空闲，持续快速输入时可能延后。session close立即停止接受新PLAY，已在执行的I/O不能被撤销；status到PAUSED代表worker已退出活动循环。

status/ping只读缓存，不访问HID；diagnose只设置后台刷新请求，随后再次status查看snapshot_age_ms、snapshot_valid等。write-only回读未知值为null，不误报为0或“已确认”。interrupt模式没有完整Feature计数，未提供的字段同样标未知。能力报告包含flags、马达数、波形数及心跳/fail-safe参数。

控制管道名为原管道名加Control，例如HybridSedHapticControl；设置`--sed-pipe`时控制命令也必须使用相同参数。它使用消息模式、ASCII命令和JSON响应，客户端收到响应后发送单字节0x06确认。控制端CLI已封装此协议。只允许本机交互会话/系统权限，当前服务仍限制活动物理控制台会话，不支持RDP多用户路由。

运行时设置目前只保存在内存，重启恢复安装或启动参数。需要持久配置时应修改部署参数；SCM安装命令会保留waveform/intensity/keepalive/status-mode/pipe选项。

## 延迟保护和不能承诺的部分

- 每次按键不做JSON编解码、状态读取、分位数排序、真实设备日志落盘或动态配置。内存计时和环缓冲记录有少量开销，不能称为零开销。
- 后台控制操作即使不抢已排队PLAY，已经进入驱动的同步HidD_GetFeature或HID写入也不能被随后到达的PLAY抢占。20s强制验证同样可能占用HID，这是防止会话过期的必要折中。
- C#语音WMI已移到独立有界后台队列，触觉发送使用专用后台线程，减少COM和线程池调度对键盘钩子与触觉路径的干扰；Windows调度和GC的停顿仍可能产生长尾。
- age_us和host_time从Service收到字节开始计时，不包含OSK、SED、管道之前的时间，更不等于LRA机械起振延迟。
- 默认mock中的逐PLAY打印仅用于功能测试；性能测试使用`--mock-quiet`关闭它，并使用`--mock-trace`启用固定128项内存QPC记录。trace只允许mock，生产环境默认无逐键trace；mock计时不能与真实HID写入耗时等同。
- 真正“整体Latency不回退”仍须同机同固件A/B测首键、持续输入p95/p99、Feature耗时和机械起振；本轮只验证调度策略、功能和mock计数。

## 验证

```powershell
powershell -ExecutionPolicy Bypass -File .\validate.ps1
powershell -ExecutionPolicy Bypass -File .\validate.ps1 -Extended
```

包含C#测试、C++核心/真实Windows管道I/O取消测试、直接引用固件协议头的报文契约测试、真实SED直接管道联调，以及控制/模式/性能策略测试。固件契约测试在相邻Athena_Haptic源码存在时启用，不构建或修改MCU固件。功能日志和样本在logs/features，结果为results.json。Extended额外运行实际OSK程序及原生DLL链路压力测试和约75秒FAULT退避锁存测试。最新结果与测试范围见[验证记录](VALIDATION.md)。

新增测试覆盖非法配置、动态回读、会话暂停/恢复、连接代次变化、recover、2MiB日志轮转、关闭/启用轻量保活、空闲心跳、诊断期间120次输入、关闭保活时120次输入、Feature/interrupt/write-only、模拟Feature故障退避及模式切换恢复。核心测试覆盖PLAY优先、维护空闲窗口、20s截止、环缓冲分位数、退避与FAULT分流。它们不证明真实硬件行为。

当前终端非管理员，SCM安装启停未实测；各状态模式和电源/锁屏恢复仍需目标机验证。没有新增自动MCU复位，也没有修改原设备驱动。
