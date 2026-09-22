"""四身份、公开投影与故障阶段（统一实施包 U09，ADR 0070 / 0071；RC-075 ~ RC-082、FO-062、FO-066）。

两组：纯模型的（identity 模块 / 公开投影 / 轨迹，任何机器）与经真实入口的（候选后端真导出，rc-venv 里跑，
主 `.venv` skip 并写明理由）。判据的主语：manifest 里**并列**的四个字段、离开本机的那份投影里**有没有**针。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tavotto import app as m, pdfbackend
from tavotto.engine import deprepair, exportjob, pool as engine_pool, project_watch as engine_watch
from tavotto.rendercore import identity, inspector

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))
import artifactbytes  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "pdf_png_assets"
HAS_CANDIDATE = all(
    importlib.util.find_spec(mod) is not None
    for mod in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)
needs_candidate = pytest.mark.skipif(not HAS_CANDIDATE, reason="候选包未装（not_run，不是绿）")


# ===========================================================================
# 身份：四个字段并列，谁也不含谁
# ===========================================================================
def test_the_run_identity_never_enters_semantic_or_render():
    """RC-075 must_fail：run UUID 进语义 hash。同一份计划、两次作业：semantic / render 逐字相同，run 不同。"""
    sem = "sha256:" + "ab" * 32
    r1 = identity.render_identity(sem, fonts_version="f1", renderer="pdfium", px=(10, 5), ppi=72)
    r2 = identity.render_identity(sem, fonts_version="f1", renderer="pdfium", px=(10, 5), ppi=72)
    assert r1 == r2
    a = identity.identities(semantic=sem, render=r1, artifact_sha256="cc" * 32, run="run-1")
    b = identity.identities(semantic=sem, render=r2, artifact_sha256="cc" * 32, run="run-2")
    assert a["run"] != b["run"]
    assert (a["semantic"], a["render"], a["artifact"]) == (
        b["semantic"],
        b["render"],
        b["artifact"],
    )
    # run 只在 identity.run 这一格；render_identity 的签名里根本没有它
    assert "run" not in identity.render_identity.__code__.co_varnames


@pytest.mark.parametrize(
    "change",
    [
        {"fonts_version": "f2"},
        {"renderer_version": "146.0"},
        {"backend_version": "0.2"},
        {"ppi": 300},
        {"px": (20, 10)},
        {"transparent": True},
        {"renderer": None},
    ],
)
def test_the_render_fingerprint_changes_with_every_known_influence(change):
    """RC-076 must_fail：同名新字体仍命中旧缓存——字体政策版本（allowlist sha）进 fingerprint；栅格器版本 /
    后端 build / ppi / 像素 / 透明同理。"""
    base = dict(fonts_version="f1", renderer="pdfium", renderer_version="145.0", px=(10, 5), ppi=72)
    sem = "sha256:" + "ab" * 32
    assert identity.render_identity(sem, **base) != identity.render_identity(
        sem, **{**base, **change}
    )


def test_unknown_dimensions_are_none_not_omitted():
    """不知道的一维写 None，不省略键——省略与 None 是两个规范化值，不会与「知道且为空」撞。"""
    sem = "sha256:" + "ab" * 32
    vector = identity.render_identity(sem, fonts_version="f1")
    assert vector == identity.render_identity(sem, fonts_version="f1", renderer=None, px=None)


def test_the_artifact_hash_is_not_written_into_the_artifact(tmp_path):
    """RC-077 must_fail：把最终 hash 再写进 PDF 导致自引用。manifest 的 artifact 就是文件字节的 sha256，
    文件里不含它（含了 hash 就不再等于文件的 hash——两者互斥）。"""
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(artifactbytes.blank_pdf(200, 100))
    manifest = inspector.inspect(
        pdf, "pdf", plan={"backend": "x", "page_pt": [200, 100]}, run_id="r1"
    )
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    assert manifest["artifact"]["sha256"] == sha == manifest["identity"]["artifact"]
    assert sha.encode() not in pdf.read_bytes()
    assert manifest["identity"]["run"] == "r1"
    # 旧后端 / 没有 RenderPlan 的计划半张：semantic / render 如实是 None，不是编一个
    assert manifest["identity"]["semantic"] is None and manifest["identity"]["render"] is None
    assert manifest["identity"]["identity_version"] == identity.IDENTITY_VERSION


# ===========================================================================
# 公开投影：只带身份与结论，针一个都不许有
# ===========================================================================
NEEDLES = {
    "data_dir": "/Users/某人/Library/Application Support/Tavotto 数据",
    "home": "/Users/某人",
    "prefix": "/opt/envs/实验 venv",
    "interpreter": "/opt/envs/实验 venv/bin/python3.11",
    "tmp": "/private/var/folders/xx/T/tavotto-tmp-1234",
    "script": "ys = [3 * x + 1 for x in xs]",
    "figure_text": "Measured Δ vs t (secret cohort)",
    "argv": "--cohort=secret",
    "source_id": "实验 数据/plot 图.pdf",
}


def _leaky_manifest() -> dict:
    """一份把所有针都塞在**内部**字段里的 manifest（内部完整路径留本机是允许的）。"""
    plan = {
        "backend": "rendercore",
        "plan_identity": "sha256:" + "11" * 32,
        "render_identity": "sha256:" + "22" * 32,
        "page_pt": [200, 100],
        "text": [NEEDLES["figure_text"]],
        "object_boxes": [{"id": NEEDLES["source_id"], "bbox": [0, 0, 1, 1]}],
        "sources": [
            {
                "source_id": NEEDLES["source_id"],
                "origin": "execution",
                "kind": "pdf",
                "bytes_sha256": "33" * 32,
                "receipt_identity": "sha256:" + "44" * 32,
                "patch_hash": "sha1:55",
                "path": NEEDLES["tmp"],
            }
        ],
        "execution_receipts": ["sha256:" + "44" * 32],
        "receipts": [
            {
                "receipt_identity": "sha256:" + "44" * 32,
                "completeness": "complete",
                "generation": 1,
                "source_revision": "abc",
                "python_version": "3.11.14",
                "packages": {"h5py": "3.16.0"},
                "observation": "partial",
                "binding": {
                    "revision": "sha256:66",
                    "matched": True,
                    "changed": 0,
                    "unobserved": 0,
                },
                "interpreter": NEEDLES["interpreter"],
                "prefix": NEEDLES["prefix"],
                "argv": [NEEDLES["argv"]],
                "script": NEEDLES["script"],
            }
        ],
        "nodes": [
            {
                "id": NEEDLES["source_id"],
                "kind": "imported_page",
                "source_id": NEEDLES["source_id"],
                "origin": "execution",
                "receipt_identity": "sha256:" + "44" * 32,
                "internal": "unknown",
            }
        ],
        "nodes_truncated": False,
        "home": NEEDLES["home"],
        "data_dir": NEEDLES["data_dir"],
    }
    manifest = inspector.uninspected(FIXTURE / "page.pdf", "pdf", plan=plan, run_id="run-1")
    manifest["notes"].append(f"期望的文字行没能从文字层抽回来：['{NEEDLES['figure_text']}']")
    manifest["observed"]["text"] = [NEEDLES["figure_text"]]
    return manifest


def test_the_public_projection_carries_identities_and_verdicts_but_no_needles():
    """RC-081 / FO-062 must_fail：绝对字体路径 / 用户路径 / argv / 脚本正文进入可公开报告。内部 manifest
    可以带路径（留本机），`public_projection()` 一根针都不许有；身份与结论一个不少。"""
    manifest = _leaky_manifest()
    internal = json.dumps(manifest, ensure_ascii=False)
    for name, needle in NEEDLES.items():
        assert needle in internal, name  # 针确实塞进去了：判据量得到目标
    public = inspector.public_projection(manifest, trace={"events": [], "failed_phase": None})
    text = json.dumps(public, ensure_ascii=False)
    for name, needle in NEEDLES.items():
        assert needle not in text, f"公开投影泄了 {name}: {needle!r}"
    assert public["identity"]["semantic"] == "sha256:" + "11" * 32
    assert public["identity"]["render"] == "sha256:" + "22" * 32
    assert public["identity"]["run"] == "run-1"
    assert public["sources"][0]["receipt_identity"] == "sha256:" + "44" * 32
    assert "source_id" not in public["sources"][0] and "path" not in public["sources"][0]
    assert public["receipts"][0]["python_version"] == "3.11.14"
    assert public["receipts"][0]["binding"]["matched"] is True
    assert set(public["receipts"][0]) <= set(inspector._PUBLIC_RECEIPT_KEYS)
    assert public["nodes"][0] == {
        "kind": "imported_page",
        "origin": "execution",
        "receipt_identity": "sha256:" + "44" * 32,
        "internal": "unknown",
    }
    assert "notes" not in public and "text" not in public
    assert public["reproducibility"]["byte"].startswith("字节逐位相同不由任何身份保证")
    assert public["trace"] == {"events": [], "failed_phase": None}


def test_summary_carries_identity_and_provenance_for_the_ui():
    manifest = _leaky_manifest()
    s = inspector.summary(manifest)
    assert s["identity"]["run"] == "run-1" and s["provenance"]["receipts"][0]["generation"] == 1


# ===========================================================================
# RC-082：PDF 元数据不是执行 / 读文件的授权
# ===========================================================================
@pytest.fixture
def client(tmp_path, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    exportjob.reset_for_tests()
    yield m.app.test_client()
    m.reset_projects()
    engine_watch.stop()
    exportjob.reset_for_tests()


def test_pdf_metadata_naming_a_script_never_triggers_execution(client, tmp_path, monkeypatch):
    """RC-082 must_fail：打开 PDF 自动运行来源脚本。一份 PDF 的 /Info 里写着 `Source` / `Script` /
    `Producer` 指向项目里一个真实存在的脚本：素材扫描不认它、准备接口是 `static_source_available`、
    一个 worker 都不起、脚本一行不跑。执行的来源只有注册表（扫描出的 script ↔ stem 关系）。"""
    import pymupdf

    figs = tmp_path / "figs"
    figs.mkdir()
    marker = tmp_path / "ran.txt"
    (figs / "evil.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    doc = pymupdf.open()
    doc.new_page(width=200, height=100)
    doc.set_metadata(
        {
            "title": "trap",
            "producer": "python evil.py",
            "creator": "evil.py",
            "subject": "Source: evil.py; Script: evil.py; Command: python evil.py",
            "keywords": "tavotto:script=evil.py",
        }
    )
    doc.save(figs / "trap.pdf")
    doc.close()
    spawned: list = []
    real = engine_pool.build_owned

    def spy(*a, **kw):
        spawned.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(engine_pool, "build_owned", spy)
    resp = client.post("/api/projects/open", json={"path": str(figs)})
    assert resp.status_code == 200, resp.get_json()
    panels = client.get("/api/panels").get_json()["panels"]
    trap = next(p for p in panels if p["id"] == "trap.pdf")
    assert not trap.get("script")
    prep = client.post("/api/engine/preparation", json={"id": "trap.pdf"})
    assert prep.status_code == 202, prep.get_json()
    body = prep.get_json()
    assert body["plan"]["script"] is None
    assert body["result"]["status"] == "static_source_available"
    assert spawned == [] and not marker.exists()


# ===========================================================================
# 候选后端真导出：四身份进 manifest、来源与回执随源、坏在 compose 不触发装包
# ===========================================================================
@pytest.fixture
def candidate(client, tmp_path, monkeypatch):
    from tavotto.rendercore import fonts, renderhost

    reg = fonts.FontRegistry.discover()
    if reg.missing:
        pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
    monkeypatch.setenv(pdfbackend.BACKEND_ENV, pdfbackend.BACKEND_RENDERCORE)
    figs = tmp_path / "figs"
    figs.mkdir(exist_ok=True)
    (figs / "p1.pdf").write_bytes((FIXTURE / "page.pdf").read_bytes())
    m.open_project(str(figs))
    yield client, figs
    renderhost.shutdown_shared()


def _canvas(**over) -> dict:
    spec = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf", "png"],
        "ppi": 150,
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 120,
            "page_h_mm": 60,
            "objects": [
                {"type": "panel", "id": "p1.pdf", "x_mm": 5, "y_mm": 5, "w_mm": 50, "h_mm": 40},
                {"type": "panel", "id": "p1.pdf", "x_mm": 60, "y_mm": 5, "w_mm": 50, "h_mm": 40},
                {
                    "type": "text",
                    "id": "t1",
                    "text": "Hello 图",
                    "x_mm": 80,
                    "y_mm": 50,
                    "w_mm": 30,
                    "h_mm": 8,
                    "size_pt": 10,
                },
            ],
        },
    }
    spec.update(over)
    return spec


def _out(body: dict, fmt: str) -> dict:
    return next(o for o in body["outputs"] if o["format"] == fmt)


@needs_candidate
def test_a_real_export_carries_four_identities_and_a_node_table(candidate):
    """真导出：PDF 与 PNG 出自同一份计划——semantic 相同、render 不同（PNG 多了栅格器与像素参数）、artifact
    等于发布字节的 sha256、run 同一次作业；节点表用画布对象 id（同一份源放两次是两个节点），外来页 internal=unknown。"""
    client, figs = candidate
    body = client.post("/api/export", json=_canvas()).get_json()
    assert body["status"] == "done", body
    pdf, png = _out(body, "pdf")["manifest"], _out(body, "png")["manifest"]
    assert pdf["identity"]["semantic"] == png["identity"]["semantic"] not in (None, "")
    assert pdf["identity"]["render"] != png["identity"]["render"]
    assert pdf["identity"]["run"] == png["identity"]["run"] == body["job_id"]
    out_dir = Path(body["export_dir"])
    for fmt, mani in (("pdf", pdf), ("png", png)):
        name = _out(body, fmt)["name"]
        assert (
            mani["identity"]["artifact"]
            == hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
        )
        assert mani["identity"]["artifact"].encode() not in (out_dir / name).read_bytes()
    nodes = pdf["provenance"]["nodes"]
    pages = [n for n in nodes if n["kind"] == "imported_page"]
    assert len(pages) == 2 and all(n["internal"] == "unknown" for n in pages)
    assert all(n["source_id"] == "p1.pdf" and n["origin"] == "static" for n in pages)
    # RC-079：同一份资产放两次是两个实例，节点 id 是画布对象 id + 实例序号（不是 PDF object number）；
    # 中间插了别的对象不影响这两个实例的 id
    assert [n["node"] for n in pages] == ["p1.pdf", "p1.pdf#2"]
    assert [n["instance"] for n in pages] == [1, 2]
    assert [n["kind"] for n in nodes if n["id"] == "t1"] == ["text"]
    assert [n["node"] for n in nodes if n["id"] == "t1"] == ["t1"]
    assert pdf["provenance"]["nodes_truncated"] is False
    # 静态源没有回执：receipts 是空的，如实
    assert (
        pdf["provenance"]["receipts"] == []
        and pdf["provenance"]["sources"][0]["origin"] == "static"
    )
    # 轨迹：候选路的四步都在、没有坏的
    tr = body["trace"]
    assert [e["phase"] for e in tr["events"]] == [
        "prepare",
        "source",
        "compile",
        "compose",
        "raster",
        "inspect",
        "publish",
    ]
    assert tr["failed_phase"] is None


@needs_candidate
def test_the_node_table_is_bounded(candidate, monkeypatch):
    """RC-080：节点超限截断并如实记 `nodes_truncated`，不无限记。"""
    from tavotto.rendercore import job as rc_job

    monkeypatch.setattr(rc_job, "NODES_LIMIT", 2)
    client, figs = candidate
    body = client.post("/api/export", json=_canvas(formats=["pdf"])).get_json()
    assert body["status"] == "done", body
    prov = _out(body, "pdf")["manifest"]["provenance"]
    assert len(prov["nodes"]) == 2 and prov["nodes_truncated"] is True


@needs_candidate
def test_a_writer_failure_is_pinned_to_compose_and_never_reaches_the_dependency_door(
    candidate, monkeypatch
):
    """FO-066 must_fail：故障阶段不准 / PDF 错误触发装包。写入器炸了 → 那一格 `format_failed`、轨迹
    `failed_phase == compose`；依赖那道门（`deprepair.gate` / `prepare`）一次都没被问。"""
    from tavotto.rendercore import pdfwriter

    def boom(*a, **kw):
        raise pdfwriter.WriterError("source_unreadable", "写入器炸了")

    asked: list = []
    monkeypatch.setattr(pdfwriter, "write_pdf", boom)
    monkeypatch.setattr(deprepair, "gate", lambda *a, **kw: asked.append(("gate", a)) or None)
    monkeypatch.setattr(deprepair, "prepare", lambda *a, **kw: asked.append(("prepare", a)) or None)
    client, figs = candidate
    body = client.post("/api/export", json=_canvas(formats=["pdf"])).get_json()
    assert body["status"] == "failed", body
    assert _out(body, "pdf")["error"]["code"] == "format_failed"
    assert body["trace"]["failed_phase"] == "compose"
    assert body["trace"]["failed_code"] == "source_unreadable"
    assert asked == []
