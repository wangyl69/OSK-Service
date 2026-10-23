"""Optional stdlib-only mock smoke check. Native executables need no Python.

Starts its own native backend and existing dispatcher, injects F23 with
and without the OSK marker, then terminates only its own mock processes.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import subprocess
import tempfile
import shutil
import time
import uuid


def inject(down, marker, vk=0x86):
    class Keyboard(c.Structure):
        _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD),
                    ('time', w.DWORD), ('extra', c.c_size_t)]

    class Mouse(c.Structure):
        _fields_ = [('x', w.LONG), ('y', w.LONG), ('data', w.DWORD),
                    ('flags', w.DWORD), ('time', w.DWORD), ('extra', c.c_size_t)]

    class Payload(c.Union):
        _fields_ = [('keyboard', Keyboard), ('mouse', Mouse)]

    class Input(c.Structure):
        _fields_ = [('type', w.DWORD), ('payload', Payload)]

    event = Input()
    event.type = 1
    event.payload.keyboard = Keyboard(vk, 0, 0 if down else 2, 0, marker)
    send = c.WinDLL('user32', use_last_error=True).SendInput
    send.argtypes = [w.UINT, c.POINTER(Input), c.c_int]
    send.restype = w.UINT
    if send(1, c.byref(event), c.sizeof(Input)) != 1:
        raise c.WinError(c.get_last_error())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dispatcher', type=Path)
    parser.add_argument('--osk-dll', type=Path,
                        help='Use OSK_Prototype actual native key injector for marked inputs')
    parser.add_argument('--run-label', default='')
    args = parser.parse_args()
    if args.run_label and any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for ch in args.run_label):
        parser.error('run-label must contain only letters, digits, dash or underscore')
    dll = args.dispatcher.resolve(strict=True)
    osk = None
    if args.osk_dll:
        osk = c.CDLL(str(args.osk_dll.resolve(strict=True)))
        for name in ('KeyDown', 'KeyUp'):
            getattr(osk, name).argtypes = [w.WORD]
            getattr(osk, name).restype = c.c_bool

    def emit(down, marker, vk=0x86):
        if osk is not None and marker == 0x5345445F4F534B:
            if not (osk.KeyDown if down else osk.KeyUp)(vk):
                raise RuntimeError('Actual OSK key injector failed')
        else:
            inject(down, marker, vk)
    exe = (Path(__file__).resolve().parents[1] / 'build/HybridHaptic.exe').resolve(strict=True)
    suffix = uuid.uuid4().hex
    sed_pipe = 'CppSed-' + suffix
    processes, handles = [], []
    expected_exits = set()
    with tempfile.TemporaryDirectory(prefix='hybrid-cpp-') as directory:
        directory = Path(directory)

        def launch(name, command, cwd=None):
            output = (directory / (name + '.txt')).open('w', encoding='utf-8')
            handles.append(output)
            process = subprocess.Popen(command, cwd=cwd, stdout=output,
                                       stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append(process)
            return directory / (name + '.txt')

        def wait_for(file, text):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if text in file.read_text(encoding='utf-8', errors='replace'):
                    return
                if any(p.poll() is not None and p not in expected_exits for p in processes):
                    raise RuntimeError('A smoke-check process exited')
                time.sleep(0.02)
            raise TimeoutError(text)

        try:
            backend = launch('backend', [str(exe), '--console', '--mock', '--sed-pipe', sed_pipe])
            wait_for(backend, 'Backend running')
            wait_for(backend, 'HID WARM')
            sed = launch('dispatcher', ['dotnet', '--roll-forward', 'Major', str(dll),
                         '--Dispatcher:HapticPipeName=' + sed_pipe,
                         '--Dispatcher:VoiceKeyVirtualKey=135',
                         '--Dispatcher:WmiClassName=MissingHybridHapticSmokeClass'], cwd=dll.parent)
            wait_for(sed, 'Keyboard hook thread started')
            for down, marker in [(True, 0x5345445F4F534B), (False, 0x5345445F4F534B),
                                 (True, 0), (False, 0)]:
                emit(down, marker)
                time.sleep(0.03)
            wait_for(backend, 'MOCK PLAY')
            time.sleep(0.15)
            output = backend.read_text(encoding='utf-8')
            if output.count('MOCK PLAY') != 1:
                raise AssertionError(output)
            # Auto-repeat is intentionally accepted: two Down edges, one Up.
            for down in (True, True, False):
                emit(down, 0x5345445F4F534B)
                time.sleep(0.03)
            time.sleep(0.15)
            output = backend.read_text(encoding='utf-8')
            if output.count('MOCK PLAY') != 3:
                raise AssertionError(output)
            # An intentionally missing WMI class must not suppress F24 haptics.
            emit(True, 0x5345445F4F534B, 0x87)
            time.sleep(0.1)
            emit(False, 0x5345445F4F534B, 0x87)
            time.sleep(0.2)
            output = backend.read_text(encoding='utf-8')
            if output.count('MOCK PLAY') != 4:
                raise AssertionError(output)
            # Early allocation filtering must preserve unmarked voice edges,
            # while they still produce no haptic request.
            emit(True, 0, 0x87)
            time.sleep(0.03)
            emit(False, 0, 0x87)
            voice_deadline = time.monotonic() + 5
            while time.monotonic() < voice_deadline:
                voice_log = sed.read_text(encoding='utf-8', errors='replace')
                if all(voice_log.count('Failed to publish queued voice mode ' + str(mode)) == 2 for mode in (0, 1)):
                    break
                time.sleep(0.02)
            else:
                raise AssertionError('Unmarked voice Down/Up did not survive early filtering')
            if backend.read_text(encoding='utf-8').count('MOCK PLAY') != 4:
                raise AssertionError('Unmarked voice key produced haptic PLAY')
            # Sustained input; no queue overflow expected at this deliberate rate.
            for _ in range(40):
                emit(True, 0x5345445F4F534B)
                time.sleep(0.02)
                emit(False, 0x5345445F4F534B)
                time.sleep(0.01)
            time.sleep(0.2)
            if backend.read_text(encoding='utf-8').count('MOCK PLAY') != 44:
                raise AssertionError('Sustained input count mismatch')
            # Restart the native receiver while SED keeps running. A failed write
            # may lose its event; it must reconnect without replaying old events.
            old_backend = processes[0]
            expected_exits.add(old_backend)
            old_backend.terminate()
            old_backend.wait(timeout=5)
            backend2 = launch('backend-reconnected', [str(exe), '--console', '--mock', '--sed-pipe', sed_pipe])
            wait_for(backend2, 'HID WARM')
            for _ in range(6):
                emit(True, 0x5345445F4F534B)
                time.sleep(0.1)
                emit(False, 0x5345445F4F534B)
                time.sleep(0.03)
            count = backend2.read_text(encoding='utf-8').count('MOCK PLAY')
            if not 4 <= count <= 6:
                raise AssertionError('Receiver restart failed: ' + str(count))
            # Disconnect publisher, then send a batch with invalid and valid codes.
            publisher = processes[1]
            expected_exits.add(publisher)
            publisher.terminate()
            publisher.wait(timeout=5)
            wait_for(backend2, 'HybridSED disconnected')
            kernel = c.WinDLL('kernel32', use_last_error=True)
            kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
            kernel.CreateFileW.restype = w.HANDLE
            kernel.WriteFile.argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
            kernel.CloseHandle.argtypes = [w.HANDLE]
            handle = kernel.CreateFileW('\\\\.\\pipe\\' + sed_pipe, 0x40000000, 0, None, 3, 0, None)
            if handle == c.c_void_p(-1).value:
                raise c.WinError(c.get_last_error())
            try:
                written = w.DWORD()
                data = c.create_string_buffer(bytes([0, 4, 255, 1, 2, 3]))
                if not kernel.WriteFile(handle, data, 6, c.byref(written), None):
                    raise c.WinError(c.get_last_error())
                time.sleep(0.15)
            finally:
                kernel.CloseHandle(handle)
            if backend2.read_text(encoding='utf-8').count('MOCK PLAY') != count + 3:
                raise AssertionError('Batch/invalid-code handling failed')
            print('PASS: actual HybridSED -> native Service -> mock HID; '
                  'Down/Up=1, unmarked=0, repeated Down/Down/Up=2, WMI failure F24=1; '
                  '40 sustained presses, receiver restart, invalid bytes and batched delivery passed.')
        except Exception:
            # Preserve reason counters before killing owned processes. A short
            # smoke test can fail before the SED's 5s background summary runs.
            try:
                diagnostic = subprocess.run([str(exe), '--sed-pipe', sed_pipe, '--control', 'status'],
                    capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                text = diagnostic.stdout + diagnostic.stderr
            except Exception as diagnostic_error:
                text = 'Status collection failed: ' + repr(diagnostic_error)
            (directory / 'failure-service-status.txt').write_text(text, encoding='utf-8')
            time.sleep(5.1)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
            for handle in handles:
                handle.close()
            for file in directory.glob('*.txt'):
                report = Path(__file__).resolve().parents[1] / 'logs' / ('validation' + ('-' + args.run_label if args.run_label else ''))
                report.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, report / file.name)
            print('Logs: ' + str(report))


if __name__ == '__main__':
    main()
