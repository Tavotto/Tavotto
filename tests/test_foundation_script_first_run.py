"""T11：真实 script-only 首跑资格（C23 / C24 / C27 / A02 / I01 / I02 / P01 / R02 / R03，经**真实公共入口**）。

FirstOpenBench 既有的 FO 用例都先在**待测目录里**跑一遍原生脚本生成原件（`_native_reference(proj, …)`），再从
「已知图」入口进——那是首开，不是首跑（T00 指出不能当 C23 证据）。这里补上不预热的那一条：

* **待测项目的起点**只有脚本 / 数据 / 依赖声明（夹具 ⑪ `script_only_first_run`，复制时排除 `truth.json`）：没有图、
  没有注册表、没有人工写入的任何 Tavotto 状态；起点的文件清单与 sha256 写进结果记录；
* **原生参考**在**另一个**临时根（同名目录、同样的相对布局）里跑，stdin 答菜单；它的执行日志在它自己的父目录；
* 被测路径只用产品的正式入口：`python -m tavotto --figures <项目>`（会话认证、空配置、没有 `TAVOTTO_WORKER_PYTHON`）→
  导入即扫描（零执行）→ 扫描给出的 `session_target` + 精确 argv 建准备会话（零执行）→ 运行目录待办 → 用户在确认框里
  选项目根（`PATCH /api/engine/workdir`，与界面同一条路）→ 重新检查 → 认领后端生成的 `run` 动作 → 运行中真 `input()`
  经事件流 + `/api/script_input/answer` 作答（界面答题的同一条路）→ 已捕获 → 用这次捕获的图进编辑（不重跑）→ 改一个真实
  元素 → 保存排版到项目（⌘S 同一端点）→ 强制冷重放 → 导出 → 关掉应用再开、读回排版、按保存的编辑重放；
* 真值一律来自**被执行的那个进程自己**写的项目外日志（解释器 / prefix / argv / cwd / 菜单 / 选项 / 画出的 y），与原生
  参考逐值对拍；图里的数据范围（ylim）由这份 y 推出。请求回显、toast、store 里的期望值都不算证据。

分段耗时（扫描 / 检查 / 用户等待 / 科学计算 / 首次渲染）写进结果记录的 `observed.timings`——**本地合成夹具上的度量**，
不是产品遥测，不上报。
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from support import foundation_app as fa, foundation_harness as fh
from tavotto.engine import figcapture, pool as _pool

pytest_plugins = (
    "support.dependency_repair",
)  # offline_managed_env：离线建受管环境（与 T06 用例同一个夹具）

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "script_only_first_run"
CASE_ID = "T11-S1"
SCRIPT = "tools/spectrum.py"
STEM = "spectrum"
PROJECT_NAME = "我的 项目"  # 中文 + 空格路径
SESSIONS = "/api/engine/preparation-sessions"
ENV = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}

# 两份配置：同一个图名、不同参数；A02 的 token 形状（中文 / 空格 / 引号 / 空串 / 负数）都在里面
ARGV_A = ["--scale", "1.5", "--label", "峰 值 A", "--offset", "-0.5", "--tag", ""]
ARGV_B = ["--scale", "3", "--label", 'B "quoted"', "--tag", "第二组"]
ANSWER_A = "1"  # smooth（两项菜单）
ANSWER_B = "2"  # log（只在三项菜单里存在）
LAYOUT_URL = urllib.parse.quote("T11 首跑")

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001 — 没有科学栈就 skip，而 skip 在 CI 校验步里是红
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


# ---------------------------------------------------------------- 夹具与独立观测


def _copy_project(dest_parent: Path) -> Path:
    proj = dest_parent / PROJECT_NAME
    shutil.copytree(FIXTURE, proj, ignore=shutil.ignore_patterns("truth.json"))
    return proj


def _snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _log_path(proj: Path) -> Path:
    return proj.parent / "t11_exec_log.jsonl"


def _runs(proj: Path) -> list[dict]:
    path = _log_path(proj)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line]


def _native(proj: Path, argv: list[str], answer: str, tmp: Path) -> dict:
    """原生参考：用户自己在终端里，站在项目根跑一次（stdin 答菜单）。**不是**产品路径，项目也不是被测那一份。"""
    env = {"PATH": "/usr/bin:/bin", "MPLCONFIGDIR": str(tmp / "mpl"), "MPLBACKEND": "Agg"}
    for name in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    before = len(_runs(proj))
    proc = subprocess.run(
        [WORKER_PY, SCRIPT, *argv],
        cwd=proj,
        input=answer + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    runs = _runs(proj)
    assert len(runs) == before + 1
    return runs[-1]


class Answerer:
    """界面答题的同一条路：带 `answers=1` 的事件流 + `/api/script_input/listen` 报「我在看这个项目」，
    收到 `script.input_requested` 后 `POST /api/script_input/answer`。"""

    def __init__(self, app: fa.RunningApp):
        self.app = app
        req = urllib.request.Request(app.base + "/api/events?answers=1", headers=dict(app.auth))
        self.resp = urllib.request.urlopen(req, timeout=900)
        self.events: queue.Queue = queue.Queue()
        self.seen: list[tuple[str, dict]] = []
        self.stream_id: str | None = None
        self._hello = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()
        assert self._hello.wait(30), "事件流没有 stream.hello"
        status, _ = app.call("/api/script_input/listen", {"stream_id": self.stream_id})
        assert status == 200

    def _pump(self) -> None:
        event = None
        try:
            for raw in self.resp:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("event: "):
                    event = line[len("event: ") :]
                elif line.startswith("data: ") and event:
                    data = json.loads(line[len("data: ") :])
                    if event == "stream.hello":
                        self.stream_id = data["stream_id"]
                        self._hello.set()
                    else:
                        self.seen.append((event, data))
                        self.events.put((event, data))
                elif not line:
                    event = None
        except Exception:  # noqa: BLE001 — 关流时读线程自然结束
            pass

    def questions(self) -> list[dict]:
        return [d for e, d in self.seen if e == "script.input_requested"]

    def answer_next(self, answer: str, *, timeout: float = 300.0) -> tuple[dict, float]:
        """等下一问并作答，回 `(这一问, 从发问到收到回答的等待秒数)`。"""
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            assert left > 0, "没有等到 script.input_requested"
            event, data = self.events.get(timeout=left)
            if event == "script.input_requested":
                break
        asked = time.monotonic()
        status, body = self.app.call(
            "/api/script_input/answer", {"id": data["id"], "answer": answer}
        )
        assert status == 200 and body.get("ok"), body
        return data, time.monotonic() - asked

    def close(self) -> None:
        try:
            self.resp.close()
        except Exception:  # noqa: BLE001
            pass


def _call(app: fa.RunningApp, path: str, payload=None, **kw):
    try:
        return app.call(path, payload, **kw)
    except fa.HttpError as exc:
        return exc.status, exc.body


def _wait_report(app, sid: str, phases, *, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while True:
        status, report = app.call(f"{SESSIONS}/{sid}", timeout=30)
        assert status == 200, report
        if report["phase"] in phases:
            return report
        assert time.time() < deadline, json.dumps(report, ensure_ascii=False)[:2000]
        time.sleep(0.1)


def _act(app, report: dict, kind: str):
    (action,) = [a for a in report["actions"] if a["kind"] == kind]
    return _call(
        app,
        f"{SESSIONS}/{report['session_id']}/actions",
        {"action_id": action["id"], "expected_config_revision": report["config_revision"]},
        timeout=60,
    )


def _axes(render: dict) -> dict:
    return next(e for e in render["manifest"]["elements"] if e["role"] == "axes")


def _ylim(render: dict) -> list[float]:
    return next(f["value"] for f in _axes(render)["editable"] if f["prop"] == "ylim")


def _title(render: dict) -> dict:
    return next(e for e in render["manifest"]["elements"] if e["role"] == "title")


def _title_text(render: dict) -> str:
    return next(f["value"] for f in _title(render)["editable"] if f["prop"] == "text")


def _geometry(render: dict) -> list[tuple]:
    """几何指纹：每个元素的 (gid, role, bbox)——热态 / 冷重放 / 重开三份逐元素对拍。"""
    out = []
    for e in render["manifest"]["elements"]:
        box = e.get("bbox")
        out.append((e["gid"], e["role"], tuple(round(float(v), 3) for v in box) if box else None))
    return out


def _expected_ylim(ys: list[float]) -> list[float]:
    margin = 0.05 * (max(ys) - min(ys))
    return [min(ys) - margin, max(ys) + margin]


def _run_config(report: dict) -> str:
    rc = report["target"].get("run_config")
    assert rc, report["target"]
    return rc


def _record(binding: dict, observed: dict, evidence: list[str], out_dir: Path) -> None:
    record = fh.ResultRecord(
        case_id=CASE_ID,
        binding=binding,
        product_outcome="guided",
        test_verdict="pass",
        observed=observed,
        evidence=tuple(evidence),
    )
    assert fh.write_result(record, fh.results_dir() or out_dir).is_file()


# ================================================================ T11-S1


@needs_worker
def test_t11_s1_script_only_first_run_through_the_public_entry(tmp_path):
    ledger = fh.load_ledger()
    case = next(c for c in ledger["cases"] if c["case_id"] == CASE_ID)
    binding = fh.binding_from_environment(
        ledger=ledger, entry=case["entry"], fixture=case["fixture"]
    )
    truth = json.loads((FIXTURE / "truth.json").read_text("utf-8"))
    evidence: list[str] = []
    timings: dict[str, float] = {}

    # ---- 原生参考：另一个根，两份配置各跑一次
    ref = _copy_project(tmp_path / "ref")
    native_a = _native(ref, ARGV_A, ANSWER_A, tmp_path)
    native_b = _native(ref, ARGV_B, ANSWER_B, tmp_path)
    assert native_a["menu"] == ["raw", "smooth"] and native_a["mode"] == "smooth"
    assert native_b["menu"] == ["raw", "smooth", "log"] and native_b["mode"] == "log"
    assert native_a["y"] != native_b["y"]

    # ---- 待测项目：起点只有脚本 / 数据 / 依赖声明（C23 前置断言）
    proj = _copy_project(tmp_path / "test")
    start = _snapshot(proj)
    assert sorted(start) == truth["initial_files"]
    assert _runs(proj) == []
    work = tmp_path / "work"

    with fa.running_app(proj, work, env_overrides=ENV) as app:
        after_open = _snapshot(proj)
        # 打开项目不生成任何图、一行用户代码都不跑。既有的打开行为会写 `tavotto_registry.json`（T00 裁决：
        # deliberate-boundary），内容只能是静态扫描读得出的东西（字面量 savefig 的图名），不是执行结果：
        # 没有运行配置、没有捕获描述——如实记进结果记录
        assert not [p for p in after_open if p.endswith((".pdf", ".png", ".svg"))]
        assert {p: h for p, h in after_open.items() if p in start} == start
        assert set(after_open) - set(start) <= {"tavotto_registry.json"}
        assert _runs(proj) == []
        registry_at_open = None
        if "tavotto_registry.json" in after_open:
            registry_at_open = json.loads((proj / "tavotto_registry.json").read_text("utf-8"))
            assert set(registry_at_open["scripts"]) == {SCRIPT}
            assert set(registry_at_open["scripts"][SCRIPT]) <= {"entry", "cost", "notes", "stems"}
        _, panels = app.call("/api/panels", timeout=30)
        panels_at_open = [
            (p["id"], p.get("capability", {}).get("status")) for p in panels["panels"]
        ]
        assert not [
            p for p in panels["panels"] if p.get("capability", {}).get("status") == "editable"
        ]

        # ---- 导入即扫描：零执行、不写项目
        t0 = time.monotonic()
        status, scan = app.call("/api/project/scan", {"reason": "claim"}, timeout=60)
        assert status in (200, 202), scan
        deadline = time.time() + 120
        while scan["state"] == "running":
            assert time.time() < deadline, scan
            time.sleep(0.1)
            _, scan = app.call("/api/project/scan", timeout=30)
        timings["scan_s"] = time.monotonic() - t0
        assert scan["state"] in ("complete", "partial"), scan
        (target,) = [t for t in scan["targets"] if t["script"] == SCRIPT]
        assert _runs(proj) == [] and _snapshot(proj) == after_open

        # ---- 准备会话（配置 A）：检查零执行；数据只在项目根找得到 → 运行目录待办
        t0 = time.monotonic()
        status, report = app.call(
            SESSIONS, {**target["session_target"], "argv": ARGV_A}, timeout=120
        )
        timings["check_s"] = time.monotonic() - t0
        assert status == 201, report
        assert report["target"]["kind"] == "script" and report["target"]["script"] == SCRIPT
        assert report["target"]["argv_count"] == len(ARGV_A)
        assert "峰 值" not in json.dumps(report, ensure_ascii=False)  # 公开报告只带个数与引用
        assert report["phase"] == "awaiting_configuration", report
        assert "run" not in [a["kind"] for a in report["actions"]]
        assert _runs(proj) == [] and _snapshot(proj) == after_open
        rc_a = _run_config(report)

        # 用户在确认框里选「项目根」（与界面同一个端点）→ 重新检查 → 可以运行；仍然零执行
        t0 = time.monotonic()
        _, patched = app.call("/api/engine/workdir", {"mode": "project_root"}, method="PATCH")
        assert patched["workdir"]["mode"] == "project_root"
        status, rechecked = _act(app, report, "recheck")
        assert status == 200, rechecked
        report = _wait_report(app, report["session_id"], ("ready_to_run",), timeout=60)
        timings["user_config_s"] = time.monotonic() - t0
        assert _runs(proj) == []

        answerer = Answerer(app)
        try:
            # ---- 认领 run：运行中问菜单，同一进程里答；执行恰好 1 次
            t0 = time.monotonic()
            status, claimed = _act(app, report, "run")
            assert status == 202 and claimed["claimed"] is True, claimed
            asked_a, waited = answerer.answer_next(ANSWER_A)
            done_a = _wait_report(
                app, report["session_id"], ("completed", "partial", "action_required")
            )
            timings["run_total_s"] = time.monotonic() - t0
            timings["input_wait_s"] = waited
            timings["compute_s"] = timings["run_total_s"] - waited
            assert done_a["phase"] == "completed", done_a
            assert done_a["facts"] == {"execution_finished": True, "figure_captured": True}
            assert asked_a["prompt"] == "mode? " and asked_a["secret"] is False
            assert "1) smooth" in (asked_a.get("stdout_tail") or "")
            runs = _runs(proj)
            assert len(runs) == 1
            got_a = runs[0]
            # 真 worker 看到的：精确 token（中文 / 空格 / 空串 / 负数原样）、cwd = 项目根、菜单与选项、数组 = 原生参考
            assert got_a["argv"] == ARGV_A == native_a["argv"]
            assert Path(got_a["cwd"]).resolve() == proj.resolve()
            assert Path(native_a["cwd"]).resolve() == ref.resolve()
            assert (got_a["menu"], got_a["mode"]) == (native_a["menu"], native_a["mode"])
            assert got_a["y"] == native_a["y"]
            (captured,) = done_a["captured"]
            asset_a = captured["asset_id"]
            assert asset_a == figcapture.runtime_asset_id(SCRIPT, STEM, rc_a)

            # 解释器：执行日志里的 prefix = 产品声明的那个解释器的独立探针
            _, envst = app.call("/api/engine/environment", timeout=60)
            chosen = (envst.get("project") or {}).get("python") or ""
            chosen_path = chosen if Path(chosen).is_absolute() else str(proj / chosen)
            ident = fa.independent_identity(chosen_path) if chosen else None
            if ident is not None:
                assert os.path.realpath(got_a["prefix"]) == os.path.realpath(ident["prefix"])

            # ---- 进入编辑：同一次捕获，不重跑（C27）；图里的数据范围 = 这次执行画出的 y
            t0 = time.monotonic()
            hot_a = app.render(asset_a)
            timings["first_render_s"] = time.monotonic() - t0
            assert _ylim(hot_a) == pytest.approx(_expected_ylim(got_a["y"]), abs=1e-6)
            assert _title_text(hot_a) == "峰 值 A [smooth]"  # 空串 tag 不进标题：脚本自己的语义
            assert len(_runs(proj)) == 1
            evidence.append(
                f"A: scan+check zero-exec; workdir guided; 1 exec argv={ARGV_A!r} cwd=root mode=smooth; "
                f"y == native; ylim {_ylim(hot_a)}; edit entry reused capture (exec count 1)"
            )

            # ---- 配置 B：同脚本、同图名、不同参数；菜单换成三项，换一个选项（旧答案不被复用）
            status, rep_b = app.call(
                SESSIONS, {**target["session_target"], "argv": ARGV_B}, timeout=120
            )
            assert status == 201 and rep_b["session_id"] != report["session_id"], rep_b
            assert rep_b["phase"] == "ready_to_run", rep_b  # 运行目录是项目级决定，已经答过
            rc_b = _run_config(rep_b)
            assert rc_b != rc_a
            status, _ = _act(app, rep_b, "run")
            assert status == 202
            asked_b, _ = answerer.answer_next(ANSWER_B)
            assert "2) log" in (asked_b.get("stdout_tail") or "")
            done_b = _wait_report(
                app, rep_b["session_id"], ("completed", "partial", "action_required")
            )
            assert done_b["phase"] == "completed", done_b
            runs = _runs(proj)
            assert len(runs) == 2
            got_b = runs[1]
            assert (
                got_b["argv"] == ARGV_B and got_b["mode"] == "log" and got_b["y"] == native_b["y"]
            )
            (cap_b,) = done_b["captured"]
            asset_b = cap_b["asset_id"]
            assert asset_b != asset_a and cap_b["stem"] == captured["stem"] == STEM

            # 交替编辑：热态各是各的数据，不串（R02 / C19）
            for asset, ys in [(asset_b, got_b["y"]), (asset_a, got_a["y"]), (asset_b, got_b["y"])]:
                assert _ylim(app.render(asset)) == pytest.approx(_expected_ylim(ys), abs=1e-6)
            assert len(_runs(proj)) == 2

            # ---- 改一个真实元素（A 的标题）→ 读回
            patch = {"gid": _title(hot_a)["gid"], "prop": "text", "value": "T11 已编辑"}
            edited = app.render(asset_a, [patch])
            assert _title_text(edited) == "T11 已编辑"
            assert _ylim(edited) == pytest.approx(_ylim(hot_a))  # 数据不变
            geometry_hot = _geometry(edited)

            # ---- 保存排版（⌘S 同一端点，写进项目的 tavottofile/）
            doc = {
                "schema": 3,
                "activeCanvasId": "c1",
                "canvases": [
                    {
                        "id": "c1",
                        "name": "T11",
                        "objects": [
                            {
                                "type": "panel",
                                "id": "panel-a",
                                "fileId": asset_a,
                                "fileKind": "runtime",
                                "overrides": [patch],
                            },
                            {
                                "type": "panel",
                                "id": "panel-b",
                                "fileId": asset_b,
                                "fileKind": "runtime",
                                "overrides": [],
                            },
                        ],
                    }
                ],
            }
            status, saved = app.call(f"/api/layouts/{LAYOUT_URL}?target=project", doc)
            assert status == 200 and saved["ok"], saved
            layout_file = saved["file"]

            # ---- 强制冷重放：只重建 A，用 A 冻结的 argv / cwd / 那一次执行的回答转录；不再问
            asked_before = len(answerer.questions())
            status, _ = app.call("/api/engine/invalidate", {"id": asset_a}, timeout=30)
            assert status == 200
            cold = app.render(asset_a, [patch])
            runs = _runs(proj)
            assert len(runs) == 3 and runs[2]["argv"] == ARGV_A and runs[2]["mode"] == "smooth"
            assert runs[2]["y"] == got_a["y"] and Path(runs[2]["cwd"]).resolve() == proj.resolve()
            assert len(answerer.questions()) == asked_before  # 回答来自转录，没有再问
            assert _title_text(cold) == "T11 已编辑"
            assert _geometry(cold) == geometry_hot
            # B 的热会话没被连带打断
            assert _ylim(app.render(asset_b)) == pytest.approx(_expected_ylim(got_b["y"]), abs=1e-6)
            assert len(_runs(proj)) == 3

            # ---- 导出（原图范围、带编辑）：冷的话按 A 自己的配置重放
            status, _ = app.call("/api/engine/invalidate", {"id": asset_a}, timeout=30)
            before_export = len(_runs(proj))
            status, exported = _call(
                app,
                "/api/export",
                {
                    "scope": "original",
                    "filename": "t11-a",
                    "formats": ["pdf"],
                    "original": {
                        "figure_id": asset_a,
                        "source_kind": "vector",
                        "overrides": [patch],
                    },
                },
                timeout=300,
            )
            assert status == 200 and exported["status"] == "done", exported
            assert [r["argv"] for r in _runs(proj)[before_export:]] == [ARGV_A]
            assert len(answerer.questions()) == asked_before
            pdfs = sorted(proj.rglob("t11-a.pdf"))
            assert pdfs and pdfs[0].read_bytes()[:5] == b"%PDF-"
        finally:
            answerer.close()

    # ---- 关掉再开：同一数据目录；读回排版，按保存的编辑重放；不接答题界面（回答只能来自转录）
    before_reopen = len(_runs(proj))
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        status, back = app.call(f"/api/layouts/{LAYOUT_URL}", timeout=30)
        assert status == 200
        objects = {o["id"]: o for o in back["canvases"][0]["objects"]}
        assert objects["panel-a"]["fileId"] == asset_a and objects["panel-a"]["overrides"] == [
            patch
        ]
        reopened_a = app.render(objects["panel-a"]["fileId"], objects["panel-a"]["overrides"])
        reopened_b = app.render(objects["panel-b"]["fileId"], objects["panel-b"]["overrides"])
        assert _title_text(reopened_a) == "T11 已编辑"
        assert _ylim(reopened_a) == pytest.approx(_expected_ylim(got_a["y"]), abs=1e-6)
        assert _ylim(reopened_b) == pytest.approx(_expected_ylim(got_b["y"]), abs=1e-6)
        assert _geometry(reopened_a) == geometry_hot
    cold_runs = _runs(proj)[before_reopen:]
    assert sorted((tuple(r["argv"]), r["mode"]) for r in cold_runs) == sorted(
        [(tuple(ARGV_A), "smooth"), (tuple(ARGV_B), "log")]
    )
    for r in cold_runs:
        assert r["y"] == (got_a["y"] if r["argv"] == ARGV_A else got_b["y"])
    evidence.append(
        f"B: same stem, own rc, menu 3 items answered '2' (log), y == native; alternate edits no mix; "
        f"title edited; layout saved to {layout_file}; cold replay A used frozen argv + transcript (no question); "
        f"export ran A's argv; reopen in new process replayed A and B from the saved layout without asking"
    )

    final_tree = _snapshot(proj)
    # 原起点文件逐字节未变（用户的脚本 / 数据 / 依赖声明一个字节都没动）
    assert {p: final_tree[p] for p in start} == start
    observed = {
        "backend": case.get("backend"),
        "start_files": start,
        "registry_written_by_open": registry_at_open,
        "panels_at_open": panels_at_open,
        "exec_count": len(_runs(proj)),
        "exec_records": [
            {k: r[k] for k in ("argv", "mode", "menu")}
            | {"cwd_is_project_root": Path(r["cwd"]).resolve() == proj.resolve()}
            for r in _runs(proj)
        ],
        "interpreter_prefix_matches_product_choice": ident is not None,
        "native_reference_root_is_separate": ref.resolve() != proj.resolve(),
        "y_equals_native": {"A": got_a["y"] == native_a["y"], "B": got_b["y"] == native_b["y"]},
        "asset_ids_distinct": asset_a != asset_b,
        "layout_file": layout_file,
        "new_project_files": sorted(set(final_tree) - set(start)),
        # T08 留给 T12 复核的事（回答转录与上下文摘要写在哪）：如实记下这份合成夹具的项目文件里有什么
        "script_inputs_file": (
            json.loads((proj / "tavottofile" / "_script_inputs.json").read_text("utf-8"))
            if (proj / "tavottofile" / "_script_inputs.json").is_file()
            else None
        ),
        "timings": {k: round(v, 3) for k, v in timings.items()},
        "timings_note": "本地合成夹具上的分段耗时；用户等待由自动化即时作答，接近 0（不代表真实用户）",
    }
    _record(binding, observed, evidence, tmp_path / "results")


# ================================================================ T11-S2：缺依赖的 script-only 首跑（离线本地 wheel）

DEP = ("tavotto-test-alpha", "tavotto_test_alpha")
DEP_SCRIPT = '''\
"""T11-S2：只有脚本 + 依赖声明；脚本要的包只存在于用例现造的离线 wheelhouse 里。"""
import json, os, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import tavotto_test_alpha as alpha

here = Path(__file__).resolve().parent
ys = [alpha.VALUE * k for k in (1, 2, 3)]
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], ys)
fig.savefig("fit.pdf")
with open(here.parent.parent / "t11_exec_log.jsonl", "a", encoding="utf-8") as out:
    out.write(json.dumps({"executable": sys.executable, "prefix": sys.prefix, "argv": sys.argv[1:],
                          "cwd": os.getcwd(), "y": ys, "module": alpha.__file__}) + "\\n")
'''


@pytest.fixture
def dep_house(tmp_path, monkeypatch) -> Path:
    from support.dependency_repair import build_wheel

    house = tmp_path / "house"
    build_wheel(house, name=DEP[0], import_name=DEP[1], version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(house))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return house


def _dep_project(parent: Path) -> Path:
    proj = parent / PROJECT_NAME
    (proj / "tools").mkdir(parents=True)
    (proj / "tools" / "fit.py").write_text(DEP_SCRIPT, encoding="utf-8")
    (proj / "requirements.txt").write_text(f"{DEP[0]}\n", encoding="utf-8")
    return proj


@needs_worker
def test_t11_s2_script_only_first_run_with_a_missing_package_from_a_local_wheel(
    tmp_path, dep_house, offline_managed_env, monkeypatch
):
    """C23 + D01 + C30 + C24（进程内 HTTP、真 pip、真 worker）：只有脚本与依赖声明的项目，缺的包只在离线 wheelhouse 里。
    原生参考在另一个根（模块经 `PYTHONPATH` 提供，与 wheel 里的模块体逐字相同，不装任何东西）；被测项目从未先跑。
    检查零执行 → 待办是一份依赖计划（一次确认）→ 装进 Tavotto 自己的受管环境的新一代（用户环境一个字节不动）→
    同一会话重新检查 → 运行一次 → 捕获 → 进编辑不重跑；执行日志里的 prefix 就是那一代受管环境。"""
    from tavotto import app as m
    from tavotto.engine import deprepair, managedenv, prepsession

    deprepair.reset_state()
    prepsession.SESSIONS.reset_for_tests()
    # 原生参考
    ref = _dep_project(tmp_path / "ref")
    mods = tmp_path / "native-mods"
    mods.mkdir()
    (mods / f"{DEP[1]}.py").write_text(f'VALUE = 42\nNAME = "{DEP[1]}"\n', encoding="utf-8")
    proc = subprocess.run(
        [WORKER_PY, "tools/fit.py"],
        cwd=ref,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(mods),
            "MPLBACKEND": "Agg",
            "MPLCONFIGDIR": str(tmp_path / "mpl"),
        },
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    (native,) = _runs(ref)

    proj = _dep_project(tmp_path / "test")
    start = _snapshot(proj)
    m.app.config["TESTING"] = True
    client = m.app.test_client()
    pid = client.post("/api/projects/open", json={"path": str(proj)}).get_json()["id"]
    try:
        scan = client.post(f"/api/project/scan?pj={pid}", json={"reason": "claim"}).get_json()
        deadline = time.time() + 60
        while scan["state"] == "running":
            assert time.time() < deadline
            time.sleep(0.1)
            scan = client.get(f"/api/project/scan?pj={pid}").get_json()
        (target,) = [t for t in scan["targets"] if t["script"] == "tools/fit.py"]
        resp = client.post(f"{SESSIONS}?pj={pid}", json=target["session_target"])
        assert resp.status_code == 201, resp.get_json()
        report = resp.get_json()
        assert report["phase"] == "awaiting_confirmation", report
        kinds = sorted(a["kind"] for a in report["actions"])
        assert "run" not in kinds and "prepare_dependencies" in kinds
        (dep_action,) = [a for a in report["actions"] if a["kind"] == "prepare_dependencies"]
        assert (
            dep_action["impact"]["modifies_user_environment"] is False
        )  # C30：装进 Tavotto 自己的环境
        assert _runs(proj) == [] and managedenv.active_generation(proj) is None
        assert set(_snapshot(proj)) - set(start) <= {"tavotto_registry.json"}

        resp = client.post(
            f"{SESSIONS}/{report['session_id']}/actions?pj={pid}",
            json={
                "action_id": dep_action["id"],
                "expected_config_revision": report["config_revision"],
            },
        )
        assert resp.status_code == 202, resp.get_json()
        deadline = time.time() + 600
        while True:
            report = client.get(f"{SESSIONS}/{report['session_id']}?pj={pid}").get_json()
            if report["phase"] in ("ready_to_run", "action_required"):
                break
            assert time.time() < deadline, report
            time.sleep(0.2)
        assert report["phase"] == "ready_to_run", report
        assert _runs(proj) == []  # 装包不执行用户脚本
        gen = managedenv.active_generation(proj)
        assert gen

        (run,) = [a for a in report["actions"] if a["kind"] == "run"]
        resp = client.post(
            f"{SESSIONS}/{report['session_id']}/actions?pj={pid}",
            json={"action_id": run["id"], "expected_config_revision": report["config_revision"]},
        )
        assert resp.status_code == 202
        deadline = time.time() + 300
        while True:
            report = client.get(f"{SESSIONS}/{report['session_id']}?pj={pid}").get_json()
            if report["phase"] in (
                "completed",
                "partial",
                "action_required",
                "awaiting_confirmation",
            ):
                break
            assert time.time() < deadline, report
            time.sleep(0.2)
        assert report["phase"] == "completed", report
        (got,) = _runs(proj)
        assert got["y"] == native["y"] == [42, 84, 126]
        # 跑脚本的那个进程就在受管环境的这一代里，包也是从那里 import 的
        assert os.path.realpath(got["prefix"]) == os.path.realpath(
            managedenv.generation_dir(proj, gen)
        )
        assert os.path.realpath(got["module"]).startswith(os.path.realpath(got["prefix"]))
        (cap,) = report["captured"]
        rendered = client.post(
            f"/api/engine/render?pj={pid}", json={"id": cap["asset_id"], "patches": []}
        )
        assert rendered.status_code == 200
        assert _ylim(rendered.get_json()) == pytest.approx(_expected_ylim(got["y"]), abs=1e-6)
        assert len(_runs(proj)) == 1  # 进编辑复用同一次捕获
        assert {p: _snapshot(proj)[p] for p in start} == start
    finally:
        m.close_project(pid, wait=True)
        prepsession.SESSIONS.reset_for_tests()
        deprepair.reset_state()


# ================================================================ T09b 留下的复核：热会话下的无参数运行

HOT_SCRIPT = """\
import json, sys
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with open(Path(__file__).resolve().parent.parent / "t11_exec_log.jsonl", "a", encoding="utf-8") as out:
    out.write(json.dumps({"argv": sys.argv[1:]}) + "\\n")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [0, 1])
fig.savefig("Runtime_map.pdf")
if "--extra" in sys.argv[1:]:
    fig2, ax2 = plt.subplots(figsize=(3, 2))
    ax2.plot([0, 1], [1, 0])
    fig2.savefig("Extra_map.pdf")
"""


def _run_session(app, body: dict) -> dict:
    status, report = app.call(SESSIONS, body, timeout=120)
    assert status in (200, 201), report
    status, _ = _act(app, report, "run")
    assert status in (200, 202), report
    return _wait_report(app, report["session_id"], ("completed", "partial", "action_required"))


@needs_worker
def test_a_bare_run_on_a_hot_session_still_says_which_figures_it_unlinked(tmp_path):
    """T09b 未验证 4：无参数运行在**已有同配置热会话**时，复用那条会话（脚本不再执行）还是重跑；无论哪种，整条替换
    注册表 stems 的那一刻都要如实带回 `unlinked_stems`（界面据此给可恢复提示），不能因为走了复用就静默丢图名。"""
    proj = tmp_path / "test" / PROJECT_NAME
    proj.mkdir(parents=True)
    (proj / "hot.py").write_text(HOT_SCRIPT, encoding="utf-8")
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        # 打开时静态扫描已把两个字面量图名都登记上（T00 deliberate-boundary）；第一次无参数运行替换掉从没产出过的
        # Extra_map——那不是「此前带其他参数生成的」，不能报（修前红：报了 ['Extra_map']）
        first = _run_session(app, {"script": "hot.py"})
        assert first["phase"] == "completed" and not first.get("unlinked_stems")
        extra = _run_session(app, {"script": "hot.py", "argv": ["--extra"]})
        assert extra["phase"] == "completed"
        reg = json.loads((proj / "tavotto_registry.json").read_text("utf-8"))
        assert sorted(reg["scripts"]["hot.py"]["stems"]) == ["Extra_map", "Runtime_map"]
        # 无参数那份配置的会话还活着（第一次运行建的）：再跑一次无参数。用户点的是「运行」——这是一次明确的新尝试，
        # T11 实测会重新执行（不拿热会话的旧结果冒充这次运行）；这里不钉执行与否，只钉替换那一刻如实带回 unlinked_stems
        bare = _run_session(app, {"script": "hot.py"})
        assert bare["phase"] == "completed", bare
        runs = [r["argv"] for r in _runs(proj)]
        assert runs[:2] == [[], ["--extra"]] and runs[2:] in (
            [],
            [[]],
        )  # T11 本机实测：[[]]（重新执行）
        reg = json.loads((proj / "tavotto_registry.json").read_text("utf-8"))
        assert reg["scripts"]["hot.py"]["stems"] == ["Runtime_map"]  # 旧语义：整条替换
        assert bare.get("unlinked_stems") == ["Extra_map"]


# ================================================================ 故障竞争：两个标签页同时确认 / 运行中切项目

HOLD_SCRIPT = """\
import json, os, sys, time
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

here = Path(__file__).resolve().parent
log = here.parent / "t11_exec_log.jsonl"
with open(log, "a", encoding="utf-8") as out:
    out.write(json.dumps({"event": "start", "argv": sys.argv[1:]}) + "\\n")
hold = here.parent / "HOLD"
deadline = time.time() + 120
while hold.exists() and time.time() < deadline:
    time.sleep(0.05)
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [5, 6, 9])
fig.savefig("held.pdf")
with open(log, "a", encoding="utf-8") as out:
    out.write(json.dumps({"event": "done"}) + "\\n")
"""


@needs_worker
def test_two_tabs_confirming_together_and_switching_projects_mid_run(tmp_path):
    """C06 / X01 / X02 / C09（真 worker、真服务）：

    * 两个「标签页」同时认领同一个 `run` 动作 → 只有一次执行（脚本 start 行恰好 1），另一个拿到同一次尝试；
    * 脚本停在闸门上时用户打开另一个项目 B（`POST /api/projects/open`，界面切项目同一条路）→ A 的尝试不被取消、照常跑完；
      B 看不见 A 的会话（跨项目 404）；回到 A 读到真实终局，执行仍是 1 次。"""
    a_root = tmp_path / "a" / PROJECT_NAME
    a_root.mkdir(parents=True)
    (a_root / "held.py").write_text(HOLD_SCRIPT, encoding="utf-8")
    b_root = tmp_path / "b" / "另一个项目"
    b_root.mkdir(parents=True)
    (b_root / "held.py").write_text(HOLD_SCRIPT, encoding="utf-8")
    hold = a_root.parent / "HOLD"
    hold.write_text("hold", encoding="utf-8")
    try:
        with fa.running_app(a_root, tmp_path / "work", env_overrides=ENV) as app:
            _, cur = app.call("/api/project", timeout=30)
            pid_a = cur["id"]
            status, report = app.call(f"{SESSIONS}?pj={pid_a}", {"script": "held.py"}, timeout=120)
            assert status == 201 and report["phase"] == "ready_to_run", report
            (run,) = [a for a in report["actions"] if a["kind"] == "run"]
            body = {"action_id": run["id"], "expected_config_revision": report["config_revision"]}
            barrier = threading.Barrier(2)
            results: list[tuple[int, dict]] = []

            def tab():
                barrier.wait()
                results.append(
                    _call(
                        app,
                        f"{SESSIONS}/{report['session_id']}/actions?pj={pid_a}",
                        body,
                        timeout=60,
                    )
                )

            threads = [threading.Thread(target=tab) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
            assert sorted((s, b["claimed"]) for s, b in results) == [(200, False), (202, True)]
            attempts = {b["report"]["provider"]["attempt_id"] for _, b in results}
            assert len(attempts) == 1

            # 脚本真的开跑了（停在闸门上）
            deadline = time.time() + 120
            while not [r for r in _runs(a_root) if r["event"] == "start"]:
                assert time.time() < deadline
                time.sleep(0.1)

            # 切到项目 B：不取消 A 的尝试
            status, opened = app.call("/api/projects/open", {"path": str(b_root)}, timeout=60)
            assert status == 200, opened
            pid_b = opened["id"]
            assert pid_b != pid_a
            status, cross = _call(app, f"{SESSIONS}/{report['session_id']}?pj={pid_b}", timeout=30)
            assert status == 404, cross  # B 看不见 A 的会话
            status, still = app.call(f"{SESSIONS}/{report['session_id']}?pj={pid_a}", timeout=30)
            assert still["phase"] == "running", still

            hold.unlink()
            deadline = time.time() + 300
            while True:
                _, final = app.call(f"{SESSIONS}/{report['session_id']}?pj={pid_a}", timeout=30)
                if final["phase"] not in ("running", "awaiting_runtime_input"):
                    break
                assert time.time() < deadline, final
                time.sleep(0.1)
            assert final["phase"] == "completed", final
            assert [r["event"] for r in _runs(a_root)] == ["start", "done"]  # 恰好一次执行，跑完
            assert _runs(b_root) == []  # B 的同名脚本一行没跑
    finally:
        if hold.exists():
            hold.unlink()
