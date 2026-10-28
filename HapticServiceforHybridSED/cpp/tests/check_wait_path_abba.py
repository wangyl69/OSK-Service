"""Sequential old/new/new/old DLL checks and same-session mixed-input soak.

No affinity, power-policy, desktop-lock or production setting changes.
All failures and successful per-event tails are retained independently.
"""
import csv
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from check_dll_latency_campaign import analyze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--factorial', action='store_true', help='Four SED/Service combinations under both loads')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    baseline = root / 'logs/dll-campaign-20261026-113321/baseline-binaries'
    repo = root.parents[1]
    binaries = {
        'old': (baseline / 'HybridHaptic.exe', baseline / 'sed/SemanticEventDispatcher.dll'),
        'new': (root / 'build/HybridHaptic.exe', repo / 'HybridSedPrototype_ExtraInfo/src/SemanticEventDispatcher/bin/Debug/net8.0/SemanticEventDispatcher.dll'),
    }
    if args.factorial:
        binaries.update(oldsed_newservice=(binaries['new'][0], binaries['old'][1]),
                        newsed_oldservice=(binaries['old'][0], binaries['new'][1]))
    hashes = {version: {str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in paths} for version, paths in binaries.items()}
    stamp = ('wait-factorial-' if args.factorial else 'wait-abba-') + datetime.now().strftime('%Y%m%d-%H%M%S')
    output = root / 'logs' / stamp
    output.mkdir(exist_ok=False)
    plan = [(version, load, 'standard', 1) for load in (0, 4)
            for version in ('old', 'new', 'new', 'old')]
    plan += [('new', load, 'cadence', 3) for load in (0, 4)]
    if args.factorial:
        order = ('old', 'oldsed_newservice', 'new', 'newsed_oldservice')
        plan = [(version, load, 'standard', 1) for load in (0, 4)
                for version in (order if load == 0 else reversed(order))]
    manifest = dict(scope='Actual DLL to native mock PLAY; no physical touch/HID/MCU',
                    hashes=hashes, planned_presses=sum(1400 if s == 'standard' else 470*c for _,_,s,c in plan), runs=[])
    table, slow = [], []
    for index, (version, load, scenario, cycles) in enumerate(plan, 1):
        label = f'{stamp}-{index}-{version}-load{load}-{scenario}'
        exe, dll = binaries[version]
        command = [sys.executable, str(root / 'tests/check_real_pipeline.py'),
                   '--with-osk', '--quiet', '--sed-trace', '--status-mode', 'interrupt',
                   '--mock-status-delay-ms', '200', '--load-workers', str(load),
                   '--scenario-set', scenario, '--cadence-cycles', str(cycles),
                   '--dispatcher-dll', str(dll.resolve()), '--run-label', label]
        env = os.environ.copy()
        env['HYBRID_TEST_EXE'] = str(exe.resolve())
        entry = dict(label=label, version=version, load_workers=load, scenario=scenario,
                     cycles=cycles, command=command)
        manifest['runs'].append(entry)
        print('RUN ' + label, flush=True)
        try:
            with (output / (label + '.txt')).open('w', encoding='utf-8') as log:
                entry['exit_code'] = subprocess.run(command, env=env, stdout=log,
                                                    stderr=subprocess.STDOUT).returncode
            if entry['exit_code']:
                raise RuntimeError('Run failed; preserved logs: ' + str(output))
            report = root / 'logs' / ('real-pipeline-' + label) / 'results.json'
            data = json.loads(report.read_text(encoding='utf-8'))
            values, tails, count = analyze(report, label, load)
            assert count == (1400 if scenario == 'standard' else 470 * cycles)
            assert data['final_status']['play_sent'] == count + 1
            if version in ('new', 'newsed_oldservice'):
                assert data['sed_queue_implementation'] == 'FixedRing'
            if version in ('new', 'oldsed_newservice'):
                assert data['service_worker_wake_mode'] == 'native-event'
            entry.update(report=str(report.resolve()), measured_presses=count)
            table.extend(dict(version=version, **v) for v in values)
            slow.extend(dict(version=version, **v) for v in tails)
            print(f'PASS {count}, slow events {len(tails)}', flush=True)
        finally:
            (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            (output / 'slow-events.json').write_text(json.dumps(slow, indent=2), encoding='utf-8')
            if table:
                with (output / 'stages.csv').open('w', newline='', encoding='utf-8-sig') as f:
                    writer = csv.DictWriter(f, fieldnames=list(table[0]))
                    writer.writeheader()
                    writer.writerows(table)
    assert sum(r['measured_presses'] for r in manifest['runs']) == manifest['planned_presses']
    print('COMPLETE ' + str(output), flush=True)


if __name__ == '__main__':
    main()
