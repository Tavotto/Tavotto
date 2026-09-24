"""QA 2026-09-24 §3 探针（续）：ENV-04 / ENV-05 / ENV-06 / ENV-07，复用 ``e1_http_probe`` 的装置与判据。

    PYTHONPATH=$PWD/src TAVOTTO_FONTS_DIR=<fonts> /Volumes/Projects/Tavotto/.venv/bin/python \\
        docs/qa/2026-09-24/env/repro/e1_http_probe_more.py <ENV-04|ENV-05|ENV-06|ENV-07> --e1 <scratch>/e1 --out <dir>
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e1_http_probe as p  # noqa: E402
import make_e1 as m  # noqa: E402

check, log, EVIDENCE = p.check, p.log, p.EVIDENCE


def make_project(root: Path, base: str, *wheels: Path, script: str | None = None) -> str:
    """一个像用户那样的项目：figure.py + 自己的 .venv（matplotlib + 给定 wheel）。回 venv python。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "figure.py").write_text(script or m.FIGURE_PY, encoding="utf-8")
    m.make_venv(root / ".venv", base, m.MPL_SPEC, *(str(w) for w in wheels))
    return p.py_of(root / ".venv")


def ident_of(python: str) -> dict:
    out = subprocess.run(
        [python, "-c", m.IDENT_SRC], capture_output=True, text=True, check=True, cwd="/"
    )
    return json.loads(out.stdout)


def project_env_state(app, pj: str | None = None) -> dict:
    q = f"?pj={pj}" if pj else ""
    _, envst = app.call("/api/engine/environment" + q, timeout=60)
    return envst.get("project") or {}


def wheel(dest: Path, name: str, version: str, files: dict) -> Path:
    """与 make_e1.build_wheel 同一套拼法，任意文件集。"""
    dist = f"{name}-{version}.dist-info"
    files = dict(files)
    files[f"{dist}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {name.replace('_', '-')}\nVersion: {version}\n".encode()
    )
    files[f"{dist}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: qa-make-e1\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    lines = []
    for n, data in files.items():
        dg = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        lines.append(f"{n},sha256={dg},{len(data)}")
    lines.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(lines) + "\n").encode()
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(out, "w") as zf:
        for n, data in files.items():
            zf.writestr(m._zinfo(n), data)
    return out


# ================================================================ ENV-04


def scenario_env04(e1: Path, out: Path) -> None:
    """两个项目各自 venv 不串；同一路径换依赖 / 换 Python 后，旧探测与旧 worker 不被沿用。"""
    rec = p.load_e1(e1)
    base = rec["base"]
    d = out / "ENV-04"
    shutil.rmtree(d, ignore_errors=True)
    wheels = e1 / "wheels"
    pa_py = make_project(d / "pA", base, wheels / "qa_probe_pkg-1.0.0-py3-none-any.whl")
    pb_py = make_project(d / "pB", base, wheels / "qa_probe_pkg-2.0.0-py3-none-any.whl")
    for py, proj in ((pa_py, d / "pA"), (pb_py, d / "pB")):
        p.native_reference(py, proj, d)
    ia, ib = ident_of(pa_py), ident_of(pb_py)
    EVIDENCE["ident"] = {"pA": ia, "pB": ib}
    work = d / "work"
    ra5 = None
    with p.app_for(d / "pA", work, {}) as app:
        _, opened = app.call(
            "/api/projects/open", {"path": str(d / "pB"), "default": False}, timeout=60
        )
        EVIDENCE["open_pB"] = (
            {k: opened.get(k) for k in ("id", "name")} if isinstance(opened, dict) else opened
        )
        pj_b = opened.get("id") if isinstance(opened, dict) else None
        check("打开第二个项目 pB 得到项目 id", bool(pj_b), opened if not pj_b else pj_b)
        pan_a = p.panel_of(app, "figure.pdf")
        pan_b = p.panel_of(app, "figure.pdf", pj_b)
        sa = p.prepare(app, pan_a["id"])
        sb = p.prepare(app, pan_b["id"], pj_b)
        check(
            "pA / pB 准备都 ready",
            sa["result"]["status"] == "ready" and sb["result"]["status"] == "ready",
            [sa["result"]["status"], sb["result"]["status"]],
        )
        ra1 = p.render(app, pan_a["id"])
        rb1 = p.render(app, pan_b["id"], None, pj_b)
        ra2 = p.render(app, pan_a["id"])
        p.assert_identity_is(
            "pA 第一次", p.ident_from(ra1), p.runtime_of(sa["result"]), ia, p.values_from(ra1)
        )
        p.assert_identity_is(
            "pB（夹在两次 pA 之间）",
            p.ident_from(rb1),
            p.runtime_of(sb["result"]),
            ib,
            p.values_from(rb1),
        )
        p.assert_identity_is(
            "pA 第二次（pB 之后）", p.ident_from(ra2), None, ia, p.values_from(ra2)
        )
        ea, eb = project_env_state(app), project_env_state(app, pj_b)
        EVIDENCE["env_state"] = {"pA": ea, "pB": eb}
        # 公开投影给的是**项目相对**路径（ADR 0053 §二）：两边都是 ".venv/bin/python"，各自相对自己的项目根
        check(
            "两个项目的环境状态各指自己项目内的 .venv",
            ea.get("source") == "project_venv"
            and eb.get("source") == "project_venv"
            and ea.get("python") == eb.get("python") == ".venv/bin/python",
            {"pA": ea.get("python"), "pB": eb.get("python")},
        )
        # ---- 同一路径换依赖：qa_probe_pkg 1.0.0 → 1.1.0（值 [3, 1, 4, 1]）
        m.VALUES["A2"], m.VERSIONS["A2"] = [3, 1, 4, 1], "1.1.0"
        w2 = m.build_wheel(d / "wheels", "A2")
        subprocess.run(
            ["uv", "pip", "install", "--offline", "--python", pa_py, str(w2)],
            check=True,
            capture_output=True,
        )
        after = ident_of(pa_py)
        check(
            "前提：pA 的 .venv 里现在是 1.1.0 / [3, 1, 4, 1]",
            after.get("value") == [3, 1, 4, 1],
            after.get("value"),
        )
        ra3 = p.render(app, pan_a["id"])
        EVIDENCE["after_dep_change_plain_render"] = {
            "values": p.values_from(ra3),
            "pkg_version": (p.ident_from(ra3) or {}).get("pkg_version"),
        }
        log(f"观察：换依赖后不点重新构建直接渲染 → {p.values_from(ra3)}（热会话）")
        st = p.prepare(app, pan_a["id"])
        EVIDENCE["after_dep_change_prepare"] = {
            "status": st["result"]["status"],
            "receipt_generation": (st["result"].get("receipt") or {}).get("generation"),
        }
        _, inv = app.call("/api/engine/invalidate", {"id": pan_a["id"]}, timeout=60)
        check("用户点「重新构建」：invalidated", inv.get("invalidated") is True, inv)
        ra4 = p.render(app, pan_a["id"])
        p.assert_identity_is(
            "换依赖 + 重新构建后", p.ident_from(ra4), None, after, p.values_from(ra4)
        )
        rb2 = p.render(app, pan_b["id"], None, pj_b)
        p.assert_identity_is(
            "pB 不受 pA 换依赖影响", p.ident_from(rb2), None, ib, p.values_from(rb2)
        )
        # ---- 同一路径换 Python：把 pA/.venv 重建成**没有 matplotlib** 的 venv（同一路径字符串）
        shutil.rmtree(d / "pA" / ".venv")
        subprocess.run(
            ["uv", "venv", "--python", base, str(d / "pA" / ".venv")],
            check=True,
            capture_output=True,
        )
        _, inv = app.call("/api/engine/invalidate", {"id": pan_a["id"]}, timeout=60)
        try:
            ra5 = p.render(app, pan_a["id"])
            EVIDENCE["after_python_replaced_render"] = {
                "ok": True,
                "values": p.values_from(ra5),
                "ident": p.ident_from(ra5),
            }
        except p.fa.HttpError as exc:
            EVIDENCE["after_python_replaced_render"] = {
                "ok": False,
                "status": exc.status,
                "code": exc.body.get("code"),
                "message": str(exc.body.get("error") or exc.body.get("message") or "")[:400],
                "module": exc.body.get("module"),
                "project_env": exc.body.get("project_env"),
            }
        st5 = p.prepare(app, pan_a["id"])
        EVIDENCE["after_python_replaced_prepare"] = {
            "status": st5["result"]["status"],
            "error": st5["result"].get("error"),
            "plan_env": {
                k: st5["plan"]["environment"].get(k)
                for k in ("source", "trigger", "invalidated", "error", "discovery")
            },
        }
        check(
            "换 Python 后：没有沿用旧模块（没有画出旧值）",
            ra5 is None or p.values_from(ra5) not in ([3, 1, 4, 1], [0, 9, 2, 10]),
            EVIDENCE["after_python_replaced_render"],
        )
        # 合同：旧体检失效并**重新验证**——同一进程里再准备一次，结论应当是体检的结论
        # （项目 venv 没有 matplotlib → 作废自动决定、如实写 invalidated / discovery.rejected），
        # 而不是沿用旧的「健康」结论去起 worker、以 session_dead 收场
        env5 = st5["plan"]["environment"]
        check(
            "换 Python 后（同一进程）：重新体检——计划写出 invalidated / discovery 拒绝原因，而不是旧的「健康」结论",
            bool(env5.get("invalidated")) or not (env5.get("discovery") or {}).get("ok", True),
            {
                "prepare": st5["result"].get("error", {}).get("code")
                if st5["result"].get("error")
                else st5["result"]["status"],
                "invalidated": env5.get("invalidated"),
                "discovery_ok": (env5.get("discovery") or {}).get("ok"),
            },
        )
    # 重启后端：进程内缓存清空，同一用户配置
    with p.app_for(d / "pA", work, {}) as app:
        st6 = p.prepare(app, p.panel_of(app, "figure.pdf")["id"])
        EVIDENCE["after_python_replaced_restart_prepare"] = {
            "status": st6["result"]["status"],
            "error": st6["result"].get("error"),
            "plan_env": {
                k: st6["plan"]["environment"].get(k)
                for k in ("source", "trigger", "invalidated", "error", "discovery")
            },
        }
        log(
            f"重启后 prepare: {json.dumps(EVIDENCE['after_python_replaced_restart_prepare'], ensure_ascii=False)[:600]}"
        )
        env6 = st6["plan"]["environment"]
        check(
            "重启后：重新体检、作废自动决定并说明原因（不静默换、不 crash）",
            (env6.get("invalidated") or {}).get("reason") == "no_matplotlib"
            and (env6.get("discovery") or {}).get("ok") is False,
            {"invalidated": env6.get("invalidated"), "discovery": env6.get("discovery")},
        )


# ================================================================ ENV-05


HEAD = "import os\nimport matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nREF = os.environ.get('QA_REFERENCE') == '1'\n"
TAIL = "fig, ax = plt.subplots(figsize=(3, 2))\nax.plot(range(len(ys)), ys)\nax.set_title('QA-E1 ' + str(ys))\nfig.savefig('{name}.pdf')\n"
SCRIPTS = {
    "fig_local": "import labmod\nys = labmod.data()\n",
    "fig_optional": (
        "import importlib\ntry:\n    import qa_optional_not_installed  # noqa: F401\nexcept ImportError:\n    pass\n"
        "json_mod = importlib.import_module('json')\nys = json_mod.loads('[0, 9, 2, 10]')\n"
    ),
    "fig_broken_internal": "if not REF:\n    import qa_broken_pkg  # noqa: F401\nys = [0, 9, 2, 10]\n",
    "fig_abi": "if not REF:\n    import qa_abi_pkg  # noqa: F401\nys = [0, 9, 2, 10]\n",
    "fig_local_typo": "if not REF:\n    import labbroken  # noqa: F401\nys = [0, 9, 2, 10]\n",
    "fig_broken_submodule": "if not REF:\n    import qa_subbroken_pkg  # noqa: F401\nys = [0, 9, 2, 10]\n",
}


def scenario_env05(e1: Path, out: Path) -> None:
    """本地模块 / 可选与动态 import / 包内部 ImportError / 二进制加载失败 的分类（不盲目安装）。

    夹具构造：原件 PDF 由「以前能跑的版本」产出（`QA_REFERENCE=1` 跳过坏 import）——面板需要磁盘上的原件；
    用户终端里（不带该变量）的真实结局另记为独立真值。"""
    rec = p.load_e1(e1)
    d = out / "ENV-05"
    shutil.rmtree(d, ignore_errors=True)
    proj = d / "p"
    proj.mkdir(parents=True)
    wd = d / "wheels"
    broken = wheel(
        wd,
        "qa_broken_pkg",
        "1.0.0",
        {"qa_broken_pkg/__init__.py": b"from . import _missing_sub  # noqa\n"},
    )
    abi = wheel(
        wd,
        "qa_abi_pkg",
        "1.0.0",
        {
            "qa_abi_pkg/__init__.py": b"from ._native import f  # noqa\n",
            "qa_abi_pkg/_native.cpython-313-darwin.so": b"\x00not a mach-o\x00" * 64,
        },
    )
    # 包装好了、但它的 __init__ 里 `import 自己的子模块`，而子模块不在：报错文字是
    # "No module named 'qa_subbroken_pkg._missing_sub'"——与 numpy ABI 坏掉时的
    # "No module named 'numpy.core._multiarray_umath'" 同形
    subbroken = wheel(
        wd,
        "qa_subbroken_pkg",
        "1.0.0",
        {"qa_subbroken_pkg/__init__.py": b"import qa_subbroken_pkg._missing_sub  # noqa\n"},
    )
    m.make_venv(proj / ".venv", rec["base"], m.MPL_SPEC, str(broken), str(abi), str(subbroken))
    py = p.py_of(proj / ".venv")
    (proj / "labmod.py").write_text("def data():\n    return [0, 9, 2, 10]\n", encoding="utf-8")
    (proj / "labbroken.py").write_text(
        "import lab_typo_helper  # noqa: F401 本地拼错的模块名\n", encoding="utf-8"
    )
    base_env = {"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(d / "mpl"), "MPLBACKEND": "Agg"}
    for name, body in SCRIPTS.items():
        (proj / f"{name}.py").write_text(HEAD + body + TAIL.format(name=name), encoding="utf-8")
        subprocess.run(
            [py, f"{name}.py"],
            cwd=proj,
            check=True,
            timeout=300,
            env={**base_env, "QA_REFERENCE": "1"},
        )
    native = {}
    for name in SCRIPTS:
        r = subprocess.run(
            [py, f"{name}.py"], cwd=proj, capture_output=True, text=True, timeout=300, env=base_env
        )
        native[name] = {
            "rc": r.returncode,
            "last": (r.stderr.strip().splitlines() or [""])[-1][:300],
        }
    EVIDENCE["native_terminal"] = native
    before = {"dists": p.dists(py), "files": p.tree_digest(proj / ".venv")}
    results = {}
    with p.app_for(proj, d / "work", {}) as app:
        for name in SCRIPTS:
            panel = p.panel_of(app, f"{name}.pdf")
            st = p.prepare(app, panel["id"])
            entry = {
                "prepare": st["result"]["status"],
                "prepare_error": (st["result"].get("error") or {}).get("code"),
                "dependency_preparation": st["plan"].get("dependency_preparation"),
            }
            try:
                r = p.render(app, panel["id"])
                entry["render"] = {"ok": True, "values": p.values_from(r)}
            except p.fa.HttpError as exc:
                b = exc.body
                entry["render"] = {
                    "ok": False,
                    "status": exc.status,
                    "code": b.get("code"),
                    "module": b.get("module"),
                    "project_env": b.get("project_env"),
                    "params": b.get("params"),
                    "repair_keys": sorted(k for k in b if "repair" in k or "offer" in k),
                    "dependency_repair": b.get("dependency_repair"),
                    "message": str(b.get("error") or "")[:300],
                }
            try:
                _, dep = app.call(f"/api/engine/dependencies?script={name}.py", timeout=120)
                entry["dependencies"] = json.loads(json.dumps(dep, default=str))
            except p.fa.HttpError as exc:
                entry["dependencies"] = {"http": exc.status, "code": exc.body.get("code")}
            results[name] = entry
            log(f"{name}: {json.dumps(entry['render'], ensure_ascii=False, default=str)[:500]}")
    EVIDENCE["results"] = results
    after = {"dists": p.dists(py), "files": p.tree_digest(proj / ".venv")}
    check(
        "副作用：项目 venv 包清单不变（没有盲目安装）",
        before["dists"] == after["dists"],
        sorted(set(after["dists"]) ^ set(before["dists"])),
    )
    check(
        "本地模块 labmod：自动成功且值 = [0, 9, 2, 10]",
        results["fig_local"]["render"].get("values") == [0, 9, 2, 10],
        results["fig_local"]["render"],
    )
    check(
        "可选 / 动态 import：自动成功",
        results["fig_optional"]["render"].get("values") == [0, 9, 2, 10],
        results["fig_optional"]["render"],
    )
    for name, what in (
        ("fig_broken_internal", "包内部 ImportError（cannot import name）"),
        ("fig_abi", "二进制加载失败（dlopen）"),
        ("fig_broken_submodule", "已安装包内部缺子模块（No module named 'pkg._sub'）"),
    ):
        rr = results[name]["render"]
        check(f"{what}：渲染失败（与终端一致）", rr.get("ok") is False, rr.get("code"))
        check(
            f"{what}：不被归为「缺包」missing_dependency（包已安装）",
            rr.get("code") != "missing_dependency",
            {"code": rr.get("code"), "module": rr.get("module")},
        )
    # 真的不存在的顶层名字（本地模块里拼错）：归「缺」可以接受，但不许被当成可装的 PyPI 包
    rr = results["fig_local_typo"]["render"]
    check("本地拼错的模块名：渲染失败（与终端一致）", rr.get("ok") is False, rr.get("code"))
    req = (rr.get("dependency_repair") or {}).get("requirement") or {}
    check("本地拼错的模块名：没有被解析成可安装的包（不盲目安装）", not req.get("installable"), req)
    plan_possible = [
        c
        for c in (
            ((results["fig_local_typo"].get("dependencies") or {}).get("offer") or {}).get("plan")
            or {}
        ).get("possible", [])
    ]
    check(
        "静态计划：lab_typo_helper 归 unknown、needed=false（不猜）",
        any(
            c.get("module") == "lab_typo_helper"
            and c.get("bucket") == "unknown"
            and c.get("needed") is False
            for c in plan_possible
        ),
        plan_possible,
    )
    local_scan = (
        (((results["fig_local"].get("dependencies") or {}).get("offer") or {}).get("plan") or {})
        .get("scan", {})
        .get("classes", [])
    )
    check(
        "静态计划：labmod 归 local、needed=false（本地模块不当 PyPI 包）",
        any(
            c.get("module") == "labmod" and c.get("bucket") == "local" and c.get("needed") is False
            for c in local_scan
        ),
        [c for c in local_scan if c.get("module") == "labmod"],
    )


# ================================================================ ENV-06


def scenario_env06(e1: Path, out: Path) -> None:
    """已有完整项目环境断网运行。注入点 = 服务进程环境：代理变量指向关闭的端口 127.0.0.1:9、
    `PIP_NO_INDEX=1`、`UV_OFFLINE=1`（经 urllib / pip / uv 的出网都会失败），本机回环 NO_PROXY。"""
    dead = "http://127.0.0.1:9"
    env = {
        "HTTP_PROXY": dead,
        "HTTPS_PROXY": dead,
        "ALL_PROXY": dead,
        "http_proxy": dead,
        "https_proxy": dead,
        "all_proxy": dead,
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "PIP_NO_INDEX": "1",
        "UV_OFFLINE": "1",
    }
    EVIDENCE["injection"] = env
    rec = p.load_e1(e1)
    proj = e1 / "proj"
    p.reset_project_state(proj)
    p.native_reference(rec["pythons"]["A"], proj, out)
    venvs = {"A": proj / ".venv"}
    before = p.snapshot("before", venvs)
    work = out / "ENV-06" / "work"
    shutil.rmtree(work, ignore_errors=True)
    with p.app_for(proj, work, env) as app:
        panel = p.panel_of(app, "figure.pdf")
        st = p.prepare(app, panel["id"])
        check(
            "断网：prepare ready（已齐依赖无需网络）",
            st["result"]["status"] == "ready",
            st["result"].get("error"),
        )
        EVIDENCE["dependency_preparation"] = st["plan"].get("dependency_preparation")
        r = p.render(app, panel["id"])
        p.assert_identity_is(
            "断网", p.ident_from(r), p.runtime_of(st["result"]), rec["envs"]["A"], p.values_from(r)
        )
        ex = p.export_pdf(app, panel["id"], None)
        check("断网：导出 done", ex.get("status") == "done", ex.get("status"))
        log_text = app.server_log()
    (out / "ENV-06" / "server.log").write_text(log_text, encoding="utf-8")
    check(
        "服务日志里没有 pip / uv 安装调用",
        not any(t in log_text for t in ("pip install", "uv pip")),
    )
    after = p.snapshot("after", venvs)
    dd = p.diff_snapshots(before, after)
    EVIDENCE["side_effects"] = dd
    check("断网：用户 venv 未被安装 / 升级 / 删除", not any(dd["A"].values()), dd["A"])


# ================================================================ ENV-07


def scenario_env07(e1: Path, out: Path) -> None:
    """首开 → 导出 → 关服务 → 清运行缓存 → 重启（同一用户配置）→ 选择仍可解释、结果重现。"""
    rec = p.load_e1(e1)
    A = rec["envs"]["A"]
    proj = e1 / "proj"
    p.reset_project_state(proj)
    p.native_reference(rec["pythons"]["A"], proj, out)
    work = out / "ENV-07" / "work"
    shutil.rmtree(work, ignore_errors=True)
    runs = []
    for round_ in (1, 2):
        with p.app_for(proj, work, {}) as app:
            envst = project_env_state(app)
            panel = p.panel_of(app, "figure.pdf")
            st = p.prepare(app, panel["id"])
            r = p.render(app, panel["id"])
            ex = p.export_pdf(app, panel["id"], None)
            text = (
                p.pdf_text(Path(ex["export_dir"]) / ex["outputs"][0]["name"])
                if ex.get("status") == "done"
                else ""
            )
            rc = st["result"].get("receipt") or {}
            runs.append(
                {
                    "round": round_,
                    "env_state_before_prepare": {
                        k: envst.get(k) for k in ("source", "automatic", "trigger", "source_label")
                    },
                    "plan_env": {
                        k: st["plan"]["environment"].get(k)
                        for k in (
                            "source",
                            "trigger",
                            "automatic",
                            "python_version",
                            "matplotlib_version",
                            "support",
                        )
                    },
                    "receipt": {
                        k: rc.get(k)
                        for k in (
                            "python_source",
                            "generation",
                            "pid_check",
                            "completeness",
                            "public_identity",
                            "source_revision",
                        )
                    },
                    "values": p.values_from(r),
                    "ident": p.ident_from(r),
                    "export_has_A_values": ("QA-E1 " + json.dumps(A["value"])) in text,
                    "export_semantic": ex["outputs"][0]["manifest"]["identity"]["semantic"]
                    if ex.get("status") == "done"
                    else None,
                }
            )
        if round_ == 1:
            data = work / "data"
            removed = []
            for child in sorted(data.iterdir()):
                if "session" in child.name or "credential" in child.name or "auth" in child.name:
                    continue
                removed.append(child.name)
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            EVIDENCE["cache_removed"] = removed
    EVIDENCE["runs"] = runs
    r1, r2 = runs
    check(
        "第 1 轮：首开后 project_venv / first_open",
        r1["plan_env"]["source"] == "project_venv" and r1["plan_env"]["trigger"] == "first_open",
        r1["plan_env"],
    )
    check(
        "重启后：prepare 之前的环境状态已是记住的 project_venv（automatic, first_open）",
        r2["env_state_before_prepare"].get("source") == "project_venv"
        and r2["env_state_before_prepare"].get("automatic") is True
        and r2["env_state_before_prepare"].get("trigger") == "first_open",
        r2["env_state_before_prepare"],
    )
    check(
        "重启后：计划的选择原因仍可解释（source / trigger）",
        r2["plan_env"]["source"] == "project_venv" and r2["plan_env"]["trigger"] == "first_open",
        r2["plan_env"],
    )
    check(
        "回执带选择来源 python_source",
        r2["receipt"].get("python_source") == "project_venv",
        r2["receipt"],
    )
    for r in runs:
        p.assert_identity_is(f"第 {r['round']} 轮", r["ident"], None, A, r["values"])
        check(f"第 {r['round']} 轮导出含 A 的点序列", r["export_has_A_values"])
    check(
        "两轮导出语义身份一致（结果重现）",
        r1["export_semantic"] is not None and r1["export_semantic"] == r2["export_semantic"],
        [r1["export_semantic"], r2["export_semantic"]],
    )
    check(
        "回执公开身份两轮一致",
        r1["receipt"].get("public_identity") == r2["receipt"].get("public_identity"),
        [r1["receipt"].get("public_identity"), r2["receipt"].get("public_identity")],
    )


p.SCENARIOS.update(
    {
        "ENV-04": scenario_env04,
        "ENV-05": scenario_env05,
        "ENV-06": scenario_env06,
        "ENV-07": scenario_env07,
    }
)

if __name__ == "__main__":
    sys.exit(p.main())
