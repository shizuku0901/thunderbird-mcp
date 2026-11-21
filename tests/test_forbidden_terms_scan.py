"""禁止語を書いてよいのは src/thunderbird_mcp/safety.py だけ、を静的に確かめる。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAFETY_FILE = ROOT / "src" / "thunderbird_mcp" / "safety.py"
SCANNED_SUFFIXES = frozenset({".py", ".pyi", ".js", ".mjs", ".cjs", ".json", ".toml", ".ps1", ".psm1", ".cmd", ".bat"})
EXCLUDED_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
})
EXCLUDED_FILES = frozenset({
    SAFETY_FILE.resolve(),
    (ROOT / ".claude" / "settings.local.json").resolve(),
})
# 最低限の禁止語。このファイル自身が禁止語を含まないように逆順で書く。
MIN_TERMS_REVERSED = ("eteled", "dnes", "egnupxe", "hsart", "knuj", "maps", "xobtuo", "ptms", "egrup")
DYNAMIC_IMPORT_MARKERS = ("__import__(", "import_module(")


def _scanned_files():
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        if path.resolve() in EXCLUDED_FILES:
            continue
        yield path


def _terms() -> tuple[str, ...]:
    from thunderbird_mcp import safety

    return tuple(term.lower() for term in safety.FORBIDDEN_TERMS)


def test_safety_file_exists():
    assert SAFETY_FILE.is_file(), "src/thunderbird_mcp/safety.py がまだない（Red）"


def test_forbidden_terms_cover_minimum():
    terms = set(_terms())
    missing = [r for r in MIN_TERMS_REVERSED if r[::-1] not in terms]
    assert not missing, f"FORBIDDEN_TERMS に最低限の語が {len(missing)} 個足りない"


def test_forbidden_terms_is_literal_tuple():
    tree = ast.parse(SAFETY_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "FORBIDDEN_TERMS" for t in node.targets
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, tuple)
            assert all(isinstance(v, str) and v == v.lower() and v for v in value)
            return
    pytest.fail("FORBIDDEN_TERMS は小文字のリテラルのタプルで定義する（hooks が ast で読むため）")


def test_only_safety_py_contains_forbidden_terms():
    terms = _terms()
    violations = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for term in terms:
            if term in text:
                violations.append(f"{path.relative_to(ROOT)}: {term}")
    assert not violations, "safety.py 以外に禁止語がある:\n" + "\n".join(violations)


def test_no_forbidden_or_dynamic_imports_in_src():
    from thunderbird_mcp import safety

    violations = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for marker in DYNAMIC_IMPORT_MARKERS:
            if marker in text:
                violations.append(f"{path.relative_to(ROOT)}: 動的 import ({marker})")
        for node in ast.walk(ast.parse(text)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in safety.FORBIDDEN_IMPORTS:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: import {name}")
    assert not violations, "\n".join(violations)