# Highest 默认配置下的长尾频率复测

## 范围与方法

本轮只测主机软件：真实 OSK DLL 按下/松开 → Windows Hook → HybridSED → Named Pipe → C++ Service → mock PLAY。没有触屏、真实 HID/I²C、MCU 或振动。四个关键线程运行时相对优先级均核实为 `2`（Highest），两个进程仍为 Normal；没有绑核或修改电源方案，结束时活动方案为平衡。

每个完整批次包含五轮同样的输入节奏：交替快速输入/短空闲、100 Hz、有无并发诊断查询、250 ms 和 1 秒空闲后的首键；每轮 470 次 KeyDown 和对应 KeyUp。所有完整批次零丢失、额外或 KeyUp PLAY，均在同一 Service 会话内运行。`--mock-status-delay-ms 200` 只延迟模拟状态 ACK，不代表 PLAY 传输时间。

三次新完成的批次：

| 批次 | 四个计算进程 | 按下次数 | Pipe＋Service >2 ms | >5 ms | Pipe＋Service P99 / 最大 | DLL→mock P99 / 最大 |
|---|---:|---:|---:|---:|---:|---:|
| `highest-rate-load4-5cycles-20261026` | 是 | 2,350 | 3 | 0 | 0.707 / 3.513 ms | 0.935 / 4.071 ms |
| `highest-rate-load0-5cycles-20261026` | 否 | 2,350 | 0 | 0 | 0.099 / 0.288 ms | 0.401 / 0.893 ms |
| `highest-rate-load4-repeat-20261026` | 是 | 2,350 | 0 | 0 | 0.131 / 0.710 ms | 0.474 / 21.062 ms |

本轮新增共 7,050 次。之前同配置、同节奏的完整批次另有四进程 1,410 次和普通负载 470 次。因此按条件汇总：四进程 **0/6,110 次 Pipe＋Service >5 ms，3/6,110 次 >2 ms（观测比例 0.049%）**；普通负载 **0/2,820 次 >5 ms**。所有 8,930 次合计为 0 次 Pipe＋Service >5 ms，但两种负载的比例应分别理解。

其中“Pipe”严格为逐键 `native_enter → service_submit`，“Service”为 `service_submit → mock_complete`，“Pipe＋Service”为同一键 `native_enter → mock_complete`。旧 JSON 字段 `pipe_receive_to_mock_complete_us` 实际只包含 Service 区间。四进程条件下合并的 Pipe P99 / 最大为 **0.162 / 3.064 ms**，Service 为 **0.141 / 1.401 ms**，Pipe＋Service 为 **0.299 / 3.513 ms**。最慢两条 Pipe＋Service 事件分别为 3.513 ms（Pipe 2.112、Service 1.401）和 3.227 ms（Pipe 3.064、Service 0.163），都在第一轮压力批次的空闲首键场景。各阶段的独立最大值不能相加。

第二轮压力批次有一条 **DLL→mock 21.062 ms**，位于 1 秒空闲后的首键；逐键拆分显示 `inject→hook` **20.968 ms**，Hook→发布 0.024 ms、SED 队列 0.025 ms、Pipe 0.017 ms、Service 0.021 ms，其余小量为写入准备。没有这条事件的同轮内核轨迹，不能进一步确认 DLL 调用、Windows 输入投递与线程调度各占多少。它证明 Highest 没有消除整条主机链路的长尾；不能把这 21.062 ms 归因于 Pipe 或 Service。

## 概率能说到什么程度

在本机、这些配置和输入节奏下，**经验频率**是上述计数。若额外假定每次按键独立、长尾概率固定且测试能代表未来运行，则零事件时单侧 95% 二项上界为 `1 - 0.05^(1/n)`：四进程 6,110 次约 **0.049%**，普通负载 2,820 次约 **0.106%**。相邻按键和同轮事件可能共享一次调度拥堵，机器负载也会变化，因此这些数字不能作为实际部署的概率保证或最大延迟承诺。

四进程条件下整条 DLL→mock 链路 >5 ms 为 **1/6,110（0.016% 的样本比例）**，其唯一事件主要发生于注入到 Hook；Pipe＋Service 没有 >5 ms。真实设备增加的 HID/I²C、MCU 和振动时延尚未测量。

## 原始数据与复现

上述三次完整结果在 `logs/real-pipeline-<批次名>/results.json`，逐键时间在 `sed_stage_records`。此前两个批次分别是 `highest-default-load4-20261026` 和 `highest-production-defaults-20261026`。压力组命令：

```powershell
py -3 HapticServiceforHybridSED/cpp/tests/check_real_pipeline.py --with-osk --quiet --sed-trace --default-priorities --status-mode interrupt --mock-status-delay-ms 200 --scenario-set cadence --cadence-cycles 5 --load-workers 4 --run-label highest-rate-load4-5cycles-20261026
```

普通负载将 `--load-workers 4` 改为 `--load-workers 0`，并改用独立 `--run-label`。最初尝试单进程十轮时，4096 条 SED 诊断记录在完成 4,060 次后耗尽；保留其 `results-failed.json`，**未将该未完成运行计入上述完整批次**。脚本已改成 `--sed-trace` 与 cadence 同用时最多允许八轮，启动前拒绝超限参数。
