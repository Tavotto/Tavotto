"""执行转录、答案上下文匹配与输入去向投影的单元合同（T08，ADR 0099 §九）——不起 worker。"""

from __future__ import annotations

import json

from tavotto.engine import inputbroker, inputtranscript, scriptanswers, scriptinput


def _rec(index, answer, *, context="ctx1:a", kind="input", prompt="p: ", secret=False):
    r = {"index": index, "kind": kind, "prompt": prompt, "context": context, "answer": answer}
    if secret:
        r["secret"] = True
    return r


# ---------------------------------------------------------------- 上下文摘要


def test_the_context_follows_the_output_since_the_last_question_and_the_earlier_answers():
    base = scriptinput.context_digest("input", "n: ", "1) a\n2) b\n", [])
    assert base == scriptinput.context_digest("input", "n: ", "1) a\n2) b\n", [])
    assert base != scriptinput.context_digest("input", "n: ", "1) b\n2) a\n", [])
    assert base != scriptinput.context_digest("readline", "n: ", "1) a\n2) b\n", [])
    one = scriptinput.context_digest("input", "m: ", "", [_rec(1, "1")])
    assert one != scriptinput.context_digest("input", "m: ", "", [_rec(1, "2")])
    # 口令只按占位进摘要：值不同摘要相同（摘要不携带也不可反推口令）
    a = scriptinput.context_digest("input", "m: ", "", [_rec(1, None, kind="getpass", secret=True)])
    b = scriptinput.context_digest("input", "m: ", "", [_rec(1, None, kind="getpass", secret=True)])
    assert a == b


def test_the_channel_keeps_no_secret_and_hands_each_question_its_own_segment(tmp_path, capsys):
    tail = scriptinput.StdoutTail(open(tmp_path / "log", "w", encoding="utf-8"))
    ch = scriptinput.Channel(tmp_path / "rv", "s.py", tail=tail)
    ch.reset()
    tail.write("1) a\n")
    (tmp_path / "rv" / scriptinput.reply_name(1)).write_text(json.dumps({"answer": "pw"}), "utf-8")
    assert ch.ask("token: ", "getpass") == "pw"
    tail.write("1) a\n")
    (tmp_path / "rv" / scriptinput.reply_name(2)).write_text(json.dumps({"answer": "1"}), "utf-8")
    assert ch.ask("n: ", "input") == "1"
    first, second = ch.record
    assert first == {
        "index": 1,
        "kind": "getpass",
        "prompt": "token: ",
        "context": first["context"],
        "answer": None,
        "secret": True,
    }
    # 第二问只看第一问之后打印的那段 + 前一问（口令只是占位）
    assert second["context"] == scriptinput.context_digest("input", "n: ", "1) a\n", [first])
    assert "pw" not in json.dumps(ch.record)


# ---------------------------------------------------------------- 项目答案：上下文 + 运行配置


def test_recall_reuses_only_a_full_match_and_otherwise_only_suggests(tmp_path):
    scriptanswers.remember(tmp_path, "s.py", 1, "n: ", "2", context="ctx1:x", run_config=None)
    exact = scriptanswers.recall(tmp_path, "s.py", 1, "n: ", context="ctx1:x")
    assert exact == scriptanswers.Recall(answer="2")
    moved = scriptanswers.recall(tmp_path, "s.py", 1, "n: ", context="ctx1:y")
    assert moved == scriptanswers.Recall(suggestion="2", recheck=scriptanswers.RECHECK_CONTEXT)
    other_cfg = scriptanswers.recall(
        tmp_path, "s.py", 1, "n: ", context="ctx1:x", run_config="rc_1"
    )
    assert other_cfg == scriptanswers.Recall(suggestion="2", recheck=scriptanswers.RECHECK_CONFIG)
    assert (
        scriptanswers.recall(tmp_path, "s.py", 1, "other: ", context="ctx1:x")
        == scriptanswers.Recall()
    )
    assert (
        scriptanswers.recall(tmp_path, "s.py", 1, "n: ", kind="readline", context="ctx1:x").answer
        is None
    )


def test_a_version_one_file_is_read_and_its_answers_are_only_suggestions(tmp_path):
    path = scriptanswers.answers_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"version": 1, "scripts": {"s.py": [{"index": 1, "prompt": "n: ", "answer": "2"}]}}
        ),
        "utf-8",
    )
    got = scriptanswers.recall(tmp_path, "s.py", 1, "n: ", context="ctx1:x")
    assert got == scriptanswers.Recall(suggestion="2", recheck=scriptanswers.RECHECK_LEGACY)
    # 同一 (序号, 运行配置) 再答一次：替换旧条目，写成新版本；界面看到的答案管理形状不变
    scriptanswers.remember(tmp_path, "s.py", 1, "n: ", "3", context="ctx1:x")
    data = json.loads(path.read_text("utf-8"))
    assert data["version"] == scriptanswers.FORMAT_VERSION == 2
    # 上下文摘要不进项目文件（T12，ADR 0099 §十）：它在本机侧表里
    assert data["scripts"]["s.py"] == [
        {"index": 1, "prompt": "n: ", "answer": "3", "kind": "input"}
    ]
    assert scriptanswers.load(tmp_path) == {
        "s.py": [{"index": 1, "prompt": "n: ", "answer": "3", "kind": "input"}]
    }


def _low_entropy_context(menu: str, earlier: str = "") -> str:
    """真 worker 会算出的那种摘要：菜单输出低熵，旁人照着脚本就能枚举出来。"""
    return scriptinput.context_digest("input", "选哪一项: ", menu, [{"answer": earlier}])


def test_the_shareable_answer_file_never_carries_a_guessable_context_digest(tmp_path):
    ctx = _low_entropy_context("1) 线性\n2) 对数\n")
    scriptanswers.remember(tmp_path, "s.py", 0, "选哪一项: ", "2", context=ctx, run_config="rc_a")
    raw = scriptanswers.answers_path(tmp_path).read_text("utf-8")
    assert ctx not in raw and ctx.split(":", 1)[1] not in raw
    assert all("context" not in e for e in json.loads(raw)["scripts"]["s.py"])
    # 答案位置与明文非口令答案保持 ADR 0099 原样
    assert json.loads(raw)["scripts"]["s.py"][0]["answer"] == "2"
    # 这台机器上仍按上下文原样复用、换了上下文只给建议
    hit = scriptanswers.recall(tmp_path, "s.py", 0, "选哪一项: ", context=ctx, run_config="rc_a")
    assert hit == scriptanswers.Recall(answer="2")
    other = _low_entropy_context("1) 对数\n2) 线性\n")
    moved = scriptanswers.recall(
        tmp_path, "s.py", 0, "选哪一项: ", context=other, run_config="rc_a"
    )
    assert moved == scriptanswers.Recall(suggestion="2", recheck=scriptanswers.RECHECK_CONTEXT)
    # 摘要在 Tavotto 自己的数据目录里，不在项目里
    side = scriptanswers.contexts_path(tmp_path)
    assert ctx in side.read_text("utf-8")
    assert tmp_path not in side.parents


def test_an_answer_file_from_another_machine_is_only_a_suggestion(tmp_path):
    ctx = _low_entropy_context("1) a\n2) b\n")
    scriptanswers.remember(tmp_path, "s.py", 0, "选哪一项: ", "1", context=ctx)
    # 换一台机器 = 项目文件跟过去了，本机侧表没有
    scriptanswers.contexts_path(tmp_path).unlink()
    got = scriptanswers.recall(tmp_path, "s.py", 0, "选哪一项: ", context=ctx)
    assert got == scriptanswers.Recall(suggestion="1", recheck=scriptanswers.RECHECK_LEGACY)


def test_a_context_written_into_the_project_file_is_not_trusted_and_is_dropped(tmp_path):
    """T08–T11 的开发版把摘要写进了项目文件（从未发布）：读入丢弃、只当建议，下一次写就不再带它。"""
    ctx = _low_entropy_context("1) a\n")
    path = scriptanswers.answers_path(tmp_path)
    path.parent.mkdir(parents=True)
    entry = {"index": 0, "prompt": "q: ", "answer": "1", "kind": "input", "context": ctx}
    path.write_text(json.dumps({"version": 2, "scripts": {"s.py": [entry]}}), "utf-8")
    got = scriptanswers.recall(tmp_path, "s.py", 0, "q: ", context=ctx)
    assert got == scriptanswers.Recall(suggestion="1", recheck=scriptanswers.RECHECK_LEGACY)
    scriptanswers.remember(tmp_path, "s.py", 1, "r: ", "x", context="ctx1:other")
    assert ctx not in path.read_text("utf-8")


def test_a_synced_answer_does_not_inherit_this_machine_s_context(tmp_path):
    ctx = _low_entropy_context("1) a\n2) b\n")
    scriptanswers.remember(tmp_path, "s.py", 0, "q: ", "1", context=ctx)
    # 别处（同步 / 手改）把同一问的答案换成了 2：本机记的上下文属于 1，不能拿来原样套 2
    path = scriptanswers.answers_path(tmp_path)
    data = json.loads(path.read_text("utf-8"))
    data["scripts"]["s.py"][0]["answer"] = "2"
    path.write_text(json.dumps(data), "utf-8")
    got = scriptanswers.recall(tmp_path, "s.py", 0, "q: ", context=ctx)
    assert got == scriptanswers.Recall(suggestion="2", recheck=scriptanswers.RECHECK_LEGACY)


def test_editing_and_forgetting_keep_the_local_contexts_in_step(tmp_path):
    scriptanswers.remember(tmp_path, "s.py", 0, "q: ", "1", context="ctx1:c")
    # 答案管理里改答案：上下文仍是那一问的，改后的答案原样复用（与 T08 行为一致）
    assert scriptanswers.update(tmp_path, "s.py", 0, "3")
    assert scriptanswers.recall(tmp_path, "s.py", 0, "q: ", context="ctx1:c").answer == "3"
    assert scriptanswers.forget(tmp_path, "s.py")
    assert "ctx1:c" not in scriptanswers.contexts_path(tmp_path).read_text("utf-8")


def test_two_configurations_keep_their_own_answers(tmp_path):
    scriptanswers.remember(tmp_path, "s.py", 1, "n: ", "1", context="c", run_config=None)
    scriptanswers.remember(tmp_path, "s.py", 1, "n: ", "2", context="c", run_config="rc_b")
    assert scriptanswers.recall(tmp_path, "s.py", 1, "n: ", context="c").answer == "1"
    assert (
        scriptanswers.recall(tmp_path, "s.py", 1, "n: ", context="c", run_config="rc_b").answer
        == "2"
    )


# ---------------------------------------------------------------- 执行转录


def test_a_transcript_is_bound_to_the_execution_and_replaced_only_by_a_new_one(tmp_path):
    first = inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "2")])
    assert first is not None and first.id.startswith(inputtranscript.ID_PREFIX)
    assert inputtranscript.lookup(tmp_path, "s.py", None) == first
    # 热会话上又一次 build 往返 / 同样的问答：不换 id、不重写
    assert inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "2")]) == first
    second = inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "3")])
    assert second.id != first.id and inputtranscript.lookup(tmp_path, "s.py", None) == second
    # 另一份运行配置 / 另一个项目互不相干
    assert inputtranscript.lookup(tmp_path, "s.py", "rc_x") is None
    assert inputtranscript.lookup(tmp_path / "other", "s.py", None) is None
    # 一次没有问任何输入的成功执行：这批图此刻不来自带输入的执行，旧转录清掉
    assert inputtranscript.bind(tmp_path, "s.py", None, []) is None
    assert inputtranscript.lookup(tmp_path, "s.py", None) is None


def test_a_secret_is_never_written_into_a_transcript(tmp_path):
    rec = _rec(1, "hunter2", kind="getpass", secret=True)
    t = inputtranscript.bind(tmp_path, "s.py", None, [rec, _rec(2, "x")])
    assert t.entries[0]["answer"] is None and t.entries[0]["secret"] is True
    assert "hunter2" not in inputtranscript.store_path(tmp_path).read_text("utf-8")
    assert t.counts() == {"id": t.id, "count": 2, "secret": 1}


def test_an_oversized_transcript_is_not_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(inputtranscript, "MAX_ENTRIES", 2)
    inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "a")])
    assert inputtranscript.bind(tmp_path, "s.py", None, [_rec(i, "a") for i in range(1, 4)]) is None
    assert inputtranscript.lookup(tmp_path, "s.py", None) is None


def test_forgetting_drops_every_configuration_of_that_script_only(tmp_path):
    inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "a")])
    inputtranscript.bind(tmp_path, "s.py", "rc_1", [_rec(1, "b")])
    inputtranscript.bind(tmp_path, "t.py", None, [_rec(1, "c")])
    assert inputtranscript.forget(tmp_path, "s.py") is True
    assert inputtranscript.lookup(tmp_path, "s.py", None) is None
    assert inputtranscript.lookup(tmp_path, "s.py", "rc_1") is None
    assert inputtranscript.lookup(tmp_path, "t.py", None) is not None


# ---------------------------------------------------------------- 重放判据


def test_replay_matching_never_fills_a_secret_or_a_changed_context():
    replay = inputbroker.ReplayAnswers.of(
        [_rec(1, None, kind="getpass", secret=True, prompt="t: "), _rec(2, "2", context="ctx1:b")]
    )
    assert replay.match(1, "t: ", "getpass", "ctx1:a")[0] == inputbroker.MATCH_SECRET
    assert replay.match(2, "p: ", "input", "ctx1:b")[0] == inputbroker.MATCH_ANSWER
    assert replay.match(2, "p: ", "input", "ctx1:z")[0] == inputbroker.MATCH_MISMATCH
    assert replay.match(2, "q: ", "input", "ctx1:b")[0] == inputbroker.MATCH_MISMATCH
    assert replay.match(3, "p: ", "input", None) == (inputbroker.MATCH_MISMATCH, None)


# ---------------------------------------------------------------- 任务诊断投影


def test_the_input_facts_projection_is_counts_and_closed_codes_only():
    facts = inputbroker.InputFacts(
        asked=2, shown=1, timed_out=1, secret=1, no_answer="secret_required", transcript="it_x"
    ).payload()
    got = inputbroker.facts_projection(facts)
    assert got == {
        "asked": 2,
        "shown": 1,
        "answered": 0,
        "eof": 0,
        "autofilled": 0,
        "replayed": 0,
        "timed_out": 1,
        "stopped": 0,
        "secret": 1,
        "no_answer": "secret_required",
        "transcript_replay": True,
    }
    assert inputbroker.facts_projection({**facts, "no_answer": "free text"})["no_answer"] is None
    assert inputbroker.facts_projection(inputbroker.InputFacts().payload()) is None
    assert inputbroker.facts_projection(None) is None


def _block_replace(monkeypatch):
    import os

    real = os.replace

    def blocked(src, dst, *a, **k):
        if str(dst).endswith(".json") and "inputtranscripts" in str(dst):
            raise OSError("blocked")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", blocked)


def test_a_failed_binding_write_never_leaves_the_old_transcript_usable(tmp_path, monkeypatch):
    # r4221584222：新转录没落盘 → 旧绑定必须失效，冷重放不能用旧值
    inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "old")])
    assert inputtranscript.lookup(tmp_path, "s.py", None) is not None
    _block_replace(monkeypatch)
    try:
        inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "new")])
    except OSError:
        pass
    monkeypatch.undo()
    assert inputtranscript.lookup(tmp_path, "s.py", None) is None


def test_a_failed_clear_write_never_leaves_the_old_transcript_usable(tmp_path, monkeypatch):
    inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "old")])
    _block_replace(monkeypatch)
    try:
        inputtranscript.bind(tmp_path, "s.py", None, [])
    except OSError:
        pass
    monkeypatch.undo()
    assert inputtranscript.lookup(tmp_path, "s.py", None) is None


def test_when_the_old_binding_cannot_be_invalidated_the_execution_is_not_bound(
    tmp_path, monkeypatch
):
    class _W:
        figures_dir = str(tmp_path)
        script_name = "s.py"
        build_failed = False

    inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "old")])
    _block_replace(monkeypatch)
    from pathlib import Path

    def no_unlink(self, *a, **k):
        raise OSError("blocked")

    monkeypatch.setattr(Path, "unlink", no_unlink)
    import pytest

    with pytest.raises(inputtranscript.StaleTranscriptError):
        inputbroker.finished(_W(), [_rec(1, "new")])


class _Worker:
    def __init__(self, root):
        self.figures_dir = str(root)
        self.script_name = "s.py"
        self.out_dir = None
        self.build_failed = False


def test_an_edit_during_a_running_build_makes_its_late_transcript_unusable(tmp_path):
    # r4221675644：build 在飞 → 改答案（forget）→ 旧 build 事后才绑定 → 冷重放不能用旧值
    w = _Worker(tmp_path)
    with inputbroker.serving(w):  # 进门取基线
        inputtranscript.forget(tmp_path, "s.py", run_config=None, all_configs=True)  # 用户改答案
    inputbroker.finished(w, [_rec(1, "old")])  # 旧 build 事后绑定
    assert inputtranscript.lookup(tmp_path, "s.py", None) is None


def test_the_reader_ignores_a_transcript_whose_basis_does_not_match(tmp_path):
    # r4221675644：不依赖写入顺序——即使旧基线的转录被写进了盘，读者也不认
    inputtranscript.bind(tmp_path, "s.py", None, [_rec(1, "old")])
    assert inputtranscript.lookup(tmp_path, "s.py", None) is not None
    path = inputtranscript.store_path(tmp_path)
    data = json.loads(path.read_text("utf-8"))
    data["generations"] = {"s.py": "g_changed"}
    path.write_text(json.dumps(data), "utf-8")
    assert inputtranscript.lookup(tmp_path, "s.py", None) is None


def test_a_single_configuration_edit_only_invalidates_that_configuration(tmp_path):
    inputtranscript.bind(tmp_path, "s.py", "rc_a", [_rec(1, "a")])
    inputtranscript.bind(tmp_path, "s.py", "rc_b", [_rec(1, "b")])
    inputtranscript.forget(tmp_path, "s.py", run_config="rc_a", all_configs=False)
    assert inputtranscript.lookup(tmp_path, "s.py", "rc_a") is None
    assert inputtranscript.lookup(tmp_path, "s.py", "rc_b") is not None
