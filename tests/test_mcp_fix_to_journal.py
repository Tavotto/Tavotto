"""`tavotto_fix_to_journal`（ADR 0180）的**真链路**：真 matplotlib、真 worker、真产物。

事务本体（`specfix.transaction`）在 `tests/test_specfix_real.py` 里盯；这里盯的是桥把它和
保留式规范化接成「两段、整体要么提交要么回到 B0」之后的几件事：

* 宽 120 mm 的图 → 自动取唯一相近的栏位、改图幅 + 修面板，修完**真的**过了、层级不倒挂；
* 栏位不唯一 → `column_choice_required`，会话与落盘记录零改动；
* 缺字体 → 字体那几条写进 `skipped`（带要的字体），其余照修；
* 修复引入了新的 warn → 整体回到 B0；导出被预检挡 / 产物核验不过 → 回到 B0、不出文件；
* `dry_run` → 与真跑同一份 diff 与最终 hash，会话回到 B0；
* 任何一次渲染带 warning → worker 作废（`worker_retired`），会话仍回到 B0 且还能继续用；
* preflight 结果的 `fix` 只在 MCP 整形层加；`auto` 的集合就是 `specfix.FIXABLE_RULES`（按结构读）。

缺带科学栈的解释器就跳过（.venv 里没有 matplotlib 是常态）。
"""

from __future__ import annotations

import copy
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto import pdfbackend  # noqa: E402
from tavotto.engine import (  # noqa: E402
    journals,
    pool as engine_pool,
    preflight,
    profiles,
    specfix,
)
from tavotto_mcp import bridge, server  # noqa: E402


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


WORKER_PY = _worker_python()
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有带科学栈的解释器，跳过真链路用例")

AVAILABLE_FONT = "DejaVu Serif"  # matplotlib 自带，任何机器都有
MISSING_FONT = "Tavotto Nonexistent Serif 9x"

#: 故意不合规的图，**120 mm 宽**：刻度 / 图例 7 pt（低于 8 pt 绝对下限）、轴标题 8.2 pt（合规）、
#: 标题 9 pt；曲线 1.2 pt、边框 0.6 pt 不在线宽档位上；刻度朝外、缺上右两边、图例带框；字体 DejaVu Sans。
SCRIPT = """\
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent


def main():
    plt.rcParams["font.family"] = "DejaVu Sans"
    x = np.linspace(0, 60, 30)
    fig, ax = plt.subplots(figsize=(120 / 25.4, 80 / 25.4))
    for k in (8, 20):
        ax.plot(x, 1 - np.exp(-x / k), lw=1.2, label=f"k = {k}")
    ax.set_xlabel("Time (min)", fontsize=8.2)
    ax.set_ylabel("Conversion (-)", fontsize=8.2)
    ax.set_title("Kinetics", fontsize=9)
    ax.tick_params(labelsize=7, direction="out")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for s in ax.spines.values():
        s.set_linewidth(0.6)
    ax.legend(fontsize=7, frameon=True)
    fig.tight_layout(pad=0.6)
    fig.savefig(OUT / "Kin.pdf")


if __name__ == "__main__":
    main()
"""


def _journal(latin: str = AVAILABLE_FONT, *, double: float = 183.0, **extra) -> dict:
    """一份期刊覆盖：单栏 110 / 双栏 `double`（现宽 120 mm 时只有单栏在 15% 以内）、不限宽高比、字体 `latin`。"""
    return {
        "widths_mm": {"single": 110.0, "double": double, "tolerance_mm": 0.5},
        "allowed_aspect_ratios": [],
        "font_family": {
            "latin": latin,
            "latin_accepted": [latin],
            "latin_substitutes_flagged": [],
        },
        **extra,
    }


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_NO_TELEMETRY", "1")
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", True)
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()


@pytest.fixture(scope="module", autouse=True)
def _shutdown_workers():
    yield
    bridge.shutdown_all()


@pytest.fixture
def sid(tmp_path) -> str:
    figures = tmp_path / "fig"
    figures.mkdir()
    (figures / "fig.py").write_text(SCRIPT, encoding="utf-8")
    (figures / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig.py": {"entry": "main", "cost": "light", "stems": ["Kin"]}}}),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [WORKER_PY, str(figures / "fig.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(figures),
    )
    assert proc.returncode == 0, proc.stderr
    return bridge.open_figure(str(figures), stem="Kin")["session_id"]


def _png_long_edge(b64: str) -> int:
    import base64

    raw = base64.b64decode(b64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", raw[16:24])
    return max(w, h)


def _fix(sid: str, **kwargs) -> dict:
    out = bridge.fix_to_journal(sid, **kwargs)
    out.pop("_previews", None)
    return out


def _state(sid: str) -> tuple[str, list]:
    s = bridge.get_session(sid)
    return s.patch_hash(), copy.deepcopy(s.patches)


def _persisted_hash(sid: str) -> str:
    """落盘记录里的 patch_hash：把内存会话摘掉，按记录重建（ADR 0078）。"""
    bridge.sessions().pop(sid)
    return bridge.get_session(sid).patch_hash()


def _eff(manifest: dict, role: str) -> list[float]:
    out = []
    for el in manifest["elements"]:
        if el.get("role") != role:
            continue
        for f in el.get("editable") or []:
            if f.get("prop") == "fontsize" and isinstance(f.get("value"), (int, float)):
                out.append(f["value"])
    return out


def _ids(sid: str, journal: dict) -> set[str]:
    out = bridge.run_preflight(sid, journal=journal)
    return {i["id"] for k in ("errors", "warnings") for i in out[k]}


# ------------------------------ 主路径 ---------------------------------------
def test_120mm_figure_becomes_single_column_compliant_with_intact_hierarchy(sid):
    j = _journal()
    b0_hash, _ = _state(sid)
    assert "page-width" in _ids(sid, j)
    res = bridge.fix_to_journal(sid, journal=j)
    previews = res.pop("_previews")
    assert res["ok"] and res["exit"] == "done", json.dumps(res, ensure_ascii=False)[:3000]
    # 图幅：自动取了唯一在 15% 以内的单栏，并写明
    assert res["size"]["from_mm"] == [120.0, 80.0]
    assert res["size"]["column"] == "single" and res["size"]["auto_picked"] is True
    assert res["size"]["to_mm"][0] == pytest.approx(110.0, abs=0.01)
    assert res["issues"]["fixed"] > 0 and res["issues"]["introduced"] == []
    assert res["worker_retired"] is False and res["rolled_back"] is False
    # 会话停在最终列表且真的合规：规范里的 error / warn 一条不剩
    s = bridge.get_session(sid)
    assert s.manifest["size_mm"][0] == pytest.approx(110.0, abs=0.01)
    left = _ids(sid, j)
    assert not left, left
    # 层级：刻度 ≤ 轴标题 ≤ 标题；绝对下限 8 pt
    ticks, labels, titles = (_eff(s.manifest, r) for r in ("ticks", "axis_label", "title"))
    assert ticks and labels and titles
    assert max(ticks) <= min(labels) + 1e-6 <= min(titles) + 2e-6
    assert min(ticks) >= 8.0
    # 改前 / 改后各一张，长边 1000 px；不进 structuredContent
    assert set(previews) == {"before", "after"}
    assert all(_png_long_edge(v) == 1000 for v in previews.values())
    assert "previews" not in res and "_previews" not in res
    assert "iVBOR" not in json.dumps(res)
    # 落盘一次：换进程（摘掉内存会话）后恢复出来的就是最终列表
    final_hash = s.patch_hash()
    assert final_hash != b0_hash
    assert _persisted_hash(sid) == final_hash


def test_second_run_is_nothing_to_do_and_changes_nothing(sid):
    j = _journal()
    _fix(sid, journal=j)
    h, _ = _state(sid)
    again = _fix(sid, journal=j)
    assert again["ok"] and again["exit"] == "nothing_to_do"
    assert again["changes"] == [] and again["size"]["from_mm"] == again["size"]["to_mm"]
    assert _state(sid)[0] == h


def test_only_names_the_issue_and_suggestions_stay_out_unless_asked(sid):
    j = _journal()
    res = _fix(sid, journal=j, only=[{"rule": "legend-frame"}])
    assert res["ok"]
    assert {c["rule"] for c in res["changes"]} == {"legend-frame"}
    assert res["size"]["from_mm"] == res["size"]["to_mm"]  # 没点名 page-width：不动图幅
    s = bridge.get_session(sid)
    assert not any(p["prop"] == "weight" for p in s.patches)  # 建议档（轴标题加粗）没被顺手改
    _fix(sid, journal=j, include_suggestions=True)
    assert any(p["prop"] == "weight" for p in bridge.get_session(sid).patches)


# ------------------------------ 栏位 ----------------------------------------
def test_two_candidate_columns_ask_and_change_nothing(sid):
    j = _journal(double=125.0)  # 120 mm：单栏 110（8.3%）与双栏 125（4.2%）都在 15% 以内
    before = _state(sid)
    manifest = bridge.get_session(sid).manifest
    res = _fix(sid, journal=j)
    assert res["ok"] is False and res["exit"] == "column_choice_required"
    assert {c["id"] for c in res["columns"]} == {"single", "double"}
    assert all("off_percent" in c and c["width_mm"] for c in res["columns"])
    assert _state(sid) == before
    assert bridge.get_session(sid).manifest is manifest  # 连渲染都没有发生
    assert _persisted_hash(sid) == before[0]
    # 点名栏位后照常走
    done = _fix(sid, journal=j, column="double")
    assert (
        done["ok"] and done["size"]["column"] == "double" and done["size"]["auto_picked"] is False
    )
    assert done["size"]["to_mm"][0] == pytest.approx(125.0, abs=0.01)


def test_no_column_within_tolerance_also_asks(sid):
    j = _journal()
    j["widths_mm"] = {"single": 60.0, "double": 183.0, "tolerance_mm": 0.5}
    res = _fix(sid, journal=j)
    assert res["exit"] == "column_choice_required"


def _max_journal(max_mm: float) -> dict:
    """单栏是「最大 `max_mm`」（JACS 式上限）、双栏 183 exact、typical 栏不参与判据。"""
    j = _journal()
    j["page_columns"] = [
        {
            "id": "single",
            "label": {"zh": "单栏", "en": "Single"},
            "width_mm": max_mm,
            "qualifier": "max",
        },
        {"id": "double", "label": {"zh": "双栏", "en": "Double"}, "width_mm": 183.0},
        {
            "id": "wide",
            "label": {"zh": "常见", "en": "Typical"},
            "width_mm": 121.0,
            "qualifier": "typical",
        },
    ]
    j["widths_mm"] = {"single": None, "double": None, "tolerance_mm": 0.5}
    return j


def test_max_column_over_the_limit_is_auto_picked_and_pressed_to_the_limit(sid):
    j = _max_journal(110.0)  # 120 mm 超了单栏上限 110（9%）；typical 121 不当目标
    res = _fix(sid, journal=j)
    assert res["ok"] and res["size"]["column"] == "single" and res["size"]["auto_picked"] is True
    assert res["size"]["to_mm"][0] == pytest.approx(110.0, abs=0.01)
    assert "page-width" not in _ids(sid, j)


def test_max_column_already_within_the_limit_keeps_the_width(sid):
    j = _max_journal(130.0)  # 120 mm 本来就在上限内：page-width 不报，点名这一栏也不放大
    assert "page-width" not in _ids(sid, j)
    res = _fix(sid, journal=j, column="single")
    assert res["ok"] and res["size"]["to_mm"][0] == pytest.approx(120.0, abs=0.01)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.fix_to_journal(sid, journal=j, column="wide")
    assert exc.value.code == "unknown_column"  # typical 栏不是判据，不能当目标


def test_width_mm_and_unknown_column(sid):
    res = _fix(sid, journal=_journal(), width_mm=110)
    assert res["ok"] and res["size"]["to_mm"][0] == pytest.approx(110.0, abs=0.01)
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.fix_to_journal(sid, journal=_journal(), column="triple")
    assert exc.value.code == "unknown_column"
    with pytest.raises(bridge.BridgeError) as exc:
        bridge.fix_to_journal(sid, journal=_journal(), column="single", width_mm=100)
    assert exc.value.code == "bad_arguments"


# ------------------------------ 缺字体 ---------------------------------------
def test_missing_font_is_skipped_with_the_wanted_name_and_the_rest_is_fixed(sid):
    j = _journal(MISSING_FONT)
    res = _fix(sid, journal=j)
    assert res["ok"] and res["exit"] == "done"
    fonts = [s for s in res["skipped"] if s["reason"] == "font_unavailable"]
    assert fonts and all(s["want"] == MISSING_FONT for s in fonts)
    s = bridge.get_session(sid)
    assert not any(p["prop"] == "fontfamily" for p in s.patches)
    assert "legend-frame" not in _ids(sid, j)


# ------------------------------ 整体回到 B0 -----------------------------------
def _fake_transaction(extra_patches=None, *, ok=True):
    """`specfix.transaction` 的替身：真去渲染一份带 `extra_patches` 的候选，再按 `ok` 回结果。"""

    def fake(render, base, scale, profile, only):
        manifest = render(base)["manifest"]
        ticks = [e["gid"] for e in manifest["elements"] if e.get("role") == "ticks"]
        extra = extra_patches(ticks) if callable(extra_patches) else (extra_patches or [])
        cand = list(base) + extra
        resp = render(cand)
        out = {
            "ok": ok,
            "exit": "done" if ok else "unsupported",
            "patches": cand if ok else list(base),
            "changes": [],
            "skipped": [],
            "adjustments": [],
            "unresolved": [],
            "blocking": [],
        }
        if not ok:
            out["warnings"] = list(resp.get("warnings") or [])
        return out

    return fake


def test_introduced_warn_rolls_the_whole_thing_back_to_b0(sid, monkeypatch):
    """事务（被替换成只会把刻度「修」到 40 pt 的）通过了，复检看到新增的 font-too-large——整体回到 B0。"""
    j = _journal()
    before = _state(sid)
    monkeypatch.setattr(
        specfix,
        "transaction",
        _fake_transaction(
            lambda ticks: [{"gid": g, "prop": "fontsize", "value": 40} for g in ticks]
        ),
    )
    res = _fix(sid, journal=j)
    assert res["ok"] is False and res["exit"] == "constraint_conflict"
    assert any(i["id"] == "font-too-large" for i in res["issues"]["introduced"])
    assert res["rolled_back"] is True
    assert _state(sid) == before
    assert bridge.get_session(sid).manifest["size_mm"][0] == 120.0  # 第 1 段改的图幅也回去了
    assert _persisted_hash(sid) == before[0]


def test_blocked_export_rolls_back_and_writes_nothing(sid, tmp_path):
    # 剩下的 palette-line-markers 是脚本问题（manual）：把它升成 error，导出的预检门就会挡
    j = _journal(severity={"palette-line-markers": "error"})
    before = _state(sid)
    out_dir = tmp_path / "out"
    res = _fix(sid, journal=j, formats=["pdf"], out_dir=str(out_dir))
    assert res["ok"] is False and res["exit"] == "acceptance_failed"
    assert res["reason"] == "export_blocked" and res["export_error"]["code"] == "preflight_blocked"
    assert res["exported"] == [] and res["rolled_back"] is True
    assert not out_dir.exists() or not list(out_dir.iterdir())
    assert _state(sid) == before
    assert _persisted_hash(sid) == before[0]


def test_rejected_artifact_withdraws_published_files_and_rolls_back(sid, tmp_path, monkeypatch):
    j = _journal()
    before = _state(sid)
    leftover = tmp_path / "published.pdf"
    leftover.write_bytes(b"%PDF-1.4 fake")
    proof = tmp_path / "published.proof.json"
    proof.write_text("{}", encoding="utf-8")

    def fake_export(session_id, **kwargs):
        assert kwargs["explicit_confirm"] is False  # 与 tavotto_export 同一条预检门，不绕
        return {
            "files": [
                {"status": "done", "format": "pdf", "path": str(leftover)},
                {"status": "partial", "format": "png", "error": {"code": "artifact_rejected"}},
            ],
            "export_dir": str(tmp_path),
            "proof_path": str(proof),
        }

    monkeypatch.setattr(bridge, "export", fake_export)
    res = _fix(sid, journal=j, formats=["pdf", "png"])
    assert res["ok"] is False and res["exit"] == "acceptance_failed"
    assert res["reason"] == "artifact_rejected" and res["failed_files"][0]["format"] == "png"
    assert not leftover.exists()  # 同一作业里已发布的其它格式也撤回
    assert not proof.exists()  # 留档一并撤回
    assert _state(sid) == before


def test_successful_export_goes_through_the_real_gate_and_checks_the_page_size(sid, tmp_path):
    j = _journal()
    out_dir = tmp_path / "out"
    res = _fix(sid, journal=j, formats=["pdf"], out_dir=str(out_dir), previews=False)
    assert res["ok"] and res["exit"] == "done", json.dumps(res, ensure_ascii=False)[:3000]
    (entry,) = res["exported"]
    probe = pdfbackend.probe_asset(Path(entry["path"]), "pdf")
    assert probe["w_pt"] == pytest.approx(110.0 / 25.4 * 72, abs=0.5)
    assert entry["inspection"]  # 产物核验的逐项结果原样带出
    assert _persisted_hash(sid) == bridge.get_session(sid).patch_hash()


def test_export_out_of_scope_is_refused_before_anything_changes(sid, tmp_path):
    before = _state(sid)
    with pytest.raises(bridge.BridgeError):
        bridge.fix_to_journal(sid, journal=_journal(), formats=["pdf"], out_dir="/etc")
    assert _state(sid) == before


def test_dry_run_is_the_same_diff_as_the_real_run_and_leaves_the_session_at_b0(sid):
    j = _journal()
    before = _state(sid)
    dry = bridge.fix_to_journal(sid, journal=j, dry_run=True)
    assert dry.pop("_previews") and dry["ok"] and dry["dry_run"] is True
    assert _state(sid) == before and dry["rolled_back"] is True
    assert bridge.get_session(sid).manifest["size_mm"][0] == 120.0
    assert _persisted_hash(sid) == before[0]
    real = _fix(sid, journal=j)
    for key in ("changes", "size", "adjustments", "skipped", "patch_hash"):
        assert dry[key] == real[key], key
    assert dry["issues"] == real["issues"]
    assert real["dry_run"] is False and real["rolled_back"] is False


def test_dry_run_does_not_export(sid, tmp_path):
    out_dir = tmp_path / "out"
    res = _fix(sid, journal=_journal(), formats=["pdf"], out_dir=str(out_dir), dry_run=True)
    assert res["ok"] and res["exported"] == []
    assert not out_dir.exists()


def test_a_render_with_warnings_retires_the_worker_and_the_session_stays_usable(sid, monkeypatch):
    j = _journal()
    before = _state(sid)
    old_worker = bridge.get_session(sid).acquire()
    retired: list[tuple] = []
    real_invalidate = engine_pool.invalidate

    def spy(*a, **k):
        retired.append(a)
        return real_invalidate(*a, **k)

    monkeypatch.setattr(engine_pool, "invalidate", spy)
    monkeypatch.setattr(
        specfix,
        "transaction",
        _fake_transaction([{"gid": "no.such.gid", "prop": "fontsize", "value": 9}], ok=False),
    )
    res = _fix(sid, journal=j)
    assert res["ok"] is False and res["exit"] == "unsupported"
    assert res["worker_retired"] is True and retired
    assert _state(sid) == before and res["rolled_back"] is True
    # 新 worker 上按全量列表重放过了：会话还能用，且不是原来那条 worker
    after = bridge.apply_overrides(sid, [])
    assert bridge.get_session(sid).acquire() is not old_worker
    assert after["warnings"] == [] and after["manifest"]["size_mm"][0] == 120.0


def test_exception_in_the_transaction_restores_b0_and_reraises(sid, monkeypatch):
    before = _state(sid)

    def boom(render, base, scale, profile, only):
        render(list(base) + [{"gid": "figure", "prop": "facecolor", "value": "#eeeeee"}])
        raise RuntimeError("事务里炸了")

    monkeypatch.setattr(specfix, "transaction", boom)
    with pytest.raises(RuntimeError):
        bridge.fix_to_journal(sid, journal=_journal())
    assert _state(sid) == before
    assert bridge.apply_overrides(sid, [])["manifest"]["size_mm"][0] == 120.0


def test_a_previous_contract_is_released_only_on_commit(sid, monkeypatch):
    s = bridge.get_session(sid)
    s.contract = {"contract_id": "c1", "allowed": [], "allowed_adjust": []}
    s.normalized = {"contract_id": "c1", "patch_hash": s.patch_hash(), "stale": False}
    with monkeypatch.context() as mp:  # 没提交：合同与验收原样留着
        mp.setattr(
            specfix,
            "transaction",
            _fake_transaction(
                lambda ticks: [{"gid": g, "prop": "fontsize", "value": 40} for g in ticks]
            ),
        )
        assert _fix(sid, journal=_journal())["ok"] is False
    assert s.contract is not None and s.normalized is not None
    done = _fix(sid, journal=_journal())  # 提交了：旧合同描述的是另一个状态，解除
    assert done["ok"] and done["contract_released"] is True
    assert s.contract is None and s.normalized is None


# ------------------------------ 工具层 ---------------------------------------
def _call(name: str, args: dict) -> dict:
    return server.call_tool(name, args)


def test_tool_is_listed_only_with_the_journal_switch_and_refuses_when_off(sid, monkeypatch):
    names = [t["name"] for t in server._tools()]
    assert "tavotto_fix_to_journal" in names
    schema = next(t for t in server._tools() if t["name"] == "tavotto_fix_to_journal")[
        "inputSchema"
    ]
    assert schema["additionalProperties"] is False and schema["required"] == ["session_id"]
    assert {"column", "width_mm", "only", "include_suggestions", "previews", "dry_run"} <= set(
        schema["properties"]
    )
    assert "tavotto_fix_to_journal" in server.HANDLERS
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", False)
    assert "tavotto_fix_to_journal" not in [t["name"] for t in server._tools()]
    res = _call("tavotto_fix_to_journal", {"session_id": sid})
    assert res["isError"] is True and res["structuredContent"]["code"] == "journal_catalog_disabled"


def test_tool_result_carries_two_image_blocks_outside_structured_content(sid):
    res = _call("tavotto_fix_to_journal", {"session_id": sid, "journal": _journal()})
    assert not res.get("isError"), res
    images = [c for c in res["content"] if c["type"] == "image"]
    assert [c["mimeType"] for c in images] == ["image/png", "image/png"]
    assert all(_png_long_edge(c["data"]) == 1000 for c in images)
    body = res["structuredContent"]
    assert body["ok"] and "_previews" not in body
    assert len(json.dumps(body, ensure_ascii=False).encode()) < 16 * 1024
    off = _call("tavotto_fix_to_journal", {"session_id": sid, "previews": False})
    assert not [c for c in off["content"] if c["type"] == "image"]


def test_tool_result_asks_the_column_question_in_words(sid):
    res = _call("tavotto_fix_to_journal", {"session_id": sid, "journal": _journal(double=125.0)})
    assert res["structuredContent"]["exit"] == "column_choice_required"
    text = res["content"][0]["text"]
    assert "single" in text and "double" in text and "column" in text


# ------------------------------ preflight 的 fix ------------------------------
def test_preflight_tool_adds_fix_to_every_issue(sid):
    j = _journal()
    res = _call("tavotto_preflight", {"session_id": sid, "journal": j})
    body = res["structuredContent"]
    issues = [i for k in ("errors", "warnings", "not_verifiable", "suggestions") for i in body[k]]
    assert issues and all("fix" in i for i in issues)
    by_id = {i["id"]: i["fix"] for i in issues}
    assert by_id["page-width"]["kind"] == "size"
    assert {c["id"] for c in by_id["page-width"]["columns"]} == {"single", "double"}
    assert by_id["legend-frame"] == {"kind": "auto"}
    assert (
        by_id["palette-line-markers"]["kind"] == "manual" and by_id["palette-line-markers"]["hint"]
    )
    # 同源：`auto` 的 id 一定在 specfix.FIXABLE_RULES 里（反向由下一条用例按结构读）
    assert {i["id"] for i in issues if i["fix"]["kind"] == "auto"} <= set(specfix.FIXABLE_RULES)


def test_preflight_fix_does_not_leak_into_the_session_cache_or_the_engine_output(sid):
    bridge.run_preflight(sid)  # 默认参数那一档缓存到会话上
    cached = bridge.get_session(sid).preflight_cache
    assert cached is not None
    assert not any("fix" in i for k in ("errors", "warnings", "suggestions") for i in cached[k])
    shaped = bridge.annotate_preflight_fix(cached)
    assert shaped is not cached and any("fix" in i for i in shaped["errors"] + shaped["warnings"])
    assert not any("fix" in i for k in ("errors", "warnings", "suggestions") for i in cached[k])
    # `preflight.run()` 本身不带 fix
    s = bridge.get_session(sid)
    spec = preflight.spec_from_manifest(s.manifest, panel_id=s.stem, kind="pdf", scale=1.0)
    assert not any("fix" in i for i in preflight.run(spec, s.profile))


def test_preflight_has_no_fix_while_the_journal_switch_is_off(sid, monkeypatch):
    monkeypatch.setattr(journals, "JOURNAL_CATALOG_AGENTS", False)
    body = _call("tavotto_preflight", {"session_id": sid})["structuredContent"]
    issues = [i for k in ("errors", "warnings", "not_verifiable", "suggestions") for i in body[k]]
    assert issues and not any("fix" in i for i in issues)


def test_fix_kind_auto_is_exactly_specfix_fixable_rules_read_structurally():
    """同源：每个检查 id 过一遍分类，`auto` 的集合 == `specfix.FIXABLE_RULES`（不在测试里抄第二份）。"""
    all_ids = set(profiles.load("journal-base-v1")["severity"])  # 期刊底补齐了全部检查 id
    assert set(specfix.FIXABLE_RULES) <= all_ids
    auto = {i for i in all_ids if bridge.issue_fix({"id": i, "severity": "warn"})["kind"] == "auto"}
    assert auto == set(specfix.FIXABLE_RULES)
    size = {i for i in all_ids if bridge.issue_fix({"id": i, "severity": "warn"})["kind"] == "size"}
    assert size == {"page-width", "page-aspect"} == set(bridge.FIX_SIZE_RULES)
    assert bridge.issue_fix({"id": "legend-frame", "severity": "not_verifiable"}) == {
        "kind": "verify"
    }
    manual = bridge.issue_fix({"id": "palette-semantic", "severity": "warn"})
    assert manual["kind"] == "manual" and manual["hint"]
    # 分类只靠引擎这一份清单：清单里去掉一条，分类就跟着变
    assert all(
        bridge.issue_fix({"id": r, "severity": "warn"})["kind"] != "manual"
        for r in specfix.FIXABLE_RULES
    )
