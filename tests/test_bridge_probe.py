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


def test_special_use_names_map_to_safety_constants(probe):
    assert probe.SPECIAL_USE_CHOICES["bin"] == safety.SU_BIN
    assert probe.SPECIAL_USE_CHOICES["pending_out"] == safety.SU_PENDING_OUT


def test_pick_folder(probe):
    assert probe.pick_folder(FOLDERS, safety.SU_BIN)["id"] == "f2"
    assert probe.pick_folder(FOLDERS, safety.SU_SENT) is None


def test_move_requires_confirmation_and_issues_nothing(probe, monkeypatch):
    seen = []
    monkeypatch.setattr(probe, "call", fake_call({}, seen))
    assert probe.main(["move", "--message-id", "1", "--special-use", "bin"]) == 2
    assert seen == []


def test_refusal_by_the_extension_is_the_expected_outcome(probe, monkeypatch, capsys):
    seen = []
    responses = {
        "folders.list": {"ok": True, "result": FOLDERS},
        "messages.move": {"ok": False, "code": "refused", "error": "refused: destination folder is not allowed"},
    }
    monkeypatch.setattr(probe, "call", fake_call(responses, seen))
    assert probe.main(["move", "--message-id", "7", "--special-use", "bin", "--yes"]) == 0
    assert seen[-1] == ("messages.move", {"message_ids": ["7"], "destination_folder_id": "f2"})
    assert "expected for AC-W3" in capsys.readouterr().out


def test_missing_refusal_is_reported_loudly(probe, monkeypatch, capsys):
    responses = {"folders.list": {"ok": True, "result": FOLDERS}, "messages.move": {"ok": True, "result": None}}
    monkeypatch.setattr(probe, "call", fake_call(responses, []))
    assert probe.main(["move", "--message-id", "7", "--special-use", "bin", "--yes"]) == 3
    assert "did NOT refuse" in capsys.readouterr().out


def test_other_failures_do_not_confirm_ac_w3(probe, monkeypatch):
    responses = {"folders.list": {"ok": True, "result": FOLDERS}, "messages.move": {"ok": False, "error": "boom"}}
    monkeypatch.setattr(probe, "call", fake_call(responses, []))
    assert probe.main(["move", "--message-id", "7", "--folder-id", "f2", "--yes"]) == 1


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
        "messages.move": {"ok": False, "code": "refused", "error": "refused: x"},
    }
    monkeypatch.setattr(probe, "call", fake_call(responses, seen))
    probe.main(["health"])
    probe.main(["folders"])
    probe.main(["move", "--message-id", "1", "--folder-id", "f2", "--yes"])
    assert {op for op, _ in seen} <= set(ipc.ALLOWED_OPS)
