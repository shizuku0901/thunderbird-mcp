"""危険なタグと不正なタグを拒否することを確かめる。"""
from __future__ import annotations

import pytest

from thunderbird_mcp import safety
from thunderbird_mcp.safety import SafetyError

BAD_KEYS = [
    "has space",
    "bell\x07",
    "paren(",
    'quote"',
    "brace{",
    "percent%",
    "star*",
    "",
    "x" * (safety.MAX_TAG_KEY_LEN + 1),
    "\\Seen",
    "\\Flagged",
]


@pytest.mark.parametrize("key", sorted(safety.DANGEROUS_TAG_KEYS))
def test_refuses_dangerous_tag_even_if_listed(service, fake, key):
    with pytest.raises(SafetyError):
        service.add_tags(["m1"], [key])
    assert "update_tags" not in fake.calls


@pytest.mark.parametrize("key", BAD_KEYS)
def test_refuses_malformed_tag(service, fake, key):
    with pytest.raises(SafetyError):
        service.add_tags(["m1"], [key])
    assert "update_tags" not in fake.calls


def test_refuses_unknown_tag(service, fake):
    with pytest.raises(SafetyError):
        service.add_tags(["m1"], ["$label9"])
    assert "update_tags" not in fake.calls


def test_refuses_mixed_list_as_a_whole(service, fake):
    """1つでも危険なら全体を拒否する（部分的に適用しない）。"""
    dangerous = sorted(safety.DANGEROUS_TAG_KEYS)[0]
    with pytest.raises(SafetyError):
        service.add_tags(["m1"], ["$label1", dangerous])
    assert "update_tags" not in fake.calls
    assert fake.get_message("m1").summary.tags == ()


@pytest.mark.parametrize("key", sorted(safety.DANGEROUS_TAG_KEYS))
def test_remove_tags_is_checked_too(service, fake, key):
    with pytest.raises(SafetyError):
        service.remove_tags(["m1"], [key])
    assert "update_tags" not in fake.calls


def test_refuses_oversized_batch(service, fake):
    ids = [f"m{i}" for i in range(safety.MAX_MESSAGES_PER_WRITE + 1)]
    with pytest.raises(SafetyError):
        service.add_tags(ids, ["$label1"])


def test_allows_known_label(service, fake):
    result = service.add_tags(["m1"], ["$label1"])
    assert result["updated"] == ["m1"]
    assert "$label1" in fake.get_message("m1").summary.tags


def test_remove_known_label(service, fake):
    service.add_tags(["m1"], ["$label2"])
    service.remove_tags(["m1"], ["$label2"])
    assert "$label2" not in fake.get_message("m1").summary.tags