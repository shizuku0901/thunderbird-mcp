"""native_host の Native Messaging のフレームと、中継の動きを確かめる。

実パイプも実際の鍵も、本物の標準入出力も使わない。os.pipe の無名パイプと MemoryChannel だけを使う。
"""
from __future__ import annotations

import io
import json
import os
import struct
import threading
import time

import pytest

from thunderbird_mcp import ipc
from thunderbird_mcp.ipc import IpcError, Reassembler, client_handshake, read_message, write_message
from thunderbird_mcp.native_host import RelayHost, read_nm_message, write_nm_message

from memory_channel import MemoryChannel, run_in_thread

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


def echo(message):
    return {"id": message["id"], "ok": True, "result": {"op": message["op"], "args": message["args"]}}


# --- Native Messaging のフレーム ----------------------------------------------------------
def test_nm_roundtrip_uses_native_endian_length():
    stream = io.BytesIO()
    write_nm_message(stream, {"a": "日本語"})
    raw = stream.getvalue()
    assert struct.unpack("=I", raw[:4])[0] == len(raw) - 4
    assert read_nm_message(io.BytesIO(raw)) == {"a": "日本語"}


def test_nm_eof_returns_none():
    assert read_nm_message(io.BytesIO(b"")) is None


@pytest.mark.parametrize("raw", [
    b"\x01\x00",
    struct.pack("=I", 10) + b"abc",
    struct.pack("=I", 3) + b"{x}",
    struct.pack("=I", 3 * 1024 * 1024),
])
def test_nm_malformed_input_is_refused(raw):
    with pytest.raises(IpcError):
        read_nm_message(io.BytesIO(raw))


def test_nm_large_message_is_split_below_the_limit_and_restored():
    obj = {"id": 1, "ok": True, "result": {"data": "あ" * (900 * 1024)}}
    stream = io.BytesIO()
    write_nm_message(stream, obj)
    stream.seek(0)
    reassembler = Reassembler()
    sizes, restored = [], None
    while (message := read_nm_message(stream)) is not None:
        sizes.append(len(json.dumps(message, ensure_ascii=False).encode("utf-8")))
        restored = reassembler.feed(message) or restored
    assert len(sizes) > 1
    assert max(sizes) <= ipc.NM_MAX_MESSAGE_BYTES
    assert restored == obj


# --- 中継 --------------------------------------------------------------------------------
class Harness:
    """RelayHost と、偽の拡張（別スレッド）をつなぐ。"""

    def __init__(self, handler, **host_kwargs):
        r1, w1 = os.pipe()
        r2, w2 = os.pipe()
        self._files = [os.fdopen(w1, "wb"), os.fdopen(r1, "rb"), os.fdopen(w2, "wb"), os.fdopen(r2, "rb")]
        self.host_out, self.ext_in, self.ext_out, self.host_in = self._files
        self.host = RelayHost(self.host_in, self.host_out, KEY, **host_kwargs)
        self.seen: list[dict] = []
        self._handler = handler
        self._ext_lock = threading.Lock()
        self.pump = run_in_thread(self.host.pump_extension)
        threading.Thread(target=self._extension_loop, daemon=True).start()

    def _extension_loop(self):
        reassembler = Reassembler()
        try:
            while (message := read_nm_message(self.ext_in)) is not None:
                done = reassembler.feed(message)
                if done is None:
                    continue
                self.seen.append(done)
                reply = self._handler(done)
                if reply is not None:
                    with self._ext_lock:
                        write_nm_message(self.ext_out, reply)
        except (OSError, ValueError, IpcError):
            pass

    def disconnect(self):
        """拡張が止まった（入力が閉じられた）状態にする。"""
        self.ext_out.close()

    def connect(self, key=KEY):
        """MCP サーバー側の接続を 1 本つくり、認証済みのクライアント側を返す。"""
        server_channel, client_channel = MemoryChannel.pair()
        self.served = run_in_thread(lambda: self.host.serve_connection(server_channel))
        client_handshake(client_channel, key, timeout=3)
        return client_channel

    def close(self):
        for f in self._files:
            try:
                f.close()
            except OSError:
                pass


@pytest.fixture
def harness():
    made: list[Harness] = []

    def make(handler=echo, **kwargs):
        h = Harness(handler, **kwargs)
        made.append(h)
        return h

    yield make
    for h in made:
        h.close()


def call(client, request, timeout=10):
    write_message(client, request)
    return read_message(client, Reassembler(), timeout=timeout)


def test_request_is_relayed_and_the_client_id_is_restored(harness):
    h = harness()
    client = h.connect()
    response = call(client, {"id": 77, "op": "messages.query", "args": {"text": "x"}})
    assert response["id"] == 77
    assert response["ok"] is True
    assert response["result"] == {"op": "messages.query", "args": {"text": "x"}}
    assert [m["op"] for m in h.seen] == ["messages.query"]
    # 拡張には、ホストが付けた内部の id が渡る（接続ごとの id が衝突しないように）
    assert isinstance(h.seen[0]["id"], int)


@pytest.mark.parametrize("op", ["messages.delete", "compose.send", "", None, 5, "health "])
def test_unknown_ops_are_refused_without_reaching_the_extension(harness, op):
    h = harness()
    client = h.connect()
    response = call(client, {"id": 1, "op": op, "args": {}})
    assert response["ok"] is False
    assert response["code"] == "refused"
    assert h.seen == []


@pytest.mark.parametrize("request_obj", [
    "text",
    ["messages.query"],
    {"id": 1, "op": "health", "args": "not-a-dict"},
])
def test_malformed_requests_are_refused(harness, request_obj):
    h = harness()
    client = h.connect()
    response = call(client, request_obj)
    assert response["ok"] is False
    assert h.seen == []


def test_every_allowed_op_is_relayed(harness):
    h = harness()
    client = h.connect()
    reassembler = Reassembler()
    for i, op in enumerate(ipc.ALLOWED_OPS):
        write_message(client, {"id": i, "op": op, "args": {}})
        assert read_message(client, reassembler, timeout=10)["ok"] is True
    assert [m["op"] for m in h.seen] == list(ipc.ALLOWED_OPS)


def test_connection_with_the_wrong_key_is_refused_and_nothing_is_relayed(harness):
    h = harness()
    server_channel, client_channel = MemoryChannel.pair()
    served = run_in_thread(lambda: h.host.serve_connection(server_channel))
    with pytest.raises(IpcError):
        client_handshake(client_channel, OTHER_KEY, timeout=3)
    served()  # serve_connection は例外を投げずに接続を閉じて戻る
    assert h.seen == []


def test_concurrent_connections_do_not_mix_responses(harness):
    def slow_echo(message):
        time.sleep(0.05)
        return echo(message)

    h = harness(slow_echo)
    results = {}

    def worker(name):
        client = h.connect()
        results[name] = call(client, {"id": name, "op": "health", "args": {"who": name}})

    joins = [run_in_thread(lambda n=n: worker(n)) for n in (101, 102, 103, 104)]
    for join in joins:
        join()
    for name, response in results.items():
        assert response["id"] == name
        assert response["result"]["args"] == {"who": name}


def test_large_response_crosses_both_chunking_layers(harness):
    payload = "A" * (5 * 1024 * 1024)

    def big(message):
        return {"id": message["id"], "ok": True, "result": {"data_b64": payload}}

    h = harness(big)
    client = h.connect()
    response = call(client, {"id": 1, "op": "attachments.read", "args": {"message_id": "1", "part_name": "1.2"}})
    assert response["ok"] is True
    assert response["result"]["data_b64"] == payload


def test_large_request_is_split_towards_the_extension(harness):
    body = "本文" * (400 * 1024)  # 768 KiB を超える
    h = harness()
    client = h.connect()
    response = call(client, {"id": 1, "op": "drafts.create", "args": {"body": body}})
    assert response["result"]["args"]["body"] == body


def test_unanswered_request_times_out(harness):
    h = harness(lambda message: None, request_timeout=0.2)
    client = h.connect()
    response = call(client, {"id": 5, "op": "health", "args": {}})
    assert response == {"id": 5, "ok": False, "error": "the extension did not answer in time"}


def test_extension_disconnect_fails_waiting_and_later_requests(harness):
    h = harness(lambda message: None, request_timeout=30)
    client = h.connect()
    threading.Timer(0.2, h.disconnect).start()
    first = call(client, {"id": 1, "op": "health", "args": {}})
    assert first["ok"] is False and "disconnected" in first["error"]
    h.pump()
    later = call(client, {"id": 2, "op": "health", "args": {}})
    assert later["ok"] is False and "disconnected" in later["error"]


def test_idle_connection_is_closed(harness):
    h = harness(idle_timeout=0.2)
    h.connect()
    h.served()  # 要求が来ないので、serve_connection が戻る


def test_stray_response_is_ignored(harness):
    h = harness()
    # 誰も待っていない id の応答が来ても、落ちずに無視する
    write_nm_message(h.ext_out, {"id": 9999, "ok": True, "result": {}})
    client = h.connect()
    assert call(client, {"id": 1, "op": "health", "args": {}})["ok"] is True
