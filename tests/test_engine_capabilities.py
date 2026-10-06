"""T10：引擎能力协商（`engine/capabilities.py`）与「同一个采用服务」。

* `/api/version` 宣告的 `features` 是新客户端发新字段之前问的那张表——前端 / MCP 桥各自留着名字的镜像，
  这里两侧对拍；
* 环境建议的「使用」只有一个实现（`envadvice.adopt_candidate`）：HTTP `PATCH` 与 MCP `adopt_environment=`
  都委派它，真 venv + 真 worker 证明 MCP 那一路采用之后实际就在那个环境里跑，记录与界面那一路一模一样。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from support import envworld
from support.envworld import real_venv
from tavotto.engine import capabilities, envadvice, pool as engine_pool, projectenv, userenvs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto_mcp import bridge  # noqa: E402

try:
    WORKER_PY = engine_pool.find_worker_python()
except engine_pool.WorkerError:
    WORKER_PY = None

needs_worker = pytest.mark.skipif(
    WORKER_PY is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.delenv("TAVOTTO_ENV_ADOPTION", raising=False)
    projectenv.reset_cache()
    userenvs.reset_cache()
    engine_pool.reset_worker_python()
    bridge.reset_root_authority()
    bridge.sessions().clear()
    yield
    bridge.sessions().clear()
    bridge.reset_root_authority()
    engine_pool.shutdown_all(wait=True)
    projectenv.reset_cache()
    userenvs.reset_cache()
    engine_pool.reset_worker_python()


@pytest.fixture
def client():
    from tavotto import app as m

    m.app.config["TESTING"] = True
    c = m.app.test_client()
    yield c
    m.reset_projects()


def _open(client, root) -> str:
    resp = client.post("/api/projects/open", json={"path": str(root)})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


# --------------------------------------------------------------------- 能力表
def test_the_version_endpoint_advertises_every_capability_and_nothing_private(client):
    from tavotto import app as tavotto_app

    body = client.get("/api/version").get_json()
    assert body["features"] == [
        tavotto_app.DESKTOP_REMOTE_WINDOW_FEATURE,
        *capabilities.FEATURES,
    ]
    # 公开端点：只说「这一版会什么」
    assert set(body) == {"build", "version", "features"}


def test_capability_names_are_stable_kebab_tokens():
    assert len(set(capabilities.FEATURES)) == len(capabilities.FEATURES)
    for name in capabilities.FEATURES:
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", name), name
    assert capabilities.features() == list(capabilities.FEATURES)
    assert capabilities.features() is not capabilities.features()


def test_the_frontend_and_the_plugin_mirror_the_engine_names():
    """同源对：前端 / 插件各留一份名字（旧引擎上它们 import 不到引擎的常量），漂了就是「永远说太旧」。"""
    api = (ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")

    def ts_const(name: str) -> str:
        m = re.search(rf"export const {name} = '([^']*)'", api)
        assert m, name
        return m.group(1)

    assert ts_const("ENGINE_FEATURE_SCRIPT_ARGV") == capabilities.SCRIPT_ARGV
    assert ts_const("ENGINE_CAPABILITY_MISSING") == capabilities.ERROR_CAPABILITY_MISSING
    assert bridge.SCRIPT_ARGV_FEATURE == capabilities.SCRIPT_ARGV
    assert bridge.ENVIRONMENT_ADOPTION_FEATURE == capabilities.ENVIRONMENT_ADOPTION
    assert bridge.CAPABILITY_MISSING == capabilities.ERROR_CAPABILITY_MISSING
    for lang in ("zh-CN", "en-US"):
        errors = json.loads(
            (ROOT / "web" / "src" / "i18n" / "locales" / lang / "errors.json").read_text("utf-8")
        )
        assert capabilities.ERROR_CAPABILITY_MISSING in errors["backend"], lang


# --------------------------------------------------------------------- 同一个采用服务
def test_http_and_mcp_adoption_delegate_to_the_same_service(client, tmp_path, monkeypatch):
    """C01 / C25：两条入口都委派 `envadvice.adopt_candidate`，不各自写一份判据。"""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (root / "Fig.pdf").write_bytes(b"%PDF-1.4\n")
    (root / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig.py": {"entry": "main", "stems": ["Fig"]}}}), encoding="utf-8"
    )
    calls: list[tuple] = []

    def adopt(root_, script, candidate, *, expected_generation="", module=""):
        calls.append((str(root_), script, candidate, expected_generation))
        return {"ok": True, "python_version": "3.12.0"}

    monkeypatch.setattr(envadvice, "adopt_candidate", adopt)
    pj = _open(client, root)
    resp = client.patch(
        "/api/engine/environment",
        json={
            "scope": "project",
            "candidate": "c1",
            "expected_generation": "g",
            "script": "fig.py",
        },
        query_string={"pj": pj},
    )
    assert resp.status_code == 200, resp.get_json()

    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    monkeypatch.setattr(bridge.engine_pool, "get", lambda *a, **k: _NoRender())
    with pytest.raises(
        bridge.BridgeError
    ):  # 采用之后开图（这里的假 worker 不画图），采用这一步已经发生
        bridge.open_figure(str(root), adopt_environment="c1", expected_environment_generation="g")
    real = str(Path(root).resolve())
    assert calls == [(str(root), "fig.py", "c1", "g"), (real, "fig.py", "c1", "g")]


class _NoRender:
    def override(self, *a, **k):
        raise engine_pool.WorkerError("假 worker 不画图", code="render_failed")


@pytest.mark.parametrize(
    "case, code, status",
    [
        ("gone", "environment_candidate_gone", 400),
        ("locked", "environment_locked", 409),
    ],
)
def test_a_refused_adoption_writes_nothing(tmp_path, monkeypatch, case, code, status):
    root = tmp_path / "proj"
    root.mkdir()
    if case == "locked":
        monkeypatch.setattr(engine_pool, "explicit_worker_python", lambda: ("/x/python", "env"))
    with pytest.raises(envadvice.AdoptionRefused) as exc:
        envadvice.adopt_candidate(root, None, "0123456789abcdef")
    assert (exc.value.code, exc.value.status) == (code, status)
    assert projectenv.remembered_record(root) is None


@needs_worker
def test_mcp_adoption_runs_the_figure_in_exactly_the_chosen_environment(tmp_path, monkeypatch):
    """E01 经 MCP：Agent 按用户的选择传 `adopt_environment` → 记录与界面那一路相同（automatic=False、
    trigger=recommended、带环境代），开图的 worker 真的在那个 venv 里（独立身份对拍）。"""
    import subprocess

    root = tmp_path / "proj"
    root.mkdir()
    (root / "fig.py").write_text(
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots(figsize=(2, 2))\nax.plot([0, 1], [0, 1])\nfig.savefig('Fig.pdf')\n",
        encoding="utf-8",
    )
    (root / "tavotto_registry.json").write_text(
        json.dumps({"scripts": {"fig.py": {"entry": "__main__", "stems": ["Fig"]}}}),
        encoding="utf-8",
    )
    subprocess.run([WORKER_PY, "fig.py"], cwd=root, check=True, capture_output=True)
    venv_python = real_venv(root, ".venv", python=WORKER_PY)
    monkeypatch.setenv(bridge.ROOTS_ENV, str(tmp_path))
    rec = envadvice.recommend(root, "fig.py")
    row = next(c for c in rec["candidates"] if c["python_relative"] == ".venv/bin/python")

    out = bridge.open_figure(
        str(root),
        adopt_environment=row["id"],
        expected_environment_generation=row["generation"],
    )
    assert out["ok"] is True and out["adopted_environment"]["candidate"] == row["id"]
    record = projectenv.remembered_record(root)
    assert record["automatic"] is False and record["trigger"] == "recommended"
    assert record["generation"] == row["generation"]
    worker = engine_pool.get("fig.py", str(Path(root).resolve()), "__main__")
    assert worker.python_source == engine_pool.SOURCE_PROJECT_VENV
    assert engine_pool.same_python(worker.python, venv_python)
    ident = envworld.python_identity(venv_python)
    assert Path(ident["prefix"]).resolve() == (root / ".venv").resolve()
