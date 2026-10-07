"""T03：精确 argv 经**真实公共入口**贯通 探测 → 编辑 → 强制冷 worker → 导出 → 保存重开（真 worker、真服务）。

夹具复用 T00 的 F3 形状：同一份 `scaled.py`，同一个输出 stem（`result`），只有 `--scale` 不同。脚本每执行一次往
**项目外**追加一行 `sys.argv[1:]`（执行次数与脚本实际看到的参数的唯一真值）；曲线 = `YS × scale`，编辑请求回的 ylim
由它决定，所以"没串配置"可以由图里真实的数据范围证明，而不是由请求回显。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from support import foundation_app as fa
from tavotto.engine import figcapture, pool as _pool

ROOT = Path(__file__).resolve().parent.parent

try:
    WORKER_PY = _pool.find_worker_python()
except Exception:  # noqa: BLE001
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)
ENV = {"TAVOTTO_USER_ENV_DISCOVERY": "0", "PYTHONPATH": str(ROOT / "src")}

YS = [1.0, 2.0, 4.0]
SCRIPT = """\
import json, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

args = sys.argv[1:]
scale = float(args[args.index("--scale") + 1]) if "--scale" in args else 1.0
with open({counter!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\\n")
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1, 2], [v * scale for v in {ys!r}])
fig.savefig("result.pdf")
"""
SECRET = "S3CRET-TOKEN-777"


def _runs(counter: Path) -> list[list[str]]:
    if not counter.exists():
        return []
    return [json.loads(line) for line in counter.read_text(encoding="utf-8").splitlines() if line]


def _ylim(render: dict) -> list[float]:
    axes = next(e for e in render["manifest"]["elements"] if e["role"] == "axes")
    return next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")


def _expected_ylim(scale: float) -> list[float]:
    ys = [v * scale for v in YS]
    margin = 0.05 * (max(ys) - min(ys))
    return [min(ys) - margin, max(ys) + margin]


def _probe(app: fa.RunningApp, argv=None, **extra) -> dict:
    body = {"script": "scaled.py", **extra}
    if argv is not None:
        body["argv"] = argv
    status, result = app.call("/api/registry/probe", body, timeout=300)
    assert status == 200, result
    return result


def _call(app, path, payload=None, **kw):
    try:
        return app.call(path, payload, **kw)
    except fa.HttpError as exc:
        return exc.status, exc.body


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    counter = tmp_path / "runs.jsonl"
    (proj / "scaled.py").write_text(SCRIPT.format(counter=str(counter), ys=YS), encoding="utf-8")
    return proj, counter, tmp_path / "work"


@needs_worker
def test_same_script_same_stem_different_argv_never_mixes_across_the_whole_lifecycle(project):
    proj, counter, work = project
    base_id = figcapture.runtime_asset_id("scaled.py", "result")
    tree_before = sorted(p.relative_to(proj).as_posix() for p in proj.rglob("*"))
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        # ---- 探测：三份配置（无参数 / --scale 2 / --scale 3），各自一次执行
        plain = _probe(app)
        two = _probe(app, ["--scale", "2"])
        three = _probe(app, ["--scale", "3"])
        for result in (plain, two, three):
            assert result["registered"] is True and result["stems"] == ["result"]
        assert _runs(counter) == [[], ["--scale", "2"], ["--scale", "3"]]  # 脚本实际看到的
        assert "run_config" not in plain
        id_two = figcapture.runtime_asset_id("scaled.py", "result", two["run_config"])
        id_three = figcapture.runtime_asset_id("scaled.py", "result", three["run_config"])
        assert len({base_id, id_two, id_three}) == 3
        # 回应里没有参数原文，只有个数以外的不透明引用
        assert "--scale" not in json.dumps([two, three], ensure_ascii=False)

        # ---- 素材库把三份配置各列一条，互不覆盖
        _, listing = app.call("/api/runtime/assets", timeout=60)
        ids = {a["id"] for a in listing["assets"]}
        assert {base_id, id_two, id_three} <= ids

        # ---- 交替编辑：热态，脚本不再执行；每张图是自己的数据
        runs_after_probe = len(_runs(counter))
        for asset_id, scale in [(id_two, 2.0), (base_id, 1.0), (id_three, 3.0), (id_two, 2.0)]:
            assert _ylim(app.render(asset_id)) == pytest.approx(_expected_ylim(scale))
        assert len(_runs(counter)) == runs_after_probe

        # ---- 强制冷 worker：只重建这一份配置，且用的是它当初的 argv（不是最新的那份）
        status, _ = app.call("/api/engine/invalidate", {"id": id_two}, timeout=30)
        assert status == 200
        assert _ylim(app.render(id_two)) == pytest.approx(_expected_ylim(2.0))
        assert _runs(counter)[runs_after_probe:] == [["--scale", "2"]]  # 真实新尝试恰好一次，旧配置
        # 同脚本别的配置的热会话没被连带打断
        assert _ylim(app.render(id_three)) == pytest.approx(_expected_ylim(3.0))
        assert len(_runs(counter)) == runs_after_probe + 1

        # ---- 导出走同一道门：冷重放用这张图自己的 argv
        status, _ = app.call("/api/engine/invalidate", {"id": id_three}, timeout=30)
        before_export = len(_runs(counter))
        status, exported = _call(
            app,
            "/api/export",
            {
                "scope": "original",
                "filename": "three",
                "formats": ["pdf"],
                "original": {"figure_id": id_three, "source_kind": "vector", "overrides": []},
            },
            timeout=300,
        )
        assert status == 200 and exported["status"] == "done", exported
        assert _runs(counter)[before_export:] == [["--scale", "3"]]

        # ---- 对用户项目：运行配置不写进去（既有的登记与用户明确要的导出除外）；参数值哪儿都没有
        new_files = set(p.relative_to(proj).as_posix() for p in proj.rglob("*")) - set(tree_before)
        assert new_files <= {
            "tavotto_registry.json",
            "tavottofile",
            "tavottofile/export",
            "tavottofile/export/three.pdf",
        }, new_files
        assert "--scale" not in (proj / "tavotto_registry.json").read_text(encoding="utf-8")

    # ---- 保存重开：新进程，同一份数据目录，资产 id 里冻结的配置引用仍解析成当初的 argv
    before_reopen = len(_runs(counter))
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        assert _ylim(app.render(id_two)) == pytest.approx(_expected_ylim(2.0))
        assert _ylim(app.render(id_three)) == pytest.approx(_expected_ylim(3.0))
        assert _ylim(app.render(base_id)) == pytest.approx(_expected_ylim(1.0))
    assert sorted(map(tuple, _runs(counter)[before_reopen:])) == [
        (),
        ("--scale", "2"),
        ("--scale", "3"),
    ]


@needs_worker
def test_a_reference_this_machine_does_not_have_is_refused_not_run_without_arguments(project):
    """C26：另一台机器保存的文档里的配置引用，本机没有 → 明确拒绝（409 + 稳定码），脚本一次都不执行。"""
    proj, counter, work = project
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        _probe(app)  # 注册 scaled.py / result
        foreign = figcapture.runtime_asset_id("scaled.py", "result", "rc_0123456789ab")
        runs = len(_runs(counter))
        status, body = _call(app, "/api/engine/render", {"id": foreign, "patches": []}, timeout=120)
        assert status == 409 and body["code"] == "run_config_missing", body
        assert len(_runs(counter)) == runs  # 没有回落成空参数去跑
        status, body = _call(app, "/api/engine/preparation", {"id": foreign}, timeout=30)
        assert status == 409 and body["code"] == "run_config_missing", body


@needs_worker
def test_an_invalid_argv_is_rejected_at_the_boundary(project):
    proj, counter, work = project
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        for bad in ("--scale 2", [1, 2], ["a\x00b"], {"a": 1}):
            status, body = _call(
                app, "/api/registry/probe", {"script": "scaled.py", "argv": bad}, timeout=60
            )
            assert status == 400 and body["code"] == "invalid_argv", (bad, body)
        status, body = _call(
            app, "/api/engine/preparation-sessions", {"script": "scaled.py", "argv": "--scale 2"}
        )
        assert status == 400 and body["code"] == "invalid_argv", body
        assert _runs(counter) == []  # 一次都没跑


@needs_worker
def test_a_sensitive_argument_is_masked_everywhere_and_not_kept_across_restarts(project):
    proj, counter, work = project
    secret_argv = ["--scale", "2", "--token", SECRET]
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        result = _probe(app, secret_argv, argv_sensitive=True)
        assert result["registered"] is True
        asset_id = figcapture.runtime_asset_id("scaled.py", "result", result["run_config"])
        assert _runs(counter) == [secret_argv]  # 脚本拿到了真值
        assert _ylim(app.render(asset_id)) == pytest.approx(_expected_ylim(2.0))
        log = app.server_log()
    # 磁盘 / 日志 / 回应里都没有明文
    assert SECRET not in log
    for path in (work / "data").rglob("*"):
        if path.is_file():
            try:
                assert SECRET not in path.read_text(encoding="utf-8", errors="ignore"), path
            except OSError:
                continue
    # 重启后：敏感值不在了 → 显式拒绝，不是空参数
    before = len(_runs(counter))
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        status, body = _call(
            app, "/api/engine/render", {"id": asset_id, "patches": []}, timeout=120
        )
        assert status == 409 and body["code"] == "run_config_secret_missing", body
        assert len(_runs(counter)) == before


@needs_worker
def test_the_preparation_session_carries_the_arguments_and_keeps_configurations_apart(project):
    """会话合同（T01）接收 argv：不同 argv 是不同会话；公开报告只带个数与引用；执行看到的是精确 token。"""
    proj, counter, work = project
    sessions = "/api/engine/preparation-sessions"
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        status, a = app.call(
            sessions, {"script": "scaled.py", "argv": ["--scale", "2"]}, timeout=120
        )
        status_b, b = app.call(
            sessions, {"script": "scaled.py", "argv": ["--scale", "3"]}, timeout=120
        )
        assert status == 201 and status_b == 201 and a["session_id"] != b["session_id"]
        assert "--scale" not in json.dumps([a, b], ensure_ascii=False)
        assert a["target"]["argv_count"] == 2 and a["target"]["run_config"]
        assert a["target"]["run_config"] != b["target"]["run_config"]
        (run,) = [x for x in a["actions"] if x["kind"] == "run"]
        assert run["impact"]["script_arguments"] == 2  # 授权绑定的影响摘要：个数，不是值
        assert _runs(counter) == []
        status, claimed = app.call(
            f"{sessions}/{a['session_id']}/actions",
            {"action_id": run["id"], "expected_config_revision": a["config_revision"]},
            timeout=60,
        )
        assert status == 202
        deadline = time.time() + 300
        while True:
            _, report = app.call(f"{sessions}/{a['session_id']}", timeout=30)
            if report["phase"] in ("completed", "partial", "action_required"):
                break
            assert time.time() < deadline, report
            time.sleep(0.2)
        assert report["phase"] == "completed", report
        assert _runs(counter) == [["--scale", "2"]]
        asset_id = figcapture.runtime_asset_id("scaled.py", "result", a["target"]["run_config"])
        assert _ylim(app.render(asset_id)) == pytest.approx(_expected_ylim(2.0))
        assert _runs(counter) == [["--scale", "2"]]  # 进编辑复用同一次捕获，不再执行


NAMED_SCRIPT = """\
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

args = sys.argv[1:]
name = args[args.index("--name") + 1]
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [1, len(name)])
fig.savefig(name + ".pdf")
"""


@needs_worker
def test_configurations_whose_outputs_have_different_names_all_stay_editable(tmp_path):
    """图名由参数决定（`--name alpha` / `--name beta`）：第二份配置的登记并进注册表，不换掉第一份的——
    否则 alpha 那张图会当场失去编辑入口（注册表是 stem 归属的唯一权威）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "named.py").write_text(NAMED_SCRIPT, encoding="utf-8")
    with fa.running_app(proj, tmp_path / "work", env_overrides=ENV) as app:
        runs = {}
        for name in ("alpha", "beta"):
            status, result = app.call(
                "/api/registry/probe",
                {"script": "named.py", "argv": ["--name", name]},
                timeout=300,
            )
            assert status == 200 and result["registered"] is True, result
            assert result["stems"] == [name]
            runs[name] = figcapture.runtime_asset_id("named.py", name, result["run_config"])
        _, listing = app.call("/api/runtime/assets", timeout=60)
        assert set(runs.values()) <= {a["id"] for a in listing["assets"]}
        for name, asset_id in runs.items():
            axes = next(
                e for e in app.render(asset_id)["manifest"]["elements"] if e["role"] == "axes"
            )
            ylim = next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")
            margin = 0.05 * (len(name) - 1)  # 各自的数据：alpha 5 个字母、beta 4 个
            assert ylim == pytest.approx([1 - margin, len(name) + margin])


@needs_worker
def test_cold_runtime_render_reports_that_the_frozen_arguments_were_rejected(project):
    """The ordinary render adapter needs the same parse facts as the probe adapter."""
    proj, _counter, work = project
    with fa.running_app(proj, work, env_overrides=ENV) as app:
        original = _probe(app, ["--scale", "2"])
        asset_id = figcapture.runtime_asset_id("scaled.py", "result", original["run_config"])
        (proj / "scaled.py").write_text(
            "import argparse\np = argparse.ArgumentParser()\n"
            "p.add_argument('--scale', choices=['3'])\np.parse_args()\n",
            encoding="utf-8",
        )
        app.call("/api/engine/invalidate", {"id": asset_id}, timeout=30)
        status, body = _call(
            app, "/api/engine/render", {"id": asset_id, "patches": []}, timeout=120
        )
        assert status == 500, body
        assert body["code"] == "script_needs_arguments"
        assert body["params"] == {"argv_count": "2", "parse_kind": "invalid_value"}
