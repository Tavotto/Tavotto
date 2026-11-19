# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""数据出图提交的请求号占位（ADR 0178 / 0132）：`prepare_v2` 期间同一请求号的重试查得到「已受理、未完成」，不取会话、不另写一份。

判据的主语：量的是**磁盘上的脚本份数**、第二个请求**有没有碰会话**（`get_item` 调用次数）与响应形状，不是占位表本身；
用 `prepare_v2` 上的可控阻塞复现 Codex 说的窗口（客户端断线、放弃会话、同号重试）。
"""

from __future__ import annotations

import threading

from tavotto.engine import ai_data_figure as adf, ai_data_propose as P, ai_data_task
from tests.test_ai_data_propose import (
    commit,
    needs_worker,
    project,  # noqa: F401
    propose,
    put,
    simple_rows,
)

RID = "rid-commit-0001"


class Gate:
    """让第一次 `prepare_v2` 卡住，直到放行；之后的调用直通。"""

    def __init__(self, monkeypatch, fail=None):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.fail = fail
        real = ai_data_task.prepare_v2

        def stub(root, sel):
            self.calls += 1
            if self.calls == 1:
                self.entered.set()
                assert self.release.wait(60), "测试里没有放行"
                if self.fail is not None:
                    raise self.fail
            return real(root, sel)

        monkeypatch.setattr(ai_data_task, "prepare_v2", stub)


def run_in_thread(client_factory, session, item, rid, out):
    def work():
        out["resp"] = client_factory().post(
            "/api/ai/data-figure/commit",
            json={"session": session, "id": item, "client_request_id": rid},
        )

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def count_scripts(figs):
    return sorted(
        p.name
        for p in (figs / "data" / "tavotto_figures").glob("*.py")
        if not p.name.startswith("tavotto_plot_")
    )


@needs_worker
def test_a_retry_during_prepare_gets_not_finished_instead_of_session_expired(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client, render="lazy").get_json()
    session, item = body["session"], body["items"][0]["id"]
    gate = Gate(monkeypatch)
    seen = {"get_item": 0}
    real_get_item = P.get_item

    def counting(*a, **kw):
        seen["get_item"] += 1
        return real_get_item(*a, **kw)

    monkeypatch.setattr(P, "get_item", counting)
    first: dict = {}
    t = run_in_thread(m.app.test_client, session, item, RID, first)
    assert gate.entered.wait(30)
    after_first = seen["get_item"]
    # 客户端放弃了会话（断线后重画前的清理），同号重试：不是 propose_session_expired，也没碰会话
    assert P.discard(next(iter(P._SESSIONS.values())).project, session)
    r = client.post(
        "/api/ai/data-figure/commit",
        json={"session": session, "id": item, "client_request_id": RID},
    )
    assert r.status_code == 200, r.get_json()
    task = r.get_json()["task"]
    assert task["deduped"] is True and task["final"] is False and task["outcome"] is None
    assert seen["get_item"] == after_first, "第二个请求不许取会话"
    # 不同请求号不受影响：它自己走完整流程（会话已被放弃，所以是 404 过期——说明它确实走到了取会话）
    other = client.post(
        "/api/ai/data-figure/commit",
        json={"session": session, "id": item, "client_request_id": "rid-commit-0002"},
    )
    assert other.status_code == 404 and other.get_json()["code"] == "propose_session_expired"
    assert m._COMMIT_INFLIGHT == {(next(iter(m._COMMIT_INFLIGHT))[0], RID)}
    gate.release.set()
    t.join(120)
    assert first["resp"].status_code == 200
    out = first["resp"].get_json()
    assert out["task"]["outcome"] == "completed", out
    # 第三次同号查询：得到最终结果（已登记的任务），磁盘上只有一份脚本
    again = client.post(
        "/api/ai/data-figure/commit",
        json={"session": session, "id": item, "client_request_id": RID},
    ).get_json()
    assert again["task"]["deduped"] is True and again["task"]["outcome"] == "completed"
    assert again["script"] == out["script"]
    assert count_scripts(figs) == ["g_figure.py"]
    assert m._COMMIT_INFLIGHT == set(), "占位已清"


@needs_worker
def test_different_request_ids_do_not_block_each_other(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client, render="lazy").get_json()
    session = body["session"]
    first_item, second_item = body["items"][0]["id"], body["items"][1]["id"]
    gate = Gate(monkeypatch)
    first: dict = {}
    t = run_in_thread(m.app.test_client, session, first_item, RID, first)
    assert gate.entered.wait(30)
    r = commit(
        client, session, second_item, rid="rid-commit-0002"
    )  # 第一个卡在 prepare 时，另一个号照常完成
    assert r.status_code == 200 and r.get_json()["task"]["outcome"] == "completed", r.get_json()
    gate.release.set()
    t.join(120)
    assert first["resp"].get_json()["task"]["outcome"] == "completed"
    assert len(count_scripts(figs)) == 2


@needs_worker
def test_a_failed_prepare_clears_the_claim_and_the_same_id_gets_a_definite_answer(
    project, monkeypatch
):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client, render="lazy").get_json()
    session, item = body["session"], body["items"][0]["id"]
    gate = Gate(monkeypatch, fail=adf.DataSourceError("data_not_found"))
    first: dict = {}
    t = run_in_thread(m.app.test_client, session, item, RID, first)
    assert gate.entered.wait(30)
    gate.release.set()
    t.join(60)
    assert first["resp"].status_code == 400 and first["resp"].get_json()["code"] == "data_not_found"
    assert m._COMMIT_INFLIGHT == set(), "失败也清占位：不会留下永远在途"
    assert count_scripts(figs) == [] if (figs / "data" / "tavotto_figures").exists() else True
    # 同一请求号再来：这是一次确定的新提交（会话还在），得到确定的完成结果，不是「未完成」
    ok = commit(client, session, item, rid=RID).get_json()
    assert ok["task"]["outcome"] == "completed" and not ok["task"].get("deduped")


@needs_worker
def test_an_unexpected_exception_in_prepare_also_clears_the_claim(project, monkeypatch):
    m, client, figs, _ = project
    put(figs, "data/g.csv", simple_rows())
    body = propose(client, render="lazy").get_json()
    session, item = body["session"], body["items"][0]["id"]
    gate = Gate(monkeypatch, fail=RuntimeError("boom"))
    gate.release.set()
    r = commit(client, session, item, rid=RID)  # 未处理的异常由全局错误处理转成 500
    assert r.status_code == 500
    assert m._COMMIT_INFLIGHT == set()
    ok = commit(client, session, item, rid=RID).get_json()
    assert ok["task"]["outcome"] == "completed"


def test_commit_without_a_request_id_does_not_use_the_table(project, monkeypatch):
    m, client, figs, _ = project
    assert m._claim_commit_rid("p", "r") is True
    assert m._claim_commit_rid("p", "r") is False
    assert m._claim_commit_rid("q", "r") is True, "不同项目互不影响"
    m._release_commit_rid("p", "r")
    m._release_commit_rid("q", "r")
    assert m._COMMIT_INFLIGHT == set()
