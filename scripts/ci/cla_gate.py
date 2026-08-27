#!/usr/bin/env python3
"""CLA 资格判定：这个 PR 里出现的每一个人，是不是都已经签过、或被显式豁免。

    python3 scripts/ci/cla_gate.py \
        --event pull_request \
        --policy .github/cla-policy.json \
        --pr-author someone \
        --commits-json commits.json \
        [--provider-checks-json check-runs.json]

为什么判定要单独成脚本（而不是几行 Bash 或者一个第三方 action 说了算）：

* 它经 `CI fast gate` 参与合并资格，和 `aggregate_gate.py` 同一条纪律——
  判定逻辑散在 workflow 的 Bash 里没法单测，而「CLA 判错」的两头都不便宜：
  放过一个没签的外部贡献，将来那段代码就不能进任何非 AGPL 的发行版；
  错拦一个已签的人，PR 卡住而且没人看得懂为什么。
* **这个脚本不是签署机制，仓库也不保存签署事实。**
  签署的法律权威**只有一个：签名服务商**（provider）。仓库存的是协议正文、
  版本、哈希、政策与显式豁免——不存 signer 名单。早期版本曾在
  `docs/legal/cla-signatures.json` 里手工维护一份，那会和服务商的数据库
  变成两个权威，分叉之后没有任何机制说得清哪一份算数，因此已经删掉。
  「在 PR 里回一句 I agree 然后 grep 评论」那种东西不在这里，也不该有。

判定规则（详见 `decide()`）：

* `--event merge_group`：判 `not_applicable` 并**成功**。资格在 PR 阶段就已经
  验过，队列候选上没有 PR 上下文可查。**这一格必须是成功而不是 skipped**——
  `aggregate_gate.py --mode fast` 把 skipped 一律当失败，这个 job 在
  merge_group 上被跳过会把整个 `CI fast gate` 卡死（tests/ 里有专门的用例钉它）。
* `--event pull_request`：PR 里出现的每一个人类贡献者，要么在 policy 的豁免表
  里被**点名**，要么由 provider 判定为已签。provider 未配置时（当前状态），
  **没有任何人能被判成已签**——非豁免的人类一律阻断，并给出明确指引。
  绝不因为「服务还没接上」就把外部贡献者当成签过。
* 其他事件：配置错误 → 失败。没设计过的上下文不许静默变绿。

fail-closed 的几处（每一处都是「宁可红」）：

* 认不出 GitHub 账号的 co-author（邮箱不是 GitHub noreply 形态）→ 失败，
  让人来认领，而不是猜一个 login 或者当它不存在；
* 豁免只按 login 精确匹配显式名单，**没有「名字像机器人就放行」这种规则**——
  `dependabot[bot]` 能过是因为它在表里写着，不是因为它带个 `[bot]` 后缀；
* policy 里记的文档哈希与磁盘上的文档对不上 → 失败（改了 CLA 正文却没走
  版本流程，见 docs/legal/CLA_VERSIONING.md）；
* 版本带 `-draft` 后缀 → provider 不许被标成已配置（草案上不存在有效签署，
  见 docs/legal/CLA_VERSIONING.md）；
* provider 说了已配置，却拿不到它的 check 结论 → 失败，不猜。

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

#: 草案后缀。带它的版本不可签署，因此也不允许把 provider 标成已配置。
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
    for key in ("schema", "agreements", "exemptions", "provider"):
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
    _validate_provider(policy)


def _validate_provider(policy: dict) -> None:
    """provider 段的形状。

    **草案版本上不许把 provider 标成已配置**——那等于宣称一份还没定稿的
    文本已经在收签名了。这条是结构性的，不靠人记得。
    """
    prov = policy["provider"]
    if not isinstance(prov, dict) or "configured" not in prov:
        raise ConfigError("cla-policy.provider 必须是带 `configured` 的对象")
    if not isinstance(prov["configured"], bool):
        raise ConfigError("cla-policy.provider.configured 必须是布尔")
    if not prov["configured"]:
        return
    for key in ("name", "check_name"):
        if not prov.get(key):
            raise ConfigError(
                f"provider.configured=true 时必须写明 `{key}`——"
                "判定要指向一个具体的、可核对的 check")
    drafts = [f"{k} {ag['version']}" for k, ag in policy["agreements"].items()
              if str(ag["version"]).endswith(DRAFT_SUFFIX)]
    if drafts:
        raise ConfigError(
            f"协议仍是草案（{'、'.join(drafts)}），不许把 provider 标成已配置——"
            "草案上不存在有效签署，见 docs/legal/CLA_VERSIONING.md")


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


def signed_logins(provider_checks: list | None, policy: dict) -> tuple[set[str], str | None]:
    """从 provider 的 check-run 结论里读出「谁算已签」。

    返回 (已签 login 的小写集合, 不可用原因)。provider 未配置时永远是
    (空集, 原因)——**未配置不等于放行**。

    判据只认 provider 自己那条 check 的 `conclusion == "success"`：签署事实的
    权威在服务商，仓库不复制、不缓存、不二次解释它的数据库。
    """
    prov = policy["provider"]
    if not prov["configured"]:
        return set(), "provider_not_configured"
    if provider_checks is None:
        return set(), "provider_checks_unavailable"
    want = prov["check_name"]
    for run in provider_checks:
        if not isinstance(run, dict):
            raise ConfigError("provider check-runs JSON 里有非对象条目")
        if run.get("name") != want:
            continue
        if run.get("status") != "completed":
            return set(), f"provider_check_incomplete:{run.get('status')}"
        if run.get("conclusion") != "success":
            return set(), f"provider_check_not_success:{run.get('conclusion')}"
        # provider 的 check 绿 = 它认为这个 PR 的贡献者资格齐了。
        return {"*"}, None
    return set(), "provider_check_missing"


def _unqualified_detail(reason: str | None, policy: dict) -> str:
    """把「为什么没通过」翻译成维护者和贡献者都能照着做的一句话。

    只写 `CLA check failed` 是最没用的一种红：看的人不知道是自己没签、
    还是服务还没接上、还是判定器配错了。
    """
    prov = policy["provider"]
    if reason == "provider_not_configured":
        return (f"CLA 签名服务尚未启用（{prov.get('note') or 'provider.configured=false'}）"
                "——目前只有 .github/cla-policy.json 里点名豁免的账号能通过。"
                "想贡献请先开一个 issue，维护者会走人工流程；"
                "启用步骤见 docs/legal/CLA_AUTOMATION_SETUP.md")
    if reason == "provider_check_missing":
        return (f"没找到 provider 的 check `{prov.get('check_name')}`——"
                "它可能还没跑完，或者 GitHub App 没装在这个仓库上")
    if reason and reason.startswith("provider_check_incomplete:"):
        return f"provider 的 check 还没跑完（status={reason.split(':', 1)[1]}），等它出结论"
    if reason and reason.startswith("provider_check_not_success:"):
        return (f"provider 的 check 结论是 {reason.split(':', 1)[1]}——"
                "按它给的链接签署 CLA 后重跑")
    if reason == "provider_checks_unavailable":
        return "取不到 provider 的 check 列表（权限或 API 失败）——判定器不猜，直接红"
    return "没有签署记录"


def decide(event: str, policy: dict, provider_checks: list | None,
           contributors: list[dict], unresolved: list[dict] | None = None) -> dict:
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

    signed, why = signed_logins(provider_checks, policy)
    detail_for_missing = _unqualified_detail(why, policy)

    problems: list[str] = []
    for u in unresolved:
        # **认不出账号一律红，但必须红得可操作**：说清是哪个 commit、哪个身份、
        # 为什么解析不出、以及维护者可以怎么处置。只有一句 "CLA check failed"
        # 会让一个合法 PR 永久卡住而没人知道下一步该做什么。
        problems.append(
            f"unresolved: commit `{u['sha']}` 的 {u['kind']} "
            f"`{u['name']} <{u['email']}>` 解析不出 GitHub 账号"
            f"（只有 `<login>@users.noreply.github.com` 与 "
            f"`<id>+<login>@users.noreply.github.com` 两种形态能可靠反解）。"
            f"处置：让本人用绑定该账号的邮箱重新提交该 commit；"
            f"或由维护者确认其身份后，在 .github/cla-policy.json 里为其"
            f"补一条写明理由的显式豁免（仅限确实无需授权的情形）")

    rows = []
    for c in contributors:
        login = c["login"]
        ex = _exemption_for(login, policy)
        if ex is not None:
            rows.append({"login": login, "verdict": "exempt",
                         "detail": f"{ex['kind']}: {ex['reason']}",
                         "sources": c["sources"]})
            continue
        if "*" in signed:
            rows.append({"login": login, "verdict": "signed",
                         "detail": f"provider `{policy['provider']['name']}` 判定已签",
                         "sources": c["sources"]})
            continue
        rows.append({"login": login, "verdict": "missing",
                     "detail": detail_for_missing, "sources": c["sources"]})
        problems.append(f"missing: `{login}` — {detail_for_missing}")

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
        lines += [f"协议正文 [`{ind}`]({ind}) / [`{corp}`]({corp})；"
                  "签署事实的权威是签名服务商，仓库不保存 signer 名单——"
                  "见 [`docs/legal/CLA_AUTOMATION_SETUP.md`]"
                  "(docs/legal/CLA_AUTOMATION_SETUP.md)。", ""]
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
    ap.add_argument("--provider-checks-json", default=None,
                    help="`GET /repos/{o}/{r}/commits/{sha}/check-runs` 的响应文件"
                         "（provider.configured=true 时才需要）")
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
            verdict = decide(args.event, policy, None, [])
        else:
            commits = []
            if args.commits_json:
                commits = load_json(Path(args.commits_json), "commits JSON")
            checks = None
            if args.provider_checks_json:
                raw = load_json(Path(args.provider_checks_json), "provider check-runs JSON")
                # GitHub 的 check-runs 响应是 {"check_runs": [...]}；也接受裸数组。
                checks = raw.get("check_runs", []) if isinstance(raw, dict) else raw
            contributors, unresolved = collect_contributors(args.pr_author, commits)
            verdict = decide(args.event, policy, checks, contributors, unresolved)
    except ConfigError as exc:
        verdict = {"event": args.event, "status": "failure", "reason": "config_error",
                   "problems": [str(exc)], "contributors": []}
        _emit(verdict, policy)
        return 2

    _emit(verdict, policy)
    return 0 if verdict["status"] in ("success", "not_applicable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
