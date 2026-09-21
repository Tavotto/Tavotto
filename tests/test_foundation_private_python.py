"""统一实施包 U05：私有 Python 的首开场景（FirstOpenBench，经产品的 HTTP 入口——进程内 test_client）。

为什么在进程内而不是 `python -m tavotto` 子进程：这组场景的**前提**是「这台机器没有任何可用的
Python」——发现链末端（`bootstrap.find_base_python` / `pool.resolve_worker_python`）的输入。子进程形态
下这个输入只能靠清 PATH + 藏解释器去凑，而 06 §2 明说那不是诚实的「无系统 Python」；进程内把
发现链末端置空是**输入**，之后的每一步（准备接口的 `needs_input` 投影、`POST /api/engine/dependencies/plan`
+ `/prepare` 的一次授权、供应、代事务、渲染）都是产品代码。会话认证的旁路是 pytest 的 test_client
（ADR 0008 三个旁路之一）。这仍是**工程验证**：无系统 Python 的目标资格在 ADR 0064 第三档。

| case | 场景 | 预期产品结果 |
|---|---|---|
| FO24 | 离线，但校验过的私有 Python 归档缓存齐备（本地供应服务没开 + 死代理）：一次授权后零请求准备好、出图 | automatic |
| FO25 | 离线且无缓存：门以「将下载 N 字节」回来 → 授权 → `private_python_offline`，没有登记任何一代、没有目录，再准备仍是 `needs_input` | safe_stop |
| FO26 | 供应来源被篡改：hash 不符 → 拒绝，旧的 active 环境原样可用（渲染照常） | safe_stop |

每条经真实入口：`POST /api/engine/preparation` → `needs_input`（`required_input.code ==
dependency_preparation_required`，`targets[受管].private_python` 带 `download_bytes`）→ `/plan` → `/prepare`
→ 终态 → 再 `/preparation` → `ready` / 仍 `needs_input` → 渲染并核对图内值（真值 [42, 84, 126]）。
safe_stop 的通过**不进**自动兼容成功的分子。私有 Python 的归档是 `tests/support/private_python.fake_archive`
（替身 exec 宿主的 worker 解释器）——供应器的状态机、hash、目录纪律全是真的，只有解释器的字节是替身的。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from support import foundation_harness as fh, private_python as pp_support
from support.dependency_repair import WORKER_PY, build_wheel, needs_worker
from support.private_python import LoopbackServer, closed_port_url, fake_archive, source_from
from tavotto import app as m
from tavotto.engine import (
    bootstrap,
    depplan,
    deprepair,
    envlease,
    managedenv,
    pool as engine_pool,
    preparation,
    privatepython,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "foundation" / "private_python"
ALPHA = ("tavotto-test-alpha", "tavotto_test_alpha")
BETA = ("tavotto-test-beta", "tavotto_test_beta")
ENTRY = "http-inprocess"

pytest_plugins = ("support.dependency_repair",)
pytestmark = needs_worker


# ---------------------------------------------------------------- 装置


@pytest.fixture(autouse=True)
def _clean(clean_state, offline_managed_env, tmp_path, monkeypatch):
    envlease.reset_for_tests()
    depplan.reset_cache()
    privatepython.reset_for_tests()
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("TAVOTTO_PRIVATE_PYTHON", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("TAVOTTO_WORKER_PYTHON", raising=False)
    monkeypatch.delenv("MM_WORKER_PYTHON", raising=False)
    # 干净机器：发现链末端什么都找不到——直到 Tavotto 自己建出受管环境（那之后真实的解析链接管：
    # 它记住了这个项目的受管环境）
    monkeypatch.setattr(bootstrap, "find_base_python", lambda accept=None: None)
    monkeypatch.setattr(deprepair, "_base_python", None)
    monkeypatch.setattr(deprepair, "_base_python_known", False)
    real_resolve = engine_pool.resolve_worker_python

    def _resolve(figures_dir=None, *, script=None, discover=True):
        if figures_dir is not None and managedenv.python_of(str(figures_dir)):
            return real_resolve(figures_dir, script=script, discover=discover)
        raise engine_pool._no_python_error()

    monkeypatch.setattr(engine_pool, "resolve_worker_python", _resolve)
    yield
    envlease.reset_for_tests()
    depplan.reset_cache()
    privatepython.reset_for_tests()


@pytest.fixture
def client(tmp_path, monkeypatch):
    m.app.config["TESTING"] = True
    m.reset_projects()
    monkeypatch.setattr(m, "BAKED_DIR", tmp_path / "_baked")
    monkeypatch.setattr(m, "BAKED_PATH", tmp_path / "_legacy_baked.json")
    monkeypatch.setattr(m, "CACHE_DIR", tmp_path / "_cache")
    preparation.SERVICE.reset_for_tests()
    yield m.app.test_client()
    for pid, ctx in list(m.PROJECTS.items()):
        m.close_project(pid, wait=True)
    m.reset_projects()
    preparation.SERVICE.reset_for_tests()


@pytest.fixture
def house(tmp_path, monkeypatch) -> Path:
    dest = tmp_path / "house"
    build_wheel(dest, name=ALPHA[0], import_name=ALPHA[1], version="1.0")
    build_wheel(dest, name=BETA[0], import_name=BETA[1], version="1.0")
    monkeypatch.setenv("PIP_FIND_LINKS", str(dest))
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    return dest


@pytest.fixture
def archive(tmp_path):
    """假 pbs 归档（替身 exec 宿主的 worker 解释器）+ 它的 sha256 / python_rel。"""
    launches = tmp_path / "launches.log"
    path, sha, rel = fake_archive(tmp_path / "serve", host_python=WORKER_PY, launches_log=launches)
    return path, sha, rel


def _source(archive, url):
    path, sha, rel = archive
    return source_from(path, sha, rel, url=url, version=pp_support.host_python_version(WORKER_PY))


def _project(tmp_path) -> Path:
    proj = tmp_path / "paper"
    shutil.copytree(FIXTURE, proj)
    _native_reference(proj, tmp_path)
    return proj


def _native_reference(proj: Path, tmp: Path, *, modules=(ALPHA[1],)) -> None:
    """夹具的原生参考：用户在别的机器上跑过一次脚本，磁盘上有原件（素材库据此列出面板）。**不是**产品
    路径，也不装任何东西：脚本要的模块以 `.py` 放在临时目录里经 `PYTHONPATH` 提供（与 wheel 的模块体
    逐字相同），项目里不留 venv（这台机器仍然「没有 Python」）。"""
    mods = tmp / "native-mods"
    mods.mkdir(parents=True, exist_ok=True)
    for name in modules:
        (mods / f"{name}.py").write_text(f'VALUE = 42\nNAME = "{name}"\n', encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(mods),
        "MPLCONFIGDIR": str(tmp / "mpl"),
        "MPLBACKEND": "Agg",
    }
    for name in ("LD_LIBRARY_PATH", "SYSTEMROOT", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    proc = subprocess.run(
        [WORKER_PY, "figure.py"],
        cwd=proj,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    shutil.rmtree(proj / "__pycache__", ignore_errors=True)


def _open(client, root: Path) -> str:
    resp = client.post("/api/projects/open", json={"path": str(root)})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def _prepare(client, panel_id: str, timeout: float = 60.0) -> dict:
    resp = client.post("/api/engine/preparation", json={"id": panel_id})
    assert resp.status_code == 202, resp.get_json()
    plan_id = resp.get_json()["plan"]["plan_id"]
    deadline = time.time() + timeout
    while True:
        body = client.get(f"/api/engine/preparation/{plan_id}").get_json()
        if body["result"]["status"] in preparation.TERMINAL:
            return body
        assert time.time() < deadline, body
        time.sleep(0.05)


def _authorize(client, script: str, timeout: float = 600.0) -> tuple[dict, dict]:
    """一次授权 = 公开的两个 POST：绑定计划 → 异步执行；回 (计划载荷, 终态进度)。"""
    resp = client.post("/api/engine/dependencies/plan", json={"script": script})
    assert resp.status_code == 200, resp.get_json()
    plan = resp.get_json()["plan"]
    resp = client.post("/api/engine/dependencies/prepare", json={"plan_id": plan["plan_id"]})
    assert resp.status_code == 200, resp.get_json()
    deadline = time.time() + timeout
    while True:
        rec = deprepair.progress(plan["plan_id"])
        if rec["state"] in (
            deprepair.STATE_DONE,
            deprepair.STATE_FAILED,
            deprepair.STATE_CANCELLED,
        ):
            return plan, rec
        assert time.time() < deadline, rec
        time.sleep(0.1)


def _render(client, panel_id: str) -> dict:
    resp = client.post("/api/engine/render", json={"id": panel_id, "patches": []})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _title(render: dict) -> str:
    title = next(e for e in render["manifest"]["elements"] if e["role"] == "title")
    return next(f["value"] for f in title["editable"] if f["prop"] == "text")


def _ylim(render: dict) -> list[float]:
    axes = next(e for e in render["manifest"]["elements"] if e["role"] == "axes")
    return next(f["value"] for f in axes["editable"] if f["prop"] == "ylim")


def _expected_ylim(ys: list[float]) -> list[float]:
    margin = 0.05 * (max(ys) - min(ys))
    return [min(ys) - margin, max(ys) + margin]


def _case(case_id: str) -> tuple[dict, dict]:
    ledger = fh.load_ledger()
    case = next(c for c in ledger["cases"] if c["case_id"] == case_id)
    binding = fh.binding_from_environment(
        ledger=ledger, entry=case["entry"], fixture=case["fixture"]
    )
    return case, binding


def _record(case_id, binding, outcome, observed, evidence, out_dir):
    record = fh.ResultRecord(
        case_id=case_id,
        binding=binding,
        product_outcome=outcome,
        test_verdict="pass",
        observed=observed,
        evidence=tuple(evidence),
    )
    path = fh.write_result(record, fh.results_dir() or out_dir)
    assert path.is_file()


def _door(body: dict) -> dict:
    assert body["result"]["status"] == preparation.STATUS_NEEDS_INPUT, body["result"]
    door = body["result"]["required_input"]
    assert door["code"] == deprepair.ERROR_PREPARATION_REQUIRED
    return door


def _managed_target(door: dict) -> dict:
    return next(t for t in door["targets"] if t["kind"] == deprepair.TARGET_MANAGED)


# ================================================================ FO24：离线、缓存齐备


def test_fo24_cached_private_python_prepares_offline_and_renders(
    client, tmp_path, house, archive, monkeypatch
):
    """FO24：校验过的归档已在缓存里，网络断着（本地供应服务没开、死代理）：门说「不用下载」（cached、
    0 字节、network_required=false），一次授权零请求准备好 → ready → 图内值 == 真值。"""
    case, binding = _case("FO24")
    src = _source(archive, closed_port_url(archive[0].name))  # 没人听的端口：真要联网就会失败
    monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src)
    privatepython.downloads_dir().mkdir(parents=True)
    shutil.copy2(archive[0], privatepython.archive_path(src))
    project = _project(tmp_path)
    _open(client, project)
    body = _prepare(client, "figure.pdf")
    door = _door(body)
    private = _managed_target(door)["private_python"]
    assert private["required"] is True and private["cached"] is True
    assert private["download_bytes"] == 0 and private["network_required"] is False
    assert door["private_python"]["cached"] is True
    plan, rec = _authorize(client, "figure.py")
    assert plan["private_python"]["cached"] is True and plan["replan"] is True
    assert rec["state"] == deprepair.STATE_DONE, rec
    assert privatepython.python_of(src) and managedenv.python_of(project)
    body = _prepare(client, "figure.pdf")
    assert body["result"]["status"] == preparation.STATUS_READY, body["result"]
    assert body["plan"]["environment"]["source"] == engine_pool.SOURCE_MANAGED_PROJECT
    render = _render(client, "figure.pdf")
    truth = json.loads((FIXTURE / "truth.json").read_text(encoding="utf-8"))
    assert _title(render) == truth["title"]
    assert _ylim(render) == pytest.approx(_expected_ylim(truth["y"]))
    observed = {
        "private_python": src.id,
        "cached": True,
        "download_bytes_disclosed": 0,
        "generation": rec["result"]["generation"],
        "title": _title(render),
        "ylim": _ylim(render),
    }
    _record(
        case["case_id"],
        binding,
        "automatic",
        observed,
        ["零请求（供应来源是没人听的端口）"],
        tmp_path,
    )


# ================================================================ FO25：离线、无缓存


def test_fo25_offline_without_cache_is_a_safe_stop_that_builds_nothing(
    client, tmp_path, house, archive, monkeypatch
):
    """FO25：门以「将下载 N 字节」回来 → 授权 → 供应失败 `private_python_offline` → 没登记任何一代、没有
    runtime 目录、没有 `.part`；再准备仍是 `needs_input`（不假装 ready，静态素材路径不受影响）。"""
    case, binding = _case("FO25")
    src = _source(archive, closed_port_url(archive[0].name))
    monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src)
    project = _project(tmp_path)
    _open(client, project)
    body = _prepare(client, "figure.pdf")
    door = _door(body)
    private = _managed_target(door)["private_python"]
    assert private["required"] is True and private["cached"] is False
    assert private["download_bytes"] == src.size and private["network_required"] is True
    assert door["plan"]["requirements"] == [f"{ALPHA[0]}==1.0"]
    text = json.dumps(body, ensure_ascii=False)
    for needle in (str(tmp_path), str(project)):
        assert needle not in text, "投影里带了机器路径"
    t0 = time.monotonic()
    plan, rec = _authorize(client, "figure.py")
    assert plan["private_python"]["download_bytes"] == src.size
    assert rec["state"] == deprepair.STATE_FAILED and rec["code"] == privatepython.ERROR_OFFLINE
    assert time.monotonic() - t0 < 120  # 有界
    assert managedenv.generations(project) == {} and managedenv.python_of(project) is None
    assert privatepython.python_of(src) is None
    assert not privatepython.runtimes_dir().exists() or not any(
        privatepython.runtimes_dir().iterdir()
    )
    assert not list(privatepython.downloads_dir().glob("*.part"))
    body = _prepare(client, "figure.pdf")
    _door(body)  # 仍是需要输入，不是 ready / error
    observed = {
        "code": rec["code"],
        "download_bytes_disclosed": src.size,
        "generations": [],
        "runtime_present": False,
    }
    _record(
        case["case_id"],
        binding,
        "safe_stop",
        observed,
        ["连接被拒（没人听的端口 + 死代理）"],
        tmp_path,
    )


# ================================================================ FO26：来源被篡改


def test_fo26_corrupted_source_is_refused_and_the_active_environment_stays(
    client, tmp_path, house, archive, monkeypatch
):
    """FO26：先在一份好的私有 Python 上准备好（渲染出真值）；锁换到另一份、来源被篡改 → hash 不符 → 拒绝；
    旧的 active 环境原样可用、渲染照常、没有新 runtime 目录。"""
    case, binding = _case("FO26")
    with LoopbackServer(tmp_path / "serve") as server:
        src_a = _source(archive, server.url(archive[0].name))
        monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src_a)
        project = _project(tmp_path)
        _open(client, project)
        _door(_prepare(client, "figure.pdf"))
        plan, rec = _authorize(client, "figure.py")
        assert rec["state"] == deprepair.STATE_DONE, rec
        assert server.requests == [f"/{archive[0].name}"]
        gen_a = managedenv.active_generation(project)
        assert _prepare(client, "figure.pdf")["result"]["status"] == preparation.STATUS_READY
        truth = json.loads((FIXTURE / "truth.json").read_text(encoding="utf-8"))
        assert _ylim(_render(client, "figure.pdf")) == pytest.approx(_expected_ylim(truth["y"]))
    # 锁换版本（另一份字节 → 新 id），来源被篡改；脚本多要一个包 → 要建新的一代
    launches_b = tmp_path / "launches-b.log"
    path_b, sha_b, rel_b = fake_archive(
        tmp_path / "serve-b", host_python=WORKER_PY, launches_log=launches_b
    )
    (project / "requirements.txt").write_text(
        f"{ALPHA[0]}==1.0\n{BETA[0]}==1.0\n", encoding="utf-8"
    )
    (project / "figure.py").write_text(
        (project / "figure.py")
        .read_text(encoding="utf-8")
        .replace(
            "import tavotto_test_alpha  # noqa: E402",
            "import tavotto_test_alpha  # noqa: E402\nimport tavotto_test_beta  # noqa: E402",
        ),
        encoding="utf-8",
    )
    with LoopbackServer(tmp_path / "serve-b") as server_b:
        server_b.mode = "corrupt"
        src_b = source_from(
            path_b, sha_b, rel_b, url=server_b.url(path_b.name), version=src_a.version
        )
        monkeypatch.setattr(privatepython, "source_for", lambda target=None, lock=None: src_b)
        # 锁换版本只会随升级发生（进程重启）：这里清掉进程内的基础解释器缓存来表达「重启之后」
        deprepair.reset_state()
        body = _prepare(client, "figure.pdf")
        door = _door(body)
        assert _managed_target(door)["private_python"]["id"] == src_b.id
        plan, rec = _authorize(client, "figure.py")
        assert rec["state"] == deprepair.STATE_FAILED
        assert rec["code"] == privatepython.ERROR_HASH_MISMATCH, rec
        assert server_b.requests == [f"/{path_b.name}"]
    assert managedenv.active_generation(project) == gen_a
    assert set(managedenv.generations(project)) == {gen_a}
    assert privatepython.python_of(src_b) is None
    assert sorted(p.name for p in privatepython.runtimes_dir().iterdir()) == [src_a.id]
    if os.name != "nt":
        assert not launches_b.exists()  # 坏归档的解释器一次都没起
    observed = {
        "code": rec["code"],
        "active_generation_before": gen_a,
        "active_generation_after": managedenv.active_generation(project),
        "runtimes": [src_a.id],
    }
    _record(
        case["case_id"], binding, "safe_stop", observed, ["篡改一个字节的本地供应服务"], tmp_path
    )
