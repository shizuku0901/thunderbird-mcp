"""Stop ガード。

- Red 段階（.claude/phase が green でない）: 禁止語スキャンのテストだけを実行する。
  ほかのテストが Red でも止めない。禁止語の混入があったときだけ続行させる（exit 2）。
- Green 段階: 全テストを実行し、失敗したら続行させる。
- stop_hook_active が true なら、すぐ抜けて無限ループを防ぐ。
- safety.py がまだなければ何もしない（最初期）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
PHASE_FILE = PROJECT / ".claude" / "phase"
SAFETY = PROJECT / "src" / "thunderbird_mcp" / "safety.py"
NO_TESTS_COLLECTED = 5


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        data = {}
    if data.get("stop_hook_active"):
        sys.exit(0)
    if not SAFETY.is_file():
        sys.exit(0)

    phase = PHASE_FILE.read_text(encoding="utf-8").strip().lower() if PHASE_FILE.is_file() else "red"
    targets = [] if phase == "green" else ["tests/test_forbidden_terms_scan.py"]
    cmd = ["uv", "run", "pytest", "-q", "--no-header", *targets]
    try:
        result = subprocess.run(cmd, cwd=PROJECT, capture_output=True, text=True, timeout=280)
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"[stop_gate] テストを実行できなかった（止めない）: {exc}\n")
        sys.exit(0)

    if result.returncode in (0, NO_TESTS_COLLECTED):
        sys.exit(0)
    tail = (result.stdout + "\n" + result.stderr)[-3000:]
    sys.stderr.write(f"[stop_gate] phase={phase} のチェックが失敗。直してから終える。\n{tail}\n")
    sys.exit(2)


if __name__ == "__main__":
    main()