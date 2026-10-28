# 全主机链路分场景验证

## 范围和判据

本轮关注全部主机阶段：DLL侧注入打点→Hook、Hook→发布、SED队列、写入准备、Named Pipe、Service队列、mock发送，以及Pipe＋Service和总耗时。2 ms为本轮尾部观察阈值，5 ms为捕获分析阈值；不将样本最大值当作未来上限。生产进程Normal，四个关键线程Highest；测试发送线程Normal，不绑核、不修改电源策略。

七组分别启动，避免只观察压力环境。每组有一个不计入统计的预热按键；500次的组分五批，各批100次，组内Service会话连续保持，不反复Open/Close。所有完整链路组均有同轮内核/Power轨迹。测试真实OSK DLL、Windows Hook、SED和Named Pipe，后端明确使用mock PLAY；未验证触屏识别、真实HID/I²C、MCU和振动。状态反馈配置interrupt，200 ms模拟ACK延迟只用于验证异步状态反馈与PLAY并行。

## 分组结果

下表单位ms，数值为该组的最大值，**每列可能来自不同按键，不能相加**。

| 场景 | 次数 | DLL→Hook | SED队列 | Pipe | Service队列 | Pipe＋Service | 总耗时 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 普通20 Hz，每50 ms一键 | 500 | 0.905 | 0.704 | 0.427 | 0.303 | 0.467 | 1.878 |
| 连续100 Hz，每10 ms一键 | 500 | 1.097 | 0.275 | 0.269 | 0.550 | 0.622 | 1.212 |
| 100 Hz＋并发诊断查询 | 500 | 1.312 | 0.247 | 0.265 | 0.211 | 0.363 | 1.635 |
| 100 Hz＋四个计算进程 | 500 | 0.389 | 0.449 | 0.246 | 0.236 | 0.312 | 0.748 |
| 四键一组，键间10 ms、组间100 ms | 500 | 1.506 | 0.460 | 0.293 | 0.251 | 0.403 | 1.646 |
| 空闲5秒后的首键 | 10 | 0.311 | 0.263 | 0.111 | 0.127 | 0.210 | 0.583 |
| 空闲15秒后的首键 | 6 | 0.184 | 0.282 | 0.137 | 0.075 | 0.168 | 0.723 |

共2,516次，所有阶段及总耗时均无>2 ms或>5 ms事件；零丢失、额外/KeyUp PLAY、Service丢弃。七组ETL均已解码，EventsLost全部为0。首键样本仅10/6次，是覆盖检查，不能据此评估低概率长尾。不同组运行时间、CPU/后台活动不同，不能因本次压力组更快就认定压力能改善延迟。

Pipe＋Service的P99/最大值分别为：普通0.271/0.467、连续0.294/0.622、诊断并发0.260/0.363、压力0.264/0.312、分组输入0.248/0.403、5秒首键0.210/0.210、15秒首键0.168/0.168 ms。小样本首键的P99实质等于样本最大值。

其他阶段的本轮最大值：Hook→发布0.340 ms，SED出队→Pipe调用前0.322 ms，Service整体0.551 ms，Service出队→mock完成0.061 ms。完整P50/P95/P99/最大值和>2/>5 ms计数见[分阶段CSV](HOST_SCENARIO_STAGES_20261027.csv)。

## 独立Pipe／Service验证

另外单独测试真实Named Pipe写入→Service→mock，排除DLL/Hook/SED输入端。普通/四进程各1,400次，Service运行时Highest已核实。均无丢弃，且全部测量区间无>2 ms或>5 ms。此项未开启内核录制，与上述完整链路的不同采集开销不能直接作因果比较。

| 独立场景 | 次数 | Pipe P99/最大 | Service P99/最大 | Pipe＋Service P99/最大 |
|---|---:|---:|---:|---:|
| 普通负载 | 1,400 | 0.183/0.675 | 0.096/0.655 | 0.218/0.705 |
| 四进程压力 | 1,400 | 0.147/0.249 | 0.104/0.283 | 0.194/0.351 |

该独立测试含20/100/200 Hz、并发诊断及持续输入批次，没有开启500 Hz或单次四字节过载选项。它的样本不与完整链路相加用于DLL路径概率估计。

## 工具改进和原始证据

* `tests/host_latency_stages.py` 统一阶段边界；服务排队和模拟发送单独记录，同时保留Pipe＋Service和总耗时。
* `--stop-any-stage-ms 5`／`Capture-Scheduling.ps1 -StopAnyStageMs 5`检查任一阶段和总耗时，不再只看DLL→Hook。分场景测试在完成一个批次后停止后续批次；若触发，保留该批全部事件。此次未触发。
* 离线解码窗口增加总耗时最慢样本及所有总耗时>2 ms样本，覆盖SED队列等中间阶段。用此前真实的Service队列28.421 ms事件回查，正确检出Service队列、Service整体、Pipe＋Service及总耗时超限。
* `tests/analyze_host_scenarios.py`逐场景导出CSV、慢事件时间戳和内核丢失检查。没有把同一个键跨多个阶段的超限重复算成多个独立按键。

原始完整链路目录：`logs/host-scenarios-20261027-100747/manifest.json`列出七组capture；外层`stage-summary.json`、`stages.csv`和`slow-events.json`保留汇总，各capture的`results.json`、`scheduler.etl`、`kernel-thread-events.json`保留原始与解码证据。独立测试为`logs/pipe-stages-summary-highest-separated-20261027.json`及两个`pipe-stages-load*-highest-separated-20261027/`目录。

复现：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File HapticServiceforHybridSED/cpp/tools/Run-HostScenarios.ps1 -Elevate
py -3 HapticServiceforHybridSED/cpp/tests/check_pipe_stage_matrix.py --workers 0 4 --run-label highest-separated-20261027
```

应改用新的run-label保存新的独立Pipe批次，以免覆盖已有证据。内核解码和汇总在输入测试结束后进行，避免解码负载影响被测输入。

本轮覆盖了多种温态使用方式，结果支持保留当前配置；不能证明Windows今后所有时刻都没有长尾。此前21 ms与其他历史慢事件仍作为未解决的证据保留。启动、断线重连、FAULT恢复属于独立生命周期场景，不能把其等待时间当成正常温态PLAY时延；本轮没有将这些时间纳入上述表格。
