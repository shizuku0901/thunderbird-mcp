"""scripts/make_update_manifest.py を、偽の manifest と偽の .xpi で確かめる（実際の extension/ や dist/ は触らない）。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
REPO = "owner/repo"


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("make_update_manifest", "build_xpi"):
        sys.modules.pop(name, None)
    import make_update_manifest as mod

    yield mod
    for name in ("make_update_manifest", "build_xpi"):
        sys.modules.pop(name, None)


@pytest.fixture
def manifest() -> dict:
    return {
        "version": "1.2.3",
        "browser_specific_settings": {
            "gecko": {"id": "demo-addon@local.invalid", "strict_min_version": "140.0"}
        },
    }


@pytest.fixture
def xpi(tmp_path) -> Path:
    path = tmp_path / "demo-addon-1.2.3.xpi"
    path.write_bytes(b"fake xpi bytes")
    return path


def test_entry_has_id_version_link_hash_and_min_version(module, manifest, xpi):
    result = module.build_update_manifest(manifest, xpi, REPO)
    (entry,) = result["addons"]["demo-addon@local.invalid"]["updates"]
    assert entry["version"] == "1.2.3"
    assert entry["update_link"] == (
        "https://github.com/owner/repo/releases/download/v1.2.3/demo-addon-1.2.3.xpi"
    )
    assert entry["update_hash"] == "sha256:" + hashlib.sha256(b"fake xpi bytes").hexdigest()
    assert entry["applications"] == {"gecko": {"strict_min_version": "140.0"}}


def test_min_version_is_omitted_when_manifest_has_none(module, manifest, xpi):
    del manifest["browser_specific_settings"]["gecko"]["strict_min_version"]
    (entry,) = module.build_update_manifest(manifest, xpi, REPO)["addons"][
        "demo-addon@local.invalid"
    ]["updates"]
    assert "applications" not in entry


def test_xpi_name_must_match_manifest_version(module, manifest, tmp_path):
    wrong = tmp_path / "demo-addon-9.9.9.xpi"
    wrong.write_bytes(b"x")
    with pytest.raises(SystemExit) as exc:
        module.build_update_manifest(manifest, wrong, REPO)
    assert "1.2.3" in str(exc.value)


def test_missing_gecko_id_stops_with_clear_message(module, xpi):
    with pytest.raises(SystemExit) as exc:
        module.build_update_manifest({"version": "1.2.3"}, xpi, REPO)
    assert "gecko.id" in str(exc.value)


@pytest.mark.parametrize("repo", ["", "no-slash", "a/b/c", "a b/c", "owner/repo?x=1"])
def test_bad_repo_slug_is_rejected(module, manifest, xpi, repo):
    with pytest.raises(SystemExit):
        module.build_update_manifest(manifest, xpi, repo)


def test_write_creates_json_next_to_xpi(module, manifest, xpi):
    out = module.write_update_manifest(manifest, xpi, REPO)
    assert out == xpi.parent / "updates.json"
    assert json.loads(out.read_text(encoding="utf-8"))["addons"]


def test_real_manifest_update_url_points_to_latest_release_asset():
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    url = manifest["browser_specific_settings"]["gecko"]["update_url"]
    assert url.startswith("https://github.com/")
    assert url.endswith("/releases/latest/download/updates.json")
