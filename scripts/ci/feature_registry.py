#!/usr/bin/env python3
"""功能登记表门禁（ADR 0097）：用户可见的功能不能被后面的修改悄悄关掉。

    python scripts/ci/feature_registry.py check                 # 登记表 ↔ e2e 用例（静态）
    python scripts/ci/feature_registry.py verify-run REPORT     # 合并态那次真跑：登记的用例都 passed
    python scripts/ci/feature_registry.py transitions --base SHA [--labels JSON]
    python scripts/ci/feature_registry.py beta-checklist        # 桌面壳手动试用清单（Markdown）

登记表是 `docs/features/registry.json`（唯一权威）：每条一个用户可见能力，`active` 的
必须有真浏览器 e2e 覆盖（`platform` 为 `desktop` 的除外，它们进 beta 手动清单）。

各子命令的主语（写判据之前先说清：谁的、哪个进程、哪个时刻、哪个维度）：

* ``check``：**Playwright 自己算出来的用例集合**（`playwright test --list --reporter=json`，
  按 web/playwright.config.ts 的全部 project）× **源码里每条用例声明的跳过修饰**
  （`web/scripts/e2e-skip-scan.mjs`，TypeScript AST）。身份 = (spec 文件, 标题路径)，标题
  路径是 describe 标题 + 用例标题以 `` › `` 连接，精确相等，不做子串。判：
    1. 登记表形状（id 唯一、状态闭集、removed 必有原因 / 日期 / 批准人……）；
    2. 每个 active 功能引用的用例在 `--list` 里存在、不是声明期跳过（expectedStatus），
       源码里也没有运行期 `test.skip / fixme / fail` 能落到它身上；
    3. 反向：带 `@feature:<id>` 标签的用例，<id> 必须是 active 功能且把这条用例列在名下。
* ``verify-run``：**合并态那次真跑的 JSON 报告**（posix-e2e 的 `pnpm e2e`，merge_group 的
  组合提交）。登记的每条用例在报告里至少出现一次，且每次出现的结论都是 expected / flaky。
  skipped 判红——skip 不是绿；「报告里根本没有它」也判红——没跑不是绿。
* ``transitions``：**base 与本次提交的两份登记表**。条目不许消失（要改成 removed）；
  active → removed 必须带「移除功能」标签（`feature:removal`）。拿不到标签（merge_group
  事件没有 PR 标签）时只判前一条。
* ``beta-checklist``：`platform` 含桌面壳、e2e 覆盖不到的那部分，按登记表原文生成清单。

退出码：0 通过；1 判据不成立；2 输入 / 用法错误（判定器自己拿不到可信输入，不能算通过）。
纯标准库。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "features" / "registry.json"
REGISTRY_REL = "docs/features/registry.json"
WEB = ROOT / "web"

REMOVAL_LABEL = "feature:removal"
TAG_PREFIX = "@feature:"
STATUSES = ("active", "removed")
PLATFORMS = ("browser", "desktop", "both")
#: 结论闭集（Playwright JSON 报告的 test.status）：这两种算「真跑过且通过」
RAN_OK = ("expected", "flaky")
ID_RE = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z0-9][a-z0-9-]*)+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEP = " › "


class InputError(ValueError):
    """输入本身不可信（清单读不懂、报告是空的、登记表不是 JSON）——rc 2。"""


# ── 登记表 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Ref:
    spec: str
    title: str

    def show(self) -> str:
        return f"{self.spec} › {self.title}"


@dataclass
class Feature:
    id: str
    status: str
    platform: str
    e2e: list[Ref] = field(default_factory=list)
    desktop_manual: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _nonempty_str(v: object) -> bool:
    return isinstance(v, str) and v.strip() != ""


def parse_registry(data: object) -> tuple[list[Feature], list[str]]:
    """登记表 → (功能列表, 形状问题)。形状问题是判据不成立（rc 1），不是输入错误。"""
    problems: list[str] = []
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or not isinstance(data.get("features"), list)
    ):
        raise InputError("登记表顶层必须是 {version: 1, features: [...]}")
    feats: list[Feature] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["features"]):
        where = f"features[{i}]"
        if not isinstance(raw, dict):
            problems.append(f"{where} 不是对象")
            continue
        fid = raw.get("id")
        if not isinstance(fid, str) or not ID_RE.match(fid):
            problems.append(f"{where}.id {fid!r} 不合形状（小写、点分层，如 canvas.select-drag）")
            continue
        where = fid
        if fid in seen:
            problems.append(f"{fid}：id 重复")
        seen.add(fid)
        status = raw.get("status")
        platform = raw.get("platform")
        if status not in STATUSES:
            problems.append(f"{fid}：status 必须是 {STATUSES} 之一，而不是 {status!r}")
        if platform not in PLATFORMS:
            problems.append(f"{fid}：platform 必须是 {PLATFORMS} 之一，而不是 {platform!r}")
        summary = raw.get("summary")
        if not (
            isinstance(summary, dict)
            and _nonempty_str(summary.get("zh-CN"))
            and _nonempty_str(summary.get("en-US"))
        ):
            problems.append(f"{fid}：summary 要有 zh-CN 与 en-US 各一句")
        if not _nonempty_str(raw.get("entry")):
            problems.append(f"{fid}：entry（界面上的入口位置）不能为空")
        refs: list[Ref] = []
        for j, r in enumerate(raw.get("e2e", [])):
            if not (
                isinstance(r, dict)
                and _nonempty_str(r.get("spec"))
                and _nonempty_str(r.get("title"))
            ):
                problems.append(f"{fid}：e2e[{j}] 必须是 {{spec, title}}")
                continue
            ref = Ref(r["spec"], r["title"])
            if ref in refs:
                problems.append(f"{fid}：e2e 里 {ref.show()} 列了两次")
            refs.append(ref)
        manual = raw.get("desktop_manual", [])
        if not (isinstance(manual, list) and all(_nonempty_str(m) for m in manual)):
            problems.append(f"{fid}：desktop_manual 必须是非空字符串的列表")
            manual = []
        if status == "active":
            if platform in ("browser", "both") and not refs:
                problems.append(f"{fid}：active 且浏览器里有这个功能，至少要一条 e2e 覆盖")
            if platform == "desktop" and not manual:
                problems.append(
                    f"{fid}：只在桌面壳里的功能 e2e 覆盖不到，desktop_manual 至少要一步手动试用"
                )
            if "removed" in raw:
                problems.append(f"{fid}：active 的条目不该带 removed 块")
        elif status == "removed":
            rm = raw.get("removed")
            if not isinstance(rm, dict):
                problems.append(
                    f"{fid}：removed 的条目必须有 removed 块（reason / date / approved_by）"
                )
            else:
                for k in ("reason", "date", "approved_by"):
                    if not _nonempty_str(rm.get(k)):
                        problems.append(f"{fid}：removed.{k} 不能为空")
                if _nonempty_str(rm.get("date")) and not DATE_RE.match(rm["date"]):
                    problems.append(f"{fid}：removed.date 要写成 YYYY-MM-DD，而不是 {rm['date']!r}")
        feats.append(Feature(fid, str(status), str(platform), refs, list(manual), raw))
    if not feats and not problems:
        raise InputError("登记表里一条功能都没有")
    return feats, problems


def load_registry_text(text: str) -> tuple[list[Feature], list[str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise InputError(f"登记表不是合法 JSON：{e}") from e
    return parse_registry(data)


# ── Playwright 的用例集合 ────────────────────────────────────────────────


@dataclass
class Listed:
    spec: str
    title: str
    line: int
    column: int
    tags: set[str] = field(default_factory=set)
    projects: set[str] = field(default_factory=set)
    #: 每个 project 的 expectedStatus（声明期 skip / fixme → "skipped"）
    expected: dict[str, str] = field(default_factory=dict)
    #: 运行报告里的结论（verify-run 用）：[(project, status)]
    outcomes: list[tuple[str, str]] = field(default_factory=list)


def _walk(suite: dict, spec_file: str, path: list[str], out: dict[Ref, Listed]) -> None:
    for sp in suite.get("specs", []):
        title = SEP.join([*path, sp["title"]])
        ref = Ref(spec_file, title)
        rec = out.get(ref)
        if rec is None:
            rec = out[ref] = Listed(spec_file, title, int(sp["line"]), int(sp["column"]))
        rec.tags.update(sp.get("tags", []))
        for t in sp.get("tests", []):
            proj = t.get("projectName") or t.get("projectId") or "?"
            rec.projects.add(proj)
            rec.expected[proj] = t.get("expectedStatus", "passed")
            if "status" in t and t.get("results"):
                rec.outcomes.append((proj, t["status"]))
            elif "status" in t and t["status"] == "skipped":
                # 真跑的报告里，被 skip 的用例 results 可能为空但 status 仍是 skipped
                rec.outcomes.append((proj, "skipped"))
    for sub in suite.get("suites", []):
        _walk(sub, spec_file, [*path, sub["title"]], out)


def parse_playwright_json(data: object, label: str) -> dict[Ref, Listed]:
    """`playwright test --reporter=json`（`--list` 或真跑）→ {(spec, 标题路径): Listed}。"""
    if not isinstance(data, dict) or not isinstance(data.get("suites"), list):
        raise InputError(f"{label}：不是 Playwright JSON 报告（没有 suites）")
    if data.get("errors"):
        raise InputError(
            f"{label}：Playwright 报告里有加载错误：{json.dumps(data['errors'], ensure_ascii=False)[:500]}"
        )
    out: dict[Ref, Listed] = {}
    for top in data["suites"]:
        spec_file = top.get("file") or top.get("title")
        _walk(top, spec_file, [], out)
    if not out:
        raise InputError(f"{label}：一条用例都没有")
    return out


def capture_list(web: Path) -> dict[Ref, Listed]:
    """在 web/ 下直接起 node 跑 `playwright test --list --reporter=json`（全部 project）。"""
    cli = web.resolve() / "node_modules" / "@playwright" / "test" / "cli.js"
    if not cli.is_file():
        raise InputError(f"{cli} 不存在——web 依赖没装？")
    proc = subprocess.run(
        ["node", str(cli), "test", "--list", "--reporter=json"],
        cwd=str(web.resolve()),
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise InputError(
            f"`playwright test --list` 退出码 {proc.returncode}：\n{proc.stderr.decode('utf-8', 'replace')[-2000:]}"
        )
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise InputError(f"`playwright test --list --reporter=json` 的输出不是 JSON：{e}") from e
    return parse_playwright_json(data, "playwright --list")


def capture_scan(web: Path) -> dict[tuple[str, int, int], list[dict]]:
    """`web/scripts/e2e-skip-scan.mjs` → {(spec, line, column): [修饰]}。"""
    script = web.resolve() / "scripts" / "e2e-skip-scan.mjs"
    proc = subprocess.run(
        ["node", str(script)], cwd=str(web.resolve()), capture_output=True, timeout=120
    )
    if proc.returncode != 0:
        raise InputError(
            f"e2e-skip-scan 退出码 {proc.returncode}：{proc.stderr.decode('utf-8', 'replace')[-2000:]}"
        )
    return parse_scan(json.loads(proc.stdout.decode("utf-8")))


def parse_scan(data: object) -> dict[tuple[str, int, int], list[dict]]:
    if not isinstance(data, dict) or not isinstance(data.get("tests"), list) or not data["tests"]:
        raise InputError("e2e-skip-scan 的输出里没有用例")
    return {
        (t["file"], int(t["line"]), int(t["column"])): list(t["modifiers"]) for t in data["tests"]
    }


# ── 判据 ────────────────────────────────────────────────────────────────


def feature_tags(rec: Listed) -> set[str]:
    # 源码里写的是 `@feature:<id>`，Playwright 的 JSON 报告里 tags **去掉了前导 @**
    # （实测 1.5x：`{ tag: '@feature:x' }` → `"tags": ["feature:x"]`）。只认带 @ 的写法
    # 会让反向判据恒绿——本 PR 的反证第一次跑就是这样漏的。两种都认，比较时统一去掉 @。
    bare = TAG_PREFIX.lstrip("@")
    return {t.lstrip("@")[len(bare) :] for t in rec.tags if t.lstrip("@").startswith(bare)}


def check(
    feats: list[Feature],
    listing: dict[Ref, Listed],
    scan: dict[tuple[str, int, int], list[dict]],
) -> list[str]:
    problems: list[str] = []
    by_id = {f.id: f for f in feats}
    for f in feats:
        if f.status != "active":
            continue
        for ref in f.e2e:
            rec = listing.get(ref)
            if rec is None:
                near = sorted(r.title for r in listing if r.spec == ref.spec)
                hint = (
                    f"（{ref.spec} 里现有 {len(near)} 条）"
                    if near
                    else f"（{ref.spec} 不存在或一条用例都没有）"
                )
                problems.append(f"{f.id}：用例 {ref.show()} 在 Playwright 的清单里不存在{hint}")
                continue
            skipped = sorted(p for p, s in rec.expected.items() if s != "passed")
            if skipped:
                problems.append(
                    f"{f.id}：用例 {ref.show()} 在 {skipped} 上是声明期跳过（expectedStatus ≠ passed）"
                )
            key = (rec.spec, rec.line, rec.column)
            if key not in scan:
                problems.append(
                    f"{f.id}：用例 {ref.show()}（{rec.spec}:{rec.line}:{rec.column}）在源码扫描里找不到"
                    "——扫描器与 Playwright 对不上位置，判不出它有没有被跳过"
                )
                continue
            for m in scan[key]:
                problems.append(
                    f"{f.id}：用例 {ref.show()} 会被 {rec.spec}:{m['line']} 的 test.{m['kind']}(…)"
                    f"（作用域：{m['scope']}）跳过——功能用例不许带条件跳过"
                )
    for ref, rec in sorted(listing.items(), key=lambda kv: kv[0].show()):
        for fid in sorted(feature_tags(rec)):
            f = by_id.get(fid)
            if f is None:
                problems.append(
                    f"用例 {ref.show()} 标了 {TAG_PREFIX}{fid}，登记表里没有这个功能（孤儿标签）"
                )
            elif f.status != "active":
                problems.append(
                    f"用例 {ref.show()} 标了 {TAG_PREFIX}{fid}，而这个功能已经 removed——摘掉标签"
                )
            elif ref not in f.e2e:
                problems.append(
                    f"用例 {ref.show()} 标了 {TAG_PREFIX}{fid}，但 {fid} 的 e2e 里没有列它"
                )
    return problems


def verify_run(feats: list[Feature], report: dict[Ref, Listed]) -> tuple[list[str], int]:
    problems: list[str] = []
    counted = 0
    for f in feats:
        if f.status != "active":
            continue
        for ref in f.e2e:
            rec = report.get(ref)
            if rec is None or not rec.outcomes:
                problems.append(f"{f.id}：用例 {ref.show()} 不在这次运行里——没跑不是绿")
                continue
            bad = [(p, s) for p, s in rec.outcomes if s not in RAN_OK]
            if bad:
                problems.append(f"{f.id}：用例 {ref.show()} 的结论是 {bad}——skip / 失败都不是绿")
            else:
                counted += 1
    return problems, counted


def transitions(
    base: list[Feature] | None, head: list[Feature], labels: list[str] | None
) -> list[str]:
    if base is None:
        return []
    problems: list[str] = []
    head_by = {f.id: f for f in head}
    for b in base:
        h = head_by.get(b.id)
        if h is None:
            problems.append(
                f"{b.id}：条目从登记表里消失了——功能下线要把它改成 status: removed（写原因 / 日期 / 批准人），不许删条目"
            )
            continue
        if (
            b.status == "active"
            and h.status == "removed"
            and labels is not None
            and REMOVAL_LABEL not in labels
        ):
            problems.append(f"{b.id}：active → removed，PR 必须打「{REMOVAL_LABEL}」标签")
    return problems


def beta_checklist(feats: list[Feature]) -> str:
    rows = [
        f
        for f in feats
        if f.status == "active" and f.platform in ("desktop", "both") and f.desktop_manual
    ]
    lines = [
        "## 桌面壳手动试用清单",
        "",
        f"<!-- 由 `python scripts/ci/feature_registry.py beta-checklist` 从 {REGISTRY_REL} 生成；"
        "改清单改登记表，别改这里。 -->",
        "",
        "下面这些功能在桌面壳里才有、或桌面壳那一半浏览器 e2e 量不到，发 beta 前逐条手动过一遍：",
        "",
    ]
    for f in rows:
        summary = f.raw["summary"]["zh-CN"]
        lines.append(f"- [ ] **{summary}**（`{f.id}`，入口：{f.raw['entry']}）")
        for step in f.desktop_manual:
            lines.append(f"  - {step}")
    if not rows:
        lines.append("- （登记表里没有需要手动试用的桌面壳功能）")
    return "\n".join(lines) + "\n"


# ── 入口 ────────────────────────────────────────────────────────────────


def _report(problems: list[str], ok_line: str) -> int:
    if problems:
        print(f"功能登记表：{len(problems)} 处不成立", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1
    print(ok_line)
    return 0


def _git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, timeout=120)


def base_registry(sha: str) -> list[Feature] | None:
    """base 提交上的登记表；base 上还没有这个文件（引入它的那个 PR）→ None。

    浅克隆里 base 不一定在本地：先定向 fetch。fetch 失败 / 提交不可达是输入错误（rc 2），
    **不能**当成「base 上没有登记表」——那会让「删条目」在拿不到 base 时静默放行。
    """
    if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        f = _git("fetch", "--no-tags", "--depth=1", "origin", sha)
        if f.returncode != 0 or _git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            raise InputError(
                f"拿不到 base 提交 {sha}：{f.stderr.decode('utf-8', 'replace')[-500:]}"
            )
    if _git("cat-file", "-e", f"{sha}:{REGISTRY_REL}").returncode != 0:
        return None
    text = _git("show", f"{sha}:{REGISTRY_REL}").stdout.decode("utf-8")
    feats, _ = load_registry_text(text)
    return feats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--registry", type=Path, default=REGISTRY)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="登记表 ↔ e2e 用例（静态）")
    c.add_argument("--web", type=Path, default=WEB)
    c.add_argument(
        "--list-json",
        type=Path,
        help="离线：已存好的 `playwright test --list --reporter=json` 输出",
    )
    c.add_argument("--scan-json", type=Path, help="离线：已存好的 e2e-skip-scan 输出")
    v = sub.add_parser("verify-run", help="合并态真跑的报告：登记的用例都真的执行且通过")
    v.add_argument("report", type=Path)
    t = sub.add_parser("transitions", help="与 base 比：条目不许消失，active → removed 要标签")
    t.add_argument("--base", required=True, help="base 提交 SHA")
    t.add_argument(
        "--labels", default="null", help="PR 标签名的 JSON 数组；null = 拿不到标签（merge_group）"
    )
    sub.add_parser("beta-checklist", help="桌面壳手动试用清单（Markdown 到 stdout）")
    args = ap.parse_args(argv)

    try:
        feats, shape = load_registry_text(args.registry.read_text(encoding="utf-8"))
        if args.cmd == "beta-checklist":
            if shape:
                return _report(shape, "")
            sys.stdout.write(beta_checklist(feats))
            return 0
        if args.cmd == "check":
            listing = (
                parse_playwright_json(
                    json.loads(args.list_json.read_text(encoding="utf-8")), str(args.list_json)
                )
                if args.list_json
                else capture_list(args.web)
            )
            scan = (
                parse_scan(json.loads(args.scan_json.read_text(encoding="utf-8")))
                if args.scan_json
                else capture_scan(args.web)
            )
            problems = shape + check(feats, listing, scan)
            active = [f for f in feats if f.status == "active"]
            n_refs = sum(len(f.e2e) for f in active)
            return _report(
                problems,
                f"功能登记表：{len(active)} 个 active 功能、{n_refs} 条 e2e 引用都在且不带跳过；"
                f"{len(feats) - len(active)} 个 removed 手续齐全；无孤儿标签",
            )
        if args.cmd == "verify-run":
            report = parse_playwright_json(
                json.loads(args.report.read_text(encoding="utf-8")), str(args.report)
            )
            problems, counted = verify_run(feats, report)
            if not problems and counted == 0:
                raise InputError("一条登记的用例都没数到——登记表是空的还是报告不对？")
            return _report(
                shape + problems, f"功能登记表：{counted} 条功能用例在这次运行里真的执行并通过"
            )
        if args.cmd == "transitions":
            labels = json.loads(args.labels)
            if labels is not None and not (
                isinstance(labels, list) and all(isinstance(x, str) for x in labels)
            ):
                raise InputError(f"--labels 必须是字符串数组或 null：{args.labels!r}")
            base = base_registry(args.base)
            problems = shape + transitions(base, feats, labels)
            where = (
                "base 上还没有登记表（引入它的那次）"
                if base is None
                else f"与 base {args.base[:12]} 比"
            )
            return _report(problems, f"功能登记表：{where}，没有条目消失、下线手续齐全")
    except InputError as e:
        print(f"功能登记表：输入不可信——{e}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        print(f"功能登记表：输入不可信——{e}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
