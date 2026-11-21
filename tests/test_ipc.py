"""IPC のフレーム、分割と再構成、HMAC の相互認証、鍵の扱いを確かめる。

実パイプも実際の鍵も使わない。MemoryChannel と、tmp_path の中の鍵だけを使う。
"""
from __future__ import annotations

import json
import struct

import pytest

from thunderbird_mcp import ipc
from thunderbird_mcp.ipc import (
    AuthenticationError,
    ConnectionClosed,
    IpcError,
    Reassembler,
    client_handshake,
    encode_frame,
    read_frame,
    read_message,
    server_handshake,
    split_message,
    write_frame,
    write_message,
)

from memory_channel import MemoryChannel, run_in_thread

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))


# --- フレーム --------------------------------------------------------------------------
def test_frame_roundtrip_with_unicode():
    a, b = MemoryChannel.pair()
    write_frame(a, {"op": "health", "text": "日本語 ✓"})
    assert read_frame(b, timeout=1) == {"op": "health", "text": "日本語 ✓"}


def test_frame_uses_four_byte_little_endian_length():
    raw = encode_frame({"a": 1})
    assert struct.unpack("<I", raw[:4])[0] == len(raw) - 4
    assert json.loads(raw[4:]) == {"a": 1}


def test_oversized_header_is_refused_without_reading_payload():
    a, b = MemoryChannel.pair()
    a.put_raw(struct.pack("<I", ipc.MAX_FRAME_BYTES + 1))
    with pytest.raises(IpcError):
        read_frame(b, timeout=1)


def test_encoding_an_oversized_frame_is_refused():
    with pytest.raises(IpcError):
        encode_frame({"x": "a" * (ipc.MAX_FRAME_BYTES + 1)})


def test_invalid_json_is_refused():
    a, b = MemoryChannel.pair()
    a.put_raw(struct.pack("<I", 3) + b"{x}")
    with pytest.raises(IpcError):
        read_frame(b, timeout=1)


def test_read_times_out():
    _, b = MemoryChannel.pair()
    with pytest.raises(TimeoutError):
        read_frame(b, timeout=0.05)


def test_closed_peer_is_reported():
    a, b = MemoryChannel.pair()
    a.close()
    with pytest.raises(ConnectionClosed):
        read_frame(b, timeout=1)


# --- 分割と再構成 ------------------------------------------------------------------------
def test_small_message_is_not_split():
    obj = {"id": 1, "ok": True}
    assert split_message(obj, 1000, 100) == [obj]


def test_large_message_is_split_and_reassembled_out_of_order():
    obj = {"id": 7, "ok": True, "result": {"text": "あ" * 500}}
    parts = split_message(obj, 100, 64)
    assert len(parts) > 1
    assert all("chunk" in p for p in parts)
    reassembler = Reassembler()
    results = [reassembler.feed(p) for p in reversed(parts)]
    assert results[:-1] == [None] * (len(parts) - 1)
    assert results[-1] == obj


def test_non_chunk_messages_pass_through():
    assert Reassembler().feed({"id": 1}) == {"id": 1}


def test_duplicate_chunk_is_refused():
    parts = split_message({"t": "x" * 300}, 10, 64)
    reassembler = Reassembler()
    reassembler.feed(parts[0])
    with pytest.raises(IpcError):
        reassembler.feed(parts[0])


@pytest.mark.parametrize("message", [
    {"chunk": {"id": "a", "index": 0, "total": 2}},
    {"chunk": "x", "data": "AA=="},
    {"chunk": {"id": "", "index": 0, "total": 1}, "data": "AA=="},
    {"chunk": {"id": "a", "index": 2, "total": 2}, "data": "AA=="},
    {"chunk": {"id": "a", "index": -1, "total": 2}, "data": "AA=="},
    {"chunk": {"id": "a", "index": 0, "total": 0}, "data": "AA=="},
    {"chunk": {"id": "a", "index": 0, "total": 99999}, "data": "AA=="},
    {"chunk": {"id": "a", "index": True, "total": 2}, "data": "AA=="},
    {"chunk": {"id": "a", "index": 0, "total": 1}, "data": "!!!not base64"},
])
def test_malformed_chunks_are_refused(message):
    with pytest.raises(IpcError):
        Reassembler().feed(message)


def test_reassembled_size_limit():
    parts = split_message({"t": "x" * 300}, 10, 64)
    reassembler = Reassembler(max_logical_bytes=100)
    with pytest.raises(IpcError):
        for part in parts:
            reassembler.feed(part)


def test_too_many_unfinished_messages_are_refused():
    reassembler = Reassembler()
    with pytest.raises(IpcError):
        for i in range(ipc.MAX_PENDING_ASSEMBLIES + 1):
            reassembler.feed({"chunk": {"id": f"id{i}", "index": 0, "total": 2}, "data": "AA=="})


def test_chunk_total_cannot_change():
    reassembler = Reassembler()
    reassembler.feed({"chunk": {"id": "a", "index": 0, "total": 3}, "data": "AA=="})
    with pytest.raises(IpcError):
        reassembler.feed({"chunk": {"id": "a", "index": 1, "total": 4}, "data": "AA=="})


def test_message_roundtrip_over_frames_with_small_limits(monkeypatch):
    monkeypatch.setattr(ipc, "IPC_PLAIN_LIMIT", 200)
    monkeypatch.setattr(ipc, "IPC_CHUNK_RAW_BYTES", 150)
    obj = {"id": 3, "ok": True, "result": {"data": "z" * 1000}}
    a, b = MemoryChannel.pair()
    write_message(a, obj)
    assert read_message(b, Reassembler(), timeout=1) == obj


def test_message_larger_than_one_frame_is_split_by_default():
    obj = {"id": 1, "ok": True, "result": {"data_b64": "A" * (5 * 1024 * 1024)}}
    a, b = MemoryChannel.pair()
    run_in_thread(lambda: write_message(a, obj))
    assert read_message(b, Reassembler(), timeout=10) == obj


# --- 認証 -------------------------------------------------------------------------------
def test_handshake_succeeds_with_the_same_key():
    server, client = MemoryChannel.pair()
    done = run_in_thread(lambda: server_handshake(server, KEY, timeout=2))
    client_handshake(client, KEY, timeout=2)
    done()


def test_server_refuses_a_client_with_another_key():
    server, client = MemoryChannel.pair()

    def serve():
        try:
            server_handshake(server, KEY, timeout=2)
        finally:
            server.close()

    done = run_in_thread(serve)
    with pytest.raises(IpcError):
        client_handshake(client, OTHER_KEY, timeout=2)
    with pytest.raises(AuthenticationError):
        done()


def test_client_refuses_a_host_with_another_key():
    server, client = MemoryChannel.pair()

    def serve():
        try:
            server_handshake(server, OTHER_KEY, timeout=2)
        finally:
            server.close()  # 実際のホストも、認証に失敗した接続は閉じる

    done = run_in_thread(serve)
    with pytest.raises(IpcError):
        client_handshake(client, KEY, timeout=2)
    with pytest.raises(AuthenticationError):
        done()


def test_client_refuses_a_forged_ready_message():
    server, client = MemoryChannel.pair()

    def impostor():
        write_frame(server, {"type": "challenge", "nonce": "0" * 32})
        read_frame(server, timeout=2)
        write_frame(server, {"type": "ready", "mac": "0" * 64})

    done = run_in_thread(impostor)
    with pytest.raises(AuthenticationError):
        client_handshake(client, KEY, timeout=2)
    done()


def test_a_recorded_response_cannot_be_replayed():
    # 1 回目の正しい応答を記録する
    server1, client1 = MemoryChannel.pair()
    recorded: dict = {}

    def first_session():
        server_handshake(server1, KEY, timeout=2)

    done = run_in_thread(first_session)
    original_write = client1.write_all

    def spy(data: bytes) -> None:
        if not recorded:
            recorded["frame"] = data
        original_write(data)

    client1.write_all = spy  # type: ignore[method-assign]
    client_handshake(client1, KEY, timeout=2)
    done()

    # 2 回目の接続に、記録した応答をそのまま返しても通らない（nonce が違う）
    server2, client2 = MemoryChannel.pair()
    done2 = run_in_thread(lambda: server_handshake(server2, KEY, timeout=2))
    read_frame(client2, timeout=2)
    client2.put_raw(recorded["frame"])
    with pytest.raises(AuthenticationError):
        done2()


@pytest.mark.parametrize("reply", [
    "not a dict",
    {"type": "other"},
    {"type": "response", "nonce": "short", "mac": "x"},
    {"type": "response", "nonce": "0" * 32},
])
def test_malformed_handshake_replies_are_refused(reply):
    server, client = MemoryChannel.pair()
    done = run_in_thread(lambda: server_handshake(server, KEY, timeout=2))
    read_frame(client, timeout=2)
    write_frame(client, reply)
    with pytest.raises(IpcError):
        done()


def test_non_ascii_mac_is_an_authentication_failure():
    server, client = MemoryChannel.pair()
    done = run_in_thread(lambda: server_handshake(server, KEY, timeout=2))
    read_frame(client, timeout=2)
    write_frame(client, {"type": "response", "nonce": "0" * 32, "mac": "あ" * 64})
    with pytest.raises(AuthenticationError):
        done()


def test_handshake_frames_have_a_small_size_limit():
    server, client = MemoryChannel.pair()
    done = run_in_thread(lambda: server_handshake(server, KEY, timeout=2))
    read_frame(client, timeout=2)
    write_frame(client, {"type": "response", "nonce": "0" * 32, "mac": "a" * (ipc.HANDSHAKE_MAX_FRAME_BYTES + 1)})
    with pytest.raises(IpcError):
        done()


# --- 鍵・パイプ名・op 表 -----------------------------------------------------------------
def test_key_is_created_once_and_never_overwritten(tmp_path):
    path = tmp_path / "sub" / "ipc.key"
    first = ipc.load_or_create_key(path)
    assert len(first) == ipc.KEY_BYTES
    assert ipc.load_or_create_key(path) == first
    assert ipc.load_key(path) == first


def test_short_key_file_is_refused(tmp_path):
    path = tmp_path / "ipc.key"
    path.write_bytes(b"short")
    with pytest.raises(IpcError):
        ipc.load_key(path)


def test_pipe_name_is_per_user_and_sanitized():
    assert ipc.pipe_name("alice") == r"\\.\pipe\thunderbird-mcp-alice"
    assert ipc.pipe_name("a\\b c") == r"\\.\pipe\thunderbird-mcp-a_b_c"


def test_op_table_matches_spec():
    assert len(ipc.ALLOWED_OPS) == len(set(ipc.ALLOWED_OPS)) == 13
    assert {"drafts.create", "health"} <= set(ipc.ALLOWED_OPS)
    assert "messages.move" not in ipc.ALLOWED_OPS
