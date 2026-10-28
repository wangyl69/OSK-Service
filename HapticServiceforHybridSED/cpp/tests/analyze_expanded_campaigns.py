"""Pool raw samples by scenario/load, retaining all runs and strict thresholds."""
import argparse
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
from analyze_host_scenarios import stats
from host_latency_stages import BOUNDARIES, durations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaigns', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    groups = defaultdict(list)
    seen, runs, slow = set(), [], []
    for campaign in args.campaigns:
        manifest = json.loads((campaign / 'manifest.json').read_text(encoding='utf-8-sig'))
        assert manifest['kernel_trace'] is False, 'Compare only the same application-trace mode here'
        for entry in manifest['runs']:
            assert entry['exit_code'] == 0
            directory = Path(entry['captures'][0]).resolve()
            assert str(directory) not in seen, 'Duplicate run would inflate sample count'
            seen.add(str(directory))
            data = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
            rows = data['sed_stage_records']
            assert len(rows) == data['total_measured_presses']
            assert data['final_status']['dropped'] == 0 and data['final_status']['generation'] == 1
            assert data['final_status']['play_sent'] == len(rows) + 1
            assert all(t['relative_priority'] == 2 for t in data['verified_sed_priorities'].values())
            assert data['verified_service_priorities']['input_thread_priority'] == data['verified_service_priorities']['hid_thread_priority'] == 2
            values = [durations(row) for row in rows]
            groups[(entry['scenario'], entry['workers'])].extend(values)
            runs.append(dict(campaign=str(campaign), capture=str(directory), scenario=entry['scenario'],
                             workers=entry['workers'], samples=len(rows)))
            for row, value in zip(rows, values):
                if any(x > 2000 for x in value.values()):
                    slow.append(dict(capture=str(directory), scenario=entry['scenario'], workers=entry['workers'],
                                     sequence=row['sequence'], durations_us=value, timestamps=row))
    table, summaries = [], []
    for (scenario, workers), values in sorted(groups.items()):
        count = len(values)
        over5 = sum(any(x > 5000 for x in value.values()) for value in values)
        summaries.append(dict(scenario=scenario, workers=workers, samples=count,
                              events_over_2ms=sum(any(x > 2000 for x in value.values()) for value in values),
                              events_over_5ms=over5,
                              zero_event_upper95_iid_assumption=(-math.expm1(math.log(.05)/count)) if over5 == 0 else None))
        for stage in BOUNDARIES:
            table.append(dict(scenario=scenario, workers=workers, stage=stage,
                              **stats([value[stage] for value in values])))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'summary.json').write_text(json.dumps(dict(groups=summaries, stages=table, runs=runs), indent=2), encoding='utf-8')
    (args.output / 'slow-events.json').write_text(json.dumps(slow, indent=2), encoding='utf-8')
    with (args.output / 'stages.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0])); writer.writeheader(); writer.writerows(table)
    print(json.dumps(summaries, indent=2))


if __name__ == '__main__':
    main()
