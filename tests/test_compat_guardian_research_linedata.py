"""Read-only curve-data comparison between the native control and the product manifest."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[1] / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import compat_driver as driver  # noqa: E402
import compat_guardian as guardian  # noqa: E402
import compat_matrix as CM  # noqa: E402


def _result(*product):
    return {"detail": {"open": {"line_xy": sorted(product)}}}


def test_agreement_equal_keeps_duplicate_curves():
    report = guardian.line_data_agreement([["a", "a", "b"], ["z"]], _result("b", "a", "a"))
    assert report["status"] == "equal"
    assert report["closest_native_figure"] == 0
    assert (report["only_native"], report["only_product"]) == (0, 0)


def test_agreement_reports_a_changed_curve_and_a_dropped_duplicate():
    report = guardian.line_data_agreement([["a", "a", "b"]], _result("a", "c"))
    assert report["status"] == "different"
    assert (report["only_native"], report["only_product"]) == (2, 1)


def test_exact_match_beats_a_figure_that_merely_shares_more_curves():
    report = guardian.line_data_agreement([["a", "b"], ["a"]], _result("a"))
    assert (report["status"], report["closest_native_figure"]) == ("equal", 1)


def test_agreement_picks_the_native_figure_the_product_opened():
    report = guardian.line_data_agreement([["x"], ["a", "b"]], _result("a", "b"))
    assert (report["status"], report["closest_native_figure"]) == ("equal", 1)


@pytest.mark.parametrize(
    "native,result",
    [(None, _result("a")), ([["a"]], {"detail": {}}), ([None], _result("a")), ([], _result("a"))],
)
def test_agreement_is_not_observed_without_both_sides(native, result):
    assert guardian.line_data_agreement(native, result) == {"status": "not_observed"}


def test_native_digest_follows_the_manifest_rule_and_sees_data_changes():
    np = pytest.importorskip("numpy")
    plt = pytest.importorskip("matplotlib.pyplot")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/tavotto/engine"))
    import manifest

    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [1.0, np.nan, 3.0])
    ax.plot([0, 1, 2], [1.0, 2.0, 3.0])
    try:
        digests = driver._line_xy_digests(fig)
        want = sorted(manifest.canonical_digest(line.get_xydata())["sha256"] for line in ax.lines)
        assert digests == want
        ax.lines[1].set_ydata([1.0, 2.0, 3.5])
        assert driver._line_xy_digests(fig) != digests
    finally:
        plt.close(fig)


def test_manifest_digests_use_only_ok_xy_parts_and_keep_duplicates():
    manifest = {
        "elements": [
            {"data_fingerprint": {"status": "ok", "parts": {"xy": {"sha256": "b"}}}},
            {"data_fingerprint": {"status": "ok", "parts": {"xy": {"sha256": "a"}}}},
            {"data_fingerprint": {"status": "ok", "parts": {"xy": {"sha256": "a"}}}},
            {"data_fingerprint": {"status": "over_budget"}},
            {"data_fingerprint": {"status": "ok", "parts": {"bars": {"sha256": "c"}}}},
            {},
        ]
    }
    assert CM.manifest_xy_digests(manifest) == ["a", "a", "b"]


def test_unobserved_product_curves_make_the_comparison_unknown_not_different():
    result = _result("a")
    result["detail"]["open"]["line_xy_unobserved"] = 2
    report = guardian.line_data_agreement([["a", "b", "c"]], result)
    assert report["status"] == "unknown"
    assert report["unobserved_product_lines"] == 2


def test_an_unobservable_native_figure_makes_the_comparison_unknown():
    report = guardian.line_data_agreement([None, ["a"]], _result("a"))
    assert report["status"] == "unknown"
    assert report["unobserved_native_figures"] == 1


def test_manifest_counts_only_unfingerprinted_lines_as_unobserved():
    manifest = {
        "elements": [
            {
                "role": "line",
                "data_fingerprint": {"status": "ok", "parts": {"xy": {"sha256": "a"}}},
            },
            {"role": "line", "data_fingerprint": {"status": "over_budget"}},
            {"role": "line", "data_fingerprint": {"status": "unavailable"}},
            {"role": "line"},
            {"role": "scatter", "data_fingerprint": {"status": "over_budget"}},
            {"role": "title"},
        ]
    }
    assert CM.manifest_unobserved_lines(manifest) == 3
    assert CM.manifest_xy_digests(manifest) == ["a"]
