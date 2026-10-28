"""Analyze the two separately controlled ABBA comparisons from the pairing tool."""
import argparse
import csv
import json
from pathlib import Path

from analyze_latency import summarize
from check_dll_latency_campaign import BOUNDARIES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    manifest = json.loads((args.directory / 'manifest.json').read_text(encoding='utf-8-sig'))
    assert manifest['restored'] and manifest['temporary_deleted'], 'Original power policy must be restored'
    assert len(manifest['runs']) == 7
    records = []
    sources = set()
    details = []
    for index, run in enumerate(manifest['runs']):
        assert run['exit_code'] == 0
        sources.update((run['source_before']['ACLineStatus'], run['source_after']['ACLineStatus']))
        data = json.loads((Path(run['capture']) / 'results.json').read_text())
        expected = 2 if run['priority'] == 'highest' else 1
        assert data['verified_service_priorities']['hid_thread_priority'] == expected
        assert data['verified_service_priorities']['input_thread_priority'] == expected
        assert all(v['relative_priority'] == expected for v in data['verified_sed_priorities'].values())
        assert data['final_status']['dropped'] == 0 and data['final_status']['play_sent'] == 471
        rows = data['sed_stage_records']
        assert len(rows) == 470
        records.append(rows)
        details.append(dict(run=index+1, power=run['power_case'], priority=run['priority'],
            pipe_service=summarize([r['mock_complete']-r['native_enter'] for r in rows]),
            dll_to_mock=summarize([r['mock_complete']-r['inject'] for r in rows])))
    assert len(sources) == 1 and 255 not in sources, 'Power source changed or unknown'
    groups = [('priority-above-normal', [0, 3]), ('priority-highest', [1, 2]),
              ('parking-original', [3, 6]), ('parking-reduced', [4, 5])]
    table = []
    for group, indexes in groups:
        rows = [r for i in indexes for r in records[i]]
        for stage, (a, b) in BOUNDARIES.items():
            s = summarize([r[b]-r[a] for r in rows])
            table.append(dict(group=group, stage=stage, samples=len(rows),
                **{k:s[k] for k in ('p50','p99','p999','max')}, over5=s['strictly_over_us']['5000']))
    with (args.directory / 'paired-stages.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(table[0])); writer.writeheader(); writer.writerows(table)
    (args.directory / 'paired-summary.json').write_text(json.dumps(dict(
        source=list(sources), runs=details, stages=table), indent=2), encoding='utf-8')
    for row in table:
        if row['stage'] in ('pipe_service','dll_to_mock'):
            print(row['group'],row['stage'],'P99/max ms',row['p99']/1000,row['max']/1000,'>5ms',row['over5'])


if __name__ == '__main__':
    main()
