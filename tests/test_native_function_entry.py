"""库式模块的函数入口：safe 被拦后的 native 回退要调到同一个函数（ADR 0127 修订 2026-11-18，一份用户反馈）。

脚本只定义了零参出图函数（`geography()` / `cooling()`），没有 `if __name__` 守卫；注册表登记的入口是函数，safe worker
`getattr(module, entry)()` 能画出来。保存辅助函数 `savefig` 之后读回自己存的文件 → safe 拦下 → 桌面静默回退 native。
以前 native 恒按 `python fig.py` 跑整份模块：什么都不调用，退出码 0，「没有图」。

现在：
* `discover.native_entry_of` 判 `call` → native 跑完模块再调那个函数（bridge `--entry`）；
* 有守卫 / 顶层自己画图 / 入口是 `__main__` → 原样跑整份模块（行为逐字节不变）；
* 入口函数已不在（`unreachable`）→ 不白跑：桌面回退以 `plan_unavailable` 不接，路由直达的 native 回 safe。
"""

# ruff: noqa: F811 — 夹具从兄弟文件导入复用，参数名与导入名相同
from __future__ import annotations

import json

import pytest

from support import bridgekit
from support.bridgekit import run_runner, write
from tavotto.engine import (
    bridge,
    discover,
    execpolicy,
    execspec,
    nativefallback,
    pool as engine_pool,
    preparation,
)
from tavotto.engine.runcodes import RunError
from test_entry_retry_session import _desktop, _go, _setup, entries  # noqa: F401
from test_execution_fallback import _history  # noqa: F401
from test_execution_fallback_origin import _restore_auth_config, native  # noqa: F401
from test_preparation_api import client, fake_pool  # noqa: F401
from test_preparation_session import sessions  # noqa: F401

#: 库式模块：两个零参出图函数，存完读回自检再关图；没有守卫，顶层不画图
LIBRARY = """\
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _save(fig, name):
    fig.savefig(name + ".png")
    os.stat(name + ".png")  # 读回自己刚存的文件：safe 拦下的就是这一步
    plt.close(fig)


def geography():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    _save(fig, "geo")


def cooling():
    fig, ax = plt.subplots()
    ax.bar([1, 2], [3, 4])
    _save(fig, "cool")
"""

GUARDED = LIBRARY + '\n\nif __name__ == "__main__":\n    geography()\n'
NEEDS_ARGS = LIBRARY.replace("def geography():", "def geography(data):")


@pytest.fixture
def user_python() -> str:
    if bridgekit.USER_PYTHON is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    return bridgekit.USER_PYTHON


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)


def _tree_path(tmp_path, text, name="s.py"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------- 判据
@pytest.mark.parametrize(
    ("text", "entry", "want"),
    [
        (LIBRARY, "geography", (discover.NATIVE_CALL_ENTRY, "geography")),
        (LIBRARY, "cooling", (discover.NATIVE_CALL_ENTRY, "cooling")),
        (GUARDED, "geography", (discover.NATIVE_RUN_MODULE, None)),  # 守卫怎么调由用户代码决定
        (
            LIBRARY + "\ngeography()\n",
            "geography",
            (discover.NATIVE_RUN_MODULE, None),
        ),  # 顶层自己画
        (LIBRARY, "__main__", (discover.NATIVE_RUN_MODULE, None)),
        (LIBRARY, None, (discover.NATIVE_RUN_MODULE, None)),
        (LIBRARY, "gone", (discover.NATIVE_ENTRY_UNREACHABLE, None)),  # 脚本被改写，入口没了
        (NEEDS_ARGS, "geography", (discover.NATIVE_ENTRY_UNREACHABLE, None)),
        ("def geography(:\n", "geography", (discover.NATIVE_RUN_MODULE, None)),  # 判不出就不改
        # Codex P1（#2044）：入口是 import 进来的函数、顶层调用它——整份跑就会调到，不许判「够不到」
        ("from helpers import main\nmain()\n", "main", (discover.NATIVE_RUN_MODULE, None)),
        ("from helpers import main\n", "main", (discover.NATIVE_RUN_MODULE, None)),
        # 要参数的本地函数在顶层带参调用（画图在别的模块里，顶层够不着 PLOT_FUNCS）
        (
            "from helpers import load, draw\n\ndef plot(rows):\n    draw(rows)\n\nplot(load())\n",
            "plot",
            (discover.NATIVE_RUN_MODULE, None),
        ),
        # 零参函数在顶层被调用：整份跑已会调它，不另调（免得跑两遍）
        ("def plot():\n    pass\n\nplot()\n", "plot", (discover.NATIVE_RUN_MODULE, None)),
        # 交给别人跑（`fire.Fire(plot)`）也算提到
        (
            "import fire\n\ndef plot():\n    pass\n\nfire.Fire(plot)\n",
            "plot",
            (discover.NATIVE_RUN_MODULE, None),
        ),
    ],
)
def test_native_entry_of(tmp_path, text, entry, want):
    assert discover.native_entry_of(_tree_path(tmp_path, text), entry) == want


def test_preparation_mirror_matches_discover():
    assert preparation.NATIVE_ENTRY_UNREACHABLE == discover.NATIVE_ENTRY_UNREACHABLE


# ---------------------------------------------------------------- 启动身份
def _desktop_spec(tmp_path, entry=None):
    script = _tree_path(tmp_path, LIBRARY)
    return execspec.desktop_native_spec(
        str(script),
        interpreter=str(tmp_path / "py"),
        cwd=str(tmp_path),
        project_root=str(tmp_path),
        entry=entry,
    )


def _argv(spec):
    return execspec.bridge_argv(spec, runner_py="runner.py", out_dir="out")


def test_bridge_argv_carries_the_function_entry_only_when_set(tmp_path):
    plain = _argv(_desktop_spec(tmp_path))
    called = _argv(_desktop_spec(tmp_path, entry="geography"))
    assert "--entry" not in plain  # 没有入口的命令行逐字节不变
    cut = called.index("--")
    assert called[:cut] == plain[: plain.index("--")] + ["--entry", "geography"]
    assert _desktop_spec(tmp_path, entry="geography").stable_payload()["entry"] == "geography"


@pytest.mark.parametrize("bad", ["__main__", "a.b", ""])
def test_native_entry_must_be_a_function_name(tmp_path, bad):
    with pytest.raises(ValueError):
        _desktop_spec(tmp_path, entry=bad)


def test_terminal_native_spec_never_has_an_entry(tmp_path):
    script = _tree_path(tmp_path, LIBRARY)
    spec = execspec.native_spec(
        str(script), interpreter=str(tmp_path / "py"), cwd=str(tmp_path), project_root=str(tmp_path)
    )
    assert spec.entry is None and "--entry" not in _argv(spec)


# ---------------------------------------------------------------- 真 bridge（真子进程）
def _run_bridge(user_python, tmp_path, entry):
    proj = tmp_path / "proj"
    write(proj / "climate.py", LIBRARY)
    report = tmp_path / "report.json"
    r = run_runner(
        user_python,
        bridge.RUNNER_PY,
        target=proj / "climate.py",
        cwd=str(proj),
        report=report,
        out_dir=tmp_path / "out",
        bridge_args=("--entry", entry) if entry else (),
    )
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return r, data, proj


@pytest.mark.usefixtures("clean_env")
def test_bridge_calls_the_function_entry_and_captures_the_closed_figure(user_python, tmp_path):
    r, data, proj = _run_bridge(user_python, tmp_path, "geography")
    assert r.returncode == 0, r.stderr
    assert [f["stem"] for f in data["figures"]] == ["geo"]  # 只调了登记的那一个函数
    assert (proj / "geo.png").exists() and not (proj / "cool.png").exists()  # savefig 照常透传


@pytest.mark.usefixtures("clean_env")
def test_bridge_without_entry_still_runs_the_whole_module(user_python, tmp_path):
    """反证：不带 `--entry` 就是以前的行为——库式模块跑完什么图都没有。"""
    r, data, _ = _run_bridge(user_python, tmp_path, None)
    assert r.returncode == 0, r.stderr
    assert data["figures"] == []


@pytest.mark.usefixtures("clean_env")
def test_bridge_entry_gone_at_runtime_is_a_script_error(user_python, tmp_path):
    r, data, _ = _run_bridge(user_python, tmp_path, "gone")
    assert r.returncode == 1 and "gone" in r.stderr


# ---------------------------------------------------------------- 准备会话（桌面回退）
def test_desktop_fallback_calls_the_blocked_function_entry(
    client, tmp_path, fake_pool, entries, native, sessions
):
    entries["blocked"] = {"geography", "cooling", "__main__"}
    root = _setup(client, tmp_path, "climate.py", LIBRARY)
    _desktop(client)
    final = _go(client, "climate.py")
    assert entries["seen"][0] == "geography"
    assert [h["profile"] for h in _history(final)][-1] == "native"
    spec = native["calls"][0].native_spec
    assert spec.entry == "geography"  # 原来被拦的那个入口，不是换过的 cooling
    assert "--entry" in execspec.bridge_argv(spec, runner_py="r.py", out_dir=str(root))


def test_desktop_fallback_on_a_guarded_script_runs_the_whole_module(
    client, tmp_path, fake_pool, entries, native, sessions
):
    entries["blocked"] = {"geography", "cooling", "__main__"}
    _setup(client, tmp_path, "climate.py", GUARDED)
    _desktop(client)
    _go(client, "climate.py")
    assert len(native["calls"]) == 1 and native["calls"][0].native_spec.entry is None


def test_desktop_fallback_declines_when_native_cannot_reach_the_entry(
    client, tmp_path, fake_pool, entries, native, sessions, monkeypatch
):
    """B：入口函数在原样运行里够不到 → 不起 native 白跑，以 `plan_unavailable` 不接，停在原来的 blocked 上。"""
    entries["blocked"] = {"geography", "cooling", "__main__"}
    _setup(client, tmp_path, "climate.py", LIBRARY)
    _desktop(client)
    monkeypatch.setattr(
        preparation,
        "native_entry_for",
        lambda *a, **k: (preparation.NATIVE_ENTRY_UNREACHABLE, None),
    )
    final = _go(client, "climate.py")
    assert native["calls"] == []
    assert final["result"]["status"] == "error"
    assert final["result"]["error"]["code"] == "safe_file_operation_blocked"
    declined = [h.get("declined") for h in _history(final)]
    assert execpolicy.REASON_PLAN_UNAVAILABLE in declined, _history(final)


# ---------------------------------------------------------------- MCP 回退（同一份判据）
def test_mcp_decide_declines_an_unreachable_entry_and_launch_passes_the_entry(tmp_path):
    _tree_path(tmp_path, LIBRARY, "climate.py")
    ok = nativefallback.decide(
        str(tmp_path),
        "safe_file_operation_blocked",
        python=str(tmp_path / "py"),
        script="climate.py",
        entry="geography",
    )
    gone = nativefallback.decide(
        str(tmp_path),
        "safe_file_operation_blocked",
        python=str(tmp_path / "py"),
        script="climate.py",
        entry="gone",
    )
    assert gone == nativefallback.Verdict(False, execpolicy.REASON_PLAN_UNAVAILABLE)
    assert ok.reason != execpolicy.REASON_PLAN_UNAVAILABLE

    seen = {}

    def start(launch):
        seen["spec"] = launch.spec
        raise RunError("bridge_child_exited")

    with pytest.raises(engine_pool.WorkerError):
        nativefallback.launch(
            str(tmp_path),
            "climate.py",
            "geo",
            python=str(tmp_path / "py"),
            start=start,
            entry="geography",
        )
    assert seen["spec"].entry == "geography"
