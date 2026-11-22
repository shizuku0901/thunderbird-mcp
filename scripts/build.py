"""Build the Thunderbird extension (.xpi) from extension/ into the TEMP folder.

The .xpi is always rebuilt from source. manifest.json sits at the zip root and
entry names use forward slashes (zipfile guarantees both).
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "extension"
SKIP_NAMES = {".DS_Store", "Thumbs.db"}


def build(out_dir: Path | None = None) -> Path:
    manifest = json.loads((SOURCE_DIR / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    name = manifest["browser_specific_settings"]["gecko"]["id"].split("@")[0]
    target_dir = out_dir or Path(tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    xpi = target_dir / f"{name}-{version}.xpi"
    xpi.unlink(missing_ok=True)
    with zipfile.ZipFile(xpi, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SOURCE_DIR.rglob("*")):
            if path.is_file() and path.name not in SKIP_NAMES:
                zf.write(path, path.relative_to(SOURCE_DIR).as_posix())
    return xpi


def main() -> int:
    xpi = build()
    sys.stderr.write(f"built {xpi}\n")
    sys.stdout.write(f"{xpi}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
