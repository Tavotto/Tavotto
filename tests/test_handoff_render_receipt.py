"""交接三态（ADR 0131 第六节）：`launch.render` 与插件的 launch / render / verified 三个字段。

`ok` 只说「解析、登记、唤起成功」，不说图画出来了（#961：交接返回 ok:true，sidecar 却因缺
引擎文件渲染失败）。这里钉的是：

* 确认信号（`engine/handoffreceipt.py` 的回执文件）的认证面：只认本人的、命名合规的、未过期的、
  尚无结论的普通小文件；结论一次性；
* `render` 三值的**每一条路径**——只有实例明确报错才是 failed，查不到 / 超时 / 没有目标 stem
  / 通道不可用一律 unverified；
* `verified` 的判据（stem 一致 ∧ 注册表有 ∧ manifest 非空）逐条变异；
* `/api/engine/render` 的真实收尾处确实写回执（成功与 WorkerError 两条）；
* 插件顶层三字段，含旧 CLI（没有 `launch.render`）与新 CLI 的兼容矩阵；
* #961 形状：`launch_success=true`、`render_success=false`、`render.code=engine_files_incomplete`，
  退出码与 `ok` 不变。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import (
    handoff,
    handoffreceipt as receipt,
    locate,
    pool as engine_pool,
    project_watch as engine_watch,
)

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "codex-plugin" / "skills" / "tavotto-figure"
PLUGIN_HANDOFF = SKILL_DIR / "scripts" / "handoff.py"

posix_only = pytest.mark.skipif(os.name == "nt", reason="权限位 / uid 判据只在 POSIX 上有意义")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """回执落在 `<data_dir>/session/handoff/`：每条用例一个私有数据目录。"""
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


class Clock:
    """可注入的假时钟：sleep 推进时间，测试里没有真等待。"""

    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


@pytest.fixture()
def project(tmp_path):
    d = tmp_path / "figs"
    d.mkdir()
    return d


# --------------------------- 回执通道的认证面 -----------------------------
def test_expectation_is_private_and_carries_an_expiry(project):
    exp = receipt.expect(str(project), "Fig1", 20, now=lambda: 5000.0)
    assert exp is not None
    assert re.fullmatch(r"[0-9a-f]{32}", exp.id)
    assert Path(exp.path).parent == Path(receipt.receipt_dir())
    data = json.loads(Path(exp.path).read_text(encoding="utf-8"))
    assert data["result"] is None and data["stem"] == "Fig1"
    assert data["expires_at"] == pytest.approx(5000.0 + 20 + receipt.GRACE_S)
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(exp.path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(receipt.receipt_dir()).st_mode) == 0o700


def test_record_ok_fills_only_the_matching_expectation(project, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    mine = receipt.expect(str(project), "Fig1", 20)
    wrong_stem = receipt.expect(str(project), "Fig2", 20)
    wrong_project = receipt.expect(str(other), "Fig1", 20)
    wrote = receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=3)
    assert wrote == 1
    assert receipt.read_result(mine)["status"] == "ok"
    assert receipt.read_result(wrong_stem) is None
    assert receipt.read_result(wrong_project) is None


def test_an_expired_expectation_is_never_filled(project):
    exp = receipt.expect(str(project), "Fig1", 1, now=lambda: 100.0)
    late = 100.0 + 1 + receipt.GRACE_S + 1
    assert (
        receipt.record_ok(
            str(project), "Fig1", registry_match=True, manifest_elements=1, now=lambda: late
        )
        == 0
    )
    assert receipt.read_result(exp) is None


def test_first_conclusion_wins(project):
    exp = receipt.expect(str(project), "Fig1", 20)
    assert receipt.record_failed(str(project), "Fig1", "engine_files_incomplete") == 1
    # 之后的渲染（用户重试成功）不改写这次交接的结论
    assert receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=9) == 0
    assert receipt.read_result(exp) == {"status": "failed", "code": "engine_files_incomplete"}


def test_nothing_happens_when_no_handoff_is_waiting(project):
    """绝大多数渲染不是交接触发的：目录都不存在，零写入。"""
    assert not os.path.exists(receipt.receipt_dir())
    assert receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=1) == 0
    assert not os.path.exists(receipt.receipt_dir())


@pytest.mark.parametrize("code", ["", "Has Space", "UPPER", "a" * 80, "../x", None])
def test_a_malformed_failure_code_is_replaced_by_a_stable_one(project, code):
    exp = receipt.expect(str(project), "Fig1", 20)
    receipt.record_failed(str(project), "Fig1", code)
    assert receipt.read_result(exp) == {"status": "failed", "code": receipt.CODE_RENDER_FAILED}


def test_foreign_files_in_the_directory_are_not_trusted(project):
    d = Path(receipt.receipt_dir())
    exp = receipt.expect(str(project), "Fig1", 20)
    good = json.loads(Path(exp.path).read_text(encoding="utf-8"))
    # 命名不合规：不是 <32 位十六进制>.json
    (d / "evil.json").write_text(json.dumps({**good, "id": "e" * 32}), encoding="utf-8")
    # 体积超限
    big_id = "b" * 32
    (d / f"{big_id}.json").write_text(
        json.dumps({**good, "id": big_id, "pad": "x" * receipt.MAX_BYTES}), encoding="utf-8"
    )
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=1)
    assert json.loads((d / "evil.json").read_text())["result"] is None
    assert json.loads((d / f"{big_id}.json").read_text())["result"] is None
    assert receipt.read_result(exp)["status"] == "ok"


@posix_only
def test_a_symlinked_receipt_is_ignored(project, tmp_path):
    exp = receipt.expect(str(project), "Fig1", 20)
    d = Path(receipt.receipt_dir())
    secret = tmp_path / "target.json"
    secret.write_text(Path(exp.path).read_text(encoding="utf-8"), encoding="utf-8")
    link_id = "c" * 32
    os.symlink(secret, d / f"{link_id}.json")
    receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=1)
    assert json.loads(secret.read_text())["result"] is None  # 没顺着链接写出去


@posix_only
def test_a_receipt_owned_by_someone_else_is_ignored(project, monkeypatch):
    exp = receipt.expect(str(project), "Fig1", 20)
    monkeypatch.setattr(os, "getuid", lambda: os.stat(exp.path).st_uid + 1)
    assert receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=1) == 0
    assert receipt.read_result(exp) is None


def test_the_result_is_sanitised_on_the_way_out(project):
    """实例写回的内容不被原样信任：类型不对的字段落成保守值。"""
    exp = receipt.expect(str(project), "Fig1", 20)
    data = json.loads(Path(exp.path).read_text(encoding="utf-8"))
    data["result"] = {
        "outcome": "ok",
        "stem": 7,
        "registry_match": "yes",
        "manifest_elements": -4,
    }
    Path(exp.path).write_text(json.dumps(data), encoding="utf-8")
    assert receipt.read_result(exp) == {
        "status": "ok",
        "stem": None,
        "registry_match": False,
        "manifest_elements": 0,
    }


# ------------------------------ verified 判据 -----------------------------
def _ok(stem="Fig1", registry_match=True, manifest_elements=5):
    return {
        "status": "ok",
        "stem": stem,
        "registry_match": registry_match,
        "manifest_elements": manifest_elements,
    }


def _exp(project, stem="Fig1"):
    return receipt.Expectation("a" * 32, "", str(project), stem, 0.0)


def test_verified_needs_all_three_facts(project):
    exp = _exp(project)
    assert receipt.summarize(exp, _ok(), 10, reason="x")["verified"] is True
    # 逐条变异：少一条都不是 verified，但渲染本身仍是 ok
    for bad in (
        _ok(stem="Fig2"),  # 渲染的不是目标那张
        _ok(registry_match=False),  # 注册表里没有这个 stem
        _ok(manifest_elements=0),  # 清单为空
    ):
        out = receipt.summarize(exp, bad, 10, reason="x")
        assert out["status"] == "ok" and out["verified"] is False, bad


def test_failed_and_unverified_are_never_verified(project):
    exp = _exp(project)
    failed = receipt.summarize(
        exp, {"status": "failed", "code": "engine_files_incomplete"}, 3, reason="x"
    )
    assert failed["status"] == "failed" and failed["verified"] is False
    assert failed["code"] == "engine_files_incomplete"
    unver = receipt.summarize(exp, None, 3, reason=receipt.REASON_TIMEOUT)
    assert unver == {"status": "unverified", "reason": "timeout", "verified": False, "waited_ms": 3}


# ------------------------------ 有界等待 ----------------------------------
def test_wait_times_out_as_unverified_not_failed(project):
    clock = Clock()
    exp = receipt.expect(str(project), "Fig1", 20)
    out = receipt.wait(exp, 20, clock=clock, sleep=clock.sleep, interval=0.5)
    assert out["status"] == "unverified" and out["reason"] == "timeout"
    assert out["waited_ms"] >= 20000
    assert clock.t - 1000.0 < 21  # 有界：不会无限轮询


def test_wait_returns_as_soon_as_a_conclusion_lands(project):
    clock = Clock()
    exp = receipt.expect(str(project), "Fig1", 20)

    def sleeping(s):
        clock.sleep(s)
        if clock.t >= 1002.0:  # 两秒后实例写回
            receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=4)

    out = receipt.wait(exp, 20, clock=clock, sleep=sleeping, interval=0.5)
    assert out["status"] == "ok" and out["verified"] is True
    assert out["waited_ms"] < 5000


# ------------------------ open_target：launch.render ----------------------
def _fig_project(tmp_path):
    d = tmp_path / "gallery"
    d.mkdir()
    (d / "fig1.py").write_text(
        "import matplotlib.pyplot as plt\n\n\ndef main():\n"
        "    fig, ax = plt.subplots()\n    fig.savefig('Fig1.pdf')\n",
        encoding="utf-8",
    )
    (d / "Fig1.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    return d


class FakeLaunch:
    """替身 `handoff.launch`：可选地在「唤起」期间让实例写回执。"""

    def __init__(self, on_launch=None, raises=None):
        self.on_launch = on_launch
        self.raises = raises
        self.calls = 0

    def __call__(self, target, **kw):
        self.calls += 1
        if self.raises:
            raise self.raises
        if self.on_launch:
            self.on_launch(target)
        return {"mode": "desktop", "app": "/x/Tavotto", "handoff": "launched", "pid": 1}


def _open(tmp_path, monkeypatch, fake, **kw):
    d = _fig_project(tmp_path)
    monkeypatch.setattr(handoff, "launch", fake)
    clock = Clock()
    result = handoff.open_target(
        str(d / "Fig1.pdf"), receipt_clock=clock, receipt_sleep=clock.sleep, **kw
    )
    return d, result


def test_render_ok_when_the_instance_reports_the_target(tmp_path, monkeypatch):
    d = _fig_project(tmp_path)
    fake = FakeLaunch(
        lambda t: receipt.record_ok(t.project, t.stem, registry_match=True, manifest_elements=6)
    )
    monkeypatch.setattr(handoff, "launch", fake)
    clock = Clock()
    result = handoff.open_target(
        str(d / "Fig1.pdf"), render_wait=20, receipt_clock=clock, receipt_sleep=clock.sleep
    )
    render = result["launch"]["render"]
    assert result["ok"] is True and result["protocol"] == 1
    assert render["status"] == "ok" and render["verified"] is True
    assert render["manifest_elements"] == 6 and render["stem"] == "Fig1"


def test_render_failed_carries_the_stable_code_and_ok_is_unchanged(tmp_path, monkeypatch):
    """#961 的形状：交接 ok:true，渲染 failed + engine_files_incomplete。"""
    d = _fig_project(tmp_path)
    fake = FakeLaunch(lambda t: receipt.record_failed(t.project, t.stem, "engine_files_incomplete"))
    monkeypatch.setattr(handoff, "launch", fake)
    clock = Clock()
    result = handoff.open_target(
        str(d / "Fig1.pdf"), render_wait=20, receipt_clock=clock, receipt_sleep=clock.sleep
    )
    assert result["ok"] is True  # 公开语义不变
    assert result["launch"]["mode"] == "desktop"
    assert result["launch"]["render"] == {
        "status": "failed",
        "code": "engine_files_incomplete",
        "verified": False,
        "waited_ms": result["launch"]["render"]["waited_ms"],
    }


def test_no_signal_within_the_budget_is_unverified_timeout(tmp_path, monkeypatch):
    _, result = _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    render = result["launch"]["render"]
    assert result["ok"] is True
    assert render["status"] == "unverified" and render["reason"] == "timeout"


def test_render_wait_zero_does_not_wait_or_write_anything(tmp_path, monkeypatch):
    _, result = _open(tmp_path, monkeypatch, FakeLaunch())  # open_target 默认 0
    assert result["launch"]["render"]["reason"] == "not_waited"
    assert not os.path.exists(receipt.receipt_dir())


def test_directory_handoff_has_no_target_stem(tmp_path, monkeypatch):
    d = _fig_project(tmp_path)
    monkeypatch.setattr(handoff, "launch", FakeLaunch())
    result = handoff.open_target(str(d), render_wait=20)
    assert result["launch"]["render"]["reason"] == "no_target_stem"


def test_a_figure_without_a_script_is_not_waited_for(tmp_path, monkeypatch):
    """不可参数化 = 界面不发起引擎渲染：不写期望、不等，直接 not_renderable。"""
    d = tmp_path / "gallery"
    d.mkdir()
    (d / "Fig9.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")  # 没有任何脚本产出它
    seen: list[bool] = []
    monkeypatch.setattr(
        handoff, "launch", FakeLaunch(lambda t: seen.append(os.path.isdir(receipt.receipt_dir())))
    )
    clock = Clock()
    result = handoff.open_target(
        str(d / "Fig9.pdf"), render_wait=20, receipt_clock=clock, receipt_sleep=clock.sleep
    )
    assert result["registry"]["parameterizable"] is False
    assert result["launch"]["render"]["reason"] == "not_renderable"
    assert clock.sleeps == [] and seen == [False]


def test_unwritable_channel_is_unverified_not_an_error(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(blocker))
    _, result = _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    assert result["ok"] is True
    assert result["launch"]["render"]["reason"] == "channel_unavailable"


def test_a_failed_launch_leaves_no_expectation_behind(tmp_path, monkeypatch):
    fake = FakeLaunch(raises=handoff.HandoffError("起不来", "launch_failed"))
    d = _fig_project(tmp_path)
    monkeypatch.setattr(handoff, "launch", fake)
    with pytest.raises(handoff.HandoffError):
        handoff.open_target(str(d / "Fig1.pdf"), render_wait=20)
    assert os.listdir(receipt.receipt_dir()) == []


def test_the_expectation_is_removed_after_the_wait(tmp_path, monkeypatch):
    _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    assert os.listdir(receipt.receipt_dir()) == []


def test_the_expectation_exists_before_launch_returns(tmp_path, monkeypatch):
    """转发给已在跑的实例时渲染可能比 launch 返回还早：期望必须先于唤起写好。"""
    seen: list[int] = []
    fake = FakeLaunch(lambda t: seen.append(len(os.listdir(receipt.receipt_dir()))))
    _open(tmp_path, monkeypatch, fake, render_wait=20)
    assert seen == [1]


def test_launch_keys_other_than_render_are_untouched(tmp_path, monkeypatch):
    _, result = _open(tmp_path, monkeypatch, FakeLaunch(), render_wait=20)
    launch = dict(result["launch"])
    launch.pop("render")
    assert launch == {"mode": "desktop", "app": "/x/Tavotto", "handoff": "launched", "pid": 1}


def test_no_launch_has_no_render_block(tmp_path, monkeypatch):
    d = _fig_project(tmp_path)
    result = handoff.open_target(str(d / "Fig1.pdf"), launch_ui=False, render_wait=20)
    assert result["launch"] is None


def test_cli_exposes_render_wait_and_passes_it_through(monkeypatch, capsys):
    seen = {}

    def fake_open(path, **kw):
        seen.update(kw)
        return {
            "ok": True,
            "protocol": 1,
            "project": "/p",
            "stem": None,
            "registry": {},
            "launch": None,
        }

    monkeypatch.setattr(handoff, "open_target", fake_open)
    assert handoff.cli(["/x", "--json"]) == 0
    assert seen["render_wait"] == handoff.DEFAULT_RENDER_WAIT
    assert handoff.cli(["/x", "--json", "--render-wait", "0"]) == 0
    assert seen["render_wait"] == 0.0
    capsys.readouterr()


def test_protocol_version_is_still_one():
    assert locate.PROTOCOL_VERSION == 1


# ----------------- /api/engine/render 收尾处确实写回执 ---------------------
@pytest.fixture
def client(monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()


class _Worker:
    def __init__(self, manifest=None, error=None):
        self.built = True
        self.rev = 1
        self.manifest = {"elements": [{"gid": "a"}]} if manifest is None else manifest
        self.error = error

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        if self.error:
            raise self.error
        return {"manifest": self.manifest, "warnings": []}


def _gallery(client, tmp_path):
    figs = tmp_path / "p"
    figs.mkdir()
    doc = pymupdf.open()
    doc.new_page(width=100, height=50)
    doc.save(figs / "p1.pdf")
    doc.close()
    client.post("/api/projects/open", json={"path": str(figs)})
    return figs


def _stub(monkeypatch, worker, registered=True):
    monkeypatch.setattr(
        m.engine_registry.Registry,
        "for_stem",
        lambda self, s: (
            {"script": "x.py", "entry": "main", "cost": "light"} if registered else None
        ),
    )
    monkeypatch.setattr(m.engine_pool, "get", lambda *a, **kw: worker)


def test_render_endpoint_reports_success_with_manifest_and_registry_facts(
    client, tmp_path, monkeypatch
):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())
    exp = receipt.expect(str(figs), "p1", 20)
    assert (
        client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []}).status_code == 200
    )
    out = receipt.summarize(exp, receipt.read_result(exp), 0, reason="x")
    assert out["status"] == "ok" and out["verified"] is True and out["manifest_elements"] == 1


def test_render_endpoint_with_an_empty_manifest_is_ok_but_not_verified(
    client, tmp_path, monkeypatch
):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker(manifest={"elements": []}))
    exp = receipt.expect(str(figs), "p1", 20)
    client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    out = receipt.summarize(exp, receipt.read_result(exp), 0, reason="x")
    assert out["status"] == "ok" and out["verified"] is False


def test_render_endpoint_with_an_unregistered_stem_is_not_verified(client, tmp_path, monkeypatch):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker(), registered=False)
    exp = receipt.expect(str(figs), "p1", 20)
    # 没登记 = 磁盘面板拿不到 worker（404）：渲染根本没发生，回执不得凭空写 ok
    client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert receipt.read_result(exp) is None


def test_render_endpoint_reports_a_worker_error_code(client, tmp_path, monkeypatch):
    figs = _gallery(client, tmp_path)
    err = engine_pool.WorkerError("缺引擎文件", code="engine_files_incomplete")
    _stub(monkeypatch, _Worker(error=err))
    exp = receipt.expect(str(figs), "p1", 20)
    assert (
        client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []}).status_code == 500
    )
    assert receipt.read_result(exp) == {"status": "failed", "code": "engine_files_incomplete"}


def test_render_endpoint_ignores_a_handoff_for_another_stem(client, tmp_path, monkeypatch):
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())
    exp = receipt.expect(str(figs), "other", 20)
    client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert receipt.read_result(exp) is None


def test_a_broken_receipt_directory_never_breaks_the_render(client, tmp_path, monkeypatch):
    _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())

    def boom(*a, **kw):
        raise OSError("disk on fire")

    monkeypatch.setattr(receipt, "record_ok", boom)
    assert (
        client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []}).status_code == 200
    )


def test_cli_and_endpoint_agree_end_to_end(client, tmp_path, monkeypatch):
    """CLI 写期望 → 「唤起」期间界面发起渲染 → 回执 → launch.render，全链路用真代码。"""
    figs = _gallery(client, tmp_path)
    _stub(monkeypatch, _Worker())

    def render_in_window(target):
        assert (
            client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []}).status_code
            == 200
        )

    monkeypatch.setattr(handoff, "launch", FakeLaunch(render_in_window))
    result = handoff.open_target(str(figs / "p1.pdf"), render_wait=20)
    assert result["launch"]["render"]["status"] == "ok"
    assert result["launch"]["render"]["verified"] is True


# ------------------------------- 插件三字段 -------------------------------
@pytest.fixture(scope="module")
def plugin():
    spec = importlib.util.spec_from_file_location("_plugin_handoff_states", PLUGIN_HANDOFF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _final(render=None, launch=True, ok=True):
    out = {"ok": ok}
    if launch:
        out["launch"] = {"mode": "desktop", **({"render": render} if render is not None else {})}
    else:
        out["launch"] = None
    return out


def test_plugin_protocol_constant_still_matches_the_locator(plugin):
    assert plugin.PROTOCOL == locate.PROTOCOL_VERSION == 1


def test_new_plugin_new_cli_verified(plugin):
    s = plugin.three_state(_final({"status": "ok", "verified": True, "waited_ms": 5}))
    assert s["launch_success"] is True
    assert s["render_success"] is True and s["verified_success"] is True
    assert s["render"]["status"] == "ok"


def test_new_plugin_new_cli_rendered_but_not_verified(plugin):
    s = plugin.three_state(_final({"status": "ok", "verified": False}))
    assert s["render_success"] is True and s["verified_success"] is False


def test_new_plugin_new_cli_961_shape(plugin):
    s = plugin.three_state(
        _final({"status": "failed", "code": "engine_files_incomplete", "verified": False})
    )
    assert s["launch_success"] is True
    assert s["render_success"] is False and s["verified_success"] is False
    assert s["render"]["code"] == "engine_files_incomplete"


def test_new_plugin_new_cli_unverified_is_unknown_not_failed(plugin):
    s = plugin.three_state(_final({"status": "unverified", "reason": "timeout", "verified": False}))
    assert s["launch_success"] is True
    assert s["render_success"] is None and s["verified_success"] is False


def test_new_plugin_old_cli_is_distinguishable_unknown(plugin):
    s = plugin.three_state(_final(render=None))  # 老 CLI：launch 里没有 render
    assert s["launch_success"] is True
    assert s["render_success"] is None and s["verified_success"] is False
    assert s["render"] == {"status": "unverified", "reason": "cli_without_render_signal"}


def test_verified_must_be_strictly_true(plugin):
    for bad in ("true", 1, None, "yes"):
        s = plugin.three_state(_final({"status": "ok", "verified": bad}))
        assert s["verified_success"] is False, bad
    # 一个不合规的 render（未知 status）按「老 CLI 没给」处理，不凭它宣称任何事
    s = plugin.three_state(_final({"status": "great", "verified": True}))
    assert s["render_success"] is None and s["verified_success"] is False


def test_no_launch_is_neither_launched_nor_failed(plugin):
    s = plugin.three_state(_final(launch=False))
    assert s["launch_success"] is None
    assert s["render_success"] is None and s["verified_success"] is False
    assert "render" not in s


# --- 真起插件脚本 + 假 CLI：旧/新 CLI × 新插件，退出码与 ok 不变 -----------
FAKE_CLI = """#!PYTHON
import json, os, sys
resp = json.load(open(os.environ["FAKE_RESPONSE"], encoding="utf-8"))
if "--no-launch" not in sys.argv:
    resp.setdefault("launch", {"mode": "desktop"})
print(json.dumps(resp, ensure_ascii=False))
"""


def _run_plugin(tmp_path, response):
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "tavotto"
    fake.write_text(FAKE_CLI.replace("#!PYTHON", "#!" + sys.executable), encoding="utf-8")
    fake.chmod(0o755)
    resp_file = tmp_path / "response.json"
    resp_file.write_text(json.dumps(response), encoding="utf-8")
    target = tmp_path / "Fig1.pdf"
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    env = {**os.environ, "PATH": str(bin_dir), "FAKE_RESPONSE": str(resp_file)}
    env.pop("TAVOTTO_CLI", None)
    proc = subprocess.run(
        [sys.executable, str(PLUGIN_HANDOFF), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return proc, json.loads(proc.stdout.strip().splitlines()[-1])


BASE = {
    "ok": True,
    "project": "/p",
    "stem": "Fig1",
    "registry": {"parameterizable": True, "conflicts": [], "dynamic_names": []},
}


@posix_only
def test_plugin_end_to_end_961_keeps_exit_code_zero_but_reports_the_failure(tmp_path):
    resp = {
        **BASE,
        "launch": {
            "mode": "desktop",
            "render": {"status": "failed", "code": "engine_files_incomplete", "verified": False},
        },
    }
    proc, out = _run_plugin(tmp_path, resp)
    assert proc.returncode == 0  # ok 的公开语义不变
    assert out["ok"] is True and out["parameterizable"] is True and out["launch"] == "desktop"
    assert out["launch_success"] is True
    assert out["render_success"] is False and out["verified_success"] is False
    assert out["render"]["code"] == "engine_files_incomplete"


@posix_only
def test_plugin_end_to_end_old_cli(tmp_path):
    proc, out = _run_plugin(tmp_path, BASE)  # 假 CLI 补 {"mode": "desktop"}，没有 render
    assert proc.returncode == 0
    assert out["ok"] is True and out["launch_success"] is True
    assert out["render_success"] is None and out["verified_success"] is False
    assert out["render"]["reason"] == "cli_without_render_signal"


@posix_only
def test_plugin_end_to_end_non_parameterizable_still_exits_four(tmp_path):
    resp = {
        **BASE,
        "registry": {"parameterizable": False, "conflicts": [], "dynamic_names": []},
        "launch": {"mode": "desktop", "render": {"status": "ok", "verified": True}},
    }
    proc, out = _run_plugin(tmp_path, resp)
    assert proc.returncode == 4 and out["error_code"] == "not_parameterizable"
    assert out["launch_success"] is True


@posix_only
def test_every_failure_payload_still_carries_the_three_fields(tmp_path):
    """没走到唤起的失败出口：launch 未成功、渲染未知、未确认——字段齐全，不缺键。"""
    proc, out = _run_plugin(tmp_path, {"ok": False, "code": "registry_write_failed", "error": "x"})
    assert proc.returncode == 2
    assert out["error_code"] == "registry_write_failed"
    assert out["launch_success"] is False
    assert out["render_success"] is None and out["verified_success"] is False


def test_plugin_missing_path_payload_has_the_three_fields(tmp_path):
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(PLUGIN_HANDOFF), str(tmp_path / "nope.pdf")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = json.loads(proc.stdout)
    assert out["launch_success"] is False and out["verified_success"] is False


# --------------------------- 同源对：文档与技能文本 -------------------------
def test_protocol_doc_and_skill_text_name_the_fields():
    doc = (ROOT / "docs" / "handoff-protocol.md").read_text(encoding="utf-8")
    ref = (SKILL_DIR / "references" / "desktop-handoff.md").read_text(encoding="utf-8")
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for token in ("launch_success", "render_success", "verified_success"):
        assert token in doc and token in ref, token
    assert "render_success" in skill and "verified_success" in skill
    assert "--render-wait" in doc
    # 文档里列的 unverified.reason 闭集与实现一致（单一来源 receipt.REASONS）
    for reason in receipt.REASONS:
        assert reason in doc, reason
    assert "cli_without_render_signal" in doc and "cli_without_render_signal" in ref


def test_plugin_unsupported_reason_is_documented_and_not_a_receipt_reason(plugin):
    """`cli_without_render_signal` 只由插件产生（CLI 的闭集里没有它）。"""
    assert plugin.RENDER_UNSUPPORTED["reason"] == "cli_without_render_signal"
    assert "cli_without_render_signal" not in receipt.REASONS


def test_concurrent_records_do_not_corrupt_the_receipt(project):
    exp = receipt.expect(str(project), "Fig1", 20)
    threads = [
        threading.Thread(
            target=receipt.record_ok,
            args=(str(project), "Fig1"),
            kwargs={"registry_match": True, "manifest_elements": i + 1},
        )
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = receipt.read_result(exp)  # 无论谁赢，文件始终是合法 JSON
    assert got is not None and got["status"] == "ok" and got["manifest_elements"] >= 1


def test_render_endpoint_reports_a_worker_startup_error(client, tmp_path, monkeypatch):
    """worker 启动 / 环境解析阶段（取 worker 的那段）失败也要写回执，CLI 才不白等到 timeout。"""
    figs = _gallery(client, tmp_path)
    err = engine_pool.WorkerError("缺引擎文件", code="engine_files_incomplete")

    def boom(*a, **kw):
        raise err

    monkeypatch.setattr(m, "_engine_worker", boom)
    exp = receipt.expect(str(figs), "p1", 20)
    client.post("/api/engine/render", json={"id": "p1.pdf", "patches": []})
    assert receipt.read_result(exp) == {"status": "failed", "code": "engine_files_incomplete"}


def test_two_interleaved_writers_keep_the_first_conclusion(project, monkeypatch):
    """第一个写者读后、写前被暂停，第二个写者此时进来：结论仍是第一个的。"""
    exp = receipt.expect(str(project), "Fig1", 20)
    real_write = receipt._write_atomic
    state = {"inner": None}

    def write_after_second_writer(path, payload):
        if state["inner"] is None:
            state["inner"] = receipt.record_failed(str(project), "Fig1", "second_writer")
        return real_write(path, payload)

    monkeypatch.setattr(receipt, "_write_atomic", write_after_second_writer)
    assert receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=3) == 1
    assert state["inner"] == 0  # 第二个写者拿不到锁，让路
    got = receipt.read_result(exp)
    assert got is not None and got["status"] == "ok" and got["manifest_elements"] == 3
    assert not os.path.exists(exp.path + receipt.LOCK_SUFFIX)


def test_a_stale_lock_is_cleared_and_a_fresh_one_blocks(project):
    exp = receipt.expect(str(project), "Fig1", 20)
    lock = exp.path + receipt.LOCK_SUFFIX
    open(lock, "w").close()
    assert receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=1) == 0
    old = time.time() - receipt.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    assert receipt.record_ok(str(project), "Fig1", registry_match=True, manifest_elements=1) == 1
    assert not os.path.exists(lock)
    assert receipt._entries(os.path.dirname(exp.path)) == [os.path.basename(exp.path)]


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-1"])
def test_render_wait_rejects_non_finite_or_negative(bad):
    with pytest.raises(SystemExit) as e:
        handoff.cli(["x.pdf", f"--render-wait={bad}"])
    assert e.value.code == 2


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -3.0])
def test_wait_budget_is_defended_against_non_finite_values(project, bad):
    exp = receipt.expect(str(project), "Fig1", bad)
    out = receipt.wait(exp, bad, clock=lambda: 0.0, sleep=lambda s: pytest.fail("不该睡"))
    assert out["status"] == "unverified" and out["reason"] == "timeout"
    assert exp.expires_at - time.time() < receipt.GRACE_S + 5
