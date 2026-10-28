# 两条>5ms事件的同轮ETL进一步分析

仅离线分析现有capture `logs/real-pipeline-schedule-20261027-152237-77ce1280/`，未修改生产调度或重新注入按键。原ETL解码EventsLost=0。以同一QPC对齐应用记录、CSwitch/ReadyThread、Power及批次CPU集合快照；进一步用TraceLog关联阻塞栈，并从微软符号服务器读取与ETL版本匹配的Windows公开PDB。

## 12.629ms：已确认在Win32k输入路径等待内部资源

sequence2为该capture第一条正式计量输入（预热不计）。DLL侧起点→Hook为12.463ms，后面的SED队列45µs、Pipe28µs、Service队列14µs。

发送线程33704在起点后18.2µs切出，处于Wait。该次连续Wait约12.160ms；整个Hook前区间内累计Wait约12.378ms，Ready约5.4µs。对应**切出线程阻塞栈**可解析为：

```text
LrSendInput64.dll
win32u!NtUserSendInput
win32kfull!NtUserSendInput
win32kbase!EnterCrit
win32kbase!UserCritInternal::...::EnterCritInternal
ntoskrnl!ExAcquireFastResourceExclusive
ntoskrnl!ExpAcquireFastResourceExclusiveSlow
ntoskrnl!ExpWaitForFastResource
ntoskrnl!KeWaitForSingleObject
```

这把此前“DLL内部或输入投递未知等待”缩小到了**NtUserSendInput进入Win32k时获取内部独占资源的慢路径**。不是DLL在CPU上计算12ms，也不是Pipe复制或Service处理。

Hook线程41452随后在`GetMessageA → NtUserGetMessage → ... → EnterCrit → ExAcquireFastResourceExclusive`路径也发生一次较短的资源等待。发送线程后续的另一次短Wait栈包含`xxxSendInput → xxxCallHook2 → xxxInterSendMsgEx`，符合输入Hook消息交接路径。它们与大段最初资源等待不能混为一个全程等待原因。

**证据限度**：公开符号和切换栈未提供这次资源实例的地址、持有者、持有期间调用栈或所有权链。因此不能断言哪个应用/驱动持锁，不能证明两个线程等待完全相同的资源实例，也不能把它追溯为此前未有同轮轨迹的21ms事件。提高等待线程自身优先级不能直接释放别人持有的资源。

## 7.808ms：后半段确有Ready等待，伴随系统I/O工作与停放选择

sequence779，关键区间如下：

| 区间 | 应用耗时 | 同轮线程状态 |
|---|---:|---|
| DLL侧起点→Hook | 2.205ms | 发送线程累计Ready约2.062ms；Hook累计Ready约1.884ms，两者重叠 |
| SED发布→出队 | 3.375ms | SED发送线程Ready约3.327ms |
| Pipe调用前→Service提交 | 2.162ms | Pipe接收线程Ready约2.129ms；SED发送线程同期等待I/O |
| Service提交→出队 | 0.030ms | worker Ready约9.9µs |

SED发送线程调入时动态优先级11，Pipe接收12，Hook13；这不是未启用Highest，配置相对值均核实为2，基础值10，动态优先级会变化。同期可见System线程14～16、音频22等更高优先级活动。

批次快照与Park/Unpark重建显示，这次输入前段、SED队列和Pipe区间开始时未停放逻辑CPU为11/12，两个后段等待区间内没有新的Park/Unpark。两枚CPU中，在SED区间System优先级14的线程上下文分别约2.799/3.297ms；在Pipe区间分别约1.864/2.001ms，另有优先级16活动。上下文时间包含可能的中断，不能再与中断耗时直接相加。

系统工作线程的切出栈包括：

```text
ntoskrnl!CcAsyncLazywriteWorkerThread
ntoskrnl!CcAsyncLazywriteWorker
ntoskrnl!CcWriteBehindAsync
ntoskrnl!CcFlushCacheOneRange / MmFlushSection
ntfs!NtfsFsdWrite / NtfsCommonWrite / NtfsNonCachedIo

ntoskrnl!ExpWorkerThread
ntoskrnl!IopProcessWorkItem
fvevol!FveWorkerKmTP
fvevol!FveIoAggregator
```

这些把System背景活动进一步识别为文件缓存回写、文件系统和卷加密相关系统工作。`fvevol.sys`本机文件说明为微软BitLocker Drive Encryption Driver。并非所有System上下文都能从切出栈还原其完整在核运行工作，也不能把“该栈出现次数”当作CPU占用百分比。

### 可排除或限制的解释

* **不是持续按键积压的证据**：上一条正式记录的PLAY已在当前发布前14.655ms完成，上一次Pipe写入也提前14.689ms结束。本键的SED出队延迟已由Ready等待解释，不能描述为前面有大量PLAY排队。
* **不是进程绑核**：SED/Service进程及各目标线程快照掩码4194303，允许22个逻辑CPU；显式CPU集合为空（继承默认），没有人工绑定CPU11/12。快照不是连续CPU可运行资格证明。
* **未见单个数毫秒中断**：SED等待区间最长单个已录ISR/DPC约50.7µs，Pipe区间约85.1µs。每个活动CPU中断重叠并集约0.271/0.438ms和0.391/0.236ms，不能单独解释整个3.327/2.129ms等待；不等于所有驱动没有影响。
* **不能仅凭Idle证明其他CPU可立即执行这些线程**：停放、电源、QoS/调度资格并非亲和性掩码一项能够描述。不能据这些日志回答调度器为何没有即时唤醒/迁移到其他CPU。
* **尚不能锁定I/O来源文件**：现有采集未对上述I/O关联文件名/IRP因果链。WPR文件录制本身可能增加磁盘回写活动，但不能据此认定本次竞争就是采集工具造成，更不能把它当作只在测试时发生。

直接可确认的是该事件中的SED/Pipe线程Ready等待，以及同时间更高优先级系统线程活动；少量未停放CPU与该竞争吻合，但不构成“停放是唯一根因”的证明。

## 对分组输入和下一步的含义

两条虽然出现在同一分组输入轮次，机制不同：一条Win32k资源等待，一条跨多个主机阶段的调度等待。第一条就在正式计量开始时发生，后一条发生时上一键已完成，不能由“分组输入导致队列越来越长”概括，也不能认定修改分组间隔就已修复产品。

下一步最有区分力的验证是：

1. 针对Win32k等待，补采Win32k输入/资源所有权或持有方栈，确认具体依赖；现有公开栈无法命名持有者。若以后调整架构，可评估OSK明确确认输入后直接交付触觉语义事件，减少触觉对Windows键盘注入/Hook路径的依赖；当前未实现该变更。
2. 针对SED/Pipe Ready等待，在同电源/优先级/输入场景下对比轻量调度采集和完整Power/栈录制，评估采集开销；需要I/O来源时再采文件/磁盘事件，避免无界增加所有provider。
3. 电源策略或优先级对比每次只改一个因素，继续保留未改善和慢样本；不直接启用Realtime、绑定CPU或关闭卷加密。

## 工具及证据文件

新离线工具：`tools/EtlStacks`，用`TraceLog.OpenOrConvert`关联stackwalk。**CSwitch普通CallStack属于切入线程；分析切出线程的等待必须取BlockingStack**，输出`stack_role=switched_out_blocking`避免混淆。411个选定事件中350个有相关栈，缺栈保留为空，不补造符号。符号解析有个别非研究帧的范围警告；上述结论只引用实际解析出的匹配帧。

```powershell
dotnet run --project HapticServiceforHybridSED/cpp/tools/EtlStacks -- HapticServiceforHybridSED/cpp/logs/real-pipeline-schedule-20261027-152237-77ce1280 --symbols
```

原ETL未修改，旁边新增ETLX索引、公开PDB缓存、`slow-stacks.json`、`stack-decode-console.txt`、`slow-context.json`；此前的`slow-stage-scheduling.json`保留线程状态分段。当前仍是mock PLAY，未引入真实HID/MCU行为结论。
