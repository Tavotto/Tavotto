"""渲染协议与原序 edits 身份；父层 JSON 合格不等于 worker 能力授权。"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import chartmodel, chartprofile as profile, patchspec

BASE = "a" * 64
EDITS = [
    {"gid": "axes_0.texts_0", "prop": "pos_frac", "value": [0.4, 0.4]},
    {"gid": "axes_0", "prop": "xlim", "value": [0, 10]},
]


def validate(edits, **kwargs):
    return profile.validate_overrides(edits, render_profile=profile.NATIVE_PROFILE, **kwargs)


def test_known_profiles_and_legacy_identity_are_explicit():
    assert profile.validate_profile(profile.LEGACY_PROFILE) == "legacy-scaffold/1"
    assert profile.validate_profile(profile.NATIVE_PROFILE) == "native-manifest/1"
    assert profile.validate_profile(profile.COMPLETE_PROFILE) == "native-manifest/2"
    assert profile.identity(BASE, profile.LEGACY_PROFILE) == BASE
    assert profile.identity(BASE, profile.LEGACY_PROFILE, []) == BASE
    assert profile.identity(BASE, profile.NATIVE_PROFILE, []) != BASE


@pytest.mark.parametrize("unknown", [None, True, 1, {}, [], "native-manifest/3", "native", ""])
def test_unknown_profile_never_selects_an_existing_renderer(unknown):
    with pytest.raises(profile.ChartProfileError, match="chart_model_unsupported"):
        profile.validate_profile(unknown)
    with pytest.raises(profile.ChartProfileError):
        profile.identity(BASE, unknown)


def test_full_edits_and_duplicates_are_preserved_in_the_original_order():
    edits = [*EDITS, {"gid": "axes_0", "prop": "xlim", "identity": "id:one", "value": [0, 20]}]
    before = copy.deepcopy(edits)
    out = validate(edits)
    assert out == before
    out[0]["value"][0] = 0.7
    assert edits == before
    assert profile.validate_overrides(()) == []
    with pytest.raises(profile.ChartProfileError, match="legacy"):
        profile.validate_overrides(edits)


def test_native_identity_includes_order_even_when_the_existing_patch_hash_does_not():
    reversed_edits = list(reversed(EDITS))
    assert patchspec.patch_hash(EDITS) == patchspec.patch_hash(reversed_edits)
    first = profile.identity(BASE, profile.NATIVE_PROFILE, EDITS)
    assert first != profile.identity(BASE, profile.NATIVE_PROFILE, reversed_edits)
    assert first != profile.identity("b" * 64, profile.NATIVE_PROFILE, EDITS)
    assert first == chartmodel.digest_of(
        {
            "base_revision": BASE,
            "render_profile": profile.NATIVE_PROFILE,
            "overrides": EDITS,
            "overrides_sha256": hashlib.sha256(
                json.dumps(
                    EDITS,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
        }
    )
    assert first == profile.identity(BASE, profile.NATIVE_PROFILE, copy.deepcopy(EDITS))


@pytest.mark.parametrize(
    "edits",
    [
        None,
        {},
        "[]",
        tuple(EDITS),
        [None],
        [{"gid": "a", "prop": "text"}],
        [{"gid": "a", "value": 1}],
        [{"prop": "text", "value": 1}],
        [{"gid": "", "prop": "text", "value": 1}],
        [{"gid": "a", "prop": "", "value": 1}],
        [{"gid": "a", "prop": "text", "value": "a", "identity": ""}],
        [{"gid": "a", "prop": "text", "value": "a", "identity": None}],
        [{"gid": "a", "prop": "text", "value": "a", "script": "evil.py"}],
        [{"gid": "a", "prop": "text", "value": "a", "path": "C:/private"}],
        [{"gid": "a", "prop": "text", "value": "a", "origin": "execution"}],
        [{"gid": "a", "prop": "text", "value": float("nan")}],
        [{"gid": "a", "prop": "text", "value": float("inf")}],
        [{"gid": "a", "prop": "text", "value": {1: "bad key"}}],
        [{"gid": "a", "prop": "text", "value": (1, 2)}],
        [{"gid": "a", "prop": "text", "value": 2**70}],
    ],
)
def test_patchspec_invalid_shapes_and_extra_capabilities_are_refused(edits):
    with pytest.raises(profile.ChartProfileError, match="chart_model_invalid"):
        validate(edits)


def test_patch_value_depth_still_uses_the_existing_patchspec_limit():
    value = 0
    for _ in range(33):
        value = [value]
    with pytest.raises(profile.ChartProfileError, match="value_too_deep"):
        validate([{"gid": "a", "prop": "pos_frac", "value": value}])


def test_shape_validation_does_not_claim_that_a_setter_or_gid_is_authorized():
    edits = [{"gid": "not_owned", "prop": "not_a_setter", "value": "literal"}]
    assert validate(edits) == edits


@pytest.mark.parametrize("budget", ["bytes", "nodes", "depth"])
def test_budgets_are_checked_before_canonical_diagnostics(monkeypatch, budget):
    monkeypatch.setattr(
        patchspec,
        "canonicalize_with_diagnostics",
        lambda _: pytest.fail("预算未过却进入 canonical diagnostics"),
    )
    if budget == "bytes":
        kwargs = {"max_bytes": 16}
    else:
        kwargs = {}
        monkeypatch.setattr(profile, "MAX_JSON_NODES" if budget == "nodes" else "MAX_JSON_DEPTH", 1)
    with pytest.raises(profile.ChartProfileError, match="svg_resource_limit"):
        validate(EDITS, **kwargs)


def test_invalid_base_revision_does_not_produce_a_valid_render_identity():
    for base in (None, "", 1):
        with pytest.raises(profile.ChartProfileError):
            profile.identity(base, profile.NATIVE_PROFILE)


def test_exact_json_identity_distinguishes_values_the_real_text_setter_distinguishes():
    values = [2**53, 2**53 + 1, 1, 1.0]
    patches = [[{"gid": "fig.texts_0", "prop": "text", "value": value}] for value in values]
    assert all(not patchspec.canonicalize_with_diagnostics(p)[1] for p in patches)
    # 既有数值身份的行为不改；新增身份要同时保住原生str(value)的文本语义。
    assert chartmodel.identity_bytes(values[0]) == chartmodel.identity_bytes(values[1])
    assert chartmodel.identity_bytes(values[2]) == chartmodel.identity_bytes(values[3])
    assert len({profile.identity(BASE, profile.NATIVE_PROFILE, p) for p in patches}) == 4
    root = Path(__file__).resolve().parents[1]
    script = f"""
import json,sys
sys.path.insert(0,{str(root / "src")!r})
import matplotlib as mpl
mpl.use('Agg')
from matplotlib.figure import Figure
from tavotto.engine import bridgeboot
runtime=bridgeboot.load_engine_modules({str(root / "src/tavotto/engine")!r},bridgeboot.ENGINE_SIBLINGS)
out=[]
with mpl.rc_context({{'text.usetex':False}}):
    for patches in json.load(sys.stdin):
        fig=Figure()
        text=fig.text(.2,.2,'original')
        state=runtime.overrides.FigState(fig)
        runtime.manifest.instrument(state)
        warnings=runtime.overrides.apply(state,patches)
        assert not warnings
        out.append(text.get_text())
print(json.dumps(out))
"""
    process = subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=json.dumps(patches),
        text=True,
        capture_output=True,
        timeout=30,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == ["9007199254740992", "9007199254740993", "1", "1.0"]
