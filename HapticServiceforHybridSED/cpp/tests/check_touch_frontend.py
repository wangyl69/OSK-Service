"""Real Windows marked input -> actual SED -> native service -> mock HID.

Uses the same INPUT pair builder as TouchHapticTest, with test-only F23 keys.
Does not synthesize physical touch or validate a board, Driver IC or LRA.
"""
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    root = Path(__file__).resolve().parents[3]
    native = root / 'HapticServiceforHybridSED/cpp/build'
    sed = root / 'Deployment/Apps/SED/SemanticEventDispatcher.exe'
    directory = root / 'artifacts' / ('touch-frontend-' + uuid.uuid4().hex[:10])
    directory.mkdir(parents=True)
    pipe = 'TouchFrontend-' + uuid.uuid4().hex
    processes, handles = [], []

    def launch(name, command, cwd=None):
        output = (directory / (name + '.log')).open('w', encoding='utf-8')
        handles.append(output)
        process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append(process)
        return directory / (name + '.log')

    def wait_for(path, predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            text = path.read_text(encoding='utf-8', errors='replace')
            if predicate(text):
                return text
            if any(p.poll() is not None for p in processes):
                raise RuntimeError('Test process exited; inspect ' + str(directory))
            time.sleep(.03)
        raise TimeoutError(str(path))

    try:
        backend = launch('backend', [str(native / 'HybridHaptic.exe'), '--console', '--mock', '--sed-pipe', pipe])
        wait_for(backend, lambda text: 'HID WARM' in text)
        dispatcher = launch('sed', [str(sed), '--Dispatcher:HapticPipeName=' + pipe,
                                   '--Dispatcher:HapticPipePreconnect=true',
                                   '--Logging:LogLevel:Default=Debug'], cwd=sed.parent)
        wait_for(dispatcher, lambda text: 'Keyboard hook thread started' in text)
        wait_for(dispatcher, lambda text: 'Preconnected to haptic pipe' in text)
        assert backend.read_text(encoding='utf-8').count('MOCK PLAY') == 0, 'Startup produced a haptic'
        emitter = native / 'TouchHapticTests.exe'
        subprocess.run([str(emitter), '--integration-inject'], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        text = wait_for(backend, lambda text: text.count('MOCK PLAY') >= 3)
        assert text.count('MOCK PLAY') == 3, 'KeyUp incorrectly produced extra haptics'
        subprocess.run([str(emitter), '--integration-unmarked'], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(.3)
        assert backend.read_text(encoding='utf-8').count('MOCK PLAY') == 3, 'Unmarked input reached haptics'
        result = {'passed': True, 'marked_downs': 3, 'marked_ups': 3,
                  'unmarked_pairs': 3, 'mock_play_count': 3,
                  'physical_touch_validated': False, 'hardware_validated': False}
        (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print('PASS: 3 marked pairs -> 3 SED/native mock PLAY; unmarked pairs -> 0 extra PLAY')
        print('Evidence:', directory)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
