"""MailService: 公開ツールと同名のメソッドを持ち、書き込みの前に safety の検査をかける。

server からは必ずこのクラスを通す（backend を直接呼ばない）。
メール由来の文字列は、sanitize_header か wrap_untrusted を通して返す。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import safety
from .backend import MailBackend
from .models import DraftRequest, MessageSummary, SearchQuery

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
        journal_path: Path | str | None = None,
    ) -> None:
        self._backend = backend
        self._attachment_root = Path(attachment_root)
        self._max_body_chars = max_body_chars
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
                "special_use": [_clean(use) for use in f.special_use],
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
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        start, end = safety.check_date_range(since, until)
        query = SearchQuery(
            text=text,
            folder_id=folder_id,
            unread_only=bool(unread_only),
            flagged_only=bool(flagged_only),
            tag=tag,
            limit=_clamp_int(limit, 1, safety.MAX_SEARCH_LIMIT, "limit"),
            since=start,
            until=end,
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
            {"key": _clean(t.key), "name": _clean(t.name), "color": _clean(t.color)}
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

    def mark_read(self, message_ids: list[str], read: bool = True) -> dict[str, Any]:
        ids = safety.check_batch(message_ids)
        if not isinstance(read, bool):
            raise safety.SafetyError("read must be a boolean")
        self._backend.set_read(ids, read)
        self._journal("mark_read", message_ids=ids, read=read)
        return {"updated": ids, "read": read}

    def set_flagged(self, message_ids: list[str], flagged: bool = True) -> dict[str, Any]:
        ids = safety.check_batch(message_ids)
        if not isinstance(flagged, bool):
            raise safety.SafetyError("flagged must be a boolean")
        self._backend.set_flagged(ids, flagged)
        self._journal("set_flagged", message_ids=ids, flagged=flagged)
        return {"updated": ids, "flagged": flagged}

    def _checked_tags(self, message_ids: list[str], tags: list[str]) -> tuple[list[str], list[str]]:
        """件数とタグを、backend を書き込む前にすべて検査する。1 つでも違反なら全体を拒否する。"""
        ids = safety.check_batch(message_ids)
        known = [t.key for t in self._backend.list_tags()]
        return ids, safety.check_tags(tags, known)

    def add_tags(self, message_ids: list[str], tags: list[str]) -> dict[str, Any]:
        ids, checked = self._checked_tags(message_ids, tags)
        self._backend.update_tags(ids, checked, [])
        self._journal("add_tags", message_ids=ids, tags=checked)
        return {"updated": ids, "tags": checked}

    def remove_tags(self, message_ids: list[str], tags: list[str]) -> dict[str, Any]:
        ids, checked = self._checked_tags(message_ids, tags)
        self._backend.update_tags(ids, [], checked)
        self._journal("remove_tags", message_ids=ids, tags=checked)
        return {"updated": ids, "tags": checked}

    def create_draft(
        self,
        subject: str,
        body: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        identity_id: str | None = None,
        reply_to_message_id: str | None = None,
        reply_type: str | None = None,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        if reply_to_message_id is None:
            if reply_type is not None:
                raise safety.SafetyError("reply_type needs reply_to_message_id")
            to_list = [] if to is None else to
            cc_list = [] if cc is None else cc
            bcc_list = [] if bcc is None else bcc
        else:
            to_list, cc_list, bcc_list, warnings = self._reply_recipients(
                reply_to_message_id, to, cc, bcc, reply_type
            )
        request = DraftRequest(
            to=to_list,
            subject=subject,
            body=body,
            cc=cc_list,
            bcc=bcc_list,
            identity_id=identity_id,
            reply_to_message_id=reply_to_message_id,
        )
        safety.check_draft(request)
        result = self._backend.save_draft(
            DraftRequest(
                to=tuple(request.to),
                subject=request.subject,
                body=request.body,
                cc=tuple(request.cc),
                bcc=tuple(request.bcc),
                identity_id=request.identity_id,
                reply_to_message_id=request.reply_to_message_id,
            )
        )
        journal: dict[str, Any] = {"message_id": result.message_id, "folder_id": result.folder_id}
        if reply_to_message_id is not None:
            journal["reply_to_message_id"] = reply_to_message_id
        self._journal("create_draft", **journal)
        return {
            "status": "saved_as_draft",
            "message_id": result.message_id,
            "folder_id": result.folder_id,
            "recipients": {"to": list(request.to), "cc": list(request.cc), "bcc": list(request.bcc)},
            "warnings": warnings,
            "untrusted_fields": [] if reply_to_message_id is None else ["recipients"],
        }

    def _reply_recipients(
        self,
        original_id: object,
        to: list[str] | None,
        cc: list[str] | None,
        bcc: list[str] | None,
        reply_type: str | None,
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """返信の宛先を決める（SPEC §6 の R-2 〜 R-6、R-8）。S-8 の検査は呼び出し側の check_draft が行う。"""
        if to is not None and reply_type is not None:
            raise safety.SafetyError("to and reply_type cannot be combined")
        if not isinstance(original_id, str) or not original_id or safety.sanitize_header(original_id) != original_id:
            raise safety.SafetyError("invalid reply_to_message_id")
        try:
            detail = self._backend.get_message(original_id)
        except KeyError:
            raise safety.SafetyError("original message not found") from None
        cc_given = [] if cc is None else cc
        bcc_given = [] if bcc is None else bcc
        warnings: list[str] = []
        if to is not None:
            to_list: list[str] = list(to)
            cc_list: list[str] = list(cc_given)
            safety.check_reply_recipients(detail, DraftRequest(to=to_list, subject="", body="", cc=cc_list, bcc=bcc_given))
            return to_list, cc_list, list(bcc_given), warnings
        effective = "author" if reply_type is None else reply_type
        to_list, cc_list = safety.derive_reply_recipients(detail, effective)
        safety.check_reply_recipients(detail, DraftRequest(to=(), subject="", body="", cc=cc_given, bcc=bcc_given))
        seen = {address.lower() for address in to_list + cc_list}
        for address in cc_given:
            if address.lower() not in seen:
                seen.add(address.lower())
                cc_list.append(address)
        if effective == "author" and any(a.lower() != detail.author_address.strip().lower() for a in to_list):
            warnings.append("reply_to_differs")
        return to_list, cc_list, list(bcc_given), warnings
