"""Shared helpers for the standalone test suites in this directory."""
# pylint: disable=wrong-import-position

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psutil  # noqa: E402
from network.node import P2PNode  # noqa: E402


def make_node(port: int, name: str, on_message=None, on_connected=None) -> P2PNode:
    """Create and start a headless P2PNode bound to 127.0.0.1:*port*."""
    node = P2PNode(
        host="127.0.0.1",
        port=port,
        username=name,
        on_message=on_message,
        on_connected=on_connected,
    )
    node.start_server()
    return node


def wait_for_session(event: threading.Event, timeout: float = 10.0) -> bool:
    """Block until *event* is set or *timeout* seconds pass; True if it fired."""
    return event.wait(timeout)


class TestMetrics:
    """Samples this process's CPU% and RSS memory on a background thread."""

    def __init__(self, interval: float = 0.5) -> None:
        self._interval = interval
        self._process = psutil.Process(os.getpid())
        self._samples: list[tuple[float, float]] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Begin sampling in the background. Safe to call once per run."""
        self._process.cpu_percent(interval=None)  # discard the first (meaningless) reading
        self._samples.clear()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        """Stop sampling and return the summary dict."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return self.summary()

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            cpu = self._process.cpu_percent(interval=None)
            rss_mb = self._process.memory_info().rss / (1024 * 1024)
            self._samples.append((cpu, rss_mb))
            self._stop_event.wait(self._interval)

    def summary(self) -> dict:
        """Return avg/peak CPU% and RAM(MB) across all samples collected so far."""
        if not self._samples:
            return {"avg_cpu_pct": 0.0, "peak_cpu_pct": 0.0,
                     "avg_ram_mb": 0.0, "peak_ram_mb": 0.0}
        cpus = [c for c, _ in self._samples]
        rams = [r for _, r in self._samples]
        return {
            "avg_cpu_pct":  round(sum(cpus) / len(cpus), 2),
            "peak_cpu_pct": round(max(cpus), 2),
            "avg_ram_mb":   round(sum(rams) / len(rams), 2),
            "peak_ram_mb":  round(max(rams), 2),
        }


def wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Poll *predicate* until it returns True or *timeout* seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()
