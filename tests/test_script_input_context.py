"""运行时 input 的上下文匹配、执行转录与秘密——真 worker（T08，ADR 0099 §九）。

判据的主语是**子进程里的用户脚本**实际读到了什么、画出了哪张图（脚本把读到的值写进项目外的结果文件，
图名由答案决定），以及发给界面的事件里有没有再问一次。不拿我们自己的记账当证据。

* 菜单换了序、提示一字不差（F2 形状）：旧编号**不许**静默复用，要重新问；旧答案最多是建议。
* 冷重放（热会话没了、重新跑脚本）用**那一次执行**的转录，不读之后被改写的项目答案文件。
* getpass：答案不进 build 响应、热会话记账、转录、日志；重放需要时重新问，没人能答就明确失败，绝不拿空串继续。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import threading

import pytest

from tavotto.engine import config, inputbroker, inputtranscript, pool, scriptanswers, scriptinput

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

#: F2（T00 fixture）的形状：列名来自数据文件，编号清单随数据变；提示一字不差。每次执行在结果文件里追加一行。
MENU = """\
import csv, json, os
import matplotlib.pyplot as plt

rows = list(csv.DictReader(open("data.csv", encoding="utf-8")))
cols = [c for c in rows[0] if c != "x"]
print("Series:")
for i, c in enumerate(cols, 1):
    print(f"  {i}) {c}")
n = int(input("Choose series number: "))
name = cols[n - 1]
with open(os.environ["SI_RESULT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(name) + "\\n")
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.plot([float(r["x"]) for r in rows], [float(r[name]) for r in rows])
fig.savefig(f"series_{name}.pdf")
"""

#: getpass 一问 + 普通 input 一问；脚本把读到的口令写进项目外的结果文件（用户脚本自己写的，不是 Tavotto）。
SECRET = """\
import getpass, json, os
import matplotlib.pyplot as plt

token = getpass.getpass("token: ")
label = input("label: ")
with open(os.environ["SI_RESULT"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps([token, label]) + "\\n")
fig, ax = plt.subplots(figsize=(2, 1.5))
ax.set_title(label)
fig.savefig("Sec.pdf")
"""

SENTINEL = "S3NT1NEL-t08-pass"


class Frontend:
    """假界面：记下每条事件；`answers` 里有的提示就当场作答。"""

    def __init__(self, answers: dict | None = None, *, present: bool = True):
        self.answers = dict(answers or {})
        self.present = present
        self.events: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def publish(self, event: str, project: str, data: dict) -> None:
        with self._lock:
            self.events.append((event, dict(data)))
        if event == "script.input_requested" and data["prompt"] in self.answers:
            value = self.answers[data["prompt"]]
            threading.Thread(
                target=inputbroker.answer, args=(data["id"], value), daemon=True
            ).start()

    def of(self, kind: str) -> list[dict]:
        with self._lock:
            return [d for e, d in self.events if e == kind]


@pytest.fixture
def figs(tmp_path, monkeypatch):
    root = tmp_path / "figs"
    root.mkdir()
    monkeypatch.setenv("SI_RESULT", str(tmp_path / "result.jsonl"))
    monkeypatch.setenv(scriptinput.TIMEOUT_ENV, "20")
    yield root
    pool.shutdown_all(str(root), wait=True)


@pytest.fixture
def frontend(monkeypatch):
    def install(answers=None, *, present=True) -> Frontend:
        fe = Frontend(answers, present=present)
        monkeypatch.setattr(inputbroker, "_publish", fe.publish)
        monkeypatch.setattr(inputbroker, "_has_answerer", lambda project: fe.present)
        return fe

    return install


def _write_menu(root, cols):
    (root / "menu.py").write_text(MENU, encoding="utf-8")
    with open(root / "data.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["x", *cols])
        for x in range(3):
            w.writerow([x, *[x * (i + 1) for i in range(len(cols))]])


def _runs(tmp_path) -> list:
    path = tmp_path / "result.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _cold(figs, script):
    """热会话没了（应用重开 / 会话被回收）：下一次用到这张图就要从零再跑一遍脚本。"""
    pool.shutdown_all(str(figs), wait=True)
    return pool.build(script, str(figs), "__main__")


@needs_worker
def test_one_answer_runs_the_script_exactly_once(figs, tmp_path, frontend):
    """I01：在界面里作答，同一次进程接着跑——脚本入口只执行一次，图按答案画出来。"""
    _write_menu(figs, ["alpha", "beta", "gamma"])
    fe = frontend({"Choose series number: ": "2"})
    _worker, resp = pool.build("menu.py", str(figs), "__main__")
    assert _runs(tmp_path) == ["beta"]
    assert sorted(resp["stems"]) == ["series_beta"]
    assert len(fe.of("script.input_requested")) == 1


@needs_worker
def test_an_unchanged_menu_is_filled_in_from_the_last_run(figs, tmp_path, frontend):
    _write_menu(figs, ["alpha", "beta", "gamma"])
    fe = frontend({"Choose series number: ": "2"})
    pool.build("menu.py", str(figs), "__main__")
    pool.invalidate("menu.py", str(figs))
    pool.build("menu.py", str(figs), "__main__")
    assert _runs(tmp_path) == ["beta", "beta"]
    assert len(fe.of("script.input_requested")) == 1
    assert [a["answer"] for a in fe.of("script.input_autofilled")] == ["2"]


@needs_worker
def test_a_reordered_menu_with_the_same_prompt_is_asked_again(figs, tmp_path, frontend):
    """I02（F2 实测缺口）：数据换了列序，编号清单变了、提示一字不差——旧的「2」不许静默复用。"""
    _write_menu(figs, ["alpha", "beta", "gamma"])
    fe = frontend({"Choose series number: ": "2"})
    pool.build("menu.py", str(figs), "__main__")
    assert _runs(tmp_path) == ["beta"]

    _write_menu(figs, ["gamma", "alpha", "beta"])
    pool.invalidate("menu.py", str(figs))
    fe.answers["Choose series number: "] = "3"  # 用户看着新的清单，选的还是 beta
    pool.build("menu.py", str(figs), "__main__")
    asked = fe.of("script.input_requested")
    assert len(asked) == 2, "菜单变了却没有再问：静默用了旧编号"
    assert _runs(tmp_path) == ["beta", "beta"]
    assert fe.of("script.input_autofilled") == []


@needs_worker
def test_a_reordered_menu_is_asked_again_from_the_project_answers_too(figs, tmp_path, frontend):
    """没有执行转录（另一台机器同步来的项目、转录超限 / 被作废）时走项目答案文件：同样按上下文判，菜单换了序
    就重新问，旧编号只是建议。"""
    _write_menu(figs, ["alpha", "beta", "gamma"])
    fe = frontend({"Choose series number: ": "2"})
    pool.build("menu.py", str(figs), "__main__")
    inputtranscript.forget(figs, "menu.py")
    _write_menu(figs, ["gamma", "alpha", "beta"])
    pool.invalidate("menu.py", str(figs))
    fe.answers["Choose series number: "] = "3"
    pool.build("menu.py", str(figs), "__main__")
    asked = fe.of("script.input_requested")
    assert len(asked) == 2, "菜单变了却按项目答案静默用了旧编号"
    assert asked[1]["suggestion"] == "2" and asked[1]["recheck"] == "context_changed"
    assert _runs(tmp_path) == ["beta", "beta"]


@needs_worker
def test_the_reordered_menu_without_a_ui_fails_instead_of_guessing(figs, tmp_path, frontend):
    """没有能答题的界面（MCP / 后台导出）：菜单变了就明确 `script_needs_input`，不拿旧编号画一张错图。"""
    _write_menu(figs, ["alpha", "beta", "gamma"])
    fe = frontend({"Choose series number: ": "2"})
    pool.build("menu.py", str(figs), "__main__")
    _write_menu(figs, ["gamma", "alpha", "beta"])
    pool.invalidate("menu.py", str(figs))
    fe.present = False
    with pytest.raises(pool.WorkerError) as ei:
        pool.build("menu.py", str(figs), "__main__")
    assert ei.value.code == "script_needs_input"
    assert _runs(tmp_path) == ["beta"]


@needs_worker
def test_a_cold_replay_uses_the_answers_of_that_execution_not_the_later_file(
    figs, tmp_path, frontend
):
    """C28 / R03：冷重放用那一次执行的转录。项目答案文件之后被改写（另一台机器同步过来 / 手改）——
    重建这张图仍用当初的答案，一次都不问。"""
    _write_menu(figs, ["alpha", "beta", "gamma"])
    fe = frontend({"Choose series number: ": "2"})
    pool.build("menu.py", str(figs), "__main__")
    scriptanswers.update(figs, "menu.py", 1, "1")  # 不经过答案管理（那条路是「明确要重算」）
    _cold(figs, "menu.py")
    assert _runs(tmp_path) == ["beta", "beta"]
    assert len(fe.of("script.input_requested")) == 1


def _all_text_under(*roots) -> str:
    out = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    out.append(
                        open(os.path.join(dirpath, name), "rb").read().decode("utf-8", "replace")
                    )
                except OSError:
                    pass
    return "\n".join(out)


@needs_worker
def test_a_getpass_answer_never_reaches_the_records_or_the_disk(figs, tmp_path, frontend):
    """I04 / C17：口令只交给脚本。build 响应、热会话记账、转录与 Tavotto 的数据目录 / 会话缓存 / 项目目录里
    都没有它，也没有它的 sha256（低熵值的哈希可猜）。"""
    fe = frontend({"token: ": SENTINEL, "label: ": "plain-label"})
    (figs / "sec.py").write_text(SECRET, encoding="utf-8")
    worker, resp = pool.build("sec.py", str(figs), "__main__")
    assert _runs(tmp_path) == [[SENTINEL, "plain-label"]]  # 脚本确实拿到了
    digest = hashlib.sha256(SENTINEL.encode()).hexdigest()
    records = json.dumps(resp["script_inputs"]) + json.dumps(worker.last_build_script_inputs)
    assert "plain-label" in records  # 先证明看的是这一次的记录
    assert SENTINEL not in records and digest not in records
    secret = [r for r in resp["script_inputs"] if r["kind"] == "getpass"]
    assert secret and secret[0]["answer"] is None and secret[0].get("secret") is True
    assert [e["prompt"] for e in scriptanswers.entries(figs, "sec.py")] == ["label: "]
    on_disk = _all_text_under(config.data_dir(), figs, worker.base)
    assert "plain-label" in on_disk
    assert SENTINEL not in on_disk and digest not in on_disk
    events = json.dumps(fe.events)
    assert SENTINEL not in events and digest not in events


@needs_worker
def test_a_cold_replay_asks_for_the_secret_again(figs, tmp_path, frontend):
    """秘密不落盘：冷重放需要它时重新问（只问口令，普通答案按转录回填）。"""
    fe = frontend({"token: ": SENTINEL, "label: ": "plain-label"})
    (figs / "sec.py").write_text(SECRET, encoding="utf-8")
    pool.build("sec.py", str(figs), "__main__")
    fe.answers["token: "] = "second-secret"
    _cold(figs, "sec.py")
    assert _runs(tmp_path) == [[SENTINEL, "plain-label"], ["second-secret", "plain-label"]]
    asked = [(a["prompt"], a["input_kind"]) for a in fe.of("script.input_requested")]
    assert asked == [("token: ", "getpass"), ("label: ", "input"), ("token: ", "getpass")]


@needs_worker
def test_a_missing_secret_without_a_ui_fails_and_never_continues_with_an_empty_string(
    figs, tmp_path, frontend
):
    fe = frontend({"token: ": SENTINEL, "label: ": "plain-label"})
    (figs / "sec.py").write_text(SECRET, encoding="utf-8")
    pool.build("sec.py", str(figs), "__main__")
    fe.present = False
    with pytest.raises(pool.WorkerError) as ei:
        _cold(figs, "sec.py")
    assert ei.value.code == "script_needs_input"
    assert _runs(tmp_path) == [[SENTINEL, "plain-label"]]  # 没有第二次「拿空串跑完」


@needs_worker
def test_the_write_back_replay_asks_for_the_secret_instead_of_reusing_it(figs, tmp_path, frontend):
    """写回 verify 的一次性重放：普通答案严格按热态那组；口令热态没留——重新问，而不是悄悄重放明文。"""
    fe = frontend({"token: ": SENTINEL, "label: ": "plain-label"})
    (figs / "sec.py").write_text(SECRET, encoding="utf-8")
    hot, _ = pool.build("sec.py", str(figs), "__main__")
    fe.answers["token: "] = "replay-secret"
    fresh = pool.one_shot(
        "sec.py", str(figs), "__main__", script_inputs=hot.last_build_script_inputs
    )
    try:
        fresh.ensure_built()
    finally:
        pool.discard(fresh)
    assert _runs(tmp_path)[-1] == ["replay-secret", "plain-label"]
    assert [a["prompt"] for a in fe.of("script.input_requested")] == [
        "token: ",
        "label: ",
        "token: ",
    ]
