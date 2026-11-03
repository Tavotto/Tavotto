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


def _result_with(*product, incomparable=0):
    result = _result(*product)
    result["detail"]["open"]["line_xy_incomparable"] = incomparable
    return result


def test_errorbar_figure_is_unknown_not_a_false_different():
    # Native sees the errorbar's data line; the product stores it as a grouped `points` fingerprint.
    report = guardian.line_data_agreement([["e", "a"]], _result_with("a", incomparable=1))
    assert report["status"] == "unknown"
    assert report["incomparable_product_lines"] == 1


def test_three_d_figure_is_unknown_when_only_the_native_side_knows_it():
    report = guardian.line_data_agreement([["z"]], _result_with(incomparable=0), [2])
    assert report["status"] == "unknown"
    assert report["incomparable_native_curves"] == 2


def test_incomparable_curves_in_another_native_figure_do_not_poison_the_comparison():
    report = guardian.line_data_agreement([["a"], ["x"]], _result_with("a"), [0, 3])
    assert report["status"] == "equal"


def test_plain_multi_line_figures_still_get_equal_and_different():
    assert (
        guardian.line_data_agreement([["a", "b"]], _result_with("b", "a"), [0])["status"] == "equal"
    )
    assert guardian.line_data_agreement([["a", "b"]], _result_with("a", "c"), [0])["status"] == (
        "different"
    )


def test_manifest_counts_grouped_and_non_xy_line_elements_as_incomparable():
    ok = {"status": "ok", "parts": {"xy": {"sha256": "a"}}}
    manifest = {
        "elements": [
            {"role": "line", "data_fingerprint": ok},
            {"role": "line", "data_fingerprint": {"status": "ok", "parts": {"xyz": {}}}},
            {"role": "errorbar", "data_fingerprint": {"status": "ok", "parts": {"points": {}}}},
            {"role": "stem_series", "data_fingerprint": ok},
            {"role": "scatter", "data_fingerprint": {"status": "ok", "parts": {"offsets": {}}}},
        ]
    }
    assert CM.manifest_incomparable_lines(manifest) == 3


def test_native_driver_flags_errorbar_stem_and_three_d_but_not_plain_lines():
    plt = pytest.importorskip("matplotlib.pyplot")
    figures = []
    try:
        plain, ax = plt.subplots()
        ax.plot([0, 1], [0, 1])
        ax.plot([0, 1], [1, 0])
        err, ax = plt.subplots()
        ax.errorbar([0, 1], [0, 1], yerr=0.1)
        stem, ax = plt.subplots()
        ax.stem([0, 1], [1, 2])
        three = plt.figure()
        three.add_subplot(projection="3d").plot([0, 1], [0, 1], [0, 1])
        figures = [plain, err, stem, three]
        counts = [driver._line_xy_incomparable(f) for f in figures]
        assert counts[0] == 0
        assert all(c > 0 for c in counts[1:]), counts
    finally:
        plt.close("all")
