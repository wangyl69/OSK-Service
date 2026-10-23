"""Native control, session, scheduling and compatibility regression (mock only)."""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get('HYBRID_TEST_EXE', ROOT / 'build/HybridHaptic.exe'))
REPORT = ROOT / 'logs/features'
KERNEL = c.WinDLL('kernel32', use_last_error=True)
KERNEL.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
KERNEL.CreateFileW.restype = w.HANDLE
KERNEL.WriteFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
KERNEL.CloseHandle.argtypes = [w.HANDLE]


class Backend:
    def __init__(self, label, *arguments, prefill=False):
        self.label = label
        self.pipe = 'FeatureTest-' + uuid.uuid4().hex
        self.output = (REPORT / (label + '-stdout.txt')).open('w', encoding='utf-8')
        self.log = REPORT / (label + '.log')
        if prefill:
            self.log.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        self.process = subprocess.Popen([str(EXE), '--console', '--mock', '--sed-pipe', self.pipe,
            '--log', str(self.log), *arguments], stdout=self.output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.writer = None

    def control(self, command, allow_error=False):
        result = subprocess.run([str(EXE), '--sed-pipe', self.pipe, '--control', command],
            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
        if result.returncode and not allow_error:
            raise RuntimeError(result.stdout + result.stderr)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(repr(result.stdout + result.stderr)) from exc

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Native service exited')
            try:
                last = self.control('status')
                if predicate(last):
                    return last
            except RuntimeError:
                pass
            time.sleep(0.02)
        raise AssertionError(last)

    def send(self, value=1):
        if self.writer is None:
            self.writer = KERNEL.CreateFileW('\\\\.\\pipe\\' + self.pipe,
                0x40000000, 0, None, 3, 0, None)
            if self.writer == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
        data = c.create_string_buffer(bytes([value]))
        done = w.DWORD()
        if not KERNEL.WriteFile(self.writer, data, 1, c.byref(done), None):
            raise c.WinError(c.get_last_error())

    def close(self):
        if self.writer is not None:
            KERNEL.CloseHandle(self.writer)
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        self.output.close()


def main():
    REPORT.mkdir(parents=True, exist_ok=True)
    results = {}
    backend = Backend('feature', '--status-mode', 'feature', prefill=True)
    try:
        ready = backend.wait(lambda s: s['ready'])
        assert ready['status_mode'] == 'feature' and ready['feedback_confirmed']
        assert backend.log.with_name(backend.log.name + '.1').exists(), 'Rotation missing'
        assert backend.log.stat().st_size < 2 * 1024 * 1024
        assert not backend.control('set waveform 5', allow_error=True)['ok']
        assert not backend.control('set keepalive 1', allow_error=True)['ok']
        for command in ('set waveform 3', 'set intensity 2'):
            revision = backend.control(command)['requested_revision']
            backend.wait(lambda s: s['applied_revision'] >= revision)
        backend.control('diagnose')
        backend.wait(lambda s: s['mcu_waveform'] == 3 and s['mcu_intensity'] == 2)
        backend.send()
        backend.wait(lambda s: s['play_sent'] == 1)
        assert 'waveform=3 intensity=2' in backend.log.read_text()
        backend.control('session close')
        disabled = backend.wait(lambda s: s['state'] == 'PAUSED' and not s['ready'])
        backend.send()
        time.sleep(0.05)
        assert backend.control('status')['play_sent'] == 1
        backend.control('session open')
        restored = backend.wait(lambda s: s['ready'] and s['generation'] > ready['generation'])
        backend.send()
        backend.wait(lambda s: s['play_sent'] == 2)
        backend.control('recover')
        backend.wait(lambda s: s['ready'] and s['generation'] > restored['generation'])
        # Quiet interval: disabling light keepalive must leave its count unchanged.
        backend.control('set keepalive 0')
        time.sleep(0.05)
        before = backend.control('status')['keepalive_count']
        time.sleep(0.35)
        assert backend.control('status')['keepalive_count'] == before
        backend.control('set keepalive 250')
        backend.wait(lambda s: s['keepalive_count'] > before)

        def trial(label, background):
            backend.control('set keepalive ' + ('250' if background else '0'))
            backend.control('metrics reset')
            baseline = backend.control('status')['play_sent']
            error = []

            def producer():
                try:
                    for _ in range(120):
                        backend.send()
                        time.sleep(0.015)
                except Exception as exc:
                    error.append(exc)

            thread = threading.Thread(target=producer)
            thread.start()
            while thread.is_alive():
                if background:
                    backend.control('diagnose')
                    backend.control('status')
                time.sleep(0.04)
            thread.join()
            if error:
                raise error[0]
            state = backend.wait(lambda s: s['play_sent'] == baseline + 120)
            assert state['overflow'] == 0 and state['stale'] == 0
            results[label] = {k: state[k] for k in
                ('play_sent', 'queue_time', 'hid_write_time', 'host_time', 'background_io_time')}

        trial('keepalive_off', False)
        trial('diagnostics_under_input', True)
        # Stay alive beyond the 10s verification deadline and ensure a readback occurred.
        backend.wait(lambda s: s['verify_count'] >= 1, timeout=12)
        latest = backend.control('status')
        assert latest['snapshot_valid']
        assert latest['capabilities']['failsafe_seconds'] == 30
        # Invalid input must be counted, not synchronously logged per byte.
        invalid_before = latest['invalid_categories']
        accepted_before = latest['play_sent']
        backend.send(0)
        data = c.create_string_buffer(bytes([255]) * 10000)
        written = w.DWORD()
        if not KERNEL.WriteFile(backend.writer, data, 10000, c.byref(written), None):
            raise c.WinError(c.get_last_error())
        backend.send(1)
        latest = backend.wait(lambda s: s['invalid_categories'] == invalid_before + 10001 and
                              s['play_sent'] == accepted_before + 1)
        assert backend.log.read_text().count('Rejected unknown haptic categories') < 10
        results['final_status'] = latest
    finally:
        backend.close()

    for label, arguments, expected in (
        ('auto-interrupt', ('--status-mode', 'auto', '--mock-feature-unavailable'), 'interrupt'),
        ('interrupt', ('--status-mode', 'interrupt'), 'interrupt'),
        ('write-only', ('--status-mode', 'write-only'), 'write-only')):
        backend = Backend(label, *arguments)
        try:
            state = backend.wait(lambda s: s['ready'])
            assert state['status_mode'] == expected
            assert state['feedback_confirmed'] == (expected != 'write-only')
            if expected in ('interrupt', 'write-only'):
                assert state['mcu_play_accepted'] is None and state['mcu_i2c_errors'] is None
            if expected == 'write-only':
                assert state['mcu_state_flags'] is None
            backend.send()
            backend.wait(lambda s: s['play_sent'] == 1)
            results[label] = backend.control('status')
        finally:
            backend.close()
    backend = Backend('failed-feature', '--status-mode', 'feature', '--mock-feature-unavailable')
    try:
        state = backend.wait(lambda s: s['consecutive_failures'] >= 2, timeout=6)
        assert state['state'] == 'RECOVERING' and not state['ready']
        assert state['error_phase'] == 'open/configure' and state['last_error']
        assert not backend.control('mode invalid', allow_error=True)['ok']
        backend.control('mode interrupt')
        state = backend.wait(lambda s: s['ready'] and s['status_mode'] == 'interrupt')
        backend.send()
        backend.wait(lambda s: s['play_sent'] == 1)
        results['failed-feature-recovered'] = state
    finally:
        backend.close()
    (REPORT / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PASS: controls, validation, live config/readback, session/recovery, log rotation, '
          'keepalive policy, telemetry, diagnostics under input, heartbeat and all status modes.')
    print('Mock timing samples only; no physical I2C/LRA or zero-regression latency claim.')
    print('Report: ' + str(REPORT / 'results.json'))


if __name__ == '__main__':
    main()
