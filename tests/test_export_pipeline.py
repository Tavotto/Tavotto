"""统一导出管线（ADR 0031）：原图 / 画布、原子性、取消、部分失败、覆盖策略。

按 CLAUDE.md 的验证约定，导出 PDF 用 pymupdf 解析结构验证，不比字节
（PDF 里有时间戳）。**不只断言"文件存在"**——那条判据在实现坏掉时照样绿。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pymupdf
import pytest

from tavotto import app as m
from tavotto.engine import exportjob, exportreq

# 源图：200 × 150 pt 的矢量 PDF。**这个尺寸是判据的一部分**——原图导出必须
# 出来 200 × 150，无论它在画布上被摆成多大
SRC_W_PT, SRC_H_PT = 200.0, 150.0


@pytest.fixture
def env(tmp_path, monkeypatch):
    figs = tmp_path / "figs"
    figs.mkdir()
    doc = pymupdf.open()
    page = doc.new_page(width=SRC_W_PT, height=SRC_H_PT)
    page.draw_rect(pymupdf.Rect(10, 10, 90, 90), color=None, fill=(0.1, 0.2, 0.8))
    page.insert_text((20, 120), "PanelText", fontsize=11)
    doc.save(figs / "p1.pdf")
    doc.close()

    # 位图源：120 × 80 像素。原图 PNG 导出必须**保持这个像素网格**
    raster = pymupdf.open()
    rp = raster.new_page(width=120, height=80)
    rp.draw_rect(pymupdf.Rect(0, 0, 60, 80), color=None, fill=(0, 0, 0))
    pix = rp.get_pixmap(alpha=False)
    pix.save(figs / "r1.png")
    raster.close()

    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    m.open_project(str(figs))
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    return m.app.test_client(), figs


def _post(client, spec, path="/api/export"):
    resp = client.post(path, json=spec)
    return resp.status_code, resp.get_json()


def _canvas(**over):
    spec = {
        "scope": "canvas",
        "filename": "Fig 1",
        "formats": ["pdf"],
        "canvas": {
            "page_w_mm": 180,
            "page_h_mm": 120,
            "objects": [
                {"type": "panel", "id": "p1.pdf", "x_mm": 10, "y_mm": 10, "w_mm": 40, "h_mm": 30}
            ],
        },
    }
    spec.update(over)
    return spec


def _original(**over):
    spec = {
        "scope": "original",
        "filename": "Fig 1",
        "formats": ["pdf"],
        "original": {"figure_id": "p1.pdf", "w_mm": 70.6, "h_mm": 52.9, "source_kind": "vector"},
    }
    spec.update(over)
    return spec


def _out(body, fmt):
    return next(o for o in body["outputs"] if o["format"] == fmt)


def _dir(body) -> Path:
    return Path(body["export_dir"])


# --------------------------- 原图不套用画布缩放 ------------------------------
def test_original_export_ignores_the_layout_scale(env):
    """**这一条是 scope=original 的全部意义。**

    面板在画布上被摆成 40 × 30 mm，而它自己是 200 × 150 pt。原图导出出来的
    必须是 200 × 150 pt——跟着画布缩的话，图里的字号会一起缩，那正是共享
    规则 §8 要挡的「原图导出偷偷套用画布缩放」。
    """
    client, _ = env
    status, body = _post(client, _original())
    assert status == 200, body
    assert body["status"] == "done"
    pdf = _out(body, "pdf")
    path = _dir(body) / pdf["name"]
    with pymupdf.open(path) as doc:
        assert doc.page_count == 1
        assert round(doc[0].rect.width, 1) == SRC_W_PT
        assert round(doc[0].rect.height, 1) == SRC_H_PT


def test_canvas_export_is_faithful_to_the_canvas(env):
    """画布导出忠实于画布：页面就是 180 × 120 mm。"""
    client, _ = env
    status, body = _post(client, _canvas())
    assert status == 200, body
    with pymupdf.open(_dir(body) / _out(body, "pdf")["name"]) as doc:
        assert round(doc[0].rect.width, 1) == round(180 / 25.4 * 72, 1)
        assert round(doc[0].rect.height, 1) == round(120 / 25.4 * 72, 1)


def test_original_pdf_stays_vector(env):
    """矢量源整页搬运，不重画：文字仍然是可提取的文字，不是一张位图。"""
    client, _ = env
    _, body = _post(client, _original())
    pdf = _out(body, "pdf")
    assert pdf["vector"] is True
    with pymupdf.open(_dir(body) / pdf["name"]) as doc:
        assert "PanelText" in doc[0].get_text()
        assert len(doc[0].get_images(full=True)) == 0


def test_original_png_of_a_raster_source_keeps_the_native_pixel_grid(env):
    """位图源**不按导出 ppi 重采样**。

    按 600 ppi 缩一遍的话，一张 120 × 80 的图会变成 750 × 500 的糊图，
    而用户点的按钮上写着"原图尺寸"。
    """
    client, _ = env
    _, body = _post(
        client,
        _original(
            formats=["png"],
            ppi=600,
            original={"figure_id": "r1.png", "source_kind": "raster", "px_w": 120, "px_h": 80},
        ),
    )
    png = _out(body, "png")
    assert png["status"] == "done"
    assert png["dimensions"]["px"] == [120, 80]
    assert png["vector"] is False


def test_original_png_of_a_vector_source_rasterizes_at_the_requested_ppi(env):
    """矢量源没有像素网格，**ppi 说了算**。

    判据不是"等于某个数"（那样量的是渲染库的取整方式），而是**换个 ppi
    出来的像素数按比例变**——位图源那条路上它是恒定的，两条路各有各的答案。
    """
    client, _ = env
    _, low = _post(client, _original(formats=["png"], ppi=150))
    _, high = _post(client, _original(filename="Fig 2", formats=["png"], ppi=300))
    lo = _out(low, "png")["dimensions"]["px"][0]
    hi = _out(high, "png")["dimensions"]["px"][0]
    assert abs(lo - SRC_W_PT / 72.0 * 150) <= 1
    assert abs(hi - SRC_W_PT / 72.0 * 300) <= 1
    assert hi > lo


# ------------------------------ 多格式同一快照 --------------------------------
def test_pdf_and_png_come_from_one_snapshot(env):
    """一次请求要两个格式 = **一个作业、一份对象快照**。

    两次请求各出一个格式的话，中间那一瞬的编辑会让两份产物对不上，
    而用户会以为它们是同一张图的两种载体。
    """
    client, _ = env
    _, body = _post(client, _canvas(formats=["pdf", "png"], ppi=300))
    assert body["status"] == "done"
    assert [o["format"] for o in body["outputs"]] == ["pdf", "png"]
    pdf, png = _out(body, "pdf"), _out(body, "png")
    # PNG 的像素数 = PDF 的 pt 尺寸 × ppi/72：同一页渲出来的，不是第二次合成
    with pymupdf.open(_dir(body) / pdf["name"]) as doc:
        assert png["dimensions"]["px"][0] == round(doc[0].rect.width / 72.0 * 300)


def test_document_revision_is_echoed_back(env):
    """服务端看不见前端的编辑，所以它**原样回传**开始那一刻的指纹，
    由客户端拿它与当前值一比，说出"导出期间又被编辑过"。"""
    client, _ = env
    _, body = _post(client, _canvas(document_revision="rev-abc"))
    assert body["document_revision"] == "rev-abc"


# -------------------------------- 覆盖策略 -----------------------------------
def test_ask_policy_stops_before_doing_anything(env):
    """先问再动手：撞名时**不渲染、不写盘**，回一个 conflict 与撞名清单。"""
    client, _ = env
    _, first = _post(client, _canvas())
    assert first["status"] == "done"
    before = (_dir(first) / "Fig 1.pdf").read_bytes()

    _, second = _post(client, _canvas())
    assert second["status"] == "conflict"
    assert second["conflicts"] == ["Fig 1.pdf"]
    assert second["outputs"] == []
    # 原文件一个字节没动
    assert (_dir(first) / "Fig 1.pdf").read_bytes() == before


def test_replace_policy_overwrites_and_says_so(env):
    client, _ = env
    _post(client, _canvas())
    _, body = _post(client, _canvas(overwrite="replace"))
    assert body["status"] == "done"
    assert _out(body, "pdf")["replaced"] is True
    assert sorted(p.name for p in _dir(body).iterdir()) == ["Fig 1.pdf"]


def test_rename_policy_numbers_the_new_file(env):
    client, _ = env
    _post(client, _canvas())
    _, body = _post(client, _canvas(overwrite="rename"))
    assert _out(body, "pdf")["name"] == "Fig 1 (2).pdf"
    assert sorted(p.name for p in _dir(body).iterdir()) == ["Fig 1 (2).pdf", "Fig 1.pdf"]


# --------------------------------- 原子性 ------------------------------------
def test_nothing_is_left_behind_when_a_format_fails(env, monkeypatch):
    """一个格式挂了：另一个照常交付，作业报 `partial`，**临时目录被清掉**。

    「部分成功报成全部成功」与「一项失败就把另一项已经渲好的成果扔掉」
    都是说谎，只是方向相反。
    """
    client, _ = env
    real_save = m.pdfbackend.compose

    class Boom(Exception):
        pass

    def broken_compose(w, h, transparent=False):
        canvas = real_save(w, h, transparent)
        original = canvas.save_png

        def fail(*a, **kw):
            raise Boom("PNG 写不出来")

        canvas.save_png = fail
        assert original is not None
        return canvas

    monkeypatch.setattr(m.pdfbackend, "compose", broken_compose)
    _, body = _post(client, _canvas(formats=["pdf", "png"], ppi=300))
    assert body["status"] == "partial"
    assert _out(body, "pdf")["status"] == "done"
    assert _out(body, "png")["status"] == "failed"
    assert _out(body, "png")["error"]["code"] == "format_failed"
    names = sorted(p.name for p in _dir(body).iterdir())
    assert names == ["Fig 1.pdf"], f"导出目录里不该有别的东西：{names}"


def test_no_half_written_file_survives_a_publish_failure(env, monkeypatch):
    """落最终位置那一步失败：导出目录里**不留半个文件**（共享规则 §8）。"""
    client, _ = env

    def refuse(tmp, dest):
        raise OSError("磁盘满了")

    monkeypatch.setattr(exportjob.atomicio, "publish_file", refuse)
    _, body = _post(client, _canvas())
    assert body["status"] == "failed"
    assert list(_dir(body).iterdir()) == []


def test_stale_temp_dirs_are_swept(env):
    """上一次进程被 kill 留下的临时目录不该一直躺在用户的导出目录里。"""
    client, _ = env
    _, body = _post(client, _canvas())
    out = _dir(body)
    junk = out / f"{exportjob.TMP_PREFIX}deadbeef"
    junk.mkdir()
    (junk / "half.pdf").write_bytes(b"x")
    _post(client, _canvas(filename="Fig 2"))
    assert not junk.exists()
    # 用户自己放在导出目录里的东西一个不碰
    keep = out / "我的笔记.txt"
    keep.write_text("hi", encoding="utf-8")
    _post(client, _canvas(filename="Fig 3"))
    assert keep.exists()


# ---------------------------------- 取消 -------------------------------------
def test_cancel_writes_nothing_and_cleans_up(env, monkeypatch):
    """取消：最终目录一个字节没动过，临时目录也不留。"""
    client, _ = env
    gate = threading.Event()
    real_produce = m._export_produce

    def slow(job, tmp_dir):
        gate.wait(5)
        job.check_cancelled()
        return real_produce(job, tmp_dir)

    monkeypatch.setattr(m, "_export_produce", slow)
    status, started = _post(client, _canvas(), path="/api/export/start")
    assert status == 200
    job_id = started["job_id"]

    assert client.post("/api/export/cancel", json={"job_id": job_id}).get_json()["cancelling"]
    gate.set()
    for _ in range(200):
        body = client.get(f"/api/export/state?job_id={job_id}").get_json()
        if body["status"] in ("cancelled", "done", "failed", "partial"):
            break
        time.sleep(0.02)
    assert body["status"] == "cancelled"
    assert body["outputs"] == []
    out = Path(body["export_dir"])
    assert not out.exists() or list(out.iterdir()) == []


def test_cancelling_an_unknown_job_is_not_an_error(env):
    client, _ = env
    assert client.post("/api/export/cancel", json={"job_id": "nope"}).get_json() == {
        "cancelling": False
    }


def test_async_job_reaches_a_terminal_state(env):
    client, _ = env
    _, started = _post(client, _canvas(), path="/api/export/start")
    for _ in range(200):
        body = client.get(f"/api/export/state?job_id={started['job_id']}").get_json()
        if body["status"] in ("done", "partial", "failed"):
            break
        time.sleep(0.02)
    assert body["status"] == "done"
    assert (Path(body["export_dir"]) / _out(body, "pdf")["name"]).is_file()
    assert body["timing"]["elapsed_ms"] is not None


# -------------------------------- 样式检查报告 --------------------------------
def test_style_check_report_carries_the_server_side_facts(env):
    """报告里必须有只有服务端知道的那半份：版本、时间、真正落盘的产物。"""
    client, _ = env
    _, body = _post(
        client,
        _canvas(
            formats=["pdf"],
            include_style_check_report=True,
            style_check_report={"kind": "tavotto-proof", "checks": [], "profile": {"x": 1}},
        ),
    )
    assert body["status"] == "done"
    report = next(o for o in body["outputs"] if o["format"] == "report")
    assert report["name"] == "Fig 1_style-check.json"
    data = json.loads((_dir(body) / report["name"]).read_text(encoding="utf-8"))
    assert data["version"] == 3
    assert data["scope"] == "canvas"
    assert data["formats"] == ["pdf"]
    assert data["tavotto_version"]
    assert data["exported_at"]
    assert [f["name"] for f in data["files"]] == ["Fig 1.pdf"]
    # 报告里不许有绝对路径
    assert str(_dir(body)) not in json.dumps(data, ensure_ascii=False)


def test_a_failing_report_does_not_take_the_images_down(env, monkeypatch):
    """§七：报告生成失败不该让图文件全部失败，但必须清楚说明部分失败。"""
    client, _ = env
    monkeypatch.setattr(
        exportjob.atomicio,
        "dumps_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("报告炸了")),
    )
    _, body = _post(
        client,
        _canvas(include_style_check_report=True, style_check_report={"checks": []}),
    )
    assert body["status"] == "partial"
    assert _out(body, "pdf")["status"] == "done"
    report = next(o for o in body["outputs"] if o["format"] == "report")
    assert report["status"] == "failed"
    assert (_dir(body) / "Fig 1.pdf").is_file()


def test_report_is_only_written_when_asked(env):
    client, _ = env
    _, body = _post(client, _canvas())
    assert [o["format"] for o in body["outputs"]] == ["pdf"]
    assert sorted(p.name for p in _dir(body).iterdir()) == ["Fig 1.pdf"]


# --------------------------------- 透明背景 -----------------------------------
def _corner_alpha(path: Path) -> int:
    """右下角那个像素的 alpha。

    量的是**那个点透不透明**，不是"这张 PNG 有没有 alpha 通道"——
    带 alpha 通道、底下却铺了一层白，`pix.alpha` 照样是 1，而用户拿到的是
    一张不透明的图。（这一条是变异反证当场抓出来的：判据的主语对了，
    维度错了。）
    """
    pix = pymupdf.Pixmap(str(path))
    assert pix.alpha == 1, "这张 PNG 根本没有 alpha 通道"
    n = pix.n
    idx = ((pix.height - 1) * pix.stride) + (pix.width - 1) * n
    return pix.samples[idx + n - 1]


def test_transparent_background_actually_leaves_the_background_transparent(env):
    client, _ = env
    _, body = _post(client, _canvas(formats=["png"], ppi=150, background="transparent"))
    assert _corner_alpha(_dir(body) / _out(body, "png")["name"]) == 0

    _, opaque = _post(
        client, _canvas(filename="Fig 2", formats=["png"], ppi=150, background="white")
    )
    # 默认背景不带 alpha 通道，也就无从"透明"
    assert pymupdf.Pixmap(str(_dir(opaque) / _out(opaque, "png")["name"])).alpha == 0


# ------------------------------ 预校验与错误 ----------------------------------
def test_validate_reports_conflicts_without_writing_anything(env):
    client, _ = env
    _post(client, _canvas())
    resp = client.post("/api/export/validate", json=_canvas())
    body = resp.get_json()
    assert body["ok"] is True
    assert body["conflicts"] == ["Fig 1.pdf"]
    assert body["names"] == {"pdf": "Fig 1.pdf"}
    assert body["ppi_applies"] is False


def test_validate_reports_a_bad_filename_as_a_structured_error(env):
    client, _ = env
    body = client.post("/api/export/validate", json=_canvas(filename="Fig?1")).get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_filename"
    assert body["error"]["params"]["reason"] == "illegal_char"


def test_bad_request_never_starts_a_job(env):
    client, _ = env
    status, body = _post(client, _canvas(filename="CON"))
    assert status == 400
    assert body["code"] == "bad_filename"
    assert "error" in body  # 旧前端与 curl 只看得到这一句
    assert exportjob.snapshot()["count"] == 0


def test_missing_source_is_a_structured_error(env):
    client, _ = env
    status, body = _post(
        client, _original(original={"figure_id": "nope.pdf", "source_kind": "vector"})
    )
    assert status in (200, 500)
    assert body["status"] == "failed"
    assert body["error"]["code"] in ("source_missing", "export_failed")


# ---------------------------- 旧契约一个字节不变 -------------------------------
def test_legacy_payload_keeps_the_timestamped_name_and_files_shape(env):
    """老标签页与 CI 脚本读的是 `files[]`，写的是 `<stem>_<时间戳>.<ext>`。"""
    client, _ = env
    status, body = _post(
        client,
        {
            "page_w_mm": 100,
            "page_h_mm": 50,
            "dpi": 300,
            "formats": ["pdf"],
            "stem": "legacy",
            "objects": [],
        },
    )
    assert status == 200
    assert len(body["files"]) == 1
    name = body["files"][0]["name"]
    assert name.startswith("legacy_") and name.endswith(".pdf")
    assert body["files"][0]["url"] == f"/exports/{name}"
    assert "warnings" in body
    # 旧路径的报告仍叫 `_proof.json`（`tests/test_package.py` 读它）
    _, with_proof = _post(
        client,
        {
            "page_w_mm": 100,
            "page_h_mm": 50,
            "formats": ["pdf"],
            "stem": "legacy",
            "objects": [],
            "proof": {"checks": []},
        },
    )
    assert any(f["name"].endswith("_proof.json") for f in with_proof["files"])


def test_ppi_key_and_dpi_key_are_the_same_setting(env):
    """新载荷用 `ppi`，旧载荷用 `dpi`。**同一个设置只能有一份含义。**"""
    client, _ = env
    _, a = _post(client, _canvas(filename="A", formats=["png"], ppi=150))
    _, b = _post(client, _canvas(filename="B", formats=["png"], dpi=150))
    assert _out(a, "png")["dimensions"]["px"] == _out(b, "png")["dimensions"]["px"]


def test_exportreq_error_codes_are_all_registered(env):
    """新增一个 code 而不加进 `ERROR_CODES`，两种语言的文案不会被要求补齐。"""
    del env
    raised = set()
    for spec in (
        {},
        {"formats": []},
        {"formats": ["tiff"], "filename": "x"},
        {"formats": ["png"], "filename": "x", "ppi": 1},
        {"formats": ["pdf"], "filename": "Fig?"},
        {"formats": ["pdf"], "filename": "x", "scope": "nope"},
        {"formats": ["pdf"], "filename": "x", "scope": "original"},
    ):
        try:
            exportreq.normalize(spec)
        except exportreq.ExportRequestError as exc:
            raised.add(exc.code)
    assert raised
    assert raised <= set(exportreq.ERROR_CODES), raised - set(exportreq.ERROR_CODES)


# ---------------------- 后台线程必须知道自己在为谁干活 -------------------------
def test_a_background_job_stays_in_the_project_that_started_it(tmp_path, monkeypatch):
    """同时开着两个项目时，为项目 B 起的后台导出**不许**去项目 A 解析面板。

    `_request_ctx()` 的兜底是"默认项目"，而后台线程没有请求上下文。兜底本身
    没错（watcher 与启动流程确实该落到默认项目），错的是让一个**知道自己在
    为谁干活**的线程去走兜底——那样用户会拿到另一个图库里同名的那张图。

    判据用"出来的是哪张图"而不是"有没有报错"：两个项目里都有 `p1.pdf`，
    只是尺寸不同。少了绑定的话作业照样成功，只是成功地导出了错的那一张。
    """

    def make(dirname: str, w: float, h: float) -> Path:
        root = tmp_path / dirname
        root.mkdir()
        doc = pymupdf.open()
        doc.new_page(width=w, height=h)
        doc.save(root / "p1.pdf")
        doc.close()
        return root

    a = make("proj_a", 100.0, 100.0)
    b = make("proj_b", 300.0, 200.0)
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    exportjob.reset_for_tests()
    m.app.config["TESTING"] = True
    m.open_project(str(a))  # A 是默认项目
    status_b = m.open_project(str(b), make_default=False)
    pid_b = status_b["id"]
    assert m.DEFAULT_PROJECT != pid_b

    client = m.app.test_client()
    started = client.post(
        f"/api/export/start?pj={pid_b}",
        json=_original(original={"figure_id": "p1.pdf", "source_kind": "vector"}),
    ).get_json()
    for _ in range(200):
        body = client.get(f"/api/export/state?job_id={started['job_id']}&pj={pid_b}").get_json()
        if body["status"] in ("done", "partial", "failed", "cancelled"):
            break
        time.sleep(0.02)
    assert body["status"] == "done", body
    out = Path(body["export_dir"]) / _out(body, "pdf")["name"]
    # 导出目录属于 B，出来的图也必须是 B 的那张
    assert b.name in str(out)
    with pymupdf.open(out) as doc:
        assert (round(doc[0].rect.width), round(doc[0].rect.height)) == (300, 200)
