"""下書きが下書きフォルダに保存されるだけで、外へ出る経路がないことを確かめる。"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from thunderbird_mcp import safety
from thunderbird_mcp.safety import SafetyError

ROOT = Path(__file__).resolve().parents[1]


def test_draft_is_saved_only_into_drafts_folder(service, fake):
    result = service.create_draft(to=["alice@example.com"], subject="Hello", body="line1\nline2")
    assert result["status"] == "saved_as_draft"
    assert fake.calls == ["save_draft"], "下書きの作成で save_draft 以外が呼ばれた"
    assert safety.SU_DRAFTS in fake.get_folder(result["folder_id"]).special_use
    assert len(fake.drafts) == 1


def test_draft_signature_has_no_extra_switch(service):
    params = set(inspect.signature(service.create_draft).parameters)
    assert params == {"to", "subject", "body", "cc", "bcc", "identity_id"}


@pytest.mark.parametrize("subject", [
    "Hi\r\nBcc: evil@example.com",
    "Hi\nX-Header: 1",
    "Hi\rthere",
    "bell\x07",
])
def test_header_injection_in_subject_is_refused(service, fake, subject):
    with pytest.raises(SafetyError):
        service.create_draft(to=["alice@example.com"], subject=subject, body="x")
    assert "save_draft" not in fake.calls


@pytest.mark.parametrize("address", [
    "evil@example.com\r\nBcc: x@example.com",
    "not-an-address",
    "",
    "a@b",
    "Alice <alice@example.com>",
    "alice@example.com, bob@example.com",
])
def test_invalid_address_is_refused(service, fake, address):
    with pytest.raises(SafetyError):
        service.create_draft(to=[address], subject="ok", body="x")
    assert "save_draft" not in fake.calls


def test_cc_and_bcc_are_checked(service, fake):
    with pytest.raises(SafetyError):
        service.create_draft(to=["alice@example.com"], subject="ok", body="x", cc=["bad\r\n@example.com"])
    with pytest.raises(SafetyError):
        service.create_draft(to=["alice@example.com"], subject="ok", body="x", bcc=["nope"])
    assert "save_draft" not in fake.calls


def test_recipient_is_required(service, fake):
    with pytest.raises(SafetyError):
        service.create_draft(to=[], subject="ok", body="x")


def test_recipient_limit(service, fake):
    many = [f"user{i}@example.com" for i in range(safety.MAX_RECIPIENTS + 1)]
    with pytest.raises(SafetyError):
        service.create_draft(to=many, subject="ok", body="x")
    assert "save_draft" not in fake.calls


def test_extension_manifest_cannot_transmit():
    manifest = json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
    permissions = set(manifest.get("permissions", [])) | set(manifest.get("optional_permissions", []))
    assert not permissions & safety.FORBIDDEN_PERMISSIONS