#!/usr/bin/env python3
"""Tavotto Matplotlib CompatBench —— 兼容性资格验证。

回答一个此前没有被量化过的问题：

    一份来自 ChatGPT / Claude / Copilot / 普通科研用户、写法不可预测的
    matplotlib 脚本，Tavotto 到底有多大概率能正确发现、执行、捕获、打开、
    识别、编辑、撤销、重放并导出？

与既有 `tests/acceptance/`（golden 回归）**问的不是同一个问题**：
那边问「已支持的行为有没有退化」，这边问「外部 matplotlib 世界我们兼容多少」。
两者共享工具，语义必须分开——合并之后就再也分不清「我们退步了」和
「我们本来就不支持」。

## 不许假兼容

这套东西的价值全部来自「走用户真实路径」。所以下面这些一条都不做：

* 只 import 脚本不真渲染；
* 只看输出文件在不在；
* 喂 worker 一份假 manifest；
* 绕过 worker 直接调 `overrides.py`；
* runner 自己解析 matplotlib；
* case 红了就自动改期望 / 自动生成基线。

兼容判定（execute / capture / open / semantic / edit / replay / export）
**全部经真实 worker 协议**（`pool.one_shot`）。只有两件生产路径按设计做不到
的事走旁路驱动：原生对照渲染、artist 普查——它们**不参与 pass/fail**。

## 漏斗，不是一个百分比

报告输出九级漏斗与六类分类。一个「92%」会把「产品刻意不支持」「环境缺字体」
「我们的 bug」揉成同一个数字，而那三件事的处理方式完全相反。

用法：
    python scripts/ci/compat_matrix.py --smoke            # PR 档，12~20 个 case
    python scripts/ci/compat_matrix.py --all              # 全量
    python scripts/ci/compat_matrix.py --target bundled   # 指定版本目标
    python scripts/ci/compat_matrix.py --case shape_pyplot_show_only
    python scripts/ci/compat_matrix.py --all --update-baseline   # 本地，人来读
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1] / "src"))
import compat_corpus as CC                                        # noqa: E402
import pixelcompare                                               # noqa: E402
# 中文 help / 中文进度行在 Windows 的 cp1252 stdout 上会把进程打死。
# `_common` 里那一份是唯一实现，这里显式调一次——本脚本用不到 `_common`
# 的其它东西，靠「import 了就自动生效」是隐式耦合。
from _common import run_metadata, use_utf8_streams               # noqa: E402

use_utf8_streams()

REPO = _HERE.parents[1]
DRIVER = _HERE / "compat_driver.py"
ENGINE_DIR = REPO / "src" / "tavotto" / "engine"

#: 保真度比对的渲染宽度（像素）。够大到能看出字号与位移，又不至于让 80 个
#: case 的 PNG 把 artifact 撑爆。
FIDELITY_WIDTH = 640

#: 零 patch 保真度的默认容差。**比 golden 视觉回归松一档**是有理由的：
#: 那边比的是「同一条链路的今天与昨天」，这边比的是「原生 matplotlib 与
#: Tavotto instrument 之后」，两侧的 PNG 由两个进程分别编码，字体 hinting
#: 与光栅化的舍入会有极小的系统性差异。松到这一档之后，真正的问题
#: （instrument 改了 artist、draw 顺序变了、图例被挪动）仍然远超阈值——
#: 实测那类问题的 changed_ratio 在 1e-2 量级，而底噪在 1e-4 量级。
FIDELITY_TOLERANCE = {
    "changed_pixel_ratio": 0.004,
    "mean_abs_diff": 1.2,
    "max_abs_diff": 140,
}

#: 导出文件的最小合理体积。低于它基本可以断定是个空壳。
MIN_EXPORT_BYTES = {"pdf": 900, "png": 900}


class Skip(Exception):
    """这个阶段不适用（不是失败）。"""


# --------------------------------------------------------------------------
# 项目物化：case → 一个临时图库
# --------------------------------------------------------------------------
def materialize(group: list[dict], workdir: Path) -> tuple[Path, str, str]:
    """按 case 组建一个临时图库，返回 (项目根, 脚本相对路径, entry)。

    刻意**不**在仓库里就地跑：corpus 脚本会写出 PDF/PNG，落进版本库既是噪音
    也会让「工作目录干净」的判定失效。
    """
    case = group[0]
    project = workdir / "project"
    project.mkdir(parents=True, exist_ok=True)

    dest_rel = case.get("script_dest") or Path(case["script"]).name
    dest = project / dest_rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CC.COMPAT_DIR / case["script"], dest)

    for rel, src in (case.get("extra_files") or {}).items():
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CC.COMPAT_DIR / src, target)

    for asset in case.get("assets") or []:
        # 素材放在**脚本自己的目录**旁边——`pd.read_csv("data.csv")` 的语义
        # 是「脚本旁边那一份」，不是「项目根那一份」。
        shutil.copy2(CC.ASSETS_DIR / asset, dest.parent / asset)

    return project, str(Path(dest_rel).as_posix()), case.get("entry", "main")


# --------------------------------------------------------------------------
# 阶段 1：发现（discover → probe → registry）
# --------------------------------------------------------------------------
def stage_discover(project: Path, script_rel: str, case: dict) -> dict:
    """走产品自己的发现链，**不允许 runner 直接把 script/entry 塞给 worker**。

    用户的真实体验不是手写注册表。一个「本该能自动发现」的 case 只有靠清单
    硬告诉 runner 才跑得起来，那是 bug 而不是 pass——这里就是那条判据。
    """
    from tavotto.engine import discover as engine_discover
    from tavotto.engine import probe as engine_probe
    from tavotto.engine import registry as engine_registry

    want_mode = case["discovery"]
    detail: dict = {"declared": want_mode}

    cfg, report, _changes = engine_discover.merge(project)
    engine_discover.write_config(project, cfg)
    reg = engine_registry.Registry()
    reg.load(project)

    stem = case["stem"]
    info = reg.for_stem(stem)
    detail["static_entry"] = (report["scripts"].get(script_rel) or {}).get("entry")
    detail["static_stems"] = sorted(
        (report["scripts"].get(script_rel) or {}).get("stems") or [])
    detail["conflicts"] = sorted(report.get("conflicts") or {})

    if info is not None:
        detail["actual"] = "discoverable"
        detail["entry"] = info["entry"]
        return {"ok": want_mode == "discoverable", "detail": detail,
                "entry": info["entry"], "registry": reg}

    if want_mode == "discoverable":
        detail["actual"] = "not_discovered"
        return {"ok": False, "detail": detail, "entry": None, "registry": reg}

    # 试运行探测：stem 只有运行期才知道时的那条路。
    result = engine_probe.probe_and_register(project, script_rel)
    detail["probe"] = {"entry": result.get("entry"), "tried": result.get("tried"),
                       "stems": result.get("stems"), "error": result.get("error")}
    reg = engine_registry.Registry()
    reg.load(project)
    info = reg.for_stem(stem)
    detail["actual"] = "requires_probe" if info is not None else "not_discovered"
    return {"ok": info is not None and want_mode == "requires_probe",
            "detail": detail,
            "entry": info["entry"] if info else None, "registry": reg}


# --------------------------------------------------------------------------
# 阶段 2~8：真实 worker
# --------------------------------------------------------------------------
def _fresh_worker(script_rel: str, project: Path, entry: str):
    from tavotto.engine import pool
    w = pool.one_shot(script_rel, str(project), entry)
    w.ensure_built()
    return w


def _elements(man: dict) -> dict:
    return {el["gid"]: el for el in man["elements"]}


_MISSING = object()


def _field(man: dict, gid: str, prop: str):
    el = _elements(man).get(gid)
    if el is None:
        return _MISSING
    for f in el.get("editable", []):
        if f["prop"] == prop:
            return f["value"]
    return _MISSING


def _same_value(want, got) -> bool:
    if isinstance(want, bool) or isinstance(got, bool):
        return want == got
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(float(want) - float(got)) <= max(0.02, abs(float(want)) * 0.02)
    if isinstance(want, str):
        return str(got).lower() == want.lower()
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        return all(_same_value(a, b) for a, b in zip(want, got))
    return want == got


def stage_semantic(man: dict, case: dict) -> dict:
    """清单声明的角色与可编辑字段，在真实 manifest 里必须都在。"""
    sem = case.get("semantic_expectations") or {}
    roles = {el["role"] for el in man["elements"]}
    missing_roles = [r for r in sem.get("roles_present") or [] if r not in roles]
    missing_fields = [f"{gid}.{prop}"
                      for gid, prop in (sem.get("editable") or [])
                      if _field(man, gid, prop) is _MISSING]
    return {"ok": not missing_roles and not missing_fields,
            "detail": {"roles": sorted(roles),
                       "missing_roles": missing_roles,
                       "missing_editable": missing_fields}}


def stage_edit(worker, stem: str, case: dict, base_man: dict) -> dict:
    """代表性编辑：应用 → manifest 反映 → 撤销 → 回到原值。

    **全量列表语义**：撤销就是把这一条从列表里去掉再发一次，不是发一个
    「反向 patch」。判据与 `tests/test_equivalence_matrix.py` 同源。
    """
    targets = case.get("mutations") or []
    if not targets:
        raise Skip("这个 case 没有声明编辑目标")
    results = []
    ok = True
    applied: list[dict] = []
    for t in targets:
        gid, prop, value = t["gid"], t["prop"], t["value"]
        before = _field(base_man, gid, prop)
        applied = applied + [{"gid": gid, "prop": prop, "value": value}]
        resp = worker.override(stem, applied)
        warns = resp.get("warnings") or []
        after = _field(resp["manifest"], gid, prop)
        landed = after is not _MISSING and _same_value(value, after)
        results.append({"gid": gid, "prop": prop, "applied": landed,
                        "warnings": warns, "before": _jsonable(before),
                        "after": _jsonable(after)})
        if warns or not landed:
            ok = False
    # 一次性全撤（全量列表 = 空列表），逐条核对回到原值
    restored = worker.override(stem, [])
    restore_bad = []
    for t in targets:
        gid, prop = t["gid"], t["prop"]
        want = _field(base_man, gid, prop)
        got = _field(restored["manifest"], gid, prop)
        if want is _MISSING or got is _MISSING or not _same_value(want, got):
            restore_bad.append(f"{gid}.{prop}: {want!r} → {got!r}")
    if restore_bad or (restored.get("warnings") or []):
        ok = False
    return {"ok": ok, "detail": {"targets": results,
                                 "restore_failures": restore_bad,
                                 "restore_warnings": restored.get("warnings") or []},
            "full_patches": applied}


def _editable_snapshot(man: dict) -> dict:
    """manifest 里每个可编辑字段的当前值：`{"gid.prop": value}`。"""
    return {f"{el['gid']}.{f['prop']}": f["value"]
            for el in man.get("elements", []) for f in el.get("editable", [])}


def _prop_diffs(a: dict, b: dict, limit: int = 8) -> list[str]:
    """两份 manifest 之间**属性值**的分歧（几何之外那一半）。"""
    sa, sb = _editable_snapshot(a), _editable_snapshot(b)
    out = []
    for key in sorted(set(sa) & set(sb)):
        if not _same_value(sa[key], sb[key]):
            out.append(f"{key}: {sa[key]!r} vs {sb[key]!r}")
        if len(out) >= limit:
            break
    return out


def stage_replay(worker, fresh, stem: str, patches: list) -> dict:
    """热态 == 清空后全量重放 == 全新 worker 重放。

    几何判据复用写回事务的 `app._compare_manifests`——放行/阻断用户写回的
    就是它，容差一字不差。另起一套只会让矩阵与产品各绿各的。

    **但只用它是不够的，而这一点是实测出来的。** 那个比较器的 docstring
    自己写着「只比几何」（gid 集合 / bbox / anchor / size_mm）。于是**纯属性
    的分歧它一处都看不见**：实测「广播改柱色 → 单柱改色 → 全撤」之后，热态
    的 `bar_0` 停在 `#775599` 而全新重放是 `#1f77b4`，`_compare_manifests`
    比过 18 个元素、报 0 处分歧。放在产品里那意味着坏颜色一路烙进用户原件、
    零报错；放在这里意味着 CompatBench 的 replay 阶段会**替产品盖住**它自己
    的盲区——一个自称在验等价性、却看不见颜色的基准。

    所以这里在几何之外**另加一层属性值比对**。这不是「第二套容差」（几何那
    一套仍然只有 `_compare_manifests` 一份）：它比的是另一个维度，而产品的
    写回门禁目前不比这个维度，是记录在案的既有缺口（见
    docs/ci/matplotlib-compatibility.md）。
    """
    from tavotto.app import _compare_manifests

    hot = worker.override(stem, patches)["manifest"]
    worker.override(stem, [])
    replay = worker.override(stem, patches)["manifest"]
    fresh_man = fresh.override(stem, patches)["manifest"]

    legs = []
    ok = True
    for name_a, a, name_b, b in (("hot", hot, "clear+replay", replay),
                                 ("hot", hot, "fresh worker", fresh_man)):
        diffs, compared = _compare_manifests(a, b)
        if compared == 0:
            ok = False
            legs.append({"pair": f"{name_a} vs {name_b}", "compared": 0,
                         "diffs": ["没有可比元素（manifest 空？）"]})
            continue
        props = _prop_diffs(a, b)
        if diffs or props:
            ok = False
        legs.append({"pair": f"{name_a} vs {name_b}", "compared": compared,
                     "diffs": [f"{d['gid'] or '<figure>'}.{d['field']}: "
                               f"{d['hot']} vs {d['fresh']}" for d in diffs[:8]],
                     # 几何之外那一半——产品的写回门禁看不见这一列，见 docstring
                     "prop_diffs": props})
    fresh.override(stem, [])
    return {"ok": ok, "detail": {"legs": legs}}


def stage_export(worker, stem: str, out_dir: Path, formats: list[str]) -> dict:
    """真导出、真检查。文件在、体积合理、**解得开**、无 warning。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    ok = True
    for fmt in formats:
        path = out_dir / f"{stem}.{fmt}"
        try:
            resp = worker.export(stem, [], str(path), fmt=fmt,
                                 dpi=200 if fmt == "png" else 600)
        except Exception as exc:                        # noqa: BLE001
            ok = False
            results[fmt] = {"ok": False, "error": str(exc)[:400]}
            continue
        warns = resp.get("warnings") or []
        entry: dict = {"warnings": warns,
                       "export_ms": (resp.get("timings") or {}).get("export_ms")}
        if not path.is_file():
            entry.update(ok=False, error="导出说成功了，文件却不在")
        else:
            size = path.stat().st_size
            entry["bytes"] = size
            if size < MIN_EXPORT_BYTES[fmt]:
                entry.update(ok=False, error=f"只有 {size} 字节，不像一张图")
            else:
                decoded = _decode_check(path, fmt)
                entry.update(decoded)
        if not entry.get("ok") or warns:
            ok = False
            entry["ok"] = entry.get("ok", False) and not warns
        results[fmt] = entry
    return {"ok": ok, "detail": results}


def _decode_check(path: Path, fmt: str) -> dict:
    """导出物必须真的打得开——「文件存在」证明不了它不是半个字节流。"""
    if fmt == "pdf":
        try:
            import pymupdf
        except ImportError:                             # pragma: no cover
            return {"ok": True, "decoded": "skipped_no_pymupdf"}
        try:
            with pymupdf.open(path) as doc:
                if doc.page_count < 1:
                    return {"ok": False, "error": "PDF 里一页都没有"}
                rect = doc[0].rect
            return {"ok": True, "pages": 1,
                    "page_pt": [round(rect.width, 2), round(rect.height, 2)]}
        except Exception as exc:                        # noqa: BLE001
            return {"ok": False, "error": f"PDF 打不开：{exc}"}
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
            size = im.size
        return {"ok": True, "px": list(size)}
    except ImportError:                                 # pragma: no cover
        return {"ok": True, "decoded": "skipped_no_pillow"}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"PNG 打不开：{exc}"}


# --------------------------------------------------------------------------
# 阶段 9：零 patch 原生保真度
# --------------------------------------------------------------------------
def run_driver(python: str, mode: str, request: dict, timeout: int = 900) -> dict:
    """spawn 旁路驱动。父进程（.venv）没有 matplotlib，只能这样跑。"""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        json.dump(request, fh)
        req_path = fh.name
    try:
        out = subprocess.run(
            [python, str(DRIVER), "--mode", mode, "--request", req_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, stdin=subprocess.DEVNULL,
            env={**os.environ, "MPLBACKEND": "Agg", "PYTHONHASHSEED": "0"})
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": "driver_timeout",
                "message": f"{mode} 驱动超过 {timeout}s"}
    finally:
        os.unlink(req_path)
    text = (out.stdout or "").strip()
    if not text:
        return {"ok": False, "code": "driver_no_output",
                "message": f"{mode} 驱动没有输出", "stderr": out.stderr[-1500:]}
    try:
        return json.loads(text.splitlines()[-1])
    except ValueError:
        return {"ok": False, "code": "driver_bad_output",
                "message": f"{mode} 驱动末行不是 JSON",
                "stdout": text[-800:], "stderr": out.stderr[-1500:]}


def stage_fidelity(python: str, project: Path, script_rel: str, entry: str,
                   worker, stem: str, workdir: Path, out_dir: Path) -> dict:
    """**原生 matplotlib** vs **Tavotto 零 override**。

    这是 golden 回归回答不了的那个问题：golden 比的是「Tavotto 今天 vs
    Tavotto 昨天」，它抓不到「Tavotto 从第一版起就一直偷偷改了某个 artist」。
    原则很硬：**没有任何 override 时，Tavotto 不该改变用户的 Figure。**
    """
    native_dir = workdir / "native"
    native = run_driver(python, "native", {
        "script": str(project / script_rel), "project": str(project),
        "engine_dir": str(ENGINE_DIR), "entry": entry,
        "out_dir": str(native_dir), "width": FIDELITY_WIDTH,
    })
    if not native.get("ok"):
        return {"ok": False, "detail": {"native": native}}
    control = native["shots"].get(stem)
    if control is None:
        return {"ok": False, "detail": {
            "error": f"原生对照里没有 {stem}（捕获到 {native['stems']}）"}}

    try:
        shot = worker.render_png(stem, FIDELITY_WIDTH)
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "detail": {"error": f"Tavotto 出图失败：{exc}"}}

    out_dir.mkdir(parents=True, exist_ok=True)
    diff = out_dir / f"{stem}.diff.png"
    try:
        metrics = pixelcompare.compare(Path(control), Path(shot), diff)
    except pixelcompare.MissingImagingDeps as exc:
        raise Skip(str(exc)) from exc
    good, reasons = pixelcompare.verdict(metrics, FIDELITY_TOLERANCE)
    if good:
        diff.unlink(missing_ok=True)                    # 通过的不留噪音
    else:
        shutil.copy2(control, out_dir / f"{stem}.control.png")
        shutil.copy2(shot, out_dir / f"{stem}.tavotto.png")
    return {"ok": good, "detail": {"metrics": metrics, "reasons": reasons,
                                   "tolerance": FIDELITY_TOLERANCE}}


def _jsonable(v):
    if v is _MISSING:
        return None
    return v


# --------------------------------------------------------------------------
# 分类：把阶段结果折成六选一
# --------------------------------------------------------------------------
def classify(case: dict, stages: dict, skipped: dict) -> tuple[str, str, str]:
    """→ (classification, reason, detail)。

    **只有清单显式声明过的边界才允许落到非 product_bug 上。**
    否则「我们的 bug」会被悄悄记成「产品边界」，而这正是这套 benchmark
    要消灭的那种自欺。
    """
    declared = case.get("classification", "full_support")
    declared_reason = str(case.get("reason", "")).strip()
    expected = case.get("expected", {})

    failed = [s for s in CC.STAGES
              if s in stages and not stages[s] and expected.get(s, True)]
    # 期望里写着 false 的阶段失败了 —— 那是**声明过的**边界，不是缺陷。
    by_design = [s for s in CC.STAGES
                 if s in stages and not stages[s] and not expected.get(s, True)]

    if not failed:
        if by_design or declared != "full_support":
            note = f"按声明未通过：{by_design}" if by_design else ""
            if declared == "environment_dependency" and not by_design:
                # 声明为环境依赖、而这台机器上依赖**在**：如实说出来。
                # 不说的话读报告的人分不清「这一档跳过了」和「这一档跑过了
                # 而且全绿」——那正是环境依赖最容易被误读的地方。
                note = "本次环境满足依赖，全部阶段通过"
            return (declared if declared != "full_support" else "partial_support",
                    declared_reason or "清单声明的产品边界", note)
        return "full_support", "", ""

    # 有阶段在**期望通过**的地方栽了。
    #
    # `environment_dependency` 只在**依赖真的缺**时吸收失败——那表现为
    # `execute` 就没过（脚本 import 不到那个包）。依赖在、脚本跑起来了，
    # 后面任何一级栽了都是真失败：那时再拿「环境依赖」挡回去，等于给这条
    # case 发了一张永久免检证。**实测撞到过**：装了 seaborn 的目标上
    # `sci_sns_bar` 的 replay 分歧被这一档吸收掉，而真实原因是 corpus 自己
    # 用了 bootstrap 置信区间（随机）——两件事都该被看见。
    if declared == "environment_dependency" and not stages.get("execute", True):
        return declared, declared_reason, f"未通过：{failed}（依赖缺失）"
    if declared == "invalid_fixture":
        return declared, declared_reason, f"未通过：{failed}"
    return "product_bug", declared_reason or "", f"未通过：{failed}"


def product_bug_stage(stages: dict, expected: dict) -> str:
    """product_bug 记在**第一个**没过的阶段上。

    「execute 崩了所以 export 也失败」重复记账会让报告读起来像塌方，
    而真正要修的只有最前面那一个。
    """
    for s in CC.STAGES:
        if s in stages and not stages[s] and expected.get(s, True):
            return s
    return ""


# --------------------------------------------------------------------------
# 一个 case 组的完整跑法
# --------------------------------------------------------------------------
def run_group(group: list[dict], *, python: str, root: Path, out_dir: Path,
              want_fidelity: bool, want_browser: bool) -> dict:
    """同一个脚本的所有 case 共用**一次 build**（两条 worker：热 + 全新）。

    分开跑等于把 corpus 的耗时乘以 stem 数，而 build 才是大头。
    stem 之间互不影响：一个 FigState 一张图，全量列表语义只作用于本 stem。
    """
    from tavotto.engine import pool

    results = {ca["id"]: _blank(ca) for ca in group}
    workdir = Path(tempfile.mkdtemp(prefix="compat-", dir=str(root)))
    project, script_rel, declared_entry = materialize(group, workdir)
    lead = group[0]
    t0 = time.perf_counter()

    hot = fresh = None
    try:
        # ── 发现 ────────────────────────────────────────────────
        disc = stage_discover(project, script_rel, lead)
        entry = disc["entry"] or declared_entry
        for ca in group:
            r = results[ca["id"]]
            r["stages"]["discover"] = bool(disc["ok"])
            r["detail"]["discover"] = disc["detail"]
        if not disc["ok"] and lead["expected"].get("discover", True):
            return _finish(results, group, t0)

        # ── 执行 + 捕获 ──────────────────────────────────────────
        try:
            hot = _fresh_worker(script_rel, project, entry)
            built = hot.ensure_built()
            stems = built.get("stems") or {}
            # 计时**只记录、不设阈值**：性能回归归 scripts/ci/benchmark.py。
            # 这里留着是为了发现异常 case（一个 30 秒的 build 说明 corpus 里
            # 混进了不确定性或真实数据量，那种 case 迟早会变成偶发超时）。
            t = built.get("timings") or {}
            exec_ok, exec_detail = True, {
                "stems": sorted(stems),
                "dropped": built.get("dropped_figures", 0),
                "script_exec_ms": t.get("script_exec_ms"),
                "build_ms": t.get("script_build_ms")}
        except Exception as exc:                        # noqa: BLE001
            exec_ok = False
            stems = {}
            exec_detail = {"error": str(exc)[:600],
                           "traceback": getattr(exc, "traceback_text", "")[-1500:]}
        for ca in group:
            r = results[ca["id"]]
            r["stages"]["execute"] = exec_ok
            r["detail"]["execute"] = exec_detail
            if exec_ok:
                got = len(stems)
                want = ca["expected_figures"]
                r["stages"]["capture"] = (ca["stem"] in stems) and got == want
                r["detail"]["capture"] = {
                    "want_figures": want, "got_figures": got,
                    "want_stem": ca["stem"], "stems": sorted(stems),
                    "source": (stems.get(ca["stem"]) or {}).get("source", "")}
        if not exec_ok:
            return _finish(results, group, t0)

        # ── 打开 / 语义 / 编辑 / 重放 / 导出 ─────────────────────
        for ca in group:
            r = results[ca["id"]]
            if not r["stages"].get("capture"):
                continue
            stem = ca["stem"]
            try:
                opened = hot.override(stem, [])
            except Exception as exc:                    # noqa: BLE001
                r["stages"]["open"] = False
                r["detail"]["open"] = {"error": str(exc)[:600]}
                continue
            base_man = opened["manifest"]
            ot = opened.get("timings") or {}
            r["stages"]["open"] = bool(base_man.get("elements"))
            r["detail"]["open"] = {"elements": len(base_man.get("elements", [])),
                                   "warnings": opened.get("warnings") or [],
                                   "size_mm": base_man.get("size_mm"),
                                   "render_ms": ot.get("total_ms")}
            if not r["stages"]["open"]:
                continue

            sem = stage_semantic(base_man, ca)
            r["stages"]["semantic"] = sem["ok"]
            r["detail"]["semantic"] = sem["detail"]
            # 桌面侧的完整可编辑集合——对拍要用它比浏览器那份（见
            # `_browser_verdict`）。存在 detail 里而不是当场比，是因为浏览器
            # 那一趟晚得多（整组 case 跑完才发一次驱动）。
            r["detail"]["semantic"]["editable_all"] = sorted(
                f"{el['gid']}.{f['prop']}"
                for el in base_man.get("elements", [])
                for f in el.get("editable", []))

            # 保真度必须在**任何编辑之前**量：零 patch 的定义就是「还没动过」。
            # 放在 edit/还原之后的话，一个还原不干净的 case 会把自己的污染
            # 算成「Tavotto 偷偷改了用户的图」——两件事得分开报。
            if want_fidelity and ca["expected"].get("fidelity", True):
                try:
                    fid = stage_fidelity(python, project, script_rel, entry,
                                         hot, stem, workdir, out_dir / "fidelity")
                    r["stages"]["fidelity"] = fid["ok"]
                    r["detail"]["fidelity"] = fid["detail"]
                except Skip as exc:
                    r["skipped"]["fidelity"] = str(exc)
            elif not ca["expected"].get("fidelity", True):
                r["skipped"]["fidelity"] = (
                    ca.get("expected_false_reasons") or {}).get("fidelity", "")
            else:
                r["skipped"]["fidelity"] = "本次运行没有开保真度比对"

            try:
                edit = stage_edit(hot, stem, ca, base_man)
                r["stages"]["edit"] = edit["ok"]
                r["detail"]["edit"] = edit["detail"]
                patches = edit["full_patches"]
            except Skip as exc:
                r["skipped"]["edit"] = str(exc)
                patches = []
            except Exception as exc:                    # noqa: BLE001
                r["stages"]["edit"] = False
                r["detail"]["edit"] = {"error": str(exc)[:600]}
                patches = []

            if patches:
                if fresh is None:
                    fresh = _fresh_worker(script_rel, project, entry)
                try:
                    rep = stage_replay(hot, fresh, stem, patches)
                    r["stages"]["replay"] = rep["ok"]
                    r["detail"]["replay"] = rep["detail"]
                except Exception as exc:                # noqa: BLE001
                    r["stages"]["replay"] = False
                    r["detail"]["replay"] = {"error": str(exc)[:600]}
            else:
                r["skipped"]["replay"] = "没有编辑目标，无从比较重放"
            hot.override(stem, [])                      # 回零，别影响导出

            exp = stage_export(hot, stem, workdir / "exports",
                               ca.get("export_formats") or ["pdf", "png"])
            r["stages"]["export"] = exp["ok"]
            r["detail"]["export"] = exp["detail"]

            # 还原不干净的 case 会把脏状态留给**同一个脚本的下一个 stem**
            # （一个脚本的所有 stem 共用这一条热会话）。同组里的 case 必须
            # 互相独立，否则报告会随清单顺序变——那种失败最难查。
            if r["detail"].get("edit", {}).get("restore_failures") or \
                    r["detail"].get("edit", {}).get("restore_warnings"):
                r["dirty"] = True
                pool.discard(hot)
                hot = _fresh_worker(script_rel, project, entry)

        # ── artist 普查（纯诊断，不参与 pass/fail）─────────────
        census = run_driver(python, "census", {
            "script": str(project / script_rel), "project": str(project),
            "engine_dir": str(ENGINE_DIR), "entry": entry,
            "sandbox": str(workdir / "census-sandbox")})
        if census.get("ok"):
            for ca in group:
                data = (census.get("census") or {}).get(ca["stem"])
                if data:
                    results[ca["id"]]["census"] = data

        # ── 浏览器语义对拍 ──────────────────────────────────────
        if want_browser:
            eligible = [ca for ca in group if ca.get("browser_eligible")]
            if eligible:
                probes = {ca["stem"]: [{"gid": m["gid"], "prop": m["prop"],
                                        "value": m["value"]}
                                       for m in (ca.get("mutations") or [])]
                          for ca in eligible}
                br = run_driver(python, "browser", {
                    "script": str(project / script_rel),
                    "engine_dir": str(ENGINE_DIR),
                    "workspace": str(workdir / "browser-ws"),
                    "patch_probe": probes})
                for ca in eligible:
                    results[ca["id"]]["browser"] = _browser_verdict(
                        ca, br, results[ca["id"]])
    finally:
        for w in (hot, fresh):
            if w is not None:
                pool.discard(w)
        shutil.rmtree(workdir / "project", ignore_errors=True)
        shutil.rmtree(workdir / "native", ignore_errors=True)
        shutil.rmtree(workdir / "census-sandbox", ignore_errors=True)
        shutil.rmtree(workdir / "browser-ws", ignore_errors=True)
        shutil.rmtree(workdir / "exports", ignore_errors=True)
        shutil.rmtree(workdir, ignore_errors=True)
    return _finish(results, group, t0)


def _browser_verdict(case: dict, br: dict, desktop: dict) -> dict:
    """桌面 vs 浏览器：只比语义，不比像素。

    字体栈、matplotlib 版本、WASM 后端都会造成合理的像素差异；语义
    （捕获了几张、有哪些角色、可编辑什么、patch 哈希）随入口改变才是事故。
    """
    if not br.get("ok"):
        return {"ok": False, "reason": "browser 驱动失败", "driver": br}
    stem = case["stem"]
    figures = br.get("figures") or []
    sem = (br.get("semantics") or {}).get(stem)
    # 浏览器把源文件名收紧过（`_safe_script_name`），stem 可能不同名——
    # 数量对上、角色对上就够；对不上一律如实报，不做名字上的猜测匹配。
    if sem is None:
        return {"ok": False,
                "reason": f"浏览器没有捕获到 {stem}（捕获到 {figures}）",
                "figures": figures}
    # **捕获到几张也要比，而且要比截断。** 上面那句「数量对上、角色对上就够」
    # 说的是「不按名字猜测匹配」，可代码里 `figures` 只在 stem 缺失那一支用过
    # ——数量从来没真的比过。这不是假想：`MAX_FIGURES` 在两侧作用的对象不同，
    #
    #   桌面   `collect_pyplot_figures(limit=8)` 只截**待补的 pyplot 兜底**，
    #          savefig 认领的那些不受限 → 8 张 savefig + 1 张 show-only = 9
    #   浏览器 `browser.py` 截的是**总数** → 同一个脚本只剩 8 张
    #
    # 于是保留下来的那张显式 stem 角色与可编辑属性完全一致、对拍照报成功，
    # 而两个入口给用户的图数不同。`browser.py` 第 71 行那句「两个入口捕获到的
    # 图数因此不会分叉」正是被这条证伪的。
    #
    # 截断单独报：它与「少捕获了一张」原因不同（一个是上限、一个是能力差），
    # 混成一句话会让人查错方向。
    desktop_stems = list((desktop.get("detail", {}).get("capture") or {})
                         .get("stems") or [])
    count_reasons = []
    if desktop_stems and len(figures) != len(desktop_stems):
        count_reasons.append(
            f"捕获张数不一致：桌面 {len(desktop_stems)}（{desktop_stems}）"
            f" vs 浏览器 {len(figures)}")
    want_figs = case.get("expected_figures")
    if want_figs is not None and len(figures) != want_figs:
        count_reasons.append(f"浏览器捕获 {len(figures)} 张，清单期望 {want_figs} 张")
    if br.get("truncated"):
        count_reasons.append(f"浏览器截断了 {br['truncated']} 张（MAX_FIGURES 上限）")

    dsem = desktop.get("detail", {}).get("semantic") or {}
    desktop_roles = set(dsem.get("roles") or [])
    browser_roles = set(sem.get("roles") or [])
    role_diff = sorted(desktop_roles.symmetric_difference(browser_roles))

    # **可编辑属性集合也要比。** 文档从第一版起就写着对拍覆盖「可编辑属性」，
    # 而代码只比了角色与哈希——浏览器侧多出或少掉任何一个属性、只要角色不变，
    # 这条就报成功。文档说的和代码做的不是一回事，比两边都不做更坏。
    desktop_edit = set(dsem.get("editable_all") or [])
    browser_edit = set(sem.get("editable") or [])
    edit_diff = sorted(desktop_edit.symmetric_difference(browser_edit))

    # patch 规范化只有**一份实现**（`engine/patchspec.py`，桌面 worker 与
    # Pyodide 平铺 import 的是同一个文件）。父进程算一遍、浏览器侧算一遍，
    # 两个数必须逐字相同——这条断言看住的正是「有没有人在某一侧另写了一份」。
    from tavotto.engine import patchspec
    hash_ok = True
    want_hash = ""
    mutations = [{"gid": m["gid"], "prop": m["prop"], "value": m["value"]}
                 for m in (case.get("mutations") or [])]
    if mutations:
        want_hash = patchspec.patch_hash(mutations)
        hash_ok = (bool(sem.get("apply_ok"))
                   and not sem.get("apply_warnings")
                   and sem.get("applied_patch_hash") == want_hash)
    reasons = list(count_reasons)
    if role_diff:
        reasons.append(f"角色不一致：{role_diff}")
    if edit_diff:
        reasons.append(f"可编辑属性不一致（{len(edit_diff)} 处）：{edit_diff[:8]}")
    if not hash_ok:
        reasons.append(
            f"patch 应用不一致：hash {sem.get('applied_patch_hash')!r} vs "
            f"{want_hash!r}，warnings={sem.get('apply_warnings')}")
    return {"ok": not reasons,
            "reason": "；".join(reasons),
            "figures": figures,
            "desktop_stems": desktop_stems,
            "truncated": br.get("truncated", 0),
            "roles_only_desktop": sorted(desktop_roles - browser_roles),
            "roles_only_browser": sorted(browser_roles - desktop_roles),
            "editable_only_desktop": sorted(desktop_edit - browser_edit)[:20],
            "editable_only_browser": sorted(browser_edit - desktop_edit)[:20],
            "patch_hash": sem.get("patch_hash", ""),
            "applied_patch_hash": sem.get("applied_patch_hash", ""),
            "expected_patch_hash": want_hash,
            "apply_warnings": sem.get("apply_warnings") or []}


def _blank(case: dict) -> dict:
    return {"id": case["id"], "category": case["category"], "tier": case["tier"],
            "stages": {}, "detail": {}, "skipped": {}, "census": {},
            "browser": None, "classification": "", "reason": "", "follow_up": "",
            "duration_ms": 0}


def _finish(results: dict, group: list[dict], t0: float) -> dict:
    ms = round((time.perf_counter() - t0) * 1000, 1)
    for case in group:
        r = results[case["id"]]
        r["duration_ms"] = ms
        cls, reason, detail = classify(case, r["stages"], r["skipped"])
        r["classification"] = cls
        r["reason"] = reason
        r["detail_note"] = detail
        if cls == "product_bug":
            r["stage"] = product_bug_stage(r["stages"], case.get("expected", {}))
            r["follow_up"] = str(case.get("follow_up", "")).strip()
    return results


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def funnel(cases: list[dict], results: dict) -> list[dict]:
    """九级漏斗。分母是「本该跑到这一级的 case 数」，不是全部 case——
    execute 就崩了的 case 不该被算进 export 的分母，那会让后面几级的数字
    互相污染，看报告的人分不清「没跑」和「跑了没过」。"""
    rows = []
    for stage in CC.STAGES:
        total = sum(1 for c in cases if stage in results[c["id"]]["stages"])
        passed = sum(1 for c in cases if results[c["id"]]["stages"].get(stage))
        skipped = sum(1 for c in cases if stage in results[c["id"]]["skipped"])
        rows.append({"stage": stage, "label": CC.STAGE_LABELS[stage],
                     "passed": passed, "total": total, "skipped": skipped,
                     "rate": round(passed / total, 4) if total else None})
    return rows


def artist_census(results: dict) -> list[dict]:
    """Top 未识别 / 部分识别的 artist 类。

    它同时是 Tavotto 的产品路线图：高频 + 实现简单 + 属于产品语义的那些
    才值得补支持。**不要预先猜**——先让 corpus 的数据说话。
    """
    total: dict[str, int] = {}
    recognized: dict[str, int] = {}
    cases_with: dict[str, set] = {}
    for cid, r in results.items():
        c = r.get("census") or {}
        for cls, n in (c.get("total") or {}).items():
            total[cls] = total.get(cls, 0) + n
            recognized.setdefault(cls, 0)
            cases_with.setdefault(cls, set()).add(cid)
        for cls, n in (c.get("recognized") or {}).items():
            recognized[cls] = recognized.get(cls, 0) + n
    rows = []
    for cls, n in total.items():
        rec = recognized.get(cls, 0)
        if rec >= n:
            continue                                    # 全认得，不是缺口
        rows.append({"artist": cls, "instances": n, "recognized": rec,
                     "unrecognized": n - rec, "cases": len(cases_with[cls])})
    rows.sort(key=lambda r: (-r["unrecognized"], -r["cases"], r["artist"]))
    return rows


def build_report(cases: list[dict], results: dict, target: dict,
                 target_name: str, mode: str) -> dict:
    by_cls: dict[str, list[str]] = {}
    for c in cases:
        by_cls.setdefault(results[c["id"]]["classification"], []).append(c["id"])
    bugs = [{"id": cid, "stage": results[cid].get("stage", ""),
             "tier": results[cid]["tier"],
             "follow_up": results[cid].get("follow_up", ""),
             "detail": results[cid].get("detail_note", "")}
            for cid in by_cls.get("product_bug", [])]
    parity = [{"id": cid, **(results[cid]["browser"] or {})}
              for cid in sorted(results)
              if results[cid].get("browser") is not None]
    return {
        "schema": 1,
        # 时间戳只进**报告**，绝不进 committed 基线。
        # 报告身份：汇总按它判「这份是不是本轮的」。缺了的话本轮真跑出来的
        # CompatBench 会被当成上一轮的陈旧报告拒收（#61 的 review 逮到）。
        "metadata": run_metadata(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "target": target_name,
        "target_versions": target,
        "summary": {
            "cases": len(cases),
            "funnel": funnel(cases, results),
            "classification": {k: len(v) for k, v in sorted(by_cls.items())},
            "product_bugs": bugs,
            "worker_crashes": sum(
                1 for c in cases
                if "worker" in str(results[c["id"]]["detail"]
                                   .get("execute", {}).get("error", "")).lower()
                and not results[c["id"]]["stages"].get("execute")),
        },
        # 性能只作**记录**，不设门禁（那是 scripts/ci/benchmark.py 的活）。
        # 它回答的是「有没有哪个 case 慢得不正常」——corpus 里混进真实数据量
        # 或不确定性的表现就是某一条突然要几十秒。
        "slowest": [{"id": results[cid]["id"],
                     "duration_ms": results[cid].get("duration_ms", 0),
                     "script_exec_ms": (results[cid].get("detail") or {})
                     .get("execute", {}).get("script_exec_ms")}
                    # 并列时按 id 兜底排序：`sorted` 是稳定的，只按耗时排会让
                    # 顺序跟着 dict 的插入序走，报告就不再可 diff。
                    for cid in sorted(
                        results,
                        key=lambda c: (-results[c].get("duration_ms", 0), c))[:10]],
        "artist_census": artist_census(results),
        "browser_parity": parity,
        # 报告必须可 diff：case 一律按 id 排序。
        "cases": [results[cid] for cid in sorted(results)],
    }


def render_summary(report: dict) -> str:
    s = report["summary"]
    out = [f"\n## Matplotlib Compatibility · {report['target']} · {s['cases']} cases\n",
           "| Stage | Pass | Total | Rate |", "|---|---:|---:|---:|"]
    for row in s["funnel"]:
        rate = "—" if row["rate"] is None else f"{row['rate'] * 100:.1f}%"
        out.append(f"| {row['label']} | {row['passed']} | {row['total']} | {rate} |")
    out.append("\n| Classification | Cases |\n|---|---:|")
    for cls in CC.CLASSIFICATIONS:
        n = s["classification"].get(cls, 0)
        if n:
            out.append(f"| {cls} | {n} |")
    if s["product_bugs"]:
        out.append("\n### Product bugs\n")
        for b in s["product_bugs"]:
            out.append(f"- `{b['id']}` — {b['stage'] or '?'} "
                       f"（tier {b['tier']}）{b['detail']}")
    else:
        out.append("\n**Product bugs: 0**\n")
    census = report["artist_census"][:10]
    if census:
        out.append("\n### Top unrecognized / partially recognized artists\n")
        out.append("| Artist | Unrecognized | Instances | Cases |")
        out.append("|---|---:|---:|---:|")
        for row in census:
            out.append(f"| {row['artist']} | {row['unrecognized']} | "
                       f"{row['instances']} | {row['cases']} |")
    bad_parity = [p for p in report["browser_parity"] if not p.get("ok")]
    if bad_parity:
        out.append("\n### Browser / Desktop semantic divergence\n")
        for p in bad_parity[:10]:
            out.append(f"- `{p['id']}` — {p.get('reason') or p}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# 门禁
# --------------------------------------------------------------------------
#: 各档要求。`product_bugs` 是**上限**，`tier1_*` 是必须为 100% 的阶段。
GATES = {
    "pr":      {"tier1_stages": ("execute", "capture", "open"),
                "product_bugs": 0, "allow_baseline_bugs": True},
    "main":    {"tier1_stages": ("execute", "capture", "open", "export", "edit"),
                "product_bugs": 0, "allow_baseline_bugs": True},
    "nightly": {"tier1_stages": ("execute", "capture", "open", "export", "edit",
                                 "replay"),
                "product_bugs": 0, "allow_baseline_bugs": True},
    "release": {"tier1_stages": ("execute", "capture", "open", "export", "edit",
                                 "replay", "fidelity"),
                "product_bugs": 0, "allow_baseline_bugs": False},
}


def evaluate_gate(gate: str, cases: list[dict], results: dict,
                  baseline: dict | None) -> tuple[bool, list[str]]:
    """门禁判定。返回 (通过?, 失败理由)。

    分三条，每条都对应一种「benchmark 会退化成摆设」的方式：

    1. **Tier 1 的指定阶段必须 100%** —— 那是标准 matplotlib 的高频路径；
    2. **新出现的 product_bug 一律红** —— 已经在基线里的那些属于「看住」，
       但 `release` 档连它们都不放过（1.0 的 exit rule 是 P0 = 0）；
    3. **分类比基线变差就红** —— 把 case 从 `full_support` 改成
       `unsupported_by_design` 让 CI 变绿，是这里唯一真正想拦的作弊；
    4. **桌面/浏览器语义分叉一律红**，不分档位。

    第 4 条是补上的：对拍结果原本只写进 `results[cid]["browser"]`，报告里
    打出一节「Browser / Desktop semantic divergence」，然后**门禁照常放行**
    ——`_finish()` 只从 `stages` 分类，`evaluate_gate()` 只看 stages 与
    classification，两处都够不着它。一个把分叉打印出来、然后说「通过」的
    门禁，比不检查更坏：它让人以为这件事有人看着。

    不分档位是有意的：同一份脚本在两个产品入口给出不同语义，本身就是缺陷，
    没有「PR 档可以先放过」的版本。
    """
    spec = GATES[gate]
    fails: list[str] = []

    for stage in spec["tier1_stages"]:
        bad = [c["id"] for c in cases
               if c["tier"] == "must"
               and c["expected"].get(stage, True)
               and stage in results[c["id"]]["stages"]
               and not results[c["id"]]["stages"][stage]]
        if bad:
            fails.append(f"Tier 1 的 {CC.STAGE_LABELS[stage]} 不是 100%：{bad}")

    known = set()
    if baseline:
        known = {cid for cid, e in baseline.get("cases", {}).items()
                 if e.get("classification") == "product_bug"}
    bugs = [c["id"] for c in cases
            if results[c["id"]]["classification"] == "product_bug"]
    new_bugs = [b for b in bugs if b not in known]
    if new_bugs:
        fails.append(f"新出现的 product_bug：{new_bugs}")
    if bugs and not spec["allow_baseline_bugs"]:
        fails.append(f"{gate} 档不接受任何 product_bug（含基线里已知的）：{bugs}")

    # 桌面/浏览器语义分叉：任何档位一律红（见 docstring 第 4 条）
    parity_bad = [c["id"] for c in cases
                  if (results[c["id"]].get("browser") or {}).get("ok") is False]
    if parity_bad:
        detail = []
        for cid in parity_bad[:5]:
            why = (results[cid].get("browser") or {}).get("reason") or "见报告"
            detail.append(f"{cid}（{why}）")
        fails.append("桌面/浏览器语义分叉：" + "；".join(detail))

    if baseline:
        rank = {c: i for i, c in enumerate(
            ("full_support", "partial_support", "environment_dependency",
             "unsupported_by_design", "invalid_fixture", "product_bug"))}
        for c in cases:
            was = baseline.get("cases", {}).get(c["id"], {}).get("classification")
            now = results[c["id"]]["classification"]
            if was and rank.get(now, 99) > rank.get(was, 99):
                fails.append(f"{c['id']} 比基线退步：{was} → {now}")
    return (not fails), fails


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _worker_python(explicit: str | None) -> str:
    """确定渲染解释器，并**让 worker 池也真的用它**。

    这里必须写 `TAVOTTO_WORKER_PYTHON`（池的最高优先级覆盖），不能只把路径
    传给旁路驱动：`pool.one_shot()` 自己走探测链，不写这个变量的话
    `--target bundled --python <bundled venv>` 会变成「对照组跑 3.11.1、
    Tavotto 跑机器上碰巧装着的 3.10.8」——两侧版本不同，保真度全线飘红，
    而报告标着 target=bundled。这条坑是真撞到过的：整轮 149 个 case 全部
    只有文字部分对不上（同一套矢量、不同版本的字体度量）。
    """
    from tavotto.engine import pool
    chosen = explicit or pool.find_worker_python()
    os.environ["TAVOTTO_WORKER_PYTHON"] = chosen
    pool.reset_worker_python()
    # 复核一遍：写完之后池选出来的必须就是它，否则后面所有数字都在骗人。
    picked, source = pool.select_worker_python()
    if not pool.same_python(picked, chosen):
        raise RuntimeError(
            f"渲染池没有采纳指定的解释器：要 {chosen}，它选了 {picked}"
            f"（来源 {source}）")
    return chosen


#: 版本核对只看这几个——它们决定 manifest 的结构与像素。`pyodide` /
#: `python` 之类的信息性字段不参与（渲染解释器本来就是 CPython）。
_VERSION_KEYS = ("matplotlib", "numpy", "pandas", "scipy", "seaborn", "pillow")

_VERSION_PROBE = """
import json
out = {}
for name, mod in (("matplotlib", "matplotlib"), ("numpy", "numpy"),
                  ("pandas", "pandas"), ("scipy", "scipy"),
                  ("seaborn", "seaborn"), ("pillow", "PIL")):
    try:
        m = __import__(mod)
    except ImportError:
        continue
    out[name] = getattr(m, "__version__", "")
print(json.dumps(out))
"""


def probe_versions(python: str) -> dict:
    """问渲染解释器它到底装了什么。"""
    out = subprocess.run([python, "-c", _VERSION_PROBE], capture_output=True,
                         text=True, timeout=180, stdin=subprocess.DEVNULL)
    try:
        return json.loads((out.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def check_python_version(target: dict, actual_python: str) -> list[str]:
    """target 钉的 Python 版本与实际跑的对不对得上（只比 major.minor）。

    **这条是我自己栽过的**：`matrix.json` 的 minimum 档钉着 `python: "3.10"`，
    而我拿一个 **3.11** 的 venv 跑完了整档、报告标着 `target: minimum` 交了
    出去。包版本核对通过（matplotlib 3.8.4 装对了），Python 版本却没人比。

    代价是实打实的：3.10 的 `pathlib` 在**类定义时**就把 `io.open` 绑进了
    `_NormalAccessor`，`Path.read_text()` 因此绕过 monkeypatch——这个只在
    3.10 上张开的缺口，正因为我跑的是 3.11，一路绿到 CI 才红。
    """
    want = str(target.get("python") or "")
    if not want or not actual_python:
        return []
    want_mm = ".".join(want.split(".")[:2])
    got_mm = ".".join(str(actual_python).split(".")[:2])
    if want_mm != got_mm:
        return [f"python: 期望 {want_mm}.x，实际 {actual_python}"]
    return []


def check_target_versions(target: dict, actual: dict) -> list[str]:
    """target 声明的版本与实际装的对不上 → 返回不符项。

    **不核对就等于撒谎**：一份标着 `target: bundled` 的报告，实际跑的却是
    机器上碰巧装着的另一个 matplotlib，比没有报告更坏——它会被当成
    「内置 runtime 上验过了」。缺包不算不符（那由 case 的
    `environment_dependency` 分类如实记账），只有**装了但版本不同**才算。
    """
    bad = []
    for key in _VERSION_KEYS:
        want = target.get(key)
        got = actual.get(key)
        if want and got and str(got) != str(want):
            bad.append(f"{key}: 期望 {want}，实际 {got}")
    return bad


def _target_env(target: dict) -> list[str]:
    """把 target 的版本要求折成人能读的一行。"""
    return [f"{k}={v}" for k, v in sorted(target.items())
            if k not in ("source", "required", "subset", "note", "runner")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Tavotto Matplotlib CompatBench —— 兼容性资格验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  %(prog)s --smoke                 PR 档的最小子集\n"
               "  %(prog)s --all                   全量 corpus\n"
               "  %(prog)s --target bundled        指定版本目标\n"
               "  %(prog)s --case shape_pyplot_show_only\n"
               "  %(prog)s --all --update-baseline 重建基线（CI 里被硬拒）\n")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--smoke", action="store_true",
                       help="只跑 smoke 子集（PR 档，覆盖全部 Tier 1 维度）")
    scope.add_argument("--all", action="store_true", help="跑全部 case")
    scope.add_argument("--case", default=None,
                       help="只跑指定 case（逗号分隔）")
    ap.add_argument("--tier", default=None,
                    help="按档位过滤：must,expected,exploratory")
    ap.add_argument("--category", default=None, help="按类别过滤（逗号分隔）")
    ap.add_argument("--target", default="current",
                    help="版本目标（见 tests/compat/matrix.json）")
    ap.add_argument("--python", default=None,
                    help="渲染解释器（默认走 pool 的探测链）")
    ap.add_argument("--gate", default="nightly", choices=sorted(GATES),
                    help="门禁档位（默认 nightly）")
    ap.add_argument("--out", default=None, help="产物目录（报告与失败 diff）")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="compat-report.json 的落点")
    ap.add_argument("--fidelity", dest="fidelity", action="store_true",
                    default=None, help="强制开启零 patch 原生保真度比对")
    ap.add_argument("--no-fidelity", dest="fidelity", action="store_false",
                    help="跳过保真度比对（省一半时间）")
    ap.add_argument("--browser", action="store_true",
                    help="对 browser_eligible 的 case 做桌面/浏览器语义对拍")
    ap.add_argument("--update-baseline", action="store_true",
                    help="重建 tests/compat/baseline.json。**CI 里被硬拒**"
                         "——基线必须由人逐条读过再提交")
    ap.add_argument("--list", action="store_true", help="只列出选中的 case")
    args = ap.parse_args(argv)

    # 硬拦截。「case 红了 → 自动更新基线 → 报绿」是这套东西唯一致命的退化方式，
    # 而且它会一直报平安。
    if args.update_baseline and os.environ.get("CI") == "true":
        print("::error::--update-baseline 不允许在 CI 环境使用："
              "基线必须由人在本地生成、逐条读过、经 code review 之后提交",
              file=sys.stderr)
        return 2

    try:
        manifest = CC.load_manifest()
        matrix = CC.load_matrix()
        target = CC.resolve_target(matrix, args.target)
    except CC.CorpusError as exc:
        print(f"::error::{exc.message}", file=sys.stderr)
        return 2

    # 默认档：显式挑 case 或指定档位时跑那些；否则 smoke。
    want_smoke = args.smoke or not (args.all or args.case or args.tier
                                    or args.category)
    try:
        cases = CC.select(
            manifest["cases"],
            ids=[c.strip() for c in args.case.split(",")] if args.case else None,
            tiers=[t.strip() for t in args.tier.split(",")] if args.tier else None,
            categories=[c.strip() for c in args.category.split(",")]
            if args.category else None,
            smoke=want_smoke)
    except CC.CorpusError as exc:
        print(f"::error::{exc.message}", file=sys.stderr)
        return 2
    # target 声明了只跑某个子集就照做（`matrix.json` 的 `subset`）。
    # 不照做的后果不是「多跑几条」而是**说假话**：browser 那一档会跑满 149 条
    # 桌面脚本，而 workflow 注释与文档都写着它跑的是 12 条对拍子集。
    subset = target.get("subset")
    if subset == "browser_eligible":
        before = len(cases)
        cases = [c for c in cases if c.get("browser_eligible")]
        print(f"target {args.target!r} 声明只跑 browser_eligible 子集："
              f"{before} → {len(cases)} 个 case")
    if not cases:
        print("::error::选出来一个 case 都没有", file=sys.stderr)
        return 2

    if args.list:
        for c in cases:
            print(f"{c['id']:<40} {c['tier']:<12} {c['category']}")
        print(f"\n合计 {len(cases)} 个 case")
        return 0

    mode = ("smoke" if want_smoke else
            "case" if args.case else "tier" if args.tier else "all")
    # 保真度默认只在全量档开：它要为每个脚本多起一个进程、多渲一遍图。
    want_fidelity = args.fidelity if args.fidelity is not None else (mode == "all")

    try:
        python = _worker_python(args.python)
    except Exception as exc:                            # noqa: BLE001
        print(f"::error::渲染解释器不可用：{exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else Path(
        tempfile.mkdtemp(prefix="compat-report-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="compat-run-"))

    print(f"CompatBench · {mode} · target={args.target} "
          f"({', '.join(_target_env(target)) or '当前环境'})")
    print(f"渲染解释器：{python}")
    actual = probe_versions(python)
    print(f"实际装的：{', '.join(f'{k}={v}' for k, v in sorted(actual.items()))}")
    import platform as _pf
    py_actual = subprocess.run(
        [python, "-c", "import platform;print(platform.python_version())"],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace").stdout.strip() or _pf.python_version()
    print(f"渲染解释器版本：Python {py_actual}")
    mismatch = (check_target_versions(target, actual)
                + check_python_version(target, py_actual))
    if mismatch:
        for line in mismatch:
            print(f"::error::target {args.target} 的版本对不上——{line}",
                  file=sys.stderr)
        print(f"::error::一份标着 target={args.target} 却跑在别的版本上的报告，"
              f"比没有报告更坏。装对版本再跑，或者用 --target current。",
              file=sys.stderr)
        return 2
    target = {**target, "actual": {**actual, "python": py_actual}}
    print(f"case {len(cases)} 个，分 {len(CC.group_by_project(cases))} 组构建\n",
          flush=True)

    results: dict = {}
    try:
        for i, group in enumerate(CC.group_by_project(cases), 1):
            names = ", ".join(c["id"] for c in group)
            print(f"[{i}] {names}", flush=True)
            try:
                results.update(run_group(
                    group, python=python, root=scratch, out_dir=out_dir,
                    want_fidelity=want_fidelity, want_browser=args.browser))
            except Exception:                           # noqa: BLE001
                # runner 自己崩了也要如实记账，绝不让整轮消失。
                tb = traceback.format_exc()
                for ca in group:
                    r = _blank(ca)
                    r["stages"]["discover"] = False
                    r["detail"]["runner_error"] = tb[-2000:]
                    r["classification"] = "product_bug"
                    r["stage"] = "discover"
                    r["reason"] = "runner 在这个 case 上崩溃"
                    r["follow_up"] = "读 detail.runner_error 里的 traceback"
                    results[ca["id"]] = r
            for ca in group:
                r = results[ca["id"]]
                mark = {"full_support": "✅", "partial_support": "🟡",
                        "unsupported_by_design": "⚪",
                        "environment_dependency": "🌤",
                        "product_bug": "❌", "invalid_fixture": "🛠"}.get(
                            r["classification"], "?")
                print(f"    {mark} {ca['id']:<38} {r['classification']}"
                      f"{'  ' + r.get('detail_note', '') if r.get('detail_note') else ''}",
                      flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    report = build_report(cases, results, target, args.target, mode)
    json_path = Path(args.json_out) if args.json_out else out_dir / "compat-report.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8", newline="\n")

    if args.update_baseline:
        payload = CC.baseline_payload(
            results, {"target": args.target, **{k: v for k, v in actual.items()}})
        CC.validate_baseline(payload)
        CC.write_baseline(payload)
        print(f"\n基线已重建：{CC.BASELINE_PATH}（{len(payload['cases'])} 条）")
        print("逐条读一遍再提交——尤其是 partial / unsupported / product_bug。")
        _emit_summary(report)
        return 0

    try:
        baseline = CC.load_baseline()
    except CC.CorpusError as exc:
        print(f"::error::{exc.message}", file=sys.stderr)
        _emit_summary(report)
        return 1

    gen = baseline.get("generated_for") or {}
    drift = [f"{k}: 基线 {gen[k]} vs 现在 {actual.get(k)}"
             for k in _VERSION_KEYS
             if gen.get(k) and actual.get(k) and gen[k] != actual[k]]
    if drift:
        # 只是提醒，不是失败：本地 `--target current` 拿全量基线做快速核对是
        # 正当用法。但「基线对不上」时得先知道是不是环境变了，别去查产品。
        print("\n注意：基线是在另一套科学栈上采的，分类差异未必来自产品改动：",
              file=sys.stderr)
        for line in drift:
            print(f"  · {line}", file=sys.stderr)
        print(f"  基线 target={gen.get('target')!r}，本次 target={args.target!r}",
              file=sys.stderr)

    delta = CC.diff_baseline(baseline, results)
    report["baseline_diff"] = delta
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")

    ok, fails = evaluate_gate(args.gate, cases, results, baseline)
    if delta["new"]:
        print(f"\n基线里没有这些 case（新加的？先跑一次 --update-baseline）："
              f"{delta['new']}", file=sys.stderr)
        ok = False
        fails.append(f"case 不在基线里：{delta['new']}")

    _emit_summary(report)
    if delta["changed"]:
        lines = ["\n### 与基线的差异\n"]
        for ch in delta["changed"][:20]:
            lines.append(f"- `{ch['id']}`：{ch['was']} → {ch['now']}"
                         + (f"，阶段 {ch['stages']}" if ch["stages"] else ""))
        _summary("\n".join(lines))
        print("\n".join(lines))

    for f in fails:
        print(f"::error::CompatBench 门禁（{args.gate}）：{f}", file=sys.stderr)
    print(f"\n报告：{json_path}")
    print(f"CompatBench：{'通过' if ok else '失败'}（门禁 {args.gate}）")
    return 0 if ok else 1


def _emit_summary(report: dict) -> None:
    _summary(render_summary(report))
    print(render_summary(report))


def _summary(text: str) -> None:
    """写 GitHub Step Summary；本地跑时什么都不做（正文另行 print）。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n")
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
