"""Slow (~80s) no-MCU test of real backoff and FAULT latch behavior."""
import json
import time
import threading
import check_service_features as features


def main():
    features.REPORT = features.ROOT / 'logs/fault-latch'
    features.REPORT.mkdir(parents=True, exist_ok=True)
    backend = features.Backend('fault', '--status-mode', 'feature', '--mock-feature-unavailable')
    notification_stop = threading.Event()
    notification_errors = []
    def notify_during_backoff():
        try:
            while not notification_stop.wait(0.25):
                backend.control('diagnose')
        except Exception as exc:
            notification_errors.append(repr(exc))
    notifier = threading.Thread(target=notify_during_backoff)
    try:
        started = time.monotonic()
        notifier.start()
        state = backend.wait(lambda s: s['state'] == 'FAULT', timeout=90)
        backoff_elapsed = time.monotonic() - started
        notification_stop.set();notifier.join(timeout=6)
        assert not notifier.is_alive() and not notification_errors, notification_errors
        # 3+6+12+24+30 seconds must not collapse when control events signal
        # the native worker wait. Allow startup observation skew, not retries.
        assert backoff_elapsed >= 74, backoff_elapsed
        assert state['consecutive_failures'] == 6 and not state['ready']
        for category in (1, 2, 3):
            backend.send(category)
        rejected = backend.wait(lambda s: s['unavailable'] == 3)
        assert rejected['play_sent'] == 0 and rejected['dropped'] == 3
        backend.control('session close')
        backend.wait(lambda s: s['state'] == 'PAUSED')
        backend.control('session open')
        restored = backend.wait(lambda s: s['state'] == 'FAULT')
        time.sleep(0.2)
        assert restored['generation'] == 0 and backend.control('status')['consecutive_failures'] == 6
        backend.control('mode interrupt')
        ready = backend.wait(lambda s: s['ready'])
        backend.send()
        recovered = backend.wait(lambda s: s['play_sent'] == 1)
        assert recovered['dropped'] == 3 and recovered['queue_depth'] == 0
        (features.REPORT / 'results.json').write_text(json.dumps({'backoff_elapsed_seconds': backoff_elapsed,
            'fault': state, 'after_pause_resume': restored,
            'input_during_fault': rejected, 'manual_recovery': ready,
            'after_recovery_input': recovered}, indent=2), encoding='utf-8')
        print('PASS: real 3/6/12/24/30s backoff, FAULT survives session close/open, '
              'faulted input counted/dropped, explicit recovery works without replay.')
    finally:
        notification_stop.set()
        if notifier.ident is not None:notifier.join(timeout=6)
        backend.close()


if __name__ == '__main__':
    main()
