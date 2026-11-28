"""本机真实的 CFF 中日韩字体在导出 / 写回的 PDF 里读得回（摸底 T-003 的真字体那一半）。

macOS 的苹方 / 冬青黑体、Linux 的 Noto Sans CJK / 思源黑体都是字体集里的 CFF（CID-keyed）轮廓。
matplotlib 的 Type 42 把它们写成 `CIDFontType2` + `FontFile2`（TrueType 槽），MuPDF 一类阅读器按 TrueType
去读：画出来是别的字形，文字层只读回一部分汉字（#2173 在 macOS CI 上撞见的就是这个，与混排无关）。

`tests/test_export_otf_fonts.py` 用测试现做的 CFF 小字体钉住结构；这里用**回退链里真装着的那张中文脸**
走产品的两条出口——导出（`/api/export`）与写回原件（`/api/engine/update_source`）——再用独立读取器
（PyMuPDF）读文字层：三处汉字都得读回、TrueType 槽里不许再有 `OTTO`。本机回退链里没有 CFF 中文脸
（Windows 的微软雅黑是 TrueType）就跳过；macOS 腿上它量的是苹方。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pikepdf
import pytest

from tavotto import app as m
from tavotto.engine import exportjob, figcapture, pool, project_watch

WORKER_PY = pool.find_worker_python()

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

ENGINE_DIR = Path(__file__).resolve().parent.parent / "src" / "tavotto" / "engine"
WORDS = ("浓度随时间", "压力", "温度变化")
EDITED = "温度变化曲线"

#: worker 解释器里：回退链上真装着、且解析到 CFF 轮廓（`CFF ` 表）那张脸的族名
_FIND_CFF_CJK = """\
import json, sys
sys.path.insert(0, sys.argv[1])
from matplotlib import font_manager
from fontTools.ttLib import TTFont
import overrides

found = []
for name in overrides.cjk_fallback_tail():
    try:
        path = font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
    except ValueError:
        continue
    index = getattr(path, "face_index", 0) or 0
    try:
        font = TTFont(str(path), fontNumber=index, lazy=True)
    except Exception:
        continue
    if "CFF " in font:
        found.append({"family": name, "file": str(path), "index": index})
print(json.dumps(found))
"""


def _cff_cjk_faces() -> list[dict]:
    out = subprocess.run(
        [WORKER_PY, "-c", _FIND_CFF_CJK, str(ENGINE_DIR)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _script(family: str) -> str:
    return f"""import matplotlib.pyplot as plt


def main():
    plt.rcParams["font.family"] = [{family!r}]
    fig, ax = plt.subplots(figsize=(4, 3), layout="constrained")
    ax.plot([0, 1], [0, 1])
    ax.set_xlabel({WORDS[0]!r})
    ax.set_ylabel({WORDS[1]!r})
    ax.set_title({WORDS[2]!r})
    fig.savefig("F.pdf")
"""


@pytest.fixture(scope="module")
def cff_face():
    faces = _cff_cjk_faces()
    if not faces:
        pytest.skip("本机回退链里没有 CFF 轮廓的中文脸（Windows 的微软雅黑等是 TrueType）")
    return faces[0]


@pytest.fixture
def project(tmp_path, monkeypatch, cff_face):
    m.reset_projects()
    m.app.config["TESTING"] = True
    monkeypatch.setenv("TAVOTTO_WORKER_PYTHON", sys.executable)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mpl"))
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    root = tmp_path / "中文 图"
    root.mkdir()
    (root / "plot.py").write_text(_script(cff_face["family"]), encoding="utf-8")
    (root / "tavotto_registry.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scripts": {
                    "plot.py": {"entry": "main", "cost": "light", "notes": "", "stems": ["F"]}
                },
            }
        ),
        encoding="utf-8",
    )
    m.open_project(str(root))
    exportjob.reset_for_tests()
    yield m.app.test_client(), root
    pool.shutdown_all(figures_dir=str(root), wait=True)
    m.reset_projects()
    project_watch.stop()
    exportjob.reset_for_tests()
    from tavotto.rendercore import renderhost

    renderhost.shutdown_shared()


def _font_programs(pdf_path: Path) -> list[bytes]:
    with pikepdf.open(str(pdf_path)) as pdf:
        return [
            bytes(o["/FontFile2"].read_bytes()[:4])
            for o in pdf.objects
            if isinstance(o, pikepdf.Dictionary)
            and o.get("/Type") == "/FontDescriptor"
            and "/FontFile2" in o
        ]


def _text(pdf_path: Path) -> str:
    """独立读取器（PyMuPDF）读文字层：CFF 塞在 TrueType 槽里时它正是丢字的那一个。"""
    import pymupdf

    with pymupdf.open(str(pdf_path)) as doc:
        return "".join(page.get_text() for page in doc)


def _registered(client) -> None:
    probe = client.post("/api/registry/probe", json={"script": "plot.py"})
    assert probe.status_code == 200 and probe.get_json().get("registered"), probe.get_json()


def _render(client, patches):
    r = client.post("/api/engine/render", json={"id": "F.pdf", "patches": patches})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["manifest"]


#: 改标题 = 由脚本重画（worker 写 PDF 的那条路）
PATCHES = [{"gid": "axes_0.title", "prop": "text", "value": EDITED}]


def _assert_reads_back(pdf: Path) -> None:
    heads = _font_programs(pdf)
    assert heads and b"OTTO" not in heads, heads
    text = _text(pdf)
    missing = [w for w in (*WORDS[:2], EDITED) if w not in text]
    assert not missing, (missing, text)


def test_the_worker_writes_this_face_into_the_truetype_slot(project, cff_face, tmp_path):
    """尺子的前提：这张脸经 worker 原样导出时 TrueType 槽里确实是 CFF（`OTTO`）——否则下面两条恒真。"""
    _, root = project
    w = pool.one_shot("plot.py", str(root), "main")
    try:
        w.ensure_built()
        raw = tmp_path / "raw.pdf"
        w.export("F", [], str(raw), "pdf")
    finally:
        pool.discard(w)
    assert b"OTTO" in _font_programs(raw), cff_face


def test_exported_pdf_reads_back_every_chinese_label(project):
    client, _ = project
    _registered(client)
    patches = PATCHES
    resp = client.post(
        "/api/export",
        json={
            "scope": "original",
            "filename": "cjk",
            "formats": ["pdf"],
            "overwrite": "replace",
            "original": {
                "figure_id": figcapture.runtime_asset_id("plot.py", "F"),
                "overrides": patches,
                "source_kind": "vector",
            },
        },
    )
    body = resp.get_json()
    assert resp.status_code == 200 and body["status"] == "done", body
    (out,) = [o for o in body["outputs"] if o["format"] == "pdf"]
    _assert_reads_back(Path(body["export_dir"]) / out["name"])


def test_written_back_original_reads_back_every_chinese_label(project):
    client, root = project
    original = pikepdf.new()
    original.add_blank_page(page_size=(200, 100))
    original.save(root / "F.pdf")
    _registered(client)
    patches = PATCHES
    _render(client, patches)
    r = client.post(
        "/api/engine/update_source",
        json={
            "id": "F.pdf",
            "patches": patches,
            "expected_mtime": int((root / "F.pdf").stat().st_mtime),
        },
    )
    assert r.status_code == 200, r.get_json()
    _assert_reads_back(root / "F.pdf")
