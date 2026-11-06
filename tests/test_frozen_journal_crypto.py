"""冻结期刊验签必须真加载 PyNaCl/CFFI，并拒绝篡改；不碰网络或用户缓存。"""

from __future__ import annotations

import ast
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_journal_selftest_accepts_rfc_vector(capsys):
    entry = runpy.run_path(str(ROOT / "packaging" / "entry.py"))
    entry["_journal_selftest"]()
    assert "journal Ed25519 selftest passed" in capsys.readouterr().out


def test_frozen_journal_selftest_fails_when_crypto_backend_is_missing():
    import subprocess
    import sys

    # 独立进程避免原生扩展缓存绕过 import；MetaPathFinder 也能拦住 C 层导入。
    script = """
import importlib.abc
import runpy
import sys
class MissingBackend(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == '_cffi_backend':
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, MissingBackend())
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[1] / 'src'))
runpy.run_path(sys.argv[1])['_journal_selftest']()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT / "packaging" / "entry.py")],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "Ed25519 selftest failed" in result.stderr


def test_frozen_journal_selftest_reads_a_signed_catalog(tmp_path, monkeypatch, capsys):
    import json

    from journal_catalog_signing import TEST_SEED, b64, build_site, public_key
    from tavotto.engine import journalremote

    catalog = json.loads((ROOT / "tests/golden/journal_vectors.json").read_text())["catalog"]
    monkeypatch.setattr(journalremote, "TRUSTED_KEYS", {"test-1": b64(public_key(TEST_SEED))})
    index = tmp_path / "index.signed.json"
    index.write_bytes(build_site(catalog, rev=5)["index.signed.json"])
    entry = runpy.run_path(str(ROOT / "packaging" / "entry.py"))
    entry["_journal_selftest"](str(index))
    expected_journals = set(catalog["journals"]) | set(journalremote.seed_catalog()["journals"])
    assert f"rev=5 journals={len(expected_journals)}" in capsys.readouterr().out


def test_frozen_journal_selftest_detects_a_verifier_that_accepts_tampering(monkeypatch):
    from tavotto.engine import journalremote

    monkeypatch.setattr(journalremote, "_ed25519_verify", lambda *args: True)
    entry = runpy.run_path(str(ROOT / "packaging" / "entry.py"))
    with pytest.raises(RuntimeError, match="accepted a tampered message"):
        entry["_journal_selftest"]()


def test_frozen_journal_selftest_rejects_an_invalid_catalog(tmp_path):
    entry = runpy.run_path(str(ROOT / "packaging" / "entry.py"))
    index = tmp_path / "index.signed.json"
    index.write_text('{"payload":"invalid","sig":"invalid"}', encoding="utf-8")
    with pytest.raises(Exception, match="签名|base64"):
        entry["_journal_selftest"](str(index))


def test_spec_declares_the_cffi_dependency_outside_the_nacl_package():
    tree = ast.parse((ROOT / "packaging" / "tavotto.spec").read_text(encoding="utf-8"))
    assignment = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "NACL_MODULES" for t in n.targets)
        and isinstance(n.value, ast.List)
    )
    assert any(
        isinstance(n, ast.Constant) and n.value == "_cffi_backend" for n in assignment.value.elts
    )
