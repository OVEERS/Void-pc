from __future__ import annotations

import socket


class SingleInstance:
    """Не даёт запустить второй экземпляр сервиса (через занятый локальный порт)."""

    def __init__(self, port: int = 47831):
        self.port = port
        self._sock: socket.socket | None = None

    def acquire(self) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", self.port))
            s.listen(1)
        except OSError:
            s.close()
            return False
        self._sock = s
        return True

    def release(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
