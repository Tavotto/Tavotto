#!/usr/bin/env python3
"""默认分支 Ruleset 的 Merge Queue 迁移工具——默认只读，写入要过三道闸。

    python scripts/ci/merge_queue_ruleset.py inspect
    python scripts/ci/merge_queue_ruleset.py plan  --phase enable-queue
    python scripts/ci/merge_queue_ruleset.py apply --phase enable-queue    --yes
    python scripts/ci/merge_queue_ruleset.py plan  --phase switch-to-gates
    python scripts/ci/merge_queue_ruleset.py apply --phase switch-to-gates --yes

两个阶段，顺序不可交换（完整顺序见 docs/ci/merge-queue-rollout.md）：

* **enable-queue**：加 merge_queue rule + 把 strict（branch must be up to
  date）关掉。**一个旧 required context 都不删**——此时 ruleset 同时要求
  Merge Queue 与全部旧检查，只是 PR 不必再手动追 main。
* **switch-to-gates**：required contexts 收敛为且仅为三个稳定 Gate
  （`CI fast gate` / `CI integration gate` / `CodeQL gate`）。apply 前自动
  核对一串前置条件（三个 Gate 已在 main 上真实产出过 success、workflow 已
  监听 merge_group、merge_queue rule 已存在、strict 已关、Ruleset 自 plan
  之后没被别人改过），任何一项不满足都拒绝写入。

安全设计（每一条都有单测钉着）：

* 不写死 Ruleset ID：按「名称 + target=branch + 条件含默认分支」定位；
  找不到或找到多个都报错，绝不猜。
* tag ruleset（`release tags: immutable`）target 不是 branch，结构上就
  选不中——这个脚本改不到它。
* 只改三样东西：merge_queue rule、strict 开关、required contexts。
  其余 rules（pull_request / deletion / non_fast_forward / 未来新增的任何
  类型）、conditions、bypass_actors、enforcement 一律原样带回。
* **绝不引入 bypass actor**：apply 的请求体里 bypass_actors 永远是读到的
  那份原文。
* plan 把当时的 Ruleset 全文哈希写进 plan 文件；apply 先重读线上、比对
  哈希，别人并发改过就拒绝——绝不拿旧 JSON 盖掉别人刚加的规则。
* 不带 --yes 的 apply 只打印将要做什么，一个写请求都不发。

GitHub 访问全部经本机 `gh api`（借用已登录的凭据，脚本自己不碰 token）。
纯标准库。
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = "Tavotto/Tavotto"
DEFAULT_RULESET_NAME = "main: PR + required checks"

#: switch-to-gates 之后 required contexts 的完整清单——与 workflow 里
#: 三个 Gate job 的 `name:` 逐字相同（tests/test_merge_queue_workflows.py 对拍）。
GATE_CONTEXTS = ["CI fast gate", "CI integration gate", "CodeQL gate"]

#: 三个 Gate 分别由哪个 workflow 产出——apply switch-to-gates 前要确认这些
#: 文件在默认分支上已经监听 merge_group，否则队列里的候选会等一个永远
#: 不会出现的 context。
GATE_WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/codeql.yml")

#: Merge Queue 参数（与 docs/ci/merge-queue-rollout.md 的表一致）。
#: `grouping_strategy: ALLGREEN` 即 UI 上的「Only merge non-failing entries」。
MERGE_QUEUE_PARAMS = {
    "check_response_timeout_minutes": 90,
    "grouping_strategy": "ALLGREEN",
    "max_entries_to_build": 2,
    "max_entries_to_merge": 1,
    "merge_method": "SQUASH",
    "min_entries_to_merge": 1,
    "min_entries_to_merge_wait_minutes": 0,
}

PHASES = ("enable-queue", "switch-to-gates", "set-build-concurrency")

#: set-build-concurrency 的前置条件要读的三样东西（ADR 0043）：源码分支上画布产物已经
#: 不在索引里、marketplace 入口已切到发行分支、发行分支存在。三条都满足，两个互不相关的
#: 前端 PR 才不会因为同一份 HTML 在队列里相撞——并发调到 2 才有意义。
GENERATED_CANVAS_PATH = "codex-plugin/mcp/widget/canvas.html"
MARKETPLACE_PATH = ".agents/plugins/marketplace.json"
PLUGIN_STABLE_BRANCH = "plugin-stable"


class MigrationError(Exception):
    """带人话的失败。任何一条都意味着**什么都没写**。"""


# ---------------------------------------------------------------- gh 访问层


def gh_api(path: str, *, method: str = "GET", body: dict | None = None) -> object:
    """经 `gh api` 打 GitHub REST。测试用假实现替换这个函数。"""
    cmd = ["gh", "api", "-X", method, path]
    stdin = None
    if body is not None:
        cmd += ["--input", "-"]
        stdin = json.dumps(body)
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise MigrationError(f"gh api {method} {path} 失败：{proc.stderr.strip()[:500]}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


# ---------------------------------------------------------------- 读取与定位


def stable_hash(obj: object) -> str:
    """Ruleset JSON 的稳定哈希——并发漂移检测用。"""
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def default_branch(api, repo: str) -> str:
    data = api(f"repos/{repo}")
    branch = data.get("default_branch")
    if not branch:
        raise MigrationError(f"repos/{repo} 里读不到 default_branch")
    return branch


def find_ruleset(api, repo: str, name: str, branch: str) -> dict:
    """按名称 + target=branch + 条件含默认分支定位，返回**完整** Ruleset。

    找不到、找到多个，都报错。绝不按 ID 写死，也绝不「就近选一个」。
    """
    listing = api(f"repos/{repo}/rulesets")
    if not isinstance(listing, list):
        raise MigrationError("rulesets 列表的形状不对")
    candidates = []
    for item in listing:
        if item.get("name") != name or item.get("target") != "branch":
            continue
        full = api(f"repos/{repo}/rulesets/{item['id']}")
        includes = (full.get("conditions") or {}).get("ref_name", {}).get("include", [])
        if "~DEFAULT_BRANCH" in includes or f"refs/heads/{branch}" in includes:
            candidates.append(full)
    if not candidates:
        raise MigrationError(
            f"找不到名为「{name}」、作用于默认分支的 branch ruleset——"
            f"名字改过的话用 --ruleset-name 指定"
        )
    if len(candidates) > 1:
        ids = [c["id"] for c in candidates]
        raise MigrationError(f"同名 ruleset 有 {len(candidates)} 个（{ids}）——先在网页上收敛成一个")
    return candidates[0]


def _rule(ruleset: dict, rtype: str) -> dict | None:
    for rule in ruleset.get("rules", []):
        if rule.get("type") == rtype:
            return rule
    return None


# ---------------------------------------------------------------- 两个阶段的变换


def build_enable_queue(current: dict) -> dict:
    """当前 Ruleset → 加 merge_queue + strict=false，其余逐字保留。"""
    updated = copy.deepcopy(current)
    rsc = _rule(updated, "required_status_checks")
    if rsc is None:
        raise MigrationError(
            "当前 ruleset 里没有 required_status_checks rule——这不是预期的形状，先人工确认"
        )
    rsc.setdefault("parameters", {})["strict_required_status_checks_policy"] = False

    mq = _rule(updated, "merge_queue")
    if mq is None:
        updated["rules"].append({"type": "merge_queue", "parameters": dict(MERGE_QUEUE_PARAMS)})
    else:
        mq["parameters"] = dict(MERGE_QUEUE_PARAMS)
    _assert_untouched(current, updated)
    return updated


def build_switch_to_gates(current: dict) -> dict:
    """当前 Ruleset → required contexts 收敛为三个 Gate。前提在这里就查一轮。"""
    if _rule(current, "merge_queue") is None:
        raise MigrationError(
            "ruleset 里还没有 merge_queue rule——先跑 enable-queue。"
            "只收敛 contexts 不强制队列，旧 main 上绿过的 PR 仍可直接合并"
        )
    rsc_now = _rule(current, "required_status_checks")
    if rsc_now is None:
        raise MigrationError("当前 ruleset 里没有 required_status_checks rule")
    if rsc_now.get("parameters", {}).get("strict_required_status_checks_policy", True):
        raise MigrationError("strict 还开着——先跑 enable-queue（strict 只能与强制队列同时关）")

    updated = copy.deepcopy(current)
    rsc = _rule(updated, "required_status_checks")
    rsc["parameters"]["required_status_checks"] = [{"context": c} for c in GATE_CONTEXTS]
    _assert_untouched(current, updated)
    return updated


def build_set_build_concurrency(current: dict, max_entries_to_build: int) -> dict:
    """当前 Ruleset → 只改 merge_queue.max_entries_to_build，其余逐字保留。

    「构建并发」与「每次合并数」是两个参数：这里**只动前者**；`max_entries_to_merge` /
    `min_entries_to_merge` / grouping_strategy / 超时原样带回（_assert_untouched 之外再钉一次）。
    不改 required contexts、不动 strict、不引入 bypass。
    """
    if not isinstance(max_entries_to_build, int) or not 1 <= max_entries_to_build <= 5:
        raise MigrationError(
            f"max_entries_to_build 只接受 1–5，拿到 {max_entries_to_build!r}——先开 2，不直接开 10"
        )
    mq = _rule(current, "merge_queue")
    if mq is None:
        raise MigrationError("ruleset 里没有 merge_queue rule——先跑 enable-queue")
    updated = copy.deepcopy(current)
    params = _rule(updated, "merge_queue").setdefault("parameters", {})
    params["max_entries_to_build"] = max_entries_to_build
    _assert_untouched(current, updated)
    before = {k: v for k, v in mq.get("parameters", {}).items() if k != "max_entries_to_build"}
    after = {k: v for k, v in params.items() if k != "max_entries_to_build"}
    if before != after:
        raise MigrationError(
            "变换意外改动了 merge_queue 的其它参数——这个阶段只许动 max_entries_to_build"
        )
    if _rule(current, "required_status_checks") != _rule(updated, "required_status_checks"):
        raise MigrationError("变换意外改动了 required_status_checks")
    return updated


def _assert_untouched(current: dict, updated: dict) -> None:
    """变换只许碰三样东西；这里把「没碰别的」变成硬断言而不是自觉。"""
    for key in ("name", "target", "enforcement", "conditions", "bypass_actors"):
        if updated.get(key) != current.get(key):
            raise MigrationError(f"变换意外改动了 {key}——这个脚本不许碰它")
    managed = {"merge_queue", "required_status_checks"}
    before = [r for r in current.get("rules", []) if r.get("type") not in managed]
    after = [r for r in updated.get("rules", []) if r.get("type") not in managed]
    if before != after:
        raise MigrationError(
            "变换意外改动了托管之外的 rule（pull_request / deletion / "
            "non_fast_forward / 未来新增的类型都不归这个脚本管）"
        )


# ---------------------------------------------------------------- 前置条件


def check_gates_on_main(api, repo: str, branch: str) -> list[str]:
    """三个 Gate 必须已在默认分支最新 commit 上真实产出 success。"""
    head = api(f"repos/{repo}/commits/{branch}")
    sha = head.get("sha")
    if not sha:
        raise MigrationError(f"读不到 {branch} 的最新 SHA")
    conclusions: dict[str, str] = {}
    page = 1
    while True:
        data = api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}")
        runs = data.get("check_runs", [])
        for run in runs:
            # 同名多次运行取最新一条（列表按最近在前；只记第一次见到的）
            conclusions.setdefault(run.get("name", ""), run.get("conclusion") or "pending")
        if len(runs) < 100:
            break
        page += 1
    problems = []
    for gate in GATE_CONTEXTS:
        got = conclusions.get(gate)
        if got is None:
            problems.append(f"「{gate}」从未出现在 {branch}@{sha[:12]} 的 check runs 里")
        elif got != "success":
            problems.append(f"「{gate}」在 {branch}@{sha[:12]} 上的结论是 {got}，不是 success")
    return problems


def check_workflows_listen_to_merge_group(api, repo: str, branch: str) -> list[str]:
    """产出 Gate 的 workflow 必须已在**默认分支**上监听 merge_group。

    读的是线上默认分支，不是本地工作副本——本地领先于 main 时，本地文件
    说明不了队列里会发生什么。
    """
    problems = []
    for path in GATE_WORKFLOWS:
        data = api(f"repos/{repo}/contents/{path}?ref={branch}")
        content = data.get("content")
        if not content:
            problems.append(f"{branch} 上读不到 {path}")
            continue
        text = base64.b64decode(content).decode("utf-8", errors="replace")
        if "merge_group:" not in text or "checks_requested" not in text:
            problems.append(f"{branch} 上的 {path} 还没监听 merge_group.checks_requested")
    return problems


def check_source_decoupled_from_the_plugin(api, repo: str, branch: str) -> list[str]:
    """并发调到 2 的前提（ADR 0043）：默认分支上画布产物不在索引里、marketplace 入口指向
    发行分支、发行分支存在。少一条，两个前端 PR 仍会为同一份 HTML 在队列里相撞。"""
    problems: list[str] = []
    try:
        api(f"repos/{repo}/contents/{GENERATED_CANVAS_PATH}?ref={branch}")
        problems.append(
            f"{branch} 上还跟踪着 {GENERATED_CANVAS_PATH}——画布产物还在源码分支里（ADR 0043 的 PR B 未落地）"
        )
    except MigrationError as exc:
        if "404" not in str(exc) and "Not Found" not in str(exc):
            raise
    try:
        raw = api(f"repos/{repo}/contents/{MARKETPLACE_PATH}?ref={branch}")
        content = base64.b64decode(raw.get("content", "")).decode("utf-8")
        data = json.loads(content)
        entry = next(p for p in data.get("plugins", []) if p.get("name") == "tavotto")
        src = entry.get("source")
        ok = (
            isinstance(src, dict)
            and src.get("source") == "git-subdir"
            and src.get("ref") == PLUGIN_STABLE_BRANCH
        )
        if not ok:
            problems.append(
                f"{MARKETPLACE_PATH} 的插件来源还不是 git-subdir → {PLUGIN_STABLE_BRANCH}：{src}"
            )
    except (MigrationError, ValueError, StopIteration) as exc:
        problems.append(f"读不出 {branch} 上的 {MARKETPLACE_PATH}：{exc}")
    try:
        api(f"repos/{repo}/branches/{PLUGIN_STABLE_BRANCH}")
    except MigrationError as exc:
        problems.append(f"发行分支 {PLUGIN_STABLE_BRANCH} 不存在：{exc}")
    return problems


def preconditions(api, repo: str, branch: str, phase: str, current: dict) -> list[str]:
    problems: list[str] = []
    if phase == "set-build-concurrency":
        problems += check_source_decoupled_from_the_plugin(api, repo, branch)
        if _rule(current, "merge_queue") is None:
            problems.append("ruleset 里还没有 merge_queue rule")
        return problems
    if phase == "enable-queue":
        # 队列一开，候选就要在 merge_group 上等 required contexts；
        # workflow 没监听 merge_group 的话，每个候选都白等 90 分钟。
        problems += check_workflows_listen_to_merge_group(api, repo, branch)
    elif phase == "switch-to-gates":
        problems += check_workflows_listen_to_merge_group(api, repo, branch)
        problems += check_gates_on_main(api, repo, branch)
        if _rule(current, "merge_queue") is None:
            problems.append("ruleset 里还没有 merge_queue rule")
        rsc = _rule(current, "required_status_checks")
        if rsc and rsc.get("parameters", {}).get("strict_required_status_checks_policy", True):
            problems.append("strict 还开着")
    return problems


# ---------------------------------------------------------------- 命令


def plan_path(phase: str) -> Path:
    return Path(f"ruleset-plan-{phase}.json")


def cmd_inspect(api, repo: str, name: str) -> int:
    branch = default_branch(api, repo)
    ruleset = find_ruleset(api, repo, name, branch)
    rsc = _rule(ruleset, "required_status_checks") or {}
    params = rsc.get("parameters", {})
    print(json.dumps(ruleset, ensure_ascii=False, indent=2))
    print(f"\n# ruleset {ruleset['id']}「{ruleset['name']}」→ {branch}", file=sys.stderr)
    print(f"# strict: {params.get('strict_required_status_checks_policy')}", file=sys.stderr)
    print(f"# merge_queue: {'有' if _rule(ruleset, 'merge_queue') else '无'}", file=sys.stderr)
    print(
        f"# required contexts: {len(params.get('required_status_checks', []))} 个", file=sys.stderr
    )
    print(f"# bypass actors: {ruleset.get('bypass_actors')}", file=sys.stderr)
    print(f"# hash: {stable_hash(ruleset)}", file=sys.stderr)
    return 0


def _builder(phase: str, max_entries_to_build: int | None):
    if phase == "set-build-concurrency":
        if max_entries_to_build is None:
            raise MigrationError("set-build-concurrency 需要 --max-entries-to-build N")
        return lambda current: build_set_build_concurrency(current, max_entries_to_build)
    return {"enable-queue": build_enable_queue, "switch-to-gates": build_switch_to_gates}[phase]


def cmd_plan(
    api, repo: str, name: str, phase: str, out: Path, max_entries_to_build: int | None = None
) -> int:
    branch = default_branch(api, repo)
    current = find_ruleset(api, repo, name, branch)
    build = _builder(phase, max_entries_to_build)
    updated = build(current)
    plan = {
        "phase": phase,
        "max_entries_to_build": max_entries_to_build,
        "repo": repo,
        "default_branch": branch,
        "ruleset_id": current["id"],
        "ruleset_name": current["name"],
        "base_hash": stable_hash(current),
        "updated": updated,
    }
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    _describe_diff(current, updated)
    print(
        f"\nplan 已写入 {out}（base_hash {plan['base_hash'][:16]}…）。"
        f"apply 前会重读线上并比对这个哈希。"
    )
    return 0


def _describe_diff(current: dict, updated: dict) -> None:
    cur_rsc = _rule(current, "required_status_checks")["parameters"]
    new_rsc = _rule(updated, "required_status_checks")["parameters"]
    cur_ctx = [c["context"] for c in cur_rsc.get("required_status_checks", [])]
    new_ctx = [c["context"] for c in new_rsc.get("required_status_checks", [])]
    print("将要做的改动：")
    print(
        f"  strict: {cur_rsc.get('strict_required_status_checks_policy')} → "
        f"{new_rsc.get('strict_required_status_checks_policy')}"
    )
    print(
        f"  merge_queue: {'有' if _rule(current, 'merge_queue') else '无'} → "
        f"{'有' if _rule(updated, 'merge_queue') else '无'}"
    )
    cur_mq = (_rule(current, "merge_queue") or {}).get("parameters", {})
    new_mq = (_rule(updated, "merge_queue") or {}).get("parameters", {})
    for key in sorted(set(cur_mq) | set(new_mq)):
        if cur_mq.get(key) != new_mq.get(key):
            print(f"  merge_queue.{key}: {cur_mq.get(key)} → {new_mq.get(key)}")
    if cur_ctx != new_ctx:
        print(f"  required contexts: {len(cur_ctx)} 个 → {len(new_ctx)} 个")
        for c in cur_ctx:
            if c not in new_ctx:
                print(f"    - {c}")
        for c in new_ctx:
            if c not in cur_ctx:
                print(f"    + {c}")
    else:
        print(f"  required contexts: 不变（{len(cur_ctx)} 个）")
    print("  其余 rules / conditions / bypass_actors：原样保留")


def cmd_apply(
    api,
    repo: str,
    name: str,
    phase: str,
    plan_file: Path,
    yes: bool,
    max_entries_to_build: int | None = None,
) -> int:
    if not plan_file.is_file():
        raise MigrationError(f"没有 plan 文件 {plan_file}——先跑 plan --phase {phase}")
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    if plan.get("phase") != phase:
        raise MigrationError(f"plan 文件是 {plan.get('phase')} 阶段的，与 --phase {phase} 不符")
    if (
        phase == "set-build-concurrency"
        and plan.get("max_entries_to_build") != max_entries_to_build
    ):
        raise MigrationError(
            f"plan 文件是 max_entries_to_build={plan.get('max_entries_to_build')} 的，"
            f"与 --max-entries-to-build {max_entries_to_build} 不符"
        )
    if plan.get("repo") != repo:
        raise MigrationError(f"plan 文件属于 {plan.get('repo')}，不是 {repo}")

    branch = default_branch(api, repo)
    current = find_ruleset(api, repo, name, branch)
    if current["id"] != plan["ruleset_id"]:
        raise MigrationError("定位到的 ruleset 与 plan 里的不是同一个——重新生成 plan")
    if stable_hash(current) != plan["base_hash"]:
        raise MigrationError(
            "Ruleset 自 plan 之后被改过（哈希不符）——拿旧 JSON 盖上去会抹掉"
            "别人刚做的修改。重新跑 plan，人工读一遍 diff 再 apply"
        )

    problems = preconditions(api, repo, branch, phase, current)
    if problems:
        raise MigrationError("前置条件不满足，拒绝写入：\n  - " + "\n  - ".join(problems))

    # **plan 文件不是权威，只是确认物。** 发给 GitHub 的 body 一律从**线上
    # 现状**重算（同一套 build_* 变换，_assert_untouched 在里面守着），
    # plan["updated"] 必须与重算结果逐字节相等——否则说明 plan 被手改过、
    # 或生成它的脚本版本与现在不同。只抽查 bypass_actors 那种点名单是
    # 挡不住的：被编辑的 plan 可以抹掉 pull_request rule、换掉 conditions
    # 或 contexts，哈希核对的是 current、根本量不到它（#119 评审 P1）。
    build = _builder(phase, max_entries_to_build)
    updated = build(current)
    if plan["updated"] != updated:
        raise MigrationError(
            "plan 的 updated 与从线上现状重算出的变换不一致——plan 文件被"
            "编辑过，或生成它的脚本版本与当前不同。重新跑 plan 并人工读 diff"
        )
    if updated.get("target") != "branch":
        raise MigrationError("变换结果的 target 不是 branch——绝不写 tag ruleset")

    _describe_diff(current, updated)
    if not yes:
        print("\n未传 --yes：以上是将要做的全部改动，一个写请求都没发。")
        return 3

    body = {
        "name": updated["name"],
        "target": updated["target"],
        "enforcement": updated["enforcement"],
        "conditions": updated["conditions"],
        "rules": updated["rules"],
        "bypass_actors": updated.get("bypass_actors", []),
    }
    api(f"repos/{repo}/rulesets/{current['id']}", method="PUT", body=body)
    print(f"\n已写入 ruleset {current['id']}（{phase}）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("command", choices=("inspect", "plan", "apply"))
    ap.add_argument("--phase", choices=PHASES, help="plan / apply 必填")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--ruleset-name", default=DEFAULT_RULESET_NAME)
    ap.add_argument(
        "--plan-file",
        type=Path,
        default=None,
        help="plan 的输出 / apply 的输入（默认 ruleset-plan-<phase>.json）",
    )
    ap.add_argument("--yes", action="store_true", help="apply 时真的写入；不带它 = 只演练")
    ap.add_argument(
        "--max-entries-to-build",
        type=int,
        default=None,
        help="set-build-concurrency 阶段的目标值（只改这一个字段；先开 2）",
    )
    args = ap.parse_args(argv)

    try:
        if args.command == "inspect":
            return cmd_inspect(gh_api, args.repo, args.ruleset_name)
        if not args.phase:
            raise MigrationError(f"{args.command} 需要 --phase {'/'.join(PHASES)}")
        plan_file = args.plan_file or plan_path(args.phase)
        if args.command == "plan":
            return cmd_plan(
                gh_api,
                args.repo,
                args.ruleset_name,
                args.phase,
                plan_file,
                args.max_entries_to_build,
            )
        return cmd_apply(
            gh_api,
            args.repo,
            args.ruleset_name,
            args.phase,
            plan_file,
            args.yes,
            args.max_entries_to_build,
        )
    except MigrationError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
