"""Named Pipe/Service stage measurements under rate, burst, query and CPU pressure."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    paths = []
    for workers in (0, 4, 8):
        label = f'host-stages-load{workers}-20261023'
        print('Running ' + label, flush=True)
        subprocess.run([sys.executable, str(root / 'tests/check_real_pipeline.py'), '--with-osk',
                        '--quiet', '--sed-trace', '--status-mode', 'interrupt', '--mock-status-delay-ms', '200',
                        '--extra-scenarios', '--load-workers', str(workers), '--run-label', label],
                       check=True, timeout=120)
        directory = root / 'logs' / ('real-pipeline-' + label)
        data = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
        assert data['final_status']['generation'] == 1 and data['final_status']['dropped'] == 0
        assert data['total_measured_presses'] == 1600
        paths.append(str(directory))
        print(json.dumps({'workers': workers, 'total_us': data['pooled_injection_to_mock_complete_us']}), flush=True)
    summary = root / 'logs/host-stages-20261023.json'
    subprocess.run([sys.executable, str(root / 'tests/analyze_latency.py'), *paths, '--output', str(summary)],
                   check=True, stdout=subprocess.DEVNULL)
    print('PASS: 4800 measured presses, detailed Service stages; physical HID/I2C not measured.', flush=True)


if __name__ == '__main__':
    main()
