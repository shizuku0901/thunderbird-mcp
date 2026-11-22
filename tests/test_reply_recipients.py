"""返信の宛先の導出と検査（SPEC §6 の R-4、R-5、§9 の U-7）。safety の関数だけを確かめる。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from thunderbird_mcp import safety
from thunderbird_mcp.safety import SafetyError


def origin(author="alice@example.com", reply_to=(), to=("me@example.com",), cc=()):
    return SimpleNamespace(author_address=author, reply_to=tuple(reply_to), to=tuple(to), cc=tuple(cc))


def draft(to=(), cc=(), bcc=()):
    return SimpleNamespace(to=tuple(to), cc=tuple(cc), bcc=tuple(bcc))


def test_reply_types_are_fixed():
    assert safety.REPLY_TYPES == ("author", "all")


# --- R-4: 導出 ------------------------------------------------------------------------
def test_author_uses_author_address():
    assert safety.derive_reply_recipients(origin(), "author") == (["alice@example.com"], [])


def test_author_prefers_reply_to():
    detail = origin(reply_to=["list@example.com"])
    assert safety.derive_reply_recipients(detail, "author") == (["list@example.com"], [])


def test_all_collects_author_to_and_cc():
    detail = origin(to=["me@example.com", "bob@example.com"], cc=["carol@example.com"])
    to, cc = safety.derive_reply_recipients(detail, "all")
    assert to == ["alice@example.com", "me@example.com", "bob@example.com"]
    assert cc == ["carol@example.com"]


def test_all_removes_duplicates_case_insensitively_and_keeps_first_spelling():
    detail = origin(
        to=["Me@Example.com", "ME@example.com"],
        cc=["alice@EXAMPLE.com", "me@example.com", "carol@example.com", "CAROL@example.com"],
    )
    to, cc = safety.derive_reply_recipients(detail, "all")
    assert to == ["alice@example.com", "Me@Example.com"]
    assert cc == ["carol@example.com"]


def test_all_with_reply_to_puts_reply_to_first():
    detail = origin(reply_to=["list@example.com"], to=["me@example.com"])
    to, _ = safety.derive_reply_recipients(detail, "all")
    assert to == ["list@example.com", "me@example.com"]


@pytest.mark.parametrize("reply_type", ["", "ALL", "both", "reply", None, 1, ["all"]])
def test_unknown_reply_type_is_refused(reply_type):
    with pytest.raises(SafetyError):
        safety.derive_reply_recipients(origin(), reply_type)


def test_no_usable_recipient_is_refused():
    with pytest.raises(SafetyError):
        safety.derive_reply_recipients(origin(author="", to=(), cc=()), "author")


# --- U-7: 元メール由来のアドレス --------------------------------------------------------
@pytest.mark.parametrize("bad", [
    "Alice <alice@example.com>",
    "a@example.com, b@example.com",
    "not-an-address",
    "",
    "x@y",
    "a@example.com\r\nBcc: evil@example.com",
])
def test_unusable_origin_addresses_are_dropped(bad):
    detail = origin(author="alice@example.com", to=[bad, "me@example.com"])
    to, cc = safety.derive_reply_recipients(detail, "all")
    assert to == ["alice@example.com", "me@example.com"]
    assert cc == []


def test_control_characters_are_stripped_before_use():
    detail = origin(author="alice@example.com\x00")
    assert safety.derive_reply_recipients(detail, "author") == (["alice@example.com"], [])


def test_only_unusable_author_is_refused():
    with pytest.raises(SafetyError):
        safety.derive_reply_recipients(origin(author="Alice <a@example.com>"), "author")


def test_derivation_does_not_leak_mail_text_in_error():
    detail = origin(author="SECRET-TOKEN-123")
    with pytest.raises(SafetyError) as exc:
        safety.derive_reply_recipients(detail, "author")
    assert "SECRET-TOKEN-123" not in str(exc.value)


# --- R-5: 指定した宛先の範囲 -------------------------------------------------------------
def test_recipients_within_participants_are_accepted():
    detail = origin(reply_to=["list@example.com"], to=["me@example.com"], cc=["carol@example.com"])
    safety.check_reply_recipients(
        detail, draft(to=["alice@example.com", "LIST@example.com"], cc=["carol@example.com"], bcc=["me@example.com"])
    )


@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
def test_recipient_outside_participants_is_refused(field):
    kwargs = {field: ["evil@example.net"]}
    if field != "to":
        kwargs["to"] = ["alice@example.com"]
    with pytest.raises(SafetyError):
        safety.check_reply_recipients(origin(), draft(**kwargs))


def test_malformed_recipient_list_is_refused():
    with pytest.raises(SafetyError):
        safety.check_reply_recipients(origin(), SimpleNamespace(to="alice@example.com", cc=(), bcc=()))


def test_participant_error_does_not_leak_address():
    with pytest.raises(SafetyError) as exc:
        safety.check_reply_recipients(origin(), draft(to=["evil@example.net"]))
    assert "evil@example.net" not in str(exc.value)
