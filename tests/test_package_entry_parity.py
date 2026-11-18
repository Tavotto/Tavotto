"""包里的脚本按模块运行（ADR 0196）：真进程对拍——safe worker 与 native bridge 的世界必须与真实 `python -m 包.模块` 一致。

同一个解释器、同一份夹具跑三遍再逐字段比：

    python -m plots.fig A B                     （cwd = 包根的上一级）
    bridge  --target-kind script --target <脚本> --as-module plots.fig -- A B   （cwd 故意是别处）
    safe worker  --script <脚本> --as-module plots.fig                              （cwd = 沙盒）

比的是脚本真的会读的字段：`__name__` / `__package__` / `__spec__` / `__file__` / `sys.argv[0]` / `sys.path[0]` /
`__main__` 的身份。cwd 规则不变（桌面是脚本目录或项目根、safe 是沙盒），所以 `sys.path[0]` 与 cwd 是两回事——
这正是用「别处」当 cwd 要证明的。另验：safe 不往项目里写 `__pycache__`、守卫（savefig 拦截）仍然生效。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from support.bridgekit import USER_PYTHON, child_env
from tavotto.engine import bridge, execspec, pool, workdir

pytestmark = pytest.mark.skipif(
    USER_PYTHON is None, reason="找不到装有 matplotlib 的解释器（TAVOTTO_WORKER_PYTHON）"
)

PROBE_BODY = """\
from .helpers import tidy
import json, os, sys
OUT = {
    "argv": list(sys.argv),
    "path0": sys.path[0],
    "name": __name__,
    "package": __package__,
    "spec": None if __spec__ is None else [__spec__.name, __spec__.origin, __spec__.parent],
    "file": __file__,
    "main_is_me": sys.modules["__main__"].__dict__ is globals(),
    "tidy": tidy(),
}
if os.environ.get("PROBE_OUT"):
    with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as f:
        json.dump(OUT, f)
else:
    raise RuntimeError("PROBE:" + json.dumps(OUT))
"""
FIELDS = ("argv", "path0", "name", "package", "spec", "file", "main_is_me", "tidy")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _project(root: Path, *, dirs=("plots",), body=PROBE_BODY) -> str:
    cur = root
    for d in dirs:
        cur = cur / d
        _write(cur / "__init__.py", "")
    _write(cur / "helpers.py", "def tidy():\n    return 'tidied'\n")
    _write(cur / "fig.py", body)
    return "/".join([*dirs, "fig.py"])


def _norm(fields: dict) -> dict:
    """路径字段按真实路径归一（macOS 的 /var → /private/var）；其余原样。"""

    def real(p):
        return os.path.normcase(os.path.realpath(p))

    out = dict(fields)
    out["path0"] = real(fields["path0"])
    out["file"] = real(fields["file"])
    out["argv"] = [real(fields["argv"][0]), *fields["argv"][1:]]
    out["spec"] = [fields["spec"][0], real(fields["spec"][1]), fields["spec"][2]]
    return {k: out[k] for k in FIELDS}


def _direct(root: Path, module: str, tag: Path) -> dict:
    out = tag / "direct.json"
    r = subprocess.run(
        [USER_PYTHON, "-m", module, "A", "B"],
        cwd=root,
        env=child_env({"PROBE_OUT": str(out)}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _bridge(root: Path, script: str, elsewhere: Path, tag: Path) -> dict:
    spec = execspec.desktop_native_spec(
        str(root / script),
        interpreter=USER_PYTHON,
        cwd=str(elsewhere),
        project_root=str(root),
        argv=("A", "B"),
        run_config="rc_parity",
        env={"PYTHONUNBUFFERED": "1"},
    )
    assert spec.module_name, "夹具没触发判据"
    argv = execspec.bridge_argv(spec, runner_py=bridge.RUNNER_PY, out_dir=tag / "bridge-out")
    out = tag / "bridge.json"
    r = subprocess.run(
        argv,
        cwd=elsewhere,
        env=child_env({"PROBE_OUT": str(out)}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_package_script_runs_like_python_dash_m_in_bridge_and_safe_worker(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    script = _project(proj)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "tags").mkdir()
    direct = _direct(proj, "plots.fig", tmp_path / "tags")
    # 前提自查：夹具真的比到了值，不是两边都空
    assert direct["package"] == "plots" and direct["spec"][0] == "plots.fig"
    assert direct["name"] == "__main__" and direct["tidy"] == "tidied"

    via_bridge = _bridge(proj, script, elsewhere, tmp_path / "tags")
    assert _norm(via_bridge) == _norm(direct)
    assert os.path.realpath(via_bridge["path0"]) == os.path.realpath(proj)  # 不是 cwd（elsewhere）

    monkeypatch.setenv(
        "PYTHONPATH", ""
    )  # 与 test_syspath_order 同口径：不让开发机的 PYTHONPATH 抢 sys.path[0] 之后的位置
    workdir.set_mode(proj, workdir.MODE_SANDBOX)
    try:
        with pytest.raises(pool.WorkerError) as err:
            pool.build(script, str(proj), "__main__")
    finally:
        pool.shutdown_all(str(proj), wait=True)
    m = re.search(
        r"PROBE:(\{.*\})",
        str(err.value) + "\n" + (err.value.args[1] if len(err.value.args) > 1 else ""),
    )
    assert m, str(err.value)
    via_safe = json.loads(m.group(1))
    # safe 的 argv 是它自己的（没给运行参数 = 只有 argv[0]）；其余逐字段与真 `python -m` 一致
    assert via_safe["argv"] == [via_safe["file"]]
    assert {**_norm(via_safe), "argv": None} == {**_norm(direct), "argv": None}
    assert _norm(via_safe)["argv"][0] == _norm(direct)["argv"][0]


def test_multi_level_package_matches_dash_m_too(tmp_path):
    proj = tmp_path / "proj"
    script = _project(proj, dirs=("paper", "figs"))
    (tmp_path / "tags").mkdir()
    direct = _direct(proj, "paper.figs.fig", tmp_path / "tags")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _norm(_bridge(proj, script, elsewhere, tmp_path / "tags")) == _norm(direct)
    assert direct["package"] == "paper.figs"


FIGURE_BODY = """\
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .helpers import tidy
fig, ax = plt.subplots(figsize=(3, 2))
ax.plot([0, 1], [0, 1])
ax.set_title(tidy())
fig.savefig("fig_out.png")  # safe 的 savefig 拦截：捕获，不落盘
"""


def test_safe_worker_keeps_its_guards_and_writes_no_bytecode_into_the_project(tmp_path):
    proj = tmp_path / "proj"
    script = _project(proj, body=FIGURE_BODY)
    before = {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()}
    workdir.set_mode(proj, workdir.MODE_SANDBOX)
    try:
        _worker, resp = pool.build(script, str(proj), "__main__")
    finally:
        pool.shutdown_all(str(proj), wait=True)
    assert "fig_out" in (resp.get("stems") or {}), resp
    # 图的身份：资产 id 仍是 `runtime:<脚本项目相对路径>#<stem>`，不是模块名
    ids = [d.get("asset_id") or d.get("id") for d in resp.get("descriptors") or []]
    assert ids and all(i == "runtime:plots/fig.py#fig_out" for i in ids), (ids, list(resp))
    assert not list(proj.rglob("__pycache__")), "safe 不往项目目录写字节码"
    assert not list(proj.rglob("*.png")), "savefig 仍被拦截捕获，没有落盘"
    # 源码摘要换了来源（`_capture_entry_sha`）：必须等于脚本字节的 sha256
    import hashlib

    shas = {k: v for k, v in resp.items() if "sha" in k}
    assert hashlib.sha256((proj / script).read_bytes()).hexdigest() in shas.values(), shas
    assert {p: p.read_bytes() for p in proj.rglob("*") if p.is_file()} == before


def test_a_plain_subdirectory_script_is_byte_for_byte_unchanged(tmp_path):
    """没有相对导入：spec / argv 不带模块名，safe 与 bridge 两条 argv 里都没有新 token。"""
    proj = tmp_path / "proj"
    _write(proj / "paper" / "fig.py", "import os\n")
    _write(proj / "paper" / "__init__.py", "")
    spec = execspec.safe_spec(
        "paper/fig.py", str(proj), "__main__", interpreter="/py", sandbox=str(tmp_path / "box")
    )
    assert spec.module_name == ""
    argv = execspec.worker_argv(spec, worker_py="/w.py", out_dir="/o")
    assert argv == [
        "/py",
        "/w.py",
        "--script",
        str(proj / "paper" / "fig.py"),
        "--figures-dir",
        str(proj),
        "--out-dir",
        "/o",
        "--sandbox",
        str(tmp_path / "box"),
        "--entry",
        "__main__",
    ]
    native = execspec.desktop_native_spec(
        str(proj / "paper" / "fig.py"),
        interpreter="/py",
        cwd=str(proj),
        project_root=str(proj),
        env={"PYTHONUNBUFFERED": "1"},
    )
    bargv = execspec.bridge_argv(native, runner_py="/r.py", out_dir="/o")
    assert "--as-module" not in bargv and bargv[bargv.index("--target-kind") + 1] == "script"


def test_native_descriptor_identity_is_the_script_not_the_module(tmp_path):
    """硬要求：按模块运行时，native 的描述符（script / asset id / 指纹）与「按脚本」的身份算法给出的一字不差。
    指纹里有 `target_kind`：runner 的身份参数若被翻成 module，指纹就变了。"""
    from tavotto.engine import figcapture

    proj = tmp_path / "proj"
    script = _project(proj, body=FIGURE_BODY.replace('fig.savefig("fig_out.png")', "pass"))
    spec = execspec.desktop_native_spec(
        str(proj / script),
        interpreter=USER_PYTHON,
        cwd=str(proj),
        project_root=str(proj),
        env={"PYTHONUNBUFFERED": "1"},
    )
    assert spec.module_name == "plots.fig"
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "bridge-out")
    sess.start()
    try:
        desc = next(iter(sess.ensure_built()["descriptors"]))
        version = subprocess.run(
            [USER_PYTHON, "-c", "import matplotlib;print(matplotlib.__version__)"],
            capture_output=True,
            text=True,
            env=child_env(),
        ).stdout.strip()
        expected = figcapture.source_fingerprint(
            (proj / script).read_bytes(),
            script="plots/fig.py",
            entry="__main__",
            profile=figcapture.PROFILE_NATIVE,
            target_kind="script",
            passthrough_savefig=True,
            matplotlib_version=version,
        )
        assert desc["script"] == "plots/fig.py"
        assert desc["asset_id"] == f"runtime:plots/fig.py#{desc['stem']}"
        assert desc["source_fingerprint"] == expected
    finally:
        sess.close()


def test_a_failing_package_init_still_reports_the_script_identity(tmp_path):
    proj = tmp_path / "proj"
    script = _project(proj)
    _write(proj / "plots" / "__init__.py", "raise RuntimeError('boom')\n")
    spec = execspec.desktop_native_spec(
        str(proj / script),
        interpreter=USER_PYTHON,
        cwd=str(proj),
        project_root=str(proj),
        env={"PYTHONUNBUFFERED": "1"},
    )
    argv = execspec.bridge_argv(
        spec, runner_py=bridge.RUNNER_PY, out_dir=tmp_path / "o", report=str(tmp_path / "r.json")
    )
    r = subprocess.run(argv, cwd=proj, env=child_env(), capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["rel_target"] == "plots/fig.py"
    assert report["target_kind"] == "script"
