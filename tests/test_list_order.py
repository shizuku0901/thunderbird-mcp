"""list_messages は date の新しい順に並べてから offset / limit を適用する（SPEC §6）。"""
from __future__ import annotations

import pytest

from thunderbird_mcp.backends.fake import FakeBackend
from thunderbird_mcp.models import Account, Folder, MessageSummary
from thunderbird_mcp.service import MailService


@pytest.fixture
def ordered_service(tmp_path):
    fb = FakeBackend()
    fb.seed_account(Account(id="acc1", name="Sample Account", type="none"))
    fb.seed_folder(Folder(id="f-inbox", account_id="acc1", name="Inbox", path="/Inbox"))
    # 古い順に登録する。o3 は +09:00 表記で、UTC に直すと o2 と o4 の間になる。
    for mid, date in (
        ("o1", "2026-11-01T00:00:00+00:00"),
        ("o2", "2026-11-02T00:00:00+00:00"),
        ("o3", "2026-11-03T05:00:00+09:00"),
        ("o4", "2026-11-04T00:00:00+00:00"),
        ("o5", ""),
    ):
        fb.seed_message(MessageSummary(id=mid, folder_id="f-inbox", subject=mid, author="a@example.com", date=date))
    return MailService(backend=fb, attachment_root=tmp_path)


def _ids(result):
    return [m["id"] for m in result["messages"]]


def test_list_messages_returns_newest_first(ordered_service):
    assert _ids(ordered_service.list_messages("f-inbox")) == ["o4", "o3", "o2", "o1", "o5"]


def test_offset_and_limit_apply_after_sorting(ordered_service):
    assert _ids(ordered_service.list_messages("f-inbox", limit=2, offset=1)) == ["o3", "o2"]
