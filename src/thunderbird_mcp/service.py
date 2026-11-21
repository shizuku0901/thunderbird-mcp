"""MailService: 公開ツールと同名のメソッドを持ち、書き込みの前に safety の検査をかける。

server からは必ずこのクラスを通す（backend を直接呼ばない）。
メール由来の文字列は、sanitize_header か wrap_untrusted を通して返す。
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import safety
from .backend import MailBackend
from .models import MessageSummary, SearchQuery

logger = logging.getLogger(__name__)

_HARD_MAX_BODY_CHARS = 200_000


def _clamp_int(value: object, low: int, high: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise safety.SafetyError(f"{name} must be an integer")
    return max(low, min(high, value))


def _clean(text: object) -> str:
    return safety.sanitize_header(text)


class MailService:
    def __init__(
        self,
        backend: MailBackend,
        attachment_root: Path | str,
        *,
        max_body_chars: int = safety.DEFAULT_MAX_BODY_CHARS,
        move_allowlist: Iterable[str] | None = None,
        journal_path: Path | str | None = None,
    ) -> None:
        self._backend = backend
        self._attachment_root = Path(attachment_root)
        self._max_body_chars = max_body_chars
        self._move_allowlist = None if move_allowlist is None else list(move_allowlist)
        self._journal_path = None if journal_path is None else Path(journal_path)

    # --- 整形（メール由来の文字列は sanitize する） -------------------------------------
    @staticmethod
    def _summary(summary: MessageSummary) -> dict[str, Any]:
        return {
            "id": summary.id,
            "folder_id": summary.folder_id,
            "subject": _clean(summary.subject),
            "author": _clean(summary.author),
            "date": _clean(summary.date),
            "read": summary.read,
            "flagged": summary.flagged,
            "tags": [_clean(tag) for tag in summary.tags],
            "has_attachments": summary.has_attachments,
        }

    @staticmethod
    def _attachment(info: Any) -> dict[str, Any]:
        return {
            "part_name": _clean(info.part_name),
            "filename": _clean(info.filename),
            "content_type": _clean(info.content_type),
            "size": info.size,
        }

    # --- 読み取り系 -----------------------------------------------------------------
    def list_accounts(self) -> dict[str, Any]:
        accounts = [
            {"id": a.id, "name": _clean(a.name), "type": _clean(a.type)}
            for a in self._backend.list_accounts()
        ]
        return {"accounts": accounts}

    def list_folders(self, account_id: str | None = None) -> dict[str, Any]:
        folders = [
            {
                "id": f.id,
                "account_id": f.account_id,
                "name": _clean(f.name),
                "path": _clean(f.path),
                "special_use": list(f.special_use),
            }
            for f in self._backend.list_folders(account_id)
        ]
        return {"folders": folders}

    def search_messages(
        self,
        text: str = "",
        folder_id: str | None = None,
        unread_only: bool = False,
        flagged_only: bool = False,
        tag: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        query = SearchQuery(
            text=text,
            folder_id=folder_id,
            unread_only=bool(unread_only),
            flagged_only=bool(flagged_only),
            tag=tag,
            limit=_clamp_int(limit, 1, safety.MAX_SEARCH_LIMIT, "limit"),
        )
        found = self._backend.search_messages(query)[: query.limit]
        return {
            "messages": [self._summary(m) for m in found],
            "untrusted_fields": ["subject", "author", "date", "tags"],
        }

    def list_messages(self, folder_id: str, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        capped = _clamp_int(limit, 1, safety.MAX_SEARCH_LIMIT, "limit")
        start = _clamp_int(offset, 0, 1_000_000, "offset")
        found = self._backend.list_messages(folder_id, capped, start)[:capped]
        return {
            "messages": [self._summary(m) for m in found],
            "untrusted_fields": ["subject", "author", "date", "tags"],
        }

    def get_message(self, message_id: str, max_body_chars: int | None = None) -> dict[str, Any]:
        if max_body_chars is None:
            limit = self._max_body_chars
        else:
            limit = _clamp_int(max_body_chars, 1, _HARD_MAX_BODY_CHARS, "max_body_chars")
        detail = self._backend.get_message(message_id)
        message = self._summary(detail.summary)
        message["attachments"] = [self._attachment(a) for a in detail.attachments]
        return {
            "message": message,
            "body": safety.wrap_untrusted(detail.body_text, limit),
            "untrusted_fields": [
                "message.subject",
                "message.author",
                "message.date",
                "message.tags",
                "message.attachments",
                "body.content",
            ],
        }

    def list_attachments(self, message_id: str) -> dict[str, Any]:
        attachments = [self._attachment(a) for a in self._backend.list_attachments(message_id)]
        return {"attachments": attachments, "untrusted_fields": ["part_name", "filename", "content_type"]}

    def list_tags(self) -> dict[str, Any]:
        tags = [
            {"key": t.key, "name": _clean(t.name), "color": _clean(t.color)}
            for t in self._backend.list_tags()
        ]
        return {"tags": tags}

    def health_check(self) -> dict[str, Any]:
        return dict(self._backend.health())

    # --- 書き込み系 -----------------------------------------------------------------
    def _journal(self, tool: str, **fields: Any) -> None:
        """操作ジャーナル（JSONL）に追記する。本文と件名は書かない。"""
        if self._journal_path is None:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tool": tool, **fields}
        try:
            self._journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self._journal_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            logger.error("could not write the operation journal")

    def save_attachment(self, message_id: str, part_name: str, filename: str | None = None) -> dict[str, Any]:
        if not isinstance(message_id, str) or not isinstance(part_name, str):
            raise safety.SafetyError("message_id and part_name must be strings")
        info = next(
            (a for a in self._backend.list_attachments(message_id) if a.part_name == part_name),
            None,
        )
        if info is None:
            raise KeyError(part_name)
        if info.size > safety.MAX_ATTACHMENT_BYTES:
            raise safety.SafetyError("attachment is too large")
        if filename is not None:
            name = filename
        else:
            name = info.filename or "attachment_" + re.sub(r"[^A-Za-z0-9.]", "_", part_name)
        # 検査は読み出しの前に行い、不正な名前なら本体を読まない
        safety.safe_attachment_path(self._attachment_root, name)
        data = self._backend.read_attachment(message_id, part_name)
        if len(data) > safety.MAX_ATTACHMENT_BYTES:
            raise safety.SafetyError("attachment is too large")
        for _ in range(100):
            path = safety.safe_attachment_path(self._attachment_root, name)
            try:
                with open(path, "xb") as fh:
                    fh.write(data)
            except FileExistsError:
                continue
            self._journal("save_attachment", message_id=message_id, part_name=part_name, path=str(path))
            return {"path": str(path), "size": len(data)}
        raise safety.SafetyError("could not choose a free filename")
