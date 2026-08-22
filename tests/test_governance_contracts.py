"""治理契约的看护：标签清单、P2 issue 表单、Codex review 门禁。

这三样东西的共同点是**坏掉之后没有任何症状**——标签少一个、表单少一问、
门禁把 unknown 当成 P3，都不会有人当场发现，只会在几周后发现「那条退出
条件其实一直没法查询」。所以每条用例都尽量钉住「坏掉之后会怎样」。

判据全文：
  docs/engineering/review-severity-policy.md
  docs/engineering/p2-lifecycle.md
  docs/engineering/codex-review-policy.md
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_DIR = ROOT / "scripts" / "ci"
ADMIN_DIR = ROOT / "scripts" / "admin"
sys.path.insert(0, str(CI_DIR))
sys.path.insert(0, str(ADMIN_DIR))

import codex_review_gate as CG  # noqa: E402
import sync_github_labels as SL  # noqa: E402

LABELS_YML = ROOT / ".github" / "labels.yml"
P2_FORM = ROOT / ".github" / "ISSUE_TEMPLATE" / "p2.yml"
PR_TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
GATE_WF = ROOT / ".github" / "workflows" / "codex-review-gate.yml"


# ── 标签清单 ──────────────────────────────────────────────────────────────

def test_label_manifest_parses_and_covers_all_four_dimensions():
    labels = SL.parse_manifest(LABELS_YML.read_text(encoding="utf-8"))
    names = {x["name"] for x in labels}
    # 四个维度缺一不可：少了 disposition，P2 就又变回「记下来了」。
    assert {"severity:P0", "severity:P1", "severity:P2", "severity:P3"} <= names
    assert "release:blocker" in names
    assert len({n for n in names if n.startswith("area:")}) >= 6
    assert {"disposition:fix-now", "disposition:guard", "disposition:patch-train",
            "disposition:minor-release", "disposition:accepted-limitation",
            "disposition:false-positive"} <= names


def test_label_descriptions_state_a_criterion_not_a_synonym():
    """description 要写判据。「severity:P1 = 严重」等于没写。

    判据是弱的（长度 + 不得只是名字的复述），但它挡得住最常见的退化：
    有人加一个标签，描述里填标签名本身。
    """
    for x in SL.parse_manifest(LABELS_YML.read_text(encoding="utf-8")):
        d = x["description"]
        assert len(d) >= 12, f"{x['name']} 的描述太短：{d!r}"
        bare = x["name"].split(":", 1)[1].replace("-", " ")
        assert d.strip().lower() != bare, f"{x['name']} 的描述只是名字的复述"


def test_manifest_parser_refuses_instead_of_silently_skipping():
    """解析不了的行必须**抛**，不能跳过。

    静默跳过的后果是「同步跑成功了，但那个标签根本没建」——而人会以为它建了。
    这正是本仓库反复撞到的空门禁形状。
    """
    with pytest.raises(SL.ManifestError):
        SL.parse_manifest("labels:\n  - name: \"a\"\n    color: \"fff\"\n"
                          "    description: \"x\"\n")          # 颜色不是 6 位
    with pytest.raises(SL.ManifestError):
        SL.parse_manifest("labels:\n  - name: \"a\"\n    color: \"ffffff\"\n")  # 缺 description
    with pytest.raises(SL.ManifestError):
        SL.parse_manifest("labels:\n  - name: a\n")            # 值没加引号
    with pytest.raises(SL.ManifestError):
        SL.parse_manifest("labels:\n  - name: \"a\"\n    color: \"ffffff\"\n"
                          "    description: \"x\"\n    这一行看不懂\n")


def test_plan_never_deletes_a_label_it_does_not_know():
    """同步只增改自己清单里的，别人建的一律保留。

    一个自动化脚本不该替人决定「你那个标签没用」——删标签会连带把它标过的
    issue 的历史一起弄丢，而那是不可逆的。
    """
    desired = [{"name": "severity:P0", "color": "b60205", "description": "x" * 20}]
    remote = [{"name": "severity:P0", "color": "b60205", "description": "x" * 20},
              {"name": "someone-elses", "color": "000000", "description": "别动"}]
    p = SL.plan(desired, remote)
    assert p["foreign"] == ["someone-elses"]
    assert p["create"] == [] and p["update"] == []
    # plan 的返回值里**根本没有 delete 这个键**——不是「有但没用」，是结构上没有。
    assert "delete" not in p


def test_plan_is_idempotent():
    desired = SL.parse_manifest(LABELS_YML.read_text(encoding="utf-8"))
    remote = [dict(d) for d in desired]
    p = SL.plan(desired, remote)
    assert not p["create"] and not p["update"]
    assert len(p["unchanged"]) == len(desired)


# ── P2 issue 表单 ─────────────────────────────────────────────────────────

def test_p2_form_asks_every_question_the_policy_requires():
    """表单少一问，就有一个维度永远填不上。"""
    t = P2_FORM.read_text(encoding="utf-8")
    ids = set(re.findall(r"^    id: (\S+)", t, re.M))
    required = {"repro", "minimal", "actual", "expected", "scope", "silent",
                "damage", "introduced", "guard", "family", "acceptance",
                "regression_proof", "milestone", "disposition", "next_action"}
    assert required <= ids, f"表单缺了这几问：{sorted(required - ids)}"


def test_p2_form_covers_the_seven_supported_targets():
    t = P2_FORM.read_text(encoding="utf-8")
    for target in ("Windows x64", "macOS arm64", "Linux browser beta",
                   "bundled runtime", "minimum matplotlib", "browser runtime",
                   "Codex MCP"):
        assert target in t, f"支持范围少了 {target}"


def test_p2_form_dispositions_match_the_labels():
    """表单里的 disposition 选项与标签清单**必须逐一对应**。

    对不上的表现是：用户在表单里选了一个不存在的 disposition，
    于是那条 issue 永远没有对应的标签，也就永远查询不到。
    """
    t = P2_FORM.read_text(encoding="utf-8")
    block = t.split("id: disposition")[1]
    labels = {x["name"] for x in SL.parse_manifest(LABELS_YML.read_text(encoding="utf-8"))}
    for name in sorted(n for n in labels if n.startswith("disposition:")):
        short = name.split(":", 1)[1]
        assert re.search(rf'"\s*{re.escape(short)}\s*[：:]', block), \
            f"表单的 disposition 选项里没有 {short}"


def test_pr_template_carries_the_review_and_risk_sections():
    t = PR_TEMPLATE.read_text(encoding="utf-8")
    for needle in ("Scope is frozen", "Codex round 1", "Codex round 2",
                   "Deferred P2 issues opened", "Regression proof done",
                   "no new user-facing capability", "enlarge the state space",
                   "full-ci"):
        assert needle in t, f"PR 模板缺了：{needle}"


# ── Codex review 门禁 ─────────────────────────────────────────────────────

def _strip_yaml_comments(text: str) -> str:
    """去掉 YAML 的整行注释与行尾注释。

    判据只该看**会被执行的那部分**。引号内的 `#` 不是注释，所以要按引号
    状态走一遍，不能简单 split("#")。
    """
    out = []
    for line in text.splitlines():
        buf, quote = [], None
        for ch in line:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf.append(ch)
            elif ch == "#":
                break
            else:
                buf.append(ch)
        out.append("".join(buf).rstrip())
    return "\n".join(out)


def test_severity_is_read_from_the_badge():
    assert CG.severity_of(
        "**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange)</sub></sub>  X**") == "P1"
    assert CG.severity_of("![P2 Badge](https://x)  something") == "P2"
    assert CG.severity_of("![p0 badge](https://x)") == "P0"


def test_unknown_severity_is_none_not_a_silent_p3():
    """读不出来必须回 None。

    悄悄当成 P3 是这道门禁最容易长出来的空转形态：一次 Codex 换了 badge
    的写法，从此**所有**发现都变成「无关紧要」，而门禁全程报平安。
    """
    assert CG.severity_of("完全没有分级标记的一段正文") is None
    assert CG.severity_of("") is None
    # 正文靠后位置提到的 P2 不算这条 thread 自己的分级
    assert CG.severity_of("x" * 300 + " P2 ") is None


def _analysis(**kw):
    base = {"pr": 1, "title": "t", "is_draft": False, "head": "abc",
            "codex_ran": True, "rounds": 1, "round_commits": ["a" * 40],
            "resolved": 0, "unresolved": {},
            "counts": {"P0": 0, "P1": 0, "P2": 0, "P3": 0, "unknown": 0}}
    base.update(kw)
    return base


def test_unresolved_p1_fails_the_gate():
    a = _analysis(counts={"P0": 0, "P1": 1, "P2": 0, "P3": 0, "unknown": 0},
                  unresolved={"P1": [{"title": "boom", "url": "u"}]})
    v = CG.verdict(a, max_rounds=2)
    assert v["conclusion"] == "failure"
    assert any("P1" in e for e in v["errors"])


def test_unresolved_p2_only_warns_and_does_not_fail():
    """**这是整道门禁的核心取舍。**

    把 P2 做成硬失败，等于逼着每条 PR 一直改到 Codex 无话可说——而那正是
    要停掉的循环（PR #48 被 review 了 18 轮，#53 十五轮，80% 的发现是 P2）。
    P2 的正确出口是「有 disposition」，而 disposition 可以是「转 issue」。
    """
    a = _analysis(counts={"P0": 0, "P1": 0, "P2": 5, "P3": 0, "unknown": 0},
                  unresolved={"P2": [{"title": "x", "url": "u"}] * 5})
    v = CG.verdict(a, max_rounds=2)
    assert v["conclusion"] == "success"
    assert v["errors"] == []
    assert any("P2" in w for w in v["warnings"])


def test_unknown_severity_warns_and_is_still_counted():
    a = _analysis(counts={"P0": 0, "P1": 0, "P2": 0, "P3": 0, "unknown": 2},
                  unresolved={"unknown": [{"title": "x", "url": None}] * 2})
    v = CG.verdict(a, max_rounds=2)
    assert v["conclusion"] == "success"       # 不硬失败……
    assert any("读不出严重度" in w for w in v["warnings"])   # ……但必须说出来


def test_too_many_rounds_warns_but_does_not_fail():
    a = _analysis(rounds=7, round_commits=["a" * 40] * 7)
    v = CG.verdict(a, max_rounds=2)
    assert v["conclusion"] == "success"
    assert any("7 轮" in w for w in v["warnings"])


def test_codex_absent_is_neutral_not_failure():
    """Codex 没跑**绝不能**让 PR 永久红。

    usage limit、App 掉线、纯文档 PR 都会走到这里。一道会因为外部服务
    不在线而卡死的门禁，第一次卡死就会被人摘掉，摘掉之后就再没人跑了。
    """
    v = CG.verdict(_analysis(codex_ran=False, rounds=0, round_commits=[]),
                   max_rounds=2)
    assert v["conclusion"] == "neutral"
    assert v["errors"] == []


def test_rounds_are_counted_by_reviewed_commit_not_by_comment_count():
    """一轮 review 会产出十几条 thread comment。

    按条数数会把一轮报成十几轮，而那个数字会被拿去判「超没超两轮」。
    """
    pr = {
        "number": 1, "title": "t", "isDraft": False, "headRefOid": "h",
        "reviews": {"nodes": [
            {"author": {"login": "chatgpt-codex-connector"}, "commit": {"oid": "a" * 40}},
            {"author": {"login": "chatgpt-codex-connector"}, "commit": {"oid": "a" * 40}},
            {"author": {"login": "chatgpt-codex-connector"}, "commit": {"oid": "b" * 40}},
            {"author": {"login": "erwanjun"}, "commit": {"oid": "c" * 40}},
        ]},
        "reviewThreads": {"nodes": [
            {"isResolved": False, "isOutdated": False, "comments": {"nodes": [
                {"author": {"login": "chatgpt-codex-connector"},
                 "body": "![P2 Badge](x) **一条**", "url": "u1"}]}},
            {"isResolved": False, "isOutdated": False, "comments": {"nodes": [
                {"author": {"login": "chatgpt-codex-connector"},
                 "body": "![P2 Badge](x) **两条**", "url": "u2"}]}},
            {"isResolved": True, "isOutdated": False, "comments": {"nodes": [
                {"author": {"login": "chatgpt-codex-connector"},
                 "body": "![P1 Badge](x) **修好了**", "url": "u3"}]}},
            # 人开的 thread 不归这道门禁管
            {"isResolved": False, "isOutdated": False, "comments": {"nodes": [
                {"author": {"login": "erwanjun"}, "body": "顺便问一下", "url": "u4"}]}},
        ]},
    }
    a = CG.analyse(pr)
    assert a["rounds"] == 2, "两个不同的 commit = 两轮，不是四轮"
    assert a["resolved"] == 1
    assert a["counts"]["P2"] == 2
    assert a["counts"]["P1"] == 0, "已 resolve 的不算未处置"


def test_the_bot_login_is_pinned():
    """登录名一变，这道门禁会静默变成「这轮很干净」。

    钉住常量本身：改它必须同时改这条用例，于是会被人看见。
    需要确证时用 `--require-bot`（默认不开——那会让纯文档 PR 一起变红）。
    """
    assert "chatgpt-codex-connector" in CG.CODEX_LOGINS
    assert "chatgpt-codex-connector[bot]" in CG.CODEX_LOGINS


def test_gate_workflow_does_not_rerun_on_every_push():
    """`synchronize` 不在触发列表里。

    每次 push 都跑一遍这道门禁，会让「轮次超了」的告警在你正在收敛的过程中
    反复刷屏——而收敛正是我们希望发生的事。
    """
    t = GATE_WF.read_text(encoding="utf-8")
    types = re.search(r"pull_request:\s*\n(?:\s*#.*\n)*\s*types:\s*\[([^\]]*)\]", t)
    assert types, "读不出 pull_request 的 types"
    assert "synchronize" not in types.group(1)
    assert "ready_for_review" in types.group(1)


def test_gate_workflow_uses_the_runner_python_not_a_venv():
    """诊断门禁不该把自己的成败押在一次 pip install 上。

    与 #61 同一个形状：汇总步骤恰恰在有失败要汇总时自己挂掉。
    """
    # **先剥注释再判。** 第一版直接在全文里搜 "pip install"，结果被自己那句
    # 「不该把成败押在一次 pip install 上」的注释满足了——判据被散文满足，
    # 是本仓库明令禁止的形状（CLAUDE.md「判据的主语」一节）。
    t = _strip_yaml_comments(GATE_WF.read_text(encoding="utf-8"))
    assert "python3 scripts/ci/codex_review_gate.py" in t
    assert "pip install" not in t
    assert "actions/setup-python" not in t


def test_gate_only_needs_read_permissions():
    t = GATE_WF.read_text(encoding="utf-8")
    block = t.split("permissions:")[1].split("concurrency:")[0]
    assert "write" not in block, "这道门禁只读，不该有任何 write 权限"


# ── 文档一致性 ────────────────────────────────────────────────────────────

def test_the_policy_docs_exist_and_reference_each_other():
    """四份文档互相指向，别再长出第二份互相竞争的规范。"""
    docs = {p.name: (ROOT / "docs" / "engineering" / p.name).read_text(encoding="utf-8")
            for p in (ROOT / "docs" / "engineering").glob("*.md")}
    assert {"p2-lifecycle.md", "review-severity-policy.md",
            "codex-review-policy.md", "p2-fix-train.md"} <= set(docs)
    assert "review-severity-policy.md" in docs["p2-lifecycle.md"]
    assert "p2-lifecycle.md" in docs["codex-review-policy.md"]
    # 严重度政策自称唯一权威，而 1.0 那份是它的摘要——这一条要写在明处
    assert "唯一权威" in docs["review-severity-policy.md"]


def test_readiness_doc_points_at_the_policy_instead_of_forking_it():
    """**退出条件那一节**必须指向唯一权威，不是「全文某处提过」。

    第一版判的是「全文里有没有这个路径」，而 §2 恰好也提到了它，于是把
    §1.1 的指向整个删掉，用例照样绿——判据的主语错了：该问「§1.1 指不指」，
    我问的是「全文提没提」。这正是 CLAUDE.md 那一节说的形状。
    """
    t = (ROOT / "docs" / "1.0-release-readiness.md").read_text(encoding="utf-8")
    # 退出条件那一节 = 从 `## 1. 退出条件` 到下一个 `## `
    m = re.search(r"^## 1\. 退出条件.*?(?=^## )", t, re.M | re.S)
    assert m, "读不出「## 1. 退出条件」这一节"
    section = m.group(0)
    assert "docs/engineering/review-severity-policy.md" in section, \
        "退出条件这一节必须指向严重度政策，否则两份判据会各自演进"
    for path in ("docs/engineering/p2-lifecycle.md",
                 "docs/engineering/codex-review-policy.md",
                 "docs/engineering/p2-fix-train.md"):
        assert path in section, f"退出条件这一节没有指向 {path}"


# ── Codex 第一轮逮到的（2026-08-23）────────────────────────────────────

def test_the_gate_runs_after_codex_submits_its_review():
    """**门禁必须在 Codex 提交 review 之后跑，否则它是空转。**

    点 Ready 会同时启动门禁与 Codex 的第一轮，而门禁只查一次 API、几秒
    就跑完 —— 那时 Codex 还没提交任何东西，`codex_ran` 是 false，
    门禁按设计回 neutral 并退出 0。于是它**永远不会真正检查任何一条发现**，
    而且全绿。

    一道检查 review 的门禁，自己栽在「跑得比被检查的东西还早」上。
    """
    t = _strip_yaml_comments(GATE_WF.read_text(encoding="utf-8"))
    assert re.search(r"^\s*pull_request_review:", t, re.M), (
        "门禁没有挂在 pull_request_review 上 —— 它会跑在 Codex 之前，"
        "拿到 codex_ran=false 然后 neutral 退出")
    m = re.search(r"pull_request_review:\s*\n\s*types:\s*\[([^\]]*)\]", t)
    assert m and "submitted" in m.group(1), "至少要接 submitted"


def test_review_threads_are_paginated():
    """GraphQL connection 一次最多 100 条。

    超过 100 之后第 101 条起的 unresolved P0/P1 **整个看不见**，
    门禁于是报 success —— 一道漏掉阻断项的门禁比没有门禁更坏。
    """
    # **判 QUERY 这个常量本身，不是「文件里有没有这几个词」。**
    # 第一版搜全文，而 `_gh_graphql` 的翻页循环里就有 `info.get("hasNextPage")`
    # ——把 GraphQL 查询里的 `pageInfo` 整行删掉，用例照样绿。
    # 判据的主语第 N 次错了：该问「查询请不请求下一页」。
    q = CG.QUERY
    assert "pageInfo" in q and "hasNextPage" in q, "reviewThreads 的查询没有请求 pageInfo"
    assert "$cursor" in q, "查询没有游标参数"
    assert "after:$cursor" in q.replace(" ", ""), "游标没有接到 reviewThreads 上"

    # 再验**消费端**：拿到 hasNextPage 之后要真的再取一页
    import ast
    fn = next(n for n in ast.walk(ast.parse(
        (CI_DIR / "codex_review_gate.py").read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name == "_gh_graphql")
    body = ast.unparse(fn)
    assert "hasNextPage" in body and "endCursor" in body, "拿到分页信息却没用"


def test_restore_strips_fields_the_api_will_not_accept():
    """**GET 的响应不是 PUT 的请求体。**

    存档里带着 `_links` / `id` / `created_at` 这些服务端生成的只读字段，
    原样 PUT 回去会被拒收 —— 而那发生在**最需要它成功的时刻**：
    某人刚把 ruleset 改坏，正要回滚。

    `docs/admin/github-ruleset-changes.md` §2.1 早就写下了这条判断，
    而脚本自己却直接 PUT 存档文件 —— 文档与实现自相矛盾。
    """
    sys.path.insert(0, str(ADMIN_DIR))
    import _strip_readonly as SR

    archived = {
        "id": 123, "node_id": "x", "name": "main", "target": "branch",
        "enforcement": "active", "conditions": {}, "rules": [],
        "bypass_actors": [], "_links": {"self": {}}, "source": "o/r",
        "source_type": "Repository", "created_at": "t", "updated_at": "t",
        "current_user_can_bypass": "never",
    }
    out = SR.strip(archived)
    assert set(out) == {"name", "target", "enforcement", "conditions",
                        "rules", "bypass_actors"}, out

    # 脚本必须**真的调它**，不是 import 了放着
    sh = (ADMIN_DIR / "apply_rulesets.sh").read_text(encoding="utf-8")
    assert "_strip_readonly.py" in sh, "restore 没有走剥字段那一步"


def test_no_shell_script_leaves_a_bare_var_before_a_non_ascii_char():
    """`"$f（…"` 里的全角括号会被 bash 当成变量名的一部分。

    实测（bash 5，`set -u`）::

        $ f=x; echo "$f（尾）"
        bash: f?: unbound variable
        $ f=x; echo "${f}（尾）"
        x（尾）

    这个仓库的所有文案都是中文，所以这个形状特别容易长出来。
    **本轮真撞到两处**，其中一处（`--diff` 的「本地没有存档」分支）
    从写下那天起**一次都没执行过**，所以一直没暴露 ——
    「从没执行过的代码不会保持正确」在同一轮里的又一个实例。

    修法是给变量名加大括号定界：`${f}`。
    """
    offenders = []
    for sh in sorted(ROOT.rglob("*.sh")):
        if any(part in ("node_modules", "target", ".git") for part in sh.parts):
            continue
        for i, line in enumerate(sh.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)(?=[^\x00-\x7f])", line):
                offenders.append(
                    f"{sh.relative_to(ROOT)}:{i}  ${m.group(1)} → ${{{m.group(1)}}}")
    assert not offenders, (
        "这些变量引用后面紧跟非 ASCII 字符，bash 会把它并进变量名：\n  "
        + "\n  ".join(offenders))


def test_the_documented_manual_restore_also_strips_read_only_fields():
    """**文档里给错的命令比没有命令更坏。**

    脚本那侧修好了（走 `_strip_readonly.py`），而文档 §3 里那条「任何时候
    都可以手动」的回滚命令还写着直接 `--input` 存档文件 —— 人会照着执行，
    而失败发生在最需要它成功的时刻：正要回滚。

    「修一处不算修完」的又一次，这次漏的是文档里的命令。
    """
    doc = (ROOT / "docs" / "admin" / "github-ruleset-changes.md").read_text(encoding="utf-8")
    for m in re.finditer(r"gh api -X PUT[^\n]*(?:\\\n[^\n]*)*", doc):
        cmd = m.group(0)
        assert "rulesets/" not in cmd or "--input docs/admin/rulesets/" not in cmd, (
            f"文档里这条命令直接 PUT 存档文件（含只读字段，会被拒收）：\n  {cmd}")
    assert "_strip_readonly.py" in doc, "文档没告诉人先剥只读字段"


def test_the_stats_tool_says_when_it_only_saw_the_first_page():
    """一个**悄悄少数**的统计比没有统计更坏。

    `codex_review_stats.py` 产出的正是审计报告里那几个「113 次 review、
    188 条 thread」的数字。超过 100 条的 PR 会被少数，而且不会报错。

    这里选的是**如实报出截断**而不是翻页：本仓库最多的一条 PR 有 18 轮 /
    29 条 thread，离 100 还远，而翻页要为每条 PR 各再发一串请求 ——
    一个一次性诊断工具不值得那个复杂度。**精度写在明处**才是关键。
    """
    src = (ADMIN_DIR / "codex_review_stats.py").read_text(encoding="utf-8")
    assert "totalCount" in src, "查询没有要总数，无从知道有没有被截断"
    assert "truncated" in src, "没有把截断这件事表达出来"
    assert "::warning::" in src, "截断了却不吭声"


def test_diff_notices_a_ruleset_that_vanished_from_the_remote():
    """**一个检测保护是否完好的工具，不许在保护完全消失时报平安。**

    `--diff` 原来遍历的是**远程**的 ruleset id。某条被删掉之后它压根不出现在
    循环里 —— `--diff` 于是 exit 0，而那条规则保护的东西（PR 要求、17 项
    必需检查、禁止直推 main）已经全部没了。

    这正是这套 CI 反复在消灭的那种失效，而它长在了检测工具自己身上。
    """
    sh = (ADMIN_DIR / "apply_rulesets.sh").read_text(encoding="utf-8")
    code = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    diff = code.split("\ndiff)")[1].split("\nrestore)")[0]
    assert 'for f in "$STORE"/ruleset-*.json' in diff, (
        "--diff 没有遍历**存档**那一侧 —— 远程被删掉的 ruleset 它看不见")
    assert "--recreate" in code, "没有给消失的 ruleset 一条重建路径"

    # 被删掉的只能 POST 重建：那个数字 id 已经不存在，PUT 会 404
    recreate = code.split("\nrecreate)")[1].split("\nadd-check")[0]
    assert "-X POST" in recreate, "重建走的不是 POST —— PUT 一个不存在的 id 会 404"
