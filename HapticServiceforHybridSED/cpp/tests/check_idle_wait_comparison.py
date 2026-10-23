"""Sequential first-key A/B; leave the established fixed-wait default unchanged."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    paths = []
    for mode in ('fixed', 'deadline'):
        label = 'idle-wait-' + mode + '-20261023'
        subprocess.run([sys.executable, str(root / 'tests/check_real_pipeline.py'), '--with-osk',
                        '--quiet', '--sed-trace', '--status-mode', 'interrupt', '--mock-status-delay-ms', '200',
                        '--service-idle-wait', mode, '--idle-wake-count', '20', '--run-label', label],
                       check=True, timeout=120)
        directory = root / 'logs' / ('real-pipeline-' + label)
        paths.append(str(directory))
        data = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
        assert data['final_status']['idle_wait_mode'] == mode
        print(mode + ' all: ' + json.dumps(data['pooled_injection_to_mock_complete_us']), flush=True)
        print(mode + ' idle-first-key: ' + json.dumps(data['first_key_after_idle']['injection_to_mock_complete_us']), flush=True)
    subprocess.run([sys.executable, str(root / 'tests/analyze_latency.py'), *paths,
                    '--output', str(root / 'logs/idle-wait-comparison-20261023.json')],
                   check=True, stdout=subprocess.DEVNULL)
    print('PASS: 2840 presses, including 40 first keys after 1s idle; no physical touch/HID timing.', flush=True)


if __name__ == '__main__':
    main()
