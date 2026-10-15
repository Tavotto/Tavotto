"""Lookup ownership follows palette content, rather than the number of bound images (#546)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tavotto.engine import pool

try:
    WORKER_PY = pool.find_worker_python()
except pool.WorkerError:
    WORKER_PY = None

pytestmark = pytest.mark.skipif(
    WORKER_PY is None, reason="requires a matplotlib worker interpreter"
)
ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "support" / "colour_tube_cache_probe.py"


@pytest.fixture(scope="module")
def facts():
    proc = subprocess.run(
        [WORKER_PY, str(PROBE)], capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert (
        Path(result["module"]).resolve() == ROOT / "src" / "tavotto" / "engine" / "colorbarmodel.py"
    )
    return result


def test_large_discrete_palette_keeps_one_lookup_for_four_bound_rasters(facts):
    one, four = facts["one"], facts["four"]
    assert one["fields"] == 1 and four["fields"] == 4
    assert one["single_tube_payload_bytes"] > 16 * 2**20  # Exercise the actual large-table case.
    assert four["unique_tubes"] == 1
    assert four["unique_payload_bytes"] == one["unique_payload_bytes"]
    assert one["cache_key_count"] == four["cache_key_count"] == 1
    assert one["cache_key_digest_bytes"] == four["cache_key_digest_bytes"] == 64
    assert one["cache_key_object_bytes"] == four["cache_key_object_bytes"]


def test_traced_retained_lookup_allocations_do_not_multiply_with_rasters(facts):
    one, four = facts["one"], facts["four"]
    # The trace must see the real NumPy buffers; otherwise the memory assertion would be empty.
    assert one["tracemalloc_retained_bytes"] >= one["unique_payload_bytes"]
    # Small per-field bookkeeping is allowed; three more 20+ MiB lookup payloads are not.
    assert four["tracemalloc_retained_bytes"] <= one["tracemalloc_retained_bytes"] + 512 * 1024


def test_equivalent_colormap_copies_share_but_mutated_palette_content_does_not(facts):
    assert facts["cache"]["copied_payload_shared"] is True
    assert facts["cache"]["mutated_palette_separate"] is True


def test_colour_positions_and_unrounded_nearest_colour_facts_are_part_of_identity(facts):
    assert facts["cache"]["positions_separate"] is True
    assert facts["cache"]["float_tubes_separate"] is True
    assert facts["cache"]["float_nearest_entries"] == [0, 1]


def test_tolerance_changes_do_not_reuse_a_wider_lookup(facts):
    assert facts["cache"]["tolerance_separate"] is True


def test_weak_cache_and_ephemeral_dense_tables_do_not_add_retained_payloads(facts):
    assert facts["cache"]["weakly_released"] is True
    assert facts["cache"]["dense_independent"] is True


def test_sharing_lookup_does_not_share_mutable_recolour_or_undo_state(facts):
    assert all(facts["recolour"].values()), facts["recolour"]


def test_direct_field_construction_uses_the_same_shared_lookup(facts):
    assert facts["direct_fields_shared"] is True


def test_user_inverse_may_mutate_its_input_without_corrupting_another_figure(facts):
    assert all(facts["cross_figure_callbacks"].values()), facts["cross_figure_callbacks"]
