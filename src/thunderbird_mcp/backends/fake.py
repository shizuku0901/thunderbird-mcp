"""メモリ上の偽バックエンド。テストと、実機なしの起動確認だけに使う。

呼ばれた公開メソッド名を `calls` に記録する。`seed_*` は記録しない。
禁止語は書かず、必要な特殊フォルダは safety の定数から作る。
"""
from __future__ import annotations

import dataclasses
from collections.abc import Iterable

from .. import safety
from ..backend import MailBackend
from ..models import (
    Account,
    AttachmentInfo,
    DraftRequest,
    DraftResult,
    Folder,
    MessageDetail,
    MessageSummary,
    SearchQuery,
    Tag,
)


class FakeBackend(MailBackend):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.drafts: list[DraftRequest] = []
        self._accounts: dict[str, Account] = {}
        self._folders: dict[str, Folder] = {}
        self._tags: dict[str, Tag] = {}
        self._messages: dict[str, MessageSummary] = {}
        self._bodies: dict[str, str] = {}
        self._attachments: dict[str, list[tuple[AttachmentInfo, bytes]]] = {}

    # --- テストの準備用（calls には記録しない） -------------------------------------
    def seed_account(self, account: Account) -> None:
        self._accounts[account.id] = account

    def seed_folder(self, folder: Folder) -> None:
        self._folders[folder.id] = folder

    def seed_tag(self, tag: Tag) -> None:
        self._tags[tag.key] = tag

    def seed_message(
        self,
        summary: MessageSummary,
        body_text: str = "",
        attachments: Iterable[tuple[AttachmentInfo, bytes]] = (),
    ) -> None:
        self._messages[summary.id] = summary
        self._bodies[summary.id] = body_text
        self._attachments[summary.id] = list(attachments)

    @classmethod
    def with_sample_data(cls) -> FakeBackend:
        """合成データだけを入れたインスタンスを返す。"""
        fb = cls()
        fb.seed_account(Account(id="acc1", name="Sample Account", type="none"))
        special = (
            ("f-inbox", "Inbox", safety.SU_INBOX),
            ("f-drafts", "Drafts", safety.SU_DRAFTS),
            ("f-archives", "Archives", safety.SU_ARCHIVES),
            ("f-sent", "Sent", safety.SU_SENT),
            ("f-templates", "Templates", safety.SU_TEMPLATES),
            ("f-bin", safety.SU_BIN.capitalize(), safety.SU_BIN),
            ("f-unwanted", safety.SU_UNWANTED.capitalize(), safety.SU_UNWANTED),
            ("f-pending", safety.SU_PENDING_OUT.capitalize(), safety.SU_PENDING_OUT),
        )
        for folder_id, name, special_use in special:
            fb.seed_folder(
                Folder(id=folder_id, account_id="acc1", name=name, path=f"/{name}", special_use=(special_use,))
            )
        fb.seed_folder(Folder(id="f-projects", account_id="acc1", name="Projects", path="/Projects"))
        fb.seed_tag(Tag(key="$label1", name="Important", color="#FF0000"))
        fb.seed_tag(Tag(key="$label2", name="Work", color="#FF9900"))
        fb.seed_message(
            MessageSummary(id="s1", folder_id="f-inbox", subject="Weekly report",
                           author="alice@example.com", date="2026-11-20T09:00:00+09:00"),
            body_text="Hello.\nThis is a synthetic message.",
        )
        fb.seed_message(
            MessageSummary(id="s2", folder_id="f-inbox", subject="Invoice",
                           author="billing@example.com", date="2026-11-20T10:00:00+09:00",
                           has_attachments=True),
            body_text="See the attachment.",
            attachments=[(
                AttachmentInfo(part_name="1.2", filename="invoice.pdf",
                               content_type="application/pdf", size=18),
                b"%PDF-1.4 synthetic",
            )],
        )
        return fb

    # --- MailBackend -------------------------------------------------------------
    def list_accounts(self) -> list[Account]:
        self.calls.append("list_accounts")
        return list(self._accounts.values())

    def list_folders(self, account_id: str | None = None) -> list[Folder]:
        self.calls.append("list_folders")
        return [f for f in self._folders.values() if account_id is None or f.account_id == account_id]

    def get_folder(self, folder_id: str) -> Folder:
        self.calls.append("get_folder")
        return self._folders[folder_id]

    def search_messages(self, query: SearchQuery) -> list[MessageSummary]:
        self.calls.append("search_messages")
        needle = query.text.casefold()
        found: list[MessageSummary] = []
        for summary in self._messages.values():
            if needle and needle not in summary.subject.casefold():
                continue
            if query.folder_id is not None and summary.folder_id != query.folder_id:
                continue
            if query.unread_only and summary.read:
                continue
            if query.flagged_only and not summary.flagged:
                continue
            if query.tag is not None and query.tag not in summary.tags:
                continue
            found.append(summary)
        return found[: max(0, query.limit)]

    def list_messages(self, folder_id: str, limit: int, offset: int) -> list[MessageSummary]:
        self.calls.append("list_messages")
        in_folder = [m for m in self._messages.values() if m.folder_id == folder_id]
        start = max(0, offset)
        return in_folder[start : start + max(0, limit)]

    def get_message(self, message_id: str) -> MessageDetail:
        self.calls.append("get_message")
        summary = self._messages[message_id]
        attachments = tuple(info for info, _ in self._attachments.get(message_id, []))
        return MessageDetail(summary=summary, body_text=self._bodies.get(message_id, ""), attachments=attachments)

    def list_attachments(self, message_id: str) -> list[AttachmentInfo]:
        self.calls.append("list_attachments")
        if message_id not in self._messages:
            raise KeyError(message_id)
        return [info for info, _ in self._attachments.get(message_id, [])]

    def read_attachment(self, message_id: str, part_name: str) -> bytes:
        self.calls.append("read_attachment")
        for info, data in self._attachments.get(message_id, []):
            if info.part_name == part_name:
                return data
        raise KeyError(part_name)

    def set_read(self, message_ids: list[str], read: bool) -> None:
        self.calls.append("set_read")
        self._update(message_ids, read=read)

    def set_flagged(self, message_ids: list[str], flagged: bool) -> None:
        self.calls.append("set_flagged")
        self._update(message_ids, flagged=flagged)

    def list_tags(self) -> list[Tag]:
        self.calls.append("list_tags")
        return list(self._tags.values())

    def update_tags(self, message_ids: list[str], add: list[str], remove: list[str]) -> None:
        self.calls.append("update_tags")
        for message_id in message_ids:
            summary = self._messages[message_id]
            tags = [t for t in summary.tags if t not in remove]
            tags.extend(t for t in add if t not in tags)
            self._messages[message_id] = dataclasses.replace(summary, tags=tuple(tags))

    def move_messages(self, message_ids: list[str], destination_folder_id: str) -> None:
        self.calls.append("move_messages")
        if destination_folder_id not in self._folders:
            raise KeyError(destination_folder_id)
        self._update(message_ids, folder_id=destination_folder_id)

    def save_draft(self, draft: DraftRequest) -> DraftResult:
        self.calls.append("save_draft")
        folder = next((f for f in self._folders.values() if safety.SU_DRAFTS in f.special_use), None)
        if folder is None:
            raise KeyError("drafts folder")
        self.drafts.append(draft)
        return DraftResult(message_id=f"draft-{len(self.drafts)}", folder_id=folder.id)

    def health(self) -> dict:
        self.calls.append("health")
        return {"backend": "fake", "ok": True, "draft_mode": "background"}

    # --- 内部 --------------------------------------------------------------------
    def _update(self, message_ids: list[str], **changes: object) -> None:
        missing = [m for m in message_ids if m not in self._messages]
        if missing:
            raise KeyError(missing[0])
        for message_id in message_ids:
            self._messages[message_id] = dataclasses.replace(self._messages[message_id], **changes)
