"""scripts/ を、実行せずに（読み取りと構文解析だけで）確かめる。

レジストリ、Thunderbird、ホストの起動、実際の鍵には触れない。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NATIVE_HOST_DIR = ROOT / "scripts" / "native-host"
REGISTER = ROOT / "scripts" / "register_native_host.ps1"
LAUNCHER = NATIVE_HOST_DIR / "thunderbird_mcp_host.cmd"
HOST_JSON = NATIVE_HOST_DIR / "thunderbird_mcp_host.json"


def test_host_manifest_template():
    host = json.loads(HOST_JSON.read_text(encoding="utf-8"))
    assert host["name"] == "thunderbird_mcp_host"
    assert host["type"] == "stdio"
    assert (NATIVE_HOST_DIR / host["path"]).is_file()
    assert len(host["allowed_extensions"]) == 1


def test_launcher_runs_the_native_host_module_from_the_project_venv():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert r"\.venv\Scripts\python.exe" in text
    assert "-m thunderbird_mcp.native_host" in text
    assert "1>&2" in text  # 診断は stderr へ


def test_registration_script_is_dry_runnable_and_reversible():
    text = REGISTER.read_text(encoding="utf-8")
    assert "SupportsShouldProcess" in text
    assert "[switch]$Unregister" in text
    assert r"HKCU:\Software\Mozilla\NativeMessagingHosts" in text
    assert "UTF8Encoding($false)" in text  # BOM なし


def test_registration_script_only_touches_the_current_user_and_starts_nothing():
    text = REGISTER.read_text(encoding="utf-8").lower()
    for forbidden in ("hklm", "start-process", "thunderbird.exe", "invoke-expression", "iex ", "reg add", "reg.exe"):
        assert forbidden not in text, forbidden


def test_registration_script_does_not_read_the_ipc_key():
    text = REGISTER.read_text(encoding="utf-8").lower()
    assert "ipc.key" not in text


def test_registration_script_parses():
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell is not available")
    command = (
        "$e=$null;$t=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile($env:PS1_PATH,[ref]$t,[ref]$e)|Out-Null;"
        "if($e.Count -gt 0){$e|ForEach-Object{$_.Message};exit 1}"
    )
    done = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        env={**os.environ, "PS1_PATH": str(REGISTER)}, capture_output=True, text=True, timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_script_file_is_non_empty_and_free_of_secrets():
    for path in (REGISTER, LAUNCHER, HOST_JSON):
        text = path.read_text(encoding="utf-8")
        assert text.strip()
        assert not re.search(r"(?i)password|secret|token", text)
