"""`tavotto run` 控制面用例的夹具（ADR 0021）。纯函数助手在
`tests/support/nativekit.py`。"""

from __future__ import annotations

import pytest

from support import nativekit
from tavotto.engine import envlease, nativesession


@pytest.fixture
def user_python() -> str:
    if nativekit.USER_PYTHON is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    return nativekit.USER_PYTHON


@pytest.fixture(autouse=True)
def _clean_registries():
    """每条用例都从空表开始，结束时**把会话真的收掉**。

    进程级单例（`nativesession.REGISTRY` / `envlease` 的两张表）在同一个
    pytest 进程里是共享的。不清的话，一条用例留下的租约会让下一条的
    `acquire_native` 直接报"这个环境正在装依赖"——而那条红出现在完全无关的
    文件里（仓库里有过同款：单跑绿、全量红）。
    """
    envlease.reset_for_tests()
    yield
    for session in nativesession.REGISTRY.list():
        try:
            session.shutdown()
        except Exception:  # noqa: BLE001 — 收尾不该让用例红
            pass
        nativesession.REGISTRY.forget(session.session_id)
    envlease.reset_for_tests()
