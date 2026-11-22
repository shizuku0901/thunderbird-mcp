"""scripts/release_prep.py の bump() を、manifest の複製で確かめる（実際の extension/ は触らない）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TEXT = '{\n  "name": "x",\n  "version": "0.1.9",\n  "id": "a"\n}\n'


@pytest.fixture
def release_prep(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("release_prep", "build_xpi"):
        sys.modules.pop(name, None)
    import release_prep as module

    manifest = tmp_path / "manifest.json"
    manifest.write_text(TEXT, encoding="utf-8")
    monkeypatch.setattr(module, "MANIFEST", manifest)
    yield module
    for name in ("release_prep", "build_xpi"):
        sys.modules.pop(name, None)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("patch", "0.1.10"), ("minor", "0.2.0"), ("major", "1.0.0")],
)
def test_bump_changes_only_the_version_line(release_prep, kind, expected):
    old, new = release_prep.bump(kind)
    assert (old, new) == ("0.1.9", expected)
    after = release_prep.MANIFEST.read_text(encoding="utf-8")
    assert after == TEXT.replace("0.1.9", expected)
    assert json.loads(after)["version"] == expected


def test_bump_rejects_non_semver_version(release_prep):
    release_prep.MANIFEST.write_text('{"version": "1.0"}', encoding="utf-8")
    with pytest.raises(SystemExit):
        release_prep.bump("patch")
