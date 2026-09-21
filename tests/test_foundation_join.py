"""统一实施包 U09：从正确执行到正确产物的一条证据链——核心联合实例 FO32（两个独立出口）与 FO30 的合同。

**`U09.existing_env_join`（`test_fo32_existing_env_*`）**：用户本来就有一个项目 `.venv`（基础解释器 **≠**
应用的、已有 matplotlib + h5py；`project_venv/make_venv.py` 现建，harness 不装包）；脚本经真实 h5py 读 HDF5、
脚本目录里另有一份同名干扰、项目路径含中文与空格。经**真实公共入口**（`python -m tavotto` + 会话认证，
候选后端 `TAVOTTO_RENDER_BACKEND=rendercore`）首开 → 歧义时问一次（不猜）→ 用户选项目根 → 真实科学执行 →
图内值 = 独立真值 [7, 13, 25]（不是干扰的 [601, 1201, 2401]）→ 一次可见 patch（数据不变）→ 新应用 worker 重放 →
**新 RenderCore** 导出 PDF / PNG / TIFF → 独立读取器核最终文件 → manifest 里四身份 / 回执 / 来源逐项对得上
（实际解释器版本、源 revision、final hash）。科学环境**没有** PDF 候选包（FO-063）——渲染是应用自己的事。

**`U09.managed_env_join`（`TestManagedEnvJoin`）**：产品自己准备环境——发现链末端置空（U05 的进程内形态）、
私有 Python 按锁供应（真 pbs 归档：`TAVOTTO_PRIVATE_PYTHON_REAL=1` + 缓存 / wheelhouse，与
`private-python-targets.yml` 三条腿同一套开关）、U04 代事务把 h5py 装进那一代 → 同一条链到 RenderCore 终点。
本机 macOS 真跑；其它目标由 U05 的目标 workflow 观察。

每条用例写一条结果记录（`foundation_harness` 的 schema，`backend=rendercore`——与 U03 旧后端终点的记录分开，D06）。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from support import foundation_app as fa, foundation_harness as fh, pdfread, tiffcheck

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "foundation"
PROJECT_PYTHON_ENV = "TAVOTTO_FOUNDATION_PROJECT_PYTHON"
PROJECT_PYTHON = os.environ.get(PROJECT_PYTHON_ENV) or ""
HAS_CANDIDATE = all(
    __import__("importlib.util").util.find_spec(mod) is not None
    for mod in ("pypdfium2", "pikepdf", "uharfbuzz", "PIL")
)

#: 项目 venv 的基础解释器由环境变量点名（与应用不同的 minor、已有 matplotlib + h5py）：harness 不替用户挑
#: 环境、也不装包，只把「用户本来就有的」摆出来。没给就 skip（skip 在 CI 校验步里是红——observing 的腿才带它）。
needs_project_python = pytest.mark.skipif(
    not PROJECT_PYTHON,
    reason=f"{PROJECT_PYTHON_ENV} 没指向一个「另一个 minor 且装了 matplotlib + h5py」的解释器（not_run）",
)
needs_candidate = pytest.mark.skipif(not HAS_CANDIDATE, reason="候选包未装（not_run，不是绿）")


# ---------------------------------------------------------------- 小工具


def _truth() -> dict:
    return json.loads((FIXTURES / "join_h5" / "truth.json").read_text(encoding="utf-8"))


def _case(case_id: str) -> tuple[dict, dict]:
    ledger = fh.load_ledger()
    case = next(c for c in ledger["cases"] if c["case_id"] == case_id)
    binding = fh.binding_from_environment(
        ledger=ledger, entry=case["entry"], fixture=case["fixture"]
    )
    return case, binding


def _record(
    case_id: str, binding: dict, outcome: str, observed: dict, evidence: list[str], out_dir
):
    record = fh.ResultRecord(
        case_id=case_id,
        binding=binding,
        product_outcome=outcome,
        test_verdict="pass",
        observed=observed,
        evidence=tuple(evidence),
    )
    path = fh.write_result(record, fh.results_dir() or out_dir)
    assert path.is_file()


def _axes_ylim(render: dict) -> list[float]:
    axes = next(e for e in render["manifest"]["elements"] if e["role"] == "axes")
    return next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")


def _title(render: dict) -> dict:
    return next(e for e in render["manifest"]["elements"] if e["role"] == "title")


def _title_text(render: dict) -> str:
    return next(f["value"] for f in _title(render)["editable"] if f["prop"] == "text")


def _first_text_patch(render: dict, value: str) -> dict:
    """任意一个带 `text` 属性的元素上的一次可见编辑（这张图没有标题就改坐标轴标签）。"""
    for e in render["manifest"]["elements"]:
        if any(f["prop"] == "text" for f in e.get("editable", [])):
            return {"gid": e["gid"], "prop": "text", "value": value}
    raise AssertionError("没有可编辑文字的元素")


def _expected_ylim(ys: list[float]) -> list[float]:
    margin = 0.05 * (max(ys) - min(ys))
    return [min(ys) - margin, max(ys) + margin]


def _panel(app: fa.RunningApp, file_name: str) -> dict:
    _, panels = app.call("/api/panels", timeout=30)
    wanted = file_name.replace("\\", "/")
    return next(p for p in panels["panels"] if p["id"].replace("\\", "/") == wanted)


def _probe(python: str, code: str) -> str:
    out = subprocess.run(
        [python, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return out.stdout.strip().splitlines()[-1]


def _importable(python: str, module: str) -> bool:
    proc = subprocess.run(
        [python, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc.returncode == 0


def _joined_project(tmp_path: Path) -> Path:
    """把夹具复制成用户的项目：路径含中文与空格。"""
    proj = tmp_path / "实验 数据" / "项目 A"
    shutil.copytree(FIXTURES / "join_h5", proj)
    (proj / "make_h5.py").unlink()  # 生成器不是用户项目的一部分
    return proj


def _build_project_venv(proj: Path, tmp_path: Path) -> dict:
    """`project_venv/make_venv.py`：基础解释器 ≠ 应用（脚本自己核 minor 不同），只 `python -m venv` +
    接宿主 site-packages（一个字节不下载）。回执里的 matplotlib 是建完当场量的。"""
    proc = subprocess.run(
        [
            sys.executable,
            str(FIXTURES / "project_venv" / "make_venv.py"),
            "--python",
            PROJECT_PYTHON,
            "--app-python",
            sys.executable,
            "--link-host-site",
            "--dest",
            str(proj),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    receipt = json.loads((proj / "venv_receipt.json").read_text(encoding="utf-8"))
    if not receipt["matplotlib_importable"]:
        pytest.skip(f"{PROJECT_PYTHON_ENV} 指向的解释器上没有 matplotlib（not_run）")
    (proj / "venv_receipt.json").unlink()  # 夹具的构造回执不是用户项目的一部分
    return receipt


def _native_reference(proj: Path, venv_python: str, tmp: Path, *, cwd: Path) -> str:
    """用户在终端里站在 `cwd` 跑过一次脚本（只留装载器与系统必需的几个变量）；回 `--dump` 打出的 y。"""
    env = {"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(tmp / "mpl"), "MPLBACKEND": "Agg"}
    for name in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP", "HDF5_USE_FILE_LOCKING"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    proc = subprocess.run(
        [venv_python, str(proj / "scripts" / "figure_h5.py"), "--dump"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout.strip().splitlines()[-1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _pdf_text(path: Path) -> str:
    """独立读取器（PDFium）抽 PDF 文字层——不是产品的检查器。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[0]
        tp = page.get_textpage()
        text = tp.get_text_range()
        tp.close()
        page.close()
    finally:
        doc.close()
    return text


CAPTION = "FO32 · U09 join"


def _canvas_export(panel_id: str, patch: dict, *, formats: list[str], ppi: int) -> dict:
    return {
        "scope": "canvas",
        "filename": "FO32 联调",
        "formats": formats,
        "ppi": ppi,
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 100,
            "page_h_mm": 70,
            "objects": [
                {
                    "type": "panel",
                    "id": panel_id,
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 80,
                    "h_mm": 60,
                    "overrides": [patch],
                },
                # 画布自己的文字（RenderCore 排的、检查器按计划里的期望行核文字层）；面板内部的文字层
                # 归外来页，检查器不编造（unknown / not_applicable），由独立读取器另核
                {
                    "type": "text",
                    "id": "caption",
                    "text": CAPTION,
                    "x_mm": 5,
                    "y_mm": 66,
                    "w_mm": 80,
                    "h_mm": 4,
                    "size_pt": 8,
                },
            ],
        },
    }


def _check_outputs(body: dict, *, expected_title: str, ppi: int) -> dict:
    """独立核最终文件 + manifest 逐项对：回 {fmt: {sha256, manifest}}。"""
    out_dir = Path(body["export_dir"])
    by_fmt = {}
    for o in body["outputs"]:
        assert o["status"] == "done", o
        path = out_dir / o["name"]
        mani = o["manifest"]
        assert mani["verdict"] == "accepted", mani
        assert mani["checks"]["integrity"] == "verified" and mani["checks"]["size"] == "verified"
        sha = _sha256(path)
        assert mani["sha256"] == sha == mani["identity"]["artifact"]  # RC-077：发布的字节
        assert sha.encode() not in path.read_bytes()  # 不自引用
        assert mani["identity"]["run"] == body["job_id"]
        by_fmt[o["format"]] = {"sha256": sha, "manifest": mani, "path": path}
    pdf = by_fmt["pdf"]
    text = _pdf_text(pdf["path"])
    assert expected_title in text and CAPTION in text, text[
        :400
    ]  # 面板里的标题 + 画布文字都在文字层
    assert pdf["manifest"]["checks"]["text_layer"] == "verified"  # 计划里的期望行（画布文字）核过
    assert pdf["manifest"]["checks"]["fonts_embedded"] == "verified"
    w_px = round(100 / 25.4 * ppi)
    h_px = round(70 / 25.4 * ppi)
    w, h, _bpp, _px = pdfread.decode_png_any(by_fmt["png"]["path"].read_bytes())
    assert (w, h) == (w_px, h_px)
    if "tiff" in by_fmt:
        tags = tiffcheck.read_tags(by_fmt["tiff"]["path"])
        assert (tags["width"], tags["height"]) == (w_px, h_px), tags
    sem = {f["manifest"]["identity"]["semantic"] for f in by_fmt.values()}
    assert len(sem) == 1 and None not in sem  # 同一份计划：语义身份一致
    return by_fmt


# ================================================================ FO32 · existing_env_join


@needs_candidate
@needs_project_python
def test_fo32_existing_env_open_edit_replay_export_through_the_new_rendercore(tmp_path):
    """FO32（出口一）：用户已有的 `.venv`（≠ 应用 Python、真 h5py）→ 真实入口首开：同名干扰 → 问一次 →
    选项目根 → 值 = 真值 → 可见 patch → 新 worker 重放 → RenderCore 导出 PDF / PNG / TIFF → 独立核文件 →
    回执里的解释器版本 / prefix、源 revision、final hash 与事实对得上；科学环境里没有 PDF 候选包。"""
    case, binding = _case("FO32")
    truth = _truth()
    proj = _joined_project(tmp_path)
    venv = _build_project_venv(proj, tmp_path)
    venv_py = venv["venv_python"]
    assert venv["venv_version"][:2] != list(sys.version_info[:2]), "项目 Python 必须与应用不同"
    assert _probe(venv_py, "import h5py; print(h5py.__version__)")
    # FO-063：科学环境不需要新增 PDF 依赖——候选包一个都不在项目 venv 里
    for mod in ("pikepdf", "pypdfium2", "uharfbuzz"):
        assert not _importable(venv_py, mod), f"项目 venv 里不该有 {mod}"
    # 用户在终端里站在项目根跑过一次：磁盘上有原件，值是真值；站在脚本目录跑读的是干扰那份
    dumped = _native_reference(proj, venv_py, tmp_path, cwd=proj)
    assert dumped == "7,13,25"
    assert _native_reference(proj, venv_py, tmp_path, cwd=proj / "scripts") == "601,1201,2401"
    (proj / "scripts" / "figure_h5.pdf").unlink(missing_ok=True)  # 脚本目录那次跑出来的原件不算素材
    assert (proj / "figure_h5.pdf").is_file()
    script_sha1 = _sha1(proj / "scripts" / "figure_h5.py")
    evidence: list[str] = []
    env = {"TAVOTTO_RENDER_BACKEND": "rendercore"}
    with fa.running_app(proj, tmp_path / "work", env_overrides=env) as app:
        panel = _panel(app, "figure_h5.pdf")
        assert panel["script"].replace("\\", "/") == "scripts/figure_h5.py"
        # ① 歧义：两处同名 measure.h5 内容不同 → 问一次，不推荐、不猜；选择前不发布图
        state = app.prepare(panel["id"])
        need = state["result"]["required_input"]
        assert state["result"]["status"] == "needs_input", state["result"]
        assert need["code"] == "workdir_confirmation_required"
        assert need["reason"] == "ambiguous_data" and need["recommended"] is None
        assert need["conflicts"] == ["data/measure.h5"]
        with pytest.raises(fa.HttpError) as blocked:
            app.render(panel["id"])
        assert blocked.value.body["code"] == "workdir_confirmation_required"
        assert state["plan"]["environment"]["source"] == "project_venv"
        evidence.append("ambiguous_data (h5) → asked once, no recommendation, render blocked")
        # ② 用户选项目根（公开流程）→ 准备 → ready；回执是**那个** venv 的
        app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        state = app.prepare(panel["id"])
        plan, result = state["plan"], state["result"]
        assert result["status"] == "ready", result
        receipt = result["receipt"]
        assert receipt["completeness"] == "complete" and receipt["runtime_rejected"] is None
        assert receipt["pid_check"] == "ok"
        assert receipt["launch_context"]["cwd_origin"] == "project.root"
        rt = receipt["runtime"]
        assert rt["python_version"] == _probe(
            venv_py, "import platform; print(platform.python_version())"
        )
        assert rt["python_version"][:4] != platform.python_version()[:4]
        assert rt["packages"]["h5py"] == _probe(venv_py, "import h5py; print(h5py.__version__)")
        assert receipt["source_revision"] == script_sha1
        # h5py 经 C 库读：输入观察看不见它——回执如实 partial、绑定核对 None（不冒充核过）
        assert receipt["inputs"]["observation"] == "partial"
        assert "native_io" in receipt["inputs"]["unobserved"]
        assert "data/measure.h5" not in {f["path"] for f in receipt["inputs"]["files"]}
        assert receipt["binding_check"]["matched"] is None
        assert receipt["binding_check"]["unobserved"] == ["data/measure.h5"]
        assert plan["binding"]["expected"]["data/measure.h5"] == _sha256(
            proj / "data" / "measure.h5"
        )
        assert "/" not in json.dumps(receipt["runtime"].get("prefix", ""))  # 公开投影不带 prefix
        _, envst = app.call("/api/engine/environment", timeout=30)
        chosen = envst["project"]["python"]
        chosen_path = chosen if Path(chosen).is_absolute() else str(proj / chosen)
        ident = fa.independent_identity(chosen_path)
        assert Path(ident["prefix"]).resolve() == Path(venv["venv_prefix"]).resolve()
        evidence.append(
            f"ready on project venv python {rt['python_version']} (app {platform.python_version()}); "
            f"h5py {rt['packages']['h5py']}; observation partial, binding unobserved"
        )
        # ③ 图内值 = 独立真值（不是干扰的那份）
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["correct"]["y"]), abs=1e-6)
        assert ylim != pytest.approx(_expected_ylim(truth["decoy"]["y"]), abs=1e-6)
        assert _title_text(render).startswith(truth["title_prefix"] + rt["python_version"][:4])
        # ④ 一次可见 patch（数据不变）→ 重放
        patch = {"gid": _title(render)["gid"], "prop": "text", "value": "U09 joined"}
        after = app.render(panel["id"], [patch])
        assert _title_text(after) == "U09 joined"
        assert _axes_ylim(after) == pytest.approx(ylim, abs=1e-6)
        evidence.append(f"ylim {ylim} == truth [7, 13, 25]; title patched and replayed")
        # ⑤ 新 RenderCore 导出 PDF / PNG / TIFF（带 override 的面板由当次 worker 现画 + 回执）
        ppi = 150
        _, body = app.call(
            "/api/export",
            _canvas_export(panel["id"], patch, formats=["pdf", "png", "tiff"], ppi=ppi),
            timeout=600,
        )
        assert body["status"] == "done", body
        assert body["trace"]["failed_phase"] is None
        outputs = _check_outputs(body, expected_title="U09 joined", ppi=ppi)
        pdf_mani = outputs["pdf"]["manifest"]
        assert pdf_mani["backend"] == "rendercore"
        prov = pdf_mani["provenance"]
        assert prov["sources"][0]["origin"] == "execution"
        assert prov["sources"][0]["receipt_identity"] == prov["execution_receipts"][0]
        facts = prov["receipts"][0]
        assert facts["python_version"] == rt["python_version"]
        assert facts["packages"]["h5py"] == rt["packages"]["h5py"]
        assert facts["source_revision"] == script_sha1
        assert facts["completeness"] == "complete" and facts["pid_check"] == "ok"
        assert facts["cwd_origin"] == "project.root"
        assert facts["binding"]["matched"] is None and facts["binding"]["unobserved"] == 1
        node = next(n for n in prov["nodes"] if n["kind"] == "imported_page")
        assert node["origin"] == "execution" and node["internal"] == "unknown"
        # 两个科学环境（项目 venv / 应用）之间：渲染是应用的事——manifest 的 backend 是应用的 RenderCore，
        # 回执的解释器是项目的 venv
        assert facts["python_version"][:4] != platform.python_version()[:4]
        evidence.append(
            "rendercore export pdf/png/tiff verified; manifest receipts == preparation receipt facts; "
            f"final sha256 {outputs['pdf']['sha256'][:12]}…"
        )
        observed = {
            "backend": "rendercore",
            "project_python": {
                "version": rt["python_version"],
                "h5py": rt["packages"]["h5py"],
                "matplotlib": rt["packages"].get("matplotlib"),
            },
            "app_python": platform.python_version(),
            "receipt": receipt,
            "required_input": need,
            "plotted_ylim": ylim,
            "patch": patch,
            "outputs": {
                fmt: {"sha256": v["sha256"], "identity": v["manifest"]["identity"]}
                for fmt, v in outputs.items()
            },
            "source_revision": script_sha1,
        }
    assert _sha1(proj / "scripts" / "figure_h5.py") == script_sha1, "脚本不许被改"
    assert _sha256(proj / "data" / "measure.h5") == plan["binding"]["expected"]["data/measure.h5"]
    _record("FO32", binding, "guided", observed, evidence, tmp_path / "results")


@needs_candidate
@needs_project_python
def test_fo32_wrong_choice_reads_the_decoy_and_the_identities_say_so(tmp_path):
    """负例（错误同名数据）：同一个项目、同一个 venv，用户选了脚本目录——读到的是干扰那份，值是
    [601, 1201, 2401]（产品不就近替换成「看起来对」的）；回执的绑定修订 / cwd_origin 与正确那次不同，
    导出的 semantic 身份也不同——错数据不会混进同一个身份。"""
    truth = _truth()
    proj = _joined_project(tmp_path)
    venv = _build_project_venv(proj, tmp_path)
    _native_reference(proj, venv["venv_python"], tmp_path, cwd=proj)
    with fa.running_app(
        proj, tmp_path / "work", env_overrides={"TAVOTTO_RENDER_BACKEND": "rendercore"}
    ) as app:
        panel = _panel(app, "figure_h5.pdf")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "needs_input"
        app.call("/api/engine/workdir", {"mode": "project"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        plan, receipt = state["plan"], state["result"]["receipt"]
        assert receipt["launch_context"]["cwd_origin"] == "script.parent"
        assert plan["binding"]["expected"]["scripts/data/measure.h5"] == _sha256(
            proj / "scripts" / "data" / "measure.h5"
        )
        render = app.render(panel["id"])
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["decoy"]["y"]), abs=1e-6)
        patch = {"gid": _title(render)["gid"], "prop": "text", "value": "U09 decoy"}
        _, body = app.call(
            "/api/export", _canvas_export(panel["id"], patch, formats=["pdf"], ppi=100), timeout=600
        )
        assert body["status"] == "done", body
        decoy_semantic = body["outputs"][0]["manifest"]["identity"]["semantic"]
        decoy_facts = body["outputs"][0]["manifest"]["provenance"]["receipts"][0]
        assert decoy_facts["cwd_origin"] == "script.parent"
        # 换回项目根：另一份绑定、另一个语义身份
        app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["plan"]["binding"]["revision"] != plan["binding"]["revision"]
        _, body2 = app.call(
            "/api/export", _canvas_export(panel["id"], patch, formats=["pdf"], ppi=100), timeout=600
        )
        assert body2["status"] == "done", body2
        assert body2["outputs"][0]["manifest"]["identity"]["semantic"] != decoy_semantic
        facts2 = body2["outputs"][0]["manifest"]["provenance"]["receipts"][0]
        assert facts2["cwd_origin"] == "project.root"
        assert facts2["binding"]["revision"] != decoy_facts["binding"]["revision"]


# ================================================================ FO32 · managed_env_join


REAL = os.environ.get("TAVOTTO_PRIVATE_PYTHON_REAL") == "1"


@needs_candidate
@pytest.mark.skipif(
    not REAL,
    reason="真 pbs 归档要联网 / 要缓存与 wheelhouse：TAVOTTO_PRIVATE_PYTHON_REAL=1 才跑（本机 macOS 真跑；"
    "其它目标由 private-python-targets.yml 观察）",
)
class TestManagedEnvJoin:
    """FO32（出口二）：**产品自己准备环境**——这台机器没有任何可用 Python（发现链末端置空，U05 的进程内形态，
    会话认证的旁路是 pytest 的 test_client）→ 首开先问工作目录（同名干扰）→ 再问依赖（`dependency_preparation_required`
    带 `private_python`：按锁下载 / 缓存的真 pbs 归档）→ 一次授权：供应私有 Python → U04 代事务把 h5py（+ adapter 的
    matplotlib / numpy）从 wheelhouse 装进那一代 → ready → 真值 → 可见 patch → RenderCore 导出 → 独立核文件 →
    manifest 里的回执说的是**那一代**（私有 Python 的版本、prefix 在受管环境、h5py 版本）。

    开关与 `tests/test_private_python_transaction.py::TestRealChain` 同一套：`TAVOTTO_PRIVATE_PYTHON_REAL=1`、
    `TAVOTTO_PRIVATE_PYTHON_CACHE`（有归档就零请求）、`TAVOTTO_PRIVATE_PYTHON_WHEELHOUSE`（要有宿主目标 cp313 的
    matplotlib / numpy / h5py 及其依赖）。这仍是工程验证，不是无系统 Python 的目标资格（ADR 0064 第三档）。
    """

    @pytest.fixture(autouse=True)
    def _clean_machine(self, tmp_path, monkeypatch):
        from tavotto.engine import (
            bootstrap,
            depplan,
            deprepair,
            envlease,
            managedenv,
            pool as engine_pool,
            privatepython,
        )

        wheelhouse = os.environ.get("TAVOTTO_PRIVATE_PYTHON_WHEELHOUSE")
        if not wheelhouse or not Path(wheelhouse).is_dir():
            pytest.skip(
                "TAVOTTO_PRIVATE_PYTHON_WHEELHOUSE 没指到一个目录（要 matplotlib / numpy / h5py 的 wheel）"
            )
        envlease.reset_for_tests()
        depplan.reset_cache()
        privatepython.reset_for_tests()
        deprepair.reset_state()
        # 数据目录：目标腿指定时用它（TestRealChain 刚供应好的 runtime 就在里面，零下载复用）
        data_dir = os.environ.get("TAVOTTO_PRIVATE_PYTHON_DATA_DIR") or str(tmp_path / "data")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("TAVOTTO_DATA_DIR", data_dir)
        monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
        monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "1")
        monkeypatch.setenv("TAVOTTO_RENDER_BACKEND", "rendercore")
        monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
        monkeypatch.delenv("MM_WORKER_PYTHON", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.setenv("PIP_FIND_LINKS", wheelhouse)
        monkeypatch.setenv("PIP_NO_INDEX", "1")
        src = privatepython.source_for()
        assert src is not None, "宿主目标不在锁文件里"
        cache = os.environ.get("TAVOTTO_PRIVATE_PYTHON_CACHE")
        if cache and (Path(cache) / src.archive_name).is_file():
            privatepython.downloads_dir().mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(cache) / src.archive_name, privatepython.archive_path(src))
        self.source = src
        # 干净机器：发现链末端什么都找不到——直到 Tavotto 自己建出受管环境
        monkeypatch.setattr(bootstrap, "find_base_python", lambda accept=None: None)
        monkeypatch.setattr(deprepair, "_base_python", None)
        monkeypatch.setattr(deprepair, "_base_python_known", False)
        real_resolve = engine_pool.resolve_worker_python

        def _resolve(figures_dir=None, *, script=None, discover=True):
            if figures_dir is not None and managedenv.python_of(str(figures_dir)):
                return real_resolve(figures_dir, script=script, discover=discover)
            raise engine_pool._no_python_error()

        monkeypatch.setattr(engine_pool, "resolve_worker_python", _resolve)
        yield
        envlease.reset_for_tests()
        depplan.reset_cache()
        privatepython.reset_for_tests()

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from tavotto import app as m
        from tavotto.engine import exportjob, preparation
        from tavotto.rendercore import fonts, renderhost

        reg = fonts.FontRegistry.discover()
        if reg.missing:
            pytest.skip(f"批准字体不全（not_run）：缺 {reg.missing}；先跑 scripts/fetch_fonts.py")
        m.app.config["TESTING"] = True
        m.reset_projects()
        monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
        monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
        monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
        monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
        preparation.SERVICE.reset_for_tests()
        exportjob.reset_for_tests()
        yield m.app.test_client()
        for pid in list(m.PROJECTS):
            m.close_project(pid, wait=True)
        m.reset_projects()
        preparation.SERVICE.reset_for_tests()
        exportjob.reset_for_tests()
        renderhost.shutdown_shared()

    @staticmethod
    def _prepare(client, panel_id: str, timeout: float = 120.0) -> dict:
        import time

        from tavotto.engine import preparation

        resp = client.post("/api/engine/preparation", json={"id": panel_id})
        assert resp.status_code == 202, resp.get_json()
        plan_id = resp.get_json()["plan"]["plan_id"]
        deadline = time.time() + timeout
        while True:
            body = client.get(f"/api/engine/preparation/{plan_id}").get_json()
            if body["result"]["status"] in preparation.TERMINAL:
                return body
            assert time.time() < deadline, body
            time.sleep(0.05)

    @staticmethod
    def _authorize(client, script: str, timeout: float = 1500.0) -> tuple[dict, dict]:
        import time

        from tavotto.engine import deprepair

        resp = client.post("/api/engine/dependencies/plan", json={"script": script})
        assert resp.status_code == 200, resp.get_json()
        plan = resp.get_json()["plan"]
        resp = client.post("/api/engine/dependencies/prepare", json={"plan_id": plan["plan_id"]})
        assert resp.status_code == 200, resp.get_json()
        deadline = time.time() + timeout
        while True:
            rec = deprepair.progress(plan["plan_id"])
            if rec["state"] in (
                deprepair.STATE_DONE,
                deprepair.STATE_FAILED,
                deprepair.STATE_CANCELLED,
            ):
                return plan, rec
            assert time.time() < deadline, rec
            time.sleep(0.2)

    def test_fo32_managed_env_private_python_joint_install_then_rendercore(self, client, tmp_path):
        from tavotto.engine import deprepair, managedenv, preparation, privatepython

        case, binding = _case("FO32")
        truth = _truth()
        proj = _joined_project(tmp_path)
        (proj / "requirements.txt").write_text("h5py>=3\n", encoding="utf-8")
        # 用户在别的机器上跑过一次：磁盘上有原件（素材库据此列出面板）——这台机器仍然「没有 Python」
        (proj / "figure_h5.pdf").write_bytes(
            (FIXTURES / "pdf_png_assets" / "page.pdf").read_bytes()
        )
        script_sha1 = _sha1(proj / "scripts" / "figure_h5.py")
        evidence: list[str] = []
        resp = client.post("/api/projects/open", json={"path": str(proj)})
        assert resp.status_code == 200, resp.get_json()
        panels = client.get("/api/panels").get_json()["panels"]
        panel = next(p for p in panels if p["id"].replace("\\", "/") == "figure_h5.pdf")
        # ① 先问工作目录（同名干扰），与已有环境那条出口同一道门
        body = self._prepare(client, panel["id"])
        need = body["result"]["required_input"]
        assert body["result"]["status"] == "needs_input", body["result"]
        assert (
            need["code"] == "workdir_confirmation_required" and need["reason"] == "ambiguous_data"
        )
        resp = client.patch("/api/engine/workdir", json={"mode": "project_root"})
        assert resp.status_code == 200, resp.get_json()
        # ② 再问依赖：目标是受管环境、要先供应私有 Python（缓存有就 0 字节）
        body = self._prepare(client, panel["id"])
        assert body["result"]["status"] == "needs_input", body["result"]
        door = body["result"]["required_input"]
        assert door["code"] == deprepair.ERROR_PREPARATION_REQUIRED
        managed = next(t for t in door["targets"] if t["kind"] == deprepair.TARGET_MANAGED)
        assert managed["private_python"]["required"] is True
        assert managed["private_python"]["id"] == self.source.id
        evidence.append(
            f"asked workdir (ambiguous_data) then dependencies (private python {self.source.id}, "
            f"download_bytes={managed['private_python']['download_bytes']})"
        )
        # ③ 一次授权：供应 → 代事务装 h5py（+ adapter）→ done
        plan, rec = self._authorize(client, "scripts/figure_h5.py")
        assert plan["private_python"]["required"] is True
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert privatepython.python_of(self.source)
        gen = rec["result"]["generation"]
        record = managedenv.generations(str(proj))[gen]
        assert (
            record["base_source"] == "private_python" and record["base_runtime"] == self.source.id
        )
        venv_py = managedenv.python_of(str(proj))
        assert venv_py and Path(venv_py).is_file()
        assert _probe(venv_py, "import h5py; print(h5py.__version__)")
        for mod in (
            "pikepdf",
            "pypdfium2",
            "uharfbuzz",
        ):  # 科学环境里没有应用的 PDF 候选包（FO-063）
            assert not _importable(venv_py, mod), f"受管环境里不该有 {mod}"
        # ④ 准备 → ready；回执是那一代的：私有 Python 的版本、h5py 在包表里
        body = self._prepare(client, panel["id"])
        assert body["result"]["status"] == preparation.STATUS_READY, body["result"]
        receipt = body["result"]["receipt"]
        rt = receipt["runtime"]
        assert receipt["completeness"] == "complete" and receipt["pid_check"] == "ok"
        assert rt["python_version"] == self.source.version
        assert rt["packages"]["h5py"] == _probe(venv_py, "import h5py; print(h5py.__version__)")
        assert receipt["source_revision"] == script_sha1
        assert receipt["launch_context"]["cwd_origin"] == "project.root"
        assert receipt["inputs"]["observation"] == "partial"
        assert receipt["binding_check"]["matched"] is None  # h5py 原生读：没观察到，不冒充
        ident = fa.independent_identity(venv_py)
        assert ident["version"] == self.source.version
        assert Path(os.path.realpath(ident["base_prefix"])) == Path(
            os.path.realpath(privatepython.runtime_dir(self.source))
        )
        assert Path(os.path.realpath(ident["prefix"])) == Path(
            os.path.realpath(managedenv.generation_dir(str(proj), gen))
        )
        evidence.append(
            f"generation {gen} on private python {rt['python_version']} (app {platform.python_version()}); "
            f"h5py {rt['packages']['h5py']} installed by the product"
        )
        # ⑤ 真值 → patch → 重放
        resp = client.post("/api/engine/render", json={"id": panel["id"], "patches": []})
        assert resp.status_code == 200, resp.get_json()
        render = resp.get_json()
        ylim = _axes_ylim(render)
        assert ylim == pytest.approx(_expected_ylim(truth["correct"]["y"]), abs=1e-6)
        patch = {"gid": _title(render)["gid"], "prop": "text", "value": "U09 joined"}
        resp = client.post("/api/engine/render", json={"id": panel["id"], "patches": [patch]})
        assert _title_text(resp.get_json()) == "U09 joined"
        # ⑥ RenderCore 导出 → 独立核文件 → manifest 的回执 == 准备的回执
        ppi = 150
        resp = client.post(
            "/api/export",
            json=_canvas_export(panel["id"], patch, formats=["pdf", "png", "tiff"], ppi=ppi),
        )
        assert resp.status_code == 200, resp.get_json()
        export = resp.get_json()
        assert export["status"] == "done", export
        outputs = _check_outputs(export, expected_title="U09 joined", ppi=ppi)
        facts = outputs["pdf"]["manifest"]["provenance"]["receipts"][0]
        assert facts["python_version"] == self.source.version
        assert facts["packages"]["h5py"] == rt["packages"]["h5py"]
        assert facts["source_revision"] == script_sha1 and facts["cwd_origin"] == "project.root"
        assert outputs["pdf"]["manifest"]["backend"] == "rendercore"
        evidence.append(
            f"rendercore export pdf/png/tiff verified; final sha256 {outputs['pdf']['sha256'][:12]}…"
        )
        observed = {
            "backend": "rendercore",
            "milestone": "managed_env_join",
            "private_python": {"id": self.source.id, "version": self.source.version},
            "generation": gen,
            "app_python": platform.python_version(),
            "receipt": receipt,
            "plotted_ylim": ylim,
            "outputs": {
                fmt: {"sha256": v["sha256"], "identity": v["manifest"]["identity"]}
                for fmt, v in outputs.items()
            },
        }
        assert _sha1(proj / "scripts" / "figure_h5.py") == script_sha1
        _record("FO32", binding, "guided", observed, evidence, tmp_path / "results")


# ================================================================ FO30 · 预检后输入或环境改变（真实入口）


try:
    from tavotto.engine import pool as _pool

    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001 — 没有科学栈就 skip，而 skip 在 CI 校验步里是红
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


def _rewrite_same_size(path: Path, text: str) -> None:
    """把文件改成另一份**同大小、同 mtime** 的内容：判据只能靠内容 hash。"""
    before = path.stat()
    assert len(text.encode("utf-8")) == before.st_size, "要同样的字节数"
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)


@needs_worker
def test_fo30_data_changed_after_execution_is_an_explicit_snapshot_until_the_user_rebuilds(
    tmp_path,
):
    """FO30（拆出来的合同，经真实入口）：
    ① prepare → ready → 值 = 数据 A；
    ② execute 与 export 之间改数据成 B（同大小、同 mtime）：再 prepare **复用**热会话——`ready`、回执的
       `binding_check.matched=False`、note 点明旧快照；render / export 仍是 A（明示旧快照，不自动重算、编辑不丢）；
       导出 manifest 的回执事实也说 binding 不匹配；
    ③ 用户明确「重新构建」（`/api/engine/invalidate`）→ 新一代读到 B：回执 matched=True、值 = B；
    ④ 改脚本再重建：回执 `source_revision` 是新的 sha1——旧计划的回执从不冒充实际执行。
    """
    case, binding = _case("FO30")
    truth = json.loads((FIXTURES / "single_file_csv" / "truth.json").read_text(encoding="utf-8"))
    proj = tmp_path / "proj"
    shutil.copytree(FIXTURES / "single_file_csv", proj)
    data = proj / "data.csv"
    # 用户在终端里跑过一次（原件在磁盘上）
    proc = subprocess.run(
        [WORKER_PY, "figure.py"],
        cwd=proj,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env={
            "PATH": "/usr/bin:/bin",
            "MPLCONFIGDIR": str(tmp_path / "mpl"),
            "MPLBACKEND": "Agg",
            **{
                k: os.environ[k]
                for k in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP")
                if os.environ.get(k)
            },
        },
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    sha_a = _sha256(data)
    evidence: list[str] = []
    with fa.running_app(proj, tmp_path / "work") as app:
        panel = _panel(app, truth["expected_output"])
        # ① 数据 A
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        r1 = state["result"]["receipt"]
        assert r1["binding_check"]["matched"] is True
        assert state["plan"]["binding"]["expected"]["data.csv"] == sha_a
        assert (
            r1["inputs"]["files"][0]["path"] == "data.csv"
            and r1["inputs"]["files"][0]["sha256"] == sha_a
        )
        ylim_a = _axes_ylim(app.render(panel["id"]))
        assert ylim_a == pytest.approx(_expected_ylim(truth["y"]), abs=1e-6)
        evidence.append(f"A: ready, binding matched, ylim {ylim_a}")
        # ② 数据变成 B（同大小同 mtime）：明示旧快照
        new_y = [9.5, 8.0, 6.5, 3.0]
        _rewrite_same_size(data, "x,y\n1,9.5\n2,8.0\n3,6.5\n4,3.0\n")
        sha_b = _sha256(data)
        assert sha_b != sha_a
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        assert state["result"]["existing_runtime"] is not None
        assert state["result"]["created_runtime"] is False
        r2 = state["result"]["receipt"]
        assert state["plan"]["binding"]["expected"]["data.csv"] == sha_b
        assert r2["binding_check"]["matched"] is False and r2["binding_check"]["changed"] == [
            "data.csv"
        ]
        assert "旧快照" in state["result"]["note"]
        assert r2["generation"] == r1["generation"]
        assert _axes_ylim(app.render(panel["id"])) == pytest.approx(ylim_a, abs=1e-6)  # 仍是 A
        patch = _first_text_patch(app.render(panel["id"]), "FO30 snapshot")
        _, body = app.call(
            "/api/export",
            {
                "scope": "original",
                "filename": "fo30",
                "formats": ["pdf"],
                "overwrite": "replace",
                "original": {
                    "figure_id": panel["id"],
                    "overrides": [patch],
                    "source_kind": "figure",
                    "w_mm": 81.28,
                    "h_mm": 60.96,
                },
            },
            timeout=300,
        )
        assert body["status"] == "done", body
        evidence.append(
            "B written (same size/mtime): reuse → ready + matched=False + snapshot note; render still A"
        )
        # ③ 用户明确重建 → 读到 B
        _, inv = app.call("/api/engine/invalidate", {"id": panel["id"]})
        assert inv["invalidated"] is True
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        r3 = state["result"]["receipt"]
        assert r3["generation"] > r1["generation"]
        assert r3["binding_check"]["matched"] is True
        assert r3["inputs"]["files"][0]["sha256"] == sha_b
        assert r3["public_identity"] != r1["public_identity"]  # 另一份数据 = 另一次执行
        ylim_b = _axes_ylim(app.render(panel["id"]))
        assert ylim_b == pytest.approx(_expected_ylim(new_y), abs=1e-6)
        evidence.append(
            f"rebuild → generation {r3['generation']}, binding matched on B, ylim {ylim_b}"
        )
        # ④ 脚本变了（加一行注释）→ 重建 → 回执的 source revision 是新的
        script = proj / "figure.py"
        script.write_text(
            script.read_text(encoding="utf-8") + "\n# FO30 revision 2\n", encoding="utf-8"
        )
        app.call("/api/engine/invalidate", {"id": panel["id"]})
        state = app.prepare(panel["id"])
        assert state["result"]["status"] == "ready", state["result"]
        r4 = state["result"]["receipt"]
        assert r4["source_revision"] == _sha1(script) != r3["source_revision"]
        evidence.append("script edited + rebuild → receipt source_revision follows the file")
        observed = {
            "backend": case.get("backend"),
            "sha": {"a": sha_a, "b": sha_b},
            "ylim": {"a": ylim_a, "b": ylim_b},
            "receipts": {
                "a": {"generation": r1["generation"], "matched": r1["binding_check"]["matched"]},
                "reuse_after_change": {
                    "generation": r2["generation"],
                    "matched": r2["binding_check"]["matched"],
                    "note": state["result"]["note"],
                },
                "rebuilt": {
                    "generation": r3["generation"],
                    "matched": r3["binding_check"]["matched"],
                },
                "script_edited": {"source_revision": r4["source_revision"]},
            },
        }
    _record("FO30", binding, "guided", observed, evidence, tmp_path / "results")
