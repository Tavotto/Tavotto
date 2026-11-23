# ruff: noqa: F811 - `project` 是从 test_ai_data_propose 导入的 fixture，用例参数同名
"""「从数据到图」按画布的出版规范出图（ADR 0178 修订 2026-11-23；ADR 0155 的新消费者）。

判据的主语：**请求带的那份规范在每一层的读法是否一致**——
* 不带规范：样式、脚本、响应与从前逐字相同（默认规范）；
* 带规范：栏宽取规范规定的栏位（`widths_mm` 或 `page_columns`，后者经 `profiles.derive_page_columns`）、正文字号夹进规范允许的范围；
  规范没规定的项落到具名默认（期刊没写就是没写），下限 0 不当成数字用；
* 候选预览的脚本与落盘的脚本用同一份样式；
* 规范不合法不让出图失败：退回默认并标出；规范进幂等键。
"""

from __future__ import annotations

import copy
import re
import socket

import pytest

from tavotto.engine import ai_data_propose as P, ai_data_template as tpl, profiles
from tavotto.engine.agent_runner import wires
from tests.test_ai_data_propose import (  # noqa: F401
    commit,
    needs_worker,
    project,
    propose,
    put,
)


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


def journal_spec(**over) -> dict:
    """期刊中性底 + 这本「期刊」写了的项（前端 `resolveDocumentSpec` 交来的就是这种并好的规范）。"""
    spec = copy.deepcopy(profiles.load("journal-base-v1"))
    spec.update(over)
    return spec


NATURE_LIKE = {
    "widths_mm": {"single": 89.0, "double": 183.0, "tolerance_mm": 0.5, "allow_custom": True},
    "min_effective_font_size_pt": 5.0,
    "max_font_size_pt": 7.0,
}

ROWS = [["group", "value"]] + [[g, v] for g in "ABC" for v in (1.0, 1.2, 0.9, 1.1)]


def figsize_single_mm(script: str) -> float:
    m = re.search(r"figsize=\(\(([0-9.]+), ", script)
    assert m, script[:400]
    return round(float(m.group(1)) * 25.4, 3)


def cand_script(body) -> str:
    return (P.CANDIDATE_ROOT / body["session"] / "tavotto_figures" / "cand_1.py").read_text(
        encoding="utf-8"
    )


# =========================================================================== 样式（纯函数）
def test_default_profile_style_is_unchanged():
    d = tpl.style_from_profile()
    assert (d["width_single_mm"], d["width_double_mm"]) == (80.0, 150.0)
    assert (d["font_pt"], d["min_font_pt"]) == (9.0, 8.0)
    assert d == tpl.style_from_profile(profiles.load())


def test_journal_widths_and_font_range_are_used():
    d = tpl.style_from_profile(profiles.validate_spec(journal_spec(**NATURE_LIKE)))
    assert (d["width_single_mm"], d["width_double_mm"]) == (89.0, 183.0)
    # 期刊不规定默认字号：9 pt 夹进 5–7 pt
    assert d["font_pt"] == 7.0 and d["min_font_pt"] == 5.0


def test_journal_that_specifies_nothing_falls_back_to_named_defaults():
    d = tpl.style_from_profile(profiles.validate_spec(journal_spec()))
    base = tpl._STYLE_DEFAULTS
    assert (d["width_single_mm"], d["width_double_mm"]) == (
        base["width_single_mm"],
        base["width_double_mm"],
    )
    # 下限 0 = 没规定：不能让图例字号变成 0
    assert d["font_pt"] == 9.0 and d["min_font_pt"] == base["min_font_pt"]
    assert d["latin_fonts"] == base["latin_fonts"] and d["line_widths_pt"] == base["line_widths_pt"]


def col(cid: str, w: float) -> dict:
    return {"id": cid, "label": {"zh": cid, "en": cid}, "width_mm": w, "qualifier": "exact"}


@pytest.mark.parametrize(
    "cols, want",
    [
        ([col("a", 85.0), col("b", 114.0), col("c", 174.0)], (85.0, 174.0)),
        ([col("only", 120.0)], (120.0, 120.0)),
    ],
)
def test_page_columns_give_narrowest_and_widest(cols, want):
    d = tpl.style_from_profile(profiles.validate_spec(journal_spec(page_columns=cols)))
    assert (d["width_single_mm"], d["width_double_mm"]) == want


def test_small_text_never_exceeds_body_text():
    d = tpl.style_from_profile(
        profiles.validate_spec(journal_spec(min_effective_font_size_pt=10.0, max_font_size_pt=12.0))
    )
    assert d["font_pt"] == 10.0 and d["min_font_pt"] == 10.0


# =========================================================================== 请求里的规范
def test_parse_spec_absent_invalid_and_oversized():
    assert P.parse_spec(None) == (None, None, False)
    for bad in ("nature", [], {"profile_id": "x"}, {"widths_mm": "wide"}):
        assert P.parse_spec(bad) == (None, "invalid", True), bad
    big = journal_spec(source="x" * (P.SPEC_MAX_BYTES + 1))
    assert P.parse_spec(big) == (None, "invalid", True)
    style, digest, bad = P.parse_spec(journal_spec(**NATURE_LIKE))
    assert style["width_single_mm"] == 89.0 and len(digest) == 64 and bad is False


def test_propose_without_spec_is_the_default_and_has_no_new_keys(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    body = propose(client, render="lazy").get_json()
    assert body["kind"] == "candidates" and "spec_fallback" not in body
    assert "figure_style" not in body
    assert figsize_single_mm(cand_script(body)) == 80.0
    assert '"font.size": 9.0' in cand_script(body)


def test_propose_with_spec_draws_at_journal_width_and_font(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    body = propose(client, render="lazy", spec=journal_spec(**NATURE_LIKE)).get_json()
    assert body["kind"] == "candidates" and "spec_fallback" not in body
    assert body["figure_style"] == {
        "width_single_mm": 89.0,
        "width_double_mm": 183.0,
        "font_pt": 7.0,
    }
    script = cand_script(body)
    assert figsize_single_mm(script) == 89.0
    assert '"font.size": 7.0' in script and '"legend.fontsize": 5.0' in script


def test_invalid_spec_falls_back_and_says_so(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    r = propose(client, render="lazy", spec={"widths_mm": "wide"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["kind"] == "candidates" and body["spec_fallback"] is True
    assert figsize_single_mm(cand_script(body)) == 80.0


def test_spec_is_part_of_the_idempotency_key(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    a = propose(client, render="lazy", client_request_id="rid-spec-0001").get_json()
    again = propose(client, render="lazy", client_request_id="rid-spec-0001").get_json()
    b = propose(
        client, render="lazy", client_request_id="rid-spec-0001", spec=journal_spec(**NATURE_LIKE)
    ).get_json()
    assert again["session"] == a["session"] and b["session"] != a["session"]
    assert figsize_single_mm(cand_script(b)) == 89.0


@needs_worker
def test_committed_script_uses_the_same_style_as_the_preview(project):
    _, client, figs, _ = project
    put(figs, "data/g.csv", ROWS)
    body = propose(client, render="lazy", spec=journal_spec(**NATURE_LIKE)).get_json()
    first = body["items"][0]
    assert client.get(first["thumbnail_url"]).status_code == 200
    out = commit(client, body["session"], first["id"])
    assert out.status_code == 200, out.get_json()
    done = out.get_json()
    assert done["task"]["outcome"] == "completed", done
    script = (figs / done["script"]).read_text(encoding="utf-8")
    assert figsize_single_mm(script) == 89.0 and '"font.size": 7.0' in script
