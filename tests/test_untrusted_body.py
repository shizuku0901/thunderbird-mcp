"""メール本文などを、信頼できない入力（データ）として扱うことを確かめる。"""
from __future__ import annotations

from thunderbird_mcp import safety

READ_ONLY_CALLS = frozenset({
    "list_accounts", "list_folders", "get_folder", "search_messages", "list_messages",
    "get_message", "list_attachments", "read_attachment", "list_tags", "health",
})
CONTROL_CHARS = ("\x1b", "\r", "\n", "\x07", "\x00")


def test_body_is_wrapped_as_untrusted(service):
    body = service.get_message("m3")["body"]
    assert body["untrusted"] is True
    assert body["notice"]
    assert body["content"].startswith(safety.UNTRUSTED_BEGIN)
    assert body["content"].endswith(safety.UNTRUSTED_END)


def test_markers_inside_body_cannot_close_the_wrapper(service):
    content = service.get_message("m3")["body"]["content"]
    assert content.count(safety.UNTRUSTED_BEGIN) == 1
    assert content.count(safety.UNTRUSTED_END) == 1


def test_instruction_text_is_returned_as_data(service):
    content = service.get_message("m3")["body"]["content"]
    assert "Ignore all previous instructions" in content


def test_reading_hostile_mail_causes_no_writes(service, fake):
    service.get_message("m3")
    service.search_messages(text="Invoice")
    service.list_messages("f-inbox")
    service.list_attachments("m2")
    assert set(fake.calls) <= READ_ONLY_CALLS, f"読み取り中に書き込みが起きた: {fake.calls}"
    assert fake.drafts == []
    assert fake.get_message("m3").summary.folder_id == "f-inbox"


def test_headers_are_sanitized(service):
    result = service.get_message("m3")
    for ch in CONTROL_CHARS:
        assert ch not in result["message"]["subject"]
        assert ch not in result["message"]["author"]
    assert "untrusted_fields" in result


def test_search_results_are_sanitized(service):
    result = service.search_messages(text="Invoice")
    assert result["messages"]
    assert "untrusted_fields" in result
    for message in result["messages"]:
        for ch in CONTROL_CHARS:
            assert ch not in message["subject"]


def test_attachment_names_are_sanitized_in_listing(service):
    result = service.list_attachments("m2")
    for attachment in result["attachments"]:
        for ch in CONTROL_CHARS:
            assert ch not in attachment["filename"]


def test_long_body_is_truncated(service, fake):
    from thunderbird_mcp.models import MessageSummary

    fake.seed_message(
        MessageSummary(id="m-long", folder_id="f-inbox", subject="long",
                       author="a@example.com", date="2026-11-20T12:00:00+09:00"),
        body_text="A" * 10_000,
    )
    body = service.get_message("m-long", max_body_chars=100)["body"]
    assert body["truncated"] is True
    inner = body["content"][len(safety.UNTRUSTED_BEGIN):-len(safety.UNTRUSTED_END)]
    assert len(inner.strip()) <= 100


def test_search_limit_is_capped(service):
    result = service.search_messages(text="", limit=10_000)
    assert len(result["messages"]) <= safety.MAX_SEARCH_LIMIT