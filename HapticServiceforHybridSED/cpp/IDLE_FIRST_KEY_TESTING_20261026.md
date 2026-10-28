# 空闲首键补测（2026-10-26）

## 边界与方法

新增两轮，各 30 次：KeyDown 前分别空闲 5 秒、15 秒。每轮保持同一会话，正常心跳与健康验证仍运行；不增加 CPU 压力、不绑核，不启用 500Hz 或四键突发。入口为真实 OSK DLL 的 KeyDown/KeyUp，经过 Windows hook、C# HybridSED、真实 Named Pipe 和 C++ Service，终点为显式启用的 mock PLAY 完成。不包含触屏识别、实际 HID/I2C、MCU 或机械起振。

每轮另有 1 个连通性预热按键，不计入测量次数。两轮结束均 ready=true、dropped=0、generation=1，play_sent=31。没有额外的松键 PLAY。旧报告的 5,282 次保持原统计范围，本页新增 60 次；加上旧空闲组，5 秒/15 秒首键累计覆盖 38/34 次。不同轮次不能直接平均分位数。

## 新增结果

单位 ms。每格为 P50 / P95 / 最大值。30 次样本的 P99 等于最大值，不用于估计低概率尾部。

| 每次输入前空闲 | 次数 | Pipe：WriteFile 入口→Service submit | Service：submit→mock 完成 | Pipe＋Service | DLL 入口→mock 完成 |
|---|---:|---:|---:|---:|---:|
| 5 秒 | 30 | 0.025 / 0.048 / 0.054 | 0.035 / 1.158 / 1.985 | 0.060 / 1.178 / 2.017 | 0.300 / 1.690 / 2.408 |
| 15 秒 | 30 | 0.026 / 0.049 / 0.054 | 0.021 / 0.927 / 1.062 | 0.050 / 0.970 / 1.087 | 0.279 / 1.244 / 1.310 |

新增两轮以上各阶段超过 5ms 的样本均为 0。此结果不证明调度长尾已消除，也不替代内核 Ready/Waiting/CSwitch 分析。各阶段分位数、最大值不能直接相加。

## 复现

从工作区根目录执行；保持普通桌面解锁，两条命令依次运行，避免测试服务/管道冲突。每轮约需数分钟，第二轮主要等待时间约 7.5 分钟。

```powershell
py -3 HapticServiceforHybridSED/cpp/tests/check_real_pipeline.py --with-osk --quiet --sed-trace --status-mode interrupt --mock-status-delay-ms 200 --idle-only --idle-wake-count 30 --idle-gap-ms 5000 --run-label idle5s-expanded-20261026
py -3 HapticServiceforHybridSED/cpp/tests/check_real_pipeline.py --with-osk --quiet --sed-trace --status-mode interrupt --mock-status-delay-ms 200 --idle-only --idle-wake-count 30 --idle-gap-ms 15000 --run-label idle15s-expanded-20261026
```

重跑请使用新的 run-label，保留原记录。运行期间不进行每键诊断查询；mock 运行时状态 ACK 延迟设为 200ms，用于保持原测试条件，不是给 PLAY 增加 200ms 延迟。

原始记录（本地 logs 被 Git 忽略）：

- `logs/real-pipeline-idle5s-expanded-20261026/results.json`
- `logs/real-pipeline-idle15s-expanded-20261026/results.json`
- `logs/idle5s-expanded-summary-20261026.json`
- `logs/idle15s-expanded-summary-20261026.json`

本轮未修改 Host/MCU 生产逻辑，未提交、推送或烧录。报告已将 500Hz/突发与日常输入验收分开；更多背景见 [原完整链路测试](UNLOCKED_CHAIN_TESTING_20261026.md)。
