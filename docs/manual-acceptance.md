# 手動の受け入れ確認（AC-W1〜W5。W3 は欠番）

SPEC §11 の手動の受け入れ基準を、Windows の実機で行うための手順書です。**人間が実行します。** 開発用の Claude Code は、レジストリの登録、拡張のインストール、Thunderbird の起動を行いません。

## 先に決めておくこと

- **テスト用のアカウントとメールで行う**（実際の受信箱では行わない）。
- 開発用の Claude Code（このリポジトリ）では、実際の `mcp__thunderbird` ツールを deny にしています（S-13）。ツールを使う確認は、**別のプロジェクト**の Claude Code で行ってください。
- 前提: Thunderbird 140 以上（`strict_min_version`）、リポジトリで `uv sync` 済み。
- 手順の途中で、想定と違う表示や挙動があれば、**そこで止めて**、その画面の内容と一緒に報告してください（SPEC の未確認事項を直す材料になります）。

## 準備: ホストの登録と拡張の読み込み

1. PowerShell で、リポジトリのルートに移動する。
2. 何が変わるかを確かめる: `.\scripts\register_native_host.ps1 -WhatIf`
3. 登録する: `.\scripts\register_native_host.ps1`
   - ホスト用のマニフェストを `%LOCALAPPDATA%\thunderbird-mcp\native-host\` に書き、`HKCU:\Software\Mozilla\NativeMessagingHosts\thunderbird_mcp_host` を、そのマニフェストに向けます。
   - 元に戻すには `.\scripts\register_native_host.ps1 -Unregister`（IPC の鍵は残ります）。
4. Thunderbird を起動する。
5. 「ツール」→「開発ツール」→「アドオンのデバッグ」→「一時的なアドオンを読み込む」で、`extension\manifest.json` を選ぶ。
   - 一時的なアドオンは、Thunderbird を再起動すると外れます。常用するには、署名したパッケージが要ります（今回は範囲外）。

## AC-W1: `health_check` が `ok: true` を返す

1. 別の PowerShell で: `uv run python scripts/bridge_probe.py health`
2. 期待する結果: `"ok": true`、`"thunderbird_version"`（例: `140.x`）、`"draft_mode"`（`compose_window`）が出る。
3. 続けて、別のプロジェクトの Claude Code にサーバーを登録する:
   `claude mcp add thunderbird --scope user -- uv --directory "C:\Rio's Repository\thunderbird-mcp" run python -m thunderbird_mcp.server`
4. その Claude Code で `health_check` を呼ぶ。同じく `ok: true` と、版と `draft_mode` が返ること。
5. 失敗したときに控えるもの: probe のエラー文、Thunderbird の「エラーコンソール」（Ctrl+Shift+J）の `thunderbird-mcp` を含む行。

## AC-W2: 下書きが下書きフォルダにだけ現れる

1. テスト用アカウントの「送信トレイ」と「送信済み」が空であることを、先に確かめる。
2. Claude Code で `create_draft` を呼ぶ（宛先は自分のテスト用アドレスにする）。
3. 期待する結果:
   - 戻り値が `{"status": "saved_as_draft", ...}`。
   - Thunderbird の「下書き」フォルダに、そのメールが現れる。
   - 作成ウィンドウが**閉じている**（J-6）。
   - 「送信トレイ」は空のまま、「送信済み」にも現れない。
4. 控えるもの: 作成ウィンドウが残ったか、下書きがどのフォルダに入ったか。

## AC-W3: 欠番

2026-11-21 に `move_messages` を外したため、この確認はありません（SPEC §13）。

## AC-W4: 権限ダイアログに、削除と送出の権限が出ない

1. 拡張を読み込むときの権限の表示、または「アドオンとテーマ」→ 拡張 →「権限」タブを見る。
2. 期待する結果: メールを削除する権限、メールを送出する権限は表示されない。宣言している権限は次の 7 個だけ（`extension/manifest.json`）:
   `accountsRead`、`messagesRead`、`messagesUpdate`、`messagesTagsList`、`compose`、`compose.save`、`nativeMessaging`
3. 注意（未確認）: `compose` の権限の説明文に、「作成して送出するときのメールを読み書きする」といった文言が含まれる可能性があります。これは `compose` の説明文で、送出専用の権限（`compose` 系の、送出を許す権限）は宣言していません。文言が出たら、**画面の文言をそのまま報告**してください。マニフェストに送出の権限がないことは、自動テスト（`tests/test_extension_manifest.py`）で確認済みです。

## AC-W5: Thunderbird を終了したとき、ハングしない

1. Thunderbird を終了する。
2. Claude Code で `health_check`（または `list_accounts`）を呼ぶ。
3. 期待する結果: 数秒以内に、`cannot connect to Thunderbird ...` というエラーが返る。サーバーは落ちず、Thunderbird を起動し直して拡張を読み込めば、再び使える。
4. 控えるもの: エラーが返るまでの時間、サーバーが落ちたか。

## AC-W6: 返信の下書きが元メールのスレッドに紐付く

1. テスト用アカウントの受信箱に、自分のテスト用アドレスから届いた合成メールを1通用意する。「送信トレイ」と「送信済み」が空であることを確かめる。
2. Claude Code で `get_message` を呼び、そのメールの `id` を控える。
3. `create_draft` を、`reply_to_message_id` にその `id` を入れ、`to` を省略して呼ぶ（`reply_type` は省略するか `author`）。次に `reply_type="all"` でも呼ぶ。
4. 期待する結果:
   - 戻り値の `recipients` が、元メールの差出人（`Reply-To` があればそれ）と一致する。`all` では元の To と Cc も入る。
   - 「下書き」フォルダに現れ、Thunderbird 上で元メールのスレッドに繋がる（`In-Reply-To` と `References` が付く）。
   - 作成ウィンドウが**閉じている**（J-9）。
   - 「送信トレイ」は空のまま、「送信済み」にも現れない。
5. 引用の扱い（元メールの引用が本文に残るか、`body` だけになるか）を見て、結果を控える。SPEC §6 の方針を決める材料にする。
6. 参加者でないアドレスを `to` に入れて呼ぶと、`refused` になること。
7. 控えるもの: スレッドに繋がったか、引用の扱い、`compose.beginReply` と `compose.setComposeDetails` でエラーが出たか。

## 追加で見ておくとよいこと

- 操作ジャーナル: `%LOCALAPPDATA%\thunderbird-mcp\journal.jsonl` に、書き込みの操作（時刻、ツール、ID）が残り、**件名と本文が入っていない**こと（S-12）。鍵のファイル（`ipc.key`）は開かないでください。
- タグ: `list_tags` → `add_tags`（既存のタグのキー）で、メールにタグが付くこと。付かない場合は、権限の名前の仮定（下の表）が違う可能性があります。

## SPEC §12 の未確認事項: 置いた仮定と、確かめ方

| 事項 | 実装で置いた仮定 | どこで分かるか |
|---|---|---|
| レジストリキー | `HKCU\Software\Mozilla\NativeMessagingHosts\thunderbird_mcp_host` | AC-W1（`health` が通れば正しい） |
| `.cmd` をホストとして起動できる | 起動できる | AC-W1 |
| Native Messaging の上限 | 1 MiB と仮定し、768 KiB で分割 | 大きな添付の保存（`save_attachment`） |
| `messages.saveMessage` | 引数が不明なので使わず、常に作成ウィンドウ経由（`draft_mode` は `compose_window`） | AC-W1 の `draft_mode`、AC-W2 |
| 権限の名前 | `messagesUpdate` でタグも更新できる（`messagesTags` は宣言しない） | 追加確認のタグ |
| `folders.query` / `folders.get` / `messages.tags.list` | あれば使い、なければ `accounts.list(true)` / `messages.listTags()` | AC-W1 の後の `list_folders`、`list_tags` |
| 名前付きパイプの ACL | 既定のまま。リモート接続は拒否し、相互 HMAC で認証 | 実機の確認は不要（残るリスクとして報告） |
