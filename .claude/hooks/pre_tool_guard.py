"""PreToolUse ガード。

1. Thunderbird のプロファイル、メールファイル、IPC の鍵に触れる操作を止める
   （Read の deny ルールは Bash 経由のサブプロセスに効かないため、ここで補う）。
2. safety.py 以外のコードへ禁止語を書き込む Write/Edit を止める。
   禁止語は safety.py から ast で読む（実行はしない）。safety.py がまだなければ 2 は飛ばす。
   safety.py があるのに FORBIDDEN_TERMS を読めないときは、コードの書き込みを止める。
3. git の commit / push / add を、ブランチ運用のルールに合わせて止める。
   main に commit しない、push は作業ブランチだけ、add は名前を指定する。
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
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

PROTECTED_BRANCHES = {"main", "master", ""}  # 空文字は detached HEAD など
BRANCH_RE = re.compile(r"^(feat|fix|refactor|chore|docs|test|build)/[a-z0-9][a-z0-9-]*$")
GIT_FALLBACK = re.compile(r"\bgit(\.exe)?\b.*\b(commit|push|add)\b", re.IGNORECASE)
SECRET_NAMES = {"ipc.key", "settings.local.json"}


def _block(reason: str) -> None:
    sys.stderr.write(f"[pre_tool_guard] {reason}\n")
    sys.exit(2)


def _load_terms() -> tuple[str, ...] | None:
    """() は safety.py がまだない（検査を飛ばす）。None は safety.py があるのに読めない。"""
    if not SAFETY.is_file():
        return ()
    try:
        tree = ast.parse(SAFETY.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        value_node = None
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FORBIDDEN_TERMS" for t in node.targets
        ):
            value_node = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "FORBIDDEN_TERMS"
            and node.value is not None
        ):
            value_node = node.value
        if value_node is None:
            continue
        try:
            value = ast.literal_eval(value_node)
        except ValueError:
            return None
        return tuple(str(v).lower() for v in value)
    return None


def _current_branch(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_violation(command: str, cwd: str) -> str | None:
    splitter = r"&&|\|\||;|\|" if "<<" in command else r"&&|\|\||;|\||\r?\n"
    for raw in re.split(splitter, command):
        seg = raw.strip().lstrip("&").strip()
        if not seg:
            continue
        tokens = [t.strip("'\"`") for t in seg.split()]
        if not tokens or tokens[0].lower() not in ("git", "git.exe"):
            if GIT_FALLBACK.search(seg):
                return "git は、コマンドの先頭で直接実行する（bash -c や cmd /c で包まない）。"
            continue
        if len(tokens) < 2:
            continue
        if tokens[1].startswith("-"):
            return "git のグローバルオプション（-C、-c など）は使わない。"
        sub, args = tokens[1], tokens[2:]
        flags = [a for a in args if a.startswith("-")]
        pos = [a for a in args if not a.startswith("-")]

        if sub == "commit":
            if _current_branch(cwd) in PROTECTED_BRANCHES:
                return "main では commit しない。先に作業ブランチを切る（git switch -c <type>/<name>）。"
            if "--amend" in flags or "--no-verify" in flags:
                return "--amend と --no-verify は使わない。誤りは新しい fix commit で直す。"
            if any(re.fullmatch(r"-[A-Za-z]*n[A-Za-z]*", f) for f in flags):
                return "-n（--no-verify と同じ）は使わない。"

        elif sub == "add":
            if any(f in ("-A", "--all", "-u", "--update", "-f", "--force") for f in flags):
                return "git add はファイル名を指定する（-A、--all、-u、-f は使わない）。"
            if not pos:
                return "git add にはファイル名を指定する。"
            for p in pos:
                name = p.replace("\\", "/")
                base = name.rsplit("/", 1)[-1]
                if name in (".", "./", "*", ":/") or name.endswith("/."):
                    return "git add . や * は使わない。ファイル名を指定する。"
                if base == ".env" or base.startswith(".env.") or base in SECRET_NAMES:
                    return f"{base} は add しない。"

        elif sub == "push":
            branch = _current_branch(cwd)
            if branch in PROTECTED_BRANCHES or not BRANCH_RE.match(branch):
                return "push できるのは作業ブランチ（<type>/<name>）だけ。"
            if any(f not in ("-u", "--set-upstream") for f in flags):
                return "push のオプションは -u だけ許可する。"
            if pos != ["origin", branch]:
                return "push は `git push -u origin <現在のブランチ>` の形だけ許可する。"

        elif sub == "rebase":
            return "rebase はしない。"

        elif sub == "reset" and "--hard" in flags:
            return "reset --hard はしない。"
    return None


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

    if tool in {"Bash", "PowerShell"}:
        reason = _git_violation(str(tool_input.get("command", "")), str(data.get("cwd") or PROJECT))
        if reason:
            _block(reason)

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
        terms = _load_terms()
        if terms is None:
            _block(
                "safety.py の FORBIDDEN_TERMS を読めない。"
                "小文字のリテラルの tuple で定義する（SPEC §8）。"
            )
        parts = [
            str(tool_input.get("content", "")),
            str(tool_input.get("new_string", "")),
            str(tool_input.get("new_source", "")),
        ]
        for item in tool_input.get("edits", []) or []:
            if isinstance(item, dict):
                parts.append(str(item.get("new_string", "")))
        lowered = "\n".join(parts).lower()
        hits = [term for term in terms if term in lowered]
        if hits:
            _block(
                f"禁止語を safety.py 以外に書こうとしている（{len(hits)} 語）。"
                "safety の定数を参照する形に直す。"
            )
    sys.exit(0)


if __name__ == "__main__":
    main()