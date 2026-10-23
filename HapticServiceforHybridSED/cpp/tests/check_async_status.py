"""No-MCU: delayed interrupt feedback must not serialize PLAY behind its ACK."""
import json
import time
import check_service_features as features


def main():
    features.REPORT = features.ROOT / 'logs/async-status'
    features.REPORT.mkdir(parents=True, exist_ok=True)
    results = {}
    backend = features.Backend('delayed', '--status-mode', 'interrupt',
                               '--mock-status-delay-ms', '200', '--mock-quiet')
    try:
        backend.wait(lambda s: s['ready'])
        time.sleep(1.05)
        backend.control('diagnose')
        waiting = backend.wait(lambda s: s['async_status_pending'])
        before = waiting['play_sent']
        backend.send()
        # Confirm the PLAY completed while the same delayed ACK is still pending,
        # rather than relying on a small wall-time threshold on a shared machine.
        overlap = backend.wait(lambda s: s['play_sent'] == before + 1)
        assert overlap['async_status_pending'], overlap
        backend.wait(lambda s: not s['async_status_pending'])
        baseline = backend.control('status')['runtime_status_queries']
        started = time.monotonic()
        while time.monotonic() - started < 1.2:
            backend.control('diagnose')
            time.sleep(0.03)
        state = backend.control('status')
        assert state['runtime_status_queries'] - baseline <= 2, state
        assert state['dropped'] == 0 and state['generation'] == 1
        backend.wait(lambda s: s['verify_count'] >= 1, timeout=12)
        # Pause while a response is pending, then reopen a fresh generation.
        backend.wait(lambda s: not s['async_status_pending'])
        time.sleep(1.05)
        backend.control('diagnose')
        backend.wait(lambda s: s['async_status_pending'])
        backend.control('session close')
        backend.wait(lambda s: s['state'] == 'PAUSED' and not s['async_status_pending'])
        backend.control('session open')
        reopened = backend.wait(lambda s: s['ready'] and s['generation'] == 2)
        results['play_during_ack'] = overlap
        results['rate_limit'] = state
        results['reopened'] = reopened
    finally:
        backend.close()

    backend = features.Backend('missing', '--status-mode', 'interrupt',
                               '--mock-status-delay-ms', '500', '--mock-quiet')
    try:
        backend.wait(lambda s: s['ready'])
        time.sleep(1.05)
        backend.control('diagnose')
        failed = backend.wait(lambda s: s['state'] == 'RECOVERING')
        assert 'async status ACK timeout' in failed['last_error'] and not failed['ready']
        results['timeout'] = failed
    finally:
        backend.close()
    (features.REPORT / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PASS: PLAY during delayed ACK; <=1Hz explicit diagnostics; periodic verification; '
          'pause/reopen with pending ACK; missing ACK recovery. Mock, no physical HID timing.')


if __name__ == '__main__':
    main()
