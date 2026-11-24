"""release workflow が、承認された形（main への push・重複防止・テスト後に公開）であることを確かめる。"""
from __future__ import annotations

from pathlib import Path

from thunderbird_mcp import safety

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists():
    assert WORKFLOW.is_file(), ".github/workflows/release.yml がまだない（Red）"


def test_runs_only_on_push_to_main():
    text = _text()
    assert "push:" in text and "branches: [main]" in text
    assert "pull_request" not in text  # フォークの PR から Release を作らない


def test_least_privilege_and_serialised():
    text = _text()
    assert "contents: write" in text
    assert "concurrency:" in text and "cancel-in-progress: false" in text


def test_skips_when_release_already_exists():
    assert "gh release view" in _text()


def test_tests_run_before_release_is_created():
    text = _text()
    assert text.index("uv run pytest") < text.index("gh release create")
    assert text.index("build_xpi.py") < text.index("gh release create")
    assert text.index("make_update_manifest.py") < text.index("gh release create")


def test_release_attaches_xpi_and_update_manifest_and_creates_tag():
    text = _text()
    assert "dist/*.xpi" in text and "dist/updates.json" in text
    assert "--target" in text  # gh release create がタグも作る


def test_no_forbidden_terms():
    text = _text().lower()
    assert not [t for t in safety.FORBIDDEN_TERMS if t in text]
