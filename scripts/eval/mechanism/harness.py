"""评测：Agent 画「受体在膜上」的最小机理图（ADR 0220 第 6.D 期）。

和 `scripts/eval/vector_edit/` 同一套约定：题库 `corpus.json`（`build_corpus.py` 生成）、判据 `predicates.py`（纯标准库、只判结果）、结果每次运行一行 JSONL。
不进 CI（单测只验题库与判据），不在 PR 里跑；高级版组件用**本地真组件**（不发布），授权在进程里置真（`license.has`），不碰任何用户配置。

子命令：
  list                  列题库
  run Aref              接线自检（**不叫模型**）：标准答案批 → 真的 `vectoredit.propose_memory`（含机理图编排、命名分组、可编辑性核对）→ 判据。记 `simulated: true`
  run Tools             插件工具层的端到端（**不叫模型**）：真的 `tavotto_layout_scene` → `tavotto_layout_edit_plan` → `tavotto_layout_edit_apply` →
                        读写盘后的排版判分 → `tavotto_layout_annotate_undo` 核回到原样；标准答案批作 `ops`，高级版组件用本地真组件（进程内替身 `prohost.call`）。
                        要一个带科学栈的解释器（`PYTHON`，缺省当前解释器）。记 `simulated: true`
  run Mut               判据自检（**不叫模型**）：对标准答案做一批故意写坏的变形，每个变形必须被指定的判据拦下（拦不下 = 判据有洞）
  judge <文件.jsonl>    给模型产出的批判分：每行 `{"task": 题号, "items": [条目…]}`（B 组 / 真模型接上以后用；本期没有真模型的记录）
  report                汇总 results/ 下的 JSONL

环境变量：
  EVAL_PRO_SRC     本地高级版组件源码目录（缺省 ../tavotto-pro-wt-mech 或 $HOME 下约定位置不存在就报错）；只经 sys.path，不安装、不发布
  EVAL_RESULTS     结果目录（缺省 results/）
  TAVOTTO_FONTS_DIR 批准字体目录（文字量宽要）
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))
PRO_SRC = os.environ.get("EVAL_PRO_SRC") or str(ROOT.parent / "tavotto-pro-wt-mech")
RESULTS = Path(os.environ.get("EVAL_RESULTS") or (HERE / "results"))
CORPUS_JSON = HERE / "corpus.json"

import predicates  # noqa: E402


def load_corpus() -> dict:
    return json.loads(CORPUS_JSON.read_text(encoding="utf-8"))


def local_ask():
    """本地真组件的 `ask(cmd, payload)`：直接调 `tavotto_pro.mechanism`（与 sidecar 同一份代码，只是不经进程）。"""
    if PRO_SRC not in sys.path:
        sys.path.insert(0, PRO_SRC)
    try:
        from tavotto_pro import mechanism as pro
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"找不到本地高级版组件（EVAL_PRO_SRC={PRO_SRC}）：{exc}")

    def ask(cmd: str, payload: dict) -> dict:
        return pro.catalog() if cmd == "mechanism.catalog" else pro.glyph(payload)

    return ask


def propose(canvas: dict, items: list, *, licensed: bool) -> dict:
    from tavotto.engine import license as engine_license, vectoredit as ve

    saved = engine_license.has
    engine_license.has = lambda feature: licensed
    try:
        return ve.propose_memory(
            canvas,
            [{"op": "add", "items": items}],
            context={"mechanism_ask": local_ask(), "locale": "zh"},
        )
    finally:
        engine_license.has = saved


def judge(task: dict, canvas: dict, items: list) -> dict:
    """一题一批条目 → 记录（成败、每条判据的说明、核对结果）。"""
    t0 = time.time()
    licensed = task.get("expect") != "pro_required"
    res = propose(copy.deepcopy(canvas), items, licensed=licensed)
    rec = {
        "task": task["id"],
        "plan_ok": bool(res.get("ok")),
        "code": res.get("code"),
        "errors": res.get("errors"),
        "failed": res.get("failed"),
        "wall_s": round(time.time() - t0, 2),
    }
    if task.get("expect") == "pro_required":
        unchanged = res.get("code") == "pro_required" and not res.get("objects")
        rec["success"] = bool(unchanged)
        rec["predicate_detail"] = [
            {
                "t": "pro_required",
                "ok": res.get("code") == "pro_required",
                "detail": f"code={res.get('code')}",
            },
            {
                "t": "nothing_written",
                "ok": not res.get("objects"),
                "detail": "没有对象落地" if not res.get("objects") else "有对象落地",
            },
        ]
        return rec
    if not res.get("ok"):
        rec["success"] = False
        rec["predicate_detail"] = []
        rec["stuck"] = (
            (res.get("errors") or [{}])[0].get("code") or res.get("code") or "plan_failed"
        )
        return rec
    got = predicates.evaluate(task, canvas["objects"], res["objects"])
    rec["success"] = got["ok"]
    rec["predicate_detail"] = got["checks"]
    rec["echo_hints"] = [e for e in res.get("echo", []) if str(e).startswith("提示")]
    return rec


def cmd_list(_args) -> int:
    for t in load_corpus()["tasks"]:
        print(f"{t['id']:20s} {t['instruction']}")
    return 0


def _append(name: str, recs: list[dict]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8"
    )
    return path


def cmd_run_aref(_args) -> int:
    corpus = load_corpus()
    recs = []
    for t in corpus["tasks"]:
        rec = {**judge(t, corpus["canvas"], t["reference"]), "mode": "Aref", "simulated": True}
        recs.append(rec)
        print(f"{'OK ' if rec['success'] else 'XX '} {t['id']}")
        for c in rec["predicate_detail"]:
            if not c["ok"]:
                print(f"     {c['t']}: {c['detail']}")
    print("→", _append("Aref", recs))
    return 0 if all(r["success"] for r in recs) else 1


def _mutations(task: dict) -> list[dict]:
    """故意写坏的变形：`{name, items, expect}`；`expect` = 必须判失败的那条判据名（或 `plan` = 引擎直接拒）。"""
    ref = task["reference"]
    out: list[dict] = []

    def clone():
        return copy.deepcopy(ref)

    names = [it["name"] for it in ref]
    if "r1" in names and "r2" in names:
        m = clone()
        for it in m:
            if it["name"] == "r2":
                it["embed"] = {"membrane": "mem", "at": 0.5}
            if it["name"] == "r1":
                it["embed"] = {"membrane": "mem", "at": 0.5}
        out.append({"name": "two-proteins-same-spot", "items": m, "expect": "plan"})
    if "r1" in names:
        m = clone()
        for it in m:
            if it["name"] == "r1":
                del it["embed"]
                it["at_mm"] = [60, 5]  # 放到膜外（框内但不在膜上）
        out.append({"name": "protein-off-membrane", "items": m, "expect": "proteins_in_membrane"})
    texts = [it for it in ref if it["kind"] == "text"]
    if texts:
        m = clone()  # 文字远离目标：锚到示意图框的右下角（离图元远）
        for it in m:
            if it["name"] == texts[0]["name"]:
                it["at"] = {
                    "anchor": {"item": {"name": "frm", "point": "se"}},
                    "offset_mm": [-14, -1],
                }
                it["attach"] = "w"
        out.append({"name": "label-far-from-target", "items": m, "expect": "labels_adjacent"})
    if any(it["kind"] == "arrow" for it in ref):
        m = clone()
        for it in m:
            if it["kind"] == "arrow":
                it["to"] = {
                    "anchor": {"item": {"name": "frm", "point": "se"}},
                    "offset_mm": [-3, -3],
                }
                break
        out.append({"name": "arrow-in-the-air", "items": m, "expect": "arrows_reach"})
    m = clone()
    for it in m:
        if it["kind"] == "frame":
            it["size_mm"] = [30, 20]
    out.append({"name": "frame-too-small", "items": m, "expect": "inside_frame"})
    m = clone()
    for it in m:
        if it["name"] == "mem" and "params" in it:
            it["params"] = {**it["params"], "length_mm": 100}
    out.append({"name": "membrane-length-out-of-range", "items": m, "expect": "plan"})
    return out


def cmd_run_mut(_args) -> int:
    corpus = load_corpus()
    recs = []
    holes = 0
    for t in corpus["tasks"]:
        if t.get("expect"):
            continue
        for mu in _mutations(t):
            rec = judge(t, corpus["canvas"], mu["items"])
            caught_by = (
                rec.get("stuck")
                if not rec["plan_ok"]
                else [c["t"] for c in rec["predicate_detail"] if not c["ok"]]
            )
            # 引擎自己拒了（核对 / 校验不过）也算拦下；否则必须是点名的那条判据判失败
            caught = (not rec["plan_ok"]) or (
                mu["expect"] != "plan" and mu["expect"] in (caught_by or [])
            )
            holes += 0 if caught else 1
            recs.append(
                {
                    "task": t["id"],
                    "mode": "Mut",
                    "mutation": mu["name"],
                    "expect": mu["expect"],
                    "caught": caught,
                    "caught_by": caught_by,
                    "errors": rec.get("errors"),
                    "simulated": True,
                }
            )
            print(f"{'caught ' if caught else 'MISSED '} {t['id']}/{mu['name']} → {caught_by}")
    print("→", _append("Mut", recs))
    return 0 if holes == 0 else 1


LAYOUT = "组图"
CANVAS_ID = "c_main0001"


def make_project(corpus: dict, task: dict, dest: Path) -> Path:
    """工具层端到端的项目：借「矢量编辑」评测的合成子图脚本（`FigM.pdf`）+ 排版文件（画布 = 本题的画布）。"""
    import shutil
    import subprocess

    ve_corpus = json.loads(
        (HERE.parent / "vector_edit" / "corpus.json").read_text(encoding="utf-8")
    )
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    (dest / "figm.py").write_text(ve_corpus["figure_script"], encoding="utf-8")
    (dest / "tavotto_registry.json").write_text(json.dumps(ve_corpus["registry"]), encoding="utf-8")
    proc = subprocess.run(  # noqa: S603
        [os.environ.get("PYTHON") or sys.executable, str(dest / "figm.py")],
        cwd=dest, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    if proc.returncode or not (dest / "FigM.pdf").is_file():
        raise RuntimeError(f"子图脚本没跑成：{proc.stderr[-300:]}")
    cv = corpus["canvas"]
    doc = {
        "schema": 3,
        "project": {"id": "p_test0001", "name": "t"},
        "canvases": [{"id": CANVAS_ID, "name": "图版 1", "page": cv["page"], "objects": cv["objects"], "guides": []}],
        "activeCanvasId": CANVAS_ID,
        "createdAt": 0,
        "updatedAt": 0,
    }  # fmt: skip
    store = dest / "tavottofile"
    store.mkdir()
    (store / f"{LAYOUT}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return dest


def cmd_run_tools(_args) -> int:
    import tempfile

    corpus = load_corpus()
    work = Path(tempfile.mkdtemp(prefix="tavotto-mech-eval-"))
    sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))
    os.environ.update(
        TAVOTTO_DATA_DIR=str(work / "data"), TAVOTTO_CONFIG_DIR=str(work / "config"),
        TAVOTTO_USER_ENV_DISCOVERY="0", TAVOTTO_NO_UPDATE_CHECK="1", TAVOTTO_NO_TELEMETRY="1",
    )  # fmt: skip
    from tavotto.engine import layoutannotate as la, license as engine_license, prohost

    ask = local_ask()
    state = {"licensed": True}
    engine_license.has = lambda feature: state["licensed"]
    prohost.call = lambda cmd, payload, feature=None: ask(
        cmd, payload
    )  # 进程内替身：真组件的代码，不经 sidecar 进程
    from tavotto_mcp import server  # noqa: PLC0415

    recs = []
    for t in corpus["tasks"]:
        project = make_project(corpus, t, work / t["id"] / "project")
        os.environ["TAVOTTO_MCP_ROOTS"] = str(project)
        target = {"project_path": str(project), "layout": LAYOUT}
        state["licensed"] = t.get("expect") != "pro_required"
        path = project / "tavottofile" / f"{LAYOUT}.json"
        before_bytes = path.read_bytes()
        before = la.open_layout(project, LAYOUT).doc["canvases"][0]["objects"]
        rec: dict = {"task": t["id"], "mode": "Tools", "simulated": True}
        t0 = time.time()
        scene = server.call_tool("tavotto_layout_scene", target)["structuredContent"]
        ops = [{"op": "add", "items": t["reference"]}]
        common = {**target, "ops": ops, "base_revision": scene["base_revision"]}
        raw = server.call_tool("tavotto_layout_edit_plan", common)
        plan = raw["structuredContent"]
        rec["plan_ok"], rec["code"] = bool(plan.get("ok")), plan.get("code")
        rec["plan_text"] = (raw["content"][0]["text"] if raw.get("content") else "")[:300]
        if t.get("expect") == "pro_required":
            rec["success"] = (
                plan.get("code") == "pro_required" and path.read_bytes() == before_bytes
            )
            rec["predicate_detail"] = [
                {
                    "t": "pro_required_and_untouched",
                    "ok": rec["success"],
                    "detail": f"code={plan.get('code')}",
                }
            ]
        elif not plan.get("ok"):
            rec["success"], rec["predicate_detail"] = False, []
            rec["stuck"] = (plan.get("errors") or [{}])[0].get("code") or "plan_failed"
            rec["errors"] = plan.get("errors")
        else:
            assert path.read_bytes() == before_bytes  # 出方案不写盘
            done = server.call_tool(
                "tavotto_layout_edit_apply", {**common, "echo_digest": plan["echo_digest"]}
            )["structuredContent"]
            after = la.open_layout(project, LAYOUT).doc["canvases"][0]["objects"]
            got = predicates.evaluate(t, before, after)
            un = server.call_tool(
                "tavotto_layout_annotate_undo", {**target, "undo_token": done["undo_token"]}
            )
            rec["undo"] = {
                k: v for k, v in (un.get("structuredContent") or {}).items() if k != "objects"
            }
            now = la.open_layout(project, LAYOUT).doc["canvases"][0]["objects"]
            # 撤销后：排版版本号回到出方案时的那个，对象表与原来深相等（文件字节可能被重新序列化，不比字节）
            undone = rec["undo"].get("restored_to") == scene["base_revision"] and now == before
            rec["predicate_detail"] = [
                *got["checks"],
                {
                    "t": "undo_restores_layout",
                    "ok": undone,
                    "detail": "撤销后版本号与对象表回到原样" if undone else "撤销没回到原样",
                },
            ]
            rec["success"] = got["ok"] and bool(done.get("written")) and undone
        rec["wall_s"] = round(time.time() - t0, 2)
        recs.append(rec)
        print(f"{'OK ' if rec['success'] else 'XX '} {t['id']}")
        for c in rec["predicate_detail"]:
            if not c["ok"]:
                print(f"     {c['t']}: {c['detail']}")
    print("→", _append("Tools", recs))
    return 0 if all(r["success"] for r in recs) else 1


def cmd_judge(args) -> int:
    corpus = load_corpus()
    by = {t["id"]: t for t in corpus["tasks"]}
    recs = []
    for line in Path(args.file).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        t = by[row["task"]]
        recs.append({**judge(t, corpus["canvas"], row["items"]), "mode": "judge"})
    print("→", _append(Path(args.file).stem + "-judged", recs))
    print(sum(r["success"] for r in recs), "/", len(recs))
    return 0


def cmd_report(_args) -> int:
    for f in sorted(RESULTS.glob("*.jsonl")):
        rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
        ok = sum(bool(r.get("success", r.get("caught"))) for r in rows)
        print(
            f"{f.name}: {ok}/{len(rows)}"
            + ("（模拟，不是模型成绩）" if rows and rows[0].get("simulated") else "")
        )
    return 0


def main(argv: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("mode", choices=["Aref", "Mut", "Tools"])
    j = sub.add_parser("judge")
    j.add_argument("file")
    sub.add_parser("report")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "run":
        return {"Aref": cmd_run_aref, "Mut": cmd_run_mut, "Tools": cmd_run_tools}[a.mode](a)
    if a.cmd == "judge":
        return cmd_judge(a)
    return cmd_report(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
