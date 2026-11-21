"""移動の経路がないことを確かめる（SPEC §13: 2026-11-21 に move_messages を外した）。

ツール、service、安全検査、環境変数のどこにも、移動の経路を残さない。
"""
from __future__ import annotations

import inspect

import pytest

from thunderbird_mcp import safety
from thunderbird_mcp.service import MailService


def test_move_tool_is_not_in_the_allowlist():
    assert "move_messages" not in safety.ALLOWED_TOOLS


def test_service_has_no_move_method(service):
    assert not hasattr(service, "move_messages")


def test_service_takes_no_move_allowlist(fake, attachment_root):
    with pytest.raises(TypeError):
        MailService(backend=fake, attachment_root=attachment_root, move_allowlist=["/Archives"])
    assert "move_allowlist" not in inspect.signature(MailService).parameters


@pytest.mark.parametrize(
    "name",
    ["check_move_destination", "ALLOWED_MOVE_SPECIAL_USES", "DENIED_FOLDER_NAMES"],
)
def test_safety_has_no_move_checks(name):
    assert not hasattr(safety, name)


def test_server_does_not_read_the_move_environment_variable():
    from thunderbird_mcp import server

    assert "MOVE_ALLOWLIST" not in inspect.getsource(server)


def test_safety_has_no_denied_special_uses_table():
    assert not hasattr(safety, "DENIED_SPECIAL_USES")


def test_backends_have_no_move_method():
    from thunderbird_mcp.backend import MailBackend
    from thunderbird_mcp.backends.bridge import BridgeBackend
    from thunderbird_mcp.backends.fake import FakeBackend

    for cls in (MailBackend, FakeBackend, BridgeBackend):
        assert not hasattr(cls, "move_messages"), cls.__name__


def test_op_table_has_no_move_op():
    from thunderbird_mcp import ipc

    assert "messages.move" not in ipc.ALLOWED_OPS


def test_extension_source_has_no_move_path():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text(encoding="utf-8")
    assert 'defineOp("messages.move"' not in source
    assert "messages.move(" not in source
    assert "ALLOWED_MOVE_SPECIAL_USES" not in source


def test_probe_has_no_move_command():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "bridge_probe.py"
    spec = importlib.util.spec_from_file_location("bridge_probe_no_move", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert not hasattr(module, "SPECIAL_USE_CHOICES")
    with pytest.raises(SystemExit):
        module.main(["move", "--message-id", "1", "--folder-id", "f1", "--yes"])
