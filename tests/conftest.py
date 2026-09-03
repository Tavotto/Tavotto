"""测试跑在 .venv（flask+pymupdf，无 matplotlib）。

需要科学栈的用例（worker round-trip）自行 spawn worker 解释器子进程，
本进程始终保持与 Flask 父进程相同的依赖边界。
"""

import os
import sys
import tempfile
import threading
import time
import traceback

import pytest

# 模块级设置：app / engine 的路径常量在 import 时求值，必须先于用例模块被
# import 就位，否则测试会写到真实的用户数据目录。单个用例仍各自 monkeypatch
# 具体常量，这里只是兜底护栏。
_DATA_DIR = tempfile.mkdtemp(prefix="tavotto-data-")
os.environ.setdefault("TAVOTTO_DATA_DIR", _DATA_DIR)

# 渲染控制面**默认走 Python 池**。开发机上 `cargo build` 之后
# `workerd/target/debug/tavotto-workerd` 就在那儿，pool 会自动认出来——
# 那样整套既有用例会在不知不觉间换一条控制面跑，「Python 实现是参考实现」
# 这件事就没人看着了。走 workerd 的用例自己把这个变量改掉（见
# tests/test_workerd_pool.py 的 workerd_enabled fixture）。
os.environ.setdefault("TAVOTTO_WORKERD", "0")

# 匿名遥测在测试里**硬关**。用 setdefault 之外还要真的钉住：这不是「默认值」
# 那一类偏好，而是「测试进程绝不产生真实的 PostHog 事件」这条硬约束——
# 开发机上的用户配置里很可能已经同意过遥测（那是同一个 config.json），
# 只靠 TAVOTTO_CONFIG_DIR 隔离在 fixture 就位之前的模块级 import 期间是空的。
# 遥测自己的用例把它摘掉并替换掉传输层（tests/test_telemetry.py）。
os.environ["TAVOTTO_NO_TELEMETRY"] = "1"


#: 会改变「渲染解释器选谁」的进程级环境变量。用例之间必须互不影响。
_INTERPRETER_ENV = ("TAVOTTO_WORKER_PYTHON",)


@pytest.fixture(autouse=True)
def _isolated_interpreter_env():
    """每个用例结束后把渲染解释器相关的环境变量恢复原状。

    `monkeypatch.delenv(name, raising=False)` **在变量本来就没设的时候什么都
    不记账**（pytest 的 delitem 直接返回），所以「先 delenv 再由被测代码
    `os.environ[name] = …`」这条组合逃得掉自动还原——CompatBench 的驱动
    `_worker_python()` 正是直接写 `os.environ`（它是 CI 驱动，不是被测函数）。
    结果是一条解释器路径漏给同一进程里后面的每一个用例：单跑绿、全量红，
    而且红在完全无关的文件里。这里兜住整类问题，不是某一个用例。
    """
    saved = {k: os.environ.get(k) for k in _INTERPRETER_ENV}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _isolated_user_config(tmp_path_factory, monkeypatch):
    """所有测试的用户级配置（最近项目等）落在临时目录，绝不碰真实用户配置。"""
    monkeypatch.setenv(
        "TAVOTTO_CONFIG_DIR",
        str(tmp_path_factory.mktemp("tavotto-config")),
    )


@pytest.fixture
def telemetry_sent(_isolated_user_config, monkeypatch):
    """打开匿名遥测并**拦下传输层**：返回收集到的 payload 列表。

    产品侧的埋点用例（导出 / AI / 预检）靠它断言「发了什么、没发什么」，
    而整个过程里一个真实网络请求都不会发出去。
    """
    from tavotto.engine import telemetry

    monkeypatch.delenv("TAVOTTO_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("TAVOTTO_TELEMETRY_ENDPOINT", raising=False)
    telemetry.reset_for_tests()
    box: list[dict] = []
    monkeypatch.setattr(telemetry, "_post", box.append)
    telemetry.set_consent(telemetry.CONSENT_ENABLED)
    telemetry.flush(5.0)
    box.clear()  # 同意本身那两条不参与产品埋点的断言
    yield box
    telemetry.reset_for_tests()


# ---------------------------------------------------------------------------
# 会话结束：不许有活着的非 daemon 线程
# ---------------------------------------------------------------------------
# pytest 内置的 faulthandler（`pytest.ini` 的 `faulthandler_timeout`）只在
# **单条用例执行期间**武装：它挂在 `pytest_runtest_protocol` 上，用例之间
# `cancel_dump_traceback_later()`，会话结束之后一个 timer 都不剩。
#
# 而 2026-08-28 合并队列那次挂了 8 小时 20 分的 job（根因见 `d9e2b60`）恰恰
# **不在那个窗口里**：40 轮用例全绿、pytest 打完摘要，**解释器退出时**
# `threading._shutdown()` 去 join 一条泄漏的非 daemon 线程，永久挂住。那一刻没有
# 任何 timer 武装，所以同一条路径再挂一次，faulthandler 一个字都不会打——只剩
# job 的 `timeout-minutes` 在 45 分钟后把它杀掉，日志里仍然零信息。
#
# 这条门禁补的就是那个窗口。它做两件事，缺一不可：
#
#   1. **点名**——会话结束时枚举还活着的非 daemon 线程，连它们停在哪一行一起
#      打出来。这正是那 8 小时里没人拿到的东西。
#   2. **别再挂着**——报完之后 `os._exit()` 硬退出，跳过 `threading._shutdown()`。
#      只报不退的话，进程照样会挂在退出上，pytest 永远返回不了退出码，CI 上仍然
#      是「job 超时被取消」而不是「测试失败」。**这条路径上唯一的替代方案是永久
#      挂住**，所以硬退出跳过的那些收尾（atexit、缓冲区、logging.shutdown）本来
#      也一次都不会跑到。
#
# 它不只保住 `d9e2b60` 修的那一条用例：任何人将来漏一个 `daemon=True`、或者写一个
# 没有上限的等待，都会在当次会话结束时当场红。

#: 给非 daemon 线程的收尾宽限（秒）。它吸收的是「最后一条用例刚撒手、线程还差
#: 一口气」这种时序噪声；吸收不了「永久 park」——后者等多久都还在，而恰恰只有
#: 后者会把解释器挂死。所以宽限不会让这条判据变空。
_THREAD_SHUTDOWN_GRACE_SECONDS = 5.0

#: 会话结束时抓到的现场（已格式化好的报告 + 该用的退出码）。为空表示这条门禁没
#: 有发现问题——`pytest_unconfigure` 只在非空时才硬退出。
_STUCK_THREAD_REPORT: list[tuple[str, int]] = []


def _threads_that_block_interpreter_exit() -> list[threading.Thread]:
    """返回解释器退出时 `threading._shutdown()` 会去 join、此刻还活着的线程。

    主语必须说清楚三件事，否则量的就不是「会不会挂在退出上」：

    * **这个进程**——跑 pytest 的这一个。用例 spawn 出去的 worker / CLI 子进程里
      有多少线程都不归这里管，它们随各自的解释器退出；
    * **非 daemon**——daemon 线程解释器退出时直接丢下，挂不住谁；
    * **主线程之外**——主线程自己就是执行 `_shutdown()` 的那个。
    """
    main = threading.main_thread()
    return [t for t in threading.enumerate() if t is not main and not t.daemon and t.is_alive()]


def _describe_stuck_threads(threads: list[threading.Thread]) -> str:
    """逐条列出线程与它停在哪一行——不带栈的话，「谁泄漏的」还是得靠猜。"""
    frames = sys._current_frames()
    blocks = []
    for t in threads:
        frame = frames.get(t.ident)
        where = (
            "".join(traceback.format_stack(frame)).rstrip()
            if frame is not None
            else "    （拿不到栈：这条线程刚好在这一瞬结束了）"
        )
        blocks.append(f"  - {t!r}\n{where}")
    return "\n".join(blocks)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """用例跑完还活着的非 daemon 线程 == 解释器一定会挂在退出上。

    `trylast=True`：让别的插件先跑完各自的 `pytest_sessionfinish`（有的插件在那里
    关自己的后台线程），免得把「还没轮到它关」诬告成泄漏。

    唯一的例外是 `INTERRUPTED`：Ctrl+C / `pytest.exit()` 之后用例根本没走到
    teardown，此时还有线程活着说明不了任何事。CI 上这一档只出现在 job 被取消时
    （那种 run 本来也不产生结论），所以这个例外挡不住任何真实的泄漏。
    """
    if exitstatus == pytest.ExitCode.INTERRUPTED:
        return
    stuck = _threads_that_block_interpreter_exit()
    if stuck:
        deadline = time.monotonic() + _THREAD_SHUTDOWN_GRACE_SECONDS
        for t in stuck:
            t.join(max(0.0, deadline - time.monotonic()))
        stuck = _threads_that_block_interpreter_exit()
    if not stuck:
        return
    # 别把已有的失败降级成「只有线程泄漏」：本来就红的会话保留它自己的退出码。
    code = int(exitstatus) or int(pytest.ExitCode.TESTS_FAILED)
    session.exitstatus = code
    _STUCK_THREAD_REPORT.append(
        (
            f"用例跑完还有 {len(stuck)} 条非 daemon 线程活着，"
            f"解释器会挂在退出上（已宽限 {_THREAD_SHUTDOWN_GRACE_SECONDS:g} 秒）：\n"
            f"{_describe_stuck_threads(stuck)}\n\n"
            "三条修法（`d9e2b60` 用的是前两条）：给等待一个有上限的形式"
            "（`wait(timeout)` 到点抛出，线程死掉而不是继续 park）、"
            "把线程标成 `daemon=True`、或者在用例里 join 干净再返回。",
            code,
        )
    )


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """报告 + 硬退出。放在 unconfigure 是为了让它成为日志的最后一段。

    `pytest_sessionfinish` 里抛异常也能红，但那会打断 terminal reporter 的
    wrapper——摘要那一行（多少条过、多少条红）就没了，而且进程照样挂在
    `threading._shutdown()` 上。这里跑在摘要之后、所有插件收尾之后。
    """
    if not _STUCK_THREAD_REPORT:
        return
    report, code = _STUCK_THREAD_REPORT[0]
    sys.stdout.flush()  # 先把摘要冲出去，报告才会落在日志最后一段
    _write_report_to_stderr(f"\n{report}\n")
    os._exit(code)


def _write_report_to_stderr(text: str) -> None:
    """把报告写出去，中文要**以中文的样子**落进日志。

    Windows 的 runner 上重定向的 stderr 默认还是旧代码页（cp1252 / cp936）。裸
    `print()` 一段中文在那里**不会抛异常**——`sys.stderr` 的错误处理器天生是
    `backslashreplace`，它会安静地把整段话变成一串 `\\u7528\\u4f8b`。门禁最该说话
    的那一刻说出来的是转义序列，等于半个零信息，而且没有任何东西会报错提醒。
    直接往二进制层写 UTF-8（CI 的日志按 UTF-8 渲染）就绕开了 stdio 的编码。
    """
    buf = getattr(sys.stderr, "buffer", None)
    if buf is None:  # stderr 被换成了没有二进制层的东西，只能按文本层写
        sys.stderr.write(text)
        sys.stderr.flush()
        return
    sys.stderr.flush()
    buf.write(text.encode("utf-8", "replace"))
    buf.flush()
