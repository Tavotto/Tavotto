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
from tavotto.engine import exportjob, exportreq, originalspec as engine_originalspec

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

    # JPEG 源：原图 PNG 导出必须**转码**，不能把 JPEG 字节塞进 .png
    jpeg = pymupdf.open()
    jp = jpeg.new_page(width=90, height=60)
    jp.draw_rect(pymupdf.Rect(0, 0, 45, 60), color=None, fill=(0.9, 0.1, 0.1))
    jp.get_pixmap(alpha=False).save(figs / "j1.jpg")
    jpeg.close()

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
def test_a_failed_check_is_recorded_in_the_report(env):
    """「检查没跑成、用户确认了继续」在报告里必须与「干干净净跑过一遍」分得开。

    只看 `forced`（有 error 才为真）与 `acknowledged`（要有规则码才非空）的话，
    那两种情形长得一模一样——而确认框上写着这次确认会被记进报告
    （PR #214 第三轮评审）。
    """
    client, _ = env
    _, body = _post(
        client,
        _canvas(
            include_style_check_report=True,
            style_check_report={
                "checks": [],
                "forced": False,
                "acknowledged": [],
                "check_failed": True,
                "acknowledged_check_failed": True,
            },
        ),
    )
    report = next(o for o in body["outputs"] if o["format"] == "report")
    data = json.loads((_dir(body) / report["name"]).read_text(encoding="utf-8"))
    assert data["check_failed"] is True
    assert data["acknowledged_check_failed"] is True

    _, clean = _post(
        client,
        _canvas(
            filename="Clean",
            include_style_check_report=True,
            style_check_report={
                "checks": [],
                "forced": False,
                "acknowledged": [],
                "check_failed": False,
                "acknowledged_check_failed": False,
            },
        ),
    )
    clean_report = next(o for o in clean["outputs"] if o["format"] == "report")
    clean_data = json.loads((_dir(clean) / clean_report["name"]).read_text(encoding="utf-8"))
    assert clean_data["check_failed"] is False
    assert clean_data != data, "两种情形在报告里必须分得出来"


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


# ================= PR #214 评审的六条（每条一组用例） =========================
def test_raster_original_pdf_honours_the_resolved_physical_size(env, tmp_path):
    """位图源装进 PDF 时，**页面尺寸用已经解析好的那个**，不是现猜一个密度。

    界面上显示的尺寸来自 `OriginalOutputSpec`；文件里的页面尺寸要是另算一遍，
    两者就各说各的。第一版 `pdfbackend` 里写死 96 dpi，于是一张 120×80 的图
    出来是 90×60 pt，而请求里明明白白写着 10.16 × 6.77 mm（= 300 dpi）。
    """
    client, _ = env
    _, body = _post(
        client,
        _original(
            filename="R",
            formats=["pdf"],
            original={
                "figure_id": "r1.png",
                "source_kind": "raster",
                "px_w": 120,
                "px_h": 80,
                # 120px @ 300dpi = 10.16mm；@ 96dpi 会是 31.75mm
                "w_mm": 10.16,
                "h_mm": 6.77,
            },
        ),
    )
    pdf = _out(body, "pdf")
    assert pdf["status"] == "done", body
    assert pdf["vector"] is False, "位图装进 PDF 不许声称是矢量"
    with pymupdf.open(_dir(body) / pdf["name"]) as doc:
        w_pt, h_pt = doc[0].rect.width, doc[0].rect.height
    assert abs(w_pt - 10.16 / 25.4 * 72) < 0.5, f"页面宽 {w_pt}pt 与请求里的 10.16mm 不符"
    assert abs(h_pt - 6.77 / 25.4 * 72) < 0.5
    # 96 dpi 那个错答案要能被这条用例分辨出来
    assert abs(w_pt - 120 / 96 * 72) > 5


def _strip_phys(src: Path, dst: Path) -> None:
    """复制一份去掉 pHYs 块的 PNG —— 「文件没写物理密度」那一档。"""
    data = src.read_bytes()
    out = bytearray(data[:8])  # 签名
    i = 8
    while i < len(data):
        length = int.from_bytes(data[i : i + 4], "big")
        ctype = data[i + 4 : i + 8]
        end = i + 12 + length
        if ctype != b"pHYs":
            out += data[i:end]
        i = end
    dst.write_bytes(bytes(out))


def test_raster_page_size_follows_the_file_not_a_constant(env):
    """请求没带尺寸时（老客户端 / MCP），密度**只从 `engine/originalspec` 取**。

    判据是「两个只有密度不同的文件出来的页面尺寸也不同」，不是「等于某个数」
    ——后者拿实现里那个常量当期望值，等于自己验自己。写死任何一个常量都会让
    这两张图出来一样大，当场红。
    """
    client, figs = env
    # ① pymupdf 存出来的 PNG 自带 pHYs=96；② 去掉 pHYs 那一份走 assumed（PNG 600）
    _strip_phys(figs / "r1.png", figs / "r_nophys.png")

    _, tagged = _post(
        client, _original(filename="T", formats=["pdf"], original={"figure_id": "r1.png"})
    )
    _, plain = _post(
        client, _original(filename="P", formats=["pdf"], original={"figure_id": "r_nophys.png"})
    )
    with pymupdf.open(_dir(tagged) / _out(tagged, "pdf")["name"]) as doc:
        w_tagged = doc[0].rect.width
    with pymupdf.open(_dir(plain) / _out(plain, "pdf")["name"]) as doc:
        w_plain = doc[0].rect.width

    spec_tagged = engine_originalspec.asset_spec(
        figs / "r1.png", "raster", m.pdfbackend.probe_asset(figs / "r1.png", "raster")
    )
    assert spec_tagged["dpi_source"] == "metadata"
    spec_plain = engine_originalspec.asset_spec(
        figs / "r_nophys.png",
        "raster",
        m.pdfbackend.probe_asset(figs / "r_nophys.png", "raster"),
    )
    assert spec_plain["dpi_source"] == "assumed"
    # 两张图像素数完全一样，只有密度不同 → 页面尺寸必须不同
    assert abs(w_tagged - w_plain) > 1, "页面尺寸没跟着文件里的密度走（是不是写死了一个常量？）"
    assert abs(w_tagged - spec_tagged["logical_w_mm"] / 25.4 * 72) < 0.5
    assert abs(w_plain - spec_plain["logical_w_mm"] / 25.4 * 72) < 0.5


def test_the_style_check_report_obeys_the_overwrite_policy(env):
    """报告也是会写进最终目录的产物，覆盖策略对它同样成立。

    三种情形各验一次：`ask` 撞到**只有报告在**时也要停下来；`rename` 给报告
    编号而不是覆盖；`replace` 才允许盖。
    """
    client, _ = env
    spec = dict(_canvas(include_style_check_report=True, style_check_report={"checks": []}))
    _, first = _post(client, spec)
    assert first["status"] == "done"
    out = _dir(first)
    report = next(o for o in first["outputs"] if o["format"] == "report")
    assert report["name"] == "Fig 1_style-check.json"

    # 图删掉、只留报告：`ask` 仍然要停下来（第一版会静默盖掉它）
    (out / "Fig 1.pdf").unlink()
    _, second = _post(client, spec)
    assert second["status"] == "conflict"
    assert second["conflicts"] == ["Fig 1_style-check.json"]

    # `rename`：报告跟着编号
    _, third = _post(client, {**spec, "overwrite": "rename"})
    assert third["status"] == "done"
    named = next(o for o in third["outputs"] if o["format"] == "report")
    assert named["name"] == "Fig 1 (2)_style-check.json"
    assert (out / "Fig 1_style-check.json").is_file(), "原报告不该被动"


def test_two_concurrent_asks_do_not_silently_clobber_each_other(env, monkeypatch):
    """两个作业同时导同一个新文件名：**后完成的不许静默盖掉先完成的**。

    只查磁盘挡不住——两边都能通过存在性检查（那一刻磁盘上确实没有），
    渲染半分钟之后两边都 `os.replace`，而两边的用户都看到了"导出成功"。
    """
    client, _ = env
    started = threading.Event()
    hold = threading.Event()
    real = m._export_produce

    def slow(job, tmp_dir):
        started.set()
        hold.wait(5)
        return real(job, tmp_dir)

    monkeypatch.setattr(m, "_export_produce", slow)
    _, a = _post(client, _canvas(), path="/api/export/start")
    assert started.wait(5)

    # A 还在渲染（名字已经被它预定），B 现在来导同一个名字
    monkeypatch.setattr(m, "_export_produce", real)
    _, b = _post(client, _canvas())
    assert b["status"] == "conflict", "并发的第二个 ask 必须报冲突而不是排队覆盖"
    assert b["conflicts"] == ["Fig 1.pdf"]

    hold.set()
    for _ in range(200):
        done = client.get(f"/api/export/state?job_id={a['job_id']}").get_json()
        if done["status"] in ("done", "partial", "failed", "cancelled"):
            break
        time.sleep(0.02)
    assert done["status"] == "done"
    # 预留在作业结束时释放：下一次 ask 看到的是磁盘上那份，不是幽灵预留
    _, c = _post(client, _canvas())
    assert c["status"] == "conflict"
    assert c["conflicts"] == ["Fig 1.pdf"]
    _, d = _post(client, _canvas(filename="别的名字"))
    assert d["status"] == "done", "别的名字不该被上一次的预留挡住"


# ============== PR #214 复审的六条（每条一组用例） ===========================
def test_a_jpeg_source_is_transcoded_not_copied_into_a_png_name(env):
    """把 JPEG 字节塞进一个叫 `.png` 的文件 = 交出一个坏文件。

    签名是 JPEG、扩展名与 MIME 是 PNG，严格的读取端直接判它损坏。转码只换
    容器，**像素数一个不变**。
    """
    client, figs = env
    src = figs / "j1.jpg"
    assert src.read_bytes()[:3] == b"\xff\xd8\xff", "夹具本身得是真 JPEG"
    _, body = _post(
        client,
        _original(formats=["png"], original={"figure_id": "j1.jpg", "source_kind": "raster"}),
    )
    out = _dir(body) / _out(body, "png")["name"]
    assert out.suffix == ".png"
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "叫 .png 的文件必须真的是 PNG"
    # 像素网格照抄源文件（转码不是重采样）
    src_pix = pymupdf.Pixmap(str(src))
    assert _out(body, "png")["dimensions"]["px"] == [src_pix.width, src_pix.height]


def test_a_png_source_is_copied_byte_for_byte(env):
    """源本来就是 PNG 时逐字节复制——元数据（pHYs 之类）一起留住。"""
    client, figs = env
    _, body = _post(
        client,
        _original(formats=["png"], original={"figure_id": "r1.png", "source_kind": "raster"}),
    )
    out = _dir(body) / _out(body, "png")["name"]
    assert out.read_bytes() == (figs / "r1.png").read_bytes()


def test_transparent_background_reaches_the_original_vector_path(env):
    """原图 + 矢量源 + PNG：勾了透明就得真的透明。

    第一版这条路上根本收不到 background，永远 `alpha=False`——界面上那个
    开关**说了而不做**。
    """
    client, _ = env
    _, body = _post(client, _original(formats=["png"], ppi=150, background="transparent"))
    assert _corner_alpha(_dir(body) / _out(body, "png")["name"]) == 0
    _, opaque = _post(client, _original(filename="O", formats=["png"], ppi=150))
    assert pymupdf.Pixmap(str(_dir(opaque) / _out(opaque, "png")["name"])).alpha == 0


def test_a_running_job_is_never_swept_by_the_ttl(env, monkeypatch):
    """跑超过 TTL 的作业**不许被当成过期清掉**。

    第一版按 `max(created_at, finished_at)` 判，而在跑的作业 `finished_at`
    是 0：临时目录被删、作业从表里消失，发起它的客户端拿到 `unknown`，
    而生产者还在往一个已经不存在的目录里写。
    """
    client, _ = env
    gate = threading.Event()
    real = m._export_produce

    def slow(job, tmp_dir):
        gate.wait(5)
        return real(job, tmp_dir)

    monkeypatch.setattr(m, "_export_produce", slow)
    _, started = _post(client, _canvas(), path="/api/export/start")
    job = exportjob.get(started["job_id"])
    assert job is not None
    # 把它伪装成"很久以前建的"，然后触发一次 prepare（`_sweep` 挂在那里）
    job.created_at = time.time() - 10 * exportjob._TTL_S
    client.post("/api/export/validate", json=_canvas(filename="别的"))
    _post(client, _canvas(filename="别的"), path="/api/export/start")

    assert exportjob.get(started["job_id"]) is not None, "在跑的作业被 TTL 清掉了"
    assert job._tmp_dir is not None and job._tmp_dir.is_dir(), "在跑的作业的临时目录被删了"
    gate.set()
    for _ in range(200):
        body = client.get(f"/api/export/state?job_id={started['job_id']}").get_json()
        if body["status"] in ("done", "partial", "failed", "cancelled"):
            break
        time.sleep(0.02)
    assert body["status"] == "done"


def test_cancel_is_refused_once_publication_has_begun(env, monkeypatch):
    """落盘一开始就没得取消了——**回 `False`，别许一个做不到的承诺**。

    第一个 `os.replace` 之后，上一版文件的内容已经不在了；回
    `cancelling: true` 然后让作业照常报 `done`，界面上就是"我点了取消，
    它还是导出了"。
    """
    client, _ = env
    _, started = _post(client, _canvas(), path="/api/export/start")
    job_id = started["job_id"]
    for _ in range(200):
        body = client.get(f"/api/export/state?job_id={job_id}").get_json()
        if body["status"] in ("done", "partial", "failed", "cancelled"):
            break
        time.sleep(0.02)
    assert body["status"] == "done"
    assert client.post("/api/export/cancel", json={"job_id": job_id}).get_json() == {
        "cancelling": False
    }

    # 提交点本身：置上之后即使作业还没结束，cancel 也要如实回 False
    job = exportjob.get(job_id)
    assert job is not None and job._committed is True
    job.status = exportjob.STATUS_RUNNING  # 假装它还在跑
    assert exportjob.cancel(job_id) is False, "提交点之后不许再接受取消"


def test_cancel_and_the_commit_point_are_serialized(env):
    """「还没提交」与「置上取消位」必须**原子**地一起发生。

    只用一个布尔挡不住：`cancel()` 读到 `False` 之后、`_cancel.set()` 之前，
    执行线程完全可能跑完最后一次检查并把 `_committed` 置上——于是 `cancel()`
    回了 `True` 而作业照常报 `done`，正是提交点想消掉的那个行为。

    判据钉在**锁本身**上：把锁攥在手里，`cancel()` 必须阻塞而不是抢先读到
    一个陈旧的 `False`。
    """
    client, _ = env
    _, started = _post(client, _canvas(), path="/api/export/start")
    for _ in range(200):
        body = client.get(f"/api/export/state?job_id={started['job_id']}").get_json()
        if body["status"] in ("done", "partial", "failed", "cancelled"):
            break
        time.sleep(0.02)
    job = exportjob.get(started["job_id"])
    assert job is not None and job._committed is True

    # 提交点与取消共用一把锁：持锁时 cancel() 进不来
    job.status = exportjob.STATUS_RUNNING
    job._committed = False
    verdicts: list = []
    with job._commit_lock:
        t = threading.Thread(target=lambda: verdicts.append(exportjob.cancel(job.id)))
        t.start()
        t.join(0.3)
        assert t.is_alive(), "cancel() 没有等锁 = 它读到的可能是陈旧的 _committed"
        job._committed = True
    t.join(2)
    assert verdicts == [False], "锁放开之后 cancel() 必须看到已提交并如实回 False"


def test_a_requested_report_with_no_payload_fails_loudly(env):
    """要了报告却没有可写的内容：**报失败，作业进 `partial`**。

    静默跳过是最坏的处置——作业报 `done`、产出清单里没有报告、进度还差一格
    永远补不上，而请求方以为拿到了留档（PR #214 第四轮评审）。
    """
    client, _ = env
    _, body = _post(client, _canvas(include_style_check_report=True))  # 没带 style_check_report
    assert body["status"] == "partial"
    assert _out(body, "pdf")["status"] == "done"
    report = next(o for o in body["outputs"] if o["format"] == "report")
    assert report["status"] == "failed"
    assert report["error"]["code"] == "report_missing_payload"
    # 进度不许停在"还差一格"
    assert body["progress"]["step"] == body["progress"]["total"]
    assert sorted(p.name for p in _dir(body).iterdir()) == ["Fig 1.pdf"]


def test_a_raster_panel_with_overrides_is_re_rendered_not_copied(env, tmp_path, monkeypatch):
    """带 override 的位图面板会被引擎**重画**，拿到的是一份 PDF。

    这条用例钉的是后端行为本身（`_resolve_panel_source` 回的是 worker 出的
    临时 PDF，于是走矢量那条路、ppi 说了算）。界面那一半在
    `exportRequest.test.ts`：**它不许对着一张即将被重画的图报源像素网格**
    （PR #214 第四轮评审）。
    """
    client, figs = env
    rendered = tmp_path / "worker-out.pdf"
    doc = pymupdf.open()
    doc.new_page(width=SRC_W_PT, height=SRC_H_PT)
    doc.save(rendered)
    doc.close()

    def fake_resolve(o, dpi, sink=None, out_dir=None):
        assert o["id"] == "r1.png" and o["overrides"], "这条用例要走的是「带 override」那一支"
        assert out_dir is not None, "导出路径必须把作业私有的临时目录传下来"
        return rendered

    monkeypatch.setattr(m, "_resolve_panel_source", fake_resolve)
    _, body = _post(
        client,
        _original(
            formats=["png"],
            ppi=150,
            original={
                "figure_id": "r1.png",
                "source_kind": "raster",
                "px_w": 120,
                "px_h": 80,
                "overrides": [{"gid": "axes_0", "prop": "fontsize", "value": 9}],
            },
        ),
    )
    png = _out(body, "png")
    assert png["status"] == "done"
    # 重画出来的是矢量，按 ppi 栅格化——**不是**源文件那 120×80
    assert png["dimensions"]["px"] != [120, 80]
    assert abs(png["dimensions"]["px"][0] - SRC_W_PT / 72.0 * 150) <= 1


def test_two_concurrent_override_renders_do_not_share_one_intermediate_file(env, monkeypatch):
    """两次导出打同一张图时，中间那份重渲染 PDF **不许是同一个路径**。

    worker 调用本身是串行的，但锁在调用返回时就放开了，而调用方还要接着去
    打开/栅格化那个文件——共享路径下，后一次会在前一次读它的过程中把它覆盖，
    第一次于是**静默拿到了别人那套 override 的图**（PR #214 第五轮评审）。

    判据是**路径互不相同**，不是"有没有报错"：共享路径下这件事不报错，它只是
    悄悄给错图。
    """
    client, figs = env
    seen: list[Path] = []
    real_worker_export = {}

    class FakeWorker:
        export_dir = figs

        def export(self, stem, patches, path, fmt="pdf", dpi=600):
            seen.append(Path(path))
            doc = pymupdf.open()
            doc.new_page(width=SRC_W_PT, height=SRC_H_PT)
            doc.save(path)
            doc.close()
            return {"warnings": []}

    monkeypatch.setattr(m, "_safe_worker", lambda *a, **kw: FakeWorker())
    monkeypatch.setattr(
        m,
        "current_registry",
        lambda: type("R", (), {"for_stem": lambda self, s: {"script": "s.py", "entry": "main"}})(),
    )
    del real_worker_export

    spec = _original(
        formats=["pdf"],
        original={
            "figure_id": "r1.png",
            "source_kind": "raster",
            "overrides": [{"gid": "axes_0", "prop": "fontsize", "value": 9}],
        },
    )
    _post(client, {**spec, "filename": "A"})
    _post(
        client,
        {
            **spec,
            "filename": "B",
            "original": {
                **spec["original"],
                "overrides": [{"gid": "axes_0", "prop": "fontsize", "value": 11}],
            },
        },
    )

    assert len(seen) == 2, f"两次导出都该触发一次重渲染：{seen}"
    assert seen[0] != seen[1], "两次导出共用了同一个中间路径 —— 后一次会覆盖前一次正在读的文件"
    # 中间产物落在作业自己的临时目录里，而且**作业结束后一个都不留**
    for p in seen:
        assert exportjob.TMP_PREFIX in str(p), f"中间 PDF 不在作业私有的临时目录里：{p}"
        assert not p.exists(), f"作业结束后中间产物还在：{p}"


# ================= PR #214 第六轮评审（四条 P2） =============================
def test_export_urls_are_url_encoded(env):
    """`check_filename()` 放行的名字里有一批对 URL 有特殊含义。

    `Fig#1` 拼出来的 `/exports/Fig#1.pdf` 会被当成"路径 `/exports/Fig` +
    锚点 `1.pdf`"——链接指向的不是刚写出去的那个文件。
    """
    client, _ = env
    assert exportreq.check_filename("Fig#1") is None, "这个名字本来就是合法的"
    _, body = _post(
        client,
        _canvas(
            filename="Fig#1", include_style_check_report=True, style_check_report={"checks": []}
        ),
    )
    assert body["status"] == "done"
    pdf = _out(body, "pdf")
    assert pdf["name"] == "Fig#1.pdf"
    assert "#" not in pdf["url"], f"URL 里还留着裸的 # ：{pdf['url']}"
    assert pdf["url"] == "/exports/Fig%231.pdf"
    # 报告那条链接同样要转义（两处各拼一遍的话总有一处漏）
    report = next(o for o in body["outputs"] if o["format"] == "report")
    assert "#" not in report["url"]
    # 链接真的取得回那个文件
    assert client.get(pdf["url"]).status_code == 200


def test_two_concurrent_renames_each_get_their_own_number(env, monkeypatch):
    """两个 `rename` 作业同时要同一个空名字：**各拿各的编号**，不许一个被报冲突。

    取名与预留分成两次持锁的话，两边取名时 `Fig 1.pdf` 都还空着——先占住的
    那个赢，后一个被报成 `conflict`，而它请求的明明是"另存一份"。
    """
    client, _ = env
    gate = threading.Event()
    real = m._export_produce

    def slow(job, tmp_dir):
        gate.wait(5)
        return real(job, tmp_dir)

    monkeypatch.setattr(m, "_export_produce", slow)
    _, a = _post(client, _canvas(overwrite="rename"), path="/api/export/start")
    for _ in range(200):
        if exportjob.get(a["job_id"]) and exportjob.get(a["job_id"])._tmp_dir:
            break
        time.sleep(0.02)

    monkeypatch.setattr(m, "_export_produce", real)
    _, b = _post(client, _canvas(overwrite="rename"))
    assert b["status"] != "conflict", "rename 请求被报成了冲突"
    assert _out(b, "pdf")["name"] == "Fig 1 (2).pdf"

    gate.set()
    for _ in range(200):
        done = client.get(f"/api/export/state?job_id={a['job_id']}").get_json()
        if done["status"] in ("done", "partial", "failed", "cancelled"):
            break
        time.sleep(0.02)
    assert done["status"] == "done"
    assert _out(done, "pdf")["name"] == "Fig 1.pdf"


def test_naming_happens_inside_the_reservation_lock(env, monkeypatch):
    """取名必须**在预留那把锁里**发生。

    这条判据量的是同步性质本身，不是某一次时序：两个 `rename` 作业各自取名
    时那个空名字都还在，谁也发现不了冲突——先占住的赢，后一个被报成
    `conflict`，而它请求的明明是"另存一份"。这种交错要靠 sleep 去撞的话，
    红不红取决于机器。

    `_LOCK.locked()` 在单线程用例里只可能是我们自己持的——它回答的正是
    「此刻取名的这个调用，是不是被那把锁保护着」。
    """
    client, _ = env
    real = exportjob._plan_names
    inside: list[bool] = []

    def spy(job):
        inside.append(exportjob._LOCK.locked())
        return real(job)

    monkeypatch.setattr(exportjob, "_plan_names", spy)
    _, body = _post(client, _canvas(overwrite="rename"))
    assert body["status"] == "done"
    assert inside == [True], f"取名没有在预留那把锁里发生：{inside}"


def test_a_live_temp_dir_is_never_swept_by_another_job(env, monkeypatch):
    """临时目录**先登记再创建**：别的作业的清扫看得见它。

    反过来的话，`mkdir()` 成功与赋值之间那一瞬里，另一次导出的清扫会在磁盘上
    看见这个目录、在存活集合里找不到它，于是把它当垃圾删掉——而本作业正要
    往里写。
    """
    client, _ = env
    seen: list[bool] = []
    real_mkdir = Path.mkdir

    def mkdir_then_sweep(self, *a, **kw):
        result = real_mkdir(self, *a, **kw)
        if exportjob.TMP_PREFIX in self.name:
            # 就在这一瞬间，另一次导出开始清扫
            exportjob.sweep_stale_tmp_dirs(self.parent)
            seen.append(self.is_dir())
        return result

    monkeypatch.setattr(Path, "mkdir", mkdir_then_sweep)
    _, body = _post(client, _canvas())
    assert seen and seen[0], "临时目录在创建的那一瞬被另一次清扫删掉了"
    assert body["status"] == "done"
