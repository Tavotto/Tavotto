"""缺依赖的三类去向（`install_route`）：可安装 / 装不了（映射不到 PyPI，如 CERN ROOT）/ 标准库缺了（Windows
内置 embeddable runtime 没有 tkinter）。后两类 pip 装不了，主文案不许再说「一键装上」，前端按 `install_route`
字段选引导而不是按文案判断。"""

from __future__ import annotations

from tavotto import app as app_module
from tavotto.engine import pool, prepsession


def _tb(mod: str) -> str:
    return f"Traceback…\nModuleNotFoundError: No module named '{mod}'\n"


# ---- 池层：只认得出标准库那一类 ------------------------------------------------
def test_stdlib_module_missing_is_its_own_route_and_never_promises_a_one_click_install():
    err = pool._worker_error("脚本执行失败", "script_error", _tb("tkinter"))
    assert err.code == "missing_dependency" and err.module == "tkinter"
    assert err.install_route == pool.INSTALL_ROUTE_STDLIB
    assert "一键装上" not in str(err)


def test_a_third_party_module_is_left_for_the_resolver_to_classify():
    err = pool._worker_error("脚本执行失败", "script_error", _tb("ROOT"))
    assert err.module == "ROOT"
    assert err.install_route == ""  # 池层不查可信解析；app 层 / 准备会话补上
    assert "一键装上" not in str(err)  # 池层不知道装不装得了，就不许替它许诺


def test_message_promises_install_only_for_nothing_but_the_neutral_default():
    for route in (pool.INSTALL_ROUTE_UNRESOLVABLE, pool.INSTALL_ROUTE_STDLIB):
        assert "一键" not in pool.missing_dependency_message("ROOT", route)
    assert "装不了 ROOT" in pool.missing_dependency_message("ROOT", pool.INSTALL_ROUTE_UNRESOLVABLE)


def test_the_python_pool_error_path_classifies_the_same_way():
    w = object.__new__(pool.EngineWorker)
    w.script_name = "fit.py"
    err = w._error_of(
        {"error": {"code": "script_error", "message": "x", "traceback": _tb("tkinter")}}
    )
    assert err.install_route == pool.INSTALL_ROUTE_STDLIB and "一键装上" not in str(err)


# ---- app 层：响应体带 install_route，装不了的主文案换掉 -----------------------
def _exc(mod: str):
    return pool._worker_error("脚本执行失败", "script_error", _tb(mod))


def _payload(monkeypatch, mod: str, repair):
    calls: list[str] = []

    def fake_offer(exc, project_env):
        calls.append(exc.module)
        return repair

    monkeypatch.setattr(app_module, "_dependency_repair_offer", fake_offer)
    monkeypatch.setattr(app_module, "_note_missing_dependency", lambda *a, **k: None)
    with app_module.app.test_request_context("/api/x"):
        return app_module._worker_error_payload(_exc(mod)), calls


def test_resolvable_module_stays_installable_and_keeps_the_original_text(monkeypatch):
    body, _ = _payload(monkeypatch, "scipy", {"requirement": {"distribution": "scipy"}})
    assert body["install_route"] == "installable"
    assert "dependency_repair" in body


def test_unmapped_module_is_unresolvable_and_says_so(monkeypatch):
    body, _ = _payload(monkeypatch, "ROOT", {"requirement": None, "targets": []})
    assert body["install_route"] == "unresolvable"
    assert "Tavotto 装不了 ROOT" in body["error"]
    assert "一键装上" not in body["error"]


def test_stdlib_module_gets_no_install_offer_at_all(monkeypatch):
    body, calls = _payload(monkeypatch, "tkinter", {"requirement": {"distribution": "tk"}})
    assert body["install_route"] == "stdlib_missing"
    assert "dependency_repair" not in body and calls == []
    assert "一键装上" not in body["error"]


# ---- 准备会话：差异计划的去向 --------------------------------------------------
def _observe(error: dict, root: str = "", script: str = "s.py"):
    from types import SimpleNamespace

    svc = object.__new__(prepsession.SessionService)

    class _Result:
        status = "error"

    r = _Result()
    r.error = error
    return svc._observe_missing(
        SimpleNamespace(project_root=root), SimpleNamespace(script=script), r
    )


def test_prep_stdlib_missing_short_circuits_the_resolver_and_is_public_as_stdlib():
    info = _observe(
        {"code": "missing_dependency", "module": "tkinter", "install_route": "stdlib_missing"}
    )
    assert info["installable"] is False
    public = prepsession.SessionService._public_missing(info)
    assert public["route"] == "stdlib_missing" and public["installable"] is False


def test_prep_unmapped_is_public_as_unresolvable():
    public = prepsession.SessionService._public_missing(
        {"module": "ROOT", "installable": False, "code": "dependency_unresolved"}
    )
    assert public["route"] == "unresolvable"


# ---- 素材库探测路径：与 Flask worker 错误同一个判据（Codex #859 P2）-------------
def test_probe_path_skips_offer_for_stdlib_even_when_project_declares_it(tmp_path, monkeypatch):
    from tavotto.engine import deprepair, probe

    (tmp_path / "requirements.txt").write_text("tkinter\n", encoding="utf-8")
    calls: list[str] = []
    real = deprepair.offer
    monkeypatch.setattr(deprepair, "offer", lambda *a, **k: calls.append(a[2]) or real(*a, **k))
    out = probe._error_from_worker(
        _exc("tkinter"), "fit.py", figures_dir=str(tmp_path), script="fit.py"
    )
    assert out["install_route"] == "stdlib_missing"
    assert "dependency_repair" not in out and calls == []


def test_probe_path_still_offers_for_a_third_party_module(tmp_path, monkeypatch):
    from tavotto.engine import deprepair, probe

    monkeypatch.setattr(
        deprepair, "offer", lambda *a, **k: {"requirement": {"distribution": "scipy"}}
    )
    out = probe._error_from_worker(
        _exc("scipy"), "fit.py", figures_dir=str(tmp_path), script="fit.py"
    )
    assert out["dependency_repair"]["requirement"]["distribution"] == "scipy"


def test_a_stdlib_name_declared_in_requirements_is_never_a_trusted_install_target(tmp_path):
    from tavotto.engine import deprepair, depresolve

    (tmp_path / "requirements.txt").write_text("tkinter\nscipy\n", encoding="utf-8")
    assert depresolve.resolve(str(tmp_path), "tkinter", "fit.py") is None
    assert depresolve.resolve(str(tmp_path), "scipy", "fit.py") is not None
    assert deprepair.offer(str(tmp_path), "fit.py", "tkinter")["requirement"] is None


# ---- tkinter 建议「把路径写进脚本」只在用法仅是文件对话框时才给 ----------------
def _tk_only(tmp_path, code: str) -> bool:
    from tavotto.engine import importscan

    p = tmp_path / "s.py"
    p.write_text(code, encoding="utf-8")
    return importscan.tk_file_dialog_only(p)


def test_file_dialog_only_usage_is_recognised(tmp_path):
    assert _tk_only(tmp_path, "from tkinter import filedialog\nf = filedialog.askopenfilename()\n")
    assert _tk_only(
        tmp_path,
        "import tkinter as tk\nfrom tkinter import filedialog\nr = tk.Tk()\nr.withdraw()\n"
        "p = filedialog.askopenfilename()\n",
    )


def test_tk_widgets_or_tkagg_or_unknown_never_claim_a_path_is_enough(tmp_path):
    assert not _tk_only(
        tmp_path, "import tkinter as tk\nr = tk.Tk()\ntk.Label(r).pack()\nr.mainloop()\n"
    )
    assert not _tk_only(tmp_path, "import tkinter as tk\nr = tk.Tk()\nr.mainloop()\n")
    assert not _tk_only(tmp_path, "from tkinter import *\n")
    assert not _tk_only(
        tmp_path, "import matplotlib\nmatplotlib.use('TkAgg')\nfrom tkinter import filedialog\n"
    )
    assert not _tk_only(tmp_path, "import numpy\n")  # 根本没 import tkinter
    assert not _tk_only(tmp_path, "def (:\n")  # 解析不了
    from tavotto.engine import importscan

    assert not importscan.tk_file_dialog_only(tmp_path / "nope.py")  # 读不了


def test_prep_payload_carries_tk_file_dialog_only_by_what_the_script_does(tmp_path):
    err = {"code": "missing_dependency", "module": "tkinter", "install_route": "stdlib_missing"}
    (tmp_path / "s.py").write_text("from tkinter import filedialog\n", encoding="utf-8")
    (tmp_path / "w.py").write_text("import tkinter as tk\ntk.Tk().mainloop()\n", encoding="utf-8")
    only = _observe(err, str(tmp_path), "s.py")
    widgets = _observe(err, str(tmp_path), "w.py")
    pub = prepsession.SessionService._public_missing
    assert pub(only)["tk_file_dialog_only"] is True
    assert pub(widgets)["tk_file_dialog_only"] is False


def test_bom_and_pep263_gbk_scripts_with_chinese_comments_are_still_recognised(tmp_path):
    from tavotto.engine import importscan

    body = "from tkinter import filedialog\n# 选择文件\nf = filedialog.askopenfilename()\n"
    bom = tmp_path / "bom.py"
    bom.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    gbk = tmp_path / "gbk.py"
    gbk.write_bytes(("# coding: gbk\n" + body).encode("gbk"))
    assert importscan.tk_file_dialog_only(bom) and importscan.tk_file_dialog_only(gbk)
