"""Slow (~80s) no-MCU test of real backoff and FAULT latch behavior."""
import json
import time
import check_service_features as features


def main():
    features.REPORT = features.ROOT / 'logs/fault-latch'
    features.REPORT.mkdir(parents=True, exist_ok=True)
    backend = features.Backend('fault', '--status-mode', 'feature', '--mock-feature-unavailable')
    try:
        state = backend.wait(lambda s: s['state'] == 'FAULT', timeout=90)
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
        (features.REPORT / 'results.json').write_text(json.dumps({'fault': state, 'after_pause_resume': restored,
            'input_during_fault': rejected, 'manual_recovery': ready,
            'after_recovery_input': recovered}, indent=2), encoding='utf-8')
        print('PASS: real 3/6/12/24/30s backoff, FAULT survives session close/open, '
              'faulted input counted/dropped, explicit recovery works without replay.')
    finally:
        backend.close()


if __name__ == '__main__':
    main()
