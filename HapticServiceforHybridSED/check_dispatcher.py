"""Explicit mock-only smoke check against an already-built HybridSED DLL.

Injects F23 down/up (no printable character), starts/stops its own dispatcher.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from core import Correlator
from keyboard_observer import KeyboardObserver
from service import HidWorker, PipeReceiver
from haptic_mock import MockHapticHID
from test_windows_pipe import inject


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dispatcher', type=Path)
    args = parser.parse_args()
    dll = args.dispatcher.resolve(strict=True)
    correlator = Correlator()
    worker = HidWorker(MockHapticHID)
    observer = KeyboardObserver(correlator)
    name = 'HybridSedSmoke-' + uuid.uuid4().hex
    pipe = PipeReceiver('\\\\.\\pipe\\' + name, correlator, worker)
    process = None
    with tempfile.TemporaryFile(mode='w+t', encoding='utf-8') as output:
        try:
            observer.start()
            pipe.start()
            worker.start()
            if not worker.ready.wait(3):
                raise RuntimeError('Mock HID not ready')
            process = subprocess.Popen(['dotnet', '--roll-forward', 'Major', str(dll),
                '--Dispatcher:HapticPipeName=' + name,
                '--Dispatcher:VoiceKeyVirtualKey=135'], cwd=dll.parent,
                stdout=output, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 15
            while not correlator.connected and time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Dispatcher exited')
                time.sleep(0.02)
            if not correlator.connected:
                raise RuntimeError('Dispatcher did not connect')
            # Publisher connects during host construction, before hook setup.
            time.sleep(1)
            for down, marker in [(True, 0x5345445F4F534B), (False, 0x5345445F4F534B),
                                 (True, 0), (False, 0)]:
                inject(down, marker)
                time.sleep(0.05)
            time.sleep(0.1)
            if worker.play_sent != 1 or correlator.stats['keyup_ignored'] != 1:
                raise AssertionError((worker.play_sent, dict(correlator.stats)))
            print('PASS: actual HybridSED -> pipe -> edge association -> mock HID: '
                  '1 PLAY, 1 KeyUp ignored, unmarked keys did not trigger.')
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            pipe.stop()
            observer.stop()
            if worker.thread.ident:
                worker.stop()
            output.seek(0)
            print(output.read())


if __name__ == '__main__':
    main()
