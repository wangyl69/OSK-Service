# Highest与减少停放的独立配对验证

## 已完成与恢复

新SED＋新Service共七轮、每轮470次，合计3,290次真实DLL→Hook/SED→Named Pipe→C++ mock PLAY。全部零丢弃、零额外或KeyUp PLAY。进程维持Normal，诊断线程Normal，没有绑核、实时优先级或MCU测试。

测试前后实际二进制SHA256一致；测试运行时验证SED Hook/发送线程、Service Pipe/HID线程的相对优先级均符合请求：AboveNormal=1、Highest=2，Service进程类别32。原平衡方案381b4222-f694-41f0-9685-ff5bb260df2e已恢复，临时副本已删除，已现场核对powercfg /list仅剩原方案。

## 为什么这样对照

线程优先级影响Ready线程之间的执行顺序，停放策略影响CPU电源管理选择；同时修改会无法区分改善来自哪项。

* 第1–4轮：原电源方案，AboveNormal→Highest→Highest→AboveNormal。
* 第4–7轮：AboveNormal固定，原方案→减少停放→减少停放→原方案。
* 两个ABBA对照共享第4轮，所以每组对照各1,880次，但实际总数为3,290，而非3,760。
* 每组每个条件940次；输入/暂停/250ms和1秒首键、状态诊断、200ms异步mock ACK场景一致，全程Power＋调度profile及CPU快照启用。每轮独立进程、一次预热不计入470；每轮内部没有反复Session Open/Close。

临时“性能”策略具体是复制原方案，只将两类处理器最低未停放比例（CPMINCORES与CPMINCORES1）的AC/DC值设为100%。没有修改EPP、频率上下限或原方案本身；它是针对停放的实验，不是笼统套用高性能方案。快照实际未停放数量20，而不是宣称22个始终未停放。

七轮前后供电状态均为电池（ACLineStatus=0），未发现供电来源切换；边界采样不能排除瞬时未采到的变化。电量从约97%下降到91%，但其中包含整个测试、WPR和后台活动，不能据此定量比较两个设置的耗电。未测温度、风扇或全系统交互延迟，因此不宣称没有系统影响。

## 结果

单位ms，各行940次，直接合并原始样本计算，不平均两轮百分位。

| 对照条件 | Pipe＋Service P99 / 最大 | DLL→mock P99 / 最大 | Pipe＋Service >5ms |
|---|---:|---:|---:|
| 原方案＋AboveNormal（优先级对照） | 1.825 / 6.699 | 2.792 / 7.069 | 1 |
| 原方案＋Highest | 0.419 / 1.608 | 1.256 / 4.039 | 0 |
| 原方案＋AboveNormal（电源对照） | 1.836 / 10.791 | 2.772 / 10.954 | 1 |
| 减少停放＋AboveNormal | 0.290 / 0.379 | 1.311 / 1.986 | 0 |

两个“原方案＋AboveNormal”行来自不同基线轮次，不能合并成同一对照值。个别阶段最大值来自不同事件，不能相加构造全链路最大值。

| 对照条件 | SED队列 P99 / 最大 | Pipe P99 / 最大 | Service P99 / 最大 |
|---|---:|---:|---:|
| 原方案＋AboveNormal（优先级） | 0.958 / 3.077 | 0.192 / 3.110 | 1.769 / 3.589 |
| 原方案＋Highest | 0.541 / 3.862 | 0.187 / 0.757 | 0.205 / 0.944 |
| 原方案＋AboveNormal（电源） | 0.941 / 3.077 | 0.258 / 0.622 | 1.769 / 10.759 |
| 减少停放＋AboveNormal | 0.204 / 0.258 | 0.194 / 0.239 | 0.195 / 0.242 |

Highest并未使所有区间最大值都下降：SED队列最大3.862ms高于对应基线3.077ms。因此不能把Service改善概括成所有环节均改善，更不能声称硬实时保证。

## 同轮内核佐证

七轮ETL均成功解码CSwitch/ReadyThread、记录丢失数均0。在每轮最慢Pipe＋Service事件中，Ready到首次运行分别为：

| 轮次 | 设置 | Pipe＋Service ms | 该事件Ready→首次运行 ms |
|---|---|---:|---:|
| 1 | 原方案 AboveNormal | 6.699 | 4.1552 |
| 2 | 原方案 Highest | 1.010 | 0.9214 |
| 3 | 原方案 Highest | 1.608 | 0.8848 |
| 4 | 原方案 AboveNormal | 2.159 | 2.0938 |
| 5 | 减少停放 AboveNormal | 0.379 | 0.1350 |
| 6 | 减少停放 AboveNormal | 0.331 | 0.1272 |
| 7 | 恢复原方案 AboveNormal | 10.791 | 10.7412 |

最后一列是最慢Pipe事件关联的Ready区间，不是该轮全部Ready等待的最大值或P99；Ready可能先于本键入队，也可能在首次运行后再被抢占，不能将Ready时间直接当本键Service全部耗时。

原策略快照未停放数量在不同轮有2–14个的波动；减少停放的两轮所有批次快照均20个。与此前观察的少量活跃CPU吻合，且回切原策略后又出现长Ready等待。支持“停放/电源调度选择值得关注、Highest有改善候选价值”，但没有控制全部后台负载、温度和电量漂移，每条件仅两轮940次，不认定唯一根因、统计显著性或永久消除长尾。

## 配置和影响

C++可选：`--thread-priority normal|above-normal|highest`，默认保持above-normal。安装服务时原有安装参数传递路径同样可携带highest；本轮只验证console/mock运行，没有重装生产服务。

SED可选：`--Dispatcher:LatencyThreadPriority=highest`；`LatencyThreadPriorityBoost=true`时应用该档位，false继续强制Normal以保持旧配置语义。默认above-normal。只改两个专用线程，不提升进程和线程池。

Highest在Normal进程中把基础优先级从9提高到10；对短时工作且阻塞等待的关键线程是可验证的选项，本轮没有观察到数据通道功能失败，但并未全面测试其他应用响应。因此保留为可选档，不自动修改用户默认配置，也不把所有线程/进程提高。

减少停放可增加活跃核心、电池消耗、温度和风扇噪声，本轮不据百分比电量变化作功耗定量结论，也不默认将其设为长期全机策略。结束后已恢复原配置。两种优化没有在本轮同时启用，不能声称组合效果等于两者相加。

## 复现和原始文件

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File HapticServiceforHybridSED/cpp/tools/Compare-PowerPriority.ps1 -Elevate
```

脚本保存原方案、创建自己的临时GUID，在finally恢复并核验原方案，之后删除临时副本。不要强制杀死管理员脚本；强制终止或系统掉电可能跳过finally。原方案GUID和临时GUID在首次修改前写入manifest，便于恢复。执行期间应保持桌面解锁和供电来源不变。

本轮外层目录：`logs/power-priority-20261026-153511/`，manifest包含七轮capture路径、供电边界、恢复状态。`binary-hashes.json`、原始/临时处理器策略全文、逐轮日志均保留。各capture下有ETL、结果、CPU快照及kernel-correlated-events.json。

[完整分段CSV](POWER_PRIORITY_STAGES_20261026.csv)单位µs；两个ABBA统计由 `tests/analyze_power_priority.py`生成并检查恢复状态、有效优先级、计数和供电来源。

修改后C#构建0警告/0错误、58项测试通过；C++构建及CTest5/5通过，实际Highest线程设置也在完整链路中验证。仍为mock，不含真实HID/I²C/MCU与振动。本轮未提交/推送。
