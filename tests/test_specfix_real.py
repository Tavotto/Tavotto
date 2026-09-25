"""按规范修图（`/api/engine/specfix`，ADR 0080）的**真链路**：真 matplotlib、真 worker。

`tests/test_specfix.py` 在合成 manifest 上盯计划与裁决的逻辑；这里盯的是用户点
「全部处理」那一刻真正会发生的事：

* 修完**真的**过了——对修改后的真实渲染再跑一遍预检，点名的问题一条不剩；
* 没有「越修越乱」：字号层级不倒挂、轴标题不被顺手加粗（建议档不进批量）、
  没有新增 / 加重的问题；
* 规范要的字体这台机器上没有时，字体那几条如实退出，其余照修，不假装换了；
* 裁决不过时 worker 回到 B0，回给前端的仍是原来那份列表（文档零改动）。

事务本体 `app._specfix_transaction()` 接一个 `render(patches) -> 响应` 的回调，
这里把它接到一个直连的 worker 子进程上——与端点里 `worker.override()` 同一条
协议命令。缺带科学栈的解释器就跳过（.venv 里没有 matplotlib 是常态）。
"""

from __future__ import annotations

import ast
import json
import subprocess
import threading

import pytest

from tavotto import app as m
from tavotto.engine import (
    nativesession,
    pool as engine_pool,
    preflight,
    profiles,
    runcodes,
    specfix,
)
from tavotto.engine.runcodes import RunError


def _worker_python():
    try:
        return engine_pool.find_worker_python()
    except engine_pool.WorkerError:
        return None


WORKER_PY = _worker_python()
pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有带科学栈的解释器，跳过真链路用例")

#: matplotlib 自带的字体：「装了的」那一个，任何机器上都有
AVAILABLE_FONT = "DejaVu Serif"
MISSING_FONT = "Tavotto Nonexistent Serif 9x"

#: 故意不合规的图：刻度 / 图例 7 pt（低于 8 pt 绝对下限）、轴标题 8.2 pt（**合规**，
#: 所以不会被点名——刻度被抬到 8.5 之后它比刻度还小，只有「按层级补齐」能把它
#: 带上来）、标题 9 pt；曲线 1.2 pt、边框 0.6 pt 不在档位上；刻度朝外、
#: 缺上右两边、图例带框；字体是 DejaVu Sans（规范的替代品名单里）。
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
    fig, ax = plt.subplots(figsize=(80 / 25.4, 60 / 25.4))
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


def _rpc(proc, obj, timeout=180):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()
    box: list = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
    reader.start()
    reader.join(timeout)
    assert not reader.is_alive(), f"worker 超时: {obj.get('cmd')}"
    line = box[0] if box else ""
    assert line, f"worker 无响应: {obj.get('cmd')}\n{proc.stderr.read()}"
    resp = json.loads(line)
    assert resp.get("ok"), f"{resp.get('error', resp)}\n{resp.get('traceback', '')}"
    return resp


def _spawn_render(tmp_path, script: str, stem: str):
    """直连 worker 的 `render(patches)`，外加一本调用账（最后一次渲染的是哪份列表）。"""
    figs = tmp_path / "figures"
    figs.mkdir()
    (figs / "fig.py").write_text(script, encoding="utf-8")
    proc = subprocess.Popen(
        [
            WORKER_PY,
            str(engine_pool.WORKER_PY),
            "--script",
            str(figs / "fig.py"),
            "--figures-dir",
            str(figs),
            "--out-dir",
            str(tmp_path / "out"),
            "--sandbox",
            str(tmp_path / "sandbox"),
            "--entry",
            "main",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    _rpc(proc, {"cmd": "build"})
    calls: list[list] = []

    def _render(patches: list) -> dict:
        calls.append(list(patches))
        return _rpc(proc, {"cmd": "override", "stem": stem, "patches": patches})

    _render.calls = calls  # type: ignore[attr-defined]
    return proc, _render


@pytest.fixture
def render(tmp_path):
    proc, fn = _spawn_render(tmp_path, SCRIPT, "Kin")
    yield fn
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _profile(latin: str = AVAILABLE_FONT) -> dict:
    p = profiles.load()
    fam = dict(p["font_family"])
    fam["latin"] = latin
    fam["latin_accepted"] = [latin]
    p["font_family"] = fam
    return p


def _ids(manifest: dict, profile: dict, scale: float) -> set[str]:
    spec = preflight.spec_from_manifest(manifest, scale=scale)
    return {i["id"] for i in preflight.run(spec, profile)}


def _eff(manifest: dict, role: str, scale: float) -> list[float]:
    out = []
    for el in manifest["elements"]:
        if el.get("role") != role:
            continue
        for f in el.get("editable") or []:
            if f.get("prop") == "fontsize" and isinstance(f.get("value"), (int, float)):
                out.append(f["value"] * scale)
    return out


def _weight(manifest: dict, role: str) -> set:
    return {
        f.get("value")
        for el in manifest["elements"]
        if el.get("role") == role
        for f in el.get("editable") or []
        if f.get("prop") == "weight"
    }


@pytest.mark.parametrize("scale", [1.0, 0.6])
def test_fix_all_really_passes_and_keeps_the_hierarchy(render, scale):
    profile = _profile()
    before = render([])["manifest"]
    assert {"font-below-absolute-floor", "legend-frame", "tick-direction"} <= _ids(
        before, profile, scale
    )
    res = m._specfix_transaction(render, [], scale, profile, None)
    assert res["ok"], res
    after = render(res["patches"])["manifest"]
    left = _ids(after, profile, scale)
    fixable_non_suggestion = {
        r for r in specfix.FIXABLE_RULES if profiles.severity_of(profile, r) != "suggestion"
    }
    assert not (left & fixable_non_suggestion), left
    # 层级：刻度 ≤ 轴标题 ≤ 标题（页面上量到的 pt）
    ticks, labels, titles = (_eff(after, r, scale) for r in ("ticks", "axis_label", "title"))
    assert ticks and labels and titles
    assert max(ticks) <= min(labels) + 1e-6 <= min(titles) + 2e-6
    assert min(ticks) > profile["absolute_min_font_size_pt"]
    # 建议档不进「全部处理」：轴标题没被顺手加粗
    assert _weight(after, "axis_label") == {"normal"}
    # 字体真的换了（刻度也换了，不止标题）
    fams = {
        f.get("value")
        for el in after["elements"]
        for f in el.get("editable") or []
        if f.get("prop") == "fontfamily"
    }
    assert fams == {AVAILABLE_FONT}


def test_missing_font_is_reported_and_the_rest_still_fixed(render):
    profile = _profile(MISSING_FONT)
    res = m._specfix_transaction(render, [], 1.0, profile, None)
    assert res["ok"], res
    assert any(s["reason"] == "font_unavailable" for s in res["skipped"])
    assert not any(p["prop"] == "fontfamily" for p in res["patches"])
    after = render(res["patches"])["manifest"]
    assert "legend-frame" not in _ids(after, profile, 1.0)


def test_only_fixes_the_named_issue(render):
    profile = _profile()
    before = render([])["manifest"]
    legend = next(el["gid"] for el in before["elements"] if el.get("role") == "legend")
    res = m._specfix_transaction(
        render, [], 1.0, profile, [{"rule": "legend-frame", "gid": legend}]
    )
    assert res["ok"], res
    assert res["patches"] == [{"gid": legend, "prop": "frameon", "value": False}]


def test_suggestion_is_fixable_when_named(render):
    profile = _profile()
    res = m._specfix_transaction(
        render, [], 1.0, profile, [{"rule": "text-weight-policy", "gid": ""}]
    )
    assert res["ok"], res
    after = render(res["patches"])["manifest"]
    assert _weight(after, "axis_label") == {"bold"}


def test_rejected_fix_restores_b0_and_returns_the_original_list(render, monkeypatch):
    """计划被裁决挡住：回给前端的是原列表，worker 最后一次渲染的也是原列表。"""
    profile = _profile()
    base = [{"gid": "figure", "prop": "facecolor", "value": "#ffffff"}]
    real_plan = specfix.plan

    def bad_plan(manifest, prof, *, scale, targets):
        out = real_plan(manifest, prof, scale=scale, targets=targets)
        # 把刻度字号「修」到 40 pt：字号偏大是新增的 warn——越修越乱，必须挡
        ticks = [el["gid"] for el in manifest["elements"] if el.get("role") == "ticks"]
        out["patches"] = [p for p in out["patches"] if p["prop"] != "fontsize"] + [
            {"gid": g, "prop": "fontsize", "value": 40} for g in ticks
        ]
        return out

    monkeypatch.setattr(specfix, "plan", bad_plan)
    res = m._specfix_transaction(render, base, 1.0, profile, None)
    assert not res["ok"]
    assert res["patches"] == base
    assert render.calls[-1] == base
    assert any(b["id"] == "font-too-large" for b in res["blocking"])


def test_nothing_to_do_on_a_compliant_figure(render):
    profile = _profile()
    first = m._specfix_transaction(render, [], 1.0, profile, None)
    assert first["ok"]
    again = m._specfix_transaction(render, first["patches"], 1.0, profile, None)
    assert again["ok"]
    assert again["exit"] == specfix.EXIT_NOTHING_TO_DO
    assert again["patches"] == first["patches"]


def test_endpoint_validates_input():
    m.app.config["TESTING"] = True
    client = m.app.test_client()
    bad = client.post("/api/engine/specfix", json={"id": "x.pdf", "patches": [], "scale": 0})
    assert bad.status_code == 400 and bad.get_json()["code"] == "invalid_scale"
    bad = client.post("/api/engine/specfix", json={"id": "x.pdf", "patches": [], "scale": 1})
    assert bad.status_code == 400 and bad.get_json()["code"] == "invalid_profile"
    bad = client.post("/api/engine/specfix", json={"id": "x.pdf", "patches": {}, "scale": 1})
    assert bad.get_json()["code"] == "invalid_patches"


def test_named_but_not_fixable_is_reported_not_counted_as_fixed(render):
    """点名了一条这里不修的规则 / B0 上不存在的问题：逐条回 skipped，不假装修好。"""
    profile = _profile()
    res = m._specfix_transaction(
        render,
        [],
        1.0,
        profile,
        [
            {"rule": "axis-label-format", "gid": "axes_0.xlabel"},
            {"rule": "legend-frame", "gid": "axes_0.no_such_legend"},
        ],
    )
    assert not res["ok"]
    assert {(s["rule"], s["reason"]) for s in res["skipped"]} == {
        ("axis-label-format", "not_found"),
        ("legend-frame", "not_found"),
    }
    assert res["patches"] == []


#: 修复前就已经出界的图：底边距压得太窄，x 轴标题探出图幅底边；另外图例带框。
CLIPPED = """\
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent


def main():
    plt.rcParams["font.family"] = "DejaVu Serif"
    x = np.linspace(0, 10, 20)
    fig, ax = plt.subplots(figsize=(80 / 25.4, 60 / 25.4))
    ax.plot(x, x ** 0.5, lw=1.0, label="a")
    ax.set_xlabel("Time (s)", fontsize=9, labelpad=LABELPAD)
    ax.set_ylabel("Signal (a.u.)", fontsize=9)
    ax.tick_params(labelsize=8.5, direction="in")
    ax.legend(fontsize=8.5, frameon=True)
    fig.subplots_adjust(left=0.2, right=0.95, top=0.95, bottom=0.08)
    fig.savefig(OUT / "Clip.pdf")


if __name__ == "__main__":
    main()
"""


@pytest.fixture
def clipped(tmp_path, request):
    proc, fn = _spawn_render(tmp_path, CLIPPED.replace("LABELPAD", str(request.param)), "Clip")
    yield fn
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def _clip_gids(manifest: dict) -> set[str]:
    from tavotto.engine import normalize

    return {c["gids"][0] for c in normalize.per_element_clipping(manifest)}


@pytest.mark.parametrize("clipped", [4], indirect=True)
def test_preexisting_clipping_is_fixed_by_moving_the_axes_not_the_text(clipped):
    """修复前就出界的轴标题：外边距重排把子图挪上来，文字本身一个属性不动。"""
    profile = _profile()
    before = clipped([])["manifest"]
    assert "axes_0.xlabel" in _clip_gids(before)
    res = m._specfix_transaction(clipped, [], 1.0, profile, None)
    assert res["ok"], res
    assert res["adjustments"], res
    assert {p["prop"] for p in res["patches"]} <= {"position", "frameon"}
    after = clipped(res["patches"])["manifest"]
    assert not _clip_gids(after)


@pytest.mark.parametrize("clipped", [70], indirect=True)
def test_clipping_that_cannot_fit_is_reported_and_the_rest_still_lands(clipped):
    """labelpad 70 pt 的轴标题在 60 mm 高的图里怎么挪都放不下：如实报 no_fit，
    图例边框照修，不因为这一条把整批都退回去。"""
    profile = _profile()
    res = m._specfix_transaction(clipped, [], 1.0, profile, None)
    assert res["ok"], res
    assert ("element-outside-figure", "no_fit") in {
        (s["rule"], s["reason"]) for s in res["skipped"]
    }
    assert any(p["prop"] == "frameon" for p in res["patches"])


#: 同一张图里混着两种字体：标题是替代品名单里的 DejaVu Sans（要修），其余文字是
#: 规范**也接受**的 DejaVu Sans Mono（不该动，也不该被拿去和目标字体比脸）。
MIXED_FONTS = """\
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent


def main():
    plt.rcParams["font.family"] = "DejaVu Sans Mono"
    x = np.linspace(0, 10, 20)
    fig, ax = plt.subplots(figsize=(80 / 25.4, 60 / 25.4))
    ax.plot(x, x ** 0.5, lw=1.0)
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Signal (a.u.)", fontsize=9)
    ax.set_title("Kinetics", fontsize=9, fontfamily="DejaVu Sans")
    ax.tick_params(labelsize=8.5, direction="in")
    fig.tight_layout(pad=0.8)
    fig.savefig(OUT / "Mixed.pdf")


if __name__ == "__main__":
    main()
"""


@pytest.fixture
def mixed(tmp_path):
    proc, fn = _spawn_render(tmp_path, MIXED_FONTS, "Mixed")
    yield fn
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


def test_font_verification_covers_only_the_elements_it_changed(mixed):
    """Codex #549 P1：没动过的、本来就合规的另一种字体不许拿去和目标字体比脸。

    修复前：允许集合按 prop 放行全图，`compare()` 于是把每个带 fontfamily 的元素都按
    目标字体核验，DejaVu Sans Mono 的刻度被报成「没落成 DejaVu Serif」，事务退掉字体
    那几条，并告诉用户「字体没装」——而标题其实已经换好了。
    """
    profile = _profile()
    profile["font_family"]["latin_accepted"] = [AVAILABLE_FONT, "DejaVu Sans Mono"]
    res = m._specfix_transaction(mixed, [], 1.0, profile, None)
    assert res["ok"], res
    assert not any(s["reason"] == "font_unavailable" for s in res["skipped"]), res["skipped"]
    fam = {(p["gid"], p["value"]) for p in res["patches"] if p["prop"] == "fontfamily"}
    assert fam == {("axes_0.title", AVAILABLE_FONT)}


@pytest.mark.parametrize("clipped", [4], indirect=True)
def test_margin_repair_round_with_warnings_is_not_accepted(clipped):
    """Codex #549 P1：外边距重排那一轮的渲染带 warning（有 override 没写进去）时不收这一轮。"""
    profile = _profile()

    def render(patches):
        resp = clipped(patches)
        if any(p["prop"] == "position" for p in patches):
            resp = {**resp, "warnings": ["position 没写进去（模拟）"]}
        return resp

    res = m._specfix_transaction(render, [], 1.0, profile, None)
    assert not any(p["prop"] == "position" for p in res["patches"]), res["patches"]
    # 这一轮没收，出界如实记成放不下；图例边框那条照修
    assert ("element-outside-figure", "no_fit") in {
        (s["rule"], s["reason"]) for s in res["skipped"]
    }
    assert any(p["prop"] == "frameon" for p in res["patches"])
    # worker 最后停在没有 warning 的那一版上
    assert not any(p["prop"] == "position" for p in clipped.calls[-1])


def test_preexisting_font_override_is_not_verified_against_the_target(mixed):
    """Codex #549 第二轮：B0 里用户自己加的 fontfamily override（另一种合规字体）原样
    带着，不算这次的改动，不许拿去和目标字体比脸、误报「字体没装」。"""
    profile = _profile()
    profile["font_family"]["latin_accepted"] = [AVAILABLE_FONT, "DejaVu Sans Mono"]
    base = [{"gid": "axes_0.xlabel", "prop": "fontfamily", "value": "DejaVu Sans Mono"}]
    res = m._specfix_transaction(mixed, base, 1.0, profile, None)
    assert res["ok"], res
    assert not any(s["reason"] == "font_unavailable" for s in res["skipped"]), res["skipped"]
    assert {"gid": "axes_0.xlabel", "prop": "fontfamily", "value": "DejaVu Sans Mono"} in res[
        "patches"
    ]


def test_baseline_replay_with_warnings_aborts_before_any_candidate(render):
    """Codex #549 第三轮 P1：B0 那一遍重放带 warning（基准本身没重放完整）时直接退出，
    不拿被污染的基准去验候选；回给前端的是原列表，后面一次候选渲染都不发。"""
    profile = _profile()
    base = [{"gid": "figure", "prop": "facecolor", "value": "#ffffff"}]
    calls = []

    def warn_on_base(patches):
        calls.append(list(patches))
        resp = render(patches)
        if patches == base:
            resp = {**resp, "warnings": ["上一份 override 恢复不回来（模拟）"]}
        return resp

    res = m._specfix_transaction(warn_on_base, base, 1.0, profile, None)
    assert not res["ok"]
    assert res["exit"] == "unsupported"
    assert res["patches"] == base
    assert calls == [base]


# ---- 只有「一次干净、明确的拒绝」才算回滚成功（Codex #549 第七轮 P1） ----
#
# 回滚渲染（或事务里任何一次渲染）带 warning / 抛异常时，热态不是它声称的那一份，而文档
# 停在 B0。safe worker 一律作废（`pool.invalidate`，与「重新构建」同一个原语），下一次
# 请求重新起、按全量列表重放；native 会话不杀（第八轮：引擎把还原失败的键留在账上、下一次
# 渲染重试）。两档响应都带 `replay_required: true`，前端据此按此刻的列表重放一次。下面几条走真端点，worker 换成转给真 worker 的替身，好在
# 指定的那一次渲染上注入 warning / 异常。


def _post_specfix(
    monkeypatch, override, base, profile, *, native=False, rel_id="Kin.pdf", engine_worker=None
):
    """走 `/api/engine/specfix`，渲染交给 `override(patches)`；回 (响应, 作废记录)。

    `native=True` 时替身是一条真的 `NativeSession`（不连 socket，`override` 转给真 worker）
    ——端点按类型分辨 native（`enginesession.is_native`），鸭子替身量不到那一档。
    """

    class _Worker:
        script_name = "fig.py"
        figures_dir = "/specfix-test-figures"

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            return override(patches)

    class _Native(nativesession.NativeSession):
        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            return override(patches)

    worker = (
        _Native(session_id="native-specfix", descriptor={"native_id": "n" * 32, "out_dir": ""})
        if native
        else _Worker()
    )
    retired: list[tuple] = []
    # 替的是解析本体，不是 `_engine_worker`：`safe_only` 那道闸要走真实现
    monkeypatch.setattr(
        m, "_resolve_engine_worker", engine_worker or (lambda rel_id: (worker, "Kin"))
    )
    monkeypatch.setattr(
        m.engine_pool, "invalidate", lambda script, root=None: retired.append((script, root))
    )
    m.app.config["TESTING"] = True
    resp = m.app.test_client().post(
        "/api/engine/specfix",
        json={"id": rel_id, "patches": base, "scale": 1.0, "profile": profile},
    )
    return resp, retired


def _reject_every_candidate(monkeypatch):
    """把刻度字号「修」到 40 pt：新增 warn，裁决必挡——事务一定走回滚那条路。"""
    real_plan = specfix.plan

    def bad_plan(manifest, prof, *, scale, targets):
        out = real_plan(manifest, prof, scale=scale, targets=targets)
        ticks = [el["gid"] for el in manifest["elements"] if el.get("role") == "ticks"]
        out["patches"] = [p for p in out["patches"] if p["prop"] != "fontsize"] + [
            {"gid": g, "prop": "fontsize", "value": 40} for g in ticks
        ]
        return out

    monkeypatch.setattr(specfix, "plan", bad_plan)


BASE = [{"gid": "figure", "prop": "facecolor", "value": "#ffffff"}]


def test_clean_rejection_keeps_the_worker(render, monkeypatch):
    """对照组：拒绝得干干净净（每次渲染都没有 warning）——worker 回到 B0，照常复用。"""
    _reject_every_candidate(monkeypatch)
    resp, retired = _post_specfix(monkeypatch, render, BASE, _profile())
    body = resp.get_json()
    assert resp.status_code == 200 and not body["ok"]
    assert body["patches"] == BASE and render.calls[-1] == BASE
    assert body["worker_retired"] is False
    assert body["replay_required"] is False
    assert retired == []


def test_rollback_with_warnings_retires_the_worker(render, monkeypatch):
    """Codex #549 第七轮 P1：候选被拒后回滚到 B0 的那次渲染带 warning——worker 作废。"""
    _reject_every_candidate(monkeypatch)
    seen: list[list] = []

    def warn_on_rollback(patches):
        seen.append(list(patches))
        resp = render(patches)
        # 第一次是 B0、第二次是候选，之后回到 B0 的那一次就是回滚
        if len(seen) > 2 and patches == BASE:
            resp = {**resp, "warnings": ["还原失败 axes_0.xticks.fontsize（模拟）"]}
        return resp

    resp, retired = _post_specfix(monkeypatch, warn_on_rollback, BASE, _profile())
    body = resp.get_json()
    assert len(seen) >= 3 and seen[-1] == BASE, seen
    assert resp.status_code == 200 and not body["ok"]
    assert body["patches"] == BASE
    assert body["worker_retired"] is True
    assert body["replay_required"] is True
    assert retired == [("fig.py", "/specfix-test-figures")]


def test_a_native_figure_is_refused_before_any_render(monkeypatch):
    """native 图（`tavotto run` 的 live Figure）暂不支持自动修复（ADR 0080）：**一次渲染都不做**，
    那张图一个字节都不碰。那是用户自己的进程，事务的回滚兜底（作废 worker）对它不成立；
    完整保证在叠栈的新 PR 里做。"""
    calls: list[list] = []

    def never(patches):
        calls.append(list(patches))
        raise AssertionError("native 图被渲染了")

    resp, retired = _post_specfix(monkeypatch, never, BASE, _profile(), native=True)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "specfix_native_unsupported"
    assert body["params"] == {"product": "Tavotto"}
    assert calls == [] and retired == []


def test_rollback_that_raises_retires_the_worker(render, monkeypatch):
    """回滚那次渲染自己抛了：热态未知，同样作废，再照常回 500。"""
    _reject_every_candidate(monkeypatch)
    seen: list[list] = []

    def die_on_rollback(patches):
        seen.append(list(patches))
        if len(seen) > 2:
            raise engine_pool.WorkerError("回滚半路死了（模拟）")
        return render(patches)

    resp, retired = _post_specfix(monkeypatch, die_on_rollback, BASE, _profile())
    assert resp.status_code == 500
    assert retired == [("fig.py", "/specfix-test-figures")]


def test_baseline_with_warnings_retires_the_worker(render, monkeypatch):
    """B0 那一遍重放就带 warning：热 worker 早已表示不了 B0，退出之外还要作废它。"""
    seen: list[list] = []

    def warn_on_base(patches):
        seen.append(list(patches))
        return {**render(patches), "warnings": ["上一份 override 恢复不回来（模拟）"]}

    resp, retired = _post_specfix(monkeypatch, warn_on_base, BASE, _profile())
    body = resp.get_json()
    assert seen == [BASE]
    assert not body["ok"] and body["exit"] == "unsupported"
    assert body["worker_retired"] is True
    assert body["replay_required"] is True
    assert retired == [("fig.py", "/specfix-test-figures")]


# ---- 第十一轮：事务途中这张图的档案变了（Codex #549 r4106101147 / r4106101158） ----

RUNTIME_ID = "runtime:fig.py#Kin"


def _stored_profile(monkeypatch, tmp_path, answer):
    """`enginesession.profile_of`（物化描述符记的那一档）换成可控的：`answer()` 每问一次回一次。"""
    asked: list[str] = []

    def profile_of(root, asset_id, *, default="safe"):
        asked.append(asset_id)
        return answer()

    monkeypatch.setattr(m.engine_enginesession, "profile_of", profile_of)
    monkeypatch.setattr(m, "require_project", lambda: tmp_path)
    return asked


def test_a_figure_that_becomes_native_mid_transaction_is_aborted_before_the_next_render(
    render, monkeypatch, tmp_path
):
    """B0 渲染之后另一个 `tavotto run` 会话到了屏障、把描述符换成 native：下一次渲染之前就
    中止，回 `specfix_native_unsupported`，不提交；safe worker 作废，路由到 native 的那张 live 图
    一次都没被渲染（`_engine_worker` 只在开头解析过一次，之后没有再去取会话）。"""
    now = {"profile": "safe"}
    _stored_profile(monkeypatch, tmp_path, lambda: now["profile"])
    seen: list[list] = []
    resolved: list[str] = []

    class _Worker:
        script_name = "fig.py"
        figures_dir = "/specfix-test-figures"

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            seen.append(list(patches))
            resp = render(patches)
            now["profile"] = "native"  # ← B0 一回来，档案就变了
            return resp

    def engine_worker(rel_id):
        resolved.append(rel_id)
        return _Worker(), "Kin"

    resp, retired = _post_specfix(
        monkeypatch, render, BASE, _profile(), rel_id=RUNTIME_ID, engine_worker=engine_worker
    )
    body = resp.get_json()
    assert resp.status_code == 409, body
    assert body["code"] == "specfix_native_unsupported"
    assert "patches" not in body
    assert seen == [BASE], "档案变了之后还在渲染"
    assert resolved == [RUNTIME_ID]
    assert retired == [("fig.py", "/specfix-test-figures")]


def test_a_figure_that_becomes_native_before_commit_is_not_committed(render, monkeypatch, tmp_path):
    """最后一次渲染之后、回结果之前档案变了：同样中止，不把列表交给前端去提交。"""
    now = {"profile": "safe", "renders": 0}
    _stored_profile(monkeypatch, tmp_path, lambda: now["profile"])

    def flip_at_last(patches):
        resp = render(patches)
        now["renders"] += 1
        if patches != BASE:
            now["profile"] = "native"  # ← 候选一回来就变（之后的渲染或提交前的检查必须拦住）
        return resp

    resp, _retired = _post_specfix(monkeypatch, flip_at_last, BASE, _profile(), rel_id=RUNTIME_ID)
    body = resp.get_json()
    assert resp.status_code == 409, body
    assert body["code"] == "specfix_native_unsupported"
    assert now["renders"] >= 2


def test_an_ended_native_figure_gets_the_unsupported_code_not_offline(monkeypatch, tmp_path):
    """r4106101158：描述符记着 native、会话已经结束——先按存下的档案拒绝，不去解析会话
    （解析会抛 `native_session_offline`，前端就归成 engine_failed 还去重放）。"""
    _stored_profile(monkeypatch, tmp_path, lambda: "native")

    def offline(rel_id):
        raise RunError(runcodes.NATIVE_SESSION_OFFLINE)

    resp, retired = _post_specfix(
        monkeypatch, lambda p: {}, BASE, _profile(), rel_id=RUNTIME_ID, engine_worker=offline
    )
    assert resp.status_code == 409
    assert resp.get_json()["code"] == "specfix_native_unsupported"
    assert retired == []


# ---- 第十二轮：事务里每一处解析 worker 都只要 safe（Codex #549 r4106191018） ----


def test_a_dependency_retry_that_resolves_a_native_session_never_touches_it(monkeypatch, tmp_path):
    """初次 safe 渲染缺依赖 → 切项目环境 → `_engine_attempt` 重新解析；恰在这时另一个
    `tavotto run` 会话把描述符换成了 native，重新解析拿到的是 `NativeSession`。它的
    `override()` 一次都不许被调（渲染前那道档案检查已经过了、提交前那道只能事后拒掉），
    结果是被拒，safe worker 作废。"""
    now = {"profile": "safe"}
    _stored_profile(monkeypatch, tmp_path, lambda: now["profile"])
    touched: list[list] = []

    class _Safe:
        script_name = "fig.py"
        figures_dir = "/specfix-test-figures"

        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            now["profile"] = "native"  # ← 缺依赖的这一刻，native 会话换了描述符
            raise engine_pool.WorkerError("缺依赖（模拟）", code="missing_dependency")

    class _Live(nativesession.NativeSession):
        def override(self, stem, patches, preview_dpi=None, inline_svg=False):
            touched.append(list(patches))
            return {"manifest": {"elements": []}, "warnings": []}

    safe = _Safe()
    live = _Live(session_id="native-live", descriptor={"native_id": "l" * 32, "out_dir": ""})

    def resolve(rel_id):
        return (live if now["profile"] == "native" else safe), "Kin"

    monkeypatch.setattr(m, "_switched_to_project_env", lambda worker, exc: True)
    resp, retired = _post_specfix(
        monkeypatch, lambda p: {}, BASE, _profile(), rel_id=RUNTIME_ID, engine_worker=resolve
    )
    assert touched == [], "依赖重试把修复发到了用户的 live 图上"
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["code"] == "specfix_native_unsupported"
    assert retired == [("fig.py", "/specfix-test-figures")]


def _calls_in(fn: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def _kw_true(call: ast.Call, key: str) -> bool:
    return any(
        k.arg == key and isinstance(k.value, ast.Constant) and k.value.value is True
        for k in call.keywords
    )


def test_every_worker_resolution_inside_the_specfix_transaction_is_safe_only():
    """**结构性守卫**：`/api/engine/specfix` 里（含内层的 `render` 闭包）每一处解析 worker 的
    调用——`_engine_worker` 与会在重试时重新解析的 `_engine_attempt`——都必须带
    `safe_only=True`；`_engine_attempt` 自己必须把它原样转给重试里那一次 `_engine_worker`。
    漏一处就是一条能把修复发到用户 live 图上的路（Codex #549 r4106191018）。"""
    import inspect

    tree = ast.parse(inspect.getsource(m))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    endpoint = funcs["api_engine_specfix"]
    resolving = _calls_in(endpoint, "_engine_worker") + _calls_in(endpoint, "_engine_attempt")
    assert len(resolving) >= 2, "判据落在空集合上：端点里没找到解析 worker 的调用"
    for call in resolving:
        assert _kw_true(call, "safe_only"), (
            f"api_engine_specfix 第 {call.lineno} 行的 {call.func.id} 没带 safe_only=True"
        )
    # 端点里不许绕开这两扇门直接去取会话
    for bypass in ("_resolve_engine_worker", "_safe_worker"):
        assert not _calls_in(endpoint, bypass), f"api_engine_specfix 直接调了 {bypass}"
    retry = _calls_in(funcs["_engine_attempt"], "_engine_worker")
    assert len(retry) == 1
    fwd = [k for k in retry[0].keywords if k.arg == "safe_only"]
    assert fwd and isinstance(fwd[0].value, ast.Name) and fwd[0].value.id == "safe_only", (
        "_engine_attempt 的重试没把 safe_only 转给 _engine_worker"
    )
