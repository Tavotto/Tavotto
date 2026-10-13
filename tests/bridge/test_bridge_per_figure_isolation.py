"""native bridge：一张图失败不拖垮其他图；原产物与活图是两个独立的事实（P08a，EX04/EX11/EX12/EX14，AC-05/AC-25）。

全部走真屏障（真起用户解释器 + 控制通道 + 认证），判据不在 `--report` 形态——那里屏障立刻返回、
会话是脚本跑完才建的一次性对象，测不到「首个 barrier 前」这件事。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from support.bridgekit import write
from tavotto.engine import figcapture

pytestmark = pytest.mark.usefixtures("clean_env")

HEAD = (
    "import matplotlib\nmatplotlib.use('Agg')\n"
    "matplotlib.rcParams['svg.fonttype'] = 'none'\n"
    "import matplotlib.pyplot as plt\nfrom matplotlib.artist import Artist\n"
)
GOOD = """\
good, ax = plt.subplots()
ax.plot([0, 1], [1, 2])
ax.set_title("Plain")
good.savefig("good.png")
"""
BAD = """\
class AggOnly(Artist):
    def draw(self, renderer):
        renderer.buffer_rgba()  # SVG renderer 没有它：额外预览画到一半抛错
bad, ax2 = plt.subplots()
ax2.plot([0, 1], [3, 4])
ax2.add_artist(AggOnly())
bad.savefig("bad.png")
"""


def _facts(build: dict) -> dict:
    return {f["stem"]: f for f in build["figure_facts"]}


def _title_gid(manifest: dict, text: str) -> str:
    return next(
        el["gid"]
        for el in manifest["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == text
    )


def _preview(out_dir: Path, stem: str, suffix: str) -> Path:
    return Path(figcapture.usable_preview_path(out_dir, stem, suffix))


@pytest.mark.parametrize("order", ["good_first", "bad_first"])
def test_one_bad_figure_leaves_exactly_one_live_healthy_figure(
    user_python, tmp_path, bridge_session, order
):
    """EX12：好图 + Agg-only 坏图，**两种顺序**。首个 barrier 照常到达；恰好 1 张健康图是活对象，
    真的能改、能导出；坏图记明确失败事实，且它真实存下的原始 PNG 被声明为只读能力。"""
    proj = tmp_path / "proj"
    body = GOOD + BAD if order == "good_first" else BAD + GOOD
    write(proj / "fig.py", HEAD + body)
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        barrier = sess.wait_event("barrier")
        assert barrier["stems"] == ["good"], "首个 barrier 的可编辑图：恰好 1 张健康图"
        assert barrier["failed_stems"] == ["bad"]
        build = sess.ensure_built()
        assert list(build["stems"]) == ["good"]
        assert [f["stem"] for f in build["failed_figures"]] == ["bad"]
        assert build["capture_summary"] == {"live": 1, "failed": 1}
        facts = _facts(build)

        good, bad = facts["good"], facts["bad"]
        assert good["state"] == "live" and good["failure"] is None
        assert good["capabilities"] == ["preview", "live_edit", "original_readonly"]
        assert isinstance(good["generation"], int)
        assert bad["state"] == "failed" and bad["generation"] is None
        assert bad["failure"]["code"] == "preview_capture_failed"
        assert "AttributeError" in bad["failure"]["message"]
        # 坏图没有预览、不能编辑；但它**本次运行成功 save** 的 PNG 是只读原件
        assert bad["capabilities"] == ["original_readonly"]
        (orig,) = bad["saved_originals"]
        assert orig["path"] == "bad.png" and orig["scope"] in ("project", "execution")
        assert orig["format"] == "png" and orig["intact"] is True
        assert (proj / orig["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        # 坏图没有预览产物：读方拿到的是「无预览」，不是半份 XML
        assert not _preview(sess.out_dir, "bad", ".svg").exists()
        assert not any(p.name.endswith(".part") for p in sess.out_dir.rglob("*"))

        # 健康图是真的活对象：改文字、重渲染、导出
        gid = _title_gid(
            json.loads(_preview(sess.out_dir, "good", ".json").read_text("utf-8")), "Plain"
        )
        patches = [{"gid": gid, "prop": "text", "value": "Edited"}]
        assert sess.override("good", patches)["warnings"] == []
        again = json.loads(_preview(sess.out_dir, "good", ".json").read_text("utf-8"))
        assert _title_gid(again, "Edited") == gid
        ET.parse(_preview(sess.out_dir, "good", ".svg"))
        pdf = tmp_path / "out.pdf"
        assert sess.export("good", patches, str(pdf), "pdf", 100)["warnings"] == []
        assert pdf.read_bytes()[:5] == b"%PDF-"

        sess.resume()
        exit_event = sess.wait_event("exit")
        assert exit_event["code"] == 0
        assert exit_event["figures"] == 1, "完成计数 = 完整发布的图，坏图不计"


def test_requesting_an_isolated_stem_returns_the_stored_failure(
    user_python, tmp_path, bridge_session
):
    """Codex #978：被隔离的 stem 不是"不存在"——render/export 请求回的是当时记下的失败原因。"""
    from tavotto.engine.bridge import BridgeError  # noqa: PLC0415

    proj = tmp_path / "proj"
    write(proj / "fig.py", HEAD + GOOD + BAD)
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        sess.ensure_built()
        with pytest.raises(BridgeError) as err:
            sess.override("bad", [])
        assert err.value.code == "preview_capture_failed", err.value.code
        assert "AttributeError" in str(err.value)
        with pytest.raises(BridgeError) as unknown:
            sess.override("never-existed", [])
        assert unknown.value.code == "unknown_stem"
        sess.resume()
        sess.wait_event("exit")


def test_the_users_own_exception_is_never_swallowed_by_isolation(
    user_python, tmp_path, bridge_session
):
    """EX11：脚本先出图再抛 RuntimeError——图保留（含隔离后的健康图），原始异常照报，退出码是 1。"""
    proj = tmp_path / "proj"
    write(proj / "fig.py", HEAD + GOOD + BAD + "raise RuntimeError('user bug')\n")
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        barrier = sess.wait_event("barrier")
        assert barrier["script_error"]["code"] == "script_error"
        assert "RuntimeError: user bug" in barrier["script_error"]["message"]
        assert barrier["stems"] == ["good"] and barrier["failed_stems"] == ["bad"]
        build = sess.ensure_built()
        assert "user bug" in build["script_error"]["message"]
        assert sess.override("good", [])["warnings"] == []
        sess.resume()
        assert sess.wait_event("exit")["code"] == 1


def test_a_failed_figures_state_never_blocks_continue(user_python, tmp_path, bridge_session):
    """失败图的 Figure 状态核对过恢复：放行（continue）照常，脚本接着跑完。"""
    proj = tmp_path / "proj"
    write(proj / "fig.py", HEAD + BAD + GOOD + "print('AFTER-BARRIER', flush=True)\n")
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        sess.ensure_built()
        sess.resume()
        assert sess.wait_event("exit")["code"] == 0


def test_saved_stage_and_unsaved_final_are_two_separate_facts(
    user_python, tmp_path, bridge_session
):
    """EX14 / AC-25：同一 Figure 先存 `Saved Stage`、再改成 `Unsaved Final` 不保存。
    磁盘原件仍是前者；活图（manifest 与无 edits 导出）是后者——两边各有来源与时刻，**不强行相同**，
    保存观察账本也不是历史 Figure 快照。"""
    proj = tmp_path / "proj"
    write(
        proj / "fig.py",
        HEAD + "fig, ax = plt.subplots()\nax.plot([0, 1], [1, 2])\n"
        "ax.set_title('Saved Stage')\nfig.savefig('original.svg')\n"
        "ax.set_title('Unsaved Final')\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        build = sess.ensure_built()
        fact = _facts(build)["original"]
        assert fact["state"] == "live"
        assert fact["capabilities"] == ["preview", "live_edit", "original_readonly"]
        (orig,) = fact["saved_originals"]
        assert orig["path"] == "original.svg" and orig["intact"] is True
        # 活图身份：manifest 里是 Unsaved Final
        manifest = json.loads(_preview(sess.out_dir, "original", ".json").read_text("utf-8"))
        assert _title_gid(manifest, "Unsaved Final")
        with pytest.raises(StopIteration):
            _title_gid(manifest, "Saved Stage")
        # 只读原件身份：磁盘上是 Saved Stage，不是 Unsaved Final
        text = (proj / "original.svg").read_text("utf-8")
        assert "Saved Stage" in text and "Unsaved Final" not in text
        # 两个事实各有各的时刻：保存发生在被捕获进活会话之前
        assert isinstance(orig["saved_at"], float) and isinstance(fact["live_captured_at"], float)
        assert orig["saved_at"] <= fact["live_captured_at"]
        sess.resume()
        sess.wait_event("exit")


def test_the_original_is_never_claimed_by_basename_or_directory_scan(
    user_python, tmp_path, bridge_session
):
    """只读降级只认**这次运行里这个 Figure 对象**成功 save 的产物：
    目录里碰巧躺着与它的 stem 同名的旧 `fig.png`（上一次运行 / 别的脚本留下的），而这张图这次根本没存盘，
    不能被当成它的原件；存了盘但随后被改写的文件也不再是「本次保存的那份」。"""
    proj = tmp_path / "proj"
    (proj).mkdir()
    (proj / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\nSTALE-FROM-ANOTHER-RUN")
    write(
        proj / "fig.py",
        HEAD
        + BAD.replace(
            'bad.savefig("bad.png")', ""
        )  # 这张图这次没存盘：pyplot 兜底给它的 stem 是脚本名 fig
        + "other, ax3 = plt.subplots()\nax3.plot([1, 2], [1, 2])\nother.savefig('other.png')\n"
        "import time; time.sleep(0.05)\n"
        "open('other.png', 'ab').write(b'TAMPERED')\n",  # 存盘后被改写
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        facts = _facts(sess.ensure_built())
        assert facts["fig"]["state"] == "failed"
        assert facts["fig"]["saved_originals"] == [], "同名旧文件被当成了替身"
        assert facts["fig"]["capabilities"] == []
        # 被改写的原件：不再声明只读能力（对账字节数 / mtime 失败）
        assert facts["other"]["state"] == "live"
        assert facts["other"]["capabilities"] == ["preview", "live_edit"]
        sess.resume()
        sess.wait_event("exit")


def test_a_save_outside_the_project_and_execution_roots_is_not_exposed(
    user_python, tmp_path, bridge_session
):
    """根外路径不外发：保存到项目与执行目录之外的产物不进事实（也不会被当成可看的原件）。"""
    proj = tmp_path / "proj"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    write(
        proj / "fig.py",
        HEAD
        + f"fig, ax = plt.subplots()\nax.plot([1, 2], [1, 2])\nfig.savefig({str(outside / 'x.png')!r})\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        (fact,) = _facts(sess.ensure_built()).values()
        assert fact["saved_originals"] == []
        sess.resume()
        sess.wait_event("exit")


def test_a_healthy_script_is_unchanged(user_python, tmp_path, bridge_session):
    """正对照：没有任何失败时，build 响应里没有 `failed_figures`，barrier 事件没有 `failed_stems`。"""
    proj = tmp_path / "proj"
    write(proj / "fig.py", HEAD + GOOD)
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        barrier = sess.wait_event("barrier")
        assert barrier["stems"] == ["good"] and not barrier.get("failed_stems")
        build = sess.ensure_built()
        assert "failed_figures" not in build
        assert build["capture_summary"] == {"live": 1, "failed": 0}
        sess.resume()
        sess.wait_event("exit")


def test_a_figure_broken_between_two_barriers_is_isolated_not_thrown_into_the_script(
    user_python, tmp_path, bridge_session
):
    """Codex #978：脚本在第一个 barrier 之后把已捕获的图弄坏（SVG 渲染失败）。rebase 路径要和新捕获同样逐图隔离：
    异常不能逃进用户脚本的 `show()`；第二个 barrier 照发、另一张图仍 live、坏图的旧预览被隐藏、脚本正常结束。"""
    proj = tmp_path / "proj"
    write(
        proj / "fig.py",
        HEAD
        + GOOD
        + "b, bx = plt.subplots()\nbx.plot([0, 1], [3, 4])\nb.savefig('b.png')\n"
        + "plt.show()\n"
        + "class AggOnly(Artist):\n    def draw(self, renderer):\n        renderer.buffer_rgba()\n"
        + "bx.add_artist(AggOnly())\n"
        + "plt.show()\nprint('SCRIPT-FINISHED', flush=True)\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        first = sess.wait_event("barrier")
        assert first["stems"] == ["good", "b"] and not first.get("failed_stems")
        sess.ensure_built()
        assert _preview(sess.out_dir, "b", ".svg").exists()
        sess.resume()
        second = sess.wait_event("barrier")
        assert second["stems"] == ["good"] and second["failed_stems"] == ["b"], second
        facts = {f["stem"]: f["state"] for f in second["figure_facts"]}
        assert facts == {"good": "live", "b": "failed"}
        assert not _preview(sess.out_dir, "b", ".svg").exists(), "上一版预览不该当本次结果"
        assert sess.override("good", [])["warnings"] == []
        sess.resume()
        sess.wait_event("barrier")  # script_end
        sess.resume()
        assert sess.wait_event("exit")["code"] == 0


def test_an_isolated_figure_that_comes_back_replays_its_saved_edits(
    user_python, tmp_path, bridge_session
):
    """Codex #978 P1：barrier1 做过编辑，barrier2 渲染失败被隔离，脚本修好后 barrier3 回来——
    它仍是"已知图"，保存的 patch 要重放，之后离开屏障也不丢（不是当新图把编辑清掉）。"""
    proj = tmp_path / "proj"
    write(
        proj / "fig.py",
        HEAD
        + "b, bx = plt.subplots()\nbx.plot([0, 1], [3, 4])\nbx.set_title('Plain')\nb.savefig('b.png')\n"
        + "plt.show()\n"
        + "class AggOnly(Artist):\n    def draw(self, renderer):\n        renderer.buffer_rgba()\n"
        + "bx.add_artist(AggOnly())\nplt.show()\n"
        + "for a in list(bx.artists):\n    a.remove()\nplt.show()\n",
    )

    def title(sess):
        man = json.loads(_preview(sess.out_dir, "b", ".json").read_text("utf-8"))
        return [
            f["value"]
            for el in man["elements"]
            for f in el.get("editable", [])
            if f["prop"] == "text" and f["value"] in ("Plain", "Edited")
        ]

    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        sess.wait_event("barrier")
        build = sess.ensure_built()
        assert "b" in build["stems"]
        gid = _title_gid(
            json.loads(_preview(sess.out_dir, "b", ".json").read_text("utf-8")), "Plain"
        )
        sess.override("b", [{"gid": gid, "prop": "text", "value": "Edited"}])
        sess.resume()
        second = sess.wait_event("barrier")
        assert second["failed_stems"] == ["b"]
        sess.resume()
        third = sess.wait_event("barrier")
        assert third["stems"] == ["b"] and not third.get("failed_stems"), third
        assert title(sess) == ["Edited"], "回来的图没有重放保存的编辑"
        sess.resume()
        sess.wait_event("barrier")  # script_end：离开上一个屏障时存下的编辑没有被清掉
        assert title(sess) == ["Edited"], "离开屏障之后编辑丢了"
        sess.resume()
        assert sess.wait_event("exit")["code"] == 0
