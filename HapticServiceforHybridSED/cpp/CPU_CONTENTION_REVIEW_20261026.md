# 新版慢事件：优先级、CPU线程上下文与中断复核

本轮离线分析已有同轮ETL，没有再次注入按键或修改生产代码、优先级、电源策略、绑核设置。对象为 `logs/real-pipeline-schedule-20261026-145114-4772c632/` 的三个慢事件。

## 新增解析

`EtlSchedule`增加CSwitch优先级、ISR/DPC/TimerDPC/ThreadedDPC起止区间，以及每个分析窗口开始前所有已知CPU的最后一次切换状态。这样可以区分“窗口没有切换”与“CPU状态未知”，不再用其他不连续窗口中的事件推测当前窗口。

`analyze_cpu_contention.py`按CPU恢复线程上下文区间，并计算中断区间并集，避免嵌套ISR/DPC重复相加。线程上下文包含中断时间，不能把两者再相加当CPU总耗时；没有CSwitch状态的CPU保留未知。ETL解码事件丢失数仍为0。

## 实际观察

| Pipe＋Service | Ready→切入 | 切入时工作线程动态优先级 | 最终切入CPU | 该CPU中断并集 | 该CPU最长单次中断重叠 |
|---|---:|---:|---:|---:|---:|
| 28.465ms | 72.3652ms | 9 | 10 | 1.1913ms | 0.1384ms |
| 6.336ms | 6.2715ms | 10 | 10 | 0.2877ms | 0.0398ms |
| 6.221ms | 6.1625ms | 10 | 10 | 0.3355ms | 0.0731ms |

72.3652ms包含本键入队之前的Ready等待，不是本键端到端延迟。以上中断统计区间为Ready到第一次运行；最终切入CPU不是线程唯一允许使用的CPU，不将这些数字扩展为全系统因果归因。

28.465ms事件的Ready等待区间内，CPU10在Code.exe动态优先级9的线程上下文约58.041ms，其余时段有Code.exe优先级10–15、音频进程优先级22、桌面进程优先级15等。6.336ms事件中，该CPU的Code.exe优先级11/12/15上下文合计约3.977ms。6.221ms事件中，NVIDIA Overlay优先级11/12上下文约2.069ms，也有Code、桌面和系统线程。

这些是调度现场数据，不是判定某进程错误或独自造成长尾。工作线程优先级9/10为切入那一刻的值，不等于整段Ready期间每一刻的值；本profile没有记录完整优先级变更事件，不能还原完整动态优先级曲线。

在全部已解析CPU上，28.465ms事件窗口中最长单次中断重叠约0.3906ms；未发现一笔几十毫秒ISR/DPC，也没有证据支持将该事件主要归因于一次长中断。不能因此排除所有驱动、电源和调度影响。

## 尚不能确定的关键条件

忙碌主要集中于CPU10/12等少数CPU；不少其他CPU的最新线程上下文为Idle。Idle线程上下文不代表该CPU当时未停放、已上电或符合工作线程的有效CPU集合要求，不能据此说调度器“明明有空核却故意不分配”。

现场只读查询：Intel Core Ultra7 155H、16物理核/22逻辑处理器；当前平衡电源方案；当前工具PowerShell进程亲和掩码为4194303（22位均开放）。这是采集后的查询，不能代替被测Service当时的线程CPU集合、CPU停放状态或功耗/QoS限制记录。

因此目前证据支持：通知迅速、队列锁迅速，实际执行等待占主导，并且有同/更高动态优先级线程活动；尚不足以说整机CPU饱和或确认某个后台进程是唯一根因。不因看到CPU10就绑核，不关闭用户后台进程，不提升至实时优先级。

## 下一步最小实验

下一次新版采集优先补完整优先级变更、CPU停放/频率/Idle电源状态，以及被测线程的有效CPU集合/亲和掩码与功耗QoS。保留当前优先级和系统策略，先确认工作线程在Ready期间究竟可在哪些CPU上执行。只有补齐这些证据后，才决定是否值得做线程调度策略或电源模式的可逆配对验证。

不以继续累计相同按键样本替代上述缺失信息，也不把“没有一笔长DPC”解释为“问题已解决”。

## 复现

```powershell
dotnet run --project HapticServiceforHybridSED/cpp/tools/EtlSchedule -- HapticServiceforHybridSED/cpp/logs/real-pipeline-schedule-20261026-145114-4772c632
py -3 HapticServiceforHybridSED/cpp/tests/correlate_kernel_waits.py HapticServiceforHybridSED/cpp/logs/real-pipeline-schedule-20261026-145114-4772c632
py -3 HapticServiceforHybridSED/cpp/tests/analyze_cpu_contention.py HapticServiceforHybridSED/cpp/logs/real-pipeline-schedule-20261026-145114-4772c632
```

`cpu-contention.json`为逐CPU上下文和中断统计，保存在本地忽略的logs目录；脚本/工具源代码可提交。工具构建与Python语法检查通过，原始QPC用于同钟对齐的编译器提示保留。本轮未提交/推送。
