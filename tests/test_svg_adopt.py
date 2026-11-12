"""可信来源采用判定测试（SG04 / ADR 0181）。

看护：同名错源、改源、过期快照、伪造 owner、native 离线都不会进入错误
Figure；无元数据走 declarative 候选；冷态（无执行观察）的显式快照引用仍可
采用；元数据是闭式 schema，可执行字段与机器路径无处容身。
"""

from __future__ import annotations

import hashlib

import pytest

from tavotto.engine import svgadopt

SCENE = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect/></svg>'
SCENE_SHA = hashlib.sha256(SCENE).hexdigest()


def metadata(**overrides):
    meta = {
        "schema": svgadopt.ADOPT_SCHEMA,
        "source": {
            "kind": "figure",
            "source_id": "src-1",
            "receipt_id": "rcp-1",
            "generation": "g1",
            "patch_hash": "ph1",
            "execution_owner": "owner-1",
        },
        "scene_sha256": SCENE_SHA,
        "capabilities": {"portable_model": True, "requires_python_transforms": False},
        "completeness": "self-contained",
    }
    for key, value in overrides.items():
        if key in ("source", "capabilities"):
            meta[key] = {**meta[key], **value}
        else:
            meta[key] = value
    import json

    return json.dumps(meta)


def adopt(meta_text, **facts):
    defaults = {
        "snapshot_static_sha256": SCENE_SHA,
        "receipt_matches": True,
        "native_offline": False,
    }
    defaults.update(facts)
    return svgadopt.decide_adoption(SCENE, meta_text, **defaults)


# ---------------------------------------------------------------------------
# 接受面
# ---------------------------------------------------------------------------


def test_verified_metadata_adopts_figure_source():
    verdict = adopt(metadata())
    assert verdict["kind"] == "figure"
    assert verdict["reference"]["source_id"] == "src-1"
    assert verdict["portable_model"] is True
    assert verdict["completeness"] == "self-contained"


def test_cold_explicit_snapshot_still_adopts():
    # 冷态：无本进程执行、引用显式且字节核对 —— 仍可采用（ADR 0150）
    verdict = adopt(metadata())
    assert verdict["kind"] == "figure"


def test_no_metadata_is_a_declarative_candidate_never_a_guess():
    verdict = adopt(None)
    assert verdict == {"kind": "declarative"}
    verdict2 = adopt("   ")
    assert verdict2["kind"] == "declarative"


def test_python_transform_capability_honestly_caps_portability():
    verdict = adopt(metadata(capabilities={"requires_python_transforms": True}))
    assert verdict["kind"] == "figure"
    assert verdict["portable_model"] is False


def test_reference_only_completeness_is_carried():
    verdict = adopt(metadata(completeness="reference-only"))
    assert verdict["completeness"] == "reference-only"


# ---------------------------------------------------------------------------
# 拒绝面（每条都是「不能进错误 Figure」的看护）
# ---------------------------------------------------------------------------


def test_wrong_scene_bytes_are_refused():
    other = SCENE.replace(b"rect", b"rect2")
    verdict = adopt(metadata(), snapshot_static_sha256=hashlib.sha256(other).hexdigest())
    assert verdict == {
        "kind": "refused",
        "reason": "adopt_source_changed",
        "detail": "snapshot is bound to different bytes",
    }
    verdict2 = adopt(metadata(scene_sha256="0" * 64))
    assert verdict2["reason"] == "adopt_source_changed"


def test_missing_snapshot_is_refused_not_guessed():
    verdict = adopt(metadata(), snapshot_static_sha256=None)
    assert verdict["reason"] == "adopt_snapshot_missing"


def test_receipt_mismatch_is_refused():
    verdict = adopt(metadata(), receipt_matches=False)
    assert verdict["reason"] == "adopt_receipt_mismatch"


def test_forged_owner_claims_are_caught_by_receipt_identity():
    # 元数据声称的出处对不上真实回执身份 → 进不了任何 Figure（授权不按 owner 字放行）
    verdict = adopt(metadata(), receipt_matches=False)
    assert verdict["reason"] == "adopt_receipt_mismatch"
    # owner 字段缺省（老元数据）不挡核对：授权只来自快照字节绑定 + 回执身份
    verdict2 = adopt(metadata(source={"execution_owner": None}))
    assert verdict2["kind"] == "figure"


def test_native_offline_never_falls_back():
    verdict = adopt(metadata(), native_offline=True)
    assert verdict["reason"] == "adopt_native_offline"
    assert verdict["kind"] == "refused"


def test_metadata_with_executable_or_unknown_fields_is_invalid():
    import json

    bad = json.loads(metadata())
    bad["hook"] = "import os; os.system('calc')"
    with pytest.raises(svgadopt.AdoptionError) as ei:
        svgadopt.parse_metadata(json.dumps(bad))
    assert ei.value.reason == "adopt_metadata_invalid"
    bad2 = json.loads(metadata())
    bad2["source"]["path"] = "C:\\Users\\machine\\secret.py"
    with pytest.raises(svgadopt.AdoptionError) as ei:
        svgadopt.parse_metadata(json.dumps(bad2))
    assert ei.value.reason == "adopt_metadata_invalid"


def test_metadata_schema_major_is_refused():
    import json

    bad = json.loads(metadata())
    bad["schema"] = "tavotto.svg-source-meta/2"
    with pytest.raises(svgadopt.AdoptionError) as ei:
        svgadopt.parse_metadata(json.dumps(bad))
    assert ei.value.reason == "adopt_schema_unsupported"


def test_refusal_reasons_are_closed():
    with pytest.raises(AssertionError):
        svgadopt.AdoptionError("not_a_reason")
