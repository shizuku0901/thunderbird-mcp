"""安全規則の定数と検査関数。

禁止語（FORBIDDEN_TERMS）をそのまま書いてよいのは、このファイルだけ。
ほかのモジュールは、ここで定義した定数を参照する。
"""
from __future__ import annotations

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
