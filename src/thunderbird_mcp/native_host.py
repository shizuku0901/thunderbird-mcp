"""Native Messaging のホスト。Thunderbird（拡張）と、MCP サーバーの間を中継するだけ。

- 拡張とは、標準入出力（4 バイト長＋JSON、1 メッセージ 768 KiB 以下に分割）でやり取りする。
- MCP サーバーとは、名前付きパイプ（HMAC の相互認証つき）でやり取りする。
- 判断はしない。ただし、op の固定表（ipc.ALLOWED_OPS）にない要求は、拡張へ渡さずに拒否する。

このモジュールだけは、標準出力を使ってよい（Native Messaging の通信路のため）。
ログは stderr に出す。
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import struct
import sys
import threading
from typing import Any, BinaryIO

from . import ipc
from .ipc import (
    ALLOWED_OPS,
    AuthenticationError,
    Channel,
    ConnectionClosed,
    IpcError,
    Reassembler,
    read_message,
    server_handshake,
    split_message,
    write_message,
)

logger = logging.getLogger("thunderbird_mcp.native_host")

NM_INCOMING_MAX_BYTES = 2 * 1024 * 1024
MAX_CONNECTIONS = 8


# --- Native Messaging のフレーム ----------------------------------------------------------
def _read_exact(stream: BinaryIO, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def read_nm_message(stream: BinaryIO) -> Any | None:
    """1 メッセージを読む。入力が閉じられていれば None。"""
    header = _read_exact(stream, 4)
    if not header:
        return None
    if len(header) < 4:
        raise IpcError("truncated native messaging header")
    (size,) = struct.unpack("=I", header)
    if size > NM_INCOMING_MAX_BYTES:
        raise IpcError("native messaging message is too large")
    payload = _read_exact(stream, size)
    if len(payload) < size:
        raise IpcError("truncated native messaging message")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IpcError("native messaging message is not valid JSON") from None


def write_nm_message(stream: BinaryIO, obj: Any) -> None:
    """obj を書く。768 KiB を超えるなら、chunk に分割して複数のメッセージにする。"""
    for part in split_message(obj, ipc.NM_MAX_MESSAGE_BYTES, ipc.NM_CHUNK_RAW_BYTES):
        data = json.dumps(part, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        stream.write(struct.pack("=I", len(data)) + data)
        stream.flush()


# --- 中継 --------------------------------------------------------------------------------
class RelayHost:
    def __init__(
        self,
        nm_in: BinaryIO,
        nm_out: BinaryIO,
        key: bytes,
        *,
        request_timeout: float = 120.0,
        handshake_timeout: float = 5.0,
        idle_timeout: float = 30.0,
    ) -> None:
        self._nm_in = nm_in
        self._nm_out = nm_out
        self._key = key
        self._request_timeout = request_timeout
        self._handshake_timeout = handshake_timeout
        self._idle_timeout = idle_timeout
        self._ids = itertools.count(1)
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._closed = threading.Event()
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

    # 拡張 → ホスト
    def pump_extension(self) -> None:
        """拡張からのメッセージを、入力が閉じられるまで読み、待っている要求へ振り分ける。"""
        reassembler = Reassembler()
        try:
            while True:
                message = read_nm_message(self._nm_in)
                if message is None:
                    break
                done = reassembler.feed(message)
                if done is None:
                    continue
                self._route(done)
        except IpcError as exc:
            logger.error("invalid message from the extension: %s", exc)
        finally:
            self._fail_all("the extension disconnected")

    def _route(self, message: Any) -> None:
        if not isinstance(message, dict):
            logger.warning("ignored a non-object message from the extension")
            return
        with self._pending_lock:
            box = self._pending.get(message.get("id"))  # type: ignore[arg-type]
        if box is None:
            logger.warning("ignored a response nobody is waiting for")
            return
        try:
            box.put_nowait(message)
        except queue.Full:
            pass

    def _fail_all(self, reason: str) -> None:
        self._closed.set()
        with self._pending_lock:
            boxes = list(self._pending.values())
        for box in boxes:
            try:
                box.put_nowait({"ok": False, "error": reason})
            except queue.Full:
                pass

    # ホスト → 拡張
    def call_extension(self, op: str, args: dict[str, Any]) -> dict[str, Any]:
        with self._pending_lock:
            if self._closed.is_set():
                return {"ok": False, "error": "the extension disconnected"}
            request_id = next(self._ids)
            box: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = box
        try:
            try:
                with self._write_lock:
                    write_nm_message(self._nm_out, {"id": request_id, "op": op, "args": args})
            except (OSError, ValueError):
                return {"ok": False, "error": "the extension disconnected"}
            try:
                return box.get(timeout=self._request_timeout)
            except queue.Empty:
                return {"ok": False, "error": "the extension did not answer in time"}
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    # MCP サーバー ↔ ホスト
    def handle_request(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            return {"id": None, "ok": False, "error": "malformed request"}
        request_id = request.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
            request_id = None
        op = request.get("op")
        args = request.get("args", {})
        if op not in ALLOWED_OPS or not isinstance(args, dict):
            return {"id": request_id, "ok": False, "code": "refused", "error": "refused: unknown op"}
        response = self.call_extension(op, args)
        return {**response, "id": request_id}

    def serve_connection(self, channel: Channel) -> None:
        """1 本の接続を処理する。認証に失敗した接続は、何も処理せずに閉じる。"""
        try:
            server_handshake(channel, self._key, timeout=self._handshake_timeout)
            reassembler = Reassembler()
            while True:
                request = read_message(channel, reassembler, timeout=self._idle_timeout)
                write_message(channel, self.handle_request(request))
        except ConnectionClosed:
            pass
        except AuthenticationError:
            logger.warning("refused a connection that failed authentication")
        except (IpcError, TimeoutError, OSError) as exc:
            logger.warning("closed a connection: %s", exc)
        finally:
            channel.close()

    def serve_pipe(self, name: str) -> None:
        """名前付きパイプで接続を受け続ける（Windows 専用。戻らない）。"""
        first = True
        while not self._closed.is_set():
            handle = ipc.create_pipe_instance(name, first=first)
            first = False
            ipc.wait_for_client(handle)
            channel = ipc.HandleChannel(handle)
            if not self._slots.acquire(blocking=False):
                channel.close()
                continue
            threading.Thread(target=self._serve_and_release, args=(channel,), daemon=True).start()

    def _serve_and_release(self, channel: Channel) -> None:
        try:
            self.serve_connection(channel)
        finally:
            self._slots.release()


def main() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    key = ipc.load_or_create_key(ipc.default_key_path())
    host = RelayHost(sys.stdin.buffer, sys.stdout.buffer, key)

    def run_pipe_server() -> None:
        try:
            host.serve_pipe(ipc.pipe_name())
        except Exception:  # noqa: BLE001 - パイプがなければ中継できないので、プロセスごと終える
            logger.exception("the pipe server failed")
            os._exit(1)

    threading.Thread(target=run_pipe_server, daemon=True).start()
    host.pump_extension()  # Thunderbird が入力を閉じたら戻る
    return 0


if __name__ == "__main__":
    sys.exit(main())
