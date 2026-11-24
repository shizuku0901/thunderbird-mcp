"""ci workflow が、承認された形（PR と main への push で pytest を動かし、権限は読み取りだけ）であることを確かめる。"""
from __future__ import annotations

from pathlib import Path

from thunderbird_mcp import safety

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists():
    assert WORKFLOW.is_file(), ".github/workflows/ci.yml がまだない（Red）"


def test_runs_on_pull_request_and_push_to_main():
    text = _text()
    assert "pull_request:" in text
    assert "push:" in text
    assert text.count("branches: [main]") == 2


def test_never_uses_pull_request_target():
    # フォークの PR のコードを、書き込み権限や秘密のある文脈で動かさない
    assert "pull_request_target" not in _text()


def test_read_only_permissions():
    text = _text()
    assert "contents: read" in text
    assert "write" not in text


def test_runs_pytest_with_locked_dependencies():
    text = _text()
    assert "uv sync --locked" in text
    assert "uv run pytest" in text
    assert text.index("uv sync --locked") < text.index("uv run pytest")


def test_node_is_installed_so_js_tests_are_not_skipped():
    assert "actions/setup-node" in _text()


def test_no_forbidden_terms():
    text = _text().lower()
    assert not [t for t in safety.FORBIDDEN_TERMS if t in text]
