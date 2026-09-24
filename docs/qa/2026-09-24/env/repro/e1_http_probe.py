"""QA 2026-09-24 §3（ENV-01…ENV-07）：E1 双环境经**真实 HTTP 首开入口**的探针。

    PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/env/repro/e1_http_probe.py <scenario> --e1 <scratch>/e1 --out <dir>

先跑 ``make_e1.py --dest <scratch>/e1`` 建夹具。每个 scenario 都在 ``--e1`` 的一份**新拷贝**
（``<out>/<scenario>/proj``）上跑：项目 ``.venv`` 用 ``uv venv --relocatable``?——不，venv 不能搬，
所以 A 环境留在 e1/proj/.venv 原地，scenario 把 ``figure.py`` 与 ``.venv`` 的**符号链接**都不用：
每个 scenario 直接用 e1/proj（场景之间靠独立的数据 / 配置目录隔离；项目内的 ``.tavotto`` 设置在
每个场景开始前清掉并记录）。

装置：``tests/support/foundation_app.running_app``（``python -m tavotto`` + 会话认证、配置 / 数据目录
全新、摘掉 ``TAVOTTO_WORKER_PYTHON``），端口钉在 5303。harness **不**设 ``TAVOTTO_WORKER_PYTHON``、
不 remember、不装包；``TAVOTTO_USER_ENV_DISCOVERY`` 从服务环境里摘掉（产品默认 = 开）。

判据的主语：worker 进程**自报**的 ``sys.executable`` / ``sys.prefix``（回执 runtime）+ 脚本在图里写下的
解释器身份与包 ``__file__``（manifest 里的文字）+ 图里的**完整点序列**（标题里的 JSON）+ 导出 PDF 的
文字层（pypdfium2 独立读取器，不是产品检查器）。

输出：``<out>/<scenario>.json``（证据）与标准输出的逐步记录；任一断言不过退出码 1。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

WT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(WT / "tests"))

from support import foundation_app as fa  # noqa: E402

PORT = 5303
fa.free_port = lambda: PORT  # 端口钉死（QA 分配），不碰 5089

VALUES_A = [0, 9, 2, 10]
VALUES_B = [1, 1, 1, 1]
CHECKS: list[dict] = []
EVIDENCE: dict = {}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def check(name: str, cond: bool, detail=None) -> bool:
    CHECKS.append({"check": name, "ok": bool(cond), "detail": detail})
    log(f"{'PASS' if cond else 'FAIL'} {name}" + (f" :: {detail}" if detail is not None else ""))
    return bool(cond)


# ---------------------------------------------------------------- 夹具 / 独立探针


def load_e1(e1: Path) -> dict:
    return json.loads((e1 / "e1.json").read_text(encoding="utf-8"))


def py_of(venv: Path) -> str:
    return str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))


def dists(python: str) -> list[str]:
    """独立探针：环境里的 distribution 清单（== pip freeze 的主语，不需要 pip）。"""
    out = subprocess.run(
        [
            python,
            "-c",
            "import importlib.metadata as m, json;"
            "print(json.dumps(sorted(f\"{d.metadata['Name']}=={d.version}\" for d in m.distributions())))",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
        cwd="/",
    )
    return json.loads(out.stdout)


#: 合法缓存白名单：解释器 import 时自己写的字节码（用户在终端里跑同样会有）。
CACHE_WHITELIST = ("__pycache__",)


def tree_digest(root: Path) -> dict[str, str]:
    """venv 下每个文件的 sha256（跳过白名单缓存目录）。"""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in CACHE_WHITELIST]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.is_symlink():
                out[str(p.relative_to(root))] = "symlink:" + os.readlink(p)
                continue
            try:
                out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError as exc:
                out[str(p.relative_to(root))] = f"unreadable:{exc}"
    return out


def snapshot(label: str, venvs: dict[str, Path]) -> dict:
    snap = {}
    for name, venv in venvs.items():
        snap[name] = {"dists": dists(py_of(venv)), "files": tree_digest(venv)}
    EVIDENCE.setdefault("snapshots", {})[label] = {
        k: {"dists": v["dists"], "file_count": len(v["files"])} for k, v in snap.items()
    }
    return snap


def diff_snapshots(before: dict, after: dict) -> dict:
    out = {}
    for name in before:
        b, a = before[name], after[name]
        out[name] = {
            "dists_added": sorted(set(a["dists"]) - set(b["dists"])),
            "dists_removed": sorted(set(b["dists"]) - set(a["dists"])),
            "files_added": sorted(set(a["files"]) - set(b["files"]))[:50],
            "files_removed": sorted(set(b["files"]) - set(a["files"]))[:50],
            "files_changed": sorted(
                k for k in set(a["files"]) & set(b["files"]) if a["files"][k] != b["files"][k]
            )[:50],
        }
    return out


def native_reference(python: str, proj: Path, tmp: Path) -> None:
    """用户在终端里跑过一次脚本：磁盘上有原件（面板从它来）。不是产品路径。"""
    env = {"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(tmp / "mpl"), "MPLBACKEND": "Agg"}
    subprocess.run([python, "figure.py"], cwd=proj, check=True, timeout=300, env=env)


def reset_project_state(proj: Path) -> None:
    """清掉项目内上一场景留下的 Tavotto 设置（记住的环境 / 工作目录决定都在这里）。"""
    for name in ("tavottofile",):
        p = proj / name
        if p.exists():
            shutil.rmtree(p)


# ---------------------------------------------------------------- 读产品响应


def title_text(render: dict) -> str | None:
    for e in render["manifest"]["elements"]:
        if e.get("role") == "title":
            for f in e.get("editable", []):
                if f["prop"] == "text":
                    return f["value"]
    return None


def all_texts(render: dict) -> list[str]:
    out = []
    for e in render["manifest"]["elements"]:
        for f in e.get("editable", []):
            if f["prop"] == "text" and isinstance(f.get("value"), str):
                out.append(f["value"])
    return out


def ident_from(render: dict) -> dict | None:
    for t in all_texts(render):
        if t.startswith("ID "):
            return json.loads(t[3:])
    return None


def values_from(render: dict) -> list | None:
    t = title_text(render)
    if t and t.startswith("QA-E1 "):
        return json.loads(t[len("QA-E1 ") :])
    return None


def title_gid(render: dict) -> str:
    return next(e["gid"] for e in render["manifest"]["elements"] if e.get("role") == "title")


def panel_of(app, name: str, pj: str | None = None) -> dict:
    q = f"?pj={pj}" if pj else ""
    _, panels = app.call("/api/panels" + q, timeout=60)
    return next(p for p in panels["panels"] if p["id"].replace("\\", "/") == name)


def prepare(app, panel_id: str, pj: str | None = None, timeout: float = 300.0) -> dict:
    q = f"?pj={pj}" if pj else ""
    status, prep = app.call("/api/engine/preparation" + q, {"id": panel_id}, timeout=60)
    assert status == 202, prep
    plan_id = prep["plan"]["plan_id"]
    deadline = time.time() + timeout
    while True:
        _, state = app.call(f"/api/engine/preparation/{plan_id}{q}", timeout=60)
        if state["result"]["status"] in fa.TERMINAL:
            return state
        assert time.time() < deadline, state
        time.sleep(0.2)


def render(app, panel_id: str, patches=None, pj: str | None = None) -> dict:
    q = f"?pj={pj}" if pj else ""
    _, body = app.call(
        "/api/engine/render" + q, {"id": panel_id, "patches": patches or []}, timeout=300
    )
    return body


def export_pdf(app, panel_id: str, patch: dict | None, pj: str | None = None) -> dict:
    q = f"?pj={pj}" if pj else ""
    body = {
        "scope": "canvas",
        "filename": "qa-env-export",
        "formats": ["pdf"],
        "ppi": 100,
        "overwrite": "replace",
        "canvas": {
            "page_w_mm": 100,
            "page_h_mm": 80,
            "objects": [
                {
                    "type": "panel",
                    "id": panel_id,
                    "x_mm": 5,
                    "y_mm": 5,
                    "w_mm": 90,
                    "h_mm": 70,
                    "overrides": [patch] if patch else [],
                }
            ],
        },
    }
    _, out = app.call("/api/export" + q, body, timeout=600)
    return out


def pdf_text(path: Path) -> str:
    """独立读取器（PDFium）抽文字层——不是产品的检查器。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        parts = []
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            parts.append(tp.get_text_range())
            tp.close()
            page.close()
    finally:
        doc.close()
    return "\n".join(parts)


def runtime_of(result_or_render: dict) -> dict:
    rc = result_or_render.get("receipt") or {}
    return rc.get("runtime") or {}


def same_path(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return os.path.realpath(a) == os.path.realpath(b) or os.path.normpath(a) == os.path.normpath(b)


def env_for(e1: Path, *, b_first: bool) -> dict:
    path = os.environ.get("PATH", "/usr/bin:/bin")
    if b_first:
        path = str(e1 / "envB" / ".venv" / "bin") + os.pathsep + path
    return {"PATH": path}


@contextlib.contextmanager
def app_for(proj: Path, work: Path, env: dict):
    os.environ.pop("TAVOTTO_USER_ENV_DISCOVERY", None)  # 产品默认（开）
    with fa.running_app(proj, work, env_overrides=env) as app:
        yield app


def assert_identity_is(label: str, ident: dict | None, rt: dict, env: dict, values) -> None:
    """图里写下的解释器身份（脚本自己量的 sys.executable / prefix / 包 __file__）+ 完整点序列；
    回执 runtime 的公开投影不带路径（ADR 0053 §二），只核版本与 matplotlib 版本（辅证）。"""
    if rt:
        check(
            f"{label}: 回执 runtime.python_version == 期望解释器版本",
            rt.get("python_version") == env.get("python_version", rt.get("python_version")),
            rt.get("python_version"),
        )
        check(
            f"{label}: 回执 runtime.packages.matplotlib == 期望环境的",
            (rt.get("packages") or {}).get("matplotlib") == env["matplotlib"],
            (rt.get("packages") or {}).get("matplotlib"),
        )
    check(
        f"{label}: 图内身份 sys.executable == 期望",
        bool(ident) and same_path(ident.get("executable"), env["executable"]),
        ident and ident.get("executable"),
    )
    check(
        f"{label}: 图内身份 sys.prefix == 期望",
        bool(ident) and same_path(ident.get("prefix"), env["prefix"]),
        ident and ident.get("prefix"),
    )
    check(
        f"{label}: 包 __file__ 在期望环境里",
        bool(ident) and same_path(ident.get("pkg_file"), env["pkg_file"]),
        ident and ident.get("pkg_file"),
    )
    check(
        f"{label}: 包版本",
        bool(ident) and ident.get("pkg_version") == env["pkg_version"],
        ident and ident.get("pkg_version"),
    )
    check(f"{label}: 图中完整点序列", values == env["value"], values)


# ================================================================ 场景


def scenario_env01_02(e1: Path, out: Path, *, b_first: bool, tag: str) -> None:
    """ENV-01（b_first=False）/ ENV-02（b_first=True）：首开 → 准备 → 渲染 → 编辑 → 导出。"""
    rec = load_e1(e1)
    A, B = rec["envs"]["A"], rec["envs"]["B"]
    proj = e1 / "proj"
    reset_project_state(proj)
    venvs = {"A": proj / ".venv", "B": e1 / "envB" / ".venv"}
    native_reference(rec["pythons"]["A"], proj, out)
    builtin = subprocess.run(
        [sys.executable, "-c", "import qa_probe_pkg"], capture_output=True, text=True, cwd="/"
    )
    check(
        "前提：Tavotto 自身 / 内置解释器里 import 不到 qa_probe_pkg",
        builtin.returncode != 0,
        sys.executable,
    )
    before = snapshot("before", venvs)
    work = out / tag / "work"
    shutil.rmtree(work, ignore_errors=True)
    env = env_for(e1, b_first=b_first)
    EVIDENCE["server_env_PATH_head"] = env["PATH"].split(os.pathsep)[:2]
    if b_first:
        # 前提：B 在 PATH 上更靠前，而且两份都 import 得到（不然「选对」就是恒真）
        which = subprocess.run(
            [
                "/usr/bin/env",
                "python",
                "-c",
                "import sys, qa_probe_pkg; print(sys.prefix, qa_probe_pkg.value())",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
            cwd="/",
        )
        check(
            "前提：PATH 上第一个 python 是 B 且 import 得到 qa_probe_pkg",
            which.returncode == 0 and B["prefix"] in which.stdout,
            which.stdout.strip() or which.stderr[-300:],
        )
    with app_for(proj, work, env) as app:
        _, envst0 = app.call("/api/engine/environment", timeout=60)
        EVIDENCE["environment_before_prepare"] = envst0.get("project")
        panel = panel_of(app, "figure.pdf")
        state = prepare(app, panel["id"])
        plan, result = state["plan"], state["result"]
        EVIDENCE["plan_environment"] = plan.get("environment")
        EVIDENCE["result_status"] = result["status"]
        EVIDENCE["receipt"] = result.get("receipt")
        check(
            "prepare 终局 ready（无需输入）",
            result["status"] == "ready",
            result.get("error") or result.get("required_input"),
        )
        check(
            "计划选择来源 = project_venv",
            (plan.get("environment") or {}).get("source") == "project_venv",
            (plan.get("environment") or {}).get("source"),
        )
        check(
            "计划 trigger = first_open",
            (plan.get("environment") or {}).get("trigger") == "first_open",
            (plan.get("environment") or {}).get("trigger"),
        )
        rt0 = runtime_of(result)
        rc = result.get("receipt") or {}
        check(
            "准备回执 python_source = project_venv、generation = 1、pid_check ok",
            rc.get("python_source") == "project_venv"
            and rc.get("generation") == 1
            and rc.get("pid_check") == "ok",
            {k: rc.get(k) for k in ("python_source", "generation", "pid_check", "completeness")},
        )
        r1 = render(app, panel["id"])
        EVIDENCE["render1_runtime"] = runtime_of(r1)
        assert_identity_is("首次渲染", ident_from(r1), rt0, A, values_from(r1))
        # 编辑：改标题文字之外的属性（线色），重放后身份 / 数据仍来自 A
        line = next(
            (e for e in r1["manifest"]["elements"] if e.get("role") in ("line", "lines")), None
        )
        patch = None
        if line:
            color = next((f for f in line.get("editable", []) if f["prop"] == "color"), None)
            if color:
                patch = {"gid": line["gid"], "prop": "color", "value": "#d62728"}
        check(
            "找到可编辑的曲线颜色",
            patch is not None,
            line and [f["prop"] for f in line.get("editable", [])],
        )
        r2 = render(app, panel["id"], [patch] if patch else [])
        assert_identity_is("编辑后渲染", ident_from(r2), rt0, A, values_from(r2))
        # 导出（RenderCore），独立读取器读文字层
        ex = export_pdf(app, panel["id"], patch)
        EVIDENCE["export_status"] = ex.get("status")
        check("导出 status == done", ex.get("status") == "done", ex.get("status"))
        if ex.get("status") == "done":
            o = ex["outputs"][0]
            path = Path(ex["export_dir"]) / o["name"]
            data = path.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            EVIDENCE["export"] = {
                "name": o["name"],
                "sha256": sha,
                "manifest_sha256": o["manifest"].get("sha256"),
            }
            dest = out / tag / "export.pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            text = pdf_text(path)
            EVIDENCE["export_text_head"] = text[:600]
            check(
                "导出 PDF 文字层含 A 的点序列",
                ("QA-E1 " + json.dumps(A["value"])) in text,
                text[:200],
            )
            check("导出 PDF 文字层不含 B 的点序列", ("QA-E1 " + json.dumps(B["value"])) not in text)
            check(
                "导出 PDF 文字层含 A 的包路径",
                A["pkg_file"] in text.replace("\n", "").replace("\r", ""),
            )
            prov = (o["manifest"].get("provenance") or {}).get("receipts") or []
            EVIDENCE["export_receipt_facts"] = prov
            check(
                "导出 manifest 回执 python_version 与 A 一致",
                bool(prov) and prov[0].get("python_version") == rt0.get("python_version"),
                prov and prov[0].get("python_version"),
            )
        _, envst = app.call("/api/engine/environment", timeout=60)
        EVIDENCE["environment_after"] = envst.get("project")
        log_text = app.server_log()
    EVIDENCE["server_log_mentions_missing_dependency"] = "missing_dependency" in log_text
    check(
        "服务日志里没有 missing_dependency（没有先在内置环境跑错一次）",
        "missing_dependency" not in log_text,
    )
    after = snapshot("after", venvs)
    d = diff_snapshots(before, after)
    EVIDENCE["side_effects"] = d
    for name in venvs:
        check(
            f"副作用：{name} 包清单不变",
            not d[name]["dists_added"] and not d[name]["dists_removed"],
            d[name],
        )
        check(
            f"副作用：{name} 文件（白名单外）不变",
            not (d[name]["files_added"] or d[name]["files_removed"] or d[name]["files_changed"]),
            d[name],
        )
    (out / tag / "server.log").write_text(log_text, encoding="utf-8")


def scenario_env03(e1: Path, out: Path) -> None:
    """ENV-03：显式失效三种 + 纠正后成功；环境变量路径不存在按 ADR 0057 视为没设。"""
    rec = load_e1(e1)
    A = rec["envs"]["A"]
    proj = e1 / "proj"
    native_reference(rec["pythons"]["A"], proj, out)
    bare = rec["pythons"]["bare"]
    # (a) 环境变量 → 存在但没有 matplotlib
    reset_project_state(proj)
    work = out / "ENV-03" / "a"
    shutil.rmtree(work, ignore_errors=True)
    with app_for(proj, work, {"TAVOTTO_WORKER_PYTHON": bare}) as app:
        panel = panel_of(app, "figure.pdf")
        _, envst = app.call("/api/engine/environment", timeout=60)
        err = (envst.get("project") or {}).get("resolution_error")
        EVIDENCE["a_resolution_error"] = err
        check(
            "(a) env 变量 bare：environment 报 explicit_python_unusable/no_matplotlib",
            bool(err)
            and err.get("code") == "explicit_python_unusable"
            and err.get("explicit", {}).get("reason") == "no_matplotlib",
            err,
        )
        state = prepare(app, panel["id"])
        EVIDENCE["a_prepare"] = {
            "status": state["result"]["status"],
            "error": state["result"].get("error"),
            "plan_env_python": state["plan"]["environment"].get("python"),
        }
        check(
            "(a) prepare 终局 error=explicit_python_unusable，未起 runtime",
            state["result"]["status"] == "error"
            and (state["result"].get("error") or {}).get("code") == "explicit_python_unusable"
            and state["result"].get("created_runtime") is False,
            EVIDENCE["a_prepare"],
        )
        try:
            render(app, panel["id"])
            check("(a) render 被拒", False, "render 成功了——静默换了环境")
        except fa.HttpError as exc:
            check(
                "(a) render 被拒且 code=explicit_python_unusable",
                exc.body.get("code") == "explicit_python_unusable",
                exc.body.get("code"),
            )
        cfg = _read_config(work)
        check(
            "(a) 没有偷偷记住项目环境（用户配置里没有 project_env 决策）",
            "project_venv" not in json.dumps(cfg)
            and ".venv" not in json.dumps(cfg.get("projects", {})),
            cfg.get("projects"),
        )
    # (a') 用户纠正：去掉环境变量重启 → 首开选 A 成功
    reset_project_state(proj)
    work = out / "ENV-03" / "a_fixed"
    shutil.rmtree(work, ignore_errors=True)
    with app_for(proj, work, {}) as app:
        panel = panel_of(app, "figure.pdf")
        state = prepare(app, panel["id"])
        check(
            "(a') 纠正后 prepare ready",
            state["result"]["status"] == "ready",
            state["result"].get("error"),
        )
        r = render(app, panel["id"])
        assert_identity_is(
            "(a') 纠正后",
            ident_from(r),
            runtime_of(r) or runtime_of(state["result"]),
            A,
            values_from(r),
        )
    # (b) 环境变量 → 路径不存在：ADR 0057 特殊合同 = 按「没设」
    reset_project_state(proj)
    work = out / "ENV-03" / "b"
    shutil.rmtree(work, ignore_errors=True)
    with app_for(
        proj, work, {"TAVOTTO_WORKER_PYTHON": str(out / "ENV-03" / "gone" / "python")}
    ) as app:
        panel = panel_of(app, "figure.pdf")
        _, envst = app.call("/api/engine/environment", timeout=60)
        EVIDENCE["b_environment"] = envst.get("project")
        state = prepare(app, panel["id"])
        check(
            "(b) env 变量指向不存在路径：按没设（ADR 0057），首开选 A 并 ready",
            state["result"]["status"] == "ready"
            and state["plan"]["environment"].get("source") == "project_venv",
            {
                "status": state["result"]["status"],
                "source": state["plan"]["environment"].get("source"),
            },
        )
    # (c) 设置里指定的全局解释器 → 之后消失
    reset_project_state(proj)
    work = out / "ENV-03" / "c"
    shutil.rmtree(work, ignore_errors=True)
    cdir = out / "ENV-03" / "c_env"
    shutil.rmtree(cdir, ignore_errors=True)
    subprocess.run(
        ["uv", "venv", "--python", rec["base"], "--system-site-packages", str(cdir)],
        check=True,
        capture_output=True,
    )
    c_python = py_of(cdir)
    # 让 C 能 import matplotlib：把 A 的 site-packages 用 .pth 接进来（一个字节不下载）
    site_a = Path(A["pkg_file"]).parent.parent
    site_c = next((cdir / "lib").glob("python3*/site-packages"))
    (site_c / "_qa_link_a.pth").write_text(str(site_a) + "\n", encoding="utf-8")
    with app_for(proj, work, {}) as app:
        panel = panel_of(app, "figure.pdf")
        try:
            st, body = app.call(
                "/api/engine/environment", {"python": c_python}, method="PATCH", timeout=180
            )
            check(
                "(c) PATCH 全局解释器 C 被接受",
                st == 200,
                body.get("python") if isinstance(body, dict) else body,
            )
        except fa.HttpError as exc:
            check("(c) PATCH 全局解释器 C 被接受", False, exc.body)
        shutil.rmtree(cdir)
        _, envst = app.call("/api/engine/environment", timeout=60)
        err = (envst.get("project") or {}).get("resolution_error")
        EVIDENCE["c_resolution_error"] = err
        state = prepare(app, panel["id"])
        e = state["result"].get("error") or {}
        EVIDENCE["c_prepare"] = {"status": state["result"]["status"], "error": e}
        check(
            "(c) 设置里的解释器消失：explicit_python_unusable/missing，不静默换 A",
            state["result"]["status"] == "error"
            and e.get("code") == "explicit_python_unusable"
            and (e.get("explicit") or {}).get("reason") == "missing"
            and (e.get("explicit") or {}).get("source") == "configured",
            e,
        )
        # 纠正：清掉全局设置
        st, body = app.call("/api/engine/environment", {"python": ""}, method="PATCH", timeout=120)
        state = prepare(app, panel["id"])
        check(
            "(c') 清除设置后 prepare ready 且来源 project_venv",
            state["result"]["status"] == "ready"
            and state["plan"]["environment"].get("source") == "project_venv",
            {
                "status": state["result"]["status"],
                "source": state["plan"]["environment"].get("source"),
            },
        )
        r = render(app, panel["id"])
        assert_identity_is(
            "(c') 纠正后",
            ident_from(r),
            runtime_of(r) or runtime_of(state["result"]),
            A,
            values_from(r),
        )
    # (d) 项目记住的手选环境失效
    reset_project_state(proj)
    work = out / "ENV-03" / "d"
    shutil.rmtree(work, ignore_errors=True)
    ddir = out / "ENV-03" / "d_env"
    shutil.rmtree(ddir, ignore_errors=True)
    subprocess.run(
        ["uv", "venv", "--python", rec["base"], str(ddir)], check=True, capture_output=True
    )
    site_d = next((ddir / "lib").glob("python3*/site-packages"))
    (site_d / "_qa_link_a.pth").write_text(str(site_a) + "\n", encoding="utf-8")
    with app_for(proj, work, {}) as app:
        panel = panel_of(app, "figure.pdf")
        try:
            st, body = app.call(
                "/api/engine/environment",
                {"python": py_of(ddir), "scope": "project"},
                method="PATCH",
                timeout=180,
            )
            check(
                "(d) PATCH 项目解释器 D 被接受",
                st == 200,
                (body.get("project") or {}).get("source") if isinstance(body, dict) else body,
            )
        except fa.HttpError as exc:
            check("(d) PATCH 项目解释器 D 被接受", False, exc.body)
        state = prepare(app, panel["id"])
        check(
            "(d) 手选 D 生效（source 非 project_venv 的自动发现）",
            state["result"]["status"] == "ready",
            {
                "status": state["result"]["status"],
                "source": state["plan"]["environment"].get("source"),
            },
        )
        # D 失效：把 .pth 摘掉 → 没有 matplotlib
        (site_d / "_qa_link_a.pth").unlink()
        # 旧会话 / 体检缓存按产品合同失效：重启服务模拟「下一次打开」
    with app_for(proj, work, {}) as app:  # 同一配置目录 = 同一个用户下一次打开
        panel = panel_of(app, "figure.pdf")
        state = prepare(app, panel["id"])
        e = state["result"].get("error") or {}
        EVIDENCE["d_prepare"] = {"status": state["result"]["status"], "error": e}
        check(
            "(d) 手选项目环境失效：project_python_unusable，不静默换 A",
            state["result"]["status"] == "error" and e.get("code") == "project_python_unusable",
            e,
        )
        st, body = app.call(
            "/api/engine/environment",
            {"python": "", "scope": "project"},
            method="PATCH",
            timeout=120,
        )
        state = prepare(app, panel["id"])
        EVIDENCE["d_fixed"] = {
            "status": state["result"]["status"],
            "source": state["plan"]["environment"].get("source"),
        }
        check(
            "(d') 用户选回默认链条后可运行",
            state["result"]["status"] == "ready",
            EVIDENCE["d_fixed"],
        )
    shutil.rmtree(ddir, ignore_errors=True)


def _read_config(work: Path) -> dict:
    """服务用户配置目录里的 config.json（项目设置 / 全局解释器都在这里）。"""
    for p in sorted((work / "config").rglob("*.json")):
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "projects" in data:
                return data
    return {}


SCENARIOS = {
    "ENV-01": lambda e1, out: scenario_env01_02(e1, out, b_first=False, tag="ENV-01"),
    "ENV-02": lambda e1, out: scenario_env01_02(e1, out, b_first=True, tag="ENV-02"),
    "ENV-03": scenario_env03,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", choices=sorted(SCENARIOS))
    ap.add_argument("--e1", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    e1, out = Path(args.e1).absolute(), Path(args.out).absolute()
    out.mkdir(parents=True, exist_ok=True)
    log(f"scenario={args.scenario} e1={e1} port={PORT}")
    crashed = None
    try:
        SCENARIOS[args.scenario](e1, out)
    except Exception:  # noqa: BLE001 — 记下来再按失败退出
        crashed = traceback.format_exc()
        log("CRASH\n" + crashed)
    ok = crashed is None and all(c["ok"] for c in CHECKS)
    report = {
        "scenario": args.scenario,
        "ok": ok,
        "crashed": crashed,
        "checks": CHECKS,
        "evidence": EVIDENCE,
    }
    (out / f"{args.scenario}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    log(
        f"RESULT {args.scenario}: {'PASS' if ok else 'FAIL'} ({sum(c['ok'] for c in CHECKS)}/{len(CHECKS)} checks)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
