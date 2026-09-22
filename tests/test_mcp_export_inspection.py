"""MCP `tavotto_export` 的有限产物验证（统一实施包 U08 第三切片，ADR 0068；RC-086 / RC-087 / RC-088）。

判据与 HTTP 导出**同一份**接线（`engine/artifactinspect.inspect_produced`）：回执 `files[].manifest` 原样带出，
不合格的那一项以 `artifact_rejected` 进 `partial`、不发布；`unknown` 不是 `verified`——给模型看的那段文字
把「未核验」与「已核验」分开说。文件事实由**独立读取器**量（stdlib `pdfread` / hashlib），不只信回执。

夹具与 `test_mcp_server.py` 同一套（假 worker 写的是合法的最小 PDF / PNG，`tests/support/artifactbytes.py`）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

import pytest  # noqa: E402

from support import artifactbytes, pdfread  # noqa: E402
from tavotto_mcp import bridge, server  # noqa: E402
from test_mcp_server import FakeWorker  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch):
    # 与 test_mcp_server 同一套清理：会话表 / 根授权 / 不真的去探本机的 Tavotto
    bridge.reset_root_authority()
    bridge.sessions().clear()
    bridge._REFRESH_CTX.clear()
    monkeypatch.setattr(bridge.engine_handoff, "http_json_status", lambda *a, **k: (None, None))
    yield
    bridge.sessions().clear()
    bridge._REFRESH_CTX.clear()
    bridge.reset_root_authority()


@pytest.fixture
def project(tmp_path, monkeypatch):
    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "fig1.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (figures / "Fig1.pdf").write_bytes(b"%PDF-1.4\n")
    (figures / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    return figures


@pytest.fixture
def fake_pool(monkeypatch):
    worker = FakeWorker()
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: worker)
    return worker


def _call(name: str, args: dict) -> dict:
    return server.call_tool(name, args)


def _body(res: dict) -> dict:
    return res["structuredContent"]


def _text(res: dict) -> str:
    return "\n".join(c["text"] for c in res["content"] if c.get("type") == "text")


def _open(project: Path) -> str:
    res = _call("tavotto_open_figure", {"project_path": str(project)})
    assert not res.get("isError"), _body(res)
    return _body(res)["session_id"]


def test_the_receipt_carries_a_manifest_that_an_independent_reader_confirms(
    project, fake_pool, tmp_path
):
    """RC-086：真的经工具入口导出，`files[].manifest` 说的尺寸 / 哈希与磁盘上那个文件逐项对得上。"""
    sid = _open(project)
    out_dir = tmp_path / "out"
    res = _call(
        "tavotto_export",
        {"session_id": sid, "formats": ["pdf", "png"], "dpi": 300, "out_dir": str(out_dir)},
    )
    assert not res.get("isError"), _body(res)
    done = _body(res)
    assert done["status"] == "done" and [f["format"] for f in done["files"]] == ["pdf", "png"]
    for f in done["files"]:
        m = f["manifest"]
        assert m["verdict"] == "accepted" and m["policy"] == "standard"
        assert m["checks"]["integrity"] == "verified" and m["checks"]["size"] == "verified"
        published = Path(f["path"])
        assert published.exists()
        # 独立读取器：哈希就是发布后的字节；PDF 的页盒就是会话 manifest 的 80 × 60 mm
        assert m["sha256"] == hashlib.sha256(published.read_bytes()).hexdigest()
        if f["format"] == "pdf":
            box = pdfread.media_box(pdfread.page(pdfread.objects(published.read_bytes()))[0])
            assert abs(box[2] - 80 / 25.4 * 72) < 0.01 and abs(box[3] - 60 / 25.4 * 72) < 0.01
            assert m["size_pt"] == [round(80 / 25.4 * 72, 3), round(60 / 25.4 * 72, 3)]
        else:
            w, h, _, _ = pdfread.decode_png_any(published.read_bytes())
            assert [w, h] == m["px"] == [int(round(80 / 25.4 * 300)), int(round(60 / 25.4 * 300))]
            assert m["checks"]["dpi_tag"] == "verified"
    text = _text(res)
    pdf_path, png_path = (f["path"] for f in done["files"])
    # PNG 四项全量得到 → 已核验；PDF 的普查项（carrier / 字体 / 文字层…）standard 下只记不判、
    # 没有 pikepdf 的机器上更是全 unknown → 文字里说「未核验项」，不说「已核验」
    assert "产物核验：" in text and "未通过" not in text
    assert f"{png_path}：已核验 integrity, size, dpi_tag，未核验 raster_density" in text
    # PDF：有 pikepdf 的机器普查到 carrier，没有的机器全 unknown——两种读取器下必需两项都在
    # 「已核验」里，且都有未核验项（clipping 没有对象可判）
    pdf_line = next(s for s in text.split("；") if s.startswith(pdf_path) or f"：{pdf_path}" in s)
    assert "已核验 integrity, size" in pdf_line and "未核验 " in pdf_line
    # 旧契约的键一个不少（RC-090）
    for key in ("path", "bytes", "vector", "dpi", "status", "format"):
        assert key in done["files"][0]


def test_a_broken_worker_output_is_rejected_before_publish_and_the_good_one_ships(
    project, fake_pool, tmp_path
):
    """RC-063 / RC-073 经 MCP 入口：worker 写出的 PDF 是坏的 → `artifact_rejected`、不发布；PNG 照常发布。"""
    real_export = fake_pool.export

    def broken_pdf(stem, patches, path, fmt="pdf", dpi=600):
        out = real_export(stem, patches, path, fmt, dpi)
        if fmt == "pdf":
            Path(path).write_bytes(b"%PDF-1.4 garbage, not a document")
        return out

    fake_pool.export = broken_pdf
    sid = _open(project)
    out_dir = tmp_path / "out"
    res = _call(
        "tavotto_export",
        {"session_id": sid, "formats": ["pdf", "png"], "dpi": 300, "out_dir": str(out_dir)},
    )
    assert not res.get("isError"), _body(res)
    done = _body(res)
    assert done["status"] == "partial" and done["ok"] is False
    pdf = next(f for f in done["files"] if f["format"] == "pdf")
    png = next(f for f in done["files"] if f["format"] == "png")
    assert pdf["status"] == "failed" and pdf["error"]["code"] == "artifact_rejected"
    assert (
        pdf["error"]["params"]["failed"] == "integrity" and pdf["manifest"]["verdict"] == "rejected"
    )
    assert pdf["path"] is None and not list(out_dir.glob("*.pdf")), "不合格的不许出现在导出目录"
    assert png["status"] == "done" and Path(png["path"]).exists()
    text = _text(res)
    assert "未出成：pdf（artifact_rejected）" in text and "部分完成" in text


def test_unknown_is_reported_as_unknown_not_verified(project, fake_pool, tmp_path):
    """RC-088：SVG 没有读取器 → `integrity: unknown`；standard 照常交付，但文字与回执都不说「已核验」。"""
    sid = _open(project)
    out_dir = tmp_path / "out"
    res = _call("tavotto_export", {"session_id": sid, "formats": ["svg"], "out_dir": str(out_dir)})
    assert not res.get("isError"), _body(res)
    done = _body(res)
    assert done["status"] == "done"
    m = done["files"][0]["manifest"]
    assert m["verdict"] == "accepted" and m["checks"] == {"integrity": "unknown"}
    assert "verified" not in m["checks"].values()
    text = _text(res)
    assert "：未核验 integrity" in text and "已核验" not in text


def test_mcp_uses_the_same_inspection_wiring_as_http(project, fake_pool, tmp_path, monkeypatch):
    """RC-087：桥不带第二份检查器——它调的是 `engine/artifactinspect.inspect_produced`，
    契约层 probe 按需 import（不进桥的常驻 import 闭包）。"""
    seen: list[dict] = []
    real = bridge.engine_artifactinspect.inspect_produced

    def spy(job, produced, *, backend, probe):
        seen.append({"backend": backend, "probe": probe, "n": len(produced)})
        return real(job, produced, backend=backend, probe=probe)

    monkeypatch.setattr(bridge.engine_artifactinspect, "inspect_produced", spy)
    sid = _open(project)
    done = _body(
        _call("tavotto_export", {"session_id": sid, "formats": ["pdf"], "out_dir": str(tmp_path)})
    )
    assert done["status"] == "done"
    assert seen == [{"backend": "worker", "probe": bridge._probe_asset, "n": 1}]
    # probe 真的是契约层那一个（按选中的后端打开），不是桥自己写的第二份
    from tavotto import pdfbackend

    facts = bridge._probe_asset(Path(done["files"][0]["path"]), "pdf")
    assert facts == pdfbackend.probe_asset(Path(done["files"][0]["path"]), "pdf")


def test_the_hand_built_fixtures_are_what_the_inspector_would_reject_if_damaged(tmp_path):
    """夹具自证：合法的最小 PDF / PNG 过完整性，坏一个字节就红——否则上面三条量的是空气。"""
    from tavotto.rendercore import inspector

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(artifactbytes.blank_pdf(100, 50))
    from tavotto import pdfbackend

    good = inspector.inspect(
        pdf, "pdf", plan={"page_pt": [100, 50], "backend": "x"}, probe=pdfbackend.probe_asset
    )
    assert good["checks"]["integrity"] == "verified" and good["checks"]["size"] == "verified"
    # 截到页面对象之前：probe 打不开 → integrity failed
    pdf.write_bytes(artifactbytes.blank_pdf(100, 50)[:60])
    bad = inspector.inspect(
        pdf, "pdf", plan={"page_pt": [100, 50], "backend": "x"}, probe=pdfbackend.probe_asset
    )
    assert bad["checks"]["integrity"] == "failed"
    # 截在页面对象中间：修复型读取器会**编出一页 Letter**——完整性它看不出来，尺寸量得出来
    # （这正是「尺寸必须量文件、不能回传请求值」那条判据的用武之地）
    pdf.write_bytes(artifactbytes.blank_pdf(100, 50)[:120])
    forged = inspector.inspect(
        pdf, "pdf", plan={"page_pt": [100, 50], "backend": "x"}, probe=pdfbackend.probe_asset
    )
    # 有 pikepdf 的机器在完整性就拦下（qpdf 不修复）；只有 probe 的机器靠尺寸拦——两条必需项之一必红
    assert "failed" in (forged["checks"]["integrity"], forged["checks"]["size"])
    png = tmp_path / "a.png"
    png.write_bytes(artifactbytes.solid_png(12, 8, dpi=300))
    assert inspector.observe_png(png)["px"] == [12, 8]
    png.write_bytes(artifactbytes.solid_png(12, 8, dpi=300)[:-4] + b"\0\0\0\0")
    assert inspector.observe_png(png)["integrity"] == "failed"


# ---------------------------------------------------------------------------
# U09（ADR 0070）：MCP 导出的 manifest 与 HTTP 候选路同一份四身份 / 来源 / 回执（真 worker）
# ---------------------------------------------------------------------------
def _worker_python():
    try:
        return bridge.engine_pool.find_worker_python()
    except bridge.engine_pool.WorkerError:
        return None


REAL_SCRIPT = """\
import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    with open("data.csv", encoding="utf-8", newline="") as fh:
        xs = [float(r["x"]) for r in csv.DictReader(fh)]
    fig, ax = plt.subplots(figsize=(80 / 25.4, 50 / 25.4))
    ax.plot(xs, [3 * x + 1 for x in xs])
    ax.set_title("mcp identity")
    fig.savefig("Fig1.pdf")


if __name__ == "__main__":
    main()
"""


@pytest.mark.skipif(_worker_python() is None, reason="没有带科学栈的解释器，跳过真链路用例")
def test_a_real_mcp_export_carries_identities_receipt_facts_and_the_data_binding(
    tmp_path, monkeypatch
):
    """真 worker 的 MCP 导出：manifest.identity 四个字段都在（semantic / render 由 `execution_provenance`
    算，artifact = 发布字节，run = 作业 id），provenance.receipts 是**这条会话**的回执公开事实（解释器版本 /
    关键包 / 数据绑定核对 matched=True / 观察 partial），sources 是 origin=execution 的产物；公开投影里没有路径。
    """
    import subprocess

    figures = tmp_path / "figures"
    figures.mkdir()
    (figures / "fig1.py").write_text(REAL_SCRIPT, encoding="utf-8")
    (figures / "data.csv").write_text("x\n2\n4\n8\n", encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig1.py": {"entry": "main", "cost": "light", "stems": ["Fig1"]}}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_worker_python(), str(figures / "fig1.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(figures),
    )
    assert proc.returncode == 0, proc.stderr
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    try:
        sid = bridge.open_figure(str(figures), stem="Fig1")["session_id"]
        out_dir = tmp_path / "out"
        res = bridge.export(
            sid, formats=["pdf", "png"], dpi=150, out_dir=str(out_dir), explicit_confirm=True
        )
    finally:
        bridge.shutdown_all()
    assert res["status"] == "done", res
    by_fmt = {f["format"]: f for f in res["files"]}
    pdf, png = by_fmt["pdf"]["manifest"], by_fmt["png"]["manifest"]
    for mani, path in ((pdf, by_fmt["pdf"]["path"]), (png, by_fmt["png"]["path"])):
        ident = mani["identity"]
        assert ident["semantic"] and ident["render"] and ident["run"]
        assert ident["artifact"] == hashlib.sha256(Path(path).read_bytes()).hexdigest()
        prov = mani["provenance"]
        assert prov["sources"][0]["origin"] == "execution"
        assert prov["sources"][0]["receipt_identity"] == prov["execution_receipts"][0]
        rcpt = prov["receipts"][0]
        assert rcpt["completeness"] == "complete" and rcpt["runtime_rejected"] is None
        assert rcpt["python_version"] and "matplotlib" in rcpt["packages"]
        assert rcpt["observation"] == "partial"
        assert rcpt["binding"]["matched"] is True and rcpt["binding"]["changed"] == 0
        assert prov["nodes"][0]["kind"] == "figure"
    # 两种格式同一次执行、同一份意图：semantic / run 相同，render 不同（PNG 多了 ppi）
    assert pdf["identity"]["semantic"] == png["identity"]["semantic"]
    assert pdf["identity"]["run"] == png["identity"]["run"]
    assert pdf["identity"]["render"] != png["identity"]["render"]
    assert "来源 / 回执段装配失败" not in " ".join(res.get("warnings") or [])
    # 回执 / 来源段本身就不带路径（进 manifest 的是公开事实）：临时目录 / 项目目录 / 数据目录一个都没有
    prov_text = json.dumps(pdf["provenance"], ensure_ascii=False)
    assert str(tmp_path) not in prov_text and str(figures) not in prov_text
