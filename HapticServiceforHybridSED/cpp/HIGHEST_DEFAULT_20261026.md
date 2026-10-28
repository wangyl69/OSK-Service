# 当前采用的优先级优化

基于已完成的同电源策略配对验证，将四个关键专用线程的默认档位改为Highest：SED键盘Hook、SED触觉发送、C++ Service Pipe接收、C++ Service HID工作线程。两个进程维持Normal，Service诊断线程Normal。默认基础优先级由9提高到10；保留Windows动态提升，不是实时优先级，也不是高于系统所有线程。

原有固定队列、事件唤醒、Pipe完成事件复用、异步反馈、心跳和故障恢复不在本次修改范围。系统平衡电源方案维持原状，不长期强制减少停放，不绑核。

## 配置

* SED：appsettings.json中`LatencyThreadPriorityBoost=true`、`LatencyThreadPriority="highest"`；代码缺省值也为highest。旧的Boost=false仍强制Normal。命令行/环境变量等显式设置会覆盖默认值。
* C++：缺省`--thread-priority highest`；仍支持normal和above-normal。旧服务注册命令若显式带`--thread-priority above-normal`，需改为highest才能使用该档；本轮未重装SCM服务。
* Capture-Scheduling.ps1缺省使用highest；历史对照测试脚本保留显式above-normal等档位以便重现旧实验，不能将它们误当成生产默认值。

重启新版SED和Service后生效。代码目录与本轮重建的输出均已更新，未提交/推送。

## 默认值验证

新增`check_real_pipeline.py --default-priorities`，启动SED/Service时不传优先级覆盖选项，再通过实际线程查询核验Highest。

实际执行：

```powershell
py -3 HapticServiceforHybridSED/cpp/tests/check_real_pipeline.py --with-osk --quiet --sed-trace --default-priorities --status-mode interrupt --mock-status-delay-ms 200 --scenario-set cadence --run-label highest-production-defaults-20261026
```

470次真实DLL→Hook/SED→Pipe→mock PLAY通过，零丢弃、零多余/松开PLAY。SED两个关键线程、Service两个关键线程的相对优先级均2（Highest），Service诊断线程0，进程类别32（Normal）。Pipe＋Service P99 0.762ms、最大1.528ms；DLL→mock P99 1.018ms、最大2.164ms，无>5ms样本。该轮无内核Power录制，不能与先前较重采集直接做性能归因，也不能把样本最大值当长期上限。

C#构建0警告/0错误，58项测试通过；C++构建和CTest5/5通过。真实HID/I²C、MCU与振动耗时仍未实测。

## 默认配置的四进程压力复测

在同一普通桌面上，以四个Normal优先级计算进程争抢CPU，保持平衡电源方案、默认Highest、不中途重开Session，重复三轮输入节奏，共1,410次真实DLL按下/松开。测试命令：

```powershell
py -3 HapticServiceforHybridSED/cpp/tests/check_real_pipeline.py --with-osk --quiet --sed-trace --default-priorities --status-mode interrupt --mock-status-delay-ms 200 --scenario-set cadence --cadence-cycles 3 --load-workers 4 --run-label highest-default-load4-20261026
```

无丢失、额外或KeyUp PLAY，Service丢弃数为0。运行时再次核实四个关键线程相对优先级均为2，诊断线程为0，进程类别为Normal。合并原始样本统计（单位ms）：

| 区间 | P50 | P95 | P99 | 最大 |
|---|---:|---:|---:|---:|
| DLL入口→mock PLAY | 0.165 | 0.360 | 0.539 | 2.240 |
| SED发布→发送线程取出 | 0.020 | 0.110 | 0.159 | 0.200 |
| Pipe原生调用入口→Service入队 | 0.021 | 0.059 | 0.110 | 0.207 |
| Pipe原生调用入口→mock PLAY | 0.035 | 0.108 | 0.197 | 0.591 |
| Service入队→mock PLAY | 0.013 | 0.045 | 0.138 | 0.564 |

DLL入口→Hook这一阶段最大2.177ms；各阶段最大值可能来自不同事件，不能相加。Pipe＋Service超过5ms为0/1,410。旧汇总字段`pipe_receive_to_mock_complete_us`实际从Service入队计时，不包含此前的Pipe传送；本表的Pipe＋Service直接从逐键`native_enter`到`mock_complete`重算。这一轮未开内核轨迹，不能说明最慢事件的精确Windows调度原因；也不能把样本最大值当作未来延迟保证。原始逐键记录和状态见`logs/real-pipeline-highest-default-load4-20261026/results.json`。

回退：C++传`--thread-priority above-normal`，SED设置`LatencyThreadPriority="above-normal"`并重启。无需更改进程类别、电源方案或CPU亲和性。
