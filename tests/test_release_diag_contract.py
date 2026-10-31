"""发布编排的诊断包条目门禁（`scripts/ci/diag_contract.py`）：客户端会写的条目，线上诊断服务都认识。

判据的主语：待发布 SHA 的 `diagsend.ENTRY_KINDS` 键 × 线上契约 `known_bundle_files`；门禁结论看退出码。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ci" / "diag_contract.py"
DIAGSEND = ROOT / "src" / "tavotto" / "engine" / "diagsend.py"

sys.path.insert(0, str(SCRIPT.parent))
import diag_contract as DC  # noqa: E402
from tavotto.engine import diagsend  # noqa: E402


def _contract(files) -> str:
    return json.dumps({"api_version": "1", "known_bundle_files": sorted(files)})


def _run(tmp_path, contract: str | None, *, ack: str = "", event: str = "workflow_dispatch"):
    path = tmp_path / "contract.json"
    if contract is not None:
        path.write_text(contract, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--client", str(DIAGSEND), "--contract", str(path)]
        + ["--ack", ack, "--event", event],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_the_client_facts_are_read_from_source_without_importing_it():
    names, url = DC.client_facts(DIAGSEND.read_text(encoding="utf-8"))
    assert names == set(diagsend.ENTRY_KINDS)
    assert url == diagsend.PRODUCTION_ENDPOINT.rstrip("/") + "/v1/reports/contract"


def test_matching_tables_pass_and_a_server_that_knows_more_is_fine(tmp_path):
    full = set(diagsend.ENTRY_KINDS)
    assert _run(tmp_path, _contract(full)).returncode == 0
    assert _run(tmp_path, _contract(full | {"legacy-file.json"})).returncode == 0


def test_an_entry_the_server_does_not_know_stops_the_release_and_is_named(tmp_path):
    dropped = sorted(diagsend.ENTRY_KINDS)[0]
    res = _run(tmp_path, _contract(set(diagsend.ENTRY_KINDS) - {dropped}))
    assert res.returncode == 1
    assert dropped in res.stdout


@pytest.mark.parametrize(
    "contract",
    [None, "", "<html>404</html>", json.dumps({"known_bundle_files": []}), json.dumps([1, 2])],
)
def test_an_unreachable_or_unreadable_contract_fails_closed(tmp_path, contract):
    assert _run(tmp_path, contract).returncode == 1


def test_a_written_reason_signs_off_a_mismatch_and_a_stale_ack_is_refused(tmp_path):
    dropped = sorted(diagsend.ENTRY_KINDS)[0]
    partial = _contract(set(diagsend.ENTRY_KINDS) - {dropped})
    signed = _run(tmp_path, partial, ack="infra 部署中，已知")
    assert signed.returncode == 0 and "已签字放行" in signed.stdout
    assert _run(tmp_path, None, ack="服务宕机").returncode == 0
    # 两侧一致时留着签字 = 陈旧签字，下次真漂移会被误当成放行
    assert _run(tmp_path, _contract(set(diagsend.ENTRY_KINDS)), ack="old").returncode == 1


def test_a_tag_push_explains_how_to_sign(tmp_path):
    res = _run(tmp_path, None, event="push")
    assert res.returncode == 1 and "ack_diag_contract" in res.stdout
