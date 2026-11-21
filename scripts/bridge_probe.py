"""手動の受け入れ確認（AC-W1 / AC-W3）用の道具。人間が、Windows の実機で実行する。

Python 側の検査（MailService）を通さずに、名前付きパイプへ直接、要求を出す。
拡張（JS）が、Python と同じ規則で拒否することを確かめるために使う。

使い方（リポジトリのルートで）:
  uv run python scripts/bridge_probe.py health
  uv run python scripts/bridge_probe.py folders
  uv run python scripts/bridge_probe.py move --message-id 123 --special-use bin --yes

注意: move は、拡張が拒否しなかった場合、実際にメールを移動してしまう。
      必ず、テスト用のアカウントとメールで行うこと。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from thunderbird_mcp import ipc, safety

# --special-use に指定できる名前と、safety の定数の対応（禁止語はここに書かない）
SPECIAL_USE_CHOICES = {
    "bin": safety.SU_BIN,
    "unwanted": safety.SU_UNWANTED,
    "pending_out": safety.SU_PENDING_OUT,
    "drafts": safety.SU_DRAFTS,
    "templates": safety.SU_TEMPLATES,
    "sent": safety.SU_SENT,
}


def call(op: str, args: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    """1 つの要求を出して、応答（ok / result / error）をそのまま返す。"""
    key = ipc.load_key(ipc.default_key_path())
    channel = ipc.connect_pipe(ipc.pipe_name())
    try:
        ipc.client_handshake(channel, key)
        ipc.write_message(channel, {"id": 1, "op": op, "args": args or {}})
        return ipc.read_message(channel, ipc.Reassembler(), timeout=timeout)
    finally:
        channel.close()


def pick_folder(folders: list[dict[str, Any]], special_use: str) -> dict[str, Any] | None:
    for folder in folders:
        if special_use in folder.get("special_use", []):
            return folder
    return None


def command_health(_: argparse.Namespace) -> int:
    response = call("health")
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response.get("ok") else 1


def command_folders(_: argparse.Namespace) -> int:
    response = call("folders.list", {"account_id": None})
    if not response.get("ok"):
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 1
    for folder in response["result"]:
        print(f'{folder["id"]}\t{folder["name"]}\t{folder["path"]}\t{",".join(folder["special_use"])}')
    return 0


def command_move(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to run without --yes. If the extension does not refuse, the message really moves.")
        return 2
    folder_id = args.folder_id
    if folder_id is None:
        listing = call("folders.list", {"account_id": None})
        if not listing.get("ok"):
            print(json.dumps(listing, ensure_ascii=False, indent=2))
            return 1
        folder = pick_folder(listing["result"], SPECIAL_USE_CHOICES[args.special_use])
        if folder is None:
            print(f"No folder with the special use {args.special_use!r} was found.")
            return 1
        folder_id = folder["id"]
    response = call("messages.move", {"message_ids": [args.message_id], "destination_folder_id": folder_id})
    print(json.dumps(response, ensure_ascii=False, indent=2))
    if response.get("ok") is False and response.get("code") == "refused":
        print("RESULT: the extension refused the move (expected for AC-W3).")
        return 0
    if response.get("ok") is True:
        print("RESULT: WARNING - the extension did NOT refuse the move. Move the message back and report this.")
        return 3
    print("RESULT: the request failed for another reason; AC-W3 is not confirmed.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="call the health op (AC-W1)").set_defaults(run=command_health)
    commands.add_parser("folders", help="list folder ids, names, paths and special uses").set_defaults(run=command_folders)
    move = commands.add_parser("move", help="post messages.move directly, bypassing the Python checks (AC-W3)")
    move.add_argument("--message-id", required=True, help="id of a TEST message (digits)")
    target = move.add_mutually_exclusive_group(required=True)
    target.add_argument("--folder-id")
    target.add_argument("--special-use", choices=sorted(SPECIAL_USE_CHOICES))
    move.add_argument("--yes", action="store_true", help="confirm that this really may move the message")
    move.set_defaults(run=command_move)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except ConnectionError as exc:
        print(f"Cannot connect: {exc}", file=sys.stderr)
        return 1
    except (ipc.IpcError, TimeoutError, OSError) as exc:
        print(f"Probe failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
