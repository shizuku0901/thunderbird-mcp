"""公開ツール名が許可リストと完全一致し、余計な経路がないことを確かめる。"""
from __future__ import annotations

import asyncio

import pytest

from thunderbird_mcp import safety

# SPEC.md §6 と同じ並び。ここを変えるときは、先に SPEC を更新して人間の承認を取る。
EXPECTED_TOOLS = (
    "list_accounts",
    "list_folders",
    "search_messages",
    "list_messages",
    "get_message",
    "list_attachments",
    "save_attachment",
    "mark_read",
    "set_flagged",
    "list_tags",
    "add_tags",
    "remove_tags",
    "move_messages",
    "create_draft",
    "health_check",
)

EXPECTED_BACKEND_METHODS = frozenset({
    "list_accounts", "list_folders", "get_folder", "search_messages", "list_messages",
    "get_message", "list_attachments", "read_attachment", "set_read", "set_flagged",
    "list_tags", "update_tags", "move_messages", "save_draft", "health",
})


def test_safety_allowlist_matches_spec():
    assert tuple(safety.ALLOWED_TOOLS) == EXPECTED_TOOLS


def test_server_exposes_exactly_the_allowlist(service):
    from thunderbird_mcp.server import build_server

    tools = asyncio.run(build_server(service).list_tools())
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names)), "同名ツールが重複して登録されている"
    assert set(names) == set(EXPECTED_TOOLS)


def test_tool_descriptions_are_clean(service):
    from thunderbird_mcp.server import build_server

    for tool in asyncio.run(build_server(service).list_tools()):
        description = (tool.description or "").lower()
        assert description, f"{tool.name} に説明がない"
        for term in safety.FORBIDDEN_TERMS:
            assert term.lower() not in description, f"{tool.name} の説明に禁止語がある"


@pytest.mark.parametrize("name", EXPECTED_TOOLS)
def test_tool_names_have_no_forbidden_terms(name):
    for term in safety.FORBIDDEN_TERMS:
        assert term.lower() not in name.lower()


def test_service_implements_every_tool(service):
    for name in EXPECTED_TOOLS:
        assert callable(getattr(service, name, None)), f"MailService.{name} がない"


def test_service_has_no_forbidden_public_attributes(service):
    for attr in dir(service):
        if attr.startswith("_"):
            continue
        for term in safety.FORBIDDEN_TERMS:
            assert term.lower() not in attr.lower(), f"MailService.{attr}"


def test_backend_interface_is_fixed():
    from thunderbird_mcp.backend import MailBackend

    assert frozenset(MailBackend.__abstractmethods__) == EXPECTED_BACKEND_METHODS
    public = {n for n in dir(MailBackend) if not n.startswith("_")}
    assert public == EXPECTED_BACKEND_METHODS, f"余計な公開メソッド: {public - EXPECTED_BACKEND_METHODS}"


def test_fake_backend_follows_interface():
    from thunderbird_mcp.backend import MailBackend
    from thunderbird_mcp.backends.fake import FakeBackend

    assert issubclass(FakeBackend, MailBackend)
    for attr in dir(FakeBackend):
        if attr.startswith("_"):
            continue
        for term in safety.FORBIDDEN_TERMS:
            assert term.lower() not in attr.lower(), f"FakeBackend.{attr}"