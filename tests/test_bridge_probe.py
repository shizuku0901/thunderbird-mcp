"""scripts/bridge_probe.py の振る舞いを、実パイプも実際の鍵も使わずに確かめる（call を差し替える）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from thunderbird_mcp import ipc, safety
from thunderbird_mcp.ipc import BridgeUnavailable

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bridge_probe.py"


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("bridge_probe_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FOLDERS = [
    {"id": "f1", "name": "Inbox", "path": "/Inbox", "special_use": [safety.SU_INBOX]},
    {"id": "f2", "name": "Bin", "path": "/Bin", "special_use": [safety.SU_BIN]},
]


def fake_call(responses, seen):
    def call(op, args=None, timeout=30.0):
        seen.append((op, args))
        return responses[op]

    return call


def test_import_has_no_side_effects(probe):
    assert callable(probe.main)


def test_probe_has_no_move_command_or_helpers(probe):
    assert not hasattr(probe, "SPECIAL_USE_CHOICES")
    assert not hasattr(probe, "pick_folder")
    assert not hasattr(probe, "command_move")
    with pytest.raises(SystemExit):
        probe.main(["move", "--message-id", "1", "--folder-id", "f1", "--yes"])


def test_connection_errors_are_reported_without_a_traceback(probe, monkeypatch, capsys):
    def broken(op, args=None, timeout=30.0):
        raise BridgeUnavailable("cannot connect to Thunderbird (the native host is not running)")

    monkeypatch.setattr(probe, "call", broken)
    assert probe.main(["health"]) == 1
    assert "Cannot connect" in capsys.readouterr().err


def test_only_ops_in_the_fixed_table_are_used(probe, monkeypatch):
    seen = []
    responses = {
        "health": {"ok": True, "result": {}},
        "folders.list": {"ok": True, "result": FOLDERS},
    }
    monkeypatch.setattr(probe, "call", fake_call(responses, seen))
    probe.main(["health"])
    probe.main(["folders"])
    assert {op for op, _ in seen} == {"health", "folders.list"}
    assert {op for op, _ in seen} <= set(ipc.ALLOWED_OPS)
