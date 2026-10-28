"""Repeat actual-DLL runs sequentially, retain failures and per-trial stage tails.

No 500Hz/burst extras, CPU affinity, hardware HID, or keyboard UI simulation.
Production protocol and scheduling are unchanged. All timings are QPC microseconds.
"""
import argparse
import csv
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from analyze_latency import summarize


BOUNDARIES = {
    'dll_to_service': ('inject', 'service_submit'),
    'sed_queue': ('publish', 'dequeue'),
    'pipe': ('native_enter', 'service_submit'),
    'service_queue': ('service_submit', 'service_dequeue'),
    'service': ('service_submit', 'mock_complete'),
    'pipe_service': ('native_enter', 'mock_complete'),
    'dll_to_mock': ('inject', 'mock_complete'),
}


def analyze(report, label, load):
    data = json.loads(report.read_text(encoding='utf-8'))
    rows = data['sed_stage_records']
    assert len(rows) == data['total_measured_presses']
    status = data['final_status']
    assert status['ready'] and status['dropped'] == 0 and status['generation'] == 1
    groups = {'all': rows}
    for row in rows:
        groups.setdefault(row['trial'], []).append(row)
    table, slow = [], []
    for trial, samples in groups.items():
        for stage, (begin, end) in BOUNDARIES.items():
            values = [row[end] - row[begin] for row in samples]
            assert all(value >= 0 for value in values)
            stats = summarize(values)
            table.append(dict(run=label, load_workers=load, trial=trial, stage=stage,
                              samples=len(samples), **{k: stats[k] for k in ('p50', 'p95', 'p99', 'p999', 'max')},
                              over_1ms=stats['strictly_over_us']['1000'],
                              over_5ms=stats['strictly_over_us']['5000'],
                              over_10ms=stats['strictly_over_us']['10000']))
    for index, row in enumerate(rows):
        durations = {stage: row[end] - row[begin] for stage, (begin, end) in BOUNDARIES.items()}
        if any(value > 5000 for value in durations.values()):
            slow.append(dict(run=label, sample_index=index, trial=row['trial'],
                             durations_us=durations, timestamps=row))
    return table, slow, len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, choices=range(1, 5), default=3)
    parser.add_argument('--cadence', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--cadence-only', action='store_true', help='Repeat only paired cadence runs')
    parser.add_argument('--dispatcher-dll', type=Path, help='Saved Dispatcher DLL for A/B')
    parser.add_argument('--service-exe', type=Path, help='Saved native Service EXE for A/B')
    args = parser.parse_args()
    if args.cadence_only and not args.cadence:
        parser.error('--cadence-only conflicts with --no-cadence')
    root = Path(__file__).resolve().parents[1]
    label = 'dll-campaign-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    output = root / 'logs' / label
    output.mkdir(parents=True, exist_ok=False)
    plan = [] if args.cadence_only else [
        (f'{label}-r{repeat}-load{load}', load, 'standard')
        for repeat in range(1, args.rounds + 1) for load in (0, 4)]
    if args.cadence:
        plan += [(f'{label}-cadence{repeat}-load{load}', load, 'cadence')
                 for repeat in range(1, (args.rounds if args.cadence_only else 1) + 1)
                 for load in (0, 4)]
    manifest = dict(scope='Actual OSK DLL -> Windows hook/SED/real pipe -> native mock PLAY; no touch/HID/I2C/MCU',
                    units='us', cpu_affinity_changed=False, overload_500hz_enabled=False,
                    planned_presses=sum(1400 if scenario == 'standard' else 470
                                        for _, _, scenario in plan), runs=[])
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    table, slow = [], []
    for run_label, load, scenario in plan:
        command = [sys.executable, str(root / 'tests/check_real_pipeline.py'), '--with-osk',
                   '--quiet', '--sed-trace', '--status-mode', 'interrupt',
                   '--mock-status-delay-ms', '200', '--load-workers', str(load),
                   '--scenario-set', scenario, '--run-label', run_label]
        if args.dispatcher_dll:
            command += ['--dispatcher-dll', str(args.dispatcher_dll.resolve())]
        environment = os.environ.copy()
        if args.service_exe:
            environment['HYBRID_TEST_EXE'] = str(args.service_exe.resolve())
        print('RUN ' + run_label, flush=True)
        entry = dict(label=run_label, command=command, load_workers=load, scenario=scenario,
                     service_exe=environment.get('HYBRID_TEST_EXE', str(root / 'build/HybridHaptic.exe')))
        manifest['runs'].append(entry)
        try:
            with (output / (run_label + '.txt')).open('w', encoding='utf-8') as log:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=environment)
            entry['exit_code'] = completed.returncode
            if completed.returncode:
                raise RuntimeError('Pipeline failed; inspect ' + str(output / (run_label + '.txt')))
            report = root / 'logs' / ('real-pipeline-' + run_label) / 'results.json'
            values, failures, count = analyze(report, run_label, load)
            entry.update(report=str(report), measured_presses=count)
            table.extend(values)
            slow.extend(failures)
            print(f'PASS {count} presses; {len(failures)} events with a stage over 5ms', flush=True)
        finally:
            (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            (output / 'slow-events.json').write_text(json.dumps(slow, indent=2), encoding='utf-8')
            if table:
                with (output / 'stages.csv').open('w', newline='', encoding='utf-8-sig') as file:
                    writer = csv.DictWriter(file, fieldnames=list(table[0]))
                    writer.writeheader()
                    writer.writerows(table)
    total = sum(run['measured_presses'] for run in manifest['runs'])
    assert total == manifest['planned_presses']
    print(f'COMPLETE {total} presses. Results: {output}', flush=True)


if __name__ == '__main__':
    main()
