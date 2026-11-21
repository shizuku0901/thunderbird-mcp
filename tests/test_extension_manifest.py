"""拡張のマニフェストが、最小権限と承認済みの範囲に収まっていることを確かめる。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from thunderbird_mcp import safety

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "extension" / "manifest.json"
HOST_MANIFEST = ROOT / "scripts" / "native-host" / "thunderbird_mcp_host.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _permissions(manifest: dict) -> set[str]:
    return set(manifest.get("permissions", [])) | set(manifest.get("optional_permissions", []))


def test_manifest_exists():
    assert MANIFEST.is_file(), "extension/manifest.json がまだない（Red）"


def test_no_forbidden_permissions():
    assert not _permissions(_load(MANIFEST)) & safety.FORBIDDEN_PERMISSIONS


def test_permissions_within_allowlist():
    extra = _permissions(_load(MANIFEST)) - safety.ALLOWED_EXTENSION_PERMISSIONS
    assert not extra, f"許可リスト外の権限: {sorted(extra)}"


def test_move_permission_is_not_declared_or_allowed():
    # SPEC §13: 2026-11-21 に移動を外した。
    assert "messagesMove" not in _permissions(_load(MANIFEST))
    assert "messagesMove" not in safety.ALLOWED_EXTENSION_PERMISSIONS


def test_no_forbidden_manifest_keys():
    assert not set(_load(MANIFEST)) & safety.FORBIDDEN_MANIFEST_KEYS


def test_no_host_permissions():
    manifest = _load(MANIFEST)
    assert not manifest.get("host_permissions")
    assert not any("://" in p or p == "<all_urls>" for p in _permissions(manifest))


def test_native_messaging_declared():
    assert "nativeMessaging" in _permissions(_load(MANIFEST))


def test_background_scripts_exist():
    manifest = _load(MANIFEST)
    scripts = manifest.get("background", {}).get("scripts", [])
    assert scripts, "background.scripts がない"
    for script in scripts:
        assert (MANIFEST.parent / script).is_file(), script


def test_native_host_manifest_matches_extension():
    manifest = _load(MANIFEST)
    host = _load(HOST_MANIFEST)
    extension_id = manifest["browser_specific_settings"]["gecko"]["id"]
    assert host["type"] == "stdio"
    assert host["allowed_extensions"] == [extension_id]
    assert re.fullmatch(r"\w+(\.\w+)*", host["name"])