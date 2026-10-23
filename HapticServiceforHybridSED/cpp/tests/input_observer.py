"""Test-only read-only input probe; never performs pipe/HID I/O."""
import ctypes as c
from ctypes import wintypes as w
import threading


class KeyboardObserver:
    def __init__(self, correlator):
        self.correlator = correlator
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='EdgeObserver', daemon=True)
        self.thread_id = 0
        self.error = None

    def start(self):
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError('Keyboard observer startup timed out')
        if self.error:
            raise self.error

    def stop(self):
        if self.thread_id:
            c.windll.user32.PostThreadMessageW(self.thread_id, 0x12, 0, 0)
        if self.thread.is_alive():
            self.thread.join(3)

    def _run(self):
        hook = None
        user = c.WinDLL('user32', use_last_error=True)
        kernel = c.WinDLL('kernel32', use_last_error=True)
        proc_type = c.WINFUNCTYPE(c.c_ssize_t, c.c_int, w.WPARAM, w.LPARAM)

        class Keyboard(c.Structure):
            _fields_ = [('vk', w.DWORD), ('scan', w.DWORD), ('flags', w.DWORD),
                        ('time', w.DWORD), ('extra', c.c_size_t)]

        user.SetWindowsHookExW.argtypes = [c.c_int, proc_type, w.HINSTANCE, w.DWORD]
        user.SetWindowsHookExW.restype = w.HANDLE
        user.CallNextHookEx.argtypes = [w.HANDLE, c.c_int, w.WPARAM, w.LPARAM]
        user.CallNextHookEx.restype = c.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [w.HANDLE]
        kernel.GetModuleHandleW.argtypes = [w.LPCWSTR]
        kernel.GetModuleHandleW.restype = w.HMODULE

        @proc_type
        def callback(code, message, data):
            try:
                if code >= 0 and message in (0x100, 0x101, 0x104, 0x105):
                    key = c.cast(data, c.POINTER(Keyboard)).contents
                    self.correlator.observe(key.vk, message in (0x100, 0x104), key.extra)
            except Exception:
                # Always preserve the normal Windows input path.
                pass
            return user.CallNextHookEx(hook, code, message, data)

        try:
            self.thread_id = kernel.GetCurrentThreadId()
            msg = w.MSG()
            user.PeekMessageW(c.byref(msg), None, 0, 0, 0)
            hook = user.SetWindowsHookExW(13, callback, kernel.GetModuleHandleW(None), 0)
            if not hook:
                raise c.WinError(c.get_last_error())
            self.ready.set()
            while True:
                result = user.GetMessageW(c.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise c.WinError(c.get_last_error())
                user.TranslateMessage(c.byref(msg))
                user.DispatchMessageW(c.byref(msg))
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()
            if hook:
                user.UnhookWindowsHookEx(hook)
