"""GitHub Release に添える updates.json を、manifest.json と .xpi から作る。

Thunderbird は manifest.json の update_url を見に行き、この updates.json の
version が新しければ update_link から .xpi を取得して入れ替える。

usage: python scripts/make_update_manifest.py [owner/repo]
  owner/repo を省くと環境変数 GITHUB_REPOSITORY を使う。
  先に build_xpi.py で .xpi を作っておく（dist/ にある最新の .xpi を読む）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from build_xpi import OUT_DIR, SOURCE_DIR, build

REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def build_update_manifest(manifest: dict, xpi: Path, repo: str) -> dict:
    if not REPO_RE.fullmatch(repo):
        raise SystemExit(f"owner/repo の形式ではありません: {repo!r}")
    try:
        gecko = manifest["browser_specific_settings"]["gecko"]
        addon_id = gecko["id"]
    except KeyError:
        raise SystemExit("manifest.json に browser_specific_settings.gecko.id がありません")
    version = manifest["version"]
    if f"-{version}.xpi" not in xpi.name:
        raise SystemExit(f".xpi の名前 {xpi.name} が manifest の version {version} と合いません")
    entry: dict = {
        "version": version,
        "update_link": f"https://github.com/{repo}/releases/download/v{version}/{xpi.name}",
        "update_hash": "sha256:" + hashlib.sha256(xpi.read_bytes()).hexdigest(),
    }
    if "strict_min_version" in gecko:
        entry["applications"] = {"gecko": {"strict_min_version": gecko["strict_min_version"]}}
    return {"addons": {addon_id: {"updates": [entry]}}}


def write_update_manifest(manifest: dict, xpi: Path, repo: str) -> Path:
    data = build_update_manifest(manifest, xpi, repo)
    out = xpi.parent / "updates.json"
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    repo = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GITHUB_REPOSITORY", "")
    manifest = json.loads((SOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    xpi = build(OUT_DIR)  # 同じソースから作り直す（再現可能ビルドなのでハッシュは変わらない）
    out = write_update_manifest(manifest, xpi, repo)
    sys.stderr.write(f"wrote {out}\n")
    sys.stdout.write(f"{out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
