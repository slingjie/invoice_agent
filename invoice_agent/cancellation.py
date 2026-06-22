from __future__ import annotations

import threading


class CancellationControl:
    """在线程和异步识别流程之间传递取消意图。"""

    def __init__(self) -> None:
        self._mode = "none"
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def request_stop(self) -> None:
        with self._lock:
            if self._mode == "none":
                self._mode = "stop"

    def request_terminate(self) -> None:
        with self._lock:
            self._mode = "terminate"

    def should_stop_starting(self) -> bool:
        return self.mode != "none"

    def should_terminate(self) -> bool:
        return self.mode == "terminate"
