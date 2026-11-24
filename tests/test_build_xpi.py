"""scripts/build_xpi.py を、偽の extension/ で確かめる（実際の extension/ や dist/ は触らない）。"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def build_xpi(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("build_xpi", None)
    import build_xpi as module

    src = tmp_path / "extension"
    src.mkdir()
    manifest = {
        "version": "1.2.3",
        "browser_specific_settings": {"gecko": {"id": "demo-addon@local.invalid"}},
    }
    (src / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (src / "background.js").write_text("// relay\n", encoding="utf-8")
    (src / "sub").mkdir()
    (src / "sub" / "inner.js").write_text("// inner\n", encoding="utf-8")
    monkeypatch.setattr(module, "SOURCE_DIR", src)
    monkeypatch.setattr(module, "OUT_DIR", tmp_path / "out")
    yield module
    sys.modules.pop("build_xpi", None)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_is_reproducible(build_xpi):
    first = _sha(build_xpi.build())
    second = _sha(build_xpi.build())
    assert first == second


def test_manifest_is_at_zip_root_with_forward_slashes(build_xpi):
    xpi = build_xpi.build()
    assert xpi.name == "demo-addon-1.2.3.xpi"
    with zipfile.ZipFile(xpi) as zf:
        names = zf.namelist()
    assert "manifest.json" in names
    assert "sub/inner.js" in names
    assert not any("\\" in n for n in names)


def test_unwanted_files_are_excluded(build_xpi):
    src = build_xpi.SOURCE_DIR
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_bytes(b"x")
    (src / "old.bak").write_text("x", encoding="utf-8")
    (src / "NOTES.md").write_text("x", encoding="utf-8")
    (src / "Thumbs.db").write_bytes(b"x")
    with zipfile.ZipFile(build_xpi.build()) as zf:
        names = set(zf.namelist())
    assert names == {"manifest.json", "background.js", "sub/inner.js"}


def test_missing_gecko_id_stops_with_clear_message(build_xpi):
    (build_xpi.SOURCE_DIR / "manifest.json").write_text(
        json.dumps({"version": "1.0.0"}), encoding="utf-8"
    )
    with pytest.raises(SystemExit) as exc:
        build_xpi.build()
    assert "gecko.id" in str(exc.value)


def test_no_temp_file_is_left_behind(build_xpi):
    xpi = build_xpi.build()
    assert [p.name for p in xpi.parent.iterdir()] == [xpi.name]
