"""`export(..., data_points=…)` 在真实控制面上的往返（ADR 0208 §二）。

字段只是 export 负载上的一个可选键（加字段不升版）：Python 池与 workerd 两条控制面都要把它原样送到 worker、
把回包里的 `data_points` 原样带回来；不传时负载一个字节不多。native 会话由用例喂响应、核发出去的帧
（它的另一端是同一个 `wireproto.V1Handler`，换算本身由 `tests/test_export_data_points.py` 在进程内量）。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

from tavotto.engine import nativesession, pool, workerd_client

SCRIPT = """\
import matplotlib.pyplot as plt

def main():
    fig, ax = plt.subplots(figsize=(4, 3), layout="constrained")
    ax.plot([0, 10], [0, 5])
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    ax.set_title("t")
    fig.savefig("pts.pdf")
"""


def _workerd_binary() -> str | None:
    saved = os.environ.pop("TAVOTTO_WORKERD", None)
    try:
        return workerd_client.find_workerd()
    finally:
        if saved is not None:
            os.environ["TAVOTTO_WORKERD"] = saved


WORKERD_EXE = _workerd_binary()


@pytest.fixture(params=["python", "workerd"])
def figs(request, tmp_path, monkeypatch):
    if request.param == "workerd":
        if WORKERD_EXE is None:
            pytest.skip("没有 tavotto-workerd 产物（not-run；先在 workerd/ 里 cargo build）")
        monkeypatch.setenv("TAVOTTO_WORKERD", WORKERD_EXE)
    else:
        monkeypatch.setenv("TAVOTTO_WORKERD", "0")
    workerd_client.reset_client()
    d = tmp_path / "figures"
    d.mkdir()
    (d / "pts.py").write_text(SCRIPT, encoding="utf-8")
    try:
        yield d, request.param
    finally:
        pool.shutdown_all(figures_dir=str(d), wait=True)
        workerd_client.reset_client()


def test_data_points_round_trip_on_both_control_planes(figs, tmp_path):
    d, plane = figs
    w = pool.get("pts.py", str(d), "main")
    assert isinstance(w, pool.WorkerdWorker if plane == "workerd" else pool.EngineWorker)
    w.ensure_built()
    stem = "pts"
    plain = w.export(stem, [], str(tmp_path / "plain.pdf"), "pdf", 300)
    assert "data_points" not in plain and "draw_manifest" not in plain  # 不问就不答
    first = w.export(stem, [], str(tmp_path / "probe.pdf"), "pdf", 300, data_points=[])
    axes = next(e for e in first["draw_manifest"]["elements"] if e["role"] == "axes")
    corners = [(0, 0), (10, 0), (0, 5), (10, 5)]
    res = w.export(
        stem,
        [],
        str(tmp_path / "pts.pdf"),
        "pdf",
        300,
        data_points=[{"axes": axes["gid"], "x": x, "y": y} for x, y in corners]
        + [{"axes": "nope", "x": 0, "y": 0}],
    )
    pts = res["data_points"]
    bx, by, bw, bh = next(e for e in res["draw_manifest"]["elements"] if e["gid"] == axes["gid"])[
        "bbox"
    ]
    want = [[bx, by + bh], [bx + bw, by + bh], [bx, by], [bx + bw, by]]
    for got, exp in zip(pts[:4], want):
        assert got["status"] == "ok" and np.allclose(got["xy"], exp, atol=1e-9), (plane, got, exp)
    assert pts[4] == {"status": "unresolvable", "reason": "not_axes"}


def test_bad_data_points_are_refused_by_the_worker(figs, tmp_path):
    d, _plane = figs
    w = pool.get("pts.py", str(d), "main")
    w.ensure_built()
    with pytest.raises(pool.WorkerError) as exc:
        w.export(
            "pts",
            [],
            str(tmp_path / "x.pdf"),
            "pdf",
            300,
            data_points=[{"axes": "a", "x": True, "y": 0}],
        )
    assert getattr(exc.value, "code", None) == "bad_request"


class _Native(nativesession.NativeSession):
    def __init__(self, out_dir):
        super().__init__(
            session_id="native-points",
            descriptor={
                "native_id": "p" * 32,
                "metadata": {
                    "project_root": str(out_dir),
                    "cwd": str(out_dir),
                    "interpreter": sys.executable,
                    "target_kind": "script",
                },
                "out_dir": str(out_dir),
            },
        )
        self.sent: list[dict] = []

    def _request(self, obj: dict, timeout: float, **_kw) -> dict:
        self.sent.append(obj)
        return {"ok": True, "path": "x.pdf", "data_points": [], "unrestored": 0}


def test_native_session_forwards_data_points_only_when_asked(tmp_path):
    s = _Native(tmp_path)
    s.export("Fig1", [], str(tmp_path / "a.pdf"))
    assert "data_points" not in s.sent[-1]
    q = [{"axes": "axes_0", "x": 1, "y": 2}]
    res = s.export("Fig1", [], str(tmp_path / "b.pdf"), data_points=q)
    assert s.sent[-1]["data_points"] == q and res["data_points"] == []
