"""破壊的な宛先（特殊フォルダ、危険な名前）への移動を拒否することを確かめる。"""
from __future__ import annotations

import pytest

from thunderbird_mcp import safety
from thunderbird_mcp.safety import SafetyError


def _folder_with_special_use(fake, special_use):
    return next(f for f in fake.list_folders() if special_use in f.special_use)


def _assert_not_moved(fake, message_id="m1"):
    assert "move_messages" not in fake.calls
    assert fake.get_message(message_id).summary.folder_id == "f-inbox"


@pytest.mark.parametrize("special_use", sorted(safety.DENIED_SPECIAL_USES))
def test_refuses_denied_special_use(service, fake, special_use):
    destination = _folder_with_special_use(fake, special_use)
    fake.calls.clear()
    with pytest.raises(SafetyError):
        service.move_messages(["m1"], destination.id)
    _assert_not_moved(fake)


@pytest.mark.parametrize("index", range(len(safety.DENIED_FOLDER_NAMES)))
def test_refuses_denied_name_without_special_use(service, fake, index):
    with pytest.raises(SafetyError):
        service.move_messages(["m1"], f"f-name-{index}")
    _assert_not_moved(fake)


def test_refuses_denied_name_case_insensitively(service, fake):
    with pytest.raises(SafetyError):
        service.move_messages(["m1"], "f-name-upper")
    _assert_not_moved(fake)


def test_refuses_unknown_special_use(service, fake):
    """特殊フォルダは許可リスト方式。知らない specialUse は拒否する。"""
    with pytest.raises(SafetyError):
        service.move_messages(["m1"], "f-custom-su")
    _assert_not_moved(fake)


def test_refuses_unknown_folder(service, fake):
    with pytest.raises(SafetyError):
        service.move_messages(["m1"], "f-does-not-exist")
    _assert_not_moved(fake)


def test_refuses_oversized_batch(service, fake):
    ids = [f"m{i}" for i in range(safety.MAX_MESSAGES_PER_WRITE + 1)]
    with pytest.raises(SafetyError):
        service.move_messages(ids, "f-projects")
    _assert_not_moved(fake)


def test_refuses_empty_batch(service, fake):
    with pytest.raises(SafetyError):
        service.move_messages([], "f-projects")
    assert "move_messages" not in fake.calls


def test_allows_user_folder(service, fake):
    result = service.move_messages(["m1"], "f-projects")
    assert result["moved"] == ["m1"]
    assert result["destination_folder_id"] == "f-projects"
    assert fake.get_message("m1").summary.folder_id == "f-projects"


def test_allows_archives(service, fake):
    service.move_messages(["m1"], "f-archives")
    assert fake.get_message("m1").summary.folder_id == "f-archives"


def test_allowlist_restricts_destinations(fake, attachment_root):
    from thunderbird_mcp.service import MailService

    restricted = MailService(backend=fake, attachment_root=attachment_root, move_allowlist=["/Archives"])
    with pytest.raises(SafetyError):
        restricted.move_messages(["m1"], "f-projects")
    restricted.move_messages(["m1"], "f-archives")
    assert fake.get_message("m1").summary.folder_id == "f-archives"