#!/usr/bin/env python3
"""CLA 资格判定：这个 PR 里出现的每一个人，是不是都已经签过、或被显式豁免。

    python3 scripts/ci/cla_gate.py \
        --event pull_request \
        --policy .github/cla-policy.json \
        --ledger docs/legal/cla-signatures.json \
        --pr-author someone \
        --commits-json commits.json

为什么判定要单独成脚本（而不是几行 Bash 或者一个第三方 action 说了算）：

* 它经 `CI fast gate` 参与合并资格，和 `aggregate_gate.py` 同一条纪律——
  判定逻辑散在 workflow 的 Bash 里没法单测，而「CLA 判错」的两头都不便宜：
  放过一个没签的外部贡献，将来那段代码就不能进任何非 AGPL 的发行版；
  错拦一个已签的人，PR 卡住而且没人看得懂为什么。
* **这个脚本不是签署机制。** 它只读一份已经存在的签署记录（ledger），
  记录是别处收上来的（签名服务商，或者人工复核过的书面协议）。
  「在 PR 里回一句 I agree 然后 grep 评论」那种东西不在这里，也不该有。

判定规则（详见 `decide()`）：

* `--event merge_group`：判 `not_applicable` 并**成功**。资格在 PR 阶段就已经
  验过，队列候选上没有 PR 上下文可查。**这一格必须是成功而不是 skipped**——
  `aggregate_gate.py --mode fast` 把 skipped 一律当失败，这个 job 在
  merge_group 上被跳过会把整个 `CI fast gate` 卡死（tests/ 里有专门的用例钉它）。
* `--event pull_request`：PR 里出现的每一个人类贡献者都必须在 ledger 里有一条
  绑到**当前 CLA 版本与哈希**的签名，或者在 policy 的豁免表里被点名。
* 其他事件：配置错误 → 失败。没设计过的上下文不许静默变绿。

fail-closed 的几处（每一处都是「宁可红」）：

* 认不出 GitHub 账号的 co-author（邮箱不是 GitHub noreply 形态）→ 失败，
  让人来认领，而不是猜一个 login 或者当它不存在；
* 豁免只按 login 精确匹配显式名单，**没有「名字像机器人就放行」这种规则**——
  `dependabot[bot]` 能过是因为它在表里写着，不是因为它带个 `[bot]` 后缀；
* policy 里记的文档哈希与磁盘上的文档对不上 → 失败（改了 CLA 正文却没走
  版本流程，见 docs/legal/CLA_VERSIONING.md）；
* 版本带 `-draft` 后缀 → 该版本上的任何签名一律不认（草案还没定稿，
  RIGHTS_HOLDER_CONFIGURATION_REQUIRED 没填完就不存在有效签署）。

退出码：0 = success 或 not_applicable；1 = 资格不足；2 = 配置 / 输入错误
（同样让 Gate 红——判定器自己坏了不能算通过）。

stdout 恒输出一行机器可读 JSON；`$GITHUB_STEP_SUMMARY` 存在时另写
人类可读的 Markdown。纯标准库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

#: 只有这两个事件有明确定义的判定。别的一律配置错误。
KNOWN_EVENTS = ("pull_request", "merge_group")

#: `Co-authored-by: Name <email>` trailer。大小写不敏感，行首匹配。
CO_AUTHOR_RE = re.compile(r"(?im)^\s*Co-authored-by:\s*(?P<name>.*?)\s*<(?P<email>[^>]+)>\s*$")

#: GitHub 的 noreply 邮箱形态：`login@users.noreply.github.com` 或
#: `12345+login@users.noreply.github.com`。**只有这两种能可靠反解出账号**，
#: 别的邮箱一律算认不出（见模块 docstring 的 fail-closed 一节）。
NOREPLY_RE = re.compile(r"(?i)^(?:\d+\+)?(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)@users\.noreply\.github\.com$")

#: 草案后缀。带它的版本不可签署。
DRAFT_SUFFIX = "-draft"


class ConfigError(Exception):
    """输入 / 配置错误。它也让 Gate 失败：判定器自己坏了不能算通过。"""


# ────────────────────────────────────────────────────────────── 读入与校验

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"{what} 找不到：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{what} 不是合法 JSON（{path}）：{exc}") from exc


def validate_policy(policy: dict) -> None:
    """policy 的形状必须严格——安静地解析出空表的判定器会把「谁都没签」判成任何东西。"""
    if not isinstance(policy, dict):
        raise ConfigError("cla-policy 不是对象")
    for key in ("schema", "agreements", "exemptions"):
        if key not in policy:
            raise ConfigError(f"cla-policy 缺字段 `{key}`")
    if not isinstance(policy["agreements"], dict) or not policy["agreements"]:
        raise ConfigError("cla-policy.agreements 必须是非空对象")
    for name, ag in policy["agreements"].items():
        for key in ("path", "version", "sha256"):
            if key not in ag:
                raise ConfigError(f"cla-policy.agreements['{name}'] 缺 `{key}`")
    if not isinstance(policy["exemptions"], list):
        raise ConfigError("cla-policy.exemptions 必须是数组")
    seen: set[str] = set()
    for i, ex in enumerate(policy["exemptions"]):
        if not isinstance(ex, dict):
            raise ConfigError(f"cla-policy.exemptions[{i}] 不是对象")
        for key in ("login", "kind", "reason"):
            if not ex.get(key):
                # 豁免必须写得出理由——「显式、可复核、有记录」是它存在的条件，
                # 一条没有 reason 的豁免下次没人敢删，也没人说得清为什么在这儿。
                raise ConfigError(f"cla-policy.exemptions[{i}] 缺 `{key}`（豁免必须显式且写明理由）")
        login = ex["login"].lower()
        if login in seen:
            raise ConfigError(f"cla-policy.exemptions 里 `{ex['login']}` 重复")
        seen.add(login)
        if ex["kind"] not in ("rights_holder", "bot"):
            raise ConfigError(
                f"cla-policy.exemptions[{i}].kind=`{ex['kind']}` 认不出"
                "（只允许 rights_holder / bot）")


def verify_documents(policy: dict, root: Path) -> list[str]:
    """policy 记的哈希 vs 磁盘上的正文。对不上就是改了 CLA 没走版本流程。"""
    problems = []
    for name, ag in policy["agreements"].items():
        path = root / ag["path"]
        if not path.is_file():
            problems.append(f"agreement `{name}`：文件不存在 {ag['path']}")
            continue
        actual = sha256_file(path)
        if actual != ag["sha256"]:
            problems.append(
                f"agreement `{name}`：{ag['path']} 的 SHA-256 是 {actual}，"
                f"policy 里记的是 {ag['sha256']}——改了正文就必须走 "
                f"docs/legal/CLA_VERSIONING.md 的版本流程")
    return problems


# ────────────────────────────────────────────────────────────── 贡献者收集

def collect_contributors(pr_author: str | None, commits: list) -> tuple[list[dict], list[dict]]:
    """把 PR 作者 + 每个 commit 的 author + Co-authored-by 收成一张去重的表。

    纯函数：`commits` 就是 `GET /repos/{o}/{r}/pulls/{n}/commits` 的响应。
    只检查 PR 发起人是不够的——一个 PR 里可以有别人写的 commit，也可以有
    co-author，那些人同样在向仓库投稿。
    """
    found: dict[str, dict] = {}
    unresolved: list[dict] = []

    def add(login: str | None, source: str, detail: str = "") -> None:
        if not login:
            return
        key = login.lower()
        entry = found.setdefault(key, {"login": login, "sources": []})
        if source not in entry["sources"]:
            entry["sources"].append(source)
        if detail and detail not in entry.get("details", []):
            entry.setdefault("details", []).append(detail)

    add(pr_author, "pr_author")

    if not isinstance(commits, list):
        raise ConfigError(f"commits JSON 不是数组：{type(commits).__name__}")

    for c in commits:
        if not isinstance(c, dict):
            raise ConfigError("commits JSON 里有非对象条目")
        sha = str(c.get("sha", ""))[:8]
        # GitHub 已经解析好的账号。null = 这个 commit 的邮箱没绑到任何账号。
        author = c.get("author")
        if isinstance(author, dict) and author.get("login"):
            add(author["login"], "commit_author", sha)
        else:
            meta = (c.get("commit") or {}).get("author") or {}
            email = str(meta.get("email", ""))
            m = NOREPLY_RE.match(email)
            if m:
                add(m.group("login"), "commit_author", sha)
            else:
                unresolved.append({"kind": "commit_author", "sha": sha,
                                   "name": str(meta.get("name", "")), "email": email})

        message = str((c.get("commit") or {}).get("message", ""))
        for m in CO_AUTHOR_RE.finditer(message):
            email = m.group("email").strip()
            nm = NOREPLY_RE.match(email)
            if nm:
                add(nm.group("login"), "co_author", sha)
            else:
                unresolved.append({"kind": "co_author", "sha": sha,
                                   "name": m.group("name"), "email": email})

    out = sorted(found.values(), key=lambda e: e["login"].lower())
    for e in out:
        e.setdefault("details", [])
    return out, unresolved


# ────────────────────────────────────────────────────────────── 判定

def _exemption_for(login: str, policy: dict) -> dict | None:
    for ex in policy["exemptions"]:
        if ex["login"].lower() == login.lower():
            return ex
    return None


def _signature_for(login: str, ledger: dict, policy: dict) -> tuple[dict | None, str | None]:
    """返回 (有效签名, 失效原因)。找不到就是 (None, None)。"""
    for sig in ledger.get("signatures", []):
        if str(sig.get("github_login", "")).lower() != login.lower():
            continue
        kind = sig.get("agreement")
        ag = policy["agreements"].get(kind)
        if ag is None:
            return None, f"签的是 `{kind}`，policy 里没有这种协议"
        if sig.get("agreement_version") != ag["version"]:
            return None, (f"签的是 {kind} {sig.get('agreement_version')}，"
                          f"当前版本是 {ag['version']}——旧签名不自动迁移")
        if sig.get("agreement_sha256") != ag["sha256"]:
            return None, (f"签名记的哈希与当前 {kind} 正文对不上"
                          f"（签的是 {str(sig.get('agreement_sha256'))[:12]}…）")
        if str(ag["version"]).endswith(DRAFT_SUFFIX):
            return None, (f"{kind} {ag['version']} 是草案（RIGHTS_HOLDER_CONFIGURATION_"
                          f"REQUIRED 未填完），草案上不存在有效签署")
        return sig, None
    return None, None


def decide(event: str, policy: dict, ledger: dict, contributors: list[dict],
           unresolved: list[dict] | None = None) -> dict:
    """核心判定。只做纯计算，不碰环境——单测才测得动每一格。"""
    unresolved = unresolved or []

    if event == "merge_group":
        # 队列候选上没有 PR 上下文；资格在 PR 阶段验过了。
        # **必须成功，不能 skipped**：fast gate 把 skipped 当失败。
        return {"event": event, "status": "not_applicable",
                "reason": "qualified_at_pull_request", "contributors": [],
                "problems": []}

    if event not in KNOWN_EVENTS:
        raise ConfigError(f"事件 `{event}` 没有定义过的 CLA 判定——"
                          f"只支持 {'/'.join(KNOWN_EVENTS)}")

    problems: list[str] = []
    for u in unresolved:
        problems.append(
            f"unresolved: {u['kind']} `{u['name']} <{u['email']}>`"
            f"（commit {u['sha']}）认不出 GitHub 账号——"
            f"请让本人用绑定该账号的邮箱重提，或由维护者在 ledger 里认领")

    rows = []
    for c in contributors:
        login = c["login"]
        ex = _exemption_for(login, policy)
        if ex is not None:
            rows.append({"login": login, "verdict": "exempt",
                         "detail": f"{ex['kind']}: {ex['reason']}",
                         "sources": c["sources"]})
            continue
        sig, why = _signature_for(login, ledger, policy)
        if sig is not None:
            rows.append({"login": login, "verdict": "signed",
                         "detail": f"{sig['agreement']} {sig['agreement_version']}",
                         "sources": c["sources"]})
            continue
        detail = why or "没有签署记录"
        rows.append({"login": login, "verdict": "missing", "detail": detail,
                     "sources": c["sources"]})
        problems.append(f"missing: `{login}` — {detail}")

    if not rows and not problems:
        # 一个人都没收集到 = 上游取数据出了问题，不是「所有人都签了」。
        problems.append("empty: 这个 PR 里一个贡献者都没收集到——"
                        "取 PR 数据那步多半失败了，不能当成资格通过")

    status = "failure" if problems else "success"
    reason = "unqualified_contributors" if problems else "all_contributors_qualified"
    return {"event": event, "status": status, "reason": reason,
            "contributors": rows, "problems": problems}


# ────────────────────────────────────────────────────────────── 输出

def render_summary(verdict: dict, policy: dict) -> str:
    icon = {"success": "✅", "not_applicable": "⏭️", "failure": "❌"}[verdict["status"]]
    lines = [f"## {icon} CLA check — {verdict['status']}", ""]
    if verdict["status"] == "not_applicable":
        lines += ["合并队列候选上不重新验 CLA——资格在 PR 阶段已经验过。", ""]
        return "\n".join(lines) + "\n"
    if verdict["contributors"]:
        lines += ["| contributor | verdict | detail |", "|---|---|---|"]
        for r in verdict["contributors"]:
            lines.append(f"| `{r['login']}` | {r['verdict']} | {r['detail']} |")
        lines.append("")
    if verdict.get("problems"):
        lines += ["问题："] + [f"- {p}" for p in verdict["problems"]] + [""]
        ind = policy.get("agreements", {}).get("individual", {}).get("path", "docs/legal/CLA_INDIVIDUAL.md")
        corp = policy.get("agreements", {}).get("corporate", {}).get("path", "docs/legal/CLA_CORPORATE.md")
        lines += [f"签署流程见 [`{ind}`]({ind}) / [`{corp}`]({corp}) 与 "
                  "[`docs/legal/CLA_AUTOMATION_SETUP.md`](docs/legal/CLA_AUTOMATION_SETUP.md)。", ""]
    return "\n".join(lines) + "\n"


def _emit(verdict: dict, policy: dict) -> None:
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(render_summary(verdict, policy))
        except OSError as exc:                        # summary 写不进不改变结论
            print(f"::warning::写不进 GITHUB_STEP_SUMMARY：{exc}", file=sys.stderr)


def refresh_hashes(policy_path: Path, root: Path) -> int:
    """把 policy 里的哈希按磁盘上的正文重算一遍。**只改哈希，不动版本号**——
    版本是个决定，见 docs/legal/CLA_VERSIONING.md。"""
    policy = load_json(policy_path, "cla-policy")
    validate_policy(policy)
    changed = []
    for name, ag in policy["agreements"].items():
        path = root / ag["path"]
        if not path.is_file():
            print(f"跳过 `{name}`：{ag['path']} 不存在", file=sys.stderr)
            continue
        actual = sha256_file(path)
        if actual != ag["sha256"]:
            changed.append(f"{name}: {ag['sha256'][:12]}… → {actual[:12]}…")
            ag["sha256"] = actual
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    if changed:
        print("哈希已更新：")
        for c in changed:
            print(f"  {c}")
        print("\n版本号没有动。正文如果有实质改动，按 docs/legal/CLA_VERSIONING.md 决定是否 bump。")
    else:
        print("哈希本来就是对的，没有改动。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--event", help="github.event_name")
    ap.add_argument("--policy", default=".github/cla-policy.json")
    ap.add_argument("--ledger", default="docs/legal/cla-signatures.json")
    ap.add_argument("--pr-author", default=None)
    ap.add_argument("--commits-json", default=None,
                    help="`GET /repos/{o}/{r}/pulls/{n}/commits` 的响应文件")
    ap.add_argument("--repo-root", default=".",
                    help="核对协议正文哈希时的仓库根")
    ap.add_argument("--refresh-hashes", action="store_true",
                    help="按磁盘正文重算 policy 里的哈希（只改哈希，不动版本）")
    args = ap.parse_args(argv)

    root = Path(args.repo_root)
    policy_path = Path(args.policy)

    if args.refresh_hashes:
        try:
            return refresh_hashes(policy_path, root)
        except ConfigError as exc:
            print(f"配置错误：{exc}", file=sys.stderr)
            return 2

    if not args.event:
        print("配置错误：--event 是必须的", file=sys.stderr)
        return 2

    policy: dict = {}
    try:
        policy = load_json(policy_path, "cla-policy")
        validate_policy(policy)
        doc_problems = verify_documents(policy, root)
        if doc_problems:
            raise ConfigError("；".join(doc_problems))

        if args.event == "merge_group":
            verdict = decide(args.event, policy, {}, [])
        else:
            ledger = load_json(Path(args.ledger), "cla-signatures")
            commits = []
            if args.commits_json:
                commits = load_json(Path(args.commits_json), "commits JSON")
            contributors, unresolved = collect_contributors(args.pr_author, commits)
            verdict = decide(args.event, policy, ledger, contributors, unresolved)
    except ConfigError as exc:
        verdict = {"event": args.event, "status": "failure", "reason": "config_error",
                   "problems": [str(exc)], "contributors": []}
        _emit(verdict, policy)
        return 2

    _emit(verdict, policy)
    return 0 if verdict["status"] in ("success", "not_applicable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
