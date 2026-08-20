"""测试跑在 .venv（flask+pymupdf，无 matplotlib）。

需要科学栈的用例（worker round-trip）自行 spawn worker 解释器子进程，
本进程始终保持与 Flask 父进程相同的依赖边界。
"""
import os
import tempfile

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
    box.clear()                 # 同意本身那两条不参与产品埋点的断言
    yield box
    telemetry.reset_for_tests()
