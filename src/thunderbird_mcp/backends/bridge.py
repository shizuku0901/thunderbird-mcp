"""BridgeBackend: 名前付きパイプ経由で、Thunderbird の拡張（native_host を通して）に要求する。

MailBackend の 15 メソッドを、ipc.ALLOWED_OPS の op に対応させるだけで、判断はしない。
1 回の呼び出しごとに接続し、認証してから 1 つの要求を出す。
拡張からのエラー文は、メール由来の文字列を含みうるので、そのまま上へ渡さない。
"""
from __future__ import annotations

import base64
import binascii
import itertools
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import ipc, safety
from ..backend import MailBackend
from ..ipc import (
    ALLOWED_OPS,
    AuthenticationError,
    BridgeUnavailable,
    Channel,
    ConnectionClosed,
    IpcError,
    Reassembler,
    client_handshake,
    read_message,
    write_message,
)
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

logger = logging.getLogger(__name__)

# 拡張が返す「拒否」の理由は固定文だけ。想定外の文字を含むものは、そのまま渡さない。
_REFUSAL_REASON = re.compile(r"refused: ([A-Za-z0-9 ,.:_()\-]{1,120})")


class BridgeProtocolError(RuntimeError):
    """拡張からの応答が、約束した形ではない。"""


class BridgeBackend(MailBackend):
    def __init__(
        self,
        *,
        pipe: str | None = None,
        key_path: Path | str | None = None,
        connector: Callable[[], Channel] | None = None,
        connect_timeout: float = 3.0,
        handshake_timeout: float = 5.0,
        call_timeout: float = 130.0,
    ) -> None:
        self._pipe = pipe or ipc.pipe_name()
        self._key_path = Path(key_path) if key_path else ipc.default_key_path()
        self._connector = connector or self._connect_pipe
        self._connect_timeout = connect_timeout
        self._handshake_timeout = handshake_timeout
        self._call_timeout = call_timeout
        self._ids = itertools.count(1)

    # --- 通信 --------------------------------------------------------------------------
    def _connect_pipe(self) -> Channel:
        return ipc.connect_pipe(self._pipe, self._connect_timeout)

    def _load_key(self) -> bytes:
        try:
            return ipc.load_key(self._key_path)
        except (OSError, IpcError):
            raise BridgeUnavailable(
                "cannot connect to Thunderbird (the IPC key is missing; start Thunderbird with the extension)"
            ) from None

    def _call(self, op: str, args: dict[str, Any] | None = None) -> Any:
        if op not in ALLOWED_OPS:
            raise ValueError("op is not in the fixed table")
        key = self._load_key()
        request_id = next(self._ids)
        channel = self._connector()
        try:
            client_handshake(channel, key, timeout=self._handshake_timeout)
            write_message(channel, {"id": request_id, "op": op, "args": args or {}})
            response = read_message(channel, Reassembler(), timeout=self._call_timeout)
        except AuthenticationError:
            raise BridgeUnavailable("cannot connect to Thunderbird (authentication failed)") from None
        except ConnectionClosed:
            raise BridgeUnavailable("cannot connect to Thunderbird (the connection was closed)") from None
        except IpcError:
            raise BridgeUnavailable("cannot connect to Thunderbird (protocol error)") from None
        finally:
            channel.close()
        return self._result(response, request_id)

    @staticmethod
    def _result(response: Any, request_id: int) -> Any:
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise BridgeProtocolError("unexpected response")
        if response.get("ok") is True:
            return response.get("result")
        code = response.get("code")
        error = response.get("error")
        if code == "refused":
            match = _REFUSAL_REASON.fullmatch(error) if isinstance(error, str) else None
            raise safety.SafetyError(match.group(1) if match else "the extension refused the request")
        if code == "not_found":
            raise KeyError("not found")
        logger.error("the extension reported an error: %s", safety.sanitize_header(error, 200))
        raise BridgeProtocolError("the extension reported an error")

    # --- 変換（形が違えば BridgeProtocolError） -------------------------------------------
    @staticmethod
    def _convert(fn: Callable[[Any], Any], value: Any) -> Any:
        try:
            return fn(value)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise BridgeProtocolError("unexpected result shape") from None

    @staticmethod
    def _account(d: dict[str, Any]) -> Account:
        return Account(id=str(d["id"]), name=str(d["name"]), type=str(d["type"]))

    @staticmethod
    def _folder(d: dict[str, Any]) -> Folder:
        return Folder(
            id=str(d["id"]),
            account_id=str(d["account_id"]),
            name=str(d["name"]),
            path=str(d["path"]),
            special_use=tuple(str(s) for s in d.get("special_use", [])),
        )

    @staticmethod
    def _summary(d: dict[str, Any]) -> MessageSummary:
        return MessageSummary(
            id=str(d["id"]),
            folder_id=str(d["folder_id"]),
            subject=str(d["subject"]),
            author=str(d["author"]),
            date=str(d["date"]),
            read=bool(d.get("read", False)),
            flagged=bool(d.get("flagged", False)),
            tags=tuple(str(t) for t in d.get("tags", [])),
            has_attachments=bool(d.get("has_attachments", False)),
        )

    @staticmethod
    def _attachment(d: dict[str, Any]) -> AttachmentInfo:
        return AttachmentInfo(
            part_name=str(d["part_name"]),
            filename=str(d["filename"]),
            content_type=str(d["content_type"]),
            size=int(d["size"]),
        )

    @staticmethod
    def _tag(d: dict[str, Any]) -> Tag:
        return Tag(key=str(d["key"]), name=str(d["name"]), color=str(d.get("color", "")))

    # --- MailBackend -------------------------------------------------------------------
    def list_accounts(self) -> list[Account]:
        return self._convert(lambda r: [self._account(d) for d in r], self._call("accounts.list"))

    def list_folders(self, account_id: str | None = None) -> list[Folder]:
        result = self._call("folders.list", {"account_id": account_id})
        return self._convert(lambda r: [self._folder(d) for d in r], result)

    def get_folder(self, folder_id: str) -> Folder:
        for folder in self.list_folders():
            if folder.id == folder_id:
                return folder
        raise KeyError(folder_id)

    def search_messages(self, query: SearchQuery) -> list[MessageSummary]:
        args = {
            "text": query.text,
            "folder_id": query.folder_id,
            "unread_only": query.unread_only,
            "flagged_only": query.flagged_only,
            "tag": query.tag,
            "limit": query.limit,
        }
        return self._convert(lambda r: [self._summary(d) for d in r], self._call("messages.query", args))

    def list_messages(self, folder_id: str, limit: int, offset: int) -> list[MessageSummary]:
        args = {"folder_id": folder_id, "limit": limit, "offset": offset}
        return self._convert(lambda r: [self._summary(d) for d in r], self._call("messages.list", args))

    def get_message(self, message_id: str) -> MessageDetail:
        def build(r: dict[str, Any]) -> MessageDetail:
            return MessageDetail(
                summary=self._summary(r["summary"]),
                body_text=str(r["body_text"]),
                attachments=tuple(self._attachment(a) for a in r.get("attachments", [])),
            )

        return self._convert(build, self._call("messages.get", {"message_id": message_id}))

    def list_attachments(self, message_id: str) -> list[AttachmentInfo]:
        result = self._call("attachments.list", {"message_id": message_id})
        return self._convert(lambda r: [self._attachment(d) for d in r], result)

    def read_attachment(self, message_id: str, part_name: str) -> bytes:
        result = self._call("attachments.read", {"message_id": message_id, "part_name": part_name})

        def decode(r: dict[str, Any]) -> bytes:
            try:
                return base64.b64decode(r["data_b64"], validate=True)
            except binascii.Error as exc:
                raise ValueError("invalid base64") from exc

        return self._convert(decode, result)

    def set_read(self, message_ids: list[str], read: bool) -> None:
        self._call("messages.mark", {"message_ids": list(message_ids), "read": read})

    def set_flagged(self, message_ids: list[str], flagged: bool) -> None:
        self._call("messages.flag", {"message_ids": list(message_ids), "flagged": flagged})

    def list_tags(self) -> list[Tag]:
        return self._convert(lambda r: [self._tag(d) for d in r], self._call("tags.list"))

    def update_tags(self, message_ids: list[str], add: list[str], remove: list[str]) -> None:
        self._call("tags.update", {"message_ids": list(message_ids), "add": list(add), "remove": list(remove)})

    def move_messages(self, message_ids: list[str], destination_folder_id: str) -> None:
        self._call(
            "messages.move",
            {"message_ids": list(message_ids), "destination_folder_id": destination_folder_id},
        )

    def save_draft(self, draft: DraftRequest) -> DraftResult:
        args = {
            "to": list(draft.to),
            "cc": list(draft.cc),
            "bcc": list(draft.bcc),
            "subject": draft.subject,
            "body": draft.body,
            "identity_id": draft.identity_id,
        }
        result = self._call("drafts.create", args)
        return self._convert(lambda r: DraftResult(message_id=str(r["message_id"]), folder_id=str(r["folder_id"])), result)

    def health(self) -> dict:
        result = self._call("health")
        if not isinstance(result, dict):
            raise BridgeProtocolError("unexpected result shape")
        return {"backend": "bridge", "ok": True, **result}
