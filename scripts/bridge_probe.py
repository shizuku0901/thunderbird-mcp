"""手動の受け入れ確認（AC-W1）用の道具。人間が、Windows の実機で実行する。

名前付きパイプへ直接、読み取り専用の要求（health、folders.list）を出す。

使い方（リポジトリのルートで）:
  uv run python scripts/bridge_probe.py health
  uv run python scripts/bridge_probe.py folders
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from thunderbird_mcp import ipc, safety

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="call the health op (AC-W1)").set_defaults(run=command_health)
    commands.add_parser("folders", help="list folder ids, names, paths and special uses").set_defaults(run=command_folders)
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
