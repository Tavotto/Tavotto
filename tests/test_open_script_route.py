"""Compatibility Bridge Session 6：`tavotto open script.py` 的产品路由。

四件事的看护：

* **显式给出 .py = 运行意图**：静态发现失败时安全 probe（本地或委托给
  已运行实例），成功后交接 RuntimeFigureAsset；`--no-probe` 关掉这一步。
* **多 Figure 不静默选第一张**：`--stem` 显式选；有界面就把选择信息
  （`pick`）交给 Figure 选择器；`--no-launch` 的机器调用必须显式选
  （`multiple_figures_found`）。
* **稳定错误码**：script_no_figure / script_probe_failed /
  multiple_figures_found / invalid_stem / runtime_asset_failed /
  native_run_required / probe_in_progress——文案随时可改，code 不行。
* **执行次数纪律（负向反证 #6）**：CLI probe 执行一次；交接后目标进程
  读注册表 + materialized cache，**绝不重复执行脚本**；CLI 退出前
  worker 会话清零（不留 orphan）。

真执行的用例与 test_script_probe 同一条纪律：本进程不 import matplotlib。
"""
import json
import os
from pathlib import Path

import pytest

from tavotto.engine import handoff
from tavotto.engine import discover as engine_discover
from tavotto.engine import pool as engine_pool
from tavotto.engine import registry as engine_registry

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）")


SHOW_ONLY = '''\
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.title("AI generated")
plt.show()
'''


def _project(tmp_path, scripts: dict[str, str]) -> Path:
    figs = tmp_path / "figs"
    figs.mkdir()
    for name, src in scripts.items():
        p = figs / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {}}), encoding="utf-8")
    return figs


def _register(figs: Path, script: str, stems: list[str], entry="main"):
    engine_discover.register(figs, script, stems, entry=entry)


NO_REMOTE = lambda *a, **k: None                                  # noqa: E731


def _fake_local(figs: Path, stems: list[str], *, register=True, error=None,
                calls=None):
    """一个假的本地 probe：按需登记 stems，记录调用次数。"""
    def run(project, script):
        if calls is not None:
            calls.append(script)
        if register and stems:
            _register(Path(project), script, stems, entry="__main__")
        return {"script": script, "entry": "__main__" if stems else None,
                "stems": list(stems), "descriptors": [], "tried": ["__main__"],
                "error": error, "timings": {}, "dropped_figures": 0,
                "registered": bool(register and stems)}
    return run


# --------------------------- 路由复用与探测 -------------------------------
def test_existing_artifact_route_skips_probe(tmp_path):
    """磁盘原件在 = 路由已有效，绝不为交接多跑一次脚本。"""
    figs = _project(tmp_path, {"plot.py": "print('x')"})
    _register(figs, "plot.py", ["Fig1"])
    (figs / "Fig1.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    target, info = handoff.resolve_script_route(
        str(figs), "plot.py",
        probe_remote=lambda *a, **k: pytest.fail("不该探测"),
        probe_local=lambda *a, **k: pytest.fail("不该探测"))
    assert target == handoff.Target(str(figs), "Fig1")
    assert info["performed"] is False
    assert info["figures"][0]["artifact"] == "Fig1.pdf"


def test_show_only_script_probes_and_lands_on_runtime_asset(tmp_path):
    calls: list = []
    figs = _project(tmp_path, {"show.py": SHOW_ONLY})
    target, info = handoff.resolve_script_route(
        str(figs), "show.py", probe_remote=NO_REMOTE,
        probe_local=_fake_local(figs, ["show"], calls=calls))
    assert calls == ["show.py"]
    assert target.stem == "show"
    assert info["performed"] is True and info["via"] == "local"
    fig = info["figures"][0]
    assert fig["artifact"] is None
    assert fig["asset_id"] == "runtime:show.py#show"


def test_multi_figure_hands_pick_to_ui_not_first_stem(tmp_path):
    """多 Figure：不静默选第一张——stem=None + pick=脚本，交给选择器。"""
    figs = _project(tmp_path, {"multi.py": "pass"})
    target, info = handoff.resolve_script_route(
        str(figs), "multi.py", probe_remote=NO_REMOTE,
        probe_local=_fake_local(figs, ["FigA", "FigB"]))
    assert target.stem is None
    assert target.pick == "multi.py"
    assert [f["stem"] for f in info["figures"]] == ["FigA", "FigB"]


def test_stem_flag_selects_explicitly(tmp_path):
    figs = _project(tmp_path, {"multi.py": "pass"})
    target, _ = handoff.resolve_script_route(
        str(figs), "multi.py", stem_arg="FigB", probe_remote=NO_REMOTE,
        probe_local=_fake_local(figs, ["FigA", "FigB"]))
    assert target.stem == "FigB" and target.pick is None


def test_unknown_stem_is_invalid_stem(tmp_path):
    figs = _project(tmp_path, {"multi.py": "pass"})
    with pytest.raises(handoff.HandoffError) as ei:
        handoff.resolve_script_route(
            str(figs), "multi.py", stem_arg="Nope", probe_remote=NO_REMOTE,
            probe_local=_fake_local(figs, ["FigA", "FigB"]))
    assert ei.value.code == "invalid_stem"
    assert ei.value.extra["stems"] == ["FigA", "FigB"]


def test_no_probe_with_nothing_registered_says_script_no_figure(tmp_path):
    figs = _project(tmp_path, {"show.py": SHOW_ONLY})
    with pytest.raises(handoff.HandoffError) as ei:
        handoff.resolve_script_route(
            str(figs), "show.py", no_probe=True,
            probe_remote=lambda *a, **k: pytest.fail("--no-probe 下不许探测"),
            probe_local=lambda *a, **k: pytest.fail("--no-probe 下不许探测"))
    assert ei.value.code == "script_no_figure"


# ------------------------------ 错误映射 ---------------------------------
@pytest.mark.parametrize("probe_code,cli_code", [
    ("script_no_figure", "script_no_figure"),
    ("script_probe_failed", "script_probe_failed"),
    ("execution_timeout", "execution_timeout"),
    ("missing_dependency", "native_run_required"),
])
def test_probe_failures_map_to_stable_codes(tmp_path, probe_code, cli_code):
    figs = _project(tmp_path, {"bad.py": "raise SystemExit(1)"})
    err = {"code": probe_code, "message": "boom",
           "params": {"module": "scipy"} if probe_code == "missing_dependency"
           else {}}
    with pytest.raises(handoff.HandoffError) as ei:
        handoff.resolve_script_route(
            str(figs), "bad.py", probe_remote=NO_REMOTE,
            probe_local=_fake_local(figs, [], error=err))
    assert ei.value.code == cli_code
    if cli_code == "native_run_required":
        # 原始 code 与缺的包保留在 extra——调用方分诊要用
        assert ei.value.extra["probe_code"] == "missing_dependency"
        assert ei.value.extra["module"] == "scipy"


def test_captured_but_unregistered_is_runtime_asset_failed(tmp_path):
    figs = _project(tmp_path, {"s.py": "pass"})
    with pytest.raises(handoff.HandoffError) as ei:
        handoff.resolve_script_route(
            str(figs), "s.py", probe_remote=NO_REMOTE,
            probe_local=_fake_local(figs, ["Fig1"], register=False))
    assert ei.value.code == "runtime_asset_failed"


def test_stem_conflict_keeps_its_own_code(tmp_path):
    figs = _project(tmp_path, {"s.py": "pass"})
    err = {"code": "multiple_stem_conflict", "message": "冲突", "params": {}}

    def local(project, script):
        return {"script": script, "entry": "__main__", "stems": ["Fig1"],
                "descriptors": [], "tried": ["__main__"], "error": err,
                "timings": {}, "dropped_figures": 0, "registered": False}

    with pytest.raises(handoff.HandoffError) as ei:
        handoff.resolve_script_route(str(figs), "s.py",
                                     probe_remote=NO_REMOTE, probe_local=local)
    assert ei.value.code == "multiple_stem_conflict"


def test_remote_409_maps_to_probe_in_progress():
    """素材库已经在跑同一个脚本：如实报 409，绝不再起第二次执行。"""
    def http_status(url, payload=None, timeout=1.0):
        if url.endswith("/api/version"):
            return 200, {"version": "1.0"}
        if url.endswith("/api/projects/open"):
            return 200, {"id": "pj1"}
        return 409, {"code": "probe_in_progress", "params": {"script": "s.py"}}

    with pytest.raises(handoff.HandoffError) as ei:
        handoff._remote_probe(5089, "/p", "s.py", http_status=http_status)
    assert ei.value.code == "probe_in_progress"
    assert ei.value.extra["retryable"] is True


def test_remote_probe_returns_none_without_instance():
    assert handoff._remote_probe(
        5089, "/p", "s.py", http_status=lambda *a, **k: (None, None)) is None


# --------------------------- 交接契约（pick） -----------------------------
def test_desktop_argv_carries_pick_for_multi_figure():
    """与 src-tauri/src/main.rs 的 parse_open_args 同源（--pick-script）。"""
    argv = handoff.desktop_argv(
        "/A/Tavotto", handoff.Target("/p", None, pick="sub/plot.py"))
    assert argv == ["/A/Tavotto", "--open", "/p", "--pick-script", "sub/plot.py"]
    # stem 定了就没有选择器（互斥）
    argv = handoff.desktop_argv(
        "/A/Tavotto", handoff.Target("/p", "Fig1", pick="plot.py"))
    assert "--pick-script" not in argv


def test_browser_url_carries_pick():
    url = handoff.browser_url(5089, handoff.Target("/p", None, pick="子目录/图.py"))
    assert url == ("http://127.0.0.1:5089/?pick="
                   "%E5%AD%90%E7%9B%AE%E5%BD%95%2F%E5%9B%BE.py")


def test_macos_open_argv_reuses_the_contract(monkeypatch):
    """`open -na … --args` 之后的形状必须与 desktop_argv 完全一致（唯一生产者）。"""
    seen = {}

    def run(argv, **kw):
        seen["argv"] = argv

        class R:
            returncode = 0
            stdout = stderr = ""
        return R()

    out = handoff._launch_desktop_via_open(
        "/A/Tavotto.app/Contents/MacOS/Tavotto", "/A/Tavotto.app",
        handoff.Target("/p", None, pick="plot.py"),
        run=run, pids_of=lambda exe: None, clock=lambda: 0.0,
        sleep=lambda s: None)
    assert seen["argv"][:4] == ["open", "-na", "/A/Tavotto.app", "--args"]
    assert seen["argv"][4:] == ["--open", "/p", "--pick-script", "plot.py"]
    assert out["ready"] == "unverified"


# ------------------------------ open_target ------------------------------
def test_open_target_multi_without_ui_requires_explicit_choice(tmp_path, monkeypatch):
    figs = _project(tmp_path, {"multi.py": "pass"})
    monkeypatch.setattr(handoff, "_remote_probe", NO_REMOTE)
    monkeypatch.setattr(handoff, "_local_probe",
                        _fake_local(figs, ["FigA", "FigB"]))
    with pytest.raises(handoff.HandoffError) as ei:
        handoff.open_target(str(figs / "multi.py"), launch_ui=False)
    assert ei.value.code == "multiple_figures_found"
    assert [f["stem"] for f in ei.value.extra["figures"]] == ["FigA", "FigB"]


def test_open_target_multi_launches_the_picker(tmp_path, monkeypatch):
    figs = _project(tmp_path, {"multi.py": "pass"})
    monkeypatch.setattr(handoff, "_remote_probe", NO_REMOTE)
    monkeypatch.setattr(handoff, "_local_probe",
                        _fake_local(figs, ["FigA", "FigB"]))
    seen = []
    result = handoff.open_target(
        str(figs / "multi.py"), prefer="browser",
        http=lambda *a, **k: None, spawn=lambda argv, **kw: seen.append(argv),
        browse=lambda url: pytest.fail("新进程自己开浏览器"))
    assert result["ok"] is True
    assert result["pick"] == "multi.py"
    assert result["stem"] is None
    assert [f["stem"] for f in result["figures"]] == ["FigA", "FigB"]
    assert result["launch"]["url"].endswith("?pick=multi.py")


def test_open_target_single_probe_lands_on_stem(tmp_path, monkeypatch):
    figs = _project(tmp_path, {"show.py": SHOW_ONLY})
    monkeypatch.setattr(handoff, "_remote_probe", NO_REMOTE)
    monkeypatch.setattr(handoff, "_local_probe", _fake_local(figs, ["show"]))
    result = handoff.open_target(str(figs / "show.py"), launch_ui=False)
    assert result["stem"] == "show"
    assert result["probe"]["performed"] is True
    assert result["figures"][0]["asset_id"] == "runtime:show.py#show"
    # 探测登记后 stem 是可参数化的
    assert result["registry"]["parameterizable"] is True


def test_stem_flag_rejected_for_non_script_targets(tmp_path):
    d = tmp_path / "figs"
    d.mkdir()
    with pytest.raises(handoff.HandoffError) as ei:
        handoff.open_target(str(d), launch_ui=False, stem="Fig1")
    assert ei.value.code == "invalid_stem"


def test_cli_passes_probe_flags(tmp_path, monkeypatch, capsys):
    figs = _project(tmp_path, {"show.py": SHOW_ONLY})
    monkeypatch.setattr(handoff, "_remote_probe", NO_REMOTE)
    monkeypatch.setattr(handoff, "_local_probe", _fake_local(figs, ["show"]))
    code = handoff.cli([str(figs / "show.py"), "--json", "--no-launch"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["stem"] == "show"
    assert data["probe"]["via"] == "local"

    # --no-probe：同一个脚本、没有登记 → 稳定错误一行 JSON
    (figs / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {}}), encoding="utf-8")
    code = handoff.cli([str(figs / "show.py"), "--json", "--no-launch",
                        "--no-probe"])
    assert code == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["code"] == "script_no_figure"


# --------------------- 真执行：一次且只有一次（反证 #6） -------------------
# 执行计数哨兵：脚本每跑一次就往**项目外的绝对路径**追加一行（safe 档隔离
# 的是项目写入与相对路径，项目外的记录文件如实落盘）。数这份文件的行数 =
# 数真实执行次数——比「池里有没有 worker」强：close_project 会把变异产生
# 的 worker 收走，行数收不走。
COUNTED = '''\
import pathlib
import matplotlib.pyplot as plt

pathlib.Path(r"{log}").open("a").write("run\\n")
plt.plot([1, 2, 3], [4, 5, 6])
plt.title("counted")
plt.show()
'''


@needs_worker
def test_cli_probe_executes_once_and_handoff_never_reruns(tmp_path, monkeypatch):
    """CLI E2E：safe probe 执行一次 → 注册表 + materialized cache 落盘 →
    目标进程（Flask app）看到 runtime asset 与预览，全程零再执行、零 orphan。

    执行次数由项目外的哨兵文件行数钉死（见 COUNTED 注释）；另附两道侧写：
    materialized cache 的 mtime 在只读浏览后不变、CLI 侧池清零。
    """
    run_log = tmp_path / "exec-count.log"
    figs = _project(tmp_path,
                    {"counted.py": COUNTED.format(log=run_log)})

    # 本机可能有别的 Tavotto 实例在 5089 端口上跑：测试必须走本地 probe，
    # 绝不把探测委托给测试环境之外的进程
    monkeypatch.setattr(handoff, "_remote_probe", NO_REMOTE)
    result = handoff.open_target(str(figs / "counted.py"), launch_ui=False)
    assert result["stem"] == "counted"
    assert result["probe"]["performed"] is True and result["probe"]["via"] == "local"
    # 执行次数 = 1（probe 那一次），safe 档也没把 runs.log 写进项目目录
    assert run_log.read_text().count("run") == 1
    assert not (figs / "runs.log").exists()
    # CLI 侧不留 orphan worker：本进程的池必须是空的
    assert not engine_pool._workers

    fig = result["figures"][0]
    assert fig["asset_id"] == "runtime:counted.py#counted"
    assert fig["cached"] is True, "probe 成功必须物化 cache（交接零重跑的前提）"

    from tavotto.engine import runtimeasset
    cache_dir = runtimeasset.cache_dir(str(figs), fig["asset_id"])
    meta_before = (cache_dir / "metadata.json").stat().st_mtime_ns

    # 目标进程视角：打开项目 → 列 runtime 素材 → 取预览，全程只读
    from tavotto import app as m
    client = m.app.test_client()
    r = client.post("/api/projects/open", json={"path": str(figs)})
    assert r.status_code == 200
    pj = r.get_json()["id"]
    try:
        r = client.get(f"/api/runtime/assets?pj={pj}")
        assert r.status_code == 200
        assets = r.get_json()["assets"]
        assert [a["id"] for a in assets] == ["runtime:counted.py#counted"]
        assert assets[0]["cached"] is True
        assert assets[0]["descriptor"] is not None
        from urllib.parse import quote
        r = client.get(f"/api/runtime/preview?pj={pj}&id={quote(fig['asset_id'], safe='')}")
        assert r.status_code == 200 and b"svg" in r.data[:300]
    finally:
        m.close_project(pj)
    # 交接与浏览没有触发任何一次重新执行/重物化（哨兵行数仍是 1——
    # close_project 收得走 worker，收不走已经发生的执行记录）
    assert run_log.read_text().count("run") == 1
    assert (cache_dir / "metadata.json").stat().st_mtime_ns == meta_before
    assert not engine_pool._workers
