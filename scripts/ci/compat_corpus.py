#!/usr/bin/env python3
"""CompatBench 的语料层：清单 / 版本矩阵 / 基线的读取与校验。

**纯标准库**——它要在还没装科学栈的机器上跑得起来（`--help`、schema 校验、
基线 diff 都不需要 matplotlib）。

三个文件，各回答一个问题，谁也不许兼任：

* `tests/compat/manifest.json` —— **这个 case 为什么存在、期望是什么**。
  它是意图，由人写、由人 review。
* `tests/compat/matrix.json` —— **在哪几套 Python/matplotlib 上验**。
  版本号本身不写在这里，只写「去哪个锁文件读」——
  `packaging/runtime-lock.json` 与 `packaging/playground-runtime.json` 是唯一
  权威（CLAUDE.md 的「版本锁是唯一输入」），复制一份必然漂开。
* `tests/compat/baseline.json` —— **今天实际是什么样**。它是**观测**，
  由 `--update-baseline` 生成、由人逐条读过之后提交。

## 基线不是豁免名单

这是整套东西最容易退化的地方：case 红了 → 加进 expected_failures → CI 变绿 →
benchmark 从此只证明「我们接受现状」。所以：

* 任何非 `full_support` 的分类都**必须有非空 reason**（schema 强制）；
* `product_bug` 还必须有 `follow_up`（它是待修的缺陷，不是产品边界）；
* **Tier 1 的 product_bug 一律不许进基线**——那一档是标准 matplotlib 的
  高频路径，有 bug 就是发不了版；
* `--gate release` 下**任何** product_bug 都让门禁红。

分类只有六种（`CLASSIFICATIONS`），阶段只有九个（`STAGES`），两张表都是
闭集：写错一个字当场报错，绝不当成「一个我们没见过的新状态」放行。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COMPAT_DIR = REPO / "tests" / "compat"
MANIFEST_PATH = COMPAT_DIR / "manifest.json"
MATRIX_PATH = COMPAT_DIR / "matrix.json"
BASELINE_PATH = COMPAT_DIR / "baseline.json"
CASES_DIR = COMPAT_DIR / "cases"
ASSETS_DIR = COMPAT_DIR / "assets"

#: 兼容漏斗的九级。**顺序即依赖**：前一级不过，后面的一律 skip 而不是 fail
#: ——「execute 崩了所以 export 也失败」重复计数会让报告读起来像塌方。
STAGES = (
    "discover",
    "execute",
    "capture",
    "open",
    "semantic",
    "edit",
    "replay",
    "export",
    "fidelity",
)

#: 漏斗每一级在报告里的名字。
STAGE_LABELS = {
    "discover": "Discovery success",
    "execute": "Execution success",
    "capture": "Figure capture",
    "open": "Opened in Tavotto",
    "semantic": "Expected semantics recognized",
    "edit": "Editable targets successful",
    "replay": "Replay equivalent",
    "export": "Export successful",
    "fidelity": "Zero-patch fidelity",
}

#: 失败分类。闭集——PASS/FAIL 两分法会把「产品边界」与「我们的 bug」混成
#: 同一个数字，而那正是这套 benchmark 存在的理由。
CLASSIFICATIONS = (
    "full_support",  # 全绿
    "partial_support",  # 跑得起来、认得出来，但部分元素不可编辑
    "unsupported_by_design",  # 产品刻意不支持（数据/结构性修改回代码）
    "environment_dependency",  # 缺字体 / 缺可选包，不是引擎问题
    "product_bug",  # Tavotto 自己的缺陷，待修
    "invalid_fixture",  # case 自己写错了
)

#: 需要写明理由的分类（`full_support` 之外全都要）。
NEEDS_REASON = tuple(c for c in CLASSIFICATIONS if c != "full_support")

#: 还必须写明后续动作的分类。
NEEDS_FOLLOW_UP = ("product_bug",)

#: 门禁档位。Tier 1 是标准 matplotlib 的高频路径。
TIERS = ("must", "expected", "exploratory")

#: 发现方式。`manual_registry` 意味着「用户必须手写注册表」——
#: 本该自动发现的 case 落到这一档就是 bug，不是 pass。
DISCOVERY_MODES = ("discoverable", "requires_probe", "manual_registry")

#: **任何档位都不许声明「不期望通过」的三级。**
#: 跑不起来 / 捕获不到 / 打不开就是不兼容——那要记成 classification 让它
#: 出现在报告里，不能记成 `expected.execute=false` 让门禁静悄悄地放行。
NON_NEGOTIABLE_STAGES = ("execute", "capture", "open")

#: 产品路由（Session 6）。「worker 能直接调」不等于「真实用户能使用」——
#: case 可以在 `product_routes` 里声明哪些**产品入口**必须走得通，runner
#: 走真实端点/真实 CLI 验证（绝不直接调内部 probe 代表产品成功）。闭集。
PRODUCT_ROUTES = ("desktop_project", "cli_open", "safe_probe", "browser_playground", "native_run")

#: 路由声明的合法取值。`true` = 必须通过；两个字符串档是**如实记账**：
#: not_implemented（产品还没有这条路，如 native_run）/ not_applicable
#: （这条路对该 case 无意义，如非 browser_eligible 的 playground）。
ROUTE_EXPECTATIONS = (True, "not_implemented", "not_applicable")


class CorpusError(RuntimeError):
    """语料层的结构性错误。带稳定 code，调用方按它分诊。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _load(path: Path, what: str) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusError(f"{what}_missing", f"读不到 {path}：{exc}") from exc
    except ValueError as exc:
        raise CorpusError(f"{what}_invalid", f"{path} 不是合法 JSON：{exc}") from exc


# --------------------------------------------------------------------------
# 清单
# --------------------------------------------------------------------------
def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    """读清单并**当场全量校验**。校验放在读取里是有意的：任何入口拿到的
    都是已经验过的数据，没有「忘了调 validate」这条失效路径。"""
    data = _load(path, "manifest")
    validate_manifest(data, root=path.parent)
    return data


def validate_manifest(data: dict, root: Path = COMPAT_DIR) -> None:
    if data.get("schema") != 1:
        raise CorpusError("manifest_schema", f"清单 schema 必须是 1，实际 {data.get('schema')!r}")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CorpusError("manifest_empty", "清单里一个 case 都没有")

    seen: set[str] = set()
    for case in cases:
        cid = case.get("id")
        if not isinstance(cid, str) or not cid:
            raise CorpusError("case_id_missing", f"有 case 没有 id：{case!r}")
        if cid in seen:
            # 重复 id 的表现极其误导：报告里两条都在，基线只认一条，
            # 于是「明明修好了却还是红」。
            raise CorpusError("duplicate_case_id", f"case id 重复：{cid}")
        seen.add(cid)
        _validate_case(case, root)

    _validate_smoke_subset(cases)


def _validate_case(case: dict, root: Path) -> None:
    cid = case["id"]

    def bad(code: str, msg: str):
        raise CorpusError(code, f"{cid}: {msg}")

    if case.get("tier") not in TIERS:
        bad("unknown_tier", f"tier 非法 {case.get('tier')!r}（可选 {list(TIERS)}）")
    if case.get("discovery") not in DISCOVERY_MODES:
        bad(
            "unknown_discovery",
            f"discovery 非法 {case.get('discovery')!r}（可选 {list(DISCOVERY_MODES)}）",
        )

    script = case.get("script")
    if not isinstance(script, str) or not script:
        bad("script_missing", "没有 script 字段")
    if not (root / script).is_file():
        bad("script_not_found", f"脚本不存在：{script}")

    for extra in (case.get("extra_files") or {}).values():
        if not (root / extra).is_file():
            bad("extra_file_not_found", f"附带文件不存在：{extra}")
    for asset in case.get("assets") or []:
        if not (ASSETS_DIR / asset).is_file():
            bad("asset_not_found", f"素材不存在：assets/{asset}")

    if not isinstance(case.get("stem"), str) or not case["stem"]:
        bad("stem_missing", "没有 stem（这个 case 对应哪张图）")
    if not isinstance(case.get("expected_figures"), int) or case["expected_figures"] < 1:
        bad("expected_figures_invalid", "expected_figures 必须是正整数")

    expected = case.get("expected")
    if not isinstance(expected, dict):
        bad("expected_missing", "没有 expected 表")
    unknown = set(expected) - set(STAGES)
    if unknown:
        bad("unknown_stage", f"expected 里有未知阶段 {sorted(unknown)}")
    reasons = case.get("expected_false_reasons") or {}
    for stage, want in expected.items():
        if not isinstance(want, bool):
            bad("expected_not_bool", f"expected.{stage} 必须是 true/false")
        if want:
            continue
        if stage in NON_NEGOTIABLE_STAGES:
            # 「声明它不该通过」是把门禁关掉最省事的办法。这三级任何档位都
            # 不许关：跑不起来 / 捕获不到 / 打不开，那就是不兼容，理由再充分
            # 也得记成 classification，不能记成「本来就没期望它过」。
            bad(
                "stage_not_negotiable",
                f"expected.{stage}=false 不允许——execute / capture / open "
                f"是任何 tier 的下限。真跑不起来的请写 classification"
                f"（environment_dependency / unsupported_by_design / product_bug）",
            )
        if not str(reasons.get(stage, "")).strip():
            bad(
                "expected_false_reason_required",
                f"expected.{stage}=false 必须在 expected_false_reasons.{stage} "
                f"里写明理由——没有理由的例外，下一个人无法判断它还该不该存在",
            )

    cls = case.get("classification", "full_support")
    if cls not in CLASSIFICATIONS:
        bad(
            "unknown_classification", f"classification 非法 {cls!r}（可选 {list(CLASSIFICATIONS)}）"
        )
    if cls in NEEDS_REASON and not str(case.get("reason", "")).strip():
        # 没有理由的例外，下一个人无法判断它还该不该存在，最终只会被无限期沿用。
        bad("reason_required", f"classification={cls} 必须写明 reason")
    if cls in NEEDS_FOLLOW_UP and not str(case.get("follow_up", "")).strip():
        bad(
            "follow_up_required",
            f"classification={cls} 必须写明 follow_up（product_bug 是待修缺陷，"
            f"不是可以长期接受的状态）",
        )
    if cls == "product_bug" and case.get("tier") == "must":
        bad(
            "tier1_product_bug",
            "Tier 1 不允许存在 product_bug——那一档是标准 matplotlib 的高频"
            "路径，有 bug 就是发不了版。要么修，要么把它降级并写清楚为什么"
            "它不再是高频路径",
        )

    routes = case.get("product_routes") or {}
    unknown_routes = set(routes) - set(PRODUCT_ROUTES)
    if unknown_routes:
        bad(
            "unknown_route",
            f"product_routes 里有未知路由 {sorted(unknown_routes)}（可选 {list(PRODUCT_ROUTES)}）",
        )
    for route, want in routes.items():
        if want not in ROUTE_EXPECTATIONS:
            bad(
                "route_expectation_invalid",
                f"product_routes.{route} 非法 {want!r}"
                f"（可选 true / 'not_implemented' / 'not_applicable'）",
            )
        if route == "native_run" and want is True:
            # native 执行是 PR 2：现在声明「必须通过」只能靠伪装 pass 兑现，
            # 而这份 benchmark 的第一条纪律就是不许假兼容。
            bad(
                "native_run_not_implemented",
                "native_run 尚未实现——第一阶段只能声明 not_implemented / "
                "not_applicable，不要伪装 pass",
            )
        if route == "browser_playground" and want is True and not case.get("browser_eligible"):
            bad(
                "route_not_browser_eligible",
                "browser_playground=true 但 case 不是 browser_eligible——"
                "对拍腿根本不会跑它，这条声明永远验不了",
            )

    sem = case.get("semantic_expectations") or {}
    for gid_prop in sem.get("editable") or []:
        if not (isinstance(gid_prop, list) and len(gid_prop) == 2):
            bad("editable_shape", f"editable 条目必须是 [gid, prop]：{gid_prop!r}")
    for target in case.get("mutations") or []:
        if not isinstance(target, dict) or "gid" not in target or "prop" not in target:
            bad("mutation_shape", f"mutation 必须带 gid/prop：{target!r}")
        if "value" not in target:
            bad("mutation_shape", f"mutation 必须带 value：{target!r}")
    if len(case.get("mutations") or []) > 5:
        bad(
            "too_many_mutations",
            "每个 case 最多 5 个代表性编辑目标——遍历所有属性是 pytest 的活，不是兼容基准的活",
        )


def _validate_smoke_subset(cases: list[dict]) -> None:
    smoke = [c for c in cases if c.get("smoke")]
    if not smoke:
        raise CorpusError("smoke_empty", "smoke 子集是空的——PR 上就等于没有兼容门禁")
    if not any(c["tier"] == "must" for c in smoke):
        raise CorpusError("smoke_no_tier1", "smoke 子集里一个 Tier 1 都没有")
    cats = {c["category"] for c in smoke}
    missing = {c["category"] for c in cases if c["tier"] == "must"} - cats
    if missing:
        raise CorpusError(
            "smoke_missing_category",
            f"smoke 子集没覆盖到这些有 Tier 1 case 的类别：{sorted(missing)}",
        )


#: `matrix.json` 里 target 可以声明只跑一个子集。值即判据名，闭集——
#: 写错一个字当场报错，不当成「一个我们没见过的新子集」放行。
SUBSETS = ("browser_eligible",)


def select(
    cases: list[dict],
    *,
    ids: list[str] | None = None,
    tiers: list[str] | None = None,
    categories: list[str] | None = None,
    smoke: bool = False,
    browser_only: bool = False,
) -> list[dict]:
    """按条件挑 case。**顺序永远是清单里的顺序**——报告要可 diff。"""
    out = cases
    if ids:
        want = set(ids)
        known = {c["id"] for c in out}
        unknown = want - known
        if unknown:
            raise CorpusError("unknown_case_id", f"没有这些 case：{sorted(unknown)}")
        out = [c for c in out if c["id"] in want]
    if smoke:
        out = [c for c in out if c.get("smoke")]
    if tiers:
        out = [c for c in out if c["tier"] in set(tiers)]
    if categories:
        out = [c for c in out if c["category"] in set(categories)]
    if browser_only:
        out = [c for c in out if c.get("browser_eligible")]
    return out


def project_key(case: dict) -> tuple:
    """同一个 case 组共用一个临时项目 + 一次 build。

    键 = 脚本 + 落点 + 附带文件 + 素材 + entry。**stem 不进键**：一个脚本
    产出多张图时，那些 stem 本来就来自同一次 build，分开跑等于把 corpus 的
    耗时乘以 stem 数（而 build 才是大头）。
    """
    return (
        case["script"],
        case.get("script_dest") or "",
        tuple(sorted((case.get("extra_files") or {}).items())),
        tuple(sorted(case.get("assets") or [])),
        case.get("entry", "main"),
    )


def group_by_project(cases: list[dict]) -> list[list[dict]]:
    """按 `project_key` 分组，组内与组间都保持清单顺序。"""
    order: list[tuple] = []
    groups: dict[tuple, list[dict]] = {}
    for case in cases:
        key = project_key(case)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(case)
    return [groups[k] for k in order]


# --------------------------------------------------------------------------
# 版本矩阵
# --------------------------------------------------------------------------
def load_matrix(path: Path = MATRIX_PATH) -> dict:
    data = _load(path, "matrix")
    validate_matrix(data)
    return data


def validate_matrix(data: dict) -> None:
    if data.get("schema") != 1:
        raise CorpusError("matrix_schema", "矩阵 schema 必须是 1")
    targets = data.get("targets")
    if not isinstance(targets, dict) or not targets:
        raise CorpusError("matrix_empty", "版本矩阵里一个 target 都没有")
    for name, spec in targets.items():
        if not isinstance(spec, dict):
            raise CorpusError("target_invalid", f"target {name} 必须是对象")
        sub = spec.get("subset")
        if sub is not None and sub not in SUBSETS:
            raise CorpusError(
                "unknown_subset",
                f"target {name} 的 subset 非法 {sub!r}（可选 {list(SUBSETS)}）。"
                f"`true` 这种写法不再接受——它说不出**跑哪个**子集，"
                f"于是 runner 只能忽略它，而 workflow 的注释还在说它跑了子集",
            )
        src = spec.get("source")
        if src is not None:
            # 版本真相只有一份：这里只准写「去哪读」。
            if not (REPO / src).is_file():
                raise CorpusError("target_source_missing", f"target {name} 的 source 不存在：{src}")
            if "matplotlib" in spec:
                raise CorpusError(
                    "target_duplicates_version",
                    f"target {name} 既写了 source 又写死了 matplotlib 版本。"
                    f"版本真相只能有一份——删掉写死的那个",
                )
        elif not spec.get("matplotlib"):
            # `required: false` 的 target 允许「当前环境是什么就用什么」
            # （本地开发那一档）；**要当发行判据的必须钉死版本**，
            # 否则这一档在 CI 上等于装 latest，什么都没验。
            if spec.get("required"):
                raise CorpusError(
                    "target_no_version",
                    f"target {name} 标了 required 却既没有 source 也没有精确"
                    f"版本——那样它在 CI 上等于装 latest",
                )
        elif str(spec["matplotlib"])[0] not in "0123456789":
            raise CorpusError(
                "target_version_not_exact",
                f"target {name} 的 matplotlib 必须是精确版本，不许范围/latest："
                f"{spec['matplotlib']!r}",
            )
    if not any(t.get("required") for t in targets.values()):
        raise CorpusError(
            "matrix_nothing_required", "没有任何 target 标了 required——这个矩阵不会拦住任何东西"
        )


def resolve_target(matrix: dict, name: str) -> dict:
    """把一个 target 解析成 {python, matplotlib, ...}，版本从锁文件现读。"""
    try:
        spec = dict(matrix["targets"][name])
    except KeyError:
        raise CorpusError(
            "unknown_target", f"没有这个 target：{name}（可选 {sorted(matrix['targets'])}）"
        ) from None
    src = spec.pop("source", None)
    if src:
        spec.update(_versions_from_lock(REPO / src))
        spec["source"] = src
    return spec


def _versions_from_lock(path: Path) -> dict:
    """从两种锁文件里取科学栈版本。两种形状各自认，不做「猜一个」。"""
    data = _load(path, "lock")
    if "pyodide_version" in data:  # playground-runtime.json
        # **键名是 `pyodide_python` 而不是 `python`**：那一档锁的是 Pyodide
        # 里那个解释器的版本，与「拿哪个 CPython 跑 browser.py」无关。叫
        # `python` 的话会被 runner 的 Python 版本核对当成运行时要求——实测
        # 后果是 nightly 的 browser 那条腿**永久红**（矩阵给 3.13，锁文件说
        # 3.14.2，版本核对当场退出 2，一个 case 都跑不到）。
        return {
            "pyodide_python": data.get("python", ""),
            "pyodide": data.get("pyodide_version", ""),
            **{k: v for k, v in (data.get("packages") or {}).items()},
        }
    targets = data.get("targets") or {}  # runtime-lock.json
    if not targets:
        raise CorpusError("lock_unreadable", f"{path} 里既没有包表也没有 targets")
    first = next(iter(targets.values()))
    raw = first.get("packages") or {}
    pkgs = (
        {str(k).lower(): str(v) for k, v in raw.items()}
        if isinstance(raw, dict)
        else {
            str(i["name"]).lower(): str(i.get("version", ""))
            for i in raw
            if isinstance(i, dict) and i.get("name")
        }
    )
    return {"python": (first.get("python") or {}).get("version", ""), **pkgs}


# --------------------------------------------------------------------------
# 基线
# --------------------------------------------------------------------------
def load_baseline(path: Path = BASELINE_PATH) -> dict:
    """读基线。**缺失即失败**，绝不当成空基线放行。

    「没有基线 → 生成一份 → 报绿」是这套门禁最容易退化成的样子：第一次跑
    永远通过，而它什么都没验证。
    """
    if not path.is_file():
        raise CorpusError(
            "baseline_missing",
            f"基线不存在：{path}。本地跑 "
            f"`python scripts/ci/compat_matrix.py --all --update-baseline` "
            f"生成并**逐条读过**之后提交——CI 里绝不自动生成",
        )
    data = _load(path, "baseline")
    validate_baseline(data)
    return data


def validate_baseline(data: dict) -> None:
    if data.get("schema") != 1:
        raise CorpusError("baseline_schema", "基线 schema 必须是 1")
    cases = data.get("cases")
    if not isinstance(cases, dict) or not cases:
        raise CorpusError("baseline_empty", "基线里一个 case 都没有")
    for cid, entry in cases.items():
        cls = entry.get("classification")
        if cls not in CLASSIFICATIONS:
            raise CorpusError("unknown_classification", f"基线 {cid}: classification 非法 {cls!r}")
        if cls in NEEDS_REASON and not str(entry.get("reason", "")).strip():
            raise CorpusError(
                "reason_required", f"基线 {cid}: classification={cls} 必须写明 reason"
            )
        if cls in NEEDS_FOLLOW_UP and not str(entry.get("follow_up", "")).strip():
            raise CorpusError(
                "follow_up_required",
                f"基线 {cid}: product_bug 必须写明 follow_up。"
                f"把已知缺陷记进基线是为了看住它，不是为了接受它",
            )
        stages = entry.get("stages") or {}
        unknown = set(stages) - set(STAGES)
        if unknown:
            raise CorpusError("unknown_stage", f"基线 {cid}: 未知阶段 {sorted(unknown)}")
    gen = data.get("generated_for")
    if gen is not None and (not isinstance(gen, dict) or not gen.get("target")):
        raise CorpusError(
            "baseline_no_target", "generated_for 必须写明 target（这份基线是在哪套版本上采的）"
        )
    # 时间戳不进基线：它每次跑都变，diff 里全是噪音，还会让「基线没动」
    # 这件事看不出来。
    for forbidden in ("generated_at", "timestamp", "run_id"):
        if forbidden in data:
            raise CorpusError(
                "baseline_has_timestamp",
                f"基线里不许有 {forbidden}——它每次都变，会把真正的分类变化淹没在 diff 噪音里",
            )


def baseline_payload(results: dict, generated_for: dict | None = None) -> dict:
    """把一次运行的结果整理成可提交的基线（确定性：键全排序，无时间戳）。

    `generated_for` 记的是**这份基线是在哪套版本上采的**（target 名 + 实际装
    的科学栈）。它不是噪音而是判据：分类会随 matplotlib 版本变（新版本可能
    把某个 artist 换成另一个类），不写下来的话「基线对不上」永远查不出是
    产品变了还是环境变了。视觉基线靠把 CI 钉在 runtime-lock 上回避这个问题，
    这里把环境如实写进基线，两条路殊途同归。
    """
    cases = {}
    for cid in sorted(results):
        r = results[cid]
        entry = {
            "classification": r["classification"],
            "stages": {s: r["stages"][s] for s in STAGES if s in r["stages"]},
        }
        # **只收人要读的那几个字段**。`detail` 是整轮的诊断原文（每个 case
        # 几 KB：manifest 摘要、像素指标、导出字节数……），它属于
        # compat-report.json。塞进基线的后果是一份 700 KB、没人愿意在 review
        # 里读的文件，而基线的全部价值就在于「有人真的读过」。
        for key in ("reason", "follow_up"):
            if str(r.get(key, "")).strip():
                entry[key] = r[key]
        if r["classification"] == "product_bug" and r.get("stage"):
            entry["stage"] = r["stage"]
        note = str(r.get("detail_note", "")).strip()
        if note:
            entry["note"] = note
        cases[cid] = entry
    out = {"schema": 1}
    if generated_for:
        out["generated_for"] = {k: generated_for[k] for k in sorted(generated_for)}
    out["cases"] = cases
    return out


def write_baseline(payload: dict, path: Path = BASELINE_PATH) -> Path:
    """把基线写盘。**换行钉死成 `\n`，不跟平台走。**

    `Path.write_text` 默认是文本模式（`newline=None`），Windows 上会把每个
    `\n` 翻成 `\r\n`。基线是**提交进仓库、要被逐条读 diff** 的资产，而
    `--update-baseline` 明确是给人在本地跑的——一个 Windows 开发者重生成一次，
    149 个 case 全变成整文件 CRLF diff，真正的分类变化就淹在里面了。
    而「有人真的读过这份 diff」正是整条基线纪律唯一的立足点。

    本机（macOS/Linux）看不出这个问题：`os.linesep` 本来就是 `\n`。
    看护在 `tests/test_windows_regressions.py`——按本仓库的规矩，
    「只在别人电脑上发生」的 bug 先变成那里的用例。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def diff_baseline(baseline: dict, results: dict) -> dict:
    """基线 vs 本次。返回 new / missing / changed 三张表。

    `missing`（基线里有、这次没跑）**不算失败**——`--smoke` 与 `--case` 本来
    就只跑一部分。真正的失败判定在 runner 里，按门禁档位决定。
    """
    base = baseline.get("cases", {})
    new = sorted(set(results) - set(base))
    missing = sorted(set(base) - set(results))
    changed = []
    for cid in sorted(set(results) & set(base)):
        was, now = base[cid], results[cid]
        stage_diff = {
            s: [was.get("stages", {}).get(s), now["stages"].get(s)]
            for s in STAGES
            if s in now["stages"] and was.get("stages", {}).get(s) != now["stages"][s]
        }
        if was.get("classification") != now["classification"] or stage_diff:
            changed.append(
                {
                    "id": cid,
                    "was": was.get("classification"),
                    "now": now["classification"],
                    "stages": stage_diff,
                }
            )
    return {"new": new, "missing": missing, "changed": changed}
