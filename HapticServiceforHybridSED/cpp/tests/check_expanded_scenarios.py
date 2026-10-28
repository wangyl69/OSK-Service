"""Fixed-sample repeated all-stage campaign; QPC tracing, no kernel recorder.

Keeps failures and repetitions separate. Does not stop on latency thresholds,
so the planned denominator is not censored by an early slow event.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, choices=range(1, 6), default=2)
    parser.add_argument('--batches', type=int, choices=range(1, 31), default=5)
    parser.add_argument('--idle-samples', type=int, choices=range(1, 101), default=20)
    parser.add_argument('--skip-idle', action='store_true', help='Supplement continuous-input samples without repeating long idle waits')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    label = 'expanded-scenarios-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    output = root / 'logs' / label
    output.mkdir(exist_ok=False)
    profiles = [('typing', 0), ('rapid', 0), ('rapid200', 0), ('diagnostics', 0),
                ('rapid', 4), ('rapid200', 4), ('bursts', 0)]
    # Reverse the second repetition's order to reduce a fixed order effect.
    plan = [(name, workers, repeat) for repeat in range(1, args.repeats + 1)
            for name, workers in (profiles if repeat % 2 else profiles[::-1])]
    if not args.skip_idle:
        plan += [('idle5s', 0, 1), ('idle15s', 0, 1)]
    manifest = dict(scope='Actual DLL/SED/Pipe to mock PLAY, all software stages; no touch/HID/MCU',
                    kernel_trace=False, fixed_sample_count=True,
                    planned_presses=len(profiles)*args.repeats*args.batches*100 + (0 if args.skip_idle else 2*args.idle_samples),
                    runs=[])
    for number, (scenario, workers, repeat) in enumerate(plan, 1):
        run_label = f'{label}-r{number}-{scenario}-load{workers}'
        command = [sys.executable, str(root / 'tests/check_real_pipeline.py'), '--with-osk', '--quiet',
                   '--sed-trace', '--default-priorities', '--status-mode', 'interrupt',
                   '--mock-status-delay-ms', '200', '--scenario-set', scenario,
                   '--load-workers', str(workers), '--run-label', run_label]
        if scenario.startswith('idle'):
            command += ['--idle-samples', str(args.idle_samples)]
        else:
            command += ['--scenario-batches', str(args.batches)]
        entry = dict(round=number, scenario=scenario, workers=workers, repeat=repeat, command=command,
                     captures=[str(root / 'logs' / ('real-pipeline-' + run_label))])
        manifest['runs'].append(entry)
        print(f'RUN {number}/{len(plan)} {scenario} workers={workers} repeat={repeat}', flush=True)
        try:
            with (output / f'round-{number}.txt').open('w', encoding='utf-8') as stream:
                entry['exit_code'] = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT).returncode
            if entry['exit_code']:
                raise RuntimeError(f'Scenario failed; retained evidence: {output}')
        finally:
            (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    subprocess.run([sys.executable, str(root / 'tests/analyze_host_scenarios.py'), str(output)], check=True)
    print(f'COMPLETE {manifest["planned_presses"]} planned presses: {output}', flush=True)


if __name__ == '__main__':
    main()
