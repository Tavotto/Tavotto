"""native bridge 端到端：用户自己的 Python → Figure → 改标题字号 → 导出 PDF。

**真进程、真 matplotlib、真 socket、真 PDF。** 这条链是整个 spike 的完成
定义（§二十一）里最靠后的那几项：manifest / override / export 复用 Tavotto
现有引擎、Figure 不出进程、用户环境不装 Tavotto。

两档：

* 默认那条用**本机装了 matplotlib 的解释器**当"用户的 Python"，并把
  `PYTHONPATH` 洗掉——报告里 `tavotto_importable` 因此是 False；
* `-m slow` 那条真造一个 `python -m venv`（**不带** system-site-packages）
  再 `pip install matplotlib`，证明"一个只有 matplotlib 的干净环境"也跑得通。
  它要联网，所以按仓库惯例标 slow，默认跳过。
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from support.bridgekit import child_env, write
from tavotto.engine import bridge

pytestmark = pytest.mark.usefixtures("clean_env")

PAPER = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import sys

print("running", sys.argv[1:], flush=True)
fig, ax = plt.subplots(figsize=(4.0, 3.0))
ax.plot([1, 2, 3], [2.0, 4.5, 9.0], label="run")
ax.set_title("Original Title")
ax.legend()
plt.show()
print("done", flush=True)
"""


def _title_gid(manifest: dict) -> str:
    return next(
        el["gid"]
        for el in manifest["elements"]
        for f in el.get("editable", [])
        if f["prop"] == "text" and f["value"] == "Original Title"
    )


def _title_fontsize(manifest: dict, gid: str) -> float:
    el = next(e for e in manifest["elements"] if e["gid"] == gid)
    return next(f["value"] for f in el["editable"] if f["prop"] == "fontsize")


def _pdf_title_size(path) -> float:
    """从导出的 PDF 里量出"Original Title"那段文字的字号。

    用 PyMuPDF（Flask 父进程本来就有它，而且它是全仓库唯一 import pymupdf
    的模块之外的唯一读者——这里只读不写）。量的是**真 PDF 里的事实**，
    不是 manifest 自己报的数——manifest 说改了、PDF 里没改，正是这条要挡的。
    """
    import pymupdf

    with pymupdf.open(path) as doc:
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if "Original Title" in span.get("text", ""):
                        return round(float(span["size"]), 2)
    raise AssertionError("导出的 PDF 里找不到标题文字")


def _full_chain(sess, tmp_path, *, expect_stem_count=1):
    """屏障 → build → manifest → 改字号 → 导出 PDF → 逐条核对。"""
    ev = sess.wait_event("barrier")
    assert ev["reason"] == "show"
    build = sess.ensure_built()
    assert len(build["stems"]) == expect_stem_count
    stem = next(iter(build["stems"]))

    # 描述符：native profile、pyplot 来源、没有原始产物 → 写回原件不成立
    desc = next(d for d in build["descriptors"] if d["stem"] == stem)
    assert desc["execution_profile"] == "native"
    assert desc["capture_source"] == "pyplot"
    assert desc["original_artifact"] is None
    assert desc["can_writeback_artifact"] is False
    assert desc["can_writeback_source"] is False

    man = json.loads((sess.out_dir / f"{stem}.json").read_text(encoding="utf-8"))
    gid = _title_gid(man)
    before = _title_fontsize(man, gid)
    assert before != 22.0, "用例前提：初值不能恰好等于要改成的值（同值提交是 no-op）"

    patches = [{"gid": gid, "prop": "fontsize", "value": 22.0}]
    resp = sess.override(stem, patches)
    assert resp["warnings"] == []
    assert _title_fontsize(resp["manifest"], gid) == 22.0

    # 导出**必须带上同一组 patches**：v1 的 override 是「全量列表」语义，
    # 空列表 = 撤销一切。带空列表导出出来的会是脚本原样那张图。
    out = tmp_path / "paper.pdf"
    exported = sess.export(stem, patches, str(out))
    assert exported["warnings"] == []
    assert out.is_file() and out.read_bytes()[:4] == b"%PDF"
    assert _pdf_title_size(out) == 22.0, "PDF 里的字号与 manifest 报的对不上"

    # 状态中立：导出没把 patches 留在热会话上，也没把它们撤掉
    again = sess.override(stem, patches)
    assert _title_fontsize(again["manifest"], gid) == 22.0
    reverted = sess.override(stem, [])
    assert _title_fontsize(reverted["manifest"], gid) == before, "全量列表回空 = 回到脚本原样"
    return stem


def test_end_to_end_on_the_users_own_interpreter(user_python, tmp_path, bridge_session):
    """完整链：用户的 Python → 捕获 → manifest → 改字号 → 导出 PDF → 撤销。

    顺带钉住两条 native 的承诺：用户的 argv 原样到脚本手里、用户的 stdout
    归他自己（协议走 socket）。
    """
    proj = tmp_path / "proj"
    write(proj / "paper.py", PAPER)
    with bridge_session(proj / "paper.py", cwd=str(proj), argv=("--dataset", "run7")) as sess:
        _full_chain(sess, tmp_path)
        sess.resume()
        sess.wait_event("barrier")  # 脚本跑完那次
        sess.resume()
        sess.wait_event("exit")


def test_the_user_environment_does_not_have_tavotto_installed(user_python, tmp_path):
    """§三的硬要求：**用户环境不需要也不允许安装 Tavotto**。

    报告里如实记一笔 `tavotto_importable`——跑得通，且它是 False。
    """
    from support.bridgekit import run_runner

    proj = tmp_path / "proj"
    write(proj / "paper.py", PAPER)
    report = tmp_path / "report.json"
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / "paper.py",
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["figures"], "前提：确实捕获到了图"
    assert data["tavotto_importable"] is False, (
        "这个解释器里能 import tavotto——本条证明不了「不装也行」。"
        "检查是不是有 PYTHONPATH 漏进了子进程。"
    )


def test_module_target_end_to_end(user_python, tmp_path, bridge_session):
    """`python -m paper.figure` 形态走完同一条链。

    module 目标的相对路径要等 import 之后才知道（`__main__.__file__`），
    描述符里的 `script` 因此是跑完之后修正的——这条钉住它没有留成空。
    """
    proj = tmp_path / "proj"
    write(proj / "paper" / "__init__.py", "")
    write(proj / "paper" / "figure.py", PAPER)
    with bridge_session(
        "paper.figure", cwd=str(proj), target_kind="module", argv=("--dataset", "run7")
    ) as sess:
        stem = _full_chain(sess, tmp_path)
        build = sess.last_build
        desc = next(d for d in build["descriptors"] if d["stem"] == stem)
        assert desc["script"] == "paper/figure.py", desc["script"]
        assert desc["asset_id"] == f"runtime:paper/figure.py#{stem}"
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


@pytest.mark.slow
def test_end_to_end_in_a_freshly_created_venv(tmp_path, bridge_session, monkeypatch):
    """真造一个**只有 matplotlib** 的 venv，整条链照样走得通。

    `python -m venv`（不带 `--system-site-packages`）：里面没有 Tavotto、
    没有 Flask、没有 PyMuPDF——正是科研项目 `.venv` 的样子。要联网装包，
    所以标 slow（仓库惯例：默认跳过，`-m slow` 单独跑）。
    """
    venv = tmp_path / "uservenv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=300)
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    assert py.is_file()
    subprocess.run(
        [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "matplotlib"],
        check=True,
        timeout=1800,
        env=child_env(),
    )
    probe = subprocess.run(
        [
            str(py),
            "-c",
            "import importlib.util as u, matplotlib, sys;"
            "sys.stdout.write(str(u.find_spec('tavotto') is not None))",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=child_env(),
        timeout=120,
    )
    assert probe.stdout.strip() == "False", "夹具 venv 里竟然有 Tavotto"

    proj = tmp_path / "proj"
    write(proj / "paper.py", PAPER)
    spec = bridge.spec_for(str(proj / "paper.py"), interpreter=str(py), cwd=str(proj))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
    try:
        sess.start()
        _full_chain(sess, tmp_path)
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")
    finally:
        sess.close()
