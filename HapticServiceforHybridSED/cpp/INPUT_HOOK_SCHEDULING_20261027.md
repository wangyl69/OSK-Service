# 输入进入 Hook 前的调度定位

2026-10-27 补充 DLL 调用前/返回时间、发送线程 ID/相对优先级/进程类别；ETL 解码新增发送线程、SED Hook/发送线程，并将分析窗口覆盖到注入打点以前。只修改诊断工具，生产 SED/Service 仍为 Normal 进程＋四个关键线程 Highest，未绑核或修改电源策略。

`inject` 是 Python 侧原始起点，`dll_call_enter` 是 ctypes 调用前打点，`dll_call_return` 是返回后打点，**并非 DLL 内部入口**。这能排除部分 Python 调用前等待，但不能独立分离 DLL 内部与 Windows 输入投递。Hook 可能在 DLL 返回以前执行，mock PLAY 也可能先于 DLL 返回，因此 DLL 调用总耗时不能直接加到触觉链路上。

## 同轮内核定位

`logs/real-pipeline-schedule-20261027-093917-d09493e9/`：四计算进程压力，1,410 次全部通过，零丢失/额外/KeyUp PLAY。发送线程相对优先级 0、进程类别 32；四个生产关键线程 Highest。Pipe＋Service P99 0.267 ms、最大 0.463 ms。没有重现此前 21.062 ms。

Hook 前最慢事件为 sequence 559，耗时 1.863 ms，其中打点到 ctypes 调用前 0.005 ms，调用前到 Hook 1.858 ms。同轮 CSwitch/ReadyThread 表明：

* 发送线程 28012 在注入起点后 203.3 µs Ready，至 1371.8 µs 才运行，单次 Ready 等待 1.1685 ms；运行时动态优先级为 15。
* Hook 线程 6988 在起点后 1408.4 µs Ready，1421.9 µs 运行，此次 Ready 等待 13.5 µs；随后还有一次短等待及唤醒，1836.6 µs Ready，1843.5 µs 运行，1863 µs 进入回调打点。

此事件已证明发送端在 DLL 调用期间也可能发生 Ready 等待；并不表示 DLL 在 CPU 上计算了 1.858 ms，也不证明此前无 ETL 的 21 ms 是相同原因。CSwitch 内数字 wait reason 未进一步解析为具体函数依赖。没有据此认定任何单一后台应用是根因。

## 发送线程 Normal / Highest 配对

新增 `tests/check_real_pipeline.py --injector-priority normal|highest`，仅改变调用 DLL 的测试线程；生产四个线程一直 Highest。`tools/Compare-InjectorPriority.ps1 -Elevate` 执行 Normal→Highest→Highest→Normal，每轮 470 次、四计算进程、同样输入节奏和 Power/内核采集。外层 `logs/injector-priority-20261027-094444/manifest.json` 保存各轮完整目录。

合并原始逐键样本，单位 ms：

| 发送线程 | 次数 | Hook 前 P99 / 最大 | DLL→mock P99 / 最大 | Pipe＋Service P99 / 最大 |
|---|---:|---:|---:|---:|
| Normal | 940 | 0.431 / 0.900 | 0.703 / 1.037 | 0.287 / 0.458 |
| Highest | 940 | 0.425 / 1.033 | 0.839 / 1.592 | 0.338 / 1.292 |

四轮零丢失、重复或 KeyUp PLAY，所有区间均未出现 >5 ms。逐轮 Hook 前最大值为 0.900、1.033、0.951、0.463 ms。每轮最慢 Hook 前事件的发送线程单次 Ready 等待分别为 7.6、16.7、15.6、16.3 µs；Hook 分别为 16.5、15.4、32.7、63.0 µs。这些数字只统计匹配 ReadyThread 到首次运行的区间，不是全部等待时间总和；其余部分不能直接归为 CPU 计算或某个具体输入组件。

五份 ETL 全部成功解析且 EventsLost=0。最初 1,410 次与四轮 1,880 次合计 3,290 次完整输入；测试有新增打点和内核 Power 采集的开销，不能直接与此前无内核采集的频率实验做因果对照。离线解码器保留了使用原始 QPC 对齐所产生的 TimeStampQPC 提示。

## 当前结论

尚未发现将发送线程提高为 Highest 的稳定收益；样本较少且没有重现 21 ms，不能宣称其对罕见事件没有任何作用。Hook 最慢事件里 Ready 等待较短，没有证据支持立即把整个 SED 进程提高到 AboveNormal。继续保留当前生产设置；诊断 Highest 选项只用于后续定点验证，未迁移成 OSK 的生产默认值。

原始逐键记录为各 capture 的 `results.json`，内核解码为 `kernel-thread-events.json`，前十条输入慢事件的 Ready 等待复核为 `input-wait-review.json`。此前 21 ms 的根因仍需捕获同类慢事件的同轮轨迹后才能确认。

## 有停止条件的追加采集

`logs/real-pipeline-schedule-20261027-095513-f4b3fbbb/` 追加八轮、3,760 次；保持 Normal 发送线程、Highest 生产关键线程、四计算进程及 Power/调度采集。新参数 `--stop-prehook-ms 5`（PowerShell 为 `-StopPreHookMs 5`）在完成一轮后检查本轮是否已有注入到 Hook >5 ms 的记录，若有则保存全部已完成数据并停止后续轮次。它不在热路径增加日志或查询。此轮没有触发，按计划上限结束，零丢失/额外/KeyUp PLAY。

| 阶段 | P99 | 最大 |
|---|---:|---:|
| DLL侧打点→Hook | 0.441 ms | 2.842 ms |
| SED发布→发送线程取出 | 0.199 ms | 1.095 ms |
| Pipe调用前→Service入队 | 0.151 ms | 0.301 ms |
| Service入队→mock PLAY | 0.173 ms | 0.900 ms |
| Pipe＋Service | 0.266 ms | 0.920 ms |
| DLL侧打点→mock PLAY | 0.649 ms | 2.974 ms |

ETL 解码 5,862,453 次 CSwitch、3,408,771 次 ReadyThread，EventsLost=0。离线解析首次因电池事件的重复字段名 `activeBatteryCount` 中止；已按字段索引保留数据、对重名键附加后缀，原 ETL 重解析成功，无需重测输入。

最慢 Hook 前事件 sequence 889，位于第二轮 250 ms 空闲首键场景，注入到 ctypes 调用前 60 µs。以注入打点为0：

* 发送线程在87.5 µs切出、状态为Wait，1854.3 µs才Ready；该次Wait **1.7668 ms**。随后1880.1 µs运行，Ready等待 **25.8 µs**。
* Hook线程1913.2 µs Ready，2026.3 µs运行；该次Ready等待 **113.1 µs**。2086.9 µs又以Wait状态切出，2674.0 µs Ready，该次Wait **587.1 µs**，随后2780.2 µs运行，2842 µs进入回调打点。

本事件主要区间是线程尚未Ready的等待，不能解释为CPU一直不给高优先级线程执行，也不能靠提高等待线程自身优先级直接消除。尚未解码切出调用栈及输入依赖链，不能确定Wait所对应的具体输入组件，更不能把这一等待原因追溯为此前21 ms的已确认根因。

本日目前共7,050次带轨迹输入（最初1,410＋ABBA1,880＋追加3,760）全部完成，未重现>5 ms。该集合混有两档发送线程设置和不同轮次，不直接与此前无内核采集的概率实验合并。重复通过不能证明长尾消失；下一步优先利用已有ETL解析Wait切出栈/输入依赖，若后续再运行输入，则使用阈值停止方式捕获罕见事件，而不是无限重复同样输入。
