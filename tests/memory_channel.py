"""テスト用の、メモリ上の双方向チャネル（実パイプも実際の鍵も使わない）。"""
from __future__ import annotations

import threading
import time
from typing import Callable


class _Buffer:
    def __init__(self) -> None:
        self.data = bytearray()
        self.cond = threading.Condition()
        self.closed = False


class MemoryChannel:
    """ipc.Channel と同じ形（read_exact / write_all / close）を、メモリ上で実装する。"""

    def __init__(self, incoming: _Buffer, outgoing: _Buffer) -> None:
        self._incoming = incoming
        self._outgoing = outgoing

    @classmethod
    def pair(cls) -> tuple["MemoryChannel", "MemoryChannel"]:
        a, b = _Buffer(), _Buffer()
        return cls(a, b), cls(b, a)

    def read_exact(self, size: int, timeout: float | None = None) -> bytes:
        from thunderbird_mcp.ipc import ConnectionClosed

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._incoming.cond:
            while len(self._incoming.data) < size:
                if self._incoming.closed:
                    raise ConnectionClosed("closed")
                wait = None if deadline is None else deadline - time.monotonic()
                if wait is not None and wait <= 0:
                    raise TimeoutError("timed out")
                self._incoming.cond.wait(wait)
            out = bytes(self._incoming.data[:size])
            del self._incoming.data[:size]
            return out

    def write_all(self, data: bytes) -> None:
        from thunderbird_mcp.ipc import ConnectionClosed

        with self._outgoing.cond:
            if self._outgoing.closed:
                raise ConnectionClosed("closed")
            self._outgoing.data += data
            self._outgoing.cond.notify_all()

    def put_raw(self, data: bytes) -> None:
        """相手が読む側の入力に、生のバイト列を置く（不正なフレームを作るため）。"""
        self.write_all(data)

    def close(self) -> None:
        for buf in (self._incoming, self._outgoing):
            with buf.cond:
                buf.closed = True
                buf.cond.notify_all()


def run_in_thread(fn: Callable[[], object]) -> Callable[[], object]:
    """fn を別スレッドで動かし、join して戻り値（または例外）を返す関数を返す。"""
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - テストで再送出する
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()

    def join() -> object:
        thread.join(timeout=10)
        assert not thread.is_alive(), "thread did not finish"
        if "error" in box:
            raise box["error"]  # type: ignore[misc]
        return box.get("value")

    return join
