"""メール操作で使うデータクラス（SPEC §5）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    type: str


@dataclass(frozen=True)
class Folder:
    id: str
    account_id: str
    name: str
    path: str
    special_use: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tag:
    key: str
    name: str
    color: str = ""


@dataclass(frozen=True)
class AttachmentInfo:
    part_name: str
    filename: str
    content_type: str
    size: int


@dataclass(frozen=True)
class MessageSummary:
    id: str
    folder_id: str
    subject: str
    author: str
    date: str
    read: bool = False
    flagged: bool = False
    tags: tuple[str, ...] = ()
    has_attachments: bool = False


@dataclass(frozen=True)
class MessageDetail:
    summary: MessageSummary
    body_text: str
    attachments: tuple[AttachmentInfo, ...] = ()


@dataclass(frozen=True)
class SearchQuery:
    text: str = ""
    folder_id: str | None = None
    unread_only: bool = False
    flagged_only: bool = False
    tag: str | None = None
    limit: int = 20


@dataclass(frozen=True)
class DraftRequest:
    to: Sequence[str]
    subject: str
    body: str
    cc: Sequence[str] = ()
    bcc: Sequence[str] = ()
    identity_id: str | None = None


@dataclass(frozen=True)
class DraftResult:
    message_id: str
    folder_id: str
