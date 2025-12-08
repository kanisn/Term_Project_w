import threading
from collections import defaultdict
from typing import Dict, List


class LogStore:
    def __init__(self) -> None:
        self._logs: Dict[str, List[str]] = defaultdict(list)
        self._lock = threading.Lock()

    def append(self, channel: str, line: str) -> None:
        clean_line = line.rstrip("\n")
        with self._lock:
            self._logs[channel].append(clean_line)

    def get_logs(self, channel: str, limit: int = 2000) -> List[str]:
        with self._lock:
            logs = self._logs.get(channel, [])
            if limit:
                return logs[-limit:]
            return list(logs)

    def clear(self, channel: str) -> None:
        with self._lock:
            if channel in self._logs:
                self._logs[channel] = []


log_store = LogStore()
