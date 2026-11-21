"""安全規則の定数と検査関数。

禁止語（FORBIDDEN_TERMS）をそのまま書いてよいのは、このファイルだけ。
ほかのモジュールは、ここで定義した定数を参照する。
"""
from __future__ import annotations

import re
from pathlib import Path

# --- 禁止語（hooks が ast で読むので、小文字のリテラルのタプルにする） ---------------
FORBIDDEN_TERMS = (
    "delete",
    "send",
    "expunge",
    "trash",
    "junk",
    "spam",
    "outbox",
    "smtp",
    "purge",
)

# src/ が import してはならないライブラリ（メール送出・IMAP・POP など）
FORBIDDEN_IMPORTS = frozenset({
    "smtplib",
    "smtpd",
    "aiosmtplib",
    "aiosmtpd",
    "imaplib",
    "aioimaplib",
    "imapclient",
    "poplib",
    "nntplib",
    "yagmail",
})

# 拡張のマニフェストに書いてはならない権限とキー
FORBIDDEN_PERMISSIONS = frozenset({
    "compose.send",
    "messagesDelete",
    "messagesModifyPermanent",
    "messagesImport",
    "accountsFolders",
    "sensitiveDataUpload",
    "downloads",
})
FORBIDDEN_MANIFEST_KEYS = frozenset({"experiment_apis"})

# 拡張が宣言してよい権限（許可リスト）
ALLOWED_EXTENSION_PERMISSIONS = frozenset({
    "accountsRead",
    "messagesRead",
    "messagesUpdate",
    "messagesMove",
    "messagesTags",
    "messagesTagsList",
    "compose",
    "compose.save",
    "nativeMessaging",
})

# 公開ツール（SPEC §6 の順）
ALLOWED_TOOLS = (
    "list_accounts",
    "list_folders",
    "search_messages",
    "list_messages",
    "get_message",
    "list_attachments",
    "save_attachment",
    "mark_read",
    "set_flagged",
    "list_tags",
    "add_tags",
    "remove_tags",
    "move_messages",
    "create_draft",
    "health_check",
)

# --- 特殊フォルダ（Thunderbird の specialUse の値） ----------------------------------
SU_INBOX = "inbox"
SU_DRAFTS = "drafts"
SU_ARCHIVES = "archives"
SU_SENT = "sent"
SU_TEMPLATES = "templates"
SU_BIN = "trash"  # ごみ箱
SU_UNWANTED = "junk"  # 迷惑メール
SU_PENDING_OUT = "outbox"  # 送信トレイ

ALLOWED_MOVE_SPECIAL_USES = frozenset({SU_INBOX, SU_ARCHIVES})
DENIED_SPECIAL_USES = frozenset({
    SU_BIN,
    SU_UNWANTED,
    SU_PENDING_OUT,
    SU_DRAFTS,
    SU_TEMPLATES,
    SU_SENT,
})

# 名前またはパスの区切りごとに、小文字で比べる（多言語）
DENIED_FOLDER_NAMES = frozenset({
    "trash",
    "deleted items",
    "deleted messages",
    "deleted",
    "recycle bin",
    "bin",
    "junk",
    "junk e-mail",
    "junk email",
    "spam",
    "bulk mail",
    "outbox",
    "ごみ箱",
    "ゴミ箱",
    "削除済みアイテム",
    "削除済み",
    "迷惑メール",
    "迷惑",
    "送信トレイ",
    "papierkorb",
    "corbeille",
    "papelera",
    "cestino",
    "lixeira",
    "postausgang",
})

# タグとして付けてはならない IMAP キーワード（小文字で比べる）
DANGEROUS_TAG_KEYS = frozenset({
    "$junk",
    "$notjunk",
    "junk",
    "nonjunk",
    "$deleted",
    "deleted",
    "$phishing",
    "$mdnsent",
    "\\deleted",
})

# --- 上限 --------------------------------------------------------------------------
MAX_MESSAGES_PER_WRITE = 50
MAX_RECIPIENTS = 20
MAX_TAG_KEY_LEN = 64
MAX_FILENAME_LEN = 200
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_BODY_CHARS = 20000
MAX_SEARCH_LIMIT = 50

# --- 信頼できない入力を囲むマーカー ---------------------------------------------------
UNTRUSTED_BEGIN = "<<<UNTRUSTED_EMAIL_CONTENT_BEGIN>>>"
UNTRUSTED_END = "<<<UNTRUSTED_EMAIL_CONTENT_END>>>"


class SafetyError(ValueError):
    """安全検査に違反した。メール由来の文字列を、メッセージに含めない。"""


# --- 添付ファイルの保存先 ------------------------------------------------------------
_PATH_SEPARATORS = re.compile(r"[\\/]")
_FILENAME_BAD_CHARS = frozenset('<>"|?*:')
_RESERVED_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(10)}
    | {f"lpt{i}" for i in range(10)}
)


def _has_control_char(text: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


def _check_filename(name: str) -> None:
    if name in ("", ".", ".."):
        raise SafetyError("invalid attachment filename")
    if len(name) > MAX_FILENAME_LEN:
        raise SafetyError("attachment filename is too long")
    if _has_control_char(name) or any(ch in _FILENAME_BAD_CHARS for ch in name):
        raise SafetyError("attachment filename has forbidden characters")
    if name.endswith((".", " ")):
        raise SafetyError("attachment filename ends with a dot or space")
    stem = name.split(".", 1)[0].rstrip(" ").casefold()
    if stem in _RESERVED_DEVICE_NAMES:
        raise SafetyError("attachment filename is a reserved device name")


def safe_attachment_path(root: Path | str, filename: str) -> Path:
    """`root` の直下だけを指す、保存用のパスを返す。root がなければ作る。

    パスの区切り（スラッシュとバックスラッシュ）より前は捨て、最後の部分だけを使う。
    既存のファイルは上書きしない（` (n)` を付ける）。
    """
    if not isinstance(filename, str):
        raise SafetyError("attachment filename must be a string")
    name = _PATH_SEPARATORS.split(filename)[-1]
    _check_filename(name)

    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    resolved_root = base.resolve()

    candidate = resolved_root / name
    suffix = Path(name).suffix
    stem = name[: len(name) - len(suffix)] if suffix else name
    counter = 1
    while candidate.exists():
        counter += 1
        candidate = resolved_root / f"{stem} ({counter}){suffix}"

    if candidate.parent != resolved_root:
        raise SafetyError("attachment path escapes the save directory")
    return candidate
