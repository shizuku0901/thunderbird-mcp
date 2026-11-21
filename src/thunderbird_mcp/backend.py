"""メールバックエンドの抽象（SPEC §5）。公開メソッドは、この 15 個に固定する。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import (
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


class MailBackend(ABC):
    @abstractmethod
    def list_accounts(self) -> list[Account]: ...

    @abstractmethod
    def list_folders(self, account_id: str | None = None) -> list[Folder]: ...

    @abstractmethod
    def get_folder(self, folder_id: str) -> Folder:
        """存在しなければ KeyError。"""

    @abstractmethod
    def search_messages(self, query: SearchQuery) -> list[MessageSummary]: ...

    @abstractmethod
    def list_messages(self, folder_id: str, limit: int, offset: int) -> list[MessageSummary]: ...

    @abstractmethod
    def get_message(self, message_id: str) -> MessageDetail:
        """存在しなければ KeyError。"""

    @abstractmethod
    def list_attachments(self, message_id: str) -> list[AttachmentInfo]: ...

    @abstractmethod
    def read_attachment(self, message_id: str, part_name: str) -> bytes: ...

    @abstractmethod
    def set_read(self, message_ids: list[str], read: bool) -> None: ...

    @abstractmethod
    def set_flagged(self, message_ids: list[str], flagged: bool) -> None: ...

    @abstractmethod
    def list_tags(self) -> list[Tag]: ...

    @abstractmethod
    def update_tags(self, message_ids: list[str], add: list[str], remove: list[str]) -> None: ...

    @abstractmethod
    def move_messages(self, message_ids: list[str], destination_folder_id: str) -> None: ...

    @abstractmethod
    def save_draft(self, draft: DraftRequest) -> DraftResult: ...

    @abstractmethod
    def health(self) -> dict: ...
