"""`scripts/verify_managed_cli.py`：先建 `cli/<名>/` 挡住 MSIX 搬迁，并且必须真的下载过才算通过。不联网。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from tavotto.engine import managedcli as mc


def _load():
    script = Path(__file__).resolve().parent.parent / "scripts" / "verify_managed_cli.py"
    spec = importlib.util.spec_from_file_location("verify_managed_cli_script", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_download_is_not_ok_and_cli_dir_precreated(tmp_path, monkeypatch):
    cli = mc.cli_for("codex")
    if cli is None:
        return  # 这个平台没有托管版本，脚本本身就回 unsupported_platform
    key = mc.load_lock()["clis"]["codex"]["sources"][0]["key"]
    mod = _load()
    seen: dict = {}

    def fake_provision(name, *, cli=None, **kw):
        d = mc.config.data_dir() / mc.DIRNAME / name
        seen["dir_exists"] = d.is_dir()
        return str(tmp_path / "nowhere" / "codex")  # 不调 _download：模拟被搬迁/已装

    def boom(*a, **kw):
        raise AssertionError("不该真下载")

    monkeypatch.setattr(mc, "provision", fake_provision)
    monkeypatch.setattr(mc, "_download", boom)
    res = mod.verify_one("codex", key, tmp_path)
    assert seen["dir_exists"] is True
    assert res["ok"] is False
    assert res["error"] == "no_download"
    assert res["downloads"] == 0
    assert mc._download is boom  # 计数包装已还原
