# Panel 操作、类别过滤与长尾复核（2026-10-23）

## 本轮交付

* 首次使用指南：`../../HybridSedPrototype_ExtraInfo/docs/panel-first-run.md`。
* 给原先没有原作者手册的 OSK_Prototype 增加 README 操作入口；未更改原有程序、blob 或配置。
* SED 的 HapticPipePublisher 在入队前只接受 NormalKey/ModifierKey/SpecialKey，
  不发送保留 TouchPad=4；未知枚举仍按原逻辑拒绝。C++ 原先已只接受 1..3。
* 实际管道测试先发布 TouchPad 再发布 SpecialKey，读取到的第一个字节必须为 3，
  防止 4 抢占键盘发送队列。57 项 C#、3 项 C++、实际标记/边沿集成与异步反馈测试通过。
* 只读 `tools/inspect_osk_package.py` 提取当前 blob 坐标表与启动默认值，不自动校准。
* 延迟分析增加 P99.9、样本数和严格超过 1/5/10/20/50ms 的计数；按状态模式、
  mock ACK 延迟、负载、是否 trace 分组，避免把不同条件混成一组对照。

## 顺序运行四组软件测试

运行 `py -3 tests/check_latency_matrix.py`；每组 1400 次，另有一次不计入的预热。
每组内部保持同一 Service 会话，generation=1；组间独立启动。没有 CPU 绑核。
所有组均启动真实 OSK 进程，通过实际 LrSendInput64.dll 注入、真实 SED hook 和
Named Pipe 到 C++ Service，但 HID 是 mock，没有点击界面或触摸识别。

| 条件 | trace | P99 | P99.9 | 最大值 | >1ms | >5ms |
|---|---|---:|---:|---:|---:|---:|
| Feature，空闲负载 | 关闭 | 1.259ms | 2.466ms | 3.005ms | 20 | 0 |
| interrupt，空闲负载，ACK 延后 200ms | 关闭 | 1.419ms | 3.115ms | 3.219ms | 28 | 0 |
| interrupt，4 个计算进程，ACK 延后 200ms | 开启 | 0.466ms | 1.251ms | 1.990ms | 3 | 0 |
| interrupt，8 个计算进程，ACK 延后 200ms | 开启 | 0.356ms | 0.810ms | 1.159ms | 1 | 0 |

全部 5600 次零丢弃；超过 1ms 共 52 次，约 0.93%，超过 5/10/20/50ms 均为 0。
这是这些不同条件合计的观测比例，不是生产故障概率或统计上的延迟保证。
单组 1400 个样本的 P99.9 接近最慢的两个样本，不能作为罕见事件的稳定估计。
Feature mock 没有真实 GetFeature 耗时，不能据此比较实机 Feature 与 interrupt 优劣。
负载组还启用了追踪，也不能把负载组较快解释为增加 CPU 负载有益或应该常驻计算。

原始汇总 `logs/latency-matrix-20261023.json`，四组明细位于
`logs/real-pipeline-matrix-*-20261023/results.json`。

## 当前长尾在哪里

4/8 个计算进程两组中，SED 发布到出队最大分别是 0.152/0.186ms，未复现 11.52ms。
两组 trace 期间 GC 次数和暂停增量均为 0。原生事件唤醒保留为默认，不扩大队列、
取消过期保护或绑核来掩盖等待。

4 计算进程组最长事件 1.990ms：DLL→Service 提交 0.186ms，Service 提交→mock 完成
1.804ms，说明此最长样本不主要卡在 SED。8 计算进程组最长事件 1.159ms：
DLL→hook 就用了 1.117ms，SED 发布→出队只有 0.009ms。空闲组未启用分段 trace，
所以其中 3.219ms 的事件只能定位为 DLL→Service 提交约 3.196ms，不能继续强归因。

这说明继续只替换 SED 队列没有证据支持。下一步应采集 Windows ReadyThread、
Context Switch 与 DPC/ISR 轨迹，区分线程等待、唤醒后等待 CPU 和运行期间被抢占。
本轮确认 WPR 原本没有录制，尝试 `wpr -start CPU -filemode`，失败：

```text
Failed to enable the policy to profile system performance.
Profile Id: CPU.Verbose.File
Error code: 0xc5585011
```

当前进程不是管理员；不能把该错误唯一归因于权限，也没有修改策略或要求绕过。
随后 `wpr -status` 确认没有遗留录制。内核轨迹未采集成功，因此不能宣布找到全部
调度长尾根因。管理员环境下可在先确认无他人录制后，用 CPU profile 录制短时复现，
再用 WPA CPU Usage (Precise) 按启动日志中的 Win32 线程 ID 关联分析；务必正常停止
自己启动的录制并保存 ETL，不取消或覆盖别人的录制。

## 对维护和真实硬件的判断

显式 diagnose 是人为请求刷新状态，不是每秒固定任务。1Hz 是请求合并后的上限，
缓存 status 不产生 HID 读写。周期验证、空闲轻量心跳和重连退避是三种不同机制，
具体时间与操作方式在首次使用指南第 8 节。

interrupt 异步反馈允许待 ACK 时继续 PLAY；不会停止已经开始的 I2C 事务。
Feature 同步读取、配置确认、维护命令 Output 和真实设备/驱动队列仍有影响空间。
本轮没有改变优先级、CPU affinity、电源计划、系统计时器、GC 模式或固件。

后续验证优先级：

1. 在可采集内核调度轨迹的环境定位超时样本，按确切阻塞源优化，避免盲目改全局设置。
2. 目标机验证较长空闲后的首键、休眠唤醒、锁屏/切换会话、多屏与 DPI；本轮持续输入
   不能覆盖所有这些状态，也没有模拟系统内存压力或所有第三方驱动负载。
3. 接入真实 Panel：报告兼容、九点命中、放手不多触发、掌触/多指/重复键。
4. 接入真实 MCU：pending Input read 与 Output write 并发、迟到/丢失 ACK、拔插/重启、
   总线逻辑分析仪及 MCU 接收/开始播放时间戳，分别测软件、总线和电机起振。

观察窗口里没有 >5ms 不代表未来不会出现；当前证据支持“原 SED 等待风险明显降低”，
不支持“所有机器任何状态下都有一个固定的最大延迟”。
