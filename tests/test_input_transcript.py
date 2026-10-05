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
    assert data["scripts"]["s.py"] == [
        {"index": 1, "prompt": "n: ", "answer": "3", "kind": "input", "context": "ctx1:x"}
    ]
    assert scriptanswers.load(tmp_path) == {
        "s.py": [{"index": 1, "prompt": "n: ", "answer": "3", "kind": "input"}]
    }


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
