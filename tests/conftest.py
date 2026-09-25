"""测试跑在 .venv（flask+pymupdf，无 matplotlib）。

需要科学栈的用例（worker round-trip）自行 spawn worker 解释器子进程，
本进程始终保持与 Flask 父进程相同的依赖边界。
"""

import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

import pytest

from support import shard as _shard

# 模块级设置：app / engine 的路径常量在 import 时求值，必须先于用例模块被
# import 就位，否则测试会写到真实的用户数据目录。单个用例仍各自 monkeypatch
# 具体常量，这里只是兜底护栏。
_DATA_DIR = tempfile.mkdtemp(prefix="tavotto-data-")
os.environ.setdefault("TAVOTTO_DATA_DIR", _DATA_DIR)

# 用户配置同理，而且**必须在 collection 之前**：`_isolated_user_config` 是 fixture，
# 管不到 collection 时求值的模块级常量——`WORKER_PY = pool.find_worker_python()`
# （test_mcp_normalize / test_compat_capture_parity / support.nativekit / support.bridgekit）
# 在那一刻读的若是开发机真实配置里的 `worker_python`，而用例里 `reset_worker_python()`
# 之后产品在隔离配置下重选了别的解释器，「参考侧」与「产品侧」就出自两个解释器：
# 像素 / 描述符对比恒红、stdout 里带上别人的路径（#452、#483）。主语是「哪个时刻」：
# collection 期与用例期读到的必须是**同一份**（空的）用户配置。
_CONFIG_DIR = tempfile.mkdtemp(prefix="tavotto-config-")
# **无条件**覆盖，不用 setdefault：开发者自己 export 过 TAVOTTO_CONFIG_DIR（指向
# 日常配置）时，setdefault 会让 collection 期仍读那份，而用例期的 fixture 又换成
# 临时目录——两个时刻又不是同一份了。
os.environ["TAVOTTO_CONFIG_DIR"] = _CONFIG_DIR

# 渲染控制面**默认走 Python 池**。开发机上 `cargo build` 之后
# `workerd/target/debug/tavotto-workerd` 就在那儿，pool 会自动认出来——
# 那样整套既有用例会在不知不觉间换一条控制面跑，「Python 实现是参考实现」
# 这件事就没人看着了。走 workerd 的用例自己把这个变量改掉（见
# tests/test_workerd_pool.py 的 workerd_enabled fixture）。
os.environ.setdefault("TAVOTTO_WORKERD", "0")

# 跑前的门里「找用户自己的 Python 并自动改用」（ADR 0079）**默认关**：开着的话每条走到真门的用例
# 都会去问这台机器的登录 shell、体检它的系统 Python，而某台 CI 机器上的 Python 碰巧装了用例要的包，
# 门就会自动改用——用例结果随机器而变。测这件事的用例自己打开（tests/test_user_environments.py）。
os.environ.setdefault("TAVOTTO_USER_ENV_DISCOVERY", "0")

# 匿名遥测在测试里**硬关**。用 setdefault 之外还要真的钉住：这不是「默认值」
# 那一类偏好，而是「测试进程绝不产生真实的 PostHog 事件」这条硬约束——
# 开发机上的用户配置里很可能已经同意过遥测（那是同一个 config.json），
# 而上面的 TAVOTTO_CONFIG_DIR 是 setdefault——外面已经设了就沿用外面那份。
# 遥测自己的用例把它摘掉并替换掉传输层（tests/test_telemetry.py）。
os.environ["TAVOTTO_NO_TELEMETRY"] = "1"

# ---------------------------------------------------------------------------
# 会话级零网络断言：遥测 endpoint 一次都不许被真的请求（#440）
# ---------------------------------------------------------------------------
# 上面那条硬开关管的是「同意判定」，管不到**判定之后**：发送线程在出队时判过
# `enabled()`，随后某条用例的 teardown 把 `_post` 与 `TAVOTTO_NO_TELEMETRY` 换回真的，
# 那一条就以真 `_post` 发到 PostHog——#440 里 249 个「source」幻影安装全是这么来的。
#
# 判据的主语：**这个 pytest 进程**里、**任何线程**、**整个会话任何时刻**（含用例之间与
# 收集期），对**遥测 endpoint 那台主机**调用了 `urllib.request.urlopen`。探针在 conftest
# import 时就装上（早于任何用例模块与 tavotto 的 import），命中就记下线程与当时的用例并
# **拒绝发出**，会话结束时有记录就判红。
#
# 前提与盲点（写在明处）：`engine/telemetry.py` 纯标准库，唯一的传输是按属性名调用的
# `urllib.request.urlopen`——这正是它能被这里截住的原因。哪天遥测换了传输（http.client
# 直连、`from urllib.request import urlopen` 早绑定），这道探针就量不到了，要跟着改。
# 用例自己 monkeypatch `urlopen` 期间调用走的是它的替身，也不经过这里；undo 之后回到探针。
_REAL_URLOPEN = urllib.request.urlopen
#: 命中记录：`线程名 | 当时的用例 | URL`。为空 = 本会话一次都没请求过遥测 endpoint。
_TELEMETRY_LEAKS: list[str] = []


def _refuse_telemetry_urlopen(url, *args, **kwargs):
    from tavotto.engine.telemetry import DEFAULT_ENDPOINT  # 地址唯一出处；调用时才 import

    target = url.full_url if isinstance(url, urllib.request.Request) else str(url)
    if urllib.parse.urlsplit(target).hostname == urllib.parse.urlsplit(DEFAULT_ENDPOINT).hostname:
        current = os.environ.get("PYTEST_CURRENT_TEST", "（不在任何用例里）")
        _TELEMETRY_LEAKS.append(f"{threading.current_thread().name} | {current} | {target}")
        raise urllib.error.URLError("测试进程不许请求真实遥测 endpoint（tests/conftest.py，#440）")
    return _REAL_URLOPEN(url, *args, **kwargs)


urllib.request.urlopen = _refuse_telemetry_urlopen


def _fail_session_on_telemetry_leaks(session) -> None:
    """会话结束：本进程请求过遥测 endpoint 就判红（#440 的零网络断言）。

    由下面的 `pytest_sessionfinish` 调用（一个 conftest 里同名钩子只能有一个）。
    判之前先把还活着的发送线程收掉（`reset_for_tests()` 会 join 它）：一条在飞的
    投递若要漏，就让它在判定之前漏进探针，而不是在判定之后。
    """
    telemetry = sys.modules.get("tavotto.engine.telemetry")
    if telemetry is not None:
        telemetry.reset_for_tests()
    if not _TELEMETRY_LEAKS:
        return
    _write_report_to_stderr(
        f"\n本会话请求了 {len(_TELEMETRY_LEAKS)} 次真实遥测 endpoint（已拦下，未发出；"
        "线程 | 当时的用例 | URL）：\n"
        + "\n".join(f"  - {line}" for line in _TELEMETRY_LEAKS)
        + "\n\n测试进程绝不产生真实的产品事件（docs/rules/backend/telemetry.md）。"
        "常见成因：fixture teardown 恢复真 `_post` 时发送线程还有一条在飞——"
        "见 `telemetry.reset_for_tests()` 的 join。\n"
    )
    if session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


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

#: 会话结束时的**嫌疑**：`(session, 该用的退出码)`。为空表示这条门禁没有发现问题。
#: 它只是嫌疑不是裁决——裁决要等 `pytest_unconfigure` 里退出前再枚举一次才作数。
_STUCK_THREAD_SUSPECTS: list[tuple[pytest.Session, int]] = []


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
    # 兜底数据目录会话结束就删。以前从不删：U04 / U05 起真 venv + 真 pip 的用例往 `environments/` 里建受管
    # 环境，一次会话 600 MB 上下，系统临时目录里攒了 3 311 个、31 GB，整机 ENOSPC，全量套件成片报
    # 「could not create numbered dir」（2026-09-22 U09 合并态全量撞上）。删不掉（Windows 上 worker 还握着
    # 句柄）就算了——下一次会话仍是新目录，不复用。放在线程判定之前：目录该删与线程泄不泄漏无关，
    # Ctrl+C 那一档也一样要删。验证在 tests/test_conftest_data_dir.py：子进程跑一个会话，结束后目录不在。
    shutil.rmtree(_DATA_DIR, ignore_errors=True)
    _fail_session_on_telemetry_leaks(session)
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
    # 这里只记**嫌疑**，不设 `session.exitstatus`、也不写报告——见 `pytest_unconfigure`。
    _STUCK_THREAD_SUSPECTS.append((session, int(exitstatus) or int(pytest.ExitCode.TESTS_FAILED)))


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    """**退出前再枚举一次**，然后报告 + 硬退出。

    放在 unconfigure 而不是在 `pytest_sessionfinish` 里抛异常，有两个理由：抛异常
    会打断 terminal reporter 的 wrapper（摘要那一行就没了），而且进程照样挂在
    `threading._shutdown()` 上。这里跑在摘要之后、所有插件收尾之后。

    「所有插件收尾之后」正是**必须重新枚举**的原因：别的插件完全可以在自己的
    `pytest_unconfigure` 里关掉它的非 daemon worker，那样解释器根本不会挂——拿
    `pytest_sessionfinish` 那一刻的名单直接判 1，判的是**过去那一刻的状态**，会把
    一个干净的会话诬告成红的。判据的主语要包含**哪个时刻**：这里的时刻是「就要
    退出了」，不是「用例刚跑完」。
    """
    if not _STUCK_THREAD_SUSPECTS:
        return
    session, code = _STUCK_THREAD_SUSPECTS[0]
    stuck = _threads_that_block_interpreter_exit()
    if not stuck:  # 有人在自己的 unconfigure 里收干净了——解释器不会挂，这不是缺陷
        return
    report = (
        f"用例跑完还有 {len(stuck)} 条非 daemon 线程活着，解释器会挂在退出上"
        f"（宽限 {_THREAD_SHUTDOWN_GRACE_SECONDS:g} 秒之后、所有插件收尾之后，"
        "仍然活着）：\n"
        f"{_describe_stuck_threads(stuck)}\n\n"
        "三条修法（`d9e2b60` 用的是前两条）：给等待一个有上限的形式"
        "（`wait(timeout)` 到点抛出，线程死掉而不是继续 park）、"
        "把线程标成 `daemon=True`、或者在用例里 join 干净再返回。"
    )
    # 退出码有两处兑现：`session.exitstatus`（`wrap_session` 在 unconfigure 之后才
    # `return session.exitstatus`）与下面的 `os._exit`。两处都设在**确认之后**，
    # 所以上面那条早退不会留下一个「已经被判红」的干净会话。
    session.exitstatus = code
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


# ---------------------------------------------------------------------------
# 按文件分片（CI03a）：`--shard K/N` 与 `--shard-manifest PATH`
# ---------------------------------------------------------------------------
# 分片在**同一进程 collection 之后**做（不是按目录切命令行）：每个 shard 进程都看见
# 完整的 collection（`-m "not slow"` 等 pytest 自己的反选已经生效——mark 插件的
# modifyitems 是 tryfirst，本钩子是 trylast），算出**全部 N 片**、自验并集 == 全集
# 之后，才把不属于自己的那些 deselect 掉。于是「漏片」在每个 shard 进程里都会当场
# rc 4，而不是等 CI 上两片都绿了才有人发现少了一个文件。
#
# **不带 `--shard` 时两个钩子完全不动 collection**：nightly.yml / desktop-tauri.yml 的
# pytest 命令一个字不改，它们跑的仍是全集——tests/test_merge_queue_workflows.py
# `test_unsharded_pytest_lanes_stay_unsharded` 钉住那两个文件里没有 `--shard`。
# lab（`_lab-qualification.yml`，含 release 的资格）的常规套件 2026-09-19 起在**同一台机器**
# 上起 N 个 `--shard=K/N` 进程——每个进程仍走上面那条自验（并集 == 全集），覆盖面与单进程
# 全集相同，被切开的只有时间；slow / 操作序列 harness 那两条仍不带 `--shard`。
# `test_lab_pytest_runs_every_shard_in_one_step`（形状）与
# `test_lab_regular_suite_step_exits_nonzero_iff_a_shard_fails`（真跑）钉住。
#
# 分配算法、权重表与自验的判据都在 tests/support/shard.py（纯函数，有单测）。


def pytest_addoption(parser):
    group = parser.getgroup("shard", "按文件分片（CI03a）")
    # 两个选项都要写成 `--opt=值`：它们是 conftest 注册的，pytest 预解析时会把未知选项的
    # 下一个 token 当路径去找 conftest——`--shard-manifest PATH` 在 PATH 已存在时只加载
    # PATH 所在目录的 conftest，本文件没被加载，选项就成了 unrecognized（rc 4）。
    group.addoption(
        "--shard",
        default=None,
        metavar="K/N",
        help="只跑第 K 片（1 ≤ K ≤ N），写成 --shard=K/N。按文件分组、按 "
        "tests/support/shard_weights.json 的权重贪心平衡；每个进程都先算全部 N 片并自验"
        "并集 == 全集。不带时不分片。",
    )
    group.addoption(
        "--shard-manifest",
        default=None,
        metavar="PATH",
        help="把这一片的分配写成 JSON，写成 --shard-manifest=PATH（只作证据与日后重平衡的"
        "数据，不作任何判定输入）。只在带 --shard 时有意义。",
    )


def _shard_file_of(item, rootpath) -> str:
    """item → 相对仓库根的 POSIX 路径（`tests/x.py`），与权重表的键同一形状。"""
    path = getattr(item, "path", None)
    if path is None:
        raise _shard.ShardError(f"{item.nodeid} 没有 path，分不了片")
    try:
        return path.relative_to(rootpath).as_posix()
    except ValueError as exc:
        raise _shard.ShardError(f"{path} 不在 rootdir {rootpath} 之下") from exc


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    spec = config.getoption("--shard")
    manifest_path = config.getoption("--shard-manifest")
    if spec is None:
        if manifest_path is not None:
            # 要证据却没分片：这是半套配置，不是「顺手写份空的」——静默写一份
            # 「全集 = 第 1/1 片」的 manifest 会让人拿它当分片证据用
            raise pytest.UsageError("--shard-manifest 只在带 --shard 时有意义")
        return
    try:
        shard, shards = _shard.parse_spec(spec)
        weights = _shard.load_weights()
        by_file: dict[str, list[str]] = {}
        for item in items:
            by_file.setdefault(_shard_file_of(item, config.rootpath), []).append(item.nodeid)
        plan = _shard.plan(by_file, weights, shard, shards)
        _shard.verify(plan, [item.nodeid for item in items])
    except _shard.ShardError as exc:
        raise pytest.UsageError(f"--shard {spec}：{exc}") from exc
    if manifest_path is not None:
        _write_shard_manifest(manifest_path, plan, config)
    config.stash[_SHARD_PLAN_KEY] = plan
    keep = set(plan.selected_nodeids)
    selected = [item for item in items if item.nodeid in keep]
    deselected = [item for item in items if item.nodeid not in keep]
    # 走 pytest 自己的 deselect 通道：摘要里会如实出现「N deselected」，
    # `--collect-only` 也只列这一片
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


_SHARD_PLAN_KEY = pytest.StashKey["_shard.Plan"]()


def pytest_report_collectionfinish(config, start_path, items):
    plan = config.stash.get(_SHARD_PLAN_KEY, None)
    if plan is None:
        return None
    # 这一行故意只用 ASCII：Windows runner 上捕获的 stdout 是 cp1252，pytest 遇到
    # 编不出的字符会整行转成 \uXXXX 转义（不崩，但没人读得懂）。manifest 才是证据，
    # 这行只是让日志里一眼看得到「这片跑了多少」。
    est = ", ".join(f"{s:.0f}s" for s in plan.estimated_seconds)
    return [
        f"shard {plan.shard}/{plan.shards}: files {len(plan.selected_files)}/"
        f"{sum(len(f) for f in plan.files)}, nodeids {len(plan.selected_nodeids)}/"
        f"{sum(len(n) for n in plan.nodeids)}, estimated per shard [{est}], "
        f"files without a weight {len(plan.unknown_files)} (default {plan.default_weight:g}s)"
    ]


def _git_head(rootpath) -> str:
    """manifest 里的 git_head：先问 git，再退回 GITHUB_SHA，都没有就 unknown。"""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(rootpath),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("GITHUB_SHA") or "unknown"


def _write_shard_manifest(path: str, plan, config) -> None:
    """一片的分配写成 JSON。只是证据：任何判定都不读它。"""
    import json
    import platform

    doc = {
        "git_head": _git_head(config.rootpath),
        "shard": plan.shard,
        "shards": plan.shards,
        "files_total": sum(len(f) for f in plan.files),
        "files_selected": len(plan.selected_files),
        "nodeids_total": sum(len(n) for n in plan.nodeids),
        "nodeids_selected": len(plan.selected_nodeids),
        "weights_source": _shard.WEIGHTS_PATH.relative_to(config.rootpath).as_posix(),
        "default_weight": plan.default_weight,
        "unknown_files": list(plan.unknown_files),
        "per_shard_estimated_seconds": [round(s, 2) for s in plan.estimated_seconds],
        "per_shard_files": [len(f) for f in plan.files],
        "per_shard_nodeids": [len(n) for n in plan.nodeids],
        "selected_files": list(plan.selected_files),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
