# thunderbird-mcp

[![Latest release](https://img.shields.io/github/v/release/shizuku0901/thunderbird-mcp)](https://github.com/shizuku0901/thunderbird-mcp/releases/latest) [![Release date](https://img.shields.io/github/release-date/shizuku0901/thunderbird-mcp)](https://github.com/shizuku0901/thunderbird-mcp/releases/latest)

Windows で起動中の Thunderbird のメールを、Claude（MCP クライアント）が**検索・閲覧・整理し、下書きを作れる**ようにする MCP サーバーです。

> **このプロジェクトは、人間（Rio）と Claude（Anthropic の AI）が共同で作っています。**
> 仕様の決定、設計の承認、実機での確認、レジストリ登録や拡張のインストールは人間が担当し、仕様書（[SPEC.md](SPEC.md)）にもとづく実装、テスト、ドキュメントの作成は Claude Code が担当します。コミットには `Co-Authored-By` で Claude を明記しています。人間と AI の役割を分け、重要な判断は必ず人間が承認する、という進め方そのものも、このプロジェクトの目的の一部です。

## できること

| 種別 | 機能 |
|---|---|
| 読み取り | アカウント・フォルダの一覧、メッセージの検索（件名・未読・フラグ・タグ・日付）、一覧、本文の取得、添付の一覧 |
| ローカルへの書き込み | 添付ファイルの保存（上書きしない、保存先は決まったフォルダの直下だけ） |
| 書き込み | 既読／未読、フラグ、タグの付け外し、**下書きの作成** |
| 診断 | `health_check` |

公開ツールは 14 個で、[SPEC.md](SPEC.md) の §6 と完全に一致させています。

## あえて持たせない機能

メールを失う、あるいは意図せず外へ出す操作は、**構造的に不可能**にしています。

- **削除系:** ごみ箱・迷惑メール・送信トレイへの移動、削除フラグ、完全消去
- **送信系:** メール送出プロトコル、送信キュー、下書きの自動送出
- **フォルダ間の移動:** 誤作動を心配して外しました（SPEC §13）

「実装しない」だけでなく、ツール・関数・拡張の権限・引数のどこにも置かず、テストで検査しています。メール本文に書かれた指示は、データとして扱い、指示としては扱いません。

## 仕組み

```text
Claude Code ──stdio──▶ MCP サーバー（Python / FastMCP）
                         └ MailService（安全検査）
                             └ BridgeBackend ──名前付きパイプ（HMAC 認証）──▶ Native Messaging ホスト
                                                                                  └ Thunderbird 拡張（JS の薄い中継）
```

- 判断はすべて Python 側（`safety.py`、`service.py`）で行い、拡張は中継と権限の宣言に限ります。
- 拡張は、削除用・送信用の権限を宣言しません。宣言しなければ、Thunderbird 自身がその API を拒否します。
- ポートは開かず、認証情報も持ちません。

詳しくは [SPEC.md](SPEC.md)（仕様の正本）を見てください。

## 必要なもの

- Windows
- Thunderbird 140 以上
- Python 3.11 以上、[uv](https://docs.astral.sh/uv/)

## 開発

```powershell
uv sync                  # 依存の同期
uv run pytest -q         # テスト（偽のバックエンドだけを使う。実メールには触れない）
```

偽のバックエンドで、サーバーを起動する:

```powershell
$env:THUNDERBIRD_MCP_BACKEND="fake"; uv run python -m thunderbird_mcp.server
```

## 実機での利用

ホストの登録と拡張のインストールは、人間が行います。手順は [docs/manual-acceptance.md](docs/manual-acceptance.md) にあります。**最初は、テスト用のアカウントとメールで試してください。**

### 拡張を Release から入れる（自動更新つき）

拡張（`.xpi`）は、GitHub の [Releases](https://github.com/shizuku0901/thunderbird-mcp/releases) で配っています。

1. 最新の Release から `thunderbird-mcp-bridge-X.Y.Z.xpi` をダウンロードする。
2. Thunderbird の「アドオンとテーマ」→ 歯車 →「ファイルからアドオンをインストール」で選ぶ。
3. 以後は、新しい Release が出ると、Thunderbird が約1日ごとに確認して自動で更新します。すぐ確かめるときは、歯車 →「アドオンの更新を確認」。

- 自動更新されるのは拡張だけです。Python のサーバーとホストの登録は、別に更新してください。Release のノートに「サーバーも更新」とあるときは、そのとおりにします。
- 0.3.0 より前の版には自動更新の設定がないため、0.3.0 だけは手で入れ直してください。
- この `.xpi` は Mozilla の署名がありません。ソースは `extension/` にあり、`uv run python scripts/build_xpi.py` で同じ `.xpi` を作れます（`dist/` に出力）。

### Claude Code に登録する

```text
claude mcp add thunderbird --scope user -- uv --directory C:\path\to\thunderbird-mcp run python -m thunderbird_mcp.server
```

### Claude デスクトップに登録する

Claude デスクトップは、設定ファイルに書いた MCP サーバーを、自分で起動します。

1. Claude デスクトップの「設定」→「開発者」→「構成を編集」を開く（`%APPDATA%\Claude\claude_desktop_config.json` が開きます）。
2. `mcpServers` に、次のように書く。`C:\\path\\to\\thunderbird-mcp` は、このリポジトリの場所に置き換えます。JSON の中では、`\` を `\\` と重ねて書きます。

   ```json
   {
     "mcpServers": {
       "thunderbird": {
         "command": "uv",
         "args": [
           "--directory", "C:\\path\\to\\thunderbird-mcp",
           "run", "python", "-m", "thunderbird_mcp.server"
         ]
       }
     }
   }
   ```

   - すでに `mcpServers` がある場合は、その中に `"thunderbird": { ... }` だけを足します。
   - `uv` が見つからないと言われたら、PowerShell で `(Get-Command uv).Source` を実行し、出てきたフルパスを `command` に書きます。
3. Claude デスクトップを**完全に終了して**（タスクトレイのアイコンからも終了）、起動し直す。
4. 新しい会話で、ツールの一覧に `thunderbird` が出ること、`health_check` が `ok: true` を返すことを確かめる。

うまくいかないときは、ログ（`%APPDATA%\Claude\logs\mcp-server-thunderbird.log`）を見ます。サーバーは標準出力に何も書かず、ログは標準エラーに出します。Thunderbird が起動していないと、`cannot connect to Thunderbird ...` というエラーになります（[docs/manual-acceptance.md](docs/manual-acceptance.md) の AC-W5）。

### 環境変数

| 変数 | 既定値 | 意味 |
|---|---|---|
| `THUNDERBIRD_MCP_BACKEND` | `bridge` | `bridge` または `fake` |
| `THUNDERBIRD_MCP_ATTACHMENT_DIR` | `%USERPROFILE%\Downloads\thunderbird-mcp` | 添付の保存先 |
| `THUNDERBIRD_MCP_JOURNAL` | `%LOCALAPPDATA%\thunderbird-mcp\journal.jsonl` | 書き込みの操作ジャーナル（本文と件名は記録しない） |

## 開発の進め方

- 仕様 → テスト → 実装の順（TDD）で進めます。
- 作業は機能ごとのブランチで行い、Pull Request を人間がレビューしてマージします。
- 公開ツールの追加や改名は、先に SPEC を更新し、人間の承認を得てから行います。

運用ルールの詳細は [CLAUDE.md](CLAUDE.md) にあります。
