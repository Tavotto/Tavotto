# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""`propose(understand=False)`：不起「读懂数据」的后台任务（ADR 0178，`ai-agent-bridge.md`「显式不起任务」）。

判据的主语：**这一次请求有没有让后端走到 `ai_data_roles.start`（= 一次模型出站）**，不是响应里有没有某个键——
所以用「有可用连接 + 一个数调用次数的假传输」量：带 `understand: false` 时传输零调用、响应没有 `understanding`，候选与不带参数时逐字相同；
不带参数 / `True` 与从前逐字相同（有连接时多一个 `understanding` 键）；非布尔是 400 `propose_input_invalid`。
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

SUPPORT = Path(__file__).resolve().parent / "support"
if str(SUPPORT) not in sys.path:
    sys.path.insert(0, str(SUPPORT))

import data_figure_cases as D  # noqa: E402
import data_figure_roles as DR  # noqa: E402

from tavotto.engine import (  # noqa: E402
    ai_data_propose as P,
    ai_data_roles as R,
)
from tavotto.engine.agent_runner import wires  # noqa: E402
from tests.test_ai_data_propose import project, propose, put  # noqa: E402, F401

BY_ID = {c["id"]: c for c in DR.load_role_cases()}


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network touched")

    monkeypatch.setattr(wires, "post_json", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(P, "CANDIDATE_ROOT", tmp_path / "propose")
    R.reset_for_tests()
    yield
    R.reset_for_tests()


class CountingTransport:
    """数调用次数的传输：真正会发出站的地方。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        return self.inner(*a, **k)


def run(tmp_path, **extra):
    case = BY_ID["RC-01"]
    folder = DR.materialize(case, tmp_path / "proj")
    t = CountingTransport(DR.scripted(case))
    R.reset_for_tests()
    R.set_overrides(conn=D.fake_conn(), sync=True, post=t)
    resp = P.propose(tmp_path / "proj", project="p", folder=folder, render="lazy", **extra)
    return resp, t


def test_understand_false_never_reaches_the_model(tmp_path):
    resp, t = run(tmp_path, understand=False)
    assert t.calls == 0
    assert "understanding" not in resp and resp["kind"] == "candidates"


def test_the_default_and_true_are_unchanged(tmp_path):
    base, t0 = run(tmp_path)
    assert "understanding" in base and t0.calls >= 1  # 有连接时照旧起任务
    again, _ = run(tmp_path, understand=True)
    assert "understanding" in again


def test_candidates_are_identical_with_and_without_the_flag(tmp_path):
    with_task, _ = run(tmp_path)
    without, _ = run(tmp_path, understand=False)
    keep = lambda r: {k: v for k, v in r.items() if k not in ("understanding", "session", "items")}  # noqa: E731
    assert keep(with_task) == keep(without)
    codes = lambda r: [(i["title_code"], i.get("error"), i.get("error_source")) for i in r["items"]]  # noqa: E731
    assert codes(with_task) == codes(without)
    assert set(with_task) - set(without) == {"understanding"}


@pytest.mark.parametrize("bad", ["false", 0, 1, [], {}])
def test_non_boolean_is_rejected(tmp_path, bad):
    case = BY_ID["RC-01"]
    folder = DR.materialize(case, tmp_path / "proj")
    with pytest.raises(P.ProposeError) as e:
        P.propose(tmp_path / "proj", project="p", folder=folder, render="lazy", understand=bad)
    assert e.value.code == "propose_input_invalid"


def test_the_request_flag_reaches_propose_over_http(project):
    _, client, figs, _ = project
    put(figs, "data/a.csv", [["g", "v"], ["a", 1], ["a", 2], ["b", 3], ["b", 4]])
    t = CountingTransport(DR.scripted(BY_ID["RC-01"]))
    R.set_overrides(conn=D.fake_conn(), sync=True, post=t)
    out = propose(client, render="lazy", understand=False).get_json()
    assert out["kind"] == "candidates" and "understanding" not in out and t.calls == 0
    bad = propose(client, render="lazy", understand="no")
    assert bad.status_code == 400 and bad.get_json()["code"] == "propose_input_invalid"
