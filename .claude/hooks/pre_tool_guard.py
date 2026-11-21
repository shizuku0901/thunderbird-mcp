"""PreToolUse ガード。

1. Thunderbird のプロファイル、メールファイル、IPC の鍵に触れる操作を止める
   （Read の deny ルールは Bash 経由のサブプロセスに効かないため、ここで補う）。
2. safety.py 以外のコードへ禁止語を書き込む Write/Edit を止める。
   禁止語は safety.py から ast で読む（実行はしない）。safety.py がまだなければ 2 は飛ばす。
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
SAFETY = PROJECT / "src" / "thunderbird_mcp" / "safety.py"
CODE_SUFFIXES = {".py", ".pyi", ".js", ".mjs", ".cjs", ".json", ".toml", ".ps1", ".psm1", ".cmd", ".bat"}

PROFILE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"thunderbird[\\/]+profiles",
        r"appdata[\\/]+(roaming|local)[\\/]+thunderbird([\\/]|$|\s|\")",
        r"%appdata%[\\/]+thunderbird",
        r"\$env:appdata[\\/]+thunderbird",
        r"imapmail",
        r"local folders",
        r"\.msf\b",
        r"\.mbox\b",
        r"\.eml\b",
        r"parent\.lock",
        r"thunderbird-mcp[\\/]+ipc\.key",
    )
]


def _block(reason: str) -> None:
    sys.stderr.write(f"[pre_tool_guard] {reason}\n")
    sys.exit(2)


def _load_terms() -> tuple[str, ...]:
    if not SAFETY.is_file():
        return ()
    try:
        tree = ast.parse(SAFETY.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return ()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FORBIDDEN_TERMS" for t in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return ()
            return tuple(str(v).lower() for v in value)
    return ()


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    tool = str(data.get("tool_name", ""))
    tool_input = data.get("tool_input") or {}

    haystack = " ".join(
        str(tool_input.get(key, "")) for key in ("command", "file_path", "path", "pattern", "glob", "notebook_path")
    )
    for pattern in PROFILE_PATTERNS:
        if pattern.search(haystack):
            _block("Thunderbird のプロファイル、メールファイル、IPC の鍵には触れない（CLAUDE.md）。")

    if tool in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        raw_path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        if not raw_path:
            sys.exit(0)
        target = Path(raw_path)
        if target.suffix.lower() not in CODE_SUFFIXES:
            sys.exit(0)
        try:
            if target.resolve() == SAFETY.resolve():
                sys.exit(0)
        except OSError:
            pass
        parts = [str(tool_input.get("content", "")), str(tool_input.get("new_string", "")), str(tool_input.get("new_source", ""))]
        for item in tool_input.get("edits", []) or []:
            if isinstance(item, dict):
                parts.append(str(item.get("new_string", "")))
        lowered = "\n".join(parts).lower()
        hits = [term for term in _load_terms() if term in lowered]
        if hits:
            _block(
                f"禁止語を safety.py 以外に書こうとしている（{len(hits)} 語）。"
                "safety の定数を参照する形に直す。"
            )
    sys.exit(0)


if __name__ == "__main__":
    main()