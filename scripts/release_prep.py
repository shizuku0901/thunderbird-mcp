"""manifest.json の version を上げて、未署名xpiをビルドする。

usage: python scripts/release_prep.py [patch|minor|major]   (default: patch)
"""

from __future__ import annotations

import json
import re
import sys

from build_xpi import SOURCE_DIR, build

MANIFEST = SOURCE_DIR / "manifest.json"
VERSION_RE = re.compile(r'("version"\s*:\s*")(\d+)\.(\d+)\.(\d+)(")')


def bump(kind: str) -> tuple[str, str]:
    text = MANIFEST.read_text(encoding="utf-8")
    m = VERSION_RE.search(text)
    if not m:
        raise SystemExit("manifest.json の version が X.Y.Z 形式ではありません")
    major, minor, patch = (int(x) for x in m.group(2, 3, 4))
    old = f"{major}.{minor}.{patch}"
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    new = f"{major}.{minor}.{patch}"
    MANIFEST.write_text(
        text[: m.start()] + f"{m.group(1)}{new}{m.group(5)}" + text[m.end():],
        encoding="utf-8",
    )
    json.loads(MANIFEST.read_text(encoding="utf-8"))  # 壊れていないか確認
    return old, new


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "patch"
    if kind not in {"patch", "minor", "major"}:
        raise SystemExit("引数は patch / minor / major のいずれか")
    old, new = bump(kind)
    xpi = build()
    sys.stderr.write(f"version {old} -> {new}\nbuilt {xpi}\n")
    sys.stdout.write(f"{xpi}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
