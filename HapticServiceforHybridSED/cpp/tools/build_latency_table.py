"""Export measured host stages plus the user's T0..T5 reference boundaries."""
import csv
import json
from pathlib import Path


def ms(value):
    return '<0.001' if value == 0 else f'{value / 1000:.3f}'


def main():
    root = Path(__file__).resolve().parents[1]
    full = json.loads((root / 'logs/host-stages-20261023.json').read_text(encoding='utf-8'))
    isolated = json.loads((root / 'logs/pipe-stages-summary-20261023.json').read_text(encoding='utf-8'))
    baseline = next(p for p in full['profiles'] if p['unrestricted_cpu_load_workers'] == 0)
    labels = {
        'dll_to_pipe_write_enter_us': 'DLL入口→管道WriteFile入口（含Windows/SED）',
        'sed_hook_to_pipe_write_us': 'SED hook→管道WriteFile入口',
        'native_to_service_submit_us': 'Named Pipe：WriteFile入口→Service submit',
        'service_queue_us': 'Service：submit→工作线程取出',
        'service_prepare_us': 'Service：取出→trigger入口（校验等）',
        'service_build_and_mock_send_us': 'Service：构造报告/CRC→mock提交完成',
        'submit_to_mock_complete_us': 'Service总计：submit→mock完成',
        'pipe_write_to_mock_complete_us': 'Named Pipe＋Service：WriteFile入口→mock完成',
        'dll_to_mock_complete_us': 'DLL入口→mock完成（软件代理全段）',
    }
    lines = ['# Haptic Latency：当前分段实测表（2026-10-23）', '',
             '共新增 9600 次：4800 次真实 OSK DLL→SED→Pipe→C++ Service，另 4800 次独立 Pipe→Service。',
             '两类均按 0/4/8 个无绑核计算进程各 1600 次，所有请求零丢弃、各组 generation=1。',
             '包含 20/100/200/500Hz、四键/四字节突发、并发状态/诊断和持续输入。',
             'interrupt 状态反馈模拟延后 200ms；真实 HID/I2C、MCU 和振动均未测。', '',
             '## 按原图保留的 T0～T5 边界', '',
             '| 测量点 | 阶段 | 测量方式 | 当前数据 | 状态 |',
             '|---|---|---|---|---|',
             '| T0 | 手指接触/传感器响应 | 原图 Touch Sensor＋LED | 原图约8ms | 用户提供，本轮未复测 |',
             '| T1 | OSK Typing确认 | 原图高速摄像 | 原图T0→T1为25～33ms | 用户提供；代码确认点尚未实测 |',
             '| T2 | Named Pipe到达Service | 同机QPC、真实管道 | 见下表管道段；真实T1→T2尚不能给出 | 本轮软件实测 |',
             '| T3 | Haptic Service处理并提交 | 同机QPC、逐事件FIFO关联 | 见下表Service段；终点是mock完成 | 软件实测，实际HID写完成待测 |',
             '| T4 | MCU收到/触发PLAY | MCU日志及协议序号/外部同步 | 本轮无数据；原图10～22ms仅为旧估计 | 待硬件验证 |',
             '| T5 | 有效设备振动 | 加速度计/外部同步 | 原图T4→T5约8ms（波形峰值） | 用户提供，本轮未复测 |',
             '| Tx | 相邻有效触发间隔 | 触摸与振动同步采集 | 尚未确认T0→T5＜Tx | 待验证 |', '',
             '若T1来自屏幕高亮/文字显示，它与触觉分支可能并发，不能把显示时间作为严格串行前置点相加。',
             '本机DLL入口记为T1′代理点；未包含原始触摸、OSK算法确认及其之前的声音等处理。', '',
             '## 正常负载、真实OSK DLL/SED链路：1600次', '',
             '单位 ms。0微秒计数显示为 <0.001，不表示零耗时；时间戳取整到微秒。', '',
             '| 阶段 | P50 | P95 | P99 | 本轮最大 |', '|---|---:|---:|---:|---:|']
    for key, label in labels.items():
        metric = baseline['stages'][key]
        lines.append('| ' + label + ' | ' + ' | '.join(ms(metric[k]) for k in ('p50', 'p95', 'p99', 'max')) + ' |')
    lines += ['', 'Service的排队段包含submit侧锁等待、入队及工作线程调度；不应把它称作CPU一直在处理。',
              'Named Pipe段包含内核交付、接收线程唤醒及少量解析，不是单纯数据复制耗时。',
              'WriteFile API本身的持续时间可能与接收端处理重叠，不另外相加。各阶段分位数/最大值也不能相加。', '',
              '## CPU压力下的真实链路', '',
              '| 计算进程数 | Pipe P99/最大 | Service P99/最大 | Pipe＋Service P99/最大 | DLL→mock P99/最大 |',
              '|---:|---:|---:|---:|---:|']
    for p in full['profiles']:
        keys = ('native_to_service_submit_us', 'submit_to_mock_complete_us', 'pipe_write_to_mock_complete_us', 'dll_to_mock_complete_us')
        cells = [f"{ms(p['stages'][k]['p99'])} / {ms(p['stages'][k]['max'])}" for k in keys]
        lines.append('| ' + str(p['unrestricted_cpu_load_workers']) + ' | ' + ' | '.join(cells) + ' |')
    lines += ['', '负载更大时某些样本更快，不代表增加计算负载能优化产品；这不是控制电源状态后的因果实验。', '',
              '## 独立管道隔离测试', '', '| 计算进程数 | Pipe P99/最大 | Service P99/最大 | Pipe＋Service P99/最大 |',
              '|---:|---:|---:|---:|']
    for p in isolated['profiles']:
        keys = ('named_pipe_delivery', 'service_total', 'write_to_mock_complete')
        cells = [f"{ms(p['stages_us'][k]['p99'])} / {ms(p['stages_us'][k]['max'])}" for k in keys]
        lines.append('| ' + str(p['workers']) + ' | ' + ' | '.join(cells) + ' |')
    lines += ['', '独立管道测试使用Python调用真实Win32 overlapped WriteFile，不经过OSK或SED；与C#发送器数据分开报告。',
              '四字节突发只用于隔离管道，不能冒充OSK四次独立WriteFile。', '',
              '## 证据和限制', '',
              '* 原始完整链路：[host-stages-20261023.json](logs/host-stages-20261023.json)。',
              '* 独立管道：[pipe-stages-summary-20261023.json](logs/pipe-stages-summary-20261023.json)。',
              '* CSV：[HOST_LATENCY_STAGES.csv](HOST_LATENCY_STAGES.csv)。',
              '* 全部为逐事件原始时序计算；未删除最慢样本。附加Service诊断仅在mockTrace开启时采集。',
              '* trace序号是Host诊断环序号，不等同于MCU协议序号。无MCU时不能给实际T3→T4或T4→T5。',
              '* 并发诊断客户端会频繁请求诊断/缓存状态，但实际诊断读取仍合并并限频，不是每个请求都访问MCU。',
              '* 图底要求的“先于文字注入的独立OSK触觉入口”尚未实现：当前从SendInput后的SED hook派生。',
              '* 初次键盘集成曾被SendInput WinError5拒绝；普通桌面复核后重试通过，再执行以上完整链路。',
              '* 内核ETL未取得，不能把应用时间空档精确归因于某个调度步骤、DPC或驱动。', '']
    (root / 'HOST_LATENCY_TABLE.md').write_text('\n'.join(lines), encoding='utf-8')
    with (root / 'HOST_LATENCY_STAGES.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(['source', 'load_workers', 'stage', 'samples', 'p50_ms', 'p95_ms', 'p99_ms', 'max_ms'])
        for p in full['profiles']:
            for key, label in labels.items():
                m = p['stages'][key]
                writer.writerow(['OSK_DLL_SED', p['unrestricted_cpu_load_workers'], label, m['samples'],
                                 *[f'{m[k]/1000:.3f}' for k in ('p50', 'p95', 'p99', 'max')]])
        for p in isolated['profiles']:
            for key, m in p['stages_us'].items():
                writer.writerow(['DIRECT_PIPE', p['workers'], key, m['samples'],
                                 *[f'{m[k]/1000:.3f}' for k in ('p50', 'p95', 'p99', 'max')]])
    print('Saved HOST_LATENCY_TABLE.md and HOST_LATENCY_STAGES.csv')


if __name__ == '__main__':
    main()
