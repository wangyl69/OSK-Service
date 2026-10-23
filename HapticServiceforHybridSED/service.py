"""Independent desktop-session HybridSED -> HID haptic service."""
import argparse
import json
import logging
from pathlib import Path
import queue
import struct
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / 'transport'))
from core import Correlator
from keyboard_observer import KeyboardObserver

log = logging.getLogger('HybridHaptic')


class HidWorker:
    """Single owner of all HID I/O, including heartbeat and reconnect."""
    def __init__(self, factory, waveform=1, intensity=1, max_age=0.05):
        self.factory = factory
        self.waveform, self.intensity, self.max_age = waveform, intensity, max_age
        self.events = queue.Queue(maxsize=8)
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='HidWorker', daemon=True)
        self.play_sent = self.dropped = 0

    def submit(self, edge):
        if not self.ready.is_set():
            self.dropped += 1
            return
        try:
            self.events.put_nowait(edge)
        except queue.Full:
            self.dropped += 1

    def _run(self):
        while not self.stop_event.is_set():
            device = None
            try:
                device = self.factory()
                device.open()
                if not device.open_session():
                    raise RuntimeError('SESSION_OPEN failed')
                if not device.set_waveform(self.waveform) or not device.set_intensity(self.intensity):
                    raise RuntimeError('MCU did not confirm waveform/intensity')
                log.info('HID ready; feedback_confirmed=%s', device.feedback_confirmed)
                # Discard anything left from a failed connection generation.
                self._clear()
                self.ready.set()
                verified = time.monotonic()
                keepalive = verified
                while not self.stop_event.is_set():
                    now = time.monotonic()
                    if now - verified >= 10:
                        if not device.heartbeat():
                            raise RuntimeError('Session heartbeat failed')
                        verified = keepalive = time.monotonic()
                    elif now - keepalive >= 0.25:
                        if not device.transport_keepalive():
                            raise RuntimeError('Transport keepalive failed')
                        keepalive = time.monotonic()
                    try:
                        edge = self.events.get(timeout=0.02)
                    except queue.Empty:
                        continue
                    age = time.monotonic() - edge.created
                    if age > self.max_age:
                        self.dropped += 1
                        continue
                    if not device.play(waveform=self.waveform, intensity=self.intensity,
                                       ack_required=False, age_us=min(65535, int(age * 1e6)),
                                       key_id=edge.key):
                        raise RuntimeError('PLAY write failed')
                    self.play_sent += 1
                    log.debug('PLAY key=%d type=%d', edge.key, edge.code)
            except Exception:
                log.exception('HID unavailable; events dropped; retry in 3 seconds')
            finally:
                self.ready.clear()
                self._clear()
                try:
                    if device is not None and device.is_connected:
                        device.close_session()
                except Exception:
                    log.debug('Session close failed', exc_info=True)
                if device is not None:
                    try:
                        device.close()
                    except Exception:
                        log.debug('Device close failed', exc_info=True)
            self.stop_event.wait(3)

    def _clear(self):
        while True:
            try:
                self.events.get_nowait()
                self.dropped += 1
            except queue.Empty:
                return

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(5)
        if self.thread.is_alive():
            log.error('HID worker did not stop within 5 seconds')


class PipeReceiver:
    def __init__(self, name, correlator, worker):
        self.name, self.correlator, self.worker = name, correlator, worker
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name='SedPipe', daemon=True)
        self.handle = None
        self.ready = threading.Event()
        self.error = None

    def start(self):
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('Pipe startup timeout')
        if self.error:
            raise self.error

    def _run(self):
        import win32pipe as p
        import win32file as f
        import pywintypes
        try:
            while not self.stop_event.is_set():
                # FIRST_PIPE_INSTANCE refuses a competing consumer/service.
                handle = p.CreateNamedPipe(self.name, p.PIPE_ACCESS_INBOUND | 0x00080000,
                    p.PIPE_TYPE_BYTE | p.PIPE_READMODE_BYTE | p.PIPE_NOWAIT | 0x8,
                    1, 4096, 4096, 0, None)
                self.handle = handle
                self.ready.set()
                log.info('Waiting for HybridSED: %s', self.name)
                try:
                    while not self.stop_event.is_set():
                        try:
                            p.ConnectNamedPipe(handle, None)
                            break
                        except pywintypes.error as exc:
                            if exc.winerror == 535:  # already connected
                                break
                            if exc.winerror not in (536, 231):
                                raise
                            self.stop_event.wait(0.002)
                    if self.stop_event.is_set():
                        break
                    self.correlator.connect(True)
                    log.info('HybridSED connected')
                    while not self.stop_event.is_set():
                        try:
                            _, data = f.ReadFile(handle, 4096)
                        except pywintypes.error as exc:
                            if exc.winerror == 232:
                                self.stop_event.wait(0.001)
                                continue
                            if exc.winerror in (109, 233):
                                break
                            raise
                        if not data:
                            self.stop_event.wait(0.001)
                            continue
                        for code in data:
                            if code not in (1, 2, 3):
                                self.correlator.connect(False)
                                raise RuntimeError(f'Unsupported HybridSED byte: {code}')
                            edge = self.correlator.match(code)
                            if edge is not None:
                                self.worker.submit(edge)
                finally:
                    self.correlator.connect(False)
                    f.CloseHandle(handle)
                    self.handle = None
        except Exception as exc:
            self.error = exc
            log.exception('Pipe receiver stopped')
        finally:
            self.ready.set()

    def stop(self):
        self.stop_event.set()
        self.correlator.connect(False)
        if self.thread.ident:
            self.thread.join(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mock', action='store_true')
    parser.add_argument('--waveform', type=int, choices=range(1, 5), default=1)
    parser.add_argument('--intensity', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--sed-settings', type=Path,
                        help='Read Dispatcher key classification/pipe settings from this JSON')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        parser.error('Windows x64 Python is required')
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    settings = {}
    if args.sed_settings:
        # appsettings.json permits whole-line // comments; this file uses those only.
        lines = args.sed_settings.read_text(encoding='utf-8-sig').splitlines()
        settings = json.loads('\n'.join(line for line in lines
                                       if not line.lstrip().startswith('//')))['Dispatcher']
    correlator = Correlator(voice=settings.get('VoiceKeyVirtualKey', 135),
        modifiers=settings.get('ModifierKeys'), special=settings.get('SpecialKeys'))
    if args.mock:
        from haptic_mock import MockHapticHID, describe
        factory = lambda: MockHapticHID(on_report=lambda report: log.info('[mock] %s', describe(report)))
    else:
        from haptic_hid import HapticHID
        factory = HapticHID
    worker = HidWorker(factory, args.waveform, args.intensity)
    observer = KeyboardObserver(correlator)
    pipe = PipeReceiver('\\\\.\\pipe\\' + settings.get('HapticPipeName', 'HybridSedHaptic'),
                        correlator, worker)
    log.warning('Legacy byte protocol: key edges inferred by local observation; '
                'no end-to-end identity/guarantee after dropped events. Run in OSK desktop session.')
    try:
        observer.start()
        pipe.start()
        worker.start()
        while not pipe.error and not observer.error:
            time.sleep(0.2)
        raise RuntimeError('Observer or pipe failed') from (pipe.error or observer.error)
    except KeyboardInterrupt:
        log.info('Stopping')
    finally:
        pipe.stop()
        observer.stop()
        if worker.thread.ident:
            worker.stop()
        log.info('PLAY sent=%d dropped=%d correlation=%s',
                 worker.play_sent, worker.dropped, dict(correlator.stats))


if __name__ == '__main__':
    main()
