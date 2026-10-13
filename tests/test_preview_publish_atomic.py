"""预览发布是「同一代整套切换」：逐图隔离 + 不可变代目录 + 账本指针（P01b → P08a，EX04/EX12，AC-05）。

历史：`figsession.render` 曾直接开正式 `<stem>.svg` 写，画到一半抛了就留下半份 XML，既被 `runcli`
按 `.svg` 后缀数成「完成了一张图」，也覆盖了上一版有效预览（P01b）。P01b 之后 SVG 与 manifest 是
先后两次 `os.replace`，并发读可能落在两次之间（新像素配旧点击几何）；一张图的额外 SVG 渲染失败还会
在首个 barrier 前拖垮同批所有图（EX12，P08a）。

现在：

* 每次发布写一个**不可变**的 `.gen-<序号>/<stem>.svg|json`，账本里该 stem 的记录就是指针，
  切换它（一次 `os.replace`）是唯一提交点——读方经 `usable_preview_generation` 一次解出的 SVG /
  manifest / 完成记录出自同一次发布；
* 任何一步失败（含账本存在但读不出来）：新代目录撤掉，账本不动，上一版预览整套原封不动；
* 一张图渲染失败只属于这张图：`failures` 记事实，健康图保持活对象、能改能导出。

这里在**引擎这一层**钉住事实（真 Matplotlib，驱动脚本跑在带 matplotlib 的解释器里）。CLI 端到端的
两图一坏在 `tests/native/test_run_cli_integration.py`。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from tavotto.engine import figcapture, pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None
needs_mpl = pytest.mark.skipif(WORKER_PY is None, reason="matplotlib worker unavailable")

ENGINE_DIR = str(Path(__file__).resolve().parents[1] / "src" / "tavotto" / "engine")

DRIVER = r"""
import builtins, json, os, sys, tempfile
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


def usable(tmp, stem, suffix):
    return Path(figcapture.usable_preview_path(tmp, stem, suffix))


def ledger(tmp):
    return json.loads((Path(tmp) / figcapture.PREVIEW_LEDGER).read_text(encoding="utf-8"))


def facts(out):
    out = Path(out)
    return {
        "published": sorted(figcapture.published_preview_stems(out)),
        "top_level_svgs": sorted(p.name for p in out.glob("*.svg")),
        "parts": sorted(str(p.relative_to(out)) for p in out.rglob("*.part")),
        "gens": sorted(p.name for p in out.glob(".gen-*")),
    }


def wellformed(path):
    import xml.etree.ElementTree as ET
    try:
        ET.parse(path)
        return True
    except (ET.ParseError, OSError):
        return False


def edit_and_export(sess, tmp, stem, title):
    # 真的改（文字内容）并导出：证明这是活对象，不是一份静态残留
    man = sess.manifest(stem)
    gid = next(
        el["gid"]
        for el in man["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == title
    )
    patches = [{"gid": gid, "prop": "text", "value": "Edited"}]
    sess.do_render(stem, patches)
    edited = json.loads(usable(tmp, stem, ".json").read_text(encoding="utf-8"))
    has_edit = any(
        f["value"] == "Edited"
        for el in edited["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text"
    )
    pdf = Path(tmp) / "export" / f"{stem}.pdf"
    sess.do_export(stem, patches, str(pdf), "pdf", 100)
    return {"edit_in_manifest": has_edit, "export_pdf": pdf.read_bytes()[:5] == b"%PDF-"}


scenario = sys.argv[2]
out = {}
with tempfile.TemporaryDirectory() as tmp:
    sess = LiveFigureSession(tmp, preview_dpi=72)
    if scenario in ("good_then_bad", "bad_then_good"):
        pair = [("good", good("G")), ("bad", bad())]
        if scenario == "bad_then_good":
            pair.reverse()
        for stem, fig in pair:
            sess.add_figure(stem, fig, figcapture.SOURCE_SAVEFIG)
        try:
            sess.instrument_all()
        except Exception as exc:
            out["error"] = type(exc).__name__
        out.update(facts(tmp))
        out["live"] = sorted(sess.states)
        out["failed"] = sorted(sess.failures)
        out["failure_code"] = sess.failures.get("bad", {}).get("code")
        out["failure_message"] = sess.failures.get("bad", {}).get("message")
        out["summary_stems"] = sorted(sess.stems_summary()["stems"])
        out["summary_failed"] = [f["stem"] for f in sess.stems_summary().get("failed_figures", [])]
        out["capture"] = sorted(sess.capture)
        out["good_wellformed"] = wellformed(usable(tmp, "good", ".svg"))
        out["bad_has_preview"] = usable(tmp, "bad", ".svg").exists()
        out["restore_owed"] = sess.failed_restore_owed()
        out["facts"] = {f["stem"]: [f["state"], f["capabilities"], f["generation"]] for f in sess.figure_facts()}
        out["ledger_caps"] = ledger(tmp)["stems"]["good"]["capabilities"]
        out.update(edit_and_export(sess, tmp, "good", "G"))
    elif scenario == "only_bad":
        sess.add_figure("bad", bad(), figcapture.SOURCE_SAVEFIG)
        try:
            sess.instrument_all()
        except Exception as exc:
            out["error"] = type(exc).__name__
        out.update(facts(tmp))
        out["failed"] = sorted(sess.failures)
        out["live"] = sorted(sess.states)
        # safe worker 的口径：一张活图都没有时，把第一个失败原样抛出
        strict = LiveFigureSession(tmp + "-strict", preview_dpi=72)
        strict.add_figure("bad", bad(), figcapture.SOURCE_SAVEFIG)
        try:
            strict.instrument_all(raise_if_none_live=True)
        except Exception as exc:
            out["strict_error"] = type(exc).__name__
        # ...但有健康图时不抛
        mixed = LiveFigureSession(tmp + "-mixed", preview_dpi=72)
        mixed.add_figure("bad", bad(), figcapture.SOURCE_SAVEFIG)
        mixed.add_figure("good", good("G"), figcapture.SOURCE_SAVEFIG)
        mixed.instrument_all(raise_if_none_live=True)
        out["mixed_live"] = sorted(mixed.states)
    elif scenario == "retry_only_after_leaving_the_barrier":
        f = bad()
        sess.add_figure("fig", f, figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        out["failed_first"] = sorted(sess.failures)
        for a in list(f.axes[0].artists):  # 脚本在屏障之间把它修好了
            a.remove()
        sess.instrument_all()  # 同一个屏障里的第二次调用：不重画一张必败的图
        out["failed_same_barrier"] = sorted(sess.failures)
        sess.allow_retry_failed()
        sess.instrument_all()
        out["live_after"] = sorted(sess.states)
        out["failed_after"] = sorted(sess.failures)
        out["published"] = figcapture.published_preview_stems(tmp)
    elif scenario == "strict_failure_retires_the_capture":
        sess.add_figure("fig", bad(), figcapture.SOURCE_SAVEFIG)
        try:
            sess.instrument_all(raise_if_none_live=True)
        except Exception as exc:
            out["first_error"] = type(exc).__name__
        out["left"] = [sorted(sess.capture), sorted(sess.failures), sorted(sess.states)]
        # worker 被复用、脚本重跑：新 Figure 必须被收下并真的发布，而不是"零活图的成功"
        out["rerun_accepted"] = sess.add_figure("fig", good("v2"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all(raise_if_none_live=True)
        out["live_after_rerun"] = sorted(sess.states)
        out["published"] = figcapture.published_preview_stems(tmp)
    elif scenario == "failure_hides_the_previous_runs_preview":
        # 上一轮运行同一个 out_dir 里发布过 fig 与 keep；本轮 fig 捕获失败、keep 正常
        prev = LiveFigureSession(tmp, preview_dpi=72)
        prev.add_figure("fig", good("last-run"), figcapture.SOURCE_SAVEFIG)
        prev.add_figure("keep", good("K"), figcapture.SOURCE_SAVEFIG)
        prev.instrument_all()
        out["before"] = sorted(figcapture.published_preview_stems(tmp))
        sess.add_figure("fig", bad(), figcapture.SOURCE_SAVEFIG)
        sess.add_figure("keep2", good("K2"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        out["failed"] = sorted(sess.failures)
        out["fig_svg_visible"] = usable(tmp, "fig", ".svg").exists()
        out["fig_json_visible"] = usable(tmp, "fig", ".json").exists()
        out["generation"] = figcapture.usable_preview_generation(tmp, "fig")
        out["published"] = sorted(figcapture.published_preview_stems(tmp))
        out["keep_wellformed"] = wellformed(usable(tmp, "keep", ".svg"))
    elif scenario == "failure_with_unreadable_ledger_still_hides_the_preview":
        prev = LiveFigureSession(tmp, preview_dpi=72)
        prev.add_figure("fig", good("last-run"), figcapture.SOURCE_SAVEFIG)
        prev.add_figure("keep", good("K"), figcapture.SOURCE_SAVEFIG)
        prev.instrument_all()
        ledger_path = Path(tmp) / figcapture.PREVIEW_LEDGER
        saved = ledger_path.read_bytes()
        ledger_path.write_bytes(b"{garbled")  # 扫描器瞬时改写：存在但读不出来
        sess.add_figure("fig", bad(), figcapture.SOURCE_SAVEFIG)
        try:
            sess.instrument_all()
            out["error"] = None
        except Exception as exc:
            out["error"] = type(exc).__name__
        ledger_path.write_bytes(saved)  # 账本恢复可读
        out["failed"] = sorted(sess.failures)
        out["fig_visible"] = usable(tmp, "fig", ".svg").exists()
        out["keep_visible"] = usable(tmp, "keep", ".svg").exists()
        out["fig_counted"] = "fig" in figcapture.published_preview_stems(tmp)
    elif scenario == "failed_figure_that_cannot_be_restored":
        sess.add_figure("bad", bad(), figcapture.SOURCE_SAVEFIG)
        import overrides
        real_apply = overrides.apply
        def broken(state, patches):
            raise RuntimeError("cannot restore")
        overrides.apply = broken
        try:
            sess.instrument_all()
            out["owed"] = sess.failed_restore_owed()
        finally:
            overrides.apply = real_apply
        out["owed_after"] = sess.failed_restore_owed()
    elif scenario == "rerender_failure_keeps_old":
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        old = (usable(tmp, "fig", ".svg").read_bytes(), usable(tmp, "fig", ".json").read_bytes())
        old_ledger = (Path(tmp) / figcapture.PREVIEW_LEDGER).read_bytes()
        out["before"] = facts(tmp)
        sess.states["fig"].fig.axes[0].add_artist(AggOnly())  # 之后的预览必然失败
        try:
            sess.render("fig")
        except Exception as exc:
            out["error"] = type(exc).__name__
        out["after"] = facts(tmp)
        out["pair_unchanged"] = (usable(tmp, "fig", ".svg").read_bytes(), usable(tmp, "fig", ".json").read_bytes()) == old
        out["ledger_unchanged"] = (Path(tmp) / figcapture.PREVIEW_LEDGER).read_bytes() == old_ledger
        out["old_wellformed"] = wellformed(usable(tmp, "fig", ".svg"))
    elif scenario == "rerender_success_advances":
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        first = usable(tmp, "fig", ".svg").read_bytes()
        out["gen_before"] = figcapture.usable_preview_generation(tmp, "fig")[0]
        sess.states["fig"].fig.axes[0].set_title("a considerably longer second title")
        sess.render("fig")
        gen = figcapture.usable_preview_generation(tmp, "fig")
        out["changed"] = usable(tmp, "fig", ".svg").read_bytes() != first
        out["published"] = facts(tmp)["published"]
        out["seq"] = ledger(tmp)["seq"]
        out["gen_after"] = gen[0]
        # 同一代：SVG 与 manifest 在同一个代目录，目录序号就是指针
        out["same_dir"] = os.path.dirname(gen[1]) == os.path.dirname(gen[2])
        out["svg_bytes_match"] = ledger(tmp)["stems"]["fig"]["svg_bytes"] == len(usable(tmp, "fig", ".svg").read_bytes())
    elif scenario == "stem_collides_with_old_ledger_name":
        # savefig("published.png") 的 stem 是 published；点开头的 stem 同理
        names = ["published", "good", ".preview-ledger", ".published"]
        for n in names:
            sess.add_figure(n, good(n), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        out["published"] = sorted(figcapture.published_preview_stems(tmp))
        out["manifests_ok"] = {
            n: json.loads(usable(tmp, n, ".json").read_text(encoding="utf-8")).get("stem") == n
            for n in names
        }
        out["ledger_stems"] = sorted(ledger(tmp)["stems"])
        out["ledger_name"] = figcapture.PREVIEW_LEDGER
        out["ledger_is_figure_like"] = figcapture.PREVIEW_LEDGER.endswith((".svg", ".json", ".part"))
    elif scenario in (
        "manifest_move_fails",
        "manifest_move_fails_first_publish",
        "ledger_commit_fails",
    ):
        first = scenario.endswith("first_publish")
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        if not first:
            sess.instrument_all()
        p = Path(tmp)
        def snap():
            if not (p / figcapture.PREVIEW_LEDGER).exists():
                return None
            return (
                usable(tmp, "fig", ".svg").read_bytes(),
                usable(tmp, "fig", ".json").read_bytes(),
                (p / figcapture.PREVIEW_LEDGER).read_bytes(),
            )
        old = snap()
        if not first:
            sess.states["fig"].fig.axes[0].set_title("a considerably longer second title")
        real_replace = os.replace
        target = figcapture.PREVIEW_LEDGER if scenario == "ledger_commit_fails" else "fig.json"

        def flaky(src, dst, *a, **k):  # Windows 上 manifest / 账本被临时锁住
            if str(dst).endswith(target):
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
        out["snapshot_same"] = snap() == old
        out["names_after"] = sorted(os.listdir(tmp))
        out["facts"] = facts(tmp)
        if not first:
            out["seq"] = ledger(tmp)["seq"]
            out["svg_matches_ledger"] = ledger(tmp)["stems"]["fig"]["svg_bytes"] == len(old[0])
            sess.render("fig")  # 锁释放：能发布新一代，三者同代前进
            out["retry_changed"] = usable(tmp, "fig", ".svg").read_bytes() != old[0]
            out["retry_seq"] = ledger(tmp)["seq"]
            out["retry_gens"] = facts(tmp)["gens"]
    elif scenario in ("ledger_read_fails", "ledger_malformed"):
        # #969：账本存在但读不出来 / 格式坏，不能被当成空账本重建（会把已发布的 a 丢掉）
        sess.add_figure("a", good("A"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        p = Path(tmp)
        before = (p / figcapture.PREVIEW_LEDGER).read_bytes()
        sess.add_figure("b", good("B"), figcapture.SOURCE_SAVEFIG)
        real_open = builtins.open
        if scenario == "ledger_malformed":
            (p / figcapture.PREVIEW_LEDGER).write_text("{not json", encoding="utf-8")
            before = (p / figcapture.PREVIEW_LEDGER).read_bytes()
        else:
            def flaky_open(file, mode="r", *a, **k):  # 扫描器瞬时拒读
                if str(file).endswith(figcapture.PREVIEW_LEDGER) and "r" in mode:
                    raise PermissionError(13, "scanner", str(file))
                return real_open(file, mode, *a, **k)
            builtins.open = flaky_open
        try:
            try:
                sess.instrument_all()
            except Exception as exc:
                out["error"] = type(exc).__name__
                out["is_ledger_error"] = isinstance(exc, figcapture.PreviewLedgerUnreadable)
        finally:
            builtins.open = real_open
        out["ledger_unchanged"] = (p / figcapture.PREVIEW_LEDGER).read_bytes() == before
        out["gens"] = facts(tmp)["gens"]
        out["parts"] = facts(tmp)["parts"]
        if scenario == "ledger_read_fails":
            out["published_after_failure"] = facts(tmp)["published"]
            out["a_still_readable"] = wellformed(usable(tmp, "a", ".svg"))
            # 读恢复后：b 被收下重试发布，a 的记录还在，两张都计
            sess.add_figure("b", good("B2"), figcapture.SOURCE_SAVEFIG)
            sess.instrument_all()
            out["published_after_retry"] = facts(tmp)["published"]
    elif scenario in ("first_publish_ledger_glitch_then_retry", "batch_glitch_rolls_back_every_fresh_capture"):
        real_write = figcapture._write_preview_ledger
        calls = []
        fail_at = 1 if scenario.startswith("first") else 2

        def glitch(*a, **k):
            calls.append(1)
            if len(calls) == fail_at:
                raise PermissionError(13, "ledger locked")
            return real_write(*a, **k)

        figcapture._write_preview_ledger = glitch
        try:
            if scenario.startswith("first"):
                sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
                try:
                    sess.instrument_all()
                except Exception as exc:
                    out["first_error"] = type(exc).__name__
                out["registered_after_failure"] = "fig" in sess.states
                # pool 复用这个 worker 重跑脚本：重跑画出的是新 Figure，必须被收下并发布（不是失败那次的旧图）
                second = good("v2")
                out["rerun_accepted"] = sess.add_figure("fig", second, figcapture.SOURCE_SAVEFIG)
                sess.instrument_all()  # 锁已解开：重试要真的重新发布这张图
                out["published_is_rerun"] = sess.capture.get("fig") is second
                out["summary_stems"] = sorted(sess.stems_summary()["stems"])
                out["published"] = figcapture.published_preview_stems(tmp)
            else:
                sess.add_figure("a", good("a1"), figcapture.SOURCE_SAVEFIG)
                sess.add_figure("b", good("b1"), figcapture.SOURCE_SAVEFIG)
                try:
                    sess.instrument_all()
                except Exception as exc:
                    out["first_error"] = type(exc).__name__
                out["left_states"] = sorted(sess.states)
                out["left_captures"] = sorted(sess.capture)
                a2, b2 = good("a2"), good("b2")
                out["rerun_accepted"] = [
                    sess.add_figure("a", a2, figcapture.SOURCE_SAVEFIG),
                    sess.add_figure("b", b2, figcapture.SOURCE_SAVEFIG),
                ]
                sess.instrument_all()
                out["published_are_rerun"] = sess.capture.get("a") is a2 and sess.capture.get("b") is b2
        finally:
            figcapture._write_preview_ledger = real_write
    elif scenario == "stale_and_truncated":
        p = Path(tmp)
        # 上一次运行残留：有完整 SVG、没有账本
        (p / "stale.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        out["stale_only"] = facts(tmp)["published"]
        out["stale_usable"] = usable(tmp, "stale", ".svg").exists()
        # 本次发布成功之后，文件被截断：账本字节数对不上，不再算
        sess.add_figure("fig", good("v1"), figcapture.SOURCE_SAVEFIG)
        sess.instrument_all()
        out["fresh"] = facts(tmp)["published"]
        svg = usable(tmp, "fig", ".svg")
        raw = svg.read_bytes()
        svg.write_bytes(raw[: len(raw) // 2])
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


def _publish(out, stem, svg=b"<svg xmlns='http://www.w3.org/2000/svg'/>", manifest=None, caps=()):
    """不经 Matplotlib 的发布夹具：走与引擎相同的 `publish_preview`。"""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    svg_part, man_part = out / f".{stem}.svg.part", out / f".{stem}.json.part"
    svg_part.write_bytes(svg)
    man_part.write_text(json.dumps(manifest if manifest is not None else {"stem": stem}))
    return figcapture.publish_preview(out, stem, svg_part, man_part, len(svg), caps)


# ===========================================================================
# 逐图隔离（P08a，EX04/EX12，AC-05）
# ===========================================================================
@needs_mpl
@pytest.mark.parametrize("order", ["good_then_bad", "bad_then_good"])
def test_one_bad_figure_does_not_take_the_healthy_one_down(order):
    """两图一坏，**两种顺序**：恰好 1 张健康图是活对象且完整发布；坏图记明确失败事实；
    健康图真的能改、能导出——不是「全部丢弃」，也不是「只 catch 了异常」。"""
    r = _drive(order)
    assert r.get("error") is None, f"一张图的预览失败拖垮了整批: {r}"
    assert r["live"] == ["good"] and r["summary_stems"] == ["good"], r
    assert r["published"] == ["good"], r
    assert r["good_wellformed"] is True
    # 坏图：明确的失败事实，活对象仍留在捕获表（原产物事实照旧可查），但没有预览、不可编辑
    assert r["failed"] == ["bad"] and r["summary_failed"] == ["bad"], r
    assert r["failure_code"] == "preview_capture_failed"
    assert "AttributeError" in r["failure_message"]
    assert sorted(r["capture"]) == ["bad", "good"]
    assert r["bad_has_preview"] is False
    # 不留半份：正式名下没有 SVG、没有 .part 临时件
    assert r["top_level_svgs"] == [] and r["parts"] == [], r
    # 能力只按实际完成的工作声明
    assert r["facts"]["good"][:2] == ["live", ["preview", "live_edit"]]
    assert r["facts"]["bad"] == ["failed", [], None], r["facts"]
    assert r["ledger_caps"] == ["preview", "live_edit"]
    # 健康图真实可用：改了文字、导出了 PDF
    assert r["edit_in_manifest"] is True and r["export_pdf"] is True, r
    # 失败图的 Figure 状态核对过恢复，不会因它挡住 continue
    assert r["restore_owed"] == []


@needs_mpl
def test_when_nothing_is_live_isolation_is_reported_not_swallowed():
    """只有坏图：会话不抛（native 的屏障仍要能服务），失败事实在；
    safe worker 的口径（`raise_if_none_live`）则把第一个失败原样抛出，build 仍然失败；
    有健康图时不抛。"""
    r = _drive("only_bad")
    assert r.get("error") is None and r["failed"] == ["bad"] and r["live"] == [], r
    assert r["published"] == [] and r["top_level_svgs"] == [] and r["parts"] == [], r
    assert r["strict_error"] == "AttributeError", r
    assert r["mixed_live"] == ["good"], r


@needs_mpl
def test_a_strict_build_failure_retires_the_isolated_capture():
    """Codex #978：safe worker 只剩坏图而 build 失败后，worker 被复用重跑脚本——失败的 stem 不能还占着捕获表，
    否则新 Figure 被拒收、第二次 build 变成零活图的"成功"。"""
    r = _drive("strict_failure_retires_the_capture")
    assert r["first_error"] == "AttributeError", r
    assert r["left"] == [[], [], []], r
    assert r["rerun_accepted"] is True and r["live_after_rerun"] == ["fig"], r
    assert r["published"] == ["fig"], r


@needs_mpl
def test_a_failed_figure_is_retried_only_after_the_barrier_is_left():
    r = _drive("retry_only_after_leaving_the_barrier")
    assert r["failed_first"] == ["fig"]
    assert r["failed_same_barrier"] == ["fig"], "同一个屏障里重复调用不该重画必败的图"
    assert r["live_after"] == ["fig"] and r["failed_after"] == [], r
    assert r["published"] == ["fig"]


@needs_mpl
def test_a_figure_that_fails_this_run_does_not_show_last_runs_preview():
    """Codex #978 P1：本次运行捕获失败的图，读方不能再把上次发布的旧 SVG 当本次结果。
    与"发布写失败时旧预览保持完整"不同——那是本次没产出新版而旧版仍是同一次运行的有效版；
    这里是这张图本次已确认失败，对外就是"失败 + 无预览"。其它 stem 不受影响。"""
    r = _drive("failure_hides_the_previous_runs_preview")
    assert r["before"] == ["fig", "keep"], r
    assert r["failed"] == ["fig"], r
    assert r["fig_svg_visible"] is False and r["fig_json_visible"] is False, r
    assert r["generation"] is None
    assert r["published"] == ["keep", "keep2"], r
    assert r["keep_wellformed"] is True


@needs_mpl
def test_hiding_the_previous_preview_fails_closed_when_the_ledger_is_unreadable():
    """Codex #978 P1：隔离时账本读不出来 ≠ 没有指针。必须落失效标记（或抛 PreviewPoisoned），
    账本恢复可读后这张图仍然不可见、不被计数；其它 stem 不受影响。"""
    r = _drive("failure_with_unreadable_ledger_still_hides_the_preview")
    assert r["error"] is None and r["failed"] == ["fig"], r
    assert r["fig_visible"] is False and r["fig_counted"] is False, r
    assert r["keep_visible"] is True, r


def test_poison_floor_is_pessimistic_when_the_ledger_is_unreadable(tmp_path):
    """账本读不出来时作废的序号下限不能当 0：否则账本恢复后旧一代被当成"作废之后重新发布"而放行。"""
    out = tmp_path / "out"
    _publish(out, "old")
    ledger = out / figcapture.PREVIEW_LEDGER
    good = ledger.read_bytes()
    ledger.write_bytes(b"{garbled")
    figcapture.poison_preview_dir(out)
    try:
        ledger.write_bytes(good)
        assert figcapture.clear_preview_poison(out) is True
        assert figcapture.usable_preview_generation(out, "old") is None
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


@needs_mpl
def test_a_failed_figure_whose_baseline_cannot_be_restored_blocks_continue():
    """恢复编辑基线失败仍阻止 continue：失败图的还原欠账进 `failed_restore_owed`（屏障据此拒绝放行）。"""
    r = _drive("failed_figure_that_cannot_be_restored")
    assert r["owed"] == ["bad"], r
    assert r["owed_after"] == [], "还原恢复正常后欠账应当清掉"


# ===========================================================================
# 完成事实：半文件 / 旧文件 / 残留都不计（P01b 原有主张，随新布局保留）
# ===========================================================================
@needs_mpl
def test_a_failed_republish_keeps_the_previous_valid_preview():
    r = _drive("rerender_failure_keeps_old")
    assert r["error"] == "AttributeError", r
    assert r["before"]["published"] == ["fig"]
    assert r["old_wellformed"] is True
    assert r["pair_unchanged"] is True, "失败的发布改写了上一版有效预览"
    assert r["ledger_unchanged"] is True
    assert r["after"]["published"] == ["fig"], "上一版仍是已发布的完整预览"
    assert r["after"]["parts"] == [] and r["after"]["gens"] == r["before"]["gens"]


@needs_mpl
def test_a_successful_republish_switches_the_whole_generation():
    """正对照：不是「什么都不让写」——成功的重发布换了整代，SVG / manifest / 完成记录同在新代目录。"""
    r = _drive("rerender_success_advances")
    assert r["changed"] is True
    assert r["published"] == ["fig"]
    assert r["seq"] == 2 and r["gen_before"] == 1 and r["gen_after"] == 2
    assert r["same_dir"] is True, "SVG 与 manifest 必须出自同一个代目录"
    assert r["svg_bytes_match"] is True, "完成记录的字节数必须对得上这一代的 SVG"


@needs_mpl
def test_stale_and_truncated_svgs_never_count():
    r = _drive("stale_and_truncated")
    assert r["stale_only"] == [], "没有账本的残留 SVG 被数进去了"
    assert r["stale_usable"] is False, "没有账本指针的残留文件不该被读方当成预览"
    assert r["fresh"] == ["fig"], "正对照：本次完整发布的必须算"
    assert r["truncated"] == [], "被截断的 SVG（字节数与账本不符）仍被算作完成"


@needs_mpl
def test_a_user_stem_cannot_collide_with_the_ledger():
    """`savefig("published.png")` 曾把 manifest 写在 published.json，再被同名账本覆盖。"""
    r = _drive("stem_collides_with_old_ledger_name")
    assert r["ledger_is_figure_like"] is False, r["ledger_name"]
    assert all(r["manifests_ok"].values()), f"某个 stem 的 <stem>.json 不是 manifest: {r}"
    assert r["published"] == sorted(["published", "good", ".preview-ledger", ".published"]), r
    assert r["ledger_stems"] == r["published"], r


# ===========================================================================
# 写失败：旧预览整套原封不动（#969 原子指针）
# ===========================================================================
@needs_mpl
@pytest.mark.parametrize("scenario", ["manifest_move_fails", "ledger_commit_fails"])
def test_a_write_failure_before_the_commit_keeps_the_whole_previous_generation(scenario):
    """manifest 落代目录失败 / 账本提交失败：旧 SVG + 旧 manifest + 旧账本逐字节不变，
    新代目录与临时件都清掉；锁一释放，重试发布新一代。（旧布局下这里要「还原」，现在根本没动它。）"""
    r = _drive(scenario)
    assert r["error"] == "PermissionError", r  # 注入确实生效
    assert r["snapshot_same"] is True, "旧预览（SVG / manifest / 账本）被改动了"
    assert r["svg_matches_ledger"] is True and r["seq"] == 1
    assert r["facts"]["published"] == ["fig"] and r["facts"]["gens"] == [".gen-1"], r
    assert r["facts"]["parts"] == [], r
    assert r["retry_changed"] is True and r["retry_seq"] == 2
    assert r["retry_gens"] == [".gen-1", ".gen-2"], (
        "旧代目录在宽限内保留，读方手里的旧路径不会撞上刚删的文件"
    )


@needs_mpl
def test_a_failed_first_publish_leaves_no_half_generation():
    r = _drive("manifest_move_fails_first_publish")
    assert r["error"] == "PermissionError", r
    leftovers = [n for n in r["names_after"] if n != figcapture.PREVIEW_LEDGER + ".lock"]
    assert leftovers == [], f"首次发布失败后残留: {r['names_after']}"
    assert r["facts"]["published"] == []


@needs_mpl
@pytest.mark.parametrize("scenario", ["ledger_read_fails", "ledger_malformed"])
def test_an_unreadable_ledger_is_not_rebuilt_as_an_empty_one(scenario):
    """#969：账本存在但读不出来（扫描器瞬时拒读）/ 格式坏——发布失败并如实报错，
    **不**用「只有当前 stem」重建账本（那会静默丢掉第一张图的完成记录）。"""
    r = _drive(scenario)
    assert r["is_ledger_error"] is True, r
    assert r["ledger_unchanged"] is True, "账本被重建 / 覆盖了"
    assert r["gens"] == [".gen-1"] and r["parts"] == [], r
    if scenario == "ledger_read_fails":
        assert r["published_after_failure"] == ["a"], "第一张图的完成记录丢了"
        assert r["a_still_readable"] is True
        assert r["published_after_retry"] == ["a", "b"], (
            "读恢复后两张都应计（figures_captured 正确）"
        )


@needs_mpl
def test_a_missing_ledger_is_a_first_publish_not_an_error(tmp_path):
    """对照：账本**不存在**就是首次发布（与「存在但读不了」是两回事）。"""
    assert not (tmp_path / figcapture.PREVIEW_LEDGER).exists()
    assert _publish(tmp_path, "a") == 1
    assert figcapture.published_preview_stems(tmp_path) == ["a"]


@needs_mpl
def test_a_transient_first_publish_failure_can_be_retried():
    """首次发布碰上瞬时账本错误：没出过预览的状态要撤掉，重试时重新 instrument 并发布，
    `stems_summary` 不得因缺缓存 manifest 永久 KeyError（Codex #958）。"""
    r = _drive("first_publish_ledger_glitch_then_retry")
    assert r["first_error"] == "PermissionError", r
    assert r["registered_after_failure"] is False, r
    assert r["rerun_accepted"] is True and r["published_is_rerun"] is True, r
    assert r["summary_stems"] == ["fig"] and r["published"] == ["fig"], r


@needs_mpl
def test_a_failed_batch_rolls_back_every_fresh_capture():
    """同一批新图里第二张**发布**失败（产物目录 / 账本的问题，不属于任何一张图）：整批撤掉
    （含已发布的第一张），重跑的新 Figure 全部被收下并发布（Codex #958）。"""
    r = _drive("batch_glitch_rolls_back_every_fresh_capture")
    assert r["first_error"] == "PermissionError", r
    assert r["left_states"] == [] and r["left_captures"] == [], r
    assert r["rerun_accepted"] == [True, True] and r["published_are_rerun"] is True, r


# ===========================================================================
# 同代读取：并发读方看不到半套（#969 原子指针）
# ===========================================================================
def test_a_concurrent_reader_never_sees_a_half_generation(tmp_path):
    """写方连续发布 N 代，每代的 SVG 与 manifest 都带同一个代号；读方循环经 `usable_preview_generation`
    一次解出一对再读——任何时刻读到的两份都必须是同一代（旧布局先后替换两个文件，读窗口里会错配）。"""
    stop = threading.Event()
    torn: list = []
    reads = [0]

    def reader():
        while not stop.is_set():
            gen = figcapture.usable_preview_generation(tmp_path, "fig")
            if gen is None:
                continue
            _, svg, man = gen
            try:
                svg_gen = Path(svg).read_bytes().decode()
                man_gen = json.loads(Path(man).read_text(encoding="utf-8"))["gen"]
            except (OSError, ValueError):
                torn.append("unreadable")  # 解出的指针指向的文件必须在
                continue
            reads[0] += 1
            if svg_gen != f"svg-{man_gen}":
                torn.append((svg_gen, man_gen))

    _publish(tmp_path, "fig", svg=b"svg-0", manifest={"gen": 0})
    t = threading.Thread(target=reader)
    t.start()
    try:
        for n in range(1, 120):
            _publish(tmp_path, "fig", svg=f"svg-{n}".encode(), manifest={"gen": n})
    finally:
        stop.set()
        t.join(timeout=30)
    assert not t.is_alive()
    assert reads[0] > 0
    assert torn == [], f"读到了半套结果: {torn[:3]}"


def test_old_generations_are_pruned_but_the_current_one_never(tmp_path, monkeypatch):
    monkeypatch.setattr(figcapture, "GENERATION_GRACE_SECONDS", 0.0)
    for n in range(10):
        _publish(tmp_path, "a", manifest={"gen": n})
    _publish(tmp_path, "b")
    gens = sorted(int(p.name.split("-")[1]) for p in tmp_path.glob(".gen-*"))
    ledger = json.loads((tmp_path / figcapture.PREVIEW_LEDGER).read_text(encoding="utf-8"))
    current = {rec["seq"] for rec in ledger["stems"].values()}
    assert current <= set(gens), "当前指针指向的代目录不能被清"
    assert len(gens) <= figcapture.GENERATION_GRACE + len(current), gens
    assert figcapture.published_preview_stems(tmp_path) == ["a", "b"]


def test_a_superseded_generation_survives_the_time_grace_for_an_in_flight_reader(tmp_path):
    """读方刚解出旧路径、被调度走了一阵：即使发布已落后好几个序号，旧代目录在秒级窗口内仍在。"""
    _publish(tmp_path, "fig", svg=b"svg-0", manifest={"gen": 0})
    stale = figcapture.usable_preview_generation(tmp_path, "fig")
    for n in range(1, 3 * figcapture.GENERATION_GRACE):
        _publish(tmp_path, "fig", svg=f"svg-{n}".encode(), manifest={"gen": n})
    assert Path(stale[1]).read_bytes() == b"svg-0", "窗口内旧代文件被删了"


def test_a_reader_that_opens_late_still_gets_one_complete_current_pair(tmp_path, monkeypatch):
    """Codex #978：持有旧路径、拖过宽限才去开的读方会撞上清理。`open_preview_pair` 在解出 → 打开之间撞上清理时
    重新解出，拿到的永远是完整的当前一对；已打开的句柄则把那一代钉住（POSIX）。"""
    monkeypatch.setattr(figcapture, "GENERATION_GRACE_SECONDS", 0.0)
    _publish(tmp_path, "fig", svg=b"svg-0", manifest={"gen": 0})
    _, svg_path, _ = figcapture.usable_preview_generation(tmp_path, "fig")
    seq0, svg_fh, man_fh = figcapture.open_preview_pair(tmp_path, "fig")
    for n in range(1, 4 * figcapture.GENERATION_GRACE):
        _publish(tmp_path, "fig", svg=f"svg-{n}".encode(), manifest={"gen": n})
    current = figcapture.usable_preview_generation(tmp_path, "fig")
    assert Path(svg_path) != Path(current[1]), "探针无效：旧路径应当已不是当前一代"
    if os.name != "nt":
        # POSIX：旧代已被清理，已打开的句柄钉住内容
        assert not Path(svg_path).exists(), "探针无效：旧代应当已被清理"
        assert svg_fh.read() == b"svg-0" and json.loads(man_fh.read())["gen"] == 0
    # Windows：被打开的目录删不掉，旧代可以留存——合同是它不可见（当前指针不指向它）也不计数
    assert figcapture.published_preview_stems(tmp_path) == ["fig"]
    svg_fh.close()
    man_fh.close()
    seq, svg, man = figcapture.open_preview_pair(tmp_path, "fig")
    with svg, man:
        assert svg.read().decode() == f"svg-{json.loads(man.read())['gen']}"
    assert seq > seq0
    assert figcapture.open_preview_pair(tmp_path, "nothing") is None


def test_the_cli_count_comes_from_the_ledger_not_from_svg_files(tmp_path):
    """CLI 的「捕获了几张图」读引擎写的账本：只有 .svg 文件而没有账本记录的，数 0。"""
    from tavotto.engine import runcli

    (tmp_path / "a.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "b.svg").write_text("<svg", encoding="utf-8")  # 半份 XML
    assert runcli._figures_written(str(tmp_path)) == 0
    _publish(tmp_path, "a")
    assert runcli._figures_written(str(tmp_path)) == 1  # 发布过的算，没发布的 b 不算


def test_no_pointer_means_no_preview_even_if_a_file_with_the_old_name_exists(tmp_path):
    (tmp_path / "fig.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "fig.json").write_text("{}", encoding="utf-8")
    assert not os.path.exists(figcapture.usable_preview_path(tmp_path, "fig", ".svg"))
    assert figcapture.usable_preview_generation(tmp_path, "fig") is None


# ===========================================================================
# app 侧（读方进程）：致命码判死会话，且读入口不再给出错配文件
# ===========================================================================
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

    out = tmp_path / "out"
    _publish(out, "fig", svg=b"<svg data-new='1'/>", manifest={})  # 旧 manifest（错配的另一半）

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

    # 新一代 build 成功但**没有**重新发布这个 stem（条件脚本这次没出图）：错配文件不能被放出来（Codex #958）——
    # 读方把作废后没重新发布的旧指针摘掉再解除，读入口看到的是「没有这张图」而不是错配
    assert figcapture.clear_preview_poison(out) is True
    assert figcapture.usable_preview_generation(out, "fig") is None
    resp = m.app.test_client().get("/api/engine/svg?id=fig.py")
    assert resp.status_code == 404
    resp.close()
    figcapture.poison_preview_dir(out)  # 接下来那段：再作废一次，验证重新发布后解除

    # 新一代把它完整重新发布之后（作废之后的账本序号、字节数对得上）才解除，读得到新发布的文件
    _publish(out, "fig", svg=b"<svg data-new='2'/>", manifest={"stem": "fig"})
    assert figcapture.clear_preview_poison(out) is True
    resp = m.app.test_client().get("/api/engine/svg?id=fig.py")
    assert resp.status_code == 200 and b"data-new='2'" in resp.data
    resp.close()


def test_poison_survives_a_build_that_republishes_only_other_stems(tmp_path):
    """作废后新一代只发布了别的 stem：旧 stem 的指针被摘掉后目录随即解除（不能因一张已不存在的图永远「无预览」）。"""
    out = tmp_path / "out"
    _publish(out, "old")
    figcapture.poison_preview_dir(out)
    try:
        _publish(out, "other")
        assert figcapture.clear_preview_poison(out) is True
        assert figcapture.usable_preview_generation(out, "old") is None
        assert figcapture.usable_preview_generation(out, "other") is not None  # 新一代刚发布的不动
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_poison_stays_while_a_stale_preview_cannot_be_dropped(tmp_path, monkeypatch):
    """旧指针摘不掉（账本仍被锁着）：继续作废，宁可「无预览」也不放出错配（Codex #958）。"""
    out = tmp_path / "out"
    _publish(out, "old")
    figcapture.poison_preview_dir(out)
    real_write = figcapture._write_preview_ledger

    def locked(*a, **k):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(figcapture, "_write_preview_ledger", locked)
    try:
        assert figcapture.clear_preview_poison(out) is False
        assert not Path(figcapture.usable_preview_path(out, "old", ".svg")).exists()
        # 锁放开之后，下一次读就重试审计并解除——不是 worker 一辈子「无预览」（Codex #958）
        monkeypatch.setattr(figcapture, "_write_preview_ledger", real_write)
        assert figcapture.preview_dir_poisoned(out) is False
        assert figcapture.usable_preview_generation(out, "old") is None
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_an_audit_does_not_erase_a_newer_poison_that_arrived_meanwhile(tmp_path, monkeypatch):
    """读方审计期间又来了一次更新的 `preview_poisoned`：这次审计没覆盖新那一代，不得把它一并解除（Codex #958）。"""
    out = tmp_path / "out"
    out.mkdir()
    figcapture.poison_preview_dir(out)
    real_published = figcapture.published_preview_stems
    bumped = []

    def published_then_new_poison(path):
        names = real_published(path)
        if not bumped:  # 审计已取走旧 floor、扫完之后，新的作废到达（账本序号前进）
            bumped.append(1)
            _publish(out, "x")
            figcapture.poison_preview_dir(out)
        return names

    monkeypatch.setattr(figcapture, "published_preview_stems", published_then_new_poison)
    try:
        assert figcapture.clear_preview_poison(out) is False
        assert figcapture._dir_key(out) in figcapture._poisoned_dirs
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_a_second_poison_with_an_unchanged_ledger_survives_a_concurrent_audit(
    tmp_path, monkeypatch
):
    """两次作废之间账本序号没动：审计按作废代号 compare-and-delete，后来的作废不被抹掉（Codex #958）。"""
    out = tmp_path / "out"
    out.mkdir()
    figcapture.poison_preview_dir(out)
    real_published = figcapture.published_preview_stems
    once = []

    def published_then_repoison(path):
        names = real_published(path)
        if not once:
            once.append(1)
            figcapture.poison_preview_dir(out)  # 账本没前进的又一次作废
        return names

    monkeypatch.setattr(figcapture, "published_preview_stems", published_then_repoison)
    try:
        assert figcapture.clear_preview_poison(out) is False
        assert figcapture._dir_key(out) in figcapture._poisoned_dirs
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_poison_audit_includes_dot_leading_stems(tmp_path):
    """用户 stem 可以以点开头：`.published` 是合法预览，作废审计不能把它当内部文件跳过（Codex #958）。"""
    out = tmp_path / "out"
    _publish(out, ".published")
    figcapture.poison_preview_dir(out)
    try:
        # 被当成用户 stem 审计：没重新发布 → 指针被摘掉（若被当成内部文件跳过，它会留着并被放出来）
        assert figcapture.clear_preview_poison(out) is True
        assert figcapture.usable_preview_generation(out, ".published") is None
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_workerd_side_poisoned_code_is_fatal_and_blinds_readers(tmp_path):
    from tavotto.engine import workerd_client

    assert figcapture.PREVIEW_POISONED_CODE in pool._FATAL_CODES
    out = tmp_path / "out"
    _publish(out, "fig")
    w = object.__new__(pool.WorkerdWorker)
    w.script_name = "fit.py"
    w.out_dir = out
    w._dead = False
    w.log_path = tmp_path / "worker.log"
    assert w.svg_path("fig").exists()
    exc = workerd_client.WorkerdError("poisoned", code="preview_poisoned")
    w._to_worker_error(exc)
    assert w._dead is True and w.svg_path("fig").exists() is False
    figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_native_session_poisoned_code_poisons_transport_and_readers(tmp_path):
    from tavotto.engine import nativesession

    out = tmp_path / "out"
    _publish(out, "fig")

    class _Transport:
        poison = None

        def request(self, obj, **kw):
            raise pool.WorkerError("x", code="preview_poisoned")

    s = object.__new__(nativesession.NativeSession)
    s.out_dir = out
    s.transport = _Transport()
    s.state = nativesession.BARRIER
    s.generation, s.rev = 1, 1
    assert s.svg_path("fig").exists()
    with pytest.raises(pool.WorkerError):
        s._request({"cmd": "render"}, 1.0)
    assert s.transport.poison is not None, "既有的 poison 机制：之后每条请求都被拒"
    assert s.svg_path("fig").exists() is False
    figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_a_poisoned_dir_stays_poisoned_while_the_ledger_cannot_be_audited(tmp_path):
    """Codex #978 P1：账本读不出来时审计不了，作废必须保持有效；当空账本处理会解除作废，
    账本恢复可读后被标为不可信的旧一代又被放出来。"""
    out = tmp_path / "out"
    _publish(out, "old", svg=b"<svg data-old/>")
    figcapture.poison_preview_dir(out)
    ledger = out / figcapture.PREVIEW_LEDGER
    good = ledger.read_bytes()
    try:
        ledger.write_bytes(b"{garbled")  # 扫描器改写 / 半写：存在但读不出来
        assert figcapture.clear_preview_poison(out) is False
        assert figcapture.preview_dir_poisoned(out) is True
        ledger.write_bytes(
            good
        )  # 恢复可读：仍要审计，旧一代（作废之前发布的）被摘掉，而不是被放出来
        assert figcapture.clear_preview_poison(out) is True
        assert figcapture.usable_preview_generation(out, "old") is None
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_a_stale_invalid_marker_that_cannot_be_removed_is_not_a_successful_publish(
    tmp_path, monkeypatch
):
    """Codex #978 P2：旧失效标记撤不掉时读方仍判无预览，发布不能报成功（否则画布 404、CLI 少计一张）。"""
    figcapture.mark_preview_invalid(tmp_path, "fig")

    def locked(out_dir, stem):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(figcapture, "clear_preview_invalid", locked)
    with pytest.raises(OSError, match="invalid marker"):
        _publish(tmp_path, "fig")
    monkeypatch.undo()
    # 重试（锁已放开）：撤掉标记，预览可见，计数正确
    _publish(tmp_path, "fig")
    assert figcapture.usable_preview_generation(tmp_path, "fig") is not None
    assert figcapture.published_preview_stems(tmp_path) == ["fig"]


def _saved_original(tmp_path, content: bytes):
    class Fig:
        pass

    fig = Fig()
    obs = figcapture.SavefigObservations(project_root=str(tmp_path))
    obs.metadata = lambda f: ((6.4, 4.8), 100.0, 100.0)
    target = tmp_path / "o.png"
    idx = obs.record(fig, str(target), "o", {"format": "png"}, explicit_format=True)
    target.write_bytes(content)
    obs.complete(idx, "saved")
    return obs, fig, target


def test_a_saved_original_replaced_by_same_size_same_mtime_content_is_not_intact(tmp_path):
    """Codex #978 P2：字节数 + mtime 一致不等于是同一份内容（copy2 / 粗时间戳）。要核内容摘要。"""
    obs, fig, target = _saved_original(tmp_path, b"AAAA-original")
    (orig,) = obs.saved_originals(fig)
    assert orig["intact"] is True  # 正对照
    st = target.stat()
    target.write_bytes(b"BBBB-replaced")  # 同长度
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))  # 同 mtime
    assert target.stat().st_size == st.st_size and target.stat().st_mtime_ns == st.st_mtime_ns
    (orig,) = obs.saved_originals(fig)
    assert orig["intact"] is False


def test_a_saved_original_too_large_to_digest_is_not_offered(tmp_path, monkeypatch):
    """无法核验（超过摘要上限）就不提供只读降级。"""
    monkeypatch.setattr(figcapture, "ORIGINAL_DIGEST_LIMIT_BYTES", 4)
    obs, fig, _ = _saved_original(tmp_path, b"more than four bytes")
    (orig,) = obs.saved_originals(fig)
    assert orig["intact"] is False


def test_poison_recorded_while_the_ledger_was_unreadable_does_not_swallow_the_next_builds_publishes(
    tmp_path,
):
    """Codex #978 回归：作废时账本读不出来，序号下限未知。之前取 sys.maxsize，账本恢复后新 worker 正常发布的
    有限序号全被判成旧的摘掉（build 报成功、面板全 404）。现在按发布时刻判：作废之前的摘，之后的留。"""
    import time  # noqa: PLC0415

    out = tmp_path / "out"
    _publish(out, "old")
    ledger = out / figcapture.PREVIEW_LEDGER
    good = ledger.read_bytes()
    ledger.write_bytes(b"{garbled")
    figcapture.poison_preview_dir(out)
    try:
        ledger.write_bytes(good)  # 账本恢复可读
        time.sleep(0.02)
        _publish(out, "new")  # 新一代 build 的正常发布（有限的小序号）
        assert figcapture.clear_preview_poison(out) is True
        assert figcapture.usable_preview_generation(out, "old") is None, (
            "作废之前的旧一代必须被摘掉"
        )
        assert figcapture.usable_preview_generation(out, "new") is not None, (
            "作废之后新发布的被误摘了"
        )
        assert figcapture.published_preview_stems(out) == ["new"]
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_the_audit_never_deletes_a_record_published_after_it_took_its_snapshot(
    tmp_path, monkeypatch
):
    """Codex #978：审计先快照旧记录，worker 随后为同一 stem 发布了新记录——摘指针必须 compare-and-delete，
    只删审计时看到的那一条。"""
    out = tmp_path / "out"
    _publish(out, "old")
    figcapture.poison_preview_dir(out)
    real = figcapture.published_preview_stems
    once = []

    def snapshot_then_republish(path):
        names = real(path)
        if not once:
            once.append(1)
            _publish(out, "old", svg=b"<svg data-new/>")  # 审计的快照已取走，worker 又发布了一代
        return names

    monkeypatch.setattr(figcapture, "published_preview_stems", snapshot_then_republish)
    try:
        figcapture.clear_preview_poison(out)
        monkeypatch.setattr(figcapture, "published_preview_stems", real)
        assert figcapture.clear_preview_poison(out) is True
        gen = figcapture.usable_preview_generation(out, "old")
        assert gen is not None and Path(gen[1]).read_bytes() == b"<svg data-new/>", (
            "新记录被审计误删了"
        )
    finally:
        figcapture._poisoned_dirs.pop(figcapture._dir_key(out), None)


def test_ledger_updates_from_two_actors_do_not_lose_each_other(tmp_path):
    """发布与摘指针共用账本锁：并发的读-改-写不丢记录。"""
    out = tmp_path / "out"
    _publish(out, "keep")
    errors = []

    def publisher():
        try:
            for n in range(30):
                _publish(out, f"p{n % 3}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=publisher)
    t.start()
    for _ in range(30):
        figcapture._drop_ledger_stem(out, "ghost")  # 不存在的 stem：只做一次读-改-写竞争
    t.join(timeout=60)
    assert not errors, errors
    assert set(figcapture.published_preview_stems(out)) == {"keep", "p0", "p1", "p2"}


def test_storage_errors_are_session_level_not_per_figure_failures(tmp_path):
    """Codex #978：账本 / 产物目录的存储错误不是"这张图画不出来"，不能被逐图隔离吞掉（否则健康图被逐张标成失败）。"""
    import sys  # noqa: PLC0415

    sys.path.insert(0, ENGINE_DIR)
    import figcapture as flat  # noqa: PLC0415 — figsession 平铺 import 的那一份（与包内路径是两个模块对象）
    import figsession  # noqa: PLC0415

    sess = figsession.LiveFigureSession(tmp_path)
    with pytest.raises(flat.PreviewLedgerUnreadable):
        sess.isolate_live_failure("fig", flat.PreviewLedgerUnreadable("garbled"))
    with pytest.raises(PermissionError):
        sess.isolate_live_failure("fig", PermissionError(13, "denied", str(tmp_path / "x.part")))
    assert sess.failures == {}
    # 对照：产物目录之外的 OSError（用户的绘制代码读了个不存在的文件）仍是这张图自己的失败
    sess.isolate_live_failure("fig", FileNotFoundError(2, "nope", "/somewhere/else.csv"))
    assert sorted(sess.failures) == ["fig"]


# ---- 账本锁：操作系统 advisory lock（持锁进程死了内核自动放；没有 pid / 陈旧阈值 / 遗留回收）-------
_HOLDER = r"""
import sys, time
from tavotto.engine import figcapture
with figcapture._ledger_lock(sys.argv[1], timeout=5.0):
    print("locked", flush=True)
    time.sleep(120)
"""


def _spawn_holder(out_dir):
    import subprocess as sp  # noqa: PLC0415
    import sys  # noqa: PLC0415

    env = {**os.environ, "PYTHONPATH": str(Path(ENGINE_DIR).parents[1])}
    proc = sp.Popen(  # noqa: S603
        [sys.executable, "-c", _HOLDER, str(out_dir)],
        stdout=sp.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdout.readline().strip() == "locked", "持锁子进程没拿到锁"
    return proc


def test_the_ledger_lock_excludes_another_process(tmp_path):
    holder = _spawn_holder(tmp_path)
    try:
        with pytest.raises(OSError, match="locked"):
            with figcapture._ledger_lock(tmp_path, timeout=0.3):
                pytest.fail("另一个进程持有的锁被拿走了")
        with pytest.raises(OSError, match="locked"):
            _publish_with_timeout(tmp_path, "fig")
    finally:
        holder.kill()
        holder.wait()


def _publish_with_timeout(out, stem):
    """发布在等锁超时之后失败（而不是无锁硬写）。"""
    real = figcapture._ledger_lock
    figcapture._ledger_lock = lambda d, timeout=0.3: real(d, timeout=0.3)
    try:
        return _publish(out, stem)
    finally:
        figcapture._ledger_lock = real


def test_a_lock_holder_killed_hard_releases_the_lock_at_once(tmp_path):
    """持锁进程被强杀：内核放锁，下一个进程立即拿到——不等任何超时 / 陈旧阈值。"""
    import time  # noqa: PLC0415

    holder = _spawn_holder(tmp_path)
    holder.kill()
    holder.wait()
    t0 = time.monotonic()
    assert _publish(tmp_path, "fig") == 1
    assert time.monotonic() - t0 < 2.0
    assert figcapture.published_preview_stems(tmp_path) == ["fig"]


def test_two_threads_of_one_process_exclude_each_other(tmp_path):
    inside = []
    overlap = []

    def worker():
        with figcapture._ledger_lock(tmp_path, timeout=10):
            inside.append(1)
            if len(inside) > 1:
                overlap.append(1)
            import time  # noqa: PLC0415

            time.sleep(0.05)
            inside.pop()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not overlap, "两个线程同时进了临界区"


def test_a_nested_acquire_in_one_thread_times_out_instead_of_deadlocking(tmp_path):
    with figcapture._ledger_lock(tmp_path):
        with pytest.raises(OSError, match="locked"):
            with figcapture._ledger_lock(tmp_path, timeout=0.2):
                pytest.fail("不可重入")


def test_the_resident_lock_file_is_not_a_preview_artifact(tmp_path):
    _publish(tmp_path, "fig")
    name = figcapture.PREVIEW_LEDGER + ".lock"
    assert (tmp_path / name).exists(), "锁文件常驻"
    assert not name.endswith((".svg", ".json", ".part"))
    assert figcapture.published_preview_stems(tmp_path) == ["fig"]
