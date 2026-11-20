"""一键排版（ADR 0199）引擎侧 + 闭源组件宿主（ADR 0198）：用开源示例扩展 `tests/fixtures/pro_echo`，CI 不需要闭源代码。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tavotto.engine import license as engine_license, prohost, quicklayout

ECHO = Path(__file__).parent / "fixtures" / "pro_echo"


# ---------------------------------------------------------------------------
# 宿主
# ---------------------------------------------------------------------------
@pytest.fixture
def dev_mode(monkeypatch, tmp_path):
    monkeypatch.delenv(engine_license.ENV_HARD_OFF, raising=False)  # conftest 全局关着授权
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(engine_license.ENV_ENDPOINT, "http://127.0.0.1:9")
    monkeypatch.setenv("TAVOTTO_DIAG_DEV_LOOPBACK", "1")
    monkeypatch.setenv(prohost.ENV_PRO_DIR, str(ECHO))
    monkeypatch.setattr(prohost, "_crashes", 0)
    prohost.shutdown()
    yield
    prohost.shutdown()


def test_pro_dir_ignored_without_dev_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(engine_license.ENV_ENDPOINT, raising=False)
    monkeypatch.setenv(prohost.ENV_PRO_DIR, str(ECHO))
    assert prohost.locate() is None  # 正式安装里设了 TAVOTTO_PRO_DIR 也无效（ADR 0198 §八）


def test_echo_roundtrip(dev_mode):
    assert prohost.locate() == ECHO
    assert prohost.call("echo", {"a": 1}, feature="quick-layout") == {"payload": {"a": 1}}


def test_unsupported_api_is_refused(dev_mode, monkeypatch):
    monkeypatch.setenv("PRO_ECHO_API", "99")
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("echo", {}, feature="quick-layout")
    assert exc.value.code == "unsupported"


def test_feature_not_offered(dev_mode):
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("echo", {}, feature="hosted-ai")
    assert exc.value.code == "unsupported"


def test_timeout_and_crash_are_failures(dev_mode):
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("sleep", {"s": 5}, feature="quick-layout", timeout=0.5)
    assert exc.value.code == "timeout"
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("crash", {}, feature="quick-layout")
    assert exc.value.code == "crashed"
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("wrong_id", {}, feature="quick-layout")
    assert exc.value.code == "internal"
    # 同一会话连崩 3 次就停用
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("echo", {}, feature="quick-layout")
    assert exc.value.code == "crashed"


def test_protocol_error_code_is_closed_set(dev_mode):
    with pytest.raises(prohost.ProUnavailable) as exc:
        prohost.call("nope", {}, feature="quick-layout")
    assert exc.value.code == "unsupported"


# ---------------------------------------------------------------------------
# 操作校验：任何一条不合规整份作废，不做部分应用
# ---------------------------------------------------------------------------
def _panel(key="k", gid_of=None):
    p = quicklayout.Panel(
        key=key, rel_id=f"{key}.pdf", base=[], box=[0, 0, 50, 40], render=lambda _p: {}
    )
    p.gid_of = gid_of or {"e1": "axes_0"}
    return p


PAGE = {"w": 210, "h": 297, "margin": 10}


@pytest.mark.parametrize(
    "op",
    [
        {"op": "panel.delete", "ref": "p1"},
        {"op": "panel.place", "ref": "p9", "x": 0, "y": 0, "w": 10, "h": 10},
        {"op": "panel.place", "ref": "p1", "x": 1990, "y": 0, "w": 50, "h": 10},
        {"op": "panel.place", "ref": "p1", "x": float("nan"), "y": 0, "w": 50, "h": 10},
        {"op": "figure.size", "ref": "p1", "w": 0, "h": 10},
        {
            "op": "axes.position",
            "ref": "p1",
            "element_ref": "e9",
            "x": 0,
            "y": 0,
            "w": 0.5,
            "h": 0.5,
        },
        {"op": "label.place", "ref": "p1", "x": -1, "y": 0},
    ],
)
def test_bad_op_voids_whole_plan(op):
    p = _panel()
    good = {"op": "figure.size", "ref": "p1", "w": 60, "h": 40}
    with pytest.raises(quicklayout.QuickLayoutError) as exc:
        quicklayout._apply_ops([good, op], {"p1": p}, PAGE)
    assert exc.value.code == "invalid_plan"
    assert p.patches == []  # 前面那条合规的也没落


def test_ops_land_as_existing_keys():
    p = _panel()
    quicklayout._apply_ops(
        [
            {"op": "panel.place", "ref": "p1", "x": 10, "y": 12, "w": 60, "h": 40},
            {"op": "figure.size", "ref": "p1", "w": 60, "h": 40},
            # 求解器给 y 向下的外框比例；落成 matplotlib 的 [左, 下, 宽, 高]
            {
                "op": "axes.position",
                "ref": "p1",
                "element_ref": "e1",
                "x": 0.2,
                "y": 0.1,
                "w": 0.7,
                "h": 0.6,
            },
        ],
        {"p1": p},
        PAGE,
    )
    assert p.box == [10.0, 12.0, 60.0, 40.0]
    assert {"gid": "figure", "prop": "size_mm", "value": [60.0, 40.0]} in p.patches
    assert {"gid": "axes_0", "prop": "position", "value": [0.2, 0.3, 0.7, 0.6]} in p.patches


# ---------------------------------------------------------------------------
# 验收指标
# ---------------------------------------------------------------------------
def _manifest(size, axes_bbox, ink=(0.0, 0.0, 1.0, 1.0)):
    return {
        "size_mm": list(size),
        "elements": [
            {"gid": "figure", "role": "figure", "bbox": [0, 0, 1, 1], "ink_bbox": list(ink)},
            {
                "gid": "axes_0",
                "role": "axes",
                "bbox": list(axes_bbox),
                "editable": [{"prop": "aspect", "value": "auto"}],
            },
        ],
    }


def _placed(key, box, axes_bbox):
    p = _panel(key)
    p.box = list(box)
    p.manifest = _manifest(box[2:], axes_bbox)
    p.size_mm = list(box[2:])
    return p


def test_check_passes_aligned_grid():
    a = _placed("a", [10, 10, 73.5, 50], [0.2, 0.1, 0.75, 0.7])
    b = _placed("b", [86.5, 10, 73.5, 50], [0.2, 0.1, 0.75, 0.7])
    assert quicklayout.check([a, b], 150, 3, [], 9) == []


def test_check_catches_misaligned_bottom_and_gap():
    a = _placed("a", [10, 10, 73.5, 50], [0.2, 0.1, 0.75, 0.7])
    b = _placed("b", [87.5, 10, 73.5, 50], [0.2, 0.1, 0.75, 0.68])
    metrics = {m["metric"] for m in quicklayout.check([a, b], 150, 3, [], 9)}
    assert {"align_bottom", "gap", "width"} <= metrics


def test_check_catches_scaled_panel_and_label_overlap():
    a = _placed("a", [10, 10, 73.5, 50], [0.2, 0.1, 0.75, 0.7])
    b = _placed("b", [86.5, 10, 73.5, 50], [0.2, 0.1, 0.75, 0.7])
    b.size_mm = [80, 50]  # 图框 ≠ 图幅：缩放了，字号就不再是规范值
    metrics = {
        m["metric"] for m in quicklayout.check([a, b], 150, 3, [{"ref": "a", "x": 12, "y": 12}], 9)
    }
    assert {"scale", "label_overlap"} <= metrics


def test_plan_refuses_without_license(monkeypatch):
    monkeypatch.setattr(engine_license, "has", lambda _f: False)
    with pytest.raises(quicklayout.QuickLayoutError) as exc:
        quicklayout.plan(
            [_panel("a"), _panel("b")],
            [],
            page=PAGE,
            target="double",
            gap_mm=3,
            profile={},
            label_pt=9,
        )
    assert exc.value.code == "not_licensed"
