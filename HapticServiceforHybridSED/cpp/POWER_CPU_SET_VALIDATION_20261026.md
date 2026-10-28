# CPU停放、CPU限制与优先级补采

已在新SED＋新Service上实际执行1,410次正常负载连续会话，零丢弃、零重复PLAY；同轮采集调度和Windows内置Power profile。没有改动系统电源方案、优先级或绑核，未提交/推送。

## 补齐的采集

* Scheduling.wprp增加ThreadPriority内核关键字；同轮启动内置Power profile。
* 每批输入前，使用只读API读取被测进程/关键线程亲和掩码、进程默认CPU Sets、线程显式CPU Sets及系统CPU Set停放/分配标记。读取位于测量按键区间之外，仍可能影响随后系统状态，不能称为零扰动。
* Windows CPU Sets为空表示未显式指定、继承默认行为，不解释为不能使用CPU。硬亲和、CPU Sets和停放状态分别保存，不将它们混为一个有效CPU列表。
* 离线解析Thread/SetPriority、SetBasePriority、SetPagePriority、SetIoPriority；本地root/wmi ThreadPriority架构给出对应opcode48/49/51/52及字段。未知opcode70/71保留为未解释原始线程事件，不伪装成已解码CPU优先级。
* Power profile记录ParkCore/UnparkCore、性能/频率策略与异构处理器调度策略等。完整保存Park/Unpark转换，其余详细Power载荷保存于最慢20个与全部>5ms事件的窗口；没有宣称保存每个电源事件的完整载荷或解析出每个CPU完整C-state驻留图。

## 实测

| 区间 | P99 ms | 最大 ms | >5ms |
|---|---:|---:|---:|
| Pipe写入入口→Service提交 | 0.159 | 0.675 | 0 |
| Service提交→mock完成 | 2.194 | 5.632 | 2 |
| Pipe＋Service | 2.316 | 5.683 | 2 |
| DLL→mock完成 | 3.210 | 6.144 | 2 |

profile启动、测试、profile停止退出码全0，解码3,438,643个CSwitch、1,870,199个ReadyThread，丢失事件0。比之前多了Power/优先级事件和CPU快照，本轮百分位不直接与较轻采集轮混池比较。

## 线程限制与停放现场

18个批次边界快照中，被测Service进程、输入线程和工作线程亲和掩码均为4194303（22个逻辑处理器），Group0；默认和显式CPU Sets均为空。SED关键线程也在同文件记录。因此这些快照未发现被测关键线程存在显式的窄亲和/CPU Sets限制；快照并非每个指令时刻的连续观测，也未覆盖所有可能的QoS/作业/调度策略限制。

各快照未停放CPU数量为3、4、5、6或11个，其余为Parked。不能从“线程亲和掩码允许22个”推导“22个CPU当时都处于同样可用的功耗状态”。

以最近的批次快照为起点，按同一QPC时钟应用之后的ParkCore/UnparkCore转换，重建按键通知时的停放选择：

| Pipe＋Service | Ready→首次运行 | 通知时重建的未停放逻辑CPU | 最近一次等待期间Park/Unpark事件 |
|---|---:|---|---:|
| 5.683ms | 5.6059ms | 10、13 | 0 |
| 5.510ms | 5.4626ms | 11、12 | 0 |

这表示电源策略当时主要保留少量未停放CPU，不能把停放标记当成操作系统绝对禁止调度到该CPU。快照是顺序只读查询，若边界恰好发生转换可能存在采样时序误差；本轮慢事件不在快照调用边界。也不能据此确认停放是唯一原因。

## 优先级变化

采集解码113,640个线程优先级类事件：SetBasePriority66,270、SetPriority45,653、SetPagePriority1,694、SetIoPriority23。工作线程42204的基础优先级在启动时有8→9的明确事件；两条慢事件实际切入时均为10，通知到Ready很快。

在该线程本轮保存的优先级类记录中没有找到慢事件附近另一次显式SetPriority操作。这不等于动态优先级从未变化，也不把启动基础优先级事件误用为Ready期间每一瞬间的完整动态优先级曲线；CSwitch给出的切入/切出数值仍分别对应那些时刻。

## 可据此做出的判断

通知和队列锁不是这两条事件的主要耗时；线程Ready后仍等待数毫秒才执行。已排查出“进程快照掩码只允许两核”不是本轮看到的情况；同时，电源停放选择确实使即时未停放CPU远少于22个。背景线程竞争和停放/异构电源策略都有继续验证的价值。

尚不能量化停放单独贡献了多少延迟，也没有对某个后台进程或驱动作唯一归因。后续最有区分力的是同样新版、相同采集场景下做电源策略的可逆配对实验，保留并恢复原设置，再比较Ready等待；不要同时改变优先级，否则无法区分原因。当前未执行电源策略变更。

## 复现

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File HapticServiceforHybridSED/cpp/tools/Capture-Scheduling.ps1 -Elevate -RequireKernelTrace -PowerDetails -ScenarioSet cadence -CadenceCycles 3 -LoadWorkers 0 -IdleWakeCount 0
```

本轮目录：`logs/real-pipeline-schedule-20261026-151204-3c9ab8a9/`。`cpu-snapshots.json`保存实际限制，`power-scheme.txt`保存当轮方案，`kernel-thread-events.json`含电源转换/优先级解码，`power-cpu-restrictions.json`为慢事件现场重建。logs为本地忽略目录。

离线执行EtlSchedule后，依次运行`correlate_kernel_waits.py`和`analyze_power_cpu_sets.py`并传入本轮目录即可重建报告JSON。工具构建、Python语法、PowerShell语法、时间戳验证与diff检查通过。
