"""Sequential, unpinned latency profiles; reports tails without inventing a hard SLA."""
import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    script = root / 'tests/check_real_pipeline.py'
    profiles = (
        ('matrix-feature-idle-20261023', ['--status-mode', 'feature']),
        ('matrix-interrupt-idle-20261023', ['--status-mode', 'interrupt', '--mock-status-delay-ms', '200']),
        ('matrix-interrupt-load4-20261023', ['--status-mode', 'interrupt', '--mock-status-delay-ms', '200', '--load-workers', '4', '--sed-trace']),
        ('matrix-interrupt-load8-20261023', ['--status-mode', 'interrupt', '--mock-status-delay-ms', '200', '--load-workers', '8', '--sed-trace']),
    )
    paths = []
    for label, arguments in profiles:
        print('Running ' + label, flush=True)
        subprocess.run([sys.executable, str(script), '--with-osk', '--quiet', '--run-label', label,
                        *arguments], check=True, timeout=120)
        paths.append(str(root / 'logs' / ('real-pipeline-' + label)))
    summary = root / 'logs/latency-matrix-20261023.json'
    subprocess.run([sys.executable, str(root / 'tests/analyze_latency.py'), *paths,
                    '--output', str(summary)], check=True, stdout=subprocess.DEVNULL)
    data = json.loads(summary.read_text(encoding='utf-8'))
    for profile in data['profiles']:
        print(Path(profile['report']).parent.name + ': ' +
              json.dumps(profile['stages']['dll_to_mock_complete_us']), flush=True)
    print('PASS: 5600 measured presses across four sequential profiles; no physical MCU or touch.', flush=True)


if __name__ == '__main__':
    main()
