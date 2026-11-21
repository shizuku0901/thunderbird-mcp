"""MCP サーバーが stdout に JSON-RPC 以外を出さないことを確かめる。"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "thunderbird_mcp"
STDOUT_ALLOWED_FILES = {"native_host.py"}  # Thunderbird との Native Messaging は stdout を使う
STDOUT_PATTERNS = (
    re.compile(r"\bprint\s*\("),
    re.compile(r"\bsys\s*\.\s*(__)?stdout\b"),
)
TIMEOUT_SEC = 30.0


def test_static_no_stdout_writes_in_server_code():
    assert PACKAGE.is_dir(), "src/thunderbird_mcp がまだない（Red）"
    violations = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in STDOUT_ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in STDOUT_PATTERNS:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(ROOT)}:{line_no}: {match.group(0)}")
    assert not violations, "stdout への書き込みがある:\n" + "\n".join(violations)


def _reader(stream, sink: queue.Queue) -> None:
    for raw in iter(stream.readline, b""):
        sink.put(raw)
    sink.put(None)


def _parse(raw: bytes) -> dict:
    line = raw.decode("utf-8", errors="replace").strip()
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        pytest.fail(f"stdout に JSON-RPC 以外が出た: {line!r}")
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        pytest.fail(f"stdout に JSON-RPC 2.0 以外が出た: {line!r}")
    return message


def _wait_for(sink: queue.Queue, want_id: int, deadline: float, stderr_log: Path) -> dict:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail(f"id={want_id} の応答がタイムアウトした")
        try:
            raw = sink.get(timeout=remaining)
        except queue.Empty:
            pytest.fail(f"id={want_id} の応答がタイムアウトした")
        if raw is None:
            tail = stderr_log.read_text(encoding="utf-8", errors="replace")[-2000:]
            pytest.fail(f"サーバーが応答前に終了した。stderr:\n{tail}")
        message = _parse(raw)
        if message.get("id") == want_id:
            return message


def test_stdio_server_emits_only_jsonrpc(tmp_path: Path):
    from thunderbird_mcp import safety

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["THUNDERBIRD_MCP_BACKEND"] = "fake"
    env["THUNDERBIRD_MCP_ATTACHMENT_DIR"] = str(tmp_path / "attachments")
    env["THUNDERBIRD_MCP_JOURNAL"] = str(tmp_path / "journal.jsonl")
    stderr_log = tmp_path / "server-stderr.log"

    with stderr_log.open("wb") as err:
        proc = subprocess.Popen(
            [sys.executable, "-m", "thunderbird_mcp.server"],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
        )
        sink: queue.Queue = queue.Queue()
        threading.Thread(target=_reader, args=(proc.stdout, sink), daemon=True).start()
        deadline = time.monotonic() + TIMEOUT_SEC

        def post(message: dict) -> None:
            proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            proc.stdin.flush()

        try:
            post({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            })
            initialized = _wait_for(sink, 1, deadline, stderr_log)
            assert "result" in initialized, initialized

            post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            listed = _wait_for(sink, 2, deadline, stderr_log)
            names = {tool["name"] for tool in listed["result"]["tools"]}
            assert names == set(safety.ALLOWED_TOOLS)

            proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass  # EOF で終了しないのは stdout 汚染ではないので、ここでは失敗にしない
            while True:
                try:
                    raw = sink.get(timeout=2)
                except queue.Empty:
                    break
                if raw is None:
                    break
                _parse(raw)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)