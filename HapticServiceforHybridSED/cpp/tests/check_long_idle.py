"""Targeted longer input-idle gaps; do not repeat the throughput baseline."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    paths = []
    for gap, count in ((5000, 10), (15000, 6)):
        label = f'long-idle-{gap}ms-20261023'
        print(f'Running {count} first keys after {gap}ms input-idle gaps', flush=True)
        subprocess.run([sys.executable, str(root / 'tests/check_real_pipeline.py'), '--with-osk',
                        '--quiet', '--sed-trace', '--status-mode', 'interrupt', '--mock-status-delay-ms', '200',
                        '--idle-only', '--idle-wake-count', str(count), '--idle-gap-ms', str(gap),
                        '--run-label', label], check=True, timeout=180)
        directory = root / 'logs' / ('real-pipeline-' + label)
        data = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
        state = data['final_status']
        assert state['ready'] and state['generation'] == 1 and state['verify_count'] > 0
        assert state['play_sent'] == count + 1 and state['dropped'] == 0
        print(json.dumps({'gap_ms': gap, 'count': count,
                          'latency_us': data['pooled_injection_to_mock_complete_us'],
                          'verify_count': state['verify_count'], 'generation': state['generation']}), flush=True)
        paths.append(str(directory))
    subprocess.run([sys.executable, str(root / 'tests/analyze_latency.py'), *paths,
                    '--output', str(root / 'logs/long-idle-summary-20261023.json')],
                   check=True, stdout=subprocess.DEVNULL)
    print('PASS: 16 longer-idle first keys; session and periodic asynchronous verification retained. No real MCU.', flush=True)


if __name__ == '__main__':
    main()
