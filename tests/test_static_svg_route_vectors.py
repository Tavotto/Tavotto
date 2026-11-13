"""Backend capability route and frontend consumer share these exact wire vectors."""

import json
from pathlib import Path

import pytest

from tavotto.engine import exportroute

VECTORS = json.loads(
    (Path(__file__).parent / "golden/static_svg_route_vectors.json").read_text(encoding="utf-8")
)["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda row: row["name"])
def test_backend_route_matches_static_svg_wire_vector(vector):
    assert exportroute.panel_route(**vector["args"]) == vector["expected"]
