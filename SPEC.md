# SPEC: thunderbird-mcp（最小スタート構成）

- 版: 0.1.0
- 作成日: 2026-11-21
- 状態: 承認済み（JS ブリッジの採用は 2026-11-21 にユーザーが承認）

## 1. 目的と範囲

**目的:** Windows で起動中の Thunderbird のメールを、Claude（MCP クライアント）が検索・閲覧・整理し、下書きを作れるようにする。

**持たせる機能:** 検索、一覧、本文の取得、添付の一覧と保存、既読/未読、フラグ、タグ、下書きの作成、フォルダ間の移動。

**持たせない機能（構造的に不可能にする）:**
- 削除系: ごみ箱・迷惑メール・送信トレイへの移動、削除フラグの付与、完全消去を含む。
- 送信系: メール送出プロトコル、送信キュー、下書きの自動送出を含む。

## 2. 用語

| 用語 | 定義 |
|---|---|
| フラグ | Thunderbird のスター（IMAP `\Flagged`） |
| タグ | Thunderbird のタグ（IMAP キーワード。例: `$label1`） |
| 特殊フォルダ | `MailFolder.specialUse` を持つフォルダ |
| メッセージ ID | WebExtension の内部 ID を文字列にしたもの。Thunderbird を再起動すると変わる |
| 禁止語 | `safety.FORBIDDEN_TERMS` に列挙した語。削除・送信・完全消去・ごみ箱・迷惑メール・スパム・送信トレイ・メール送出プロトコル・パージに当たる英語。最低限の集合は `tests/test_forbidden_terms_scan.py` の `MIN_TERMS_REVERSED`（逆順で表記）で固定する |

## 3. 接続方式の決定（ADR-001）

- **決定:** MailExtension（JS の薄いブリッジ）＋ Native Messaging（Python のホスト）＋ 名前付きパイプ（HMAC 認証）＋ Python の MCP サーバー。
- **理由:** Thunderbird の権限モデルでは、削除用と送信用の権限が独立している。それらを宣言しなければ、Thunderbird 自身がその API を拒否する。ポートを開かず、認証情報も持たずに済む。
- **却下した案:** IMAP 直結（認証情報を持つ必要があり、削除コマンドがプロトコルに含まれる）、localhost WebSocket（ポートを開く）、プロファイルの直接読み取り（書き込みができず、起動中は破損のリスクがある）、Experiment API（全権限を持つ）、UI 自動化（壊れやすい）。
- **「Python のみ」との衝突:** JS は `extension/` の中継と権限宣言に限る。判断はすべて Python 側の `safety.py` と `service.py` で行い、JS 側は同じ規則を許可リスト方式で二重に検査する。
- **承認:** 2026-11-21、ユーザーが承認。

## 4. 構成

```text
Claude Code ──stdio(JSON-RPC)──▶ thunderbird_mcp.server（FastMCP）
                                   └ MailService（safety で検査）
                                       └ BridgeBackend ──名前付きパイプ \\.\pipe\thunderbird-mcp-<USERNAME>（HMAC）──▶ native_host.py
                                                                                                                      └ Native Messaging（stdio、4バイト長＋JSON）──▶ extension/background.js
                                                                                                                                                                  └ messenger.* API
```

| 部品 | 起動するもの | 役割 |
|---|---|---|
| `server.py` | Claude Code | MCP の stdio サーバー。stdout は JSON-RPC 専用 |
| `native_host.py` | Thunderbird（`runtime.connectNative`） | パイプのサーバーになり、要求を拡張へ中継する |
| `ipc.py` | 双方 | フレーム（4バイト長＋UTF-8 JSON、上限 8 MiB）と、HMAC-SHA256 のチャレンジ応答 |
| `background.js` | Thunderbird | op の固定表を引いて、messenger API を呼ぶ |

- IPC の鍵: `native_host.py` が初回起動時に `%LOCALAPPDATA%\thunderbird-mcp\ipc.key`（32バイトの乱数）を作る。BridgeBackend はそれを読んで応答する。比較には `hmac.compare_digest` を使う。
- Native Messaging の1メッセージは 768 KiB 以下に分割する（上限は未確認なので、1 MiB を仮定して余裕を取る）。
- Thunderbird が起動していないときは、ツールが「Thunderbird に接続できない」というエラーを返す。
- IPC の実装では、禁止語を含む API 名（標準ライブラリのメソッド名も含む）を使わない。候補は asyncio の ProactorEventLoop の名前付きパイプ（`transport.write`）。

## 5. MailBackend 抽象

```python
class MailBackend(ABC):
    def list_accounts(self) -> list[Account]: ...
    def list_folders(self, account_id: str | None = None) -> list[Folder]: ...
    def get_folder(self, folder_id: str) -> Folder: ...            # 存在しなければ KeyError
    def search_messages(self, query: SearchQuery) -> list[MessageSummary]: ...
    def list_messages(self, folder_id: str, limit: int, offset: int) -> list[MessageSummary]: ...
    def get_message(self, message_id: str) -> MessageDetail: ...   # 存在しなければ KeyError
    def list_attachments(self, message_id: str) -> list[AttachmentInfo]: ...
    def read_attachment(self, message_id: str, part_name: str) -> bytes: ...
    def set_read(self, message_ids: list[str], read: bool) -> None: ...
    def set_flagged(self, message_ids: list[str], flagged: bool) -> None: ...
    def list_tags(self) -> list[Tag]: ...
    def update_tags(self, message_ids: list[str], add: list[str], remove: list[str]) -> None: ...
    def move_messages(self, message_ids: list[str], destination_folder_id: str) -> None: ...
    def save_draft(self, draft: DraftRequest) -> DraftResult: ...
    def health(self) -> dict: ...
```

- 公開メソッドはこの 15 個に固定する（`tests/test_tool_allowlist.py` で検証）。
- `models.py` に置く dataclass: `Account(id, name, type)`、`Folder(id, account_id, name, path, special_use: tuple[str, ...] = ())`、`Tag(key, name, color="")`、`AttachmentInfo(part_name, filename, content_type, size)`、`MessageSummary(id, folder_id, subject, author, date, read=False, flagged=False, tags=(), has_attachments=False)`、`MessageDetail(summary, body_text, attachments)`、`SearchQuery(text="", folder_id=None, unread_only=False, flagged_only=False, tag=None, limit=20)`、`DraftRequest(to, subject, body, cc=(), bcc=(), identity_id=None)`、`DraftResult(message_id, folder_id)`。
- `FakeBackend`（`backends/fake.py`）について:
  - 呼ばれたメソッド名を `calls: list[str]` に記録する。
  - 下書きは `drafts: list[DraftRequest]` に保存し、`special_use` に `SU_DRAFTS` を含む最初のフォルダの ID を返す。
  - テストの準備用に `seed_account`、`seed_folder`、`seed_tag`、`seed_message(summary, body_text="", attachments=())` を持つ。`attachments` は `(AttachmentInfo, bytes)` の並びで、これらの呼び出しは `calls` に記録しない。
  - `search_messages` は件名の部分一致（大文字小文字を区別しない）で探す。
  - `with_sample_data()` は合成データを入れたインスタンスを返す。禁止語は safety の定数から組み立てる。
- `MailService(backend, attachment_root, *, max_body_chars=20_000, move_allowlist=None, journal_path=None)` が、公開ツールと同名のメソッドを持つ。

## 6. 公開ツール許可リスト（完全一致）

| # | ツール | 引数 | 戻り値（要旨） | 種別 |
|---|---|---|---|---|
| 1 | `list_accounts` | なし | `{"accounts": [...]}` | 読み取り |
| 2 | `list_folders` | `account_id?` | `{"folders": [{id, account_id, name, path, special_use}]}` | 読み取り |
| 3 | `search_messages` | `text=""`, `folder_id?`, `unread_only=false`, `flagged_only=false`, `tag?`, `limit=20`（50 を超えたら 50 にする） | `{"messages": [summary], "untrusted_fields": [...]}` | 読み取り |
| 4 | `list_messages` | `folder_id`, `limit=20`（最大 50）, `offset=0` | 同上 | 読み取り |
| 5 | `get_message` | `message_id`, `max_body_chars?` | `{"message": {...}, "body": {untrusted, notice, content, truncated}, "untrusted_fields": [...]}` | 読み取り |
| 6 | `list_attachments` | `message_id` | `{"attachments": [{part_name, filename, content_type, size}]}` | 読み取り |
| 7 | `save_attachment` | `message_id`, `part_name`, `filename?` | `{"path", "size"}` | ローカルへの書き込み |
| 8 | `mark_read` | `message_ids`, `read=true` | `{"updated", "read"}` | 書き込み |
| 9 | `set_flagged` | `message_ids`, `flagged=true` | `{"updated", "flagged"}` | 書き込み |
| 10 | `list_tags` | なし | `{"tags": [{key, name, color}]}` | 読み取り |
| 11 | `add_tags` | `message_ids`, `tags` | `{"updated", "tags"}` | 書き込み |
| 12 | `remove_tags` | `message_ids`, `tags` | `{"updated", "tags"}` | 書き込み |
| 13 | `move_messages` | `message_ids`, `destination_folder_id` | `{"moved", "destination_folder_id"}` | 書き込み |
| 14 | `create_draft` | `to`, `subject`, `body`, `cc?`, `bcc?`, `identity_id?` | `{"status": "saved_as_draft", "message_id", "folder_id"}` | 書き込み |
| 15 | `health_check` | なし | `{"backend", "ok", "thunderbird_version?", "draft_mode?"}` | 読み取り |

- summary の項目: `id`, `folder_id`, `subject`, `author`, `date`, `read`, `flagged`, `tags`, `has_attachments`。
- `create_draft` の引数は上の 6 個に固定する。「すぐ出す」のようなフラグは持たない。

## 7. ブリッジ op 許可リスト（Python ↔ JS）

| op | 使う messenger API（要実機確認） | 必要な権限 |
|---|---|---|
| `accounts.list` | `accounts.list()` | accountsRead |
| `folders.list` | `folders.query()` または `accounts.list(true)` | accountsRead |
| `messages.query` | `messages.query()` | messagesRead |
| `messages.list` | `messages.list()` / `continueList()` | messagesRead |
| `messages.get` | `messages.getFull()` | messagesRead |
| `attachments.list` | `messages.listAttachments()` | messagesRead |
| `attachments.read` | `messages.getAttachmentFile()` | messagesRead |
| `messages.mark` / `messages.flag` / `tags.update` | `messages.update()` | messagesRead / messagesUpdate |
| `tags.list` | `messages.tags.list()`（古い版は `messages.listTags()`） | messagesTags 系 |
| `messages.move` | `messages.move()` | messagesMove |
| `drafts.create` | `messages.saveMessage()` があればそれを使う。なければ `compose.beginNew()` → `compose.saveMessage(tabId, {mode: "draft"})` → `tabs.remove(tabId)` | compose, compose.save |
| `health` | `runtime.getBrowserInfo()` | なし |

**JS 側で守ること（Python 側と二重に検査する）:**
- J-1: op は固定表だけを受け付け、知らない op は拒否する。
- J-2: `messages.update` に渡すキーは `read`、`flagged`、`tags` の3つだけ。
- J-3: 移動先は `specialUse` が空か、`inbox` / `archives` だけを許す（許可リスト方式）。
- J-4: タグのキーは、タグ一覧に存在するものだけを受け付ける。
- J-5: 1回の書き込みは 50 件まで。
- J-6: 作成ウィンドウは保存後に閉じる。閉じられなければエラーを返す。
- J-7: `health` で `draft_mode`（`background` または `compose_window`）を返す。版は起動時に判定する（ユーザーの版が不明なため）。
- マニフェストは MV2（`background.scripts`）、`strict_min_version` は `140.0`。

## 8. 安全要件

**禁止語ルール（CLAUDE.md・SPEC・テストで共通）:** 禁止語をそのまま書いてよいのは `src/thunderbird_mcp/safety.py` だけ。対象はリポジトリ内の `.py .pyi .js .mjs .cjs .json .toml .ps1 .psm1 .cmd .bat` のすべてで、`.venv`、`.git`、キャッシュ、`.claude/settings.local.json` は除く。Markdown は対象外だが、本文では英語の禁止語を使わない。

| ID | 要件 |
|---|---|
| S-1 | 拡張は `safety.FORBIDDEN_PERMISSIONS` の権限も `safety.FORBIDDEN_MANIFEST_KEYS` のキー（Experiment API）も宣言しない。`host_permissions` も持たない |
| S-2 | 公開ツールは §6 と完全一致させる |
| S-3 | `MailBackend` の公開メソッドは §5 の 15 個に固定する |
| S-4 | `src/` は `safety.FORBIDDEN_IMPORTS`（メール送出・IMAP・POP のライブラリ）を import しない。`__import__(` と `import_module(` も使わない |
| S-5 | 移動先の検査: `special_use` が `ALLOWED_MOVE_SPECIAL_USES`（inbox、archives）以外なら拒否する。名前またはパスの末尾が `DENIED_FOLDER_NAMES`（多言語、大文字小文字を区別しない）に当たれば拒否する。存在しないフォルダも拒否する。`move_allowlist` が設定されていれば、その `path` に含まれるものだけを許す |
| S-6 | タグの検査: `DANGEROUS_TAG_KEYS` に当たるもの、`\` で始まるもの、空白・制御文字・`(){%*"]\` を含むもの、64 文字を超えるもの、一覧にないものは拒否する。外す場合も同じ検査をする |
| S-7 | 1回の書き込みは 1〜50 件（`MAX_MESSAGES_PER_WRITE`）。重複は順序を保って除く |
| S-8 | 下書き: 件名とアドレスに CR、LF、制御文字があれば拒否する。アドレスは素のアドレスだけ（`Name <addr>` や `,` での区切りは不可）。宛先は合計 20 件まで。`to` は 1 件以上 |
| S-9 | 添付の保存: パスの最後の部分だけを使う。空、`.`、`..`、`:`、制御文字、`<>"\|?*`、末尾の `.` や空白、予約デバイス名（拡張子付きを含む）、200 文字超は拒否する。保存先は `attachment_root` の直下に限り、既存のファイルは上書きしない（`open(..., "xb")` と ` (n)` の付与）。上限は 25 MiB |
| S-10 | stdout: `native_host.py` 以外は stdout に書かない。ログは stderr に出す |
| S-11 | 安全検査に違反したら `SafetyError` を投げ、backend は呼ばない。ツールのエラー文は `refused: <理由>` とし、メール由来の文字列を含めない |
| S-12 | 書き込みは JSONL の操作ジャーナルに残す（時刻、ツール、ID、移動元と移動先またはタグ）。本文と件名は書かない |
| S-13 | 開発用の Claude Code では、実際の `mcp__thunderbird` ツールを deny にする |

**`safety.py` が公開する名前:**
- 定数: `FORBIDDEN_TERMS`（小文字のリテラルのタプル）、`FORBIDDEN_IMPORTS`、`FORBIDDEN_PERMISSIONS`、`FORBIDDEN_MANIFEST_KEYS`、`ALLOWED_EXTENSION_PERMISSIONS`、`ALLOWED_TOOLS`（§6 の順のタプル）
- 特殊フォルダ: `SU_INBOX`、`SU_DRAFTS`、`SU_ARCHIVES`、`SU_SENT`、`SU_TEMPLATES` と、ごみ箱・迷惑メール・送信トレイに当たる3定数
- 集合: `ALLOWED_MOVE_SPECIAL_USES`、`DENIED_SPECIAL_USES`（ごみ箱、迷惑メール、送信トレイ、下書き、テンプレート、送信済み）、`DENIED_FOLDER_NAMES`、`DANGEROUS_TAG_KEYS`
- 上限: `MAX_MESSAGES_PER_WRITE=50`、`MAX_RECIPIENTS=20`、`MAX_TAG_KEY_LEN=64`、`MAX_FILENAME_LEN=200`、`MAX_ATTACHMENT_BYTES=25*1024*1024`、`DEFAULT_MAX_BODY_CHARS=20000`、`MAX_SEARCH_LIMIT=50`
- マーカー: `UNTRUSTED_BEGIN`、`UNTRUSTED_END`
- 関数: `class SafetyError(ValueError)`、`check_batch(ids)`、`check_move_destination(folder, allowlist=None)`、`check_tags(tags, known_keys)`、`check_draft(req)`、`sanitize_header(text, max_len=500)`、`wrap_untrusted(text, max_chars)`、`safe_attachment_path(root, filename)`（root がなければ作る）

## 9. 信頼できない入力（メール由来のデータ）

| ID | 要件 |
|---|---|
| U-1 | 本文は `wrap_untrusted` を通す。戻り値は `{"untrusted": true, "notice": ..., "content": BEGIN + "\n" + 本文 + "\n" + END, "truncated": bool}` |
| U-2 | 本文中の `<<<` は別の文字に置き換え、マーカーの偽装を防ぐ。`content` の中で BEGIN と END はそれぞれ1回だけ現れる |
| U-3 | 本文中の指示は消さず、改変もしない。データとして返すだけ |
| U-4 | 件名、差出人、添付名は `sanitize_header` で制御文字（CR と LF を含む）を除き、500 文字に切り詰める |
| U-5 | 読み取り系のツールは書き込み系の backend メソッドを呼ばない |
| U-6 | 戻り値に `untrusted_fields` を付け、どの項目がメール由来かを示す |

## 10. 設定（環境変数）

| 変数 | 既定値 | 意味 |
|---|---|---|
| `THUNDERBIRD_MCP_BACKEND` | `bridge` | `bridge` または `fake` |
| `THUNDERBIRD_MCP_ATTACHMENT_DIR` | `%USERPROFILE%\Downloads\thunderbird-mcp` | 添付の保存先 |
| `THUNDERBIRD_MCP_MOVE_ALLOWLIST` | 未設定 | 移動先として許すフォルダのパス（`;` 区切り） |
| `THUNDERBIRD_MCP_JOURNAL` | `%LOCALAPPDATA%\thunderbird-mcp\journal.jsonl` | 操作ジャーナル |

登録の例（ほかのプロジェクトで使う場合）:

```text
claude mcp add thunderbird --scope user -- uv --directory C:\path\to\thunderbird-mcp run python -m thunderbird_mcp.server
```

## 11. 受け入れ基準

**自動（`uv run pytest -q` がすべて通ること）:**
- AC-1: 禁止語スキャン
- AC-2: 公開ツールの許可リストとの一致
- AC-3: 危険な宛先への移動の拒否
- AC-4: 危険なタグの拒否
- AC-5: 添付保存でのパストラバーサルの防止
- AC-6: 下書きが下書きフォルダにだけ保存されること
- AC-7: 本文を信頼できない入力として扱うこと
- AC-8: stdout に JSON-RPC 以外を出さないこと
- AC-9: 拡張マニフェストの権限

**手動（Windows の実機で、人間が行う）:**
- AC-W1: `register_native_host.ps1` を実行し、Thunderbird を再起動する。`health_check` が `ok: true` を返し、`thunderbird_version` と `draft_mode` が出る。
- AC-W2: `create_draft` の結果が下書きフォルダに現れる。送信トレイは空のまま。
- AC-W3: `bridge_probe.py` で、Python の検査を迂回してごみ箱フォルダへの `messages.move` を直接投げると、JS が拒否する。
- AC-W4: 拡張の権限ダイアログに、削除と送信に当たる権限が表示されない。
- AC-W5: Thunderbird を終了した状態で、ツールが接続エラーを返し、ハングしない。

## 12. 未確認事項（実機で確認する）

Windows の Thunderbird が読むレジストリキー、Native Messaging のメッセージの上限、`messages.saveMessage` の引数と必要な権限、`messagesUpdate` と `messagesTags` の権限名（ESR 140 と 153 以降）、`folders.query` と `tags.list` の有無、`.cmd` をホストとして起動できるか、asyncio の名前付きパイプ API、名前付きパイプの既定の ACL。