"""真实 TeX 工具链的 smoke（单列：与 `test_tex_diagnostics.py` 的可控 stub 分开报告）。

没有 `latex` 的机器上整份 skip，原因写 `environment-blocked`——那不是兼容通过，也不是失败。

走产品真实入口 `/api/engine/render`（真 app + 真 safe worker + 本机 TeX）：

1. 缺宏包：独立原生 matplotlib（不带 Tavotto 钩子）与 Tavotto 因**同一个**文件失败；Tavotto 报
   `tex_package_missing` + 文件名，不是缺 Python 包；
2. 用户把宏包装上之后**直接重试**就重新验证通过——环境代（generation）一点没变，没有任何缓存
   拦着这次重跑（宏包经 `TEXINPUTS` 指到的目录「安装」，kpathsea 每次编译现查）；
3. 渲染环境的 PATH 上没有 `latex`：报 `tex_command_missing`（用的是 worker 实际继承的 PATH，
   不是父进程 `which` 一下）；DVI 命中缓存时失败挪到 dviread 找字体，那一句分不清 kpsewhich 缺失与
   文件缺失，报 `tex_failed`（证据不足），不冒充缺宏包；
4. 工具链齐全时 usetex 的图：原生 savefig 与 Tavotto 空编辑导出逐像素相同（同一 Agg、同 dpi）。
   **不关 usetex、不换字体**来制造通过。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool, project_watch

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = [
    pytest.mark.skipif(WORKER_PY is None, reason="not_run：找不到装有 matplotlib 的解释器"),
    pytest.mark.skipif(
        shutil.which("latex") is None,
        reason="environment-blocked：本机没有真实 TeX 工具链（latex）",
    ),
]

PKG = "tavottofixturepkg"

SCRIPT = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    plt.rcParams.update({{"text.usetex": True, "font.family": "serif", "font.size": 9}})
    {preamble}
    fig, ax = plt.subplots(figsize=(2.4, 1.8), dpi=100)
    ax.plot([0, 1, 2], [0.0, 1.0, 0.5])
    ax.set_title(r"$\\alpha_{{i}} = \\frac{{1}}{{2}}$")
    ax.set_xlabel(r"time $t$ (s)")
    fig.savefig("TexFig.pdf")
    fig.savefig("TexFig.png", dpi=100)


if __name__ == "__main__":
    main()
"""

REGISTRY = json.dumps(
    {
        "version": 1,
        "scripts": {
            "figtex.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["TexFig"]}
        },
    }
)


def _project(tmp_path, monkeypatch, preamble: str):
    import pikepdf

    from tavotto import app as m

    # 每条用例自己的 matplotlib 缓存（原生子进程与 worker 都继承）：tex.cache 按 TeX 源码哈希存 DVI，
    # 共用的话上一条编译成功的 DVI 会让下一条根本不调 latex——判据就量错了对象
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mplconfig"))
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "figtex.py").write_text(SCRIPT.format(preamble=preamble), encoding="utf-8")
    (figs / "tavotto_registry.json").write_text(REGISTRY, encoding="utf-8")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 100))
    pdf.save(figs / "TexFig.pdf")
    m.open_project(str(figs))
    return m, figs


@pytest.fixture
def cleanup():
    made: list = []
    yield made
    from tavotto import app as m

    m.reset_projects()
    for figs in made:
        pool.shutdown_all(figures_dir=str(figs), wait=True)
    project_watch.stop()


def _render(client):
    return client.post("/api/engine/render", json={"id": "TexFig.pdf", "patches": []})


def test_missing_package_matches_native_and_a_plain_retry_revalidates(
    tmp_path, monkeypatch, cleanup
):
    texinputs = tmp_path / "texinputs"
    texinputs.mkdir()
    # 末尾的空项 = 「再查默认树」；worker 继承这份环境（用户自己的解释器原样继承）
    monkeypatch.setenv("TEXINPUTS", f"{texinputs}{os.pathsep}")
    preamble = f'plt.rcParams["text.latex.preamble"] = r"\\usepackage{{{PKG}}}"'
    m, figs = _project(tmp_path, monkeypatch, preamble)
    cleanup.append(figs)

    # 1. 独立原生 matplotlib：同一个文件缺失
    native_dir = tmp_path / "native"
    native_dir.mkdir()
    native = subprocess.run(
        [WORKER_PY, str(figs / "figtex.py")],
        cwd=native_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert native.returncode != 0
    assert f"File `{PKG}.sty' not found" in native.stderr

    client = m.app.test_client()
    r = _render(client)
    body = r.get_json()
    assert r.status_code == 500, body
    assert body["code"] == "tex_package_missing"
    assert body["params"] == {"command": "latex", "file": f"{PKG}.sty"}
    assert "dependency_repair" not in body and not body.get("module")

    # 2. 用户把宏包「装上」（TEXINPUTS 指到的目录里放进 .sty），环境代不变，直接重试
    (texinputs / f"{PKG}.sty").write_text("\\ProvidesPackage{" + PKG + "}\n", encoding="utf-8")
    r = _render(client)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["manifest"]["elements"], "重试后应当真的建出了可编辑元素"


def test_latex_missing_from_the_worker_path_is_a_command_missing(tmp_path, monkeypatch, cleanup):
    keep = [
        d
        for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and not (Path(d) / "latex").exists() and not (Path(d) / "latex.exe").exists()
    ]
    monkeypatch.setenv("PATH", os.pathsep.join(keep))
    assert shutil.which("latex", path=os.environ["PATH"]) is None
    m, figs = _project(tmp_path, monkeypatch, "")
    cleanup.append(figs)
    r = _render(m.app.test_client())
    body = r.get_json()
    assert r.status_code == 500, body
    assert body["code"] == "tex_command_missing"
    assert body["params"] == {"command": "latex"}


def test_a_working_usetex_figure_is_pixel_identical_to_native(tmp_path, monkeypatch, cleanup):
    m, figs = _project(tmp_path, monkeypatch, "")
    cleanup.append(figs)
    native_dir = tmp_path / "native"
    native_dir.mkdir()
    subprocess.run([WORKER_PY, str(figs / "figtex.py")], cwd=native_dir, check=True, timeout=180)
    w = pool.one_shot("figtex.py", str(figs), "main")
    try:
        w.ensure_built()
        man = w.override("TexFig", [])["manifest"]
        roles = {e["role"] for e in man["elements"]}
        assert {"title", "axis_label"} <= roles, (
            f"usetex 的标题 / 轴标签应当仍是可编辑文字元素：{roles}"
        )
        w.export("TexFig", [], str(tmp_path / "ours.png"), "png", 100)
    finally:
        pool.discard(w)
    from PIL import Image

    a = Image.open(native_dir / "TexFig.png").convert("RGB")
    b = Image.open(tmp_path / "ours.png").convert("RGB")
    assert a.size == b.size
    assert a.tobytes() == b.tobytes()


def test_cached_dvi_with_tex_gone_is_reported_as_unknown_not_missing_package(
    tmp_path, monkeypatch, cleanup
):
    """DVI 命中 matplotlib 的 tex 缓存时不会调 latex；PATH 上没有 TeX，失败出在 dviread 用 kpsewhich
    找字体那一步——那一句分不清「kpsewhich 不在」与「文件不在」，必须报证据不足，不冒充缺宏包。"""
    m, figs = _project(tmp_path, monkeypatch, "")
    cleanup.append(figs)
    assert _render(m.app.test_client()).status_code == 200  # 工具链齐全：编译一次，DVI 进缓存
    pool.shutdown_all(figures_dir=str(figs), wait=True)
    keep = [
        d
        for d in os.environ.get("PATH", "").split(os.pathsep)
        if d and not (Path(d) / "kpsewhich").exists() and not (Path(d) / "latex").exists()
    ]
    monkeypatch.setenv("PATH", os.pathsep.join(keep))
    r = _render(m.app.test_client())
    body = r.get_json()
    assert r.status_code == 500, body
    assert body["code"] == "tex_failed"
    assert "dependency_repair" not in body and not body.get("module")
