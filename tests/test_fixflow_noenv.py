"""AI 修复：「项目还没有可用环境」这条路（D3 补）——提示词规则、change_python 的描述与可省的 version、整条链。

判据的主语：**给模型的文字**（提示词 / 工具描述 / 工具结果）、**工具实际收到的执行器请求**（version 为 None、带脚本路径）、
**整条链上工具的调用顺序与卡片**（假模型 + 假执行器；不起真实环境、不装包、不联网）。
"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from tavotto.engine import fixsession, userenvs
from tavotto.engine.agent_runner.wires import ToolCall, Turn
from tavotto.engine.fixtools import REGISTRY, AuditLog, FixContext, run_tool


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    cfg, data = tmp_path / "cfg", tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    for k in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(k, str(home))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(data))
    (cfg / "config.json").write_bytes(b'{"ai": {"providers": []}}')
    (proj / "fig.py").write_bytes(b"import colorcet\n")
    fixsession.SESSIONS.reset_for_tests()
    yield type("Box", (), {"home": home, "proj": proj, "tmp": tmp_path, "data": data})
    fixsession.SESSIONS.reset_for_tests()


def fake_python(box) -> str:
    p = box.tmp / "venvs" / "new" / ("Scripts" if os.name == "nt" else "bin") / "python"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return str(p)


def test_prompt_tells_the_ai_not_to_give_up_when_there_is_no_environment():
    text = fixsession.SYSTEM_PROMPT
    assert "NO USABLE ENVIRONMENT" in text and "do NOT give up" in text
    for step in ("propose_fix", "change_python", "install_packages", "try_run", "finish"):
        assert step in text.split("NO USABLE ENVIRONMENT", 1)[1].split("4. If you need")[0]
    # CLI 与内置循环用同一份提示词
    ctx = FixContext.for_fix_session("s", os.getcwd())
    assert "NO USABLE ENVIRONMENT" in fixsession.cli_prompt(ctx, "fig.py")


def test_change_python_description_and_schema_cover_the_no_environment_case():
    tool = REGISTRY["change_python"]
    assert "no usable environment" in tool.description
    assert tool.schema["required"] == [] and "version" in tool.schema["properties"]


def test_an_empty_environment_list_points_at_change_python(box):
    ctx = FixContext.for_fix_session(
        "s", box.proj, home=box.home, environment_source=lambda: [], case_insensitive=False
    )
    r = run_tool(REGISTRY["list_environments"], ctx, {})
    assert "change_python" in r.content


def test_change_python_without_a_version_asks_the_executor_to_choose(box):
    seen: list[dict] = []

    def changer(req):
        seen.append(req)
        return {"ok": True, "python": fake_python(box), "to": "3.12"}

    script = box.proj / "fig.py"
    ctx = FixContext.for_fix_session(
        "s",
        box.proj,
        home=box.home,
        python_changer=changer,
        script_path=script,
        case_insensitive=False,
        freezer=lambda _py: "",
    )
    r = run_tool(REGISTRY["change_python"], ctx, {})
    assert not r.is_error and "new environment id" in r.content
    assert seen == [{"version": None, "project": str(box.proj), "script": "fig.py"}]
    bad = run_tool(REGISTRY["change_python"], ctx, {"version": "banana"})
    assert bad.is_error and bad.code == "bad_arguments"


class Scripted:
    def __init__(self, steps):
        self.steps, self.calls = list(steps), 0

    def complete(self, **_kw) -> Turn:
        self.calls += 1
        if not self.steps:
            return Turn(text="done", stop="end")
        step = self.steps.pop(0)
        step = step() if callable(step) else step
        return Turn(tool_calls=[ToolCall(f"c{self.calls}", n, a) for n, a in step], stop="tool_use")


def test_full_chain_without_any_environment_builtin_loop(box):
    python = fake_python(box)
    new_id = userenvs.env_id(python)
    installs: list[tuple] = []
    runs: list[tuple] = []
    model = Scripted(
        [
            [("list_environments", {})],
            [
                (
                    "propose_fix",
                    {
                        "summary": "This project has no Python environment that works yet. I will create one and install colorcet."
                    },
                )
            ],
            [("change_python", {})],
            [("install_packages", {"env": new_id, "packages": ["colorcet"]})],
            [("try_run", {"env": new_id})],
            [("finish", {"outcome": "fixed", "summary": "好了"})],
        ]
    )
    s = fixsession.SESSIONS.start(
        scope="pj",
        prep_session_id="p1",
        script="fig.py",
        project_root=box.proj,
        model=model,
        audit=AuditLog(),
        script_path=box.proj / "fig.py",
        ctx_kwargs={
            "home": box.home,
            "case_insensitive": False,
            "environment_source": lambda: [],
            "python_changer": lambda req: {"ok": True, "python": python, "to": "3.12"},
            "installer": lambda py, reqs: (
                installs.append((py, list(reqs))) or {"ok": True, "code": "", "done": list(reqs)}
            ),
            "freezer": lambda py: "pip==24.0\n",
            "runner": lambda py, work, name, mode, timeout: (
                runs.append((py, name))
                or {"status": "ok", "returncode": 0, "figures": 1, "stderr": ""}
            ),
        },
    )
    cards: list[dict] = []

    def answer_all():  # 用户对每张卡都点「同意」
        end = time.monotonic() + 30
        while time.monotonic() < end and s.state != fixsession.STATE_FINISHED:
            c = fixsession.SESSIONS.get(s.id, "pj")["card"]
            if c and (not cards or cards[-1]["id"] != c["id"]):
                cards.append(c)
                fixsession.SESSIONS.answer(s.id, "pj", c["id"], "yes")
            time.sleep(0.02)

    t = threading.Thread(target=answer_all)
    t.start()
    t.join(40)
    assert (s.outcome, s.summary) == (fixsession.OUTCOME_FIXED, "好了")
    assert cards[0]["kind"] == "approve" and "colorcet" in cards[0]["summary"]
    assert installs == [(python, ["colorcet"])]  # 装进 change_python 返回的新环境
    assert runs == [(python, "fig.py")]
    from tavotto.engine.fixtools import AuditLog as _A  # noqa: F401

    rows = [
        __import__("json").loads(x)
        for x in (box.data / "ai" / "fix_audit.jsonl").read_text("utf-8").splitlines()
    ]
    assert [r["tool"] for r in rows if r["session"] == s.id] == [
        "list_environments",
        "propose_fix",
        "change_python",
        "install_packages",
        "try_run",
        "finish",
    ]


# =========================================================================== propose_fix 点过头的包，install_packages 不再问第二遍
def agreed_ctx(box, answers):
    """`answers`：按顺序给每张卡的回答；返回 (ctx, 卡片列表, 假环境 id)。"""
    python = fake_python(box)
    cards: list[dict] = []
    it = iter(answers)

    def ask(card):
        cards.append(card)
        return {"answer": next(it, "no")}

    ctx = FixContext.for_fix_session(
        "s",
        box.proj,
        home=box.home,
        ask=ask,
        case_insensitive=False,
        environments={"e": python},
        freezer=lambda _p: "pip==24.0\n",
        installer=lambda py, reqs: {"ok": True, "code": "", "done": list(reqs)},
    )
    return ctx, cards


UNLISTED = "zzz-not-on-any-list"


def test_packages_the_user_agreed_to_are_not_asked_again_and_audit_says_user(box):
    ctx, cards = agreed_ctx(box, ["yes"])
    audit = AuditLog()
    r = run_tool(
        REGISTRY["propose_fix"], ctx, {"summary": "装包", "packages": [UNLISTED + "==1.0"]}, audit
    )
    assert not r.is_error and cards[0]["packages"] == [UNLISTED + "==1.0"]
    step = ctx.step
    r = run_tool(
        REGISTRY["install_packages"],
        ctx,
        {"env": "e", "packages": [UNLISTED.upper().replace("-", "_")]},
        audit,
    )
    assert (
        not r.is_error and len(cards) == 1
    )  # 没有第二张卡（规范名同名即可：大小写 / 连字符 / 下划线不分）
    row = [
        json.loads(x) for x in (box.data / "ai" / "fix_audit.jsonl").read_text("utf-8").splitlines()
    ][-1]
    assert (
        row["tool"] == "install_packages"
        and row["approved_by"] == "user"
        and row["approved_step"] == step
    )


def test_a_package_that_was_not_listed_still_asks(box):
    ctx, cards = agreed_ctx(box, ["yes", "yes"])
    run_tool(REGISTRY["propose_fix"], ctx, {"summary": "装包", "packages": [UNLISTED]})
    r = run_tool(
        REGISTRY["install_packages"],
        ctx,
        {"env": "e", "packages": [UNLISTED, "another-unlisted-pkg"]},
    )
    assert len(cards) == 2 and cards[1]["tool"] == "install_packages"  # 多出来的包照旧问
    assert not r.is_error


def test_declining_the_proposal_agrees_to_nothing(box):
    ctx, cards = agreed_ctx(box, ["no", "no"])
    run_tool(REGISTRY["propose_fix"], ctx, {"summary": "装包", "packages": [UNLISTED]})
    assert ctx.approved_installs == {}
    r = run_tool(REGISTRY["install_packages"], ctx, {"env": "e", "packages": [UNLISTED]})
    assert r.is_error and r.code == "needs_approval" and len(cards) == 2


def test_propose_fix_packages_must_be_plain_requirements(box):
    ctx, cards = agreed_ctx(box, ["yes"])
    for bad in (["-r evil.txt"], ["pkg @ https://x"], "colorcet", [1]):
        r = run_tool(REGISTRY["propose_fix"], ctx, {"summary": "x", "packages": bad})
        assert r.is_error and r.code == "bad_requirement"
    assert cards == []


def test_prompt_asks_for_packages_on_the_proposal():
    assert "`packages`" in fixsession.SYSTEM_PROMPT
