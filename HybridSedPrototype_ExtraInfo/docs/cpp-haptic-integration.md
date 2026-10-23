# 当前 C++ 触觉集成（2026-10-22）

HybridSED 保留 C#/.NET；C++ Service 独立进程部署，直接接收 HybridSedHaptic，无 Agent 或第二条管道。

当前`Dispatcher:LatencyThreadPriorityBoost=true`默认只将键盘钩子和触觉发送线程设为AboveNormal；启动时记录Win32线程ID、请求和实读优先级，设置失败记录Warning并继续运行。进程级别不主动提升，WMI、日志线程不提优。通过CLI `--Dispatcher:LatencyThreadPriorityBoost=false`恢复Normal。C++接收/HID线程有独立`--thread-priority normal|above-normal`开关，默认above-normal。实测和关闭方法见[优先级报告](../../HapticServiceforHybridSED/cpp/PRIORITY_TESTING.md)。

新增 `Dispatcher:HapticLatencyTraceName` 诊断选项，默认空值关闭。测试使用唯一 Local 共享内存名称启用最多4096项 hook/发布/入队前后/sender取出/管道写入计时，不改变单字节协议；记录满后仅停止 trace，不停止发送。开启有诊断开销。GC 计数和累计暂停时间只在 trace 时采集。最新无绑核复测、失败数据保留和等待区间证据见 [SED 分段计时报告](../../HapticServiceforHybridSED/cpp/SED_STAGE_TESTING.md)。

发送线程现在默认 `Dispatcher:HapticSenderNativeWake=true`：入队后发送Windows Event信号，sender取完队列再等待新事件/停止信号；false恢复旧BlockingCollection阻塞等待。仍是容量8、50ms门限，hook不执行连接/管道写入。三轮对照、当前默认配置与回退方法见 [发送线程唤醒报告](../../HapticServiceforHybridSED/cpp/SENDER_WAKE_TESTING.md)。

钩子现会在创建语义对象前跳过无需输出的事件：只有标记OSK KeyDown或任意来源语音键才进入分类；语音键仍保留Down/Up。原有最终触觉门控保留。Debug关闭时跳过逐键日志参数构造，减少无用分配。

- Worker 调用 HapticInputGate：OSK 标记匹配且 KeyDown 才路由触觉，Voice 保留 Down/Up。
- 每个语义事件单独捕获路由异常，WMI 失败不会跳过后续 haptic。
- HapticPipePublisher 钩子侧只做零等待入队，专用命名线程连接和写管道；保留8项上限、50ms过期，失败不重放。NativePipeWriter使用可复用的固定缓冲区和原生OVERLAPPED/event，发送线程直接等待Windows完成事件，不再等待托管WriteAsync完成回调。连接及写入共用默认20ms预算；超时/停止时取消并等待内核操作结束后才复用内存，取消收尾可能超过预算。
- WmiVoicePublisher 也仅在钩子侧校验和入队，独立后台任务按顺序执行实际 WMI；WMI 卡顿不会延迟后续触觉入队或正常按键放行。
- 语音队列最多 32 项，过载时丢最旧待发布状态、保留剩余顺序与最终 Stop；不保证过载期间每个中间录音边沿均发布。正常停止最多等待 2 秒顺序排空；COM 卡住时只取消剩余队列，已进入的 COM 调用仍需自行返回。
- 默认不记录每键日志；诊断需要时设 `--Logging:LogLevel:Default=Debug`，避免常态控制台开销。
- 管道权限失败会发出限流 Warning 并丢弃本次请求，后续新请求仍会尝试恢复；键盘钩子在 ready 前创建消息队列，并检查退出信号和线程停止结果。
- 触觉发布器每 5 秒在计数变化时汇总 `Haptic pipe stats`，区分入队、发送、队列满、50ms 过期、连接/写入失败、超时、权限拒绝和停止丢弃。`TimedOut` / `AccessDenied` 是失败原因的子集，不应再次加进 `Dropped`。`Sent` 只表示管道写入完成，需与 Service PLAY 计数及独立 Windows 输入观察器计数对应，不能当作 MCU 已振动。
- `ExpiredAtDequeue` / `ExpiredBeforeWrite` 区分等待发送线程与连接后检查时过期；`MaxQueueWaitUs`、`MaxExpiredAgeUs`、`MaxConnectUs`、`MaxWriteUs` 提供进程生命周期内的最大耗时，单位微秒。Windows 调度与运行时 GC 仍可能造成抖动；这些计数用于暴露它，不能将专用线程理解为实时系统保证。
- 分类器仍可生成 KeyUp 的内部触觉分类，但 Worker 会过滤；不得绕过此门控发布。
- 类别字节 1/2/3 不变，每个字节代表一次 KeyDown 请求；实体键盘、无标记注入及 KeyUp 不发送。
- HapticPipePublisher 在入队前也只允许类别 1/2/3；保留 TouchPad=4 不发送，C++ Service 再次检查 1..3。此字节是类别而非原始键值；接收端不能从它重新判断标记或 Down/Up。
- F24 Down：Voice Mode0 和 SpecialKey；Up：仅 Voice Mode1。分类键集合应避免重叠。
- C++ 服务位于同级 HapticServiceforHybridSED/cpp；原 HapticPipeConsumer 仅保留为调试示例，不能与服务同时运行。

部署、验证和限制见 [C++ Service 说明](../../HapticServiceforHybridSED/cpp/README.md)。

旧文档中“按下松开都发触觉”、“pipe 同步运行在 hook 线程”和“WMI 同步运行在 hook 线程”是历史基线，不再适用。
