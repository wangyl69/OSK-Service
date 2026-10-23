import threading
import time
import unittest

from core import Correlator, Edge, OSK_MARKER
from service import HidWorker


class CorrelationTests(unittest.TestCase):
    def setUp(self):
        self.c = Correlator()
        self.c.connect(True)

    def test_interleaved_keys_only_down_edges(self):
        for key, down in [(65, True), (66, True), (65, False), (66, False)]:
            self.c.observe(key, down, OSK_MARKER)
        matches = [self.c.match(1, wait=0) for _ in range(4)]
        self.assertEqual([e.key if e else None for e in matches], [65, 66, None, None])

    def test_physical_and_unmarked_injection_rejected(self):
        self.c.observe(65, True, 0)
        self.assertIsNone(self.c.match(1, wait=0))

    def test_repeated_keydowns_are_each_accepted(self):
        for _ in range(3):
            self.c.observe(65, True, OSK_MARKER)
            self.assertIsNotNone(self.c.match(1, wait=0))

    def test_all_classes(self):
        for key, code in [(65, 1), (160, 2), (13, 3), (135, 3)]:
            self.c.observe(key, True, OSK_MARKER)
            self.assertEqual(self.c.match(code, wait=0).key, key)

    def test_mismatch_drops_instead_of_searching(self):
        self.c.observe(65, True, OSK_MARKER)
        self.c.observe(13, True, OSK_MARKER)
        self.assertIsNone(self.c.match(3, wait=0))
        self.assertIsNone(self.c.match(3, wait=0))

    def test_stale_and_reconnect_clear_observations(self):
        self.c.observe(65, True, OSK_MARKER, now=time.monotonic() - 1)
        self.assertIsNone(self.c.match(1, wait=0))
        self.c.observe(65, True, OSK_MARKER)
        self.c.connect(False)
        self.c.connect(True)
        self.assertIsNone(self.c.match(1, wait=0))

    def test_pipe_can_arrive_before_observer(self):
        timer = threading.Timer(0.005, self.c.observe, args=(65, True, OSK_MARKER))
        timer.start()
        try:
            self.assertEqual(self.c.match(1, wait=0.1).key, 65)
        finally:
            timer.join()


class MockHidTests(unittest.TestCase):
    def test_real_encoder_sends_only_keydown_and_closes_session(self):
        from haptic_mock import MockHapticHID
        from haptic_hid import (REPORT_ID_EVENT, REPORT_ID_CONTROL, OPCODE_PLAY,
                                OPCODE_SESSION_CLOSE, compute_crc8)
        reports = []
        def factory():
            device = MockHapticHID(on_report=reports.append)
            device._use_crc = True
            return device
        worker = HidWorker(factory)
        worker.start()
        try:
            self.assertTrue(worker.ready.wait(3))
            c = Correlator()
            c.connect(True)
            for down in (True, False):
                c.observe(65, down, OSK_MARKER)
                edge = c.match(1)
                if edge:
                    worker.submit(edge)
            limit = time.monotonic() + 2
            while worker.play_sent < 1 and time.monotonic() < limit:
                time.sleep(0.01)
            self.assertEqual(worker.play_sent, 1)
        finally:
            worker.stop()
        plays = [r for r in reports if r[0] == REPORT_ID_EVENT and r[2] == OPCODE_PLAY]
        self.assertEqual(len(plays), 1)
        self.assertEqual(len(plays[0]), 32)
        self.assertEqual(plays[0][31], compute_crc8(plays[0][:31]))
        self.assertTrue(any(r[0] == REPORT_ID_CONTROL and r[2] == OPCODE_SESSION_CLOSE
                            for r in reports))

    def test_stale_event_not_played(self):
        from haptic_mock import MockHapticHID
        worker = HidWorker(MockHapticHID)
        worker.start()
        try:
            self.assertTrue(worker.ready.wait(3))
            worker.submit(Edge(1, 65, True, time.monotonic() - 1))
            limit = time.monotonic() + 1
            while not worker.dropped and time.monotonic() < limit:
                time.sleep(0.01)
            self.assertEqual(worker.play_sent, 0)
            self.assertEqual(worker.dropped, 1)
        finally:
            worker.stop()


if __name__ == '__main__':
    unittest.main()
