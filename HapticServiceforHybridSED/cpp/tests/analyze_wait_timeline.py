"""Export notification/wait boundaries and thread IDs for same-run ETL review."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    data = json.loads((args.directory / 'results.json').read_text(encoding='utf-8'))
    rows = data['sed_stage_records']
    events = []
    for index, row in enumerate(rows):
        assert row['service_submit'] <= row['service_notify_enter'] <= row['service_notify_return']
        assert row['service_wait_enter'] <= row['service_wait_returned'] <= row['service_worker_lock_request']
        duration = row['mock_complete'] - row['native_enter']
        events.append(dict(index=index, trial=row['trial'], pipe_service_us=duration,
            enqueue_to_notify_enter_us=row['service_notify_enter']-row['service_enqueue_stamp'],
            notify_api_us=row['service_notify_return']-row['service_notify_enter'],
            notify_enter_to_dequeue_us=row['service_dequeue']-row['service_notify_enter'],
            worker_lock_us=row['service_worker_locked']-row['service_worker_lock_request'],
            timestamps=row))
    events.sort(key=lambda item: item['pipe_service_us'], reverse=True)
    result = dict(scope='Same-run application boundaries; kernel Ready/CSwitch not inferred',
                  units='QPC microseconds', samples=len(rows),
                  slow_events=[e for e in events if e['pipe_service_us'] > 5000],
                  largest_events=events[:10],
                  note='Notify may return after dequeue. Latest wait may precede enqueue; merged signals are not one-to-one.')
    (args.directory / 'wait-timeline.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(f"Validated {len(rows)} timelines; {len(result['slow_events'])} Pipe+Service events over 5ms")


if __name__ == '__main__':
    main()
