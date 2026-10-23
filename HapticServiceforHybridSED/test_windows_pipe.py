"""Windows pipe/observer integration with harmless F23 synthetic key edges."""
import ctypes as c
from ctypes import wintypes as w
import time
import unittest
import uuid

from core import Correlator, OSK_MARKER
from keyboard_observer import KeyboardObserver
from service import HidWorker, PipeReceiver
from haptic_mock import MockHapticHID


def inject(down, marker=OSK_MARKER):
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
    event.payload.keyboard = Keyboard(0x86, 0, 0 if down else 2, 0, marker)
    send = c.WinDLL('user32', use_last_error=True).SendInput
    send.argtypes = [w.UINT, c.POINTER(Input), c.c_int]
    send.restype = w.UINT
    if send(1, c.byref(event), c.sizeof(Input)) != 1:
        raise c.WinError(c.get_last_error())


class WindowsPipeTests(unittest.TestCase):
    def test_actual_pipe_and_keyboard_observer(self):
        import win32file as f
        correlator = Correlator(max_age=0.5)
        worker = HidWorker(MockHapticHID, max_age=0.5)
        observer = KeyboardObserver(correlator)
        pipe = PipeReceiver('\\\\.\\pipe\\HybridSedTest-' + uuid.uuid4().hex,
                            correlator, worker)
        client = None
        try:
            observer.start()
            worker.start()
            self.assertTrue(worker.ready.wait(3))
            pipe.start()
            client = f.CreateFile(pipe.name, f.GENERIC_WRITE, 0, None,
                                  f.OPEN_EXISTING, 0, None)
            deadline = time.monotonic() + 2
            while not correlator.connected and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(correlator.connected)
            for down in (True, False):
                inject(down)
                f.WriteFile(client, bytes([1]))
                time.sleep(0.02)
            deadline = time.monotonic() + 2
            while not correlator.stats['keyup_ignored'] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(worker.play_sent, 1)
            self.assertEqual(correlator.stats['keyup_ignored'], 1)
            self.assertIsNone(pipe.error)
        finally:
            if client is not None:
                f.CloseHandle(client)
            pipe.stop()
            observer.stop()
            worker.stop()


if __name__ == '__main__':
    unittest.main()
