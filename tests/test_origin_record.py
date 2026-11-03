"""来源记录（ADR 0139 PR3 第一片）：只记录事实 + 只读展示，不影响任何判定。

三层：记录模块本身（有界 / 原子 / fail-open / 隐私）→ 插件 handoff.py 真跑脚本（记的是脚本进程的实际值，
只在 ran:true 时写）→ 准入响应（有记录时带 origin，判定与无记录时逐字相同）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tavotto.engine import atomicio, config, originrecord
from test_savefig_capture_params import WORKER_PY, needs_worker
from test_script_backed_policy import (  # noqa: F401  (fixture 经导入使用)
    identity,
    opened,
    project,
    render,
    sb_env,
)

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_HANDOFF = ROOT / "codex-plugin" / "skills" / "tavotto-figure" / "scripts" / "handoff.py"

FACTS = {
    "python_version": "3.11.4",
    "matplotlib_version": "3.8.2",
    "backend": "agg",
    "figure_dpi": 100.0,
}


def sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path / "data"


def make_png(root: Path, name: str, payload: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + payload)
    return path


def put(root: Path, name: str, payload: bytes = b"a", facts=FACTS) -> tuple[Path, str]:
    png = make_png(root, name, payload)
    sha = sha_of(png)
    assert originrecord.record(
        project_root=root,
        source_id=name,
        bytes_sha256=sha,
        size_bytes=png.stat().st_size,
        facts=facts,
        script_rel="fig.py",
        script_sha1="0" * 40,
    )
    return png, sha


# ------------------------------------------------------------------ 记录模块


def test_roundtrip_is_keyed_by_the_bytes_not_the_path(data, tmp_path):
    root = tmp_path / "proj"
    png, sha = put(root, "a.png")
    got = originrecord.lookup(sha, root)
    assert got == {
        "python_version": "3.11.4",
        "matplotlib_version": "3.8.2",
        "backend": "agg",
        "figure_dpi": 100.0,
        "recorded_by": "handoff",
    }
    # 同字节换个名字/目录：照样命中（键是字节，不是路径）；改一个字节：不命中
    moved = make_png(tmp_path / "other", "zzz.png", b"a")
    assert originrecord.lookup(sha_of(moved)) == got
    png.write_bytes(png.read_bytes() + b"x")
    assert originrecord.lookup(sha_of(png), root) is None


def test_record_file_is_one_document_per_byte_identity(data, tmp_path):
    root = tmp_path / "proj"
    _, sha = put(root, "a.png")
    doc = json.loads((data / "origin" / f"{sha}.json").read_text(encoding="utf-8"))
    entry = doc["entries"][0]
    assert doc["version"] == 1 and doc["bytes_sha256"] == sha
    assert entry["source_id"] == "a.png" and entry["size_bytes"] > 0
    assert entry["project"] == config.normalize_path_identity(str(root.resolve()))
    assert entry["script"] == {"rel": "fig.py", "sha1": "0" * 40}
    assert entry["recorded_by"] == "handoff" and entry["recorded_at"]


def test_store_is_bounded_and_prunes_dead_records_first(data, tmp_path, monkeypatch):
    monkeypatch.setattr(originrecord, "MAX_RECORDS", 3)
    root = tmp_path / "proj"
    pngs = [put(root, f"{i}.png", bytes([i]))[0] for i in range(3)]
    # 0 号原件字节变了 → 它的记录已失效，第四条写入时应先清它
    pngs[0].write_bytes(b"changed")
    live_sha = [sha_of(p) for p in pngs[1:]]
    _, sha4 = put(root, "3.png", b"\x09")
    files = list((data / "origin").glob("*.json"))
    assert len(files) == 3
    assert all(originrecord.lookup(s, root) for s in [*live_sha, sha4])
    # 全部仍有效时也不超上限：挤掉最旧的，新的一定在
    _, sha5 = put(root, "4.png", b"\x0a")
    assert len(list((data / "origin").glob("*.json"))) == 3
    assert originrecord.lookup(sha5, root)


def test_write_goes_through_atomicio_and_a_failed_write_keeps_the_old_record(
    data, tmp_path, monkeypatch
):
    root = tmp_path / "proj"
    png, sha = put(root, "a.png")
    before = (data / "origin" / f"{sha}.json").read_bytes()
    calls = []
    real = atomicio.write_bytes
    monkeypatch.setattr(atomicio, "write_bytes", lambda p, d, **kw: (calls.append(p), real(p, d)))
    other = FACTS | {"backend": "pdf"}
    assert originrecord.record(
        project_root=root, source_id="a.png", bytes_sha256=sha, size_bytes=1, facts=other
    )
    assert len(calls) == 1 and Path(calls[0]).parent == data / "origin"

    def boom(path, data_, **kw):
        raise atomicio.AtomicWriteError("write_failed", "boom", Path(path))

    monkeypatch.setattr(atomicio, "write_bytes", boom)
    kept = (data / "origin" / f"{sha}.json").read_bytes()
    assert not originrecord.record(
        project_root=root, source_id="a.png", bytes_sha256=sha, size_bytes=1, facts=FACTS
    )
    assert (data / "origin" / f"{sha}.json").read_bytes() == kept != before
    assert not list((data / "origin").glob("*.tmp*"))
    assert png.exists()


@pytest.mark.parametrize("content", ["{not json", "[]", '{"version": 9}', '{"version": 1}', ""])
def test_a_damaged_record_reads_as_no_record_and_never_raises(data, tmp_path, content):
    """fail-open：这是展示线索不是安全闸（与写回闸的 fail-closed 相反）。"""
    root = tmp_path / "proj"
    _, sha = put(root, "a.png")
    (data / "origin" / f"{sha}.json").write_text(content, encoding="utf-8")
    assert originrecord.lookup(sha, root) is None
    # 坏文件不挡路：同一字节再记一次能恢复
    assert originrecord.record(
        project_root=root, source_id="a.png", bytes_sha256=sha, size_bytes=1, facts=FACTS
    )
    assert originrecord.lookup(sha, root)


def test_tampered_entries_with_foreign_fields_are_not_displayed(data, tmp_path):
    root = tmp_path / "proj"
    _, sha = put(root, "a.png")
    path = data / "origin" / f"{sha}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["entries"][0]["matplotlib_version"] = "/usr/bin/python3"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert originrecord.lookup(sha, root) is None


def test_no_interpreter_path_or_environment_is_ever_stored(data, tmp_path):
    root = tmp_path / "proj"
    _, sha = put(root, "a.png")
    text = (data / "origin" / f"{sha}.json").read_text(encoding="utf-8")
    for secret in (sys.executable, sys.prefix, os.path.dirname(sys.executable)):
        assert secret not in text
    # 形似路径的值在入口就被拒收，而不是被存下来
    for bad in (sys.executable, "/usr/local/bin/python3", "C:\\Python311\\python.exe", "3.11/../x"):
        for key in ("python_version", "matplotlib_version", "backend"):
            assert originrecord.sanitize_process_facts(FACTS | {key: bad}) is None, (key, bad)
    assert not originrecord.record(
        project_root=root,
        source_id="a.png",
        bytes_sha256=sha,
        size_bytes=1,
        facts=FACTS | {"python_version": sys.executable},
    )


# ------------------------------------------------------------- 插件 handoff.py


def load_plugin():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_plugin_handoff_origin", PLUGIN_HANDOFF)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PROBED = """\
import json, os, sys
import matplotlib
import matplotlib.pyplot as plt
matplotlib.rcParams["figure.dpi"] = 123
matplotlib.rcParams["savefig.dpi"] = 150
fig, ax = plt.subplots(figsize=(2, 2))
ax.plot([0, 1], [1, 0])
fig.savefig("fig.png")
json.dump(
    {
        "py": sys.version.split()[0],
        "mpl": matplotlib.__version__,
        "backend": matplotlib.get_backend(),
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "leaked_env": "TAVOTTO_ORIGIN_OUT" in os.environ,
    },
    open("expected.json", "w", encoding="utf-8"),
)
"""


@needs_worker
def test_plugin_probe_records_what_the_script_process_actually_used(tmp_path, monkeypatch):
    monkeypatch.delenv("MPLBACKEND", raising=False)
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    mod = load_plugin()
    proj = tmp_path / "proj"
    proj.mkdir()
    script = proj / "fig.py"
    script.write_text(PROBED, encoding="utf-8")
    out = tmp_path / "origin.json"
    ok, err = mod.run_script(WORKER_PY, str(script), origin_out=str(out))
    assert ok, err
    got = json.loads(out.read_text(encoding="utf-8"))
    expected = json.loads((proj / "expected.json").read_text(encoding="utf-8"))
    assert got["python_version"] == expected["py"]
    assert got["matplotlib_version"] == expected["mpl"]
    assert got["backend"] == expected["backend"] and got["backend"].lower() == "agg"
    assert got["figure_dpi"] == 123
    # 闭集：不记 savefig 任何东西（脚本里 rc 的 savefig.dpi=150 也不能冒充某次 savefig 的实参）
    assert set(got) == {"python_version", "matplotlib_version", "backend", "figure_dpi"}
    assert originrecord.sanitize_process_facts(got) is not None  # 两侧同源：引擎白名单收得下
    # 执行语义不变：argv / cwd 与直接跑一样，旁路变量不泄给脚本
    assert expected["argv"] == [str(script)] and Path(expected["cwd"]).resolve() == proj.resolve()
    assert expected["leaked_env"] is False
    for secret in (sys.executable, sys.prefix, WORKER_PY):
        assert secret not in out.read_text(encoding="utf-8")


@needs_worker
def test_plugin_probe_chains_the_users_sitecustomize_and_skips_scripts_without_matplotlib(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TAVOTTO_CONFIG_DIR", str(tmp_path / "cfg"))
    mod = load_plugin()
    users = tmp_path / "users"
    users.mkdir()
    (users / "sitecustomize.py").write_text(
        "import pathlib; pathlib.Path(%r).write_text('ran')" % str(tmp_path / "marker"),
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(users))
    script = tmp_path / "plain.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    out = tmp_path / "origin.json"
    ok, err = mod.run_script(WORKER_PY, str(script), origin_out=str(out))
    assert ok, err
    assert (tmp_path / "marker").read_text() == "ran", "用户自己的 sitecustomize 必须照常运行"
    assert not out.exists(), "没导入 matplotlib 的脚本不留记录（探针不替脚本 import）"


CLI_SHIM = """\
#!{python}
import os, sys
sys.path.insert(0, {src!r})
from tavotto.engine import cli
raise SystemExit(cli.dispatch(sys.argv[1:]))
"""

SAVE_THEN_FAIL = """\
import os
import matplotlib
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(2, 2))
ax.plot([0, 1], [1, 0])
fig.savefig("fig.png")
# 只在插件「在脚本目录里直接跑」时失败；Tavotto 的 safe 探测用沙盒 cwd，照常通过
try:
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.samefile(os.getcwd(), here):
        raise SystemExit(3)
except NameError:
    pass
"""

SAVE_OK = SAVE_THEN_FAIL.split("# 只在")[0]


def run_handoff(tmp_path, proj: Path, *args):
    shim = tmp_path / "tavotto-shim"
    shim.write_text(
        CLI_SHIM.format(python=WORKER_PY, src=str(ROOT / "src")),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    env = {
        **os.environ,
        "TAVOTTO_CLI": str(shim),
        "TAVOTTO_DATA_DIR": str(tmp_path / "data"),
        "TAVOTTO_CONFIG_DIR": str(tmp_path / "config"),
        "TAVOTTO_NO_TELEMETRY": "1",
        "PYTHONPATH": str(ROOT / "src"),
    }
    env.pop("MPLBACKEND", None)
    proc = subprocess.run(
        [WORKER_PY, str(PLUGIN_HANDOFF), str(proj / "fig.py"), "--python", WORKER_PY, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    lines = proc.stdout.strip().splitlines()
    return proc, (json.loads(lines[-1]) if lines else {})


posix_only = pytest.mark.skipif(os.name == "nt", reason="假 CLI 是 shebang 脚本")


@needs_worker
@posix_only
def test_handoff_writes_a_record_only_when_it_really_ran_the_script(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SAVE_OK, encoding="utf-8")
    proc, out = run_handoff(tmp_path, proj, "--no-launch")
    assert out.get("ran") is True and out.get("origin", {}).get("recorded") == 1, (proc, out)
    png = proj / "fig.png"
    rec_file = tmp_path / "data" / "origin" / f"{sha_of(png)}.json"
    entry = json.loads(rec_file.read_text(encoding="utf-8"))["entries"][0]
    assert entry["source_id"] == "fig.png" and entry["recorded_by"] == "handoff"
    assert entry["matplotlib_version"]
    assert (
        entry["python_version"]
        == subprocess.run(
            [WORKER_PY, "-c", "import platform;print(platform.python_version())"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
    )
    assert entry["backend"].lower() == "agg"
    assert entry["script"]["rel"] == "fig.py"
    # 产物没重跑（比脚本新）→ 不写：删掉记录再交接一次，仍是没有记录
    rec_file.unlink()
    os.utime(png, (png.stat().st_atime, png.stat().st_mtime + 10))
    proc2, out2 = run_handoff(tmp_path, proj, "--no-launch")
    assert out2.get("ran") is False and "origin" not in out2, (proc2, out2)
    assert not rec_file.exists()


@needs_worker
@posix_only
def test_a_failed_run_never_leaves_a_record_even_if_it_rewrote_the_png(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "fig.py").write_text(SAVE_THEN_FAIL, encoding="utf-8")
    proc, out = run_handoff(tmp_path, proj, "--no-launch")
    assert out.get("error_code") == "script_failed", (proc, out)
    assert (proj / "fig.png").exists(), "前提：失败前已经写出了 PNG"
    assert not list((tmp_path / "data" / "origin").glob("*.json"))


# ------------------------------------------------------------------ 准入响应


def clean_state(body):
    backed = dict(body["source_backed"])
    backed.pop("origin", None)
    return backed


@needs_worker
@pytest.mark.parametrize(
    "prelude, status",
    [
        ("", "exact"),
        ("import matplotlib; matplotlib.rcParams['lines.antialiased']=False", "needs_confirmation"),
    ],
)
def test_admission_shows_the_origin_but_the_verdict_is_identical(
    sb_env,  # noqa: F811
    opened,  # noqa: F811
    prelude,
    status,
):
    root = project(sb_env, original_prelude=prelude)
    client = opened(root)
    bare = render(client, []).get_json()
    assert bare["source_backed"]["status"] == status
    assert "origin" not in bare["source_backed"], "没有记录时字段缺省"

    sha = identity(root)["bytes_sha256"]
    assert originrecord.record(
        project_root=root,
        source_id="same.png",
        bytes_sha256=sha,
        size_bytes=(root / "same.png").stat().st_size,
        facts=FACTS,
    )
    shown = render(client, []).get_json()
    origin = shown["source_backed"]["origin"]
    assert origin["python_version"] == "3.11.4" and origin["matplotlib_version"] == "3.8.2"
    assert origin["backend"] == "agg" and origin["figure_dpi"] == 100.0
    assert origin["recorded_by"] == "handoff"
    assert set(origin) == {
        "python_version",
        "matplotlib_version",
        "backend",
        "figure_dpi",
        "recorded_by",
        "redraw",
    }
    # 本次重画实际用的环境（来自 worker 构建自报，不是记录）
    redraw = origin["redraw"]
    want = subprocess.run(
        [
            WORKER_PY,
            "-c",
            "import platform,matplotlib;print(platform.python_version(),matplotlib.__version__)",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.split()
    assert [redraw.get("python_version"), redraw.get("matplotlib_version")] == want
    # 判定一字不变
    assert clean_state(shown) == clean_state(bare)
    assert shown["source_backed"]["status"] == status


def test_stored_record_fields_are_a_closed_set_without_savefig_facts(data, tmp_path):
    root = tmp_path / "proj"
    _, sha = put(root, "a.png", facts=FACTS | {"savefig": {"dpi": 150}, "savefig_dpi": 150})
    entry = json.loads((data / "origin" / f"{sha}.json").read_text(encoding="utf-8"))["entries"][0]
    assert not [k for k in entry if "savefig" in k]
    assert "savefig_dpi" not in originrecord.lookup(sha, root)


# ------------------------------------------------- 跨进程锁（Codex P2：独立进程的读改写）----
_CHILD = r"""
import json, os, sys, time
from pathlib import Path
from tavotto.engine import originrecord

cfg = json.loads(sys.argv[1])
originrecord.MAX_RECORDS = cfg.get("max_records", originrecord.MAX_RECORDS)
if "lock_timeout" in cfg:
    originrecord.LOCK_TIMEOUT_S = cfg["lock_timeout"]
Path(cfg["ready"]).write_text("1")
while not Path(cfg["go"]).exists():  # 确定性起跑线：全部就绪后主进程一次放行
    time.sleep(0.005)
ok = originrecord.record(
    project_root=cfg["project"], source_id="a.png", bytes_sha256=cfg["sha"], size_bytes=1,
    facts={"python_version": "3.11.4", "matplotlib_version": "3.8.2", "backend": "agg"},
)
print(json.dumps(ok))
"""

_HOLDER = r"""
import sys, time
from pathlib import Path
from tavotto.engine import originrecord

with originrecord._WriteLock() as locked:
    assert locked
    Path(sys.argv[1]).write_text("held")  # 拿到锁之后才写就绪文件
    while not Path(sys.argv[2]).exists():
        time.sleep(0.005)
"""


def _env(data: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["TAVOTTO_DATA_DIR"] = str(data)
    env["TAVOTTO_NO_TELEMETRY"] = "1"
    env.pop("PYTHONSAFEPATH", None)
    return env


def _wait_for(path: Path, timeout: float = 30.0) -> None:
    import time

    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f"等不到 {path}"
        time.sleep(0.005)


def _sub(base: Path, name: str) -> Path:
    (base / name).mkdir()
    return base / name


def _run_racers(data: Path, tmp_path: Path, cfgs: list[dict]) -> list[bool]:
    go = tmp_path / "go"
    procs = []
    for i, cfg in enumerate(cfgs):
        cfg = {**cfg, "ready": str(tmp_path / f"ready{i}"), "go": str(go)}
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", _CHILD, json.dumps(cfg)],
                env=_env(data),
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        )
    for i in range(len(cfgs)):
        _wait_for(tmp_path / f"ready{i}")
    go.write_text("1")
    outs = [p.communicate(timeout=120)[0] for p in procs]
    assert [p.returncode for p in procs] == [0] * len(procs)
    return [json.loads(o) for o in outs]


def test_concurrent_processes_same_sha_keep_every_project(data, tmp_path):
    sha = "ab" * 32
    n = 6
    results = _run_racers(
        data, tmp_path, [{"project": str(tmp_path / f"p{i}"), "sha": sha} for i in range(n)]
    )
    assert results == [True] * n
    parsed = originrecord._read_file(originrecord._record_path(sha))
    assert parsed is not None
    assert len({e["project"] for e in parsed[1]}) == n


def test_concurrent_processes_distinct_shas_respect_max_records(data, tmp_path):
    cap = 4
    cfgs = [
        {"project": str(tmp_path / f"p{i}"), "sha": f"{i:02x}" * 32, "max_records": cap}
        for i in range(12)
    ]
    assert _run_racers(data, tmp_path, cfgs) == [True] * 12
    assert len(list((data / "origin").glob("*.json"))) <= cap


def test_write_waits_for_cross_process_lock_holder(data, tmp_path):
    held, release = tmp_path / "held", tmp_path / "release"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(held), str(release)], env=_env(data)
    )
    try:
        _wait_for(held)
        sha = "cd" * 32
        # 别的进程持锁：记录进程等满超时后放弃（弱线索，不记，不阻断），且一个字节都没写
        blocked = _run_racers(
            data,
            _sub(tmp_path, "r1"),
            [{"project": str(tmp_path / "p"), "sha": sha, "lock_timeout": 0.3}],
        )
        assert blocked == [False]
        assert not originrecord._record_path(sha).exists()
    finally:
        release.write_text("1")
        holder.wait(timeout=60)
    assert holder.returncode == 0
    ok = _run_racers(
        data,
        _sub(tmp_path, "r2"),
        [{"project": str(tmp_path / "p"), "sha": sha, "lock_timeout": 5}],
    )
    assert ok == [True]
    assert originrecord._record_path(sha).exists()
