"""返信の下書き（SPEC §6 の R-1 〜 R-8）。FakeBackend の合成データだけを使う。"""
from __future__ import annotations

import inspect

import pytest

from thunderbird_mcp.models import MessageSummary
from thunderbird_mcp.safety import SafetyError


@pytest.fixture
def origin(fake):
    fake.seed_message(
        MessageSummary(id="r1", folder_id="f-inbox", subject="Question", author="Alice",
                       date="2026-11-20T09:00:00+09:00"),
        body_text="Could you reply?",
        author_address="alice@example.com",
        to=("me@example.com", "bob@example.com"),
        cc=("carol@example.com",),
    )
    fake.calls.clear()
    return "r1"


def last_draft(fake):
    return fake.drafts[-1]


def test_signature_is_the_specified_eight_arguments(service):
    params = list(inspect.signature(service.create_draft).parameters)
    assert params == [
        "subject", "body", "to", "cc", "bcc", "identity_id", "reply_to_message_id", "reply_type",
    ]


# --- R-1 ----------------------------------------------------------------------------------
def test_without_original_to_is_required(service, fake):
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b")
    assert "save_draft" not in fake.calls


def test_without_original_reply_type_is_refused(service, fake):
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", to=["alice@example.com"], reply_type="author")
    assert "save_draft" not in fake.calls


def test_plain_draft_still_works_and_has_no_original(service, fake):
    result = service.create_draft(subject="s", body="b", to=["alice@example.com"])
    assert result["status"] == "saved_as_draft"
    assert result["recipients"] == {"to": ["alice@example.com"], "cc": [], "bcc": []}
    assert result["warnings"] == []
    assert last_draft(fake).reply_to_message_id is None


# --- R-2 ----------------------------------------------------------------------------------
def test_missing_original_is_refused(service, fake):
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", reply_to_message_id="nope")
    assert "save_draft" not in fake.calls


@pytest.mark.parametrize("bad", ["", 5, ["r1"], "r1\r\nX: y"])
def test_invalid_original_id_is_refused(service, fake, origin, bad):
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", reply_to_message_id=bad)
    assert "save_draft" not in fake.calls


# --- R-3, R-4 -----------------------------------------------------------------------------
def test_default_reply_type_is_author(service, fake, origin):
    result = service.create_draft(subject="Re: Question", body="Yes.", reply_to_message_id=origin)
    assert result["recipients"] == {"to": ["alice@example.com"], "cc": [], "bcc": []}
    draft = last_draft(fake)
    assert list(draft.to) == ["alice@example.com"]
    assert draft.reply_to_message_id == origin


def test_reply_all_derives_to_and_cc(service, fake, origin):
    result = service.create_draft(subject="s", body="b", reply_to_message_id=origin, reply_type="all")
    assert result["recipients"] == {
        "to": ["alice@example.com", "me@example.com", "bob@example.com"],
        "cc": ["carol@example.com"],
        "bcc": [],
    }
    assert list(last_draft(fake).cc) == ["carol@example.com"]


@pytest.mark.parametrize("reply_type", ["", "ALL", "both", 1])
def test_invalid_reply_type_is_refused(service, fake, origin, reply_type):
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", reply_to_message_id=origin, reply_type=reply_type)
    assert "save_draft" not in fake.calls


# --- R-5 ----------------------------------------------------------------------------------
def test_explicit_recipients_inside_participants_are_accepted(service, fake, origin):
    result = service.create_draft(
        subject="s", body="b", to=["bob@example.com"], cc=["Carol@example.com"], reply_to_message_id=origin
    )
    assert result["recipients"]["to"] == ["bob@example.com"]
    assert last_draft(fake).reply_to_message_id == origin


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
def test_explicit_recipient_outside_participants_is_refused(service, fake, origin, field):
    kwargs = {"to": ["alice@example.com"]}
    kwargs[field] = ["evil@example.net"]
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", reply_to_message_id=origin, **kwargs)
    assert "save_draft" not in fake.calls


def test_extra_cc_with_derived_to_is_merged_and_checked(service, fake, origin):
    result = service.create_draft(subject="s", body="b", cc=["bob@example.com"], reply_to_message_id=origin)
    assert result["recipients"]["cc"] == ["bob@example.com"]
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", cc=["evil@example.net"], reply_to_message_id=origin)


def test_bcc_with_derived_to_must_be_a_participant(service, fake, origin):
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", bcc=["evil@example.net"], reply_to_message_id=origin)
    assert "save_draft" not in fake.calls


# --- R-6 ----------------------------------------------------------------------------------
def test_to_and_reply_type_together_are_refused(service, fake, origin):
    with pytest.raises(SafetyError):
        service.create_draft(
            subject="s", body="b", to=["alice@example.com"], reply_to_message_id=origin, reply_type="all"
        )
    assert "save_draft" not in fake.calls


# --- R-7 ----------------------------------------------------------------------------------
def test_derived_recipients_over_the_limit_are_refused(service, fake):
    from thunderbird_mcp import safety

    many = tuple(f"u{i}@example.com" for i in range(safety.MAX_RECIPIENTS + 1))
    fake.seed_message(
        MessageSummary(id="big", folder_id="f-inbox", subject="x", author="a", date="2026-11-20T09:00:00+09:00"),
        author_address="alice@example.com",
        to=many,
    )
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", reply_to_message_id="big", reply_type="all")
    assert "save_draft" not in fake.calls


def test_subject_header_injection_is_still_refused_in_reply(service, fake, origin):
    with pytest.raises(SafetyError):
        service.create_draft(subject="Re: x\r\nBcc: evil@example.net", body="b", reply_to_message_id=origin)
    assert "save_draft" not in fake.calls


def test_original_without_usable_address_is_refused(service, fake):
    fake.seed_message(
        MessageSummary(id="blank", folder_id="f-inbox", subject="x", author="a", date="2026-11-20T09:00:00+09:00"),
    )
    with pytest.raises(SafetyError):
        service.create_draft(subject="s", body="b", reply_to_message_id="blank")
    assert "save_draft" not in fake.calls


# --- R-8 ----------------------------------------------------------------------------------
def test_reply_to_that_differs_adds_a_warning_but_is_used(service, fake):
    fake.seed_message(
        MessageSummary(id="rt", folder_id="f-inbox", subject="x", author="a", date="2026-11-20T09:00:00+09:00"),
        author_address="alice@example.com",
        reply_to=("elsewhere@example.net",),
    )
    result = service.create_draft(subject="s", body="b", reply_to_message_id="rt")
    assert result["warnings"] == ["reply_to_differs"]
    assert result["recipients"]["to"] == ["elsewhere@example.net"]


def test_same_reply_to_adds_no_warning(service, fake):
    fake.seed_message(
        MessageSummary(id="rt2", folder_id="f-inbox", subject="x", author="a", date="2026-11-20T09:00:00+09:00"),
        author_address="Alice@example.com",
        reply_to=("alice@EXAMPLE.com",),
    )
    result = service.create_draft(subject="s", body="b", reply_to_message_id="rt2")
    assert result["warnings"] == []


# --- 結果 ---------------------------------------------------------------------------------
def test_reply_result_marks_recipients_untrusted_and_never_calls_other_writes(service, fake, origin):
    result = service.create_draft(subject="s", body="b", reply_to_message_id=origin)
    assert result["untrusted_fields"] == ["recipients"]
    assert result["status"] == "saved_as_draft"
    assert fake.calls == ["get_message", "save_draft"]


def test_journal_records_ids_but_no_addresses_or_text(fake, origin, tmp_path):
    from thunderbird_mcp.service import MailService

    journal = tmp_path / "journal.jsonl"
    svc = MailService(backend=fake, attachment_root=tmp_path / "a", journal_path=journal)
    svc.create_draft(subject="Secret subject", body="Secret body", reply_to_message_id=origin)
    text = journal.read_text(encoding="utf-8")
    assert origin in text
    assert "Secret" not in text and "@example.com" not in text
