# thunderbird-mcp

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

Claude Code に登録する例:

```text
claude mcp add thunderbird --scope user -- uv --directory C:\path\to\thunderbird-mcp run python -m thunderbird_mcp.server
```

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
