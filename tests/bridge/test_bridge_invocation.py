"""invocation 对拍：bridge 里的世界必须与**真实 python 命令**逐字段一致。

判据不是"看起来对"，而是拿**同一个解释器、同一份夹具**跑两遍再逐字段比：

    python probe.py A B          vs   bridge --target-kind script --target probe.py -- A B
    python -m paper.figure A B   vs   bridge --target-kind module --target paper.figure -- A B

比的字段就是脚本真的会读的那些：`sys.executable` / `os.getcwd()` /
`sys.argv` / `sys.path[0]` / `__name__` / `__package__` / `__spec__` /
`__file__` / 若干环境变量。

**这条对拍是 native 全部价值的地基**：native 的定义就是"与你自己在终端里
跑这条命令完全等同"。差一个字段就有一类脚本会以看不出原因的方式失败——
`__package__ is None` 是相对 import 兜底的常见写法，
`os.path.dirname(sys.argv[0])` 到处都是。
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from support.bridgekit import child_env, run_runner, write
from tavotto.engine import bridge

#: 脚本真的会读到的那些"我是谁、我在哪、我是怎么被叫起来的"。
PROBE = '''\
import json, os, sys
out = {
    "executable": sys.executable,
    "cwd": os.getcwd(),
    "argv": list(sys.argv),
    "path0": sys.path[0],
    "name": __name__,
    "package": __package__,
    "spec": None if __spec__ is None else [__spec__.name, __spec__.origin, __spec__.parent],
    "file": __file__,
    "main_is_me": sys.modules["__main__"].__dict__ is globals(),
    "env": {k: os.environ.get(k) for k in ("TAVOTTO_PARITY_MARK", "PATH", "HOME")},
    "dont_write_bytecode": sys.dont_write_bytecode,
    "flags_isolated": sys.flags.isolated,
    "flags_no_site": sys.flags.no_site,
}
with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as f:
    json.dump(out, f)
'''


@pytest.fixture
def parity(tmp_path, user_python):
    """跑两遍同一份 probe（真 python / bridge），返回两份字段表。"""
    proj = tmp_path / "proj"
    write(proj / "probe.py", PROBE)
    write(proj / "paper" / "__init__.py", "")
    write(proj / "paper" / "figure.py", PROBE)

    def _run(kind: str, target: str, argv, tag: str):
        out = tmp_path / f"{tag}.json"
        env = child_env({"PROBE_OUT": str(out), "TAVOTTO_PARITY_MARK": "kept"})
        if tag.startswith("direct"):
            cmd = [user_python, *(["-m", target] if kind == "module" else [target]), *argv]
            r = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True, timeout=120)
        else:
            r = run_runner(
                user_python,
                bridge.RUNNER_PY,
                target=target,
                target_kind=kind,
                argv=argv,
                cwd=str(proj),
                env=env,
            )
        assert r.returncode == 0, f"{tag}: {r.stderr}"
        return json.loads(out.read_text(encoding="utf-8"))

    return proj, _run


#: 逐字段比的清单。**`executable` 必须在里面**——它回答的是"跑我的到底是
#: 哪个 Python"，而 native 最不能出错的就是这一条。
FIELDS = (
    "executable",
    "cwd",
    "argv",
    "path0",
    "name",
    "package",
    "spec",
    "file",
    "main_is_me",
    "env",
    "dont_write_bytecode",
    "flags_isolated",
    "flags_no_site",
)


def test_script_invocation_matches_real_python(parity):
    """`python probe.py A B` 与 bridge 的 script 形态逐字段一致。

    两处最容易错的：`__file__` 必须是**绝对**路径（`runpy.run_path` 给的是
    传进去的原串），`__package__` 必须是 **None**（run_path 给 `""`）。
    所以 script 形态不用 run_path，按 CPython 自己的做法组装 `__main__`。
    """
    _, run = parity
    direct = run("script", "probe.py", ["A", "B"], "direct-script")
    via = run("script", "probe.py", ["A", "B"], "bridge-script")
    assert {k: direct[k] for k in FIELDS} == {k: via[k] for k in FIELDS}
    # 前提自查：夹具真的比到了值，不是两边都空
    assert direct["argv"] == ["probe.py", "A", "B"]
    assert direct["package"] is None and direct["spec"] is None


def test_absolute_script_path_keeps_argv0_verbatim(parity):
    """`python /abs/probe.py` 时 argv[0] 是**绝对**的——不许被规范化。

    `ExecutionSpec.target` 是项目相对 POSIX 路径（身份，跨机器稳定），
    拿它当 argv[0] 会让脚本里的 `os.path.dirname(sys.argv[0])` 指到别处。
    `raw_target` 这个字段就是为这条存在的。
    """
    proj, run = parity
    abs_target = str(proj / "probe.py")
    direct = run("script", abs_target, [], "direct-abs")
    via = run("script", abs_target, [], "bridge-abs")
    assert direct["argv"] == [abs_target]
    assert {k: direct[k] for k in FIELDS} == {k: via[k] for k in FIELDS}


def test_module_invocation_matches_real_python(parity):
    """`python -m paper.figure A B` 与 bridge 的 module 形态逐字段一致。

    `runpy.run_module(alter_sys=True)` 在 `__name__` / `__package__` /
    `__spec__` / `__file__` / `argv[0]` 五项上本来就对；唯一要自己补的是
    `sys.path[0]` = **cwd**（真实 `-m` 放的是它，而 runpy 不动 sys.path）。
    不补的话 `paper` 这个包根本 import 不到。
    """
    _, run = parity
    direct = run("module", "paper.figure", ["A", "B"], "direct-module")
    via = run("module", "paper.figure", ["A", "B"], "bridge-module")
    assert {k: direct[k] for k in FIELDS} == {k: via[k] for k in FIELDS}
    assert direct["package"] == "paper"
    assert direct["spec"][0] == "paper.figure"


def test_bridge_adds_no_interpreter_flags(parity):
    """bridge 不给解释器加任何标志（没有 -B / -S / -I / -E）。

    加一个就不是"与你自己敲那条命令等同"了：`-B` 改变 .pyc 行为、
    `-S` 让 site-packages 消失、`-I` 连 PYTHONPATH 都不认。
    """
    _, run = parity
    via = run("script", "probe.py", [], "bridge-flags")
    assert via["flags_isolated"] == 0
    assert via["flags_no_site"] == 0
    assert via["dont_write_bytecode"] is False


def test_environment_is_inherited_verbatim(parity):
    """env **原样继承**：不重建 conda / poetry / uv，不清洗 PATH。

    `TAVOTTO_PARITY_MARK` 是随手放进去的一个变量——bridge 一路带到用户脚本
    面前，两边一模一样。
    """
    _, run = parity
    direct = run("script", "probe.py", [], "direct-env")
    via = run("script", "probe.py", [], "bridge-env")
    assert via["env"] == direct["env"]
    assert via["env"]["TAVOTTO_PARITY_MARK"] == "kept"


def test_the_auth_token_never_reaches_the_user_script(tmp_path, user_python, clean_env):
    """token 是 Tavotto 与自己子进程之间的凭据，**用户脚本不该看见**。

    留在 `os.environ` 里等于交给脚本以及脚本起的每一个子进程。runner 一起来
    就把它摘掉（`_take_token`）。
    """
    proj = tmp_path / "proj"
    out = tmp_path / "seen.json"
    write(
        proj / "peek.py",
        "import json, os\n"
        f"open({str(out)!r}, 'w').write(json.dumps({{'token': os.environ.get('TAVOTTO_BRIDGE_TOKEN')}}))\n"
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\nplt.figure()\n",
    )
    spec = bridge.spec_for(str(proj / "peek.py"), interpreter=user_python, cwd=str(proj))
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
    try:
        sess.start()
        sess.wait_event("barrier")
        sess.resume()
    finally:
        sess.close()
    assert json.loads(out.read_text(encoding="utf-8"))["token"] is None
    assert os.environ.get("TAVOTTO_BRIDGE_TOKEN") is None, "父进程自己的环境也不该被改"
