"""IPC: フレーム、分割と再構成、HMAC-SHA256 の相互チャレンジ応答、名前付きパイプ。

- フレーム: 4 バイト長（リトルエンディアン）＋ UTF-8 の JSON。上限は 8 MiB。
- 大きなメッセージは `{"chunk": {...}, "data": <base64>}` に分割し、受け手で組み直す。
- 認証: 双方が鍵を知っていることを、チャレンジ応答で相互に確かめる（hmac.compare_digest）。
- 名前付きパイプは Windows 専用。標準ライブラリの `_winapi` で、読み書きだけを行う。
  禁止語を含む API 名（標準ライブラリのメソッド名を含む）は使わない。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import struct
import sys
import time
from pathlib import Path
from typing import Any, Protocol

MAX_FRAME_BYTES = 8 * 1024 * 1024
HANDSHAKE_MAX_FRAME_BYTES = 4096
IPC_PLAIN_LIMIT = 4 * 1024 * 1024  # これを超える JSON は分割する
IPC_CHUNK_RAW_BYTES = 4 * 1024 * 1024  # base64 にしても 8 MiB に収まる
NM_MAX_MESSAGE_BYTES = 768 * 1024  # Native Messaging の 1 メッセージ（上限は 1 MiB と仮定して余裕を取る）
NM_CHUNK_RAW_BYTES = 512 * 1024  # base64 で約 683 KiB
MAX_LOGICAL_BYTES = 64 * 1024 * 1024  # 組み直した 1 メッセージの上限
MAX_PENDING_ASSEMBLIES = 8
KEY_BYTES = 32

# Python ↔ JS の op 固定表（SPEC §7）。ここにない op は、どちらの側も拒否する。
ALLOWED_OPS = (
    "accounts.list",
    "folders.list",
    "messages.query",
    "messages.list",
    "messages.get",
    "attachments.list",
    "attachments.read",
    "messages.mark",
    "messages.flag",
    "tags.update",
    "tags.list",
    "messages.move",
    "drafts.create",
    "health",
)


class IpcError(Exception):
    """フレームやプロトコルの違反。"""


class AuthenticationError(IpcError):
    """相手が鍵を知らない（認証に失敗した）。"""


class ConnectionClosed(IpcError):
    """相手が接続を閉じた。"""


class BridgeUnavailable(ConnectionError):
    """Thunderbird 側のホストに接続できない。"""


class Channel(Protocol):
    def read_exact(self, size: int, timeout: float | None = None) -> bytes: ...

    def write_all(self, data: bytes) -> None: ...

    def close(self) -> None: ...


# --- フレーム --------------------------------------------------------------------------
def encode_frame(obj: Any) -> bytes:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise IpcError("frame is too large")
    return struct.pack("<I", len(payload)) + payload


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("timed out waiting for the peer")
    return left


def read_frame(channel: Channel, timeout: float | None = None, max_bytes: int = MAX_FRAME_BYTES) -> Any:
    deadline = None if timeout is None else time.monotonic() + timeout
    (size,) = struct.unpack("<I", channel.read_exact(4, _remaining(deadline)))
    if size > max_bytes:
        raise IpcError("frame is too large")
    payload = channel.read_exact(size, _remaining(deadline)) if size else b""
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IpcError("frame is not valid JSON") from None


def write_frame(channel: Channel, obj: Any) -> None:
    channel.write_all(encode_frame(obj))


# --- 分割と再構成 ------------------------------------------------------------------------
def split_message(obj: Any, plain_limit: int, raw_chunk_bytes: int) -> list[Any]:
    """JSON にして plain_limit 以下ならそのまま、超えれば chunk の並びにして返す。"""
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) <= plain_limit:
        return [obj]
    if len(data) > MAX_LOGICAL_BYTES:
        raise IpcError("message is too large")
    total = math.ceil(len(data) / raw_chunk_bytes)
    chunk_id = secrets.token_hex(8)
    return [
        {
            "chunk": {"id": chunk_id, "index": index, "total": total},
            "data": base64.b64encode(data[index * raw_chunk_bytes : (index + 1) * raw_chunk_bytes]).decode("ascii"),
        }
        for index in range(total)
    ]


class Reassembler:
    """chunk を集めて、元のメッセージを返す。chunk でないメッセージはそのまま返す。"""

    def __init__(self, max_logical_bytes: int = MAX_LOGICAL_BYTES) -> None:
        self._max = max_logical_bytes
        self._pending: dict[str, dict[int, bytes]] = {}
        self._totals: dict[str, int] = {}

    def feed(self, message: Any) -> Any | None:
        if not isinstance(message, dict) or "chunk" not in message:
            return message
        info = message["chunk"]
        data = message.get("data")
        if not isinstance(info, dict) or not isinstance(data, str):
            raise IpcError("malformed chunk")
        chunk_id, index, total = info.get("id"), info.get("index"), info.get("total")
        if (
            not isinstance(chunk_id, str)
            or not 0 < len(chunk_id) <= 64
            or isinstance(index, bool)
            or isinstance(total, bool)
            or not isinstance(index, int)
            or not isinstance(total, int)
            or not 0 < total <= 4096
            or not 0 <= index < total
        ):
            raise IpcError("malformed chunk header")
        if chunk_id not in self._pending:
            if len(self._pending) >= MAX_PENDING_ASSEMBLIES:
                raise IpcError("too many unfinished messages")
            self._pending[chunk_id] = {}
            self._totals[chunk_id] = total
        if self._totals[chunk_id] != total:
            raise IpcError("chunk total changed")
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise IpcError("chunk data is not base64") from None
        parts = self._pending[chunk_id]
        if index in parts:
            raise IpcError("duplicate chunk")
        parts[index] = raw
        if sum(len(p) for p in parts.values()) > self._max:
            self._drop(chunk_id)
            raise IpcError("message is too large")
        if len(parts) < total:
            return None
        joined = b"".join(parts[i] for i in range(total))
        self._drop(chunk_id)
        try:
            return json.loads(joined.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise IpcError("reassembled message is not valid JSON") from None

    def _drop(self, chunk_id: str) -> None:
        self._pending.pop(chunk_id, None)
        self._totals.pop(chunk_id, None)


def write_message(channel: Channel, obj: Any) -> None:
    for part in split_message(obj, IPC_PLAIN_LIMIT, IPC_CHUNK_RAW_BYTES):
        write_frame(channel, part)


def read_message(channel: Channel, reassembler: Reassembler, timeout: float | None = None) -> Any:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        done = reassembler.feed(read_frame(channel, _remaining(deadline)))
        if done is not None:
            return done


# --- 鍵と認証 ----------------------------------------------------------------------------
def default_key_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "thunderbird-mcp" / "ipc.key"


def load_key(path: Path) -> bytes:
    key = Path(path).read_bytes()
    if len(key) < KEY_BYTES:
        raise IpcError("the key file is invalid")
    return key


def load_or_create_key(path: Path) -> bytes:
    """鍵がなければ、排他的に作る（既存の鍵は上書きしない）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as fh:
            fh.write(secrets.token_bytes(KEY_BYTES))
    except FileExistsError:
        pass
    return load_key(path)


def _mac(key: bytes, label: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, b"|".join((label, *parts)), hashlib.sha256).hexdigest().encode("ascii")


def _nonce(value: object) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise IpcError("malformed handshake")
    return value.encode("ascii")


def _mac_field(value: object) -> bytes:
    if not isinstance(value, str) or not value.isascii():
        raise AuthenticationError("authentication failed")
    return value.encode("ascii")


def server_handshake(channel: Channel, key: bytes, timeout: float = 5.0) -> None:
    """接続してきた相手が鍵を知っていることを確かめ、こちらも鍵を知っていることを示す。"""
    server_nonce = secrets.token_hex(16).encode("ascii")
    write_frame(channel, {"type": "challenge", "nonce": server_nonce.decode("ascii")})
    reply = read_frame(channel, timeout, HANDSHAKE_MAX_FRAME_BYTES)
    if not isinstance(reply, dict) or reply.get("type") != "response":
        raise IpcError("malformed handshake")
    client_nonce = _nonce(reply.get("nonce"))
    expected = _mac(key, b"client", server_nonce)
    if not hmac.compare_digest(expected, _mac_field(reply.get("mac"))):
        raise AuthenticationError("authentication failed")
    mac = _mac(key, b"server", client_nonce, server_nonce).decode("ascii")
    write_frame(channel, {"type": "ready", "mac": mac})


def client_handshake(channel: Channel, key: bytes, timeout: float = 5.0) -> None:
    """相手が鍵を知っていること（本物のホストであること）を確かめる。"""
    challenge = read_frame(channel, timeout, HANDSHAKE_MAX_FRAME_BYTES)
    if not isinstance(challenge, dict) or challenge.get("type") != "challenge":
        raise IpcError("malformed handshake")
    server_nonce = _nonce(challenge.get("nonce"))
    client_nonce = secrets.token_hex(16).encode("ascii")
    write_frame(
        channel,
        {
            "type": "response",
            "nonce": client_nonce.decode("ascii"),
            "mac": _mac(key, b"client", server_nonce).decode("ascii"),
        },
    )
    ready = read_frame(channel, timeout, HANDSHAKE_MAX_FRAME_BYTES)
    if not isinstance(ready, dict) or ready.get("type") != "ready":
        raise IpcError("malformed handshake")
    expected = _mac(key, b"server", client_nonce, server_nonce)
    if not hmac.compare_digest(expected, _mac_field(ready.get("mac"))):
        raise AuthenticationError("authentication failed")


# --- 名前付きパイプ（Windows 専用） -------------------------------------------------------
def pipe_name(username: str | None = None) -> str:
    user = username if username is not None else os.environ.get("USERNAME", "user")
    return r"\\.\pipe\thunderbird-mcp-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)


if sys.platform == "win32":
    import _winapi

    _ERROR_FILE_NOT_FOUND = 2
    _ERROR_ACCESS_DENIED = 5
    _ERROR_PIPE_BUSY = 231
    _ERROR_PIPE_CONNECTED = 535
    _PIPE_REJECT_REMOTE_CLIENTS = 0x8
    _BROKEN_PIPE_ERRORS = frozenset({109, 232, 233})  # BROKEN_PIPE, NO_DATA, PIPE_NOT_CONNECTED

    class HandleChannel:
        """Windows のパイプのハンドルを、Channel として読み書きする（同期、タイムアウト付き）。"""

        def __init__(self, handle: int) -> None:
            self._handle: int | None = handle

        def read_exact(self, size: int, timeout: float | None = None) -> bytes:
            handle = self._require_handle()
            deadline = None if timeout is None else time.monotonic() + timeout
            buf = bytearray()
            delay = 0.002
            while len(buf) < size:
                want = size - len(buf)
                try:
                    if deadline is not None:
                        available = _winapi.PeekNamedPipe(handle, 0)[0]
                        if available == 0:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("timed out waiting for the peer")
                            time.sleep(delay)
                            delay = min(delay * 2, 0.02)
                            continue
                        want = min(want, available)
                    data, _ = _winapi.ReadFile(handle, want)
                except OSError as exc:
                    if getattr(exc, "winerror", None) in _BROKEN_PIPE_ERRORS:
                        raise ConnectionClosed("the peer closed the connection") from None
                    raise
                if not data:
                    raise ConnectionClosed("the peer closed the connection")
                buf += data
            return bytes(buf)

        def write_all(self, data: bytes) -> None:
            handle = self._require_handle()
            view = memoryview(data)
            while view:
                try:
                    written, _ = _winapi.WriteFile(handle, bytes(view[:65536]))
                except OSError as exc:
                    if getattr(exc, "winerror", None) in _BROKEN_PIPE_ERRORS:
                        raise ConnectionClosed("the peer closed the connection") from None
                    raise
                view = view[written:]

        def close(self) -> None:
            handle, self._handle = self._handle, None
            if handle is not None:
                try:
                    _winapi.CloseHandle(handle)
                except OSError:
                    pass

        def _require_handle(self) -> int:
            if self._handle is None:
                raise ConnectionClosed("the channel is closed")
            return self._handle

    def create_pipe_instance(name: str, first: bool = False) -> int:
        """サーバー側のパイプを 1 つ作る。first なら、先に別のプロセスが作っていた場合は失敗する。"""
        open_mode = _winapi.PIPE_ACCESS_DUPLEX | (_winapi.FILE_FLAG_FIRST_PIPE_INSTANCE if first else 0)
        # バイトモード・ブロッキング・リモート接続の拒否
        pipe_mode = _winapi.PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS
        return _winapi.CreateNamedPipe(
            name, open_mode, pipe_mode, _winapi.PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, _winapi.NULL
        )

    def wait_for_client(handle: int) -> None:
        """クライアントが接続するまで待つ（ブロッキング）。"""
        try:
            _winapi.ConnectNamedPipe(handle, False)
        except OSError as exc:
            if getattr(exc, "winerror", None) != _ERROR_PIPE_CONNECTED:
                raise

    def connect_pipe(name: str, timeout: float = 3.0) -> HandleChannel:
        """クライアント側。ホストが動いていなければ、すぐ BridgeUnavailable にする。"""
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle = _winapi.CreateFile(
                    name,
                    _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                    0,
                    _winapi.NULL,
                    _winapi.OPEN_EXISTING,
                    0,
                    _winapi.NULL,
                )
            except OSError as exc:
                code = getattr(exc, "winerror", None)
                if code == _ERROR_FILE_NOT_FOUND:
                    raise BridgeUnavailable("cannot connect to Thunderbird (the native host is not running)") from None
                if code == _ERROR_ACCESS_DENIED:
                    raise BridgeUnavailable("cannot connect to Thunderbird (access denied)") from None
                if code == _ERROR_PIPE_BUSY:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise BridgeUnavailable("cannot connect to Thunderbird (the host is busy)") from None
                    try:
                        _winapi.WaitNamedPipe(name, max(1, int(min(left, 1.0) * 1000)))
                    except OSError:
                        pass
                    continue
                raise
            return HandleChannel(handle)
