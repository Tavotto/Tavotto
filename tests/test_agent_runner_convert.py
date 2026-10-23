"""内置执行器的第二套任务工具集 `convert_to_editable`（ADR 0147 / 0143 §二）。

判据的主语：

* 写入面：**磁盘上的字节**。每条被拒的写都断言「原脚本与候选里其余文件逐字节不变」，而不是只看错误码；
* 预算：`check` 回调**真实被调用的次数**（不是工具返回了什么）；
* 启动即拒的输入（`--output` 非法）：模型对象**被调用的次数 == 0**，`done` 为 `internal/bad_output`；
* 提示词：系统提示里**逐字包含** `ai_prompt_pack.convert_requirements()` 的每一条（同一份文本，不是抄一份）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import ai_prompt_pack
from tavotto.engine.agent_runner import loop, tools
from tavotto.engine.agent_runner.redact import Redactor

SRC = Path(__file__).resolve().parent.parent / "src"

KEY = "sk-canary-KEY-91c2d7"
ORIG = 'from PIL import Image\nimg = Image.new("RGB", (10, 10))\nimg.save("fig.png")\n'
NEW = "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig('fig.png')\n"
TASK = tools.TOOLSETS["convert_to_editable"]


@pytest.fixture
def cand(tmp_path) -> Path:
    d = tmp_path / "cand"
    d.mkdir()
    (d / "fig.py").write_text(ORIG, encoding="utf-8")
    (d / "data.csv").write_text("1,2\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("OUTSIDE\n", encoding="utf-8")
    return d


def _tree(d: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}


def envelope(cand: Path, **kw) -> tools.RunEnvelope:
    return tools.RunEnvelope(
        task_type="convert_to_editable",
        cwd=cand,
        target=kw.pop("target", "fig.py"),
        instruction=kw.pop("instruction", "make it editable"),
        limits=kw.pop("limits", TASK.default_limits),
        output=kw.pop("output", "fig_matplotlib.py"),
        **kw,
    )


def run_demo(cand: Path, script: dict, **kw):
    events: list[dict] = []
    model = kw.pop("model", None) or loop.DemoModel(script)
    res = loop.run(model, TASK, envelope(cand, **kw), events.append, None)
    return res, events


def call(name: str, **args) -> dict:
    return {"name": name, "args": args}


def step(*calls, text="", expect=None) -> dict:
    return {"tool_calls": list(calls), "text": text, "expect": expect or []}


def done(events: list[dict]) -> dict:
    assert events[-1]["type"] == "done"
    return events[-1]


def tool_events(events: list[dict]) -> list[tuple[str, bool]]:
    return [(e["name"], e["ok"]) for e in events if e["type"] == "tool"]


class CountingCheck:
    def __init__(self, ok=True, report="figure captured"):
        self.calls = 0
        self.ok, self.report = ok, report

    def __call__(self):
        self.calls += 1
        return self.ok, self.report


# ================================================================ 正常流
class TestNormalFlow:
    def test_read_create_check_final_text(self, cand):
        chk = CountingCheck()
        script = {
            "turns": [
                step(call("read_file", path="fig.py")),
                step(call("create_file", content=NEW), expect=[{"name": "read_file"}]),
                step(
                    call("check"), expect=[{"name": "create_file", "summary_contains": "created"}]
                ),
                step(text="Done: rebuilt with matplotlib.", expect=[{"name": "check"}]),
            ]
        }
        res, events = run_demo(cand, script, check=chk)
        assert (res.ok, res.reason, res.turns, res.changed) == (True, "completed", 4, True)
        assert tool_events(events) == [
            ("read_file", True),
            ("create_file", True),
            ("check", True),
        ]
        assert (cand / "fig_matplotlib.py").read_text(encoding="utf-8") == NEW
        assert (cand / "fig.py").read_text(encoding="utf-8") == ORIG
        assert chk.calls == 1
        assert done(events)["ok"] is True

    def test_edit_and_rewrite_only_touch_the_new_file(self, cand):
        script = {
            "turns": [
                step(call("create_file", content=NEW)),
                step(call("edit_file", old="[1, 2]", new="[3, 4]")),
                step(call("create_file", content=NEW + "# v2\n")),
                step(text="ok"),
            ]
        }
        res, _ = run_demo(cand, script)
        assert res.ok
        assert (cand / "fig_matplotlib.py").read_text(encoding="utf-8") == NEW + "# v2\n"
        assert (cand / "fig.py").read_text(encoding="utf-8") == ORIG

    def test_edit_after_create_changes_new_file(self, cand):
        script = {
            "turns": [
                step(call("create_file", content=NEW)),
                step(call("edit_file", old="[1, 2]", new="[3, 4]")),
                step(text="ok"),
            ]
        }
        res, _ = run_demo(cand, script)
        assert res.ok
        assert "[3, 4]" in (cand / "fig_matplotlib.py").read_text(encoding="utf-8")

    def test_toolset_has_no_shell_network_or_write_file(self, cand):
        names = [t.name for t in TASK.tools(envelope(cand))]
        assert names == ["read_file", "list_dir", "create_file", "edit_file", "check"]

    def test_first_message_names_both_files_and_prompt_is_stable(self, cand):
        msg = TASK.first_message(envelope(cand))
        assert "fig.py" in msg and "fig_matplotlib.py" in msg and "make it editable" in msg
        assert TASK.system_prompt(envelope(cand)) == TASK.system_prompt(
            envelope(cand, instruction="other", output="fig_matplotlib.py")
        )


# ================================================================ 原脚本永远不可写
class TestOriginalIsReadOnly:
    @pytest.mark.parametrize(
        "bad_call",
        [
            call("create_file", path="fig.py", content="x = 1\n"),
            call("edit_file", path="fig.py", old="Image", new="Foo"),
            call("write_file", path="fig.py", content="x = 1\n"),  # 没有这个工具
            call("edit_file", old="Image", new="Foo", path="./sub/../fig.py"),
        ],
    )
    def test_writes_to_the_original_are_tool_errors_and_bytes_unchanged(self, cand, bad_call):
        before = _tree(cand)
        script = {"turns": [step(call("create_file", content=NEW)), step(bad_call), step(text="x")]}
        res, events = run_demo(cand, script)
        assert res.ok
        assert tool_events(events)[1][1] is False
        after = _tree(cand)
        assert after.pop("fig_matplotlib.py") == NEW.encode()
        assert after == before  # 原脚本与 data.csv 逐字节不变

    def test_original_cannot_be_overwritten_even_before_the_new_file_exists(self, cand):
        before = _tree(cand)
        script = {
            "turns": [
                step(call("create_file", path="fig.py", content="x = 1\n")),
                step(text="x", expect=[{"name": "create_file", "is_error": True}]),
            ]
        }
        res, _ = run_demo(cand, script)
        assert res.ok
        assert _tree(cand) == before

    @pytest.mark.parametrize("path", ["data.csv", "../outside.txt", "/etc/hosts", "sub/other.py"])
    def test_other_paths_are_refused(self, cand, path):
        before = _tree(cand)
        outside = (cand.parent / "outside.txt").read_bytes()
        script = {"turns": [step(call("create_file", path=path, content="x\n")), step(text="x")]}
        res, events = run_demo(cand, script)
        assert res.ok and tool_events(events) == [("create_file", False)]
        assert _tree(cand) == before
        assert (cand.parent / "outside.txt").read_bytes() == outside

    def test_symlink_to_outside_is_refused(self, cand):
        link = cand / "link.py"
        try:
            link.symlink_to(cand.parent / "outside.txt")
        except OSError:
            pytest.skip("symlink not available")
        script = {
            "turns": [step(call("create_file", path="link.py", content="x\n")), step(text="x")]
        }
        res, events = run_demo(cand, script)
        assert tool_events(events) == [("create_file", False)]
        assert (cand.parent / "outside.txt").read_text() == "OUTSIDE\n"

    def test_reads_stay_inside_the_candidate(self, cand):
        script = {
            "turns": [
                step(call("read_file", path="../outside.txt")),
                step(call("read_file", path="fig.py")),
                step(text="x"),
            ]
        }
        _, events = run_demo(cand, script)
        assert tool_events(events) == [("read_file", False), ("read_file", True)]


# ================================================================ --output 非法
class TestBadOutput:
    @pytest.mark.parametrize(
        "output",
        [
            "",
            "fig.py",
            "fig_matplotlib.txt",
            "other_matplotlib.py",  # 不是 <原名>_matplotlib.py
            "sub/fig_matplotlib.py",
            "./fig_matplotlib.py",
            "../fig_matplotlib.py",
            "/tmp/fig_matplotlib.py",
            "fig_matplotlib.py\\x",
            "taken_matplotlib.py",  # 见下：用例里先建出来
        ],
    )
    def test_rejected_at_start_with_zero_requests(self, cand, output):
        (cand / "taken_matplotlib.py").write_text("old\n", encoding="utf-8")
        target = "taken.py" if output == "taken_matplotlib.py" else "fig.py"
        (cand / "taken.py").write_text("x\n", encoding="utf-8")
        before = _tree(cand)

        class NeverCalled:
            calls = 0

            def complete(self, **_kw):
                NeverCalled.calls += 1
                raise AssertionError("model must not be called")

        res, events = run_demo(cand, {}, model=NeverCalled(), output=output, target=target)
        assert (res.ok, res.reason, res.detail) == (False, "internal", "bad_output")
        assert done(events)["detail"] == "bad_output"
        assert NeverCalled.calls == 0
        assert _tree(cand) == before

    def test_dangling_symlink_counts_as_existing(self, cand):
        link = cand / "fig_matplotlib.py"
        try:
            link.symlink_to(cand.parent / "nowhere.py")
        except OSError:
            pytest.skip("symlink not available")
        res, _ = run_demo(cand, {"turns": []})
        assert (res.reason, res.detail) == ("internal", "bad_output")
        assert not (cand.parent / "nowhere.py").exists()

    def test_target_outside_is_bad_target_not_bad_output(self, cand):
        res, _ = run_demo(cand, {"turns": []}, target="../outside.txt")
        assert (res.reason, res.detail) == ("internal", "bad_target")

    def test_file_appearing_after_start_is_not_overwritten(self, cand):
        """起跑校验之后、create_file 之前有人建了同名文件：O_EXCL 拒绝，不覆盖。"""
        sess = tools.ConvertSession(envelope(cand))
        (cand / "fig_matplotlib.py").write_text("someone else\n", encoding="utf-8")
        r = sess.create_file({"content": NEW})
        assert r.is_error and r.code == "output_exists"
        assert (cand / "fig_matplotlib.py").read_text(encoding="utf-8") == "someone else\n"


# ================================================================ check 预算
class TestCheckBudget:
    def test_fourth_check_is_a_tool_error_and_callback_runs_three_times(self, cand):
        chk = CountingCheck(ok=False, report="no figure")
        script = {
            "turns": [
                step(call("create_file", content=NEW)),
                step(call("check")),
                step(call("edit_file", old="[1, 2]", new="[1, 3]")),
                step(call("check")),
                step(call("edit_file", old="[1, 3]", new="[1, 4]")),
                step(call("check")),
                step(call("check")),
                step(text="gave up"),
            ]
        }
        res, events = run_demo(cand, script, check=chk)
        assert res.ok
        assert chk.calls == 3
        t = tool_events(events)
        assert t[-1] == ("check", False)
        assert [ok for n, ok in t if n == "check"] == [True, True, True, False]

    def test_budget_error_code(self, cand):
        chk = CountingCheck()
        sess = tools.ConvertSession(envelope(cand, check=chk))
        sess.create_file({"content": NEW})
        for _ in range(3):
            assert not sess.check({}).is_error
        r = sess.check({})
        assert (r.is_error, r.code) == (True, "check_budget")
        assert chk.calls == 3

    def test_check_before_creating_does_not_run_or_count(self, cand):
        chk = CountingCheck()
        sess = tools.ConvertSession(envelope(cand, check=chk))
        r = sess.check({})
        assert (r.is_error, r.code) == (True, "not_created")
        assert chk.calls == 0 and sess.checks_run == 0

    def test_no_callback_is_not_available(self, cand):
        sess = tools.ConvertSession(envelope(cand))
        sess.create_file({"content": NEW})
        r = sess.check({})
        assert (r.is_error, r.code) == (True, "not_available")


# ================================================================ edit 前提 / 密钥
class TestEditAndSecrets:
    def test_edit_before_create_is_an_error_and_writes_nothing(self, cand):
        before = _tree(cand)
        script = {"turns": [step(call("edit_file", old="a", new="b")), step(text="x")]}
        res, events = run_demo(cand, script)
        assert tool_events(events) == [("edit_file", False)]
        assert _tree(cand) == before
        sess = tools.ConvertSession(envelope(cand))
        assert sess.edit_file({"old": "a", "new": "b"}).code == "not_created"

    def test_edit_old_must_match_exactly_once(self, cand):
        sess = tools.ConvertSession(envelope(cand))
        sess.create_file({"content": "a\na\nb\n"})
        assert sess.edit_file({"old": "a", "new": "c"}).code == "old_not_unique"
        assert sess.edit_file({"old": "zzz", "new": "c"}).code == "old_not_found"
        assert (cand / "fig_matplotlib.py").read_text() == "a\na\nb\n"
        assert not sess.edit_file({"old": "b", "new": "c"}).is_error
        assert (cand / "fig_matplotlib.py").read_text() == "a\na\nc\n"

    def test_secret_in_new_file_is_refused(self, cand):
        red = Redactor(KEY)
        sess = tools.ConvertSession(envelope(cand, redactor=red))
        r = sess.create_file({"content": f"KEY = '{KEY}'\n"})
        assert (r.is_error, r.code) == (True, "secret_in_content")
        assert not (cand / "fig_matplotlib.py").exists()
        assert sess.create_file({"content": NEW}).is_error is False
        r = sess.edit_file({"old": "[1, 2]", "new": f"['{KEY}']"})
        assert r.code == "secret_in_content"
        assert KEY not in (cand / "fig_matplotlib.py").read_text()
        r = sess.create_file({"content": f"x = '{KEY}'\n"})
        assert r.code == "secret_in_content"
        assert (cand / "fig_matplotlib.py").read_text() == NEW

    def test_secret_split_create_then_edit_is_refused(self, cand):
        head, tail = KEY[:-3], KEY[-3:]
        sess = tools.ConvertSession(envelope(cand, redactor=Redactor(KEY)))
        content = f"K = '{head}PAD'\n"
        assert not sess.create_file({"content": content}).is_error
        r = sess.edit_file({"old": "PAD", "new": tail})
        assert (r.is_error, r.code) == (True, "secret_in_content")
        assert (cand / "fig_matplotlib.py").read_text() == content

    def test_secret_split_across_two_edits_or_rewrite_is_refused(self, cand):
        head, tail = KEY[:-3], KEY[-3:]
        sess = tools.ConvertSession(envelope(cand, redactor=Redactor(KEY)))
        sess.create_file({"content": NEW})
        assert not sess.edit_file({"old": "[1, 2]", "new": f"['{head}XY']"}).is_error
        cur = (cand / "fig_matplotlib.py").read_text()
        assert sess.edit_file({"old": "XY", "new": tail}).code == "secret_in_content"
        assert (cand / "fig_matplotlib.py").read_text() == cur
        # create_file 整份重写同样检查结果文本
        assert sess.create_file({"content": f"x='{KEY}'\n"}).code == "secret_in_content"
        assert (cand / "fig_matplotlib.py").read_text() == cur

    def test_empty_content_refused(self, cand):
        sess = tools.ConvertSession(envelope(cand))
        assert sess.create_file({"content": "  \n"}).code == "empty_content"
        assert not (cand / "fig_matplotlib.py").exists()


# ================================================================ 提示词与预算默认值
class TestPromptAndLimits:
    def test_system_prompt_embeds_the_shared_requirements_verbatim(self, cand):
        prompt = TASK.system_prompt(envelope(cand))
        reqs = ai_prompt_pack.convert_requirements()
        assert len(reqs) == 3
        for r in reqs:
            assert r in prompt
        # CLI 路径的提示词里也是同一批文字
        cli = ai_prompt_pack.convert_prompt(
            script="fig.py",
            target="fig_matplotlib.py",
            library="PIL",
            reference=None,
            project_root="/p",
        )
        for r in reqs:
            assert r in cli
        for word in ("check", "create_file", "read-only", "language of the user's instruction"):
            assert word in prompt

    def test_defaults(self):
        lim = TASK.default_limits
        assert lim.max_turns == 20 and lim.max_output_tokens == 16384
        assert tools.CONVERT_MAX_CHECKS == 3
        assert tools.TOOLSETS["figure_edit"].default_limits == tools.Limits()


# ================================================================ 入口
def run_cli(cand, *extra, stdin="make it editable"):
    env = {k: v for k, v in os.environ.items() if k != "TAVOTTO_AGENT_API_KEY"}
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONUTF8"] = "1"
    argv = [
        sys.executable,
        "-m",
        "tavotto.engine.agent_runner",
        "--cwd",
        str(cand),
        "--target",
        "fig.py",
        "--wire",
        "demo",
        *extra,
    ]
    return subprocess.run(
        argv, env=env, input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=60
    )


class TestCli:
    def test_task_flag_end_to_end_without_check_injector(self, cand, tmp_path):
        script = tmp_path / "demo.json"
        script.write_text(
            json.dumps(
                {
                    "turns": [
                        step(call("create_file", content=NEW)),
                        step(call("check"), expect=[{"name": "create_file"}]),
                        step(text="done", expect=[{"name": "check", "is_error": True}]),
                    ]
                }
            ),
            encoding="utf-8",
        )
        p = run_cli(
            cand,
            "--task",
            "convert_to_editable",
            "--output",
            "fig_matplotlib.py",
            "--demo-script",
            str(script),
        )
        lines = [json.loads(ln) for ln in p.stdout.splitlines()]
        assert p.returncode == 0, p.stderr
        assert lines[-1]["type"] == "done" and lines[-1]["ok"] is True
        assert [(e["name"], e["ok"]) for e in lines if e["type"] == "tool"] == [
            ("create_file", True),
            ("check", False),  # 本阶段入口没有注入方：not_available
        ]
        assert (cand / "fig_matplotlib.py").read_text(encoding="utf-8") == NEW
        assert (cand / "fig.py").read_text(encoding="utf-8") == ORIG

    def test_bad_output_via_cli(self, cand, tmp_path):
        script = tmp_path / "demo.json"
        script.write_text(json.dumps({"turns": [step(text="never")]}), encoding="utf-8")
        before = _tree(cand)
        p = run_cli(
            cand,
            "--task",
            "convert_to_editable",
            "--output",
            "evil.py",
            "--demo-script",
            str(script),
        )
        lines = [json.loads(ln) for ln in p.stdout.splitlines()]
        assert p.returncode == 1
        assert (lines[-1]["reason"], lines[-1]["detail"], lines[-1]["turns"]) == (
            "internal",
            "bad_output",
            0,
        )
        assert _tree(cand) == before

    def test_default_task_is_still_figure_edit(self):
        from tavotto.engine.agent_runner.__main__ import _parser

        a = _parser().parse_args(["--cwd", "x", "--target", "t", "--wire", "demo"])
        assert a.task == "figure_edit" and a.output == ""


def test_tool_definitions_and_system_prompt_are_stable_across_output_names(tmp_path):
    """提示缓存的前缀（系统提示 + 工具定义）不许随脚本名变；文件名只走首条用户消息（Codex #1329 P2）。"""

    def prefix(target: str, output: str):
        cand = tmp_path / target.replace(".", "_")
        cand.mkdir()
        (cand / target).write_text(ORIG, encoding="utf-8")
        env = envelope(cand, target=target, output=output)
        specs = [(t.name, t.description, t.schema) for t in TASK.tools(env)]
        return TASK.system_prompt(env), specs, TASK.first_message(env)

    sys_a, tools_a, first_a = prefix("fig.py", "fig_matplotlib.py")
    sys_b, tools_b, first_b = prefix("panel_b.py", "panel_b_matplotlib.py")
    assert sys_a == sys_b
    assert tools_a == tools_b
    assert "fig_matplotlib.py" in first_a and "panel_b_matplotlib.py" in first_b


def test_system_prompt_does_not_restate_the_shared_requirements():
    """产品要求只有 ai_prompt_pack.convert_requirements() 一份；执行器那段只许写执行器特有的约束（Codex #1329 P2）。"""
    from tavotto.engine.agent_runner.tools import CONVERT_SYSTEM_PROMPT

    extra = CONVERT_SYSTEM_PROMPT.split("Additional rules for this session", 1)[1]
    for word in (
        "data reading",
        "calculations",
        "font size",
        "figure's size",
        "Match the original",
    ):
        assert word not in extra, word


class TestOutputSizeLimit:
    """Codex #1328 第七轮：写出的新文件同样不许超过读取上限（首次新建走 O_EXCL 路径，不经 _commit_write）。"""

    @staticmethod
    def _ts(cand: Path) -> dict[str, tools.Tool]:
        return {t.name: t for t in TASK.tools(envelope(cand))}

    def test_first_create_over_limit_is_refused_and_nothing_is_created(self, cand):
        ts = self._ts(cand)
        before = _tree(cand)
        big = "x = 1\n" * (tools.READ_LIMIT_BYTES // 6 + 1)
        r = ts["create_file"].handler({"content": big})
        assert (r.is_error, r.code) == (True, "too_large")
        assert _tree(cand) == before

    def test_rewrite_and_edit_over_limit_are_refused_and_file_keeps_old_bytes(self, cand):
        ts = self._ts(cand)
        assert not ts["create_file"].handler({"content": NEW}).is_error
        out = cand / "fig_matplotlib.py"
        assert out.read_bytes() == NEW.encode()
        big = "x = 1\n" * (tools.READ_LIMIT_BYTES // 6 + 1)
        r = ts["create_file"].handler({"content": big})
        assert (r.is_error, r.code) == (True, "too_large")
        assert out.read_bytes() == NEW.encode()
        # 用例各行互异：difflib 对海量相同行是二次方的
        near = "".join(f"x{i:05d} = 1\n" for i in range(tools.READ_LIMIT_BYTES // 11 - 5))
        assert not ts["create_file"].handler({"content": near}).is_error
        size = out.stat().st_size
        r = ts["edit_file"].handler({"old": "x00000 = 1", "new": "x00000 = 1  # " + "y" * 200})
        assert (r.is_error, r.code) == (True, "too_large")
        assert out.stat().st_size == size


@pytest.mark.parametrize("kind", ["missing", "directory", "oversized", "not_utf8"])
def test_unusable_original_fails_before_any_model_request(tmp_path, kind):
    """原脚本读不了时起跑即 bad_target、0 次模型请求（跟 #1328 第八轮同一判据）。"""
    cand = tmp_path / "c"
    cand.mkdir()
    if kind == "directory":
        (cand / "fig.py").mkdir()
    elif kind == "oversized":
        (cand / "fig.py").write_bytes(b"#" * (tools.READ_LIMIT_BYTES + 1))
    elif kind == "not_utf8":
        (cand / "fig.py").write_bytes(b"\xff\xfe\x00")
    calls = []
    model = loop.DemoModel({"turns": [{"text": "done"}]})
    orig = model.complete
    model.complete = lambda **kw: (calls.append(1), orig(**kw))[1]
    res, _ = run_demo(cand, {"turns": []}, model=model)
    assert (res.ok, res.reason, res.detail) == (False, "internal", "bad_target")
    assert calls == []


def test_check_before_create_reports_not_created_even_without_a_callback(tmp_path):
    """没注入 check 回调时，先 check 也要报 not_created（可操作的流程错误优先，Codex #1329 P2）。"""
    cand = tmp_path / "c"
    cand.mkdir()
    (cand / "fig.py").write_text(ORIG, encoding="utf-8")
    env = envelope(cand)
    assert env.check is None
    ts = {x.name: x for x in TASK.tools(env)}
    r = ts["check"].handler({})
    assert r.is_error and r.code == "not_created"
