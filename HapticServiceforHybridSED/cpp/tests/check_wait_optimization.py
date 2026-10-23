"""Sequential before/after runs. No affinity; no parallel performance experiments."""
import json
import os
from pathlib import Path
import subprocess
import sys
from analyze_latency import summarize


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / 'logs/wait-optimization-20261023'
    before = evidence / 'HybridHaptic-before.exe'
    after = root / 'build/HybridHaptic.exe'
    reports = []
    # Reverse normal-load order on repetition to limit order bias. The explicit
    # pressure runs use the revised code and preserve independent results.
    for version, workers in [('after', 0), ('before', 0), ('after', 4), ('after', 8)]:
        label = f'wait-{version}-load{workers}-repeat-20261023'
        env = dict(os.environ, HYBRID_TEST_EXE=str(before if version == 'before' else after))
        command = [sys.executable, str(root / 'tests/check_real_pipeline.py'), '--with-osk', '--quiet',
                   '--sed-trace', '--status-mode', 'interrupt', '--mock-status-delay-ms', '200',
                   '--extra-scenarios', '--load-workers', str(workers), '--run-label', label]
        subprocess.run(command, env=env, check=True, timeout=180)
        path = root / 'logs' / ('real-pipeline-' + label) / 'results.json'
        d = json.loads(path.read_text(encoding='utf8'))
        assert d['total_measured_presses'] == 1600 and d['final_status']['generation'] == 1 and not d['final_status']['dropped']
        rows = d['sed_stage_records']
        stages = {'pipe': ('native_enter', 'service_submit'),
                  'service_queue': ('service_submit', 'service_dequeue'),
                  'service_total': ('service_submit', 'mock_complete'),
                  'pipe_service': ('native_enter', 'mock_complete'),
                  'dll_mock': ('inject', 'mock_complete')}
        summary = {k: summarize([r[b]-r[a] for r in rows]) for k, (a,b) in stages.items()}
        wait_stages = {'submit_lock': [], 'worker_lock': [], 'enqueue_to_dequeue': []}
        for trial in d.values():
            if not isinstance(trial, dict): continue
            for s, w in zip(trial.get('service_stage_samples', []), trial.get('service_wait_samples', [])):
                wait_stages['submit_lock'].append(w[1]-s[1])
                wait_stages['worker_lock'].append(w[4]-w[3])
                wait_stages['enqueue_to_dequeue'].append(s[2]-w[2])
        summary.update({k:summarize(v) for k,v in wait_stages.items() if v})
        reports.append(dict(version=version, workers=workers, path=str(path), stages_us=summary))
        (evidence / 'repeat-summary.json').write_text(json.dumps(reports, indent=2), encoding='utf8')
        print(json.dumps(reports[-1]), flush=True)
    print('PASS: 6400 sequential repeated/pressure events; mock completion only.')


if __name__ == '__main__': main()
