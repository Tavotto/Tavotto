#!/usr/bin/env python3
"""PR 冲突域检查——把「哪些 open PR 与你同域」在进队列之前摆出来。

    python scripts/ci/pr_conflict_domains.py --pr 123 \
        --repo Tavotto/Tavotto --config .github/conflict-domains.json

Merge Queue 能验「组合起来还绿不绿」，验不了真正的 Git 冲突——尤其是
本仓库的受管生成物（codex-plugin/mcp/widget/canvas.html 是最热的一个）：
两个前端 PR 的**源码**互不相干，却各自携带一份重建过的同一个 bundle，
合掉一个另一个必冲突、必重建、必重跑。这个检查在 PR 上提前把三种重叠
报出来（只 ::warning:: + Job Summary，**不是 required check、不阻断**）：

* 同域直接重叠：两个 PR 改了同一个域里的同一个文件；
* 同域间接重叠：不同文件、同一个域（例如都动 ci-control-plane）；
* 生成物间接重叠：一个 PR 改了域的 sources、另一个改了 generated——
  「不同源码、同一生成物」正是 canvas.html 冲突的多数形态。

失败哲学：这是导航不是门禁。GitHub API 打不通、配置读不出，如实
::warning:: 后**退出码 0**——绝不因为一个咨询性检查把产品 PR 拦下。
输出里绝不包含 token 或请求头。纯标准库；网络走 GITHUB_TOKEN 的
Bearer 认证（GitHub Actions 注入），本地跑也可以用 `gh auth token`。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"


# ---------------------------------------------------------------- glob 匹配

def glob_to_regex(pattern: str) -> re.Pattern:
    """`**` 跨路径段、`*` 段内、`?` 单字符。锚定全路径。"""
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 3] == "**/":
                out.append(r"(?:[^/]+/)*")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
        elif ch == "?":
            out.append(r"[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(path: str, patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


# ---------------------------------------------------------------- 配置

class ConfigError(Exception):
    pass


def load_config(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读不出冲突域配置 {path}：{exc}") from exc
    domains = data.get("domains")
    if not isinstance(domains, dict) or not domains:
        raise ConfigError(f"{path} 里没有 domains")
    for name, spec in domains.items():
        pats = spec.get("files", []) + spec.get("sources", []) + spec.get("generated", [])
        if not pats:
            raise ConfigError(f"域 {name} 一个 pattern 都没有")
        if spec.get("policy") not in ("stack-or-train", "serialize"):
            raise ConfigError(f"域 {name} 的 policy 不认识：{spec.get('policy')}")
    return domains


def domain_patterns(spec: dict) -> list[str]:
    return spec.get("files", []) + spec.get("sources", []) + spec.get("generated", [])


# ---------------------------------------------------------------- GitHub API

class ApiUnavailable(Exception):
    """网络 / 认证问题——如实 warning，不阻断。"""


def _request(url: str, token: str | None) -> object:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        # 绝不把 token / 请求头写进异常文本
        raise ApiUnavailable(f"GitHub API 请求失败（{url.split('?')[0]}）：{type(exc).__name__}") from exc


def paginate(url_base: str, token: str | None, fetch=_request) -> list:
    """`?per_page=100&page=N` 翻页到底。"""
    out: list = []
    page = 1
    while True:
        sep = "&" if "?" in url_base else "?"
        batch = fetch(f"{url_base}{sep}per_page=100&page={page}", token)
        if not isinstance(batch, list):
            raise ApiUnavailable(f"分页响应不是列表：{url_base.split('?')[0]}")
        out.extend(batch)
        if len(batch) < 100:
            return out
        page += 1


def open_prs(repo: str, token: str | None, fetch=_request) -> list[dict]:
    """全部 open PR（含 draft——draft 也在开发，同样会撞）。"""
    return paginate(f"{API}/repos/{repo}/pulls?state=open", token, fetch)


def pr_files(repo: str, number: int, token: str | None, fetch=_request) -> list[str]:
    rows = paginate(f"{API}/repos/{repo}/pulls/{number}/files", token, fetch)
    return [r["filename"] for r in rows if isinstance(r, dict) and "filename" in r]


# ---------------------------------------------------------------- 判定

def classify(files: list[str], domains: dict) -> dict[str, dict[str, list[str]]]:
    """每个域里，这组文件分别命中了 files/sources/generated 的哪些。"""
    hit: dict[str, dict[str, list[str]]] = {}
    for name, spec in domains.items():
        buckets = {}
        for kind in ("files", "sources", "generated"):
            got = [f for f in files if matches(f, spec.get(kind, []))]
            if got:
                buckets[kind] = got
        if buckets:
            hit[name] = buckets
    return hit


def _flat(buckets: dict[str, list[str]]) -> set[str]:
    return {f for got in buckets.values() for f in got}


def overlaps(mine: dict, theirs: dict, domains: dict) -> list[dict]:
    """两个 PR 的域命中 → 重叠清单。"""
    out = []
    for name in sorted(set(mine) & set(theirs)):
        a, b = mine[name], theirs[name]
        direct = sorted(_flat(a) & _flat(b))
        # 「会撞同一个生成物」的判定按**域声明**走，不按 diff 里有没有出现
        # generated 路径：两个 PR 各改 web/src 的不同文件、谁都没带
        # canvas.html，合并时各自重建的仍是同一个 bundle——sources×sources
        # 在声明了 generated 的域里就是生成物重叠（#120 评审 P2；
        # browser-playground 的产物在别的仓库提交，更是只有这条判据够得到）。
        declares_generated = bool(domains[name].get("generated"))
        a_touch = a.get("sources") or a.get("generated")
        b_touch = b.get("sources") or b.get("generated")
        cross_generated = bool(declares_generated and a_touch and b_touch)
        out.append({
            "domain": name,
            "policy": domains[name]["policy"],
            "direct": direct,
            "generated_overlap": cross_generated,
        })
    return out


def advice(policy: str, generated_overlap: bool) -> str:
    if policy == "serialize":
        return "串行：一次只开一个动这个域的 PR，按队列先后合，后者在前者合入后 rebase"
    if generated_overlap:
        return ("生成物会撞：相关改动改用 Stacked PR；互不相关的进同一个 train "
                "branch，源码冲突解完后在最终状态上只重建一次生成物"
                "（见 docs/ci/parallel-prs.md）")
    return "同域但暂无生成物撞点：留意合并顺序，后合的一侧 rebase 后重跑快线即可"


# ---------------------------------------------------------------- 输出

def render(pr_number: int, findings: list[dict], summary_lines: list[str]) -> None:
    for f in findings:
        for o in f["overlaps"]:
            level = "直接文件重叠" if o["direct"] else (
                "生成物重叠" if o["generated_overlap"] else "同域")
            print(f"::warning::PR #{f['number']}「{f['title'][:80]}」与本 PR 在域 "
                  f"`{o['domain']}` {level}（policy: {o['policy']}）")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("\n".join(summary_lines) + "\n")
        except OSError:
            pass


def build_summary(pr_number: int, mine_hit: dict, findings: list[dict],
                  domains: dict) -> list[str]:
    lines = [f"## PR #{pr_number} 冲突域检查", ""]
    if not mine_hit:
        lines.append("本 PR 不落在任何声明过的冲突域里。")
        return lines
    lines.append("本 PR 触达的域：" + "、".join(f"`{d}`" for d in sorted(mine_hit)))
    lines.append("")
    if not findings:
        lines.append("没有其他 open PR 与本 PR 同域。可以直接进 Merge Queue。")
        return lines
    lines += ["| PR | 域 | 直接文件重叠 | 生成物重叠 | 建议 |", "|---|---|---|---|---|"]
    for f in findings:
        for o in f["overlaps"]:
            direct = "<br>".join(f"`{p}`" for p in o["direct"][:5]) or "—"
            gen = "是" if o["generated_overlap"] else "—"
            lines.append(f"| #{f['number']} {f['title'][:60]} | `{o['domain']}` "
                         f"| {direct} | {gen} | {advice(o['policy'], o['generated_overlap'])} |")
    lines += ["", "本检查只是导航（不是 required check）；两种协作形态的具体做法见 "
              "`docs/ci/parallel-prs.md`。"]
    return lines


# ---------------------------------------------------------------- 主流程

def run(repo: str, pr_number: int, config_path: Path, token: str | None,
        fetch=_request) -> int:
    try:
        domains = load_config(config_path)
    except ConfigError as exc:
        print(f"::warning::{exc}——冲突域检查跳过（不阻断产品 CI）")
        return 0
    try:
        my_files = pr_files(repo, pr_number, token, fetch)
        others = [p for p in open_prs(repo, token, fetch)
                  if p.get("number") != pr_number and p.get("state") == "open"]
        mine_hit = classify(my_files, domains)
        findings = []
        if mine_hit:                     # 自己不在任何域里就不用翻别人的文件
            for p in others:
                their_files = pr_files(repo, p["number"], token, fetch)
                their_hit = classify(their_files, domains)
                ols = overlaps(mine_hit, their_hit, domains)
                if ols:
                    findings.append({"number": p["number"],
                                     "title": p.get("title", ""),
                                     "draft": bool(p.get("draft")),
                                     "overlaps": ols})
    except ApiUnavailable as exc:
        print(f"::warning::{exc}——冲突域检查这次没跑成（不阻断产品 CI）")
        return 0
    render(pr_number, findings, build_summary(pr_number, mine_hit, findings, domains))
    print(json.dumps({"pr": pr_number, "domains": sorted(mine_hit),
                      "overlapping_prs": [f["number"] for f in findings]},
                     ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--config", type=Path,
                    default=Path(".github/conflict-domains.json"))
    args = ap.parse_args(argv)
    if not args.repo:
        print("::warning::没有 --repo 也没有 GITHUB_REPOSITORY——检查跳过")
        return 0
    token = os.environ.get("GITHUB_TOKEN")
    return run(args.repo, args.pr, args.config, token)


if __name__ == "__main__":
    raise SystemExit(main())
