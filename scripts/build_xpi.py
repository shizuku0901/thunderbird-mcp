"""Build the Thunderbird extension (.xpi) from extension/ into the repo-local TEMP/ folder (git-ignored).

The .xpi is always rebuilt from source. manifest.json sits at the zip root and
entry names use forward slashes. Zip timestamps are fixed so builds are reproducible.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "extension"
OUT_DIR = ROOT / "TEMP"

SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_DIRS = {"__pycache__", "node_modules", ".git"}
SKIP_SUFFIXES = {".pyc", ".bak", ".md"}  # ライセンス表記のmdを同梱したいなら .md を外す
FIXED_DATE = (2020, 1, 1, 0, 0, 0)  # 再現可能ビルド用


def _included(path: Path) -> bool:
    rel = path.relative_to(SOURCE_DIR)
    return (
        path.is_file()
        and path.name not in SKIP_NAMES
        and path.suffix not in SKIP_SUFFIXES
        and not (set(rel.parts) & SKIP_DIRS)
    )


def build(out_dir: Path | None = None) -> Path:
    manifest = json.loads((SOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    try:
        addon_id = manifest["browser_specific_settings"]["gecko"]["id"]
    except KeyError:
        raise SystemExit(
            "manifest.json に browser_specific_settings.gecko.id がありません"
        )
    name = addon_id.split("@")[0]
    target_dir = out_dir or OUT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    xpi = target_dir / f"{name}-{manifest['version']}.xpi"
    tmp = xpi.with_suffix(".xpi.tmp")
    tmp.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(p for p in SOURCE_DIR.rglob("*") if _included(p)):
                info = zipfile.ZipInfo(
                    path.relative_to(SOURCE_DIR).as_posix(), FIXED_DATE
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
        tmp.replace(xpi)
    finally:
        tmp.unlink(missing_ok=True)
    return xpi


def main() -> int:
    xpi = build()
    sys.stderr.write(f"built {xpi}\n")
    sys.stdout.write(f"{xpi}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
