"""extension/background.js が、固定の op 表と J-1〜J-7 を守ることを確かめる。

JS は node で、偽の messenger に対して動かす（Thunderbird には触れない）。node がなければ skip する。
禁止語はここに書かず、safety の定数から組み立てて harness に渡す。
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from thunderbird_mcp import ipc, safety

ROOT = Path(__file__).resolve().parents[1]
BACKGROUND = ROOT / "extension" / "background.js"
HARNESS = ROOT / "tests" / "extension_harness.mjs"
NODE = shutil.which("node")

DRAFT_BODY = "本文" * (400 * 1024)  # 768 KiB を超える


def _chunked_request():
    request = {
        "id": 5,
        "op": "drafts.create",
        "args": {
            "to": ["alice@example.com"], "cc": [], "bcc": [],
            "subject": "Big", "body": DRAFT_BODY, "identity_id": None,
        },
    }
    return ipc.split_message(request, ipc.NM_MAX_MESSAGE_BYTES, ipc.NM_CHUNK_RAW_BYTES)


def test_op_table_in_js_matches_the_python_table():
    source = BACKGROUND.read_text(encoding="utf-8")
    defined = re.findall(r'defineOp\("([^"]+)"', source)
    assert len(defined) == len(set(defined))
    assert set(defined) == set(ipc.ALLOWED_OPS)


def test_js_uses_the_expected_native_host_name():
    # scripts/native-host/thunderbird_mcp_host.json の "name" と同じ値（整合は別のテストで確かめる）
    assert 'const HOST_NAME = "thunderbird_mcp_host";' in BACKGROUND.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def harness_output():
    if NODE is None:
        pytest.skip("node is not installed")
    payload = {
        "backgroundPath": str(BACKGROUND),
        "forbiddenOps": [f"{area}.{term}" for area in ("messages", "compose", "folders", "accounts")
                         for term in safety.FORBIDDEN_TERMS],
        "dollarKeys": sorted(key for key in safety.DANGEROUS_TAG_KEYS if key.startswith("$")),
        "chunkedDraftRequest": _chunked_request(),
        "chunkedDraftBodyLength": len(DRAFT_BODY),
    }
    done = subprocess.run(
        [NODE, str(HARNESS)], input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_extension_rules_hold(harness_output):
    scenarios = harness_output["scenarios"]
    assert len(scenarios) > 100, "the harness ran far fewer scenarios than expected"
    failed = [f"{s['name']}: {s['detail']}" for s in scenarios if not s["ok"]]
    assert not failed, "\n".join(failed)


def test_response_parts_from_js_reassemble_in_python(harness_output):
    parts = harness_output["extra"]["outgoingParts"]
    reassembler = ipc.Reassembler()
    message = None
    for part in parts:
        message = reassembler.feed(part) or message
    assert message["id"] == 41 and message["ok"] is True
    size = 3 * 1024 * 1024
    assert base64.b64decode(message["result"]["data_b64"]) == bytes(i % 251 for i in range(size))
