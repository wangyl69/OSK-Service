"""Correlation for the legacy, edge-less HybridSED byte protocol."""
from collections import Counter, deque
from dataclasses import dataclass
import threading
import time

OSK_MARKER = 0x5345445F4F534B


@dataclass(frozen=True)
class Edge:
    code: int
    key: int
    down: bool
    created: float


class Correlator:
    def __init__(self, voice=135, modifiers=None, special=None, max_age=0.05):
        self.voice = voice
        self.modifiers = set(modifiers if modifiers is not None else
                             [16, 17, 18, 160, 161, 162, 163, 164, 165])
        self.special = set(special if special is not None else [32, 13, 8])
        self.max_age = max_age
        self.cv = threading.Condition()
        self.edges = deque()
        self.connected = False
        self.stats = Counter()

    def connect(self, active):
        with self.cv:
            self.connected = active
            self.edges.clear()
            self.cv.notify_all()

    def observe(self, key, down, marker, now=None):
        if marker != OSK_MARKER:
            return
        codes = []
        if key == self.voice:
            codes.append(3)
        if key in self.modifiers:
            codes.append(2)
        if key in self.special:
            codes.append(3)
        if not codes:
            codes.append(1)
        with self.cv:
            if not self.connected:
                return
            if len(self.edges) + len(codes) > 256:
                self.edges.clear()
                self.stats['observation_overflow'] += 1
                return
            for code in codes:
                self.edges.append(Edge(code, key, down,
                                       time.monotonic() if now is None else now))
            self.cv.notify_all()

    def match(self, code, wait=0.02):
        deadline = time.monotonic() + wait
        with self.cv:
            while self.connected and not self.edges:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.cv.wait(remaining)
            if not self.edges:
                self.stats['unmatched'] += 1
                return None
            edge = self.edges.popleft()
            if edge.code != code or time.monotonic() - edge.created > self.max_age:
                # Never skip forward looking for a convenient same-type key.
                self.edges.clear()
                self.stats['desynchronized'] += 1
                return None
            if not edge.down:
                self.stats['keyup_ignored'] += 1
                return None
            self.stats['keydown_matched'] += 1
            return edge
