"""添付保存のパストラバーサル防止（Windows の流儀を含む）を確かめる。"""
from __future__ import annotations

from pathlib import Path

import pytest

from thunderbird_mcp.safety import SafetyError, safe_attachment_path

ACCEPTED = [
    ("report.pdf", "report.pdf"),
    ("../evil.txt", "evil.txt"),
    ("..\\evil.txt", "evil.txt"),
    ("C:\\Windows\\System32\\evil.dll", "evil.dll"),
    ("\\\\server\\share\\evil.txt", "evil.txt"),
    ("/etc/passwd", "passwd"),
    ("dir/sub/ok.txt", "ok.txt"),
    ("資料_2026-11.xlsx", "資料_2026-11.xlsx"),
]

REJECTED = [
    "",
    ".",
    "..",
    "../",
    "..\\",
    "CON",
    "con.txt",
    "NUL",
    "PRN.log",
    "AUX",
    "COM1.log",
    "LPT9",
    "file.txt:stream",
    "C:evil.txt",
    "evil.txt.",
    "evil.txt ",
    "tab\tname.txt",
    "new\nline.txt",
    "what?.txt",
    "pipe|name.txt",
    "<x>.txt",
    'q"x.txt',
    "star*.txt",
    "a" * 201 + ".txt",
]


@pytest.mark.parametrize(("name", "expected"), ACCEPTED)
def test_accepted_names_stay_inside_root(tmp_path: Path, name, expected):
    root = tmp_path / "att"
    path = safe_attachment_path(root, name)
    assert path.parent == root.resolve()
    assert path.name == expected


@pytest.mark.parametrize("name", REJECTED)
def test_rejected_names(tmp_path: Path, name):
    with pytest.raises(SafetyError):
        safe_attachment_path(tmp_path / "att", name)


def test_does_not_overwrite_existing_file(tmp_path: Path):
    root = tmp_path / "att"
    first = safe_attachment_path(root, "a.txt")
    first.write_bytes(b"1")
    second = safe_attachment_path(root, "a.txt")
    assert second != first
    assert second.parent == root.resolve()
    assert first.read_bytes() == b"1"


def test_service_saves_hostile_name_inside_root(service, attachment_root, sample):
    result = service.save_attachment("m2", "1.2")
    saved = Path(result["path"])
    assert saved.parent == attachment_root.resolve()
    assert saved.name == "evil.cmd"
    assert saved.read_bytes() == sample.attachment_bytes
    assert result["size"] == len(sample.attachment_bytes)


def test_service_second_save_gets_new_name(service, attachment_root):
    first = Path(service.save_attachment("m2", "1.2")["path"])
    second = Path(service.save_attachment("m2", "1.2")["path"])
    assert first != second
    assert second.parent == attachment_root.resolve()


@pytest.mark.parametrize("override", ["CON", "..", "x:y.txt", "evil.txt."])
def test_service_checks_filename_override(service, override):
    with pytest.raises(SafetyError):
        service.save_attachment("m2", "1.2", filename=override)


def test_service_unknown_part_is_refused(service):
    with pytest.raises((SafetyError, KeyError)):
        service.save_attachment("m2", "9.9")