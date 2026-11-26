# ruff: noqa: F811 - `env` 是从端点用例导入的 fixture，用例参数同名
"""修改方案的真 worker 冒烟（ADR 0206）：真脚本、真 manifest、真 warnings 形状，走完 B0 出图 → 固定提议（改一个字号）→ 候选 → 有效改动。

模型是注入的（永不真调）；worker 是直连的真子进程（写法与跳过条件同 `tests/test_specfix_real.py`）。
"""

from __future__ import annotations

import pytest

from tests.test_changeplan_endpoints import (  # noqa: F401 - `env` 是 fixture
    env,
    post,
    wait,
)
from tests.test_specfix_real import SCRIPT, WORKER_PY, _rpc, _spawn_render

pytestmark = pytest.mark.skipif(WORKER_PY is None, reason="没有带科学栈的解释器，跳过真链路用例")


class RealAdapter:
    """把直连 worker 的协议命令包成端点用的 `override` / `preview_png_snapshot`。"""

    script_name = "fig.py"
    figures_dir = "/changeplan-real-figures"
    execution_replaces_current = False

    def __init__(self, proc):
        self.proc = proc

    def override(self, stem, patches, preview_dpi=None, inline_svg=False):
        return _rpc(self.proc, {"cmd": "override", "stem": stem, "patches": patches})

    def preview_png_snapshot(self, stem, patches, width, **_kw):
        return _rpc(
            self.proc,
            {
                "cmd": "preview_png",
                "stem": stem,
                "patches": patches,
                "width": width,
                "with_manifest": True,
                "tag": "cp",
            },
        )


def test_real_worker_roundtrip_gives_one_effective_change_and_retires_the_worker(
    env, tmp_path, monkeypatch
):
    m, client, _figs, _fake, model, retired = env
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    proc, render = _spawn_render(real_dir, SCRIPT, "Kin")
    try:
        manifest = render([])["manifest"]
        title = next(e for e in manifest["elements"] if e.get("role") == "title")
        size = next(f for f in title["editable"] if f["prop"] == "fontsize")
        assert size["value"] == 9
        monkeypatch.setattr(
            m, "_resolve_engine_worker", lambda rel_id, **kw: (RealAdapter(proc), "Kin")
        )
        model.result = {
            "groups": [
                {
                    "comment_id": "c1",
                    "changes": [{"gid": title["gid"], "prop": "fontsize", "value": 14}],
                }
            ],
            "unhandled": [{"comment_id": "c2", "reason": "needs_script"}],
        }
        resp = post(client)
        assert resp.status_code == 202
        out = wait(client, resp.get_json()["plan_id"], timeout=120)
        assert out["state"] == "ready", out
        (group,) = out["plan"]["groups"]
        (change,) = group["changes"]
        assert (change["target_gid"], change["prop"], change["before"], change["after"]) == (
            title["gid"],
            "fontsize",
            9,
            14,
        )
        assert group["boxes"]["before"] and group["boxes"]["after"]
        assert all(len(b["box"]) == 4 for b in group["boxes"]["before"] + group["boxes"]["after"])
        assert out["replay_required"] is True and len(retired) == 1
        for name in ("before", "after"):
            assert client.get(out["plan"]["preview"][name]).data[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def test_real_worker_log_yscale_on_a_zero_based_axis_gets_a_positive_lower_limit(
    env, tmp_path, monkeypatch
):
    """真 manifest：动力学图的纵轴范围从 0 起，只改 `yscale` 会坍掉；方案要带上正数下限并问用户（维护者 2026-11-26）。"""
    m, client, _figs, _fake, model, _retired = env
    real_dir = tmp_path / "real_log"
    real_dir.mkdir()
    proc, render = _spawn_render(real_dir, SCRIPT, "Kin")
    try:
        manifest = render([])["manifest"]
        axes = next(e for e in manifest["elements"] if e.get("role") == "axes")
        lim = next(f for f in axes["editable"] if f["prop"] == "ylim")["value"]
        assert min(lim) <= 0 < max(lim)
        monkeypatch.setattr(
            m, "_resolve_engine_worker", lambda rel_id, **kw: (RealAdapter(proc), "Kin")
        )
        model.result = {
            "groups": [
                {
                    "comment_id": "c1",
                    "changes": [{"gid": axes["gid"], "prop": "yscale", "value": "log"}],
                }
            ],
            "unhandled": [],
        }
        resp = post(client)
        assert resp.status_code == 202
        out = wait(client, resp.get_json()["plan_id"], timeout=120)
        assert out["state"] == "ready", out
        (group,) = out["plan"]["groups"]
        assert group["confirm"]["kind"] == "log_lower_limit" and group["confirm"]["axis"] == "y"
        changes = {c["prop"]: c for c in group["changes"]}
        assert changes["yscale"]["after"] == "log"
        assert 0 < min(changes["ylim"]["after"]) < max(changes["ylim"]["after"])
        # 候选真的渲染出来：下限已经是正数，对数轴没有坍掉
        after = render(out["plan"]["candidate_patches"])["manifest"]
        ax_after = next(e for e in after["elements"] if e["gid"] == axes["gid"])
        got = {f["prop"]: f["value"] for f in ax_after["editable"]}
        assert got["yscale"] == "log" and min(got["ylim"]) > 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
