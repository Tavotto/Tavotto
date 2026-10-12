"""预览发布是「先写临时文件、成功了才发布、最后记账」（P01b，EX12，AC-05 前半）。

`figsession.render` 曾直接开正式 `<stem>.svg` 写：画到一半抛了就留下半份 XML，
既被 `runcli` 按 `.svg` 后缀数成「完成了一张图」，也覆盖了上一版有效预览。

这里在**引擎这一层**钉住事实（真 Matplotlib，驱动脚本跑在带 matplotlib 的解释器里）：
完成 = 账本里有记录**且**磁盘上的 SVG 字节数对得上；半文件 / 上一次残留 / 被截断的
文件都不算；失败的发布不动上一版。CLI 端到端的两图一坏在
`tests/native/test_run_cli_integration.py`。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")

ENGINE_DIR = str(Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine")

DRIVER = r"""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.artist import Artist
import figcapture
from figsession import LiveFigureSession


class AggOnly(Artist):
    def draw(self, renderer):
        renderer.buffer_rgba()  # SVG renderer 上没有它：画到一半抛 AttributeError


def good(title):
    fig, ax = plt.subplots()
    ax.plot([0, 1], [1, 2])
    ax.set_title(title)
    return fig


def bad():
    fig, ax = plt.subplots()
    ax.plot([0, 1], [3, 4])
    ax.add_artist(AggOnly())
    return fig


def facts(out):
    out = Path(out)
    return {
        "published": sorted(figcapture.published_preview_stems(out)),
        "svgs": sorted(p.name for p in out.glob("*.svg")),
        "parts": sorted(p.name for p in out.glob("*.part")),
    }


def wellformed(path):
    import xml.etree.ElementTree as ET
    try:
        ET.parse(path)
        return True
    except ET.ParseError:
        return False


scenario = sys.argv[2]
out = {}
with tempfile.TemporaryDirectory() as tmp:
    sess = LiveFigureSession(tmp, preview_dpi=72)
    if scenario == "good_then_bad":
        sess.add_figure("good", good("G"), figcapture.SOURCE_SAVEFIG)
        sess.add_figure("bad", bad(), figcapture.SOURCE_SAVEFIG)
        try:
            sess.instrument_all()
        except Exception as exc:
            out["error"] = type(exc).__name__
        out.update(facts(tmp))
        out["good_wellformed"] = wellformed(Path(tmp) / "good.svg")
    elif scenario == "bad_first":
        sess.add_figure("bad", bad(), figcapture.SOURCE_SAVEFIG)
        try:
            sess.instrument_all()
        except Exception as exc:
            out["error"] = type(exc).__name__
        out.update(facts(tmp))
    elif scenario == "rerender_failure_keeps_old":
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        old = (Path(tmp) / "fig.svg").read_bytes()
        old_json = (Path(tmp) / "fig.json").read_bytes()
        out["before"] = facts(tmp)
        sess.states["fig"].fig.axes[0].add_artist(AggOnly())  # 之后的预览必然失败
        try:
            sess.render("fig")
        except Exception as exc:
            out["error"] = type(exc).__name__
        out["after"] = facts(tmp)
        out["svg_unchanged"] = (Path(tmp) / "fig.svg").read_bytes() == old
        out["json_unchanged"] = (Path(tmp) / "fig.json").read_bytes() == old_json
        out["old_wellformed"] = wellformed(Path(tmp) / "fig.svg")
    elif scenario == "rerender_success_advances":
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        first = (Path(tmp) / "fig.svg").read_bytes()
        sess.states["fig"].fig.axes[0].set_title("a considerably longer second title")
        sess.render("fig")
        out["changed"] = (Path(tmp) / "fig.svg").read_bytes() != first
        out["published"] = facts(tmp)["published"]
        out["seq"] = json.loads((Path(tmp) / figcapture.PREVIEW_LEDGER).read_text(encoding="utf-8"))["seq"]
    elif scenario == "stem_collides_with_old_ledger_name":
        # savefig("published.png") 的 stem 是 published；点开头的 stem 同理
        names = ["published", "good", ".preview-ledger", ".published"]
        for n in names:
            sess.add_figure(n, good(n), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        out["published"] = sorted(figcapture.published_preview_stems(tmp))
        out["manifests_ok"] = {
            n: json.loads((Path(tmp) / f"{n}.json").read_text(encoding="utf-8")).get("stem") == n
            for n in names
        }
        out["ledger_stems"] = sorted(
            json.loads((Path(tmp) / figcapture.PREVIEW_LEDGER).read_text(encoding="utf-8"))["stems"]
        )
        out["ledger_name"] = figcapture.PREVIEW_LEDGER
        out["ledger_is_figure_like"] = figcapture.PREVIEW_LEDGER.endswith((".svg", ".json", ".part"))
    elif scenario in ("second_replace_fails", "second_replace_fails_first_publish"):
        first = scenario.endswith("first_publish")
        if not first:
            sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
            sess.instrument_all()
        else:
            sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        p = Path(tmp)
        snap = lambda: {  # noqa: E731
            n: (p / n).read_bytes() for n in ("fig.svg", "fig.json", figcapture.PREVIEW_LEDGER)
            if (p / n).exists()
        }
        old = snap()
        if not first:
            sess.states["fig"].fig.axes[0].set_title("a considerably longer second title")
        real_replace = os.replace

        def flaky(src, dst, *a, **k):  # Windows 上 manifest 被临时锁住
            if str(dst).endswith("fig.json"):
                raise PermissionError(13, "locked", str(dst))
            return real_replace(src, dst, *a, **k)

        os.replace = flaky
        try:
            try:
                if first:
                    sess.instrument_all()
                else:
                    sess.render("fig")
            except Exception as exc:
                out["error"] = type(exc).__name__
        finally:
            os.replace = real_replace
        new = snap()
        out["files_same"] = new == old
        out["svg_same"] = new.get("fig.svg") == old.get("fig.svg")
        out["json_same"] = new.get("fig.json") == old.get("fig.json")
        out["names_after"] = sorted(n for n in os.listdir(tmp))
        out["published"] = sorted(figcapture.published_preview_stems(tmp))
        if not first:
            # 成套且一致：SVG 字节数 = 账本记的数；manifest 仍是旧代
            led = json.loads(new[figcapture.PREVIEW_LEDGER])
            out["svg_matches_ledger"] = led["stems"]["fig"]["svg_bytes"] == len(new["fig.svg"])
            out["seq"] = led["seq"]
            os.replace(p / "fig.svg", p / "x.svg")  # 清场后重试：现在没有锁了，能发布新一代
            os.replace(p / "x.svg", p / "fig.svg")
            sess.render("fig")
            out["retry_changed"] = (p / "fig.svg").read_bytes() != old["fig.svg"]
            out["retry_seq"] = json.loads((p / figcapture.PREVIEW_LEDGER).read_text(encoding="utf-8"))["seq"]
    elif scenario in ("restore_also_fails", "restore_and_removal_fail", "marker_also_fails"):
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        p = Path(tmp)
        sess.states["fig"].fig.axes[0].set_title("a considerably longer second title")
        stuck = scenario != "restore_also_fails"  # 连删除 / 改名隔离都被锁
        no_marker = scenario == "marker_also_fails"  # 连失效标记都写不下去（卷满 / 权限）
        real_replace, real_unlink = os.replace, Path.unlink
        import builtins
        real_open = builtins.open

        def flaky_open(file, mode="r", *a, **k):
            if no_marker and str(file).endswith(".preview-invalid") and "w" in mode:
                raise OSError(28, "No space left on device", str(file))
            return real_open(file, mode, *a, **k)

        builtins.open = flaky_open

        def deny(*a):
            raise PermissionError(13, "locked", str(a[-1]))

        def flaky(src, dst, *a, **k):
            if str(dst).endswith("fig.json") and str(src).endswith(".part") and ".prev" not in str(src):
                deny(src, dst)  # 发布 manifest 失败
            if str(src).endswith(".prev.part") or (stuck and str(src).endswith("fig.svg")):
                deny(src, dst)  # 还原（以及 stuck 时的隔离改名）也失败
            return real_replace(src, dst, *a, **k)

        def flaky_unlink(self, *a, **k):
            if stuck and self.name == "fig.svg":
                deny(self)
            return real_unlink(self, *a, **k)

        os.replace, Path.unlink = flaky, flaky_unlink
        try:
            try:
                sess.render("fig")
            except Exception as exc:
                out["error"] = type(exc).__name__
                out["is_invalidated"] = isinstance(exc, __import__("figsession").PreviewInvalidated)
                out["protocol_code"] = getattr(exc, "protocol_code", None)
        finally:
            os.replace, Path.unlink = real_replace, real_unlink
            builtins.open = real_open
        out["usable_svg_exists"] = os.path.exists(figcapture.usable_preview_path(tmp, "fig", ".svg"))
        out["usable_json_exists"] = os.path.exists(figcapture.usable_preview_path(tmp, "fig", ".json"))
        out["published"] = figcapture.published_preview_stems(tmp)
        out["ledger_stems"] = sorted(json.loads((p / figcapture.PREVIEW_LEDGER).read_text(encoding="utf-8"))["stems"])
        out["backups"] = sorted(n for n in os.listdir(tmp) if n.endswith(".prev.part"))
        out["marker"] = (p / ".fig.preview-invalid").exists()
        # 解锁后重新发布：恢复，标记与备份清干净，序号前进
        sess.render("fig")
        out["after_published"] = figcapture.published_preview_stems(tmp)
        out["after_usable_svg"] = os.path.exists(figcapture.usable_preview_path(tmp, "fig", ".svg"))
        out["after_names"] = sorted(os.listdir(tmp))
        out["after_seq"] = json.loads((p / figcapture.PREVIEW_LEDGER).read_text(encoding="utf-8"))["seq"]
    elif scenario in ("retry_ledger_fails_keeps_marker", "retry_ledger_and_remark_fail"):
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        p = Path(tmp)
        figcapture.mark_preview_invalid(tmp, "fig")  # 此前那一代已被作废（错配可能还在磁盘上）
        sess.states["fig"].fig.axes[0].set_title("retry")
        real_record = figcapture.record_preview_published
        import builtins
        real_open = builtins.open
        no_remark = scenario == "retry_ledger_and_remark_fail"

        def failing_record(*a, **k):
            if no_remark:
                os.remove(p / ".fig.preview-invalid")  # 标记在这一刻被外部撤掉（扫描器 / 清理工具）
                builtins.open = flaky_open  # 且写不回去
            raise OSError(28, "No space left on device")

        def flaky_open(file, mode="r", *a, **k):
            if str(file).endswith(".preview-invalid") and "w" in mode:
                raise OSError(28, "No space left on device", str(file))
            return real_open(file, mode, *a, **k)

        figcapture.record_preview_published = failing_record
        try:
            try:
                sess.render("fig")
            except Exception as exc:
                out["error"] = type(exc).__name__
                out["protocol_code"] = getattr(exc, "protocol_code", None)
        finally:
            figcapture.record_preview_published = real_record
            builtins.open = real_open
        out["marker"] = (p / ".fig.preview-invalid").exists()
        out["usable_svg_exists"] = os.path.exists(figcapture.usable_preview_path(tmp, "fig", ".svg"))
    elif scenario == "stale_and_truncated":
        p = Path(tmp)
        # 上一次运行残留：有完整 SVG、没有账本
        (p / "stale.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        out["stale_only"] = facts(tmp)["published"]
        # 本次发布成功之后，文件被截断：账本字节数对不上，不再算
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        out["fresh"] = facts(tmp)["published"]
        raw = (p / "fig.svg").read_bytes()
        (p / "fig.svg").write_bytes(raw[: len(raw) // 2])
        out["truncated"] = facts(tmp)["published"]
print(json.dumps(out))
"""


def _drive(scenario: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["MPLBACKEND"] = "Agg"
    proc = subprocess.run(  # noqa: S603 — 列表 argv
        [WORKER_PY, "-c", DRIVER, ENGINE_DIR, scenario],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_a_half_written_svg_is_not_a_completed_figure():
    r = _drive("good_then_bad")
    assert r["error"] == "AttributeError", r  # 坏图的渲染确实失败了（探针有效）
    assert r["published"] == ["good"], r
    assert r["good_wellformed"] is True
    assert "bad.svg" not in r["svgs"], f"半份 SVG 留在了正式名下: {r}"
    assert r["parts"] == [], f"临时文件没清: {r}"


def test_a_failing_first_publish_leaves_nothing_published():
    r = _drive("bad_first")
    assert r["error"] == "AttributeError", r
    assert r["published"] == [] and r["svgs"] == [] and r["parts"] == [], r


def test_a_failed_republish_keeps_the_previous_valid_preview():
    r = _drive("rerender_failure_keeps_old")
    assert r["error"] == "AttributeError", r
    assert r["before"]["published"] == ["fig"]
    assert r["old_wellformed"] is True
    assert r["svg_unchanged"] is True, "失败的发布改写了上一版有效预览"
    assert r["json_unchanged"] is True
    assert r["after"]["published"] == ["fig"], "上一版仍是已发布的完整预览"
    assert r["after"]["parts"] == []


def test_a_successful_republish_replaces_the_preview_and_advances_the_sequence():
    """正对照：不是「什么都不让写」——成功的重发布确实换掉了文件、序号前进。"""
    r = _drive("rerender_success_advances")
    assert r["changed"] is True
    assert r["published"] == ["fig"]
    assert r["seq"] == 2


def test_stale_and_truncated_svgs_never_count():
    r = _drive("stale_and_truncated")
    assert r["stale_only"] == [], "没有账本的残留 SVG 被数进去了"
    assert r["fresh"] == ["fig"], "正对照：本次完整发布的必须算"
    assert r["truncated"] == [], "被截断的 SVG（字节数与账本不符）仍被算作完成"


def test_the_cli_count_comes_from_the_ledger_not_from_svg_files(tmp_path):
    """CLI 的「捕获了几张图」读引擎写的账本：只有 .svg 文件而没有账本记录的，数 0。"""
    from tavotto.engine import figcapture, runcli

    (tmp_path / "a.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "b.svg").write_text("<svg", encoding="utf-8")  # 半份 XML
    assert runcli._figures_written(str(tmp_path)) == 0
    figcapture.record_preview_published(tmp_path, "a", (tmp_path / "a.svg").stat().st_size)
    assert runcli._figures_written(str(tmp_path)) == 1  # 记过账的算，没记账的 b 不算


def test_a_user_stem_cannot_collide_with_the_ledger():
    """`savefig("published.png")` 曾把 manifest 写在 published.json，再被同名账本覆盖。"""
    r = _drive("stem_collides_with_old_ledger_name")
    assert r["ledger_is_figure_like"] is False, r["ledger_name"]
    assert all(r["manifests_ok"].values()), f"某个 stem 的 <stem>.json 不是 manifest: {r}"
    assert r["published"] == sorted(["published", "good", ".preview-ledger", ".published"]), r
    assert r["ledger_stems"] == r["published"], r


def test_a_failed_manifest_replace_restores_the_whole_previous_generation():
    """SVG 已换上而 manifest 被锁住：旧 svg + 旧 manifest + 旧账本仍成套，不留备份文件。"""
    r = _drive("second_replace_fails")
    assert r["error"] == "PermissionError", r  # 注入确实生效
    assert r["svg_same"] is True, "新像素配旧命中几何：SVG 没被还原"
    assert r["json_same"] is True
    assert r["files_same"] is True, "账本也必须还是旧的"
    assert r["svg_matches_ledger"] is True and r["seq"] == 1
    assert r["published"] == ["fig"]
    assert not [n for n in r["names_after"] if n.endswith(".part")], r["names_after"]
    # 正对照：锁一释放，重试能发布新一代，三者同代前进
    assert r["retry_changed"] is True and r["retry_seq"] == 2


def test_a_failed_first_publish_leaves_no_half_generation():
    r = _drive("second_replace_fails_first_publish")
    assert r["error"] == "PermissionError", r
    assert r["names_after"] == [], f"首次发布失败后残留: {r['names_after']}"
    assert r["published"] == []


@pytest.mark.parametrize("scenario", ["restore_also_fails", "restore_and_removal_fail"])
def test_a_failed_restore_invalidates_the_stem_instead_of_serving_a_mismatch(scenario):
    """发布失败且还原也失败：读者只看到「无预览」，备份留着，账本不含它；下次发布恢复。"""
    r = _drive(scenario)
    assert r["error"] == "PreviewInvalidated" and r["is_invalidated"] is True, r
    assert r["protocol_code"] is None, "标记写成了：不是致命，会话可继续用"
    assert r["usable_svg_exists"] is False and r["usable_json_exists"] is False, r
    assert r["published"] == [] and r["ledger_stems"] == [], r
    assert r["backups"], f"还原失败不该删备份: {r}"
    if scenario == "restore_and_removal_fail":
        assert r["marker"] is True, "删不掉也隔离不了时必须靠标记兜底"
    # 恢复
    assert r["after_published"] == ["fig"] and r["after_usable_svg"] is True, r
    assert not [
        n for n in r["after_names"] if n.endswith(".part") or n.endswith("preview-invalid")
    ], r
    assert r["after_seq"] == 2, r


def test_marker_write_failure_is_fatal_not_swallowed():
    """还原、删除 / 改名、标记写入**全部**失败：不能静默留下错配，抛带协议码的致命错误。

    磁盘上此刻确实是错配的（新 SVG 配旧 manifest）——本层已无法在磁盘层面隐藏，
    所以必须升级成 `PreviewPoisoned`（`protocol_code == "preview_poisoned"`），由父进程判死会话。
    解锁后重发布仍恢复（对照：错配只在升级路径上被拦，不是永久瘫痪）。
    """
    r = _drive("marker_also_fails")
    assert r["error"] == "PreviewPoisoned" and r["is_invalidated"] is True, r
    assert r["protocol_code"] == "preview_poisoned", r
    assert r["marker"] is False, "注入确实生效：标记没写成"
    assert r["published"] == [] and r["ledger_stems"] == [], "账本仍要摘掉该 stem"
    assert r["after_published"] == ["fig"] and r["after_usable_svg"] is True, r


def test_a_retry_whose_ledger_write_fails_keeps_the_stem_invalid():
    """已作废的 stem 重试发布，记账那步失败：失效标记必须仍在、读方仍是「无预览」（Codex #958）。
    账本记成之后才撤标记，所以失败时旧标记原样还在。"""
    r = _drive("retry_ledger_fails_keeps_marker")
    assert r["error"] == "OSError", r
    assert r["marker"] is True and r["usable_svg_exists"] is False, r


def test_a_retry_that_cannot_re_establish_the_marker_is_fatal():
    """重试失败、还原回此前作废的那一代、标记却写不回去：读方失去保证，升级为致命 `preview_poisoned`。"""
    r = _drive("retry_ledger_and_remark_fail")
    assert r["error"] == "PreviewPoisoned" and r["protocol_code"] == "preview_poisoned", r


# ---- app 侧（读方进程）：致命码判死会话，且读入口不再给出错配文件 -----------------
def _poisoned_error_resp():
    return {
        "ok": False,
        "error": {"code": "preview_poisoned", "message": "x", "traceback": "", "retryable": False},
    }


def test_app_side_poisoned_code_kills_the_session_and_blinds_every_reader(tmp_path, monkeypatch):
    """`preview_poisoned` → 会话判死（下次 `get()` 重建）+ `out_dir` 记入读方作废表：
    `/api/engine/svg`、`svg_path`、`usable_preview_path` 都回「无预览」；新一代 build 成功才解除。
    """
    from tavotto import app as m
    from tavotto.engine import figcapture

    out = tmp_path / "out"
    out.mkdir()
    (out / "fig.svg").write_text("<svg data-new='1'/>", encoding="utf-8")
    (out / "fig.json").write_text("{}", encoding="utf-8")  # 旧 manifest（错配的另一半）

    killed = []

    class _Proc:
        def kill(self):
            killed.append(1)

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    w = object.__new__(pool.EngineWorker)
    w.script_name = "fit.py"
    w.out_dir = out
    w.log_path = tmp_path / "worker.log"
    w._dead = False
    w.proc = _Proc()
    w.built = True
    assert w.svg_path("fig").exists()  # 对照：作废前读得到

    err = w._error_of(_poisoned_error_resp())
    assert err.code == "preview_poisoned"
    assert w._dead is True and not w.alive() and killed == [1]

    assert not w.svg_path("fig").exists()
    assert not Path(figcapture.usable_preview_path(out, "fig", ".json")).exists()

    # 真实读取入口：Flask /api/engine/svg（worker 假装还在，证明读方自己拦得住）
    m.app.config["TESTING"] = True
    monkeypatch.setattr(m, "_engine_worker", lambda rel_id: (w, "fig"))
    monkeypatch.setattr(m, "_refuse_artifact_policy", lambda *a, **k: None)
    resp = m.app.test_client().get("/api/engine/svg?id=fig.py")
    assert resp.status_code == 404

    # 新一代会话 build 成功之后解除，读得到新发布的文件
    figcapture.clear_preview_poison(out)
    resp = m.app.test_client().get("/api/engine/svg?id=fig.py")
    assert resp.status_code == 200 and b"data-new" in resp.data
    resp.close()


def test_workerd_side_poisoned_code_is_fatal_and_blinds_readers(tmp_path):
    from tavotto.engine import figcapture, workerd_client

    assert figcapture.PREVIEW_POISONED_CODE in pool._FATAL_CODES
    out = tmp_path / "out"
    out.mkdir()
    (out / "fig.svg").write_text("<svg/>", encoding="utf-8")
    w = object.__new__(pool.WorkerdWorker)
    w.script_name = "fit.py"
    w.out_dir = out
    w._dead = False
    w.log_path = tmp_path / "worker.log"
    exc = workerd_client.WorkerdError("poisoned", code="preview_poisoned")
    w._to_worker_error(exc)
    assert w._dead is True and w.svg_path("fig").exists() is False
    figcapture.clear_preview_poison(out)


def test_native_session_poisoned_code_poisons_transport_and_readers(tmp_path):
    from tavotto.engine import figcapture, nativesession

    out = tmp_path / "out"
    out.mkdir()
    (out / "fig.svg").write_text("<svg/>", encoding="utf-8")

    class _Transport:
        poison = None

        def request(self, obj, **kw):
            raise pool.WorkerError("x", code="preview_poisoned")

    s = object.__new__(nativesession.NativeSession)
    s.out_dir = out
    s.transport = _Transport()
    s.state = nativesession.BARRIER
    s.generation, s.rev = 1, 1
    with pytest.raises(pool.WorkerError):
        s._request({"cmd": "render"}, 1.0)
    assert s.transport.poison is not None, "既有的 poison 机制：之后每条请求都被拒"
    assert s.svg_path("fig").exists() is False
    figcapture.clear_preview_poison(out)
