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
