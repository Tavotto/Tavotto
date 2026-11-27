# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""数据文件的内容摘要（ADR 0178，图的来历核对「数据变没变」）：`propose` 响应的 `data_sha256` 与 `GET /api/ai/data-figure/data-digest` 是同一口径
（原始字节的 sha256）。判据的主语是**磁盘上那个文件此刻的字节**：同样大小、内容不同的两份必须摘要不同（只比大小的缺口）；
路径出界 / 不在 / 不是文件 / 开关关着都不泄露任何东西（null / 404），也不是错误。
"""

from __future__ import annotations

import hashlib

from tavotto.engine import ai_data_propose as P
from tests.test_ai_data_propose import project, propose, put  # noqa: F401

ROWS = [["g", "v"], ["a", 1.2], ["a", 2.2], ["b", 3.2], ["b", 4.2]]


def digest(client, table):
    return client.get("/api/ai/data-figure/data-digest", query_string={"table": table})


def test_propose_reports_the_sha_of_the_bytes_it_read_and_the_endpoint_agrees(project):
    _, client, figs, _ = project
    path = put(figs, "data/a.csv", ROWS)
    want = hashlib.sha256(path.read_bytes()).hexdigest()
    out = propose(client, render="lazy", table="a.csv").get_json()
    assert out["kind"] == "candidates" and out["data_sha256"] == want
    assert digest(client, "data/a.csv").get_json() == {"sha256": want}


def test_same_size_different_content_has_a_different_digest(project):
    _, client, figs, _ = project
    path = put(figs, "data/a.csv", ROWS)
    before = digest(client, "data/a.csv").get_json()["sha256"]
    raw = path.read_bytes()
    assert b"1.2" in raw
    path.write_bytes(raw.replace(b"1.2", b"1.3"))  # 1.2 → 1.3：字节数不变
    assert path.stat().st_size == len(raw)
    assert digest(client, "data/a.csv").get_json()["sha256"] != before


def test_paths_that_cannot_be_checked_are_null_not_errors(project, tmp_path):
    _, client, figs, _ = project
    put(figs, "data/a.csv", ROWS)
    (tmp_path / "outside.csv").write_text("x\n1\n", encoding="utf-8")
    for bad in (
        "data/missing.csv",
        "data",
        "../outside.csv",
        str(tmp_path / "outside.csv"),
        "",
        "C:\\x.csv",
    ):
        r = digest(client, bad)
        assert r.status_code == 200 and r.get_json() == {"sha256": None}, bad
    assert client.get("/api/ai/data-figure/data-digest").get_json() == {"sha256": None}


def test_too_large_is_null(project, monkeypatch):
    _, client, figs, _ = project
    put(figs, "data/a.csv", ROWS)
    monkeypatch.setattr(P, "DIGEST_MAX_BYTES", 4)
    assert digest(client, "data/a.csv").get_json() == {"sha256": None}


def test_the_endpoint_is_behind_the_experiment_switch(project):
    _, client, figs, state = project
    put(figs, "data/a.csv", ROWS)
    state["taf"] = False
    r = digest(client, "data/a.csv")
    assert r.status_code == 404 and r.get_json()["code"] == "taf_disabled"
