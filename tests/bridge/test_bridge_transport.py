"""控制通道：loopback + 一次性 token（ADR 0020 §6）。

要求逐条：只 bind 127.0.0.1、随机高强度 token、单次 session、有握手、
非认证连接立即拒绝、不监听 0.0.0.0、token 不进普通日志、session 结束关 socket。

**协议语义零改动**：信封由 `pool.build_envelope()` 产出（与 stdin/stdout
那条控制面是同一个函数），执行侧由 `wireproto` 分派（与 worker.py 是同一个
类）。换的只有字节走哪条管子。
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time

import pytest

from support import bridgekit
from support.bridgekit import write
from tavotto.engine import bridge, bridge_spike, pool

pytestmark = pytest.mark.usefixtures("clean_env")

SHOW_ONLY = (
    "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "plt.plot([1,2],[3,4])\nplt.show()\n"
)


def test_wire_key_names_match_on_both_sides():
    """握手 / 事件 / token 三个键名是同源对：改一处必须改两处。

    它们分别写在父进程（`bridge.py`）与子进程（`bridge_runner.py`）里——
    两份文件由不同的解释器执行，不可能共享一个常量模块（子进程那份
    连 tavotto 包都 import 不到）。所以只能靠这条用例钉着。
    """
    runner_src = bridge.RUNNER_PY.read_text(encoding="utf-8")
    for const, value in (
        ("TOKEN_ENV", bridge.TOKEN_ENV),
        ("HELLO_KEY", bridge.HELLO_KEY),
        ("EVENT_KEY", bridge.EVENT_KEY),
    ):
        assert f'{const} = "{value}"' in runner_src, f"{const} 两侧不同源"


def test_listener_binds_loopback_only(user_python, tmp_path, monkeypatch):
    """**只** bind 127.0.0.1，绝不 0.0.0.0。

    按源码判之外还真的看一眼 `getsockname()`——源码里写对了、运行时
    被别的代码改掉，是完全可能的。
    """
    seen = {}
    real_bind = socket.socket.bind

    def spy_bind(self, addr):
        seen.setdefault("addr", addr)
        return real_bind(self, addr)

    monkeypatch.setattr(socket.socket, "bind", spy_bind)
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    spec = bridge.spec_for(str(proj / "fig.py"), interpreter=user_python, cwd=str(proj))
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
    try:
        sess.start()
        sess.wait_event("barrier")
        sess.resume()
    finally:
        sess.close()
    assert seen["addr"][0] == "127.0.0.1", f"监听地址是 {seen['addr']}"


def test_bad_token_is_rejected_and_the_real_child_still_gets_through(
    user_python, tmp_path, monkeypatch
):
    """抢先用错 token 连上来的进程被拒，**真正的子进程照样连得上**。

    认证失败就关掉监听等于把 DoS 送出去：本机任何进程抢先连一下，
    用户的 `tavotto run` 就永远起不来。所以拒绝之后必须继续 accept。
    """
    ports: list = []
    real_listen = socket.socket.listen

    def spy_listen(self, backlog=0):
        out = real_listen(self, backlog)
        try:
            ports.append(self.getsockname()[1])
        except OSError:
            pass
        return out

    monkeypatch.setattr(socket.socket, "listen", spy_listen)
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    spec = bridge.spec_for(str(proj / "fig.py"), interpreter=user_python, cwd=str(proj))
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")

    rejected = {}

    real_accept = bridge.BridgeSession._accept

    def wrapped(self, listener, token):
        # 冒充一个拿错 token 的本机进程抢先连上去。**必须在后台线程里做**：
        # 回复是 `_accept` 里发出来的，而它这会儿还没跑起来——同步等回复
        # 只会等到自己超时（第一版就是这样红的）。
        port = listener.getsockname()[1]

        def impostor():
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=20) as bad:
                    bad.sendall(b'{"bridge_hello":1,"token":"wrong-token","pid":0}\n')
                    rejected["reply"] = bad.makefile("r", encoding="utf-8").readline()
            except OSError as exc:  # pragma: no cover - 只在真出问题时走到
                rejected["reply"] = f'{{"error":{str(exc)!r}}}'

        t = threading.Thread(target=impostor, daemon=True)
        t.start()
        try:
            return real_accept(self, listener, token)
        finally:
            t.join(timeout=20)

    monkeypatch.setattr(bridge.BridgeSession, "_accept", wrapped)
    try:
        hello = sess.start()
        ev = sess.wait_event("barrier")
        sess.resume()
    finally:
        sess.close()
    assert json.loads(rejected["reply"]) == {"ok": False, "code": "bad_token"}
    assert hello.get("protocol_version") == 1
    assert "token" not in hello, "握手帧里的 token 不许留在任何可能被打日志的结构里"
    assert ev["stems"] == ["fig"]
    assert ports, "用例前提：确实开了监听"


def test_the_token_is_random_per_session(user_python, tmp_path, monkeypatch):
    """一次会话一枚 token，且长度足够（`secrets.token_urlsafe(32)` = 256 位）。"""
    tokens: list = []
    real = bridge.secrets.token_urlsafe

    def spy(n=None):
        t = real(n)
        tokens.append(t)
        return t

    monkeypatch.setattr(bridge.secrets, "token_urlsafe", spy)
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    for i in range(2):
        spec = bridge.spec_for(str(proj / "fig.py"), interpreter=user_python, cwd=str(proj))
        sess = bridge.BridgeSession(spec, out_dir=tmp_path / f"out{i}")
        try:
            sess.start()
            sess.wait_event("barrier")
            sess.resume()
        finally:
            sess.close()
    assert len(tokens) == 2 and tokens[0] != tokens[1]
    assert all(len(t) >= 40 for t in tokens), tokens


def test_the_envelope_comes_from_the_same_function_as_the_pipe_control_plane():
    """**换传输不换协议**：信封由 `pool.build_envelope()` 独家产出。

    在 bridge 里另拼一遍就是造第二套协议语义——它一开始逐字相同，然后在
    某次「只给 bridge 加个字段」之后分叉。按源码判：`bridge.py` 里不许出现
    自己攒 `protocol_version` 的地方。
    """
    src = (bridge.RUNNER_PY.parent / "bridge.py").read_text(encoding="utf-8")
    assert "pool.build_envelope(" in src
    assert '"protocol_version":' not in src.replace("HELLO_KEY", ""), (
        "bridge.py 里出现了手拼的 v1 信封"
    )
    env = pool.build_envelope({"cmd": "override", "stem": "s", "patches": []}, generation=2)
    assert env["cmd"] == "render", "override→render 的映射是调用侧信封的一部分"
    assert env["protocol_version"] == pool.PROTOCOL_VERSION
    assert env["worker_generation"] == 2
    assert env["canonical_patch_hash"], "带 patches 的命令要带 canonical hash"


def test_a_noisy_script_never_desyncs_the_control_channel(user_python, tmp_path, bridge_session):
    """用户在 stdout 上打印**合法协议 JSON** 时，控制通道毫发无伤。

    这是「协议不能偷 stdout」那条判断的直接判据：脚本连打 200 行看起来像
    请求/响应的东西，会话照样 build / render / 拿到配对的 SVG。

    反证：把控制通道改回 stdin/stdout，本条与 E2E 一起当场红。
    """
    proj = tmp_path / "proj"
    noise = "\n".join(
        f'print(\'{{"protocol_version":1,"request_id":"r-{i}","ok":true}}\')' for i in range(200)
    )
    write(
        proj / "fig.py",
        "import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        + noise
        + '\nprint(\'{"protocol_version":1,"cmd":"shutdown","request_id":"x"}\')\n'
        "plt.plot([1,2],[3,4])\nplt.show()\n"
        "print('SURVIVED', flush=True)\n",
    )
    with bridge_session(proj / "fig.py", cwd=str(proj)) as sess:
        ev = sess.wait_event("barrier")
        assert ev["stems"] == ["fig"]
        build = sess.ensure_built()
        stem = next(iter(build["stems"]))
        resp = sess.override(stem, [], inline_svg=True)
        assert resp["svg"].lstrip().startswith("<?xml")
        sess.resume()
        sess.wait_event("barrier")
        sess.resume()
        sess.wait_event("exit")


def test_disconnecting_never_leaves_the_user_script_hanging(user_python, tmp_path):
    """父进程走掉时，屏障必须放开——用户的脚本不是我们的人质。

    native 里那个进程是**用户的**。控制通道断了只说明"没人在编辑了"，
    脚本该接着跑完（或者说，至少不能永远挂在屏障上）。
    """
    proj = tmp_path / "proj"
    marker = tmp_path / "finished.txt"
    write(
        proj / "fig.py",
        SHOW_ONLY + f"open({str(marker)!r}, 'w').write('done')\n",
    )
    spec = bridge.spec_for(str(proj / "fig.py"), interpreter=user_python, cwd=str(proj))
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
    sess.start()
    sess.wait_event("barrier")
    assert not marker.exists()
    # 只关 socket，不杀进程——模拟父进程崩掉 / 用户关掉窗口
    sess.sock.close()
    sess.rfile.close()
    proc = sess.proc
    proc.wait(timeout=60)
    assert proc.returncode == 0, "脚本应当正常跑完"
    assert marker.read_text(encoding="utf-8") == "done"


def test_shutdown_closes_the_socket_and_reaps_the_child(user_python, tmp_path):
    """会话结束：socket 关掉、子进程收掉，不留 orphan。"""
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    spec = bridge.spec_for(str(proj / "fig.py"), interpreter=user_python, cwd=str(proj))
    sess = bridge.BridgeSession(spec, out_dir=tmp_path / "out")
    sess.start()
    sess.wait_event("barrier")
    sess.shutdown()
    deadline = time.monotonic() + 30
    while sess.proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sess.proc.poll() is not None, "子进程没被收掉"
    assert sess.sock.fileno() == -1, "socket 没关"


def test_the_runner_never_writes_into_the_user_home(user_python, tmp_path, monkeypatch):
    """没给 `--out-dir` 时产物落**临时目录**，绝不在用户 home 里留 dotdir。

    仓库纪律：运行时可写数据一律走 `config.data_dir()`，不往包目录 / 安装
    目录 / 仓库根写东西。runner 跑在用户环境里 import 不到 `config`，所以
    它的正确行为是"没给就用临时目录"，而不是猜一个 `~/.tavotto-*`。
    （第一版猜了，本机 home 里当场多出一个 `.tavotto-bridge-out/`。）
    """
    home = tmp_path / "fakehome"
    home.mkdir()
    proj = tmp_path / "proj"
    write(proj / "fig.py", SHOW_ONLY)
    env = bridgekit.child_env({"HOME": str(home), "USERPROFILE": str(home)})
    report = tmp_path / "report.json"
    r = subprocess.run(
        [
            user_python,
            str(bridge.RUNNER_PY),
            "--target-kind",
            "script",
            "--target",
            str(proj / "fig.py"),
            "--report",
            str(report),
            "--",
        ],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["stems"] == ["fig"], (
        "用例前提：确实渲染过（否则根本不会有产物要落盘）"
    )
    # 判据是「有没有 **Tavotto** 留下的东西」，不是「home 里一个文件都没多」：
    # `.matplotlib`（字体缓存）是 **matplotlib 自己**建的，用户直接
    # `python fig.py` 一样会有它——native 刻意**不**改 `MPLCONFIGDIR`
    # （safe 那边改是因为内置 runtime 装在只读的安装目录里；用户的环境是
    # 他的地盘，我们没资格替他改）。各平台上 matplotlib 落哪个目录不一样，
    # 拿"必须为空"当判据会在别的平台上无缘无故红。
    leaked = sorted(p.name for p in home.iterdir() if "tavotto" in p.name.lower())
    assert leaked == [], f"runner 在用户 home 里留了 Tavotto 的东西: {leaked}"
    others = sorted(p.name for p in home.iterdir())
    assert others in ([], [".matplotlib"]), f"home 里多出了意料之外的东西: {others}"


def test_the_spike_cli_is_not_wired_into_the_product_cli():
    """spike **不是**产品：`tavotto` 的 CLI 一个子命令都没多。

    ADR 0020 是架构决策的依据，不是对外承诺。命令行形状随时会变，
    任何人都不该照着它写脚本。
    """
    from tavotto.engine import cli

    cli_src = (bridge.RUNNER_PY.parent / "cli.py").read_text(encoding="utf-8")
    assert "bridge_spike" not in cli_src
    assert "bridge" not in getattr(cli, "COMMANDS", {}), "bridge 不该出现在产品 CLI 里"
    assert hasattr(bridge_spike, "main"), "spike 只经 python -m 调用"
