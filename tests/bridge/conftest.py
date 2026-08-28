"""native bridge 用例的夹具。纯函数助手在 `tests/support/bridgekit.py`。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from support import bridgekit
from tavotto.engine import bridge


@pytest.fixture
def user_python() -> str:
    if bridgekit.USER_PYTHON is None:
        pytest.skip("找不到装有 matplotlib 的解释器")
    return bridgekit.USER_PYTHON


@pytest.fixture
def clean_env(monkeypatch):
    """把开发机的 `PYTHONPATH` 摘掉——用户环境里不该有 Tavotto 的源码根。

    影响的是 `BridgeSession` 那条路径（它继承 `os.environ`）；直接 spawn
    的用例走 `bridgekit.child_env()`。
    """
    monkeypatch.delenv("PYTHONPATH", raising=False)


@pytest.fixture
def bridge_session(user_python, clean_env, tmp_path):
    """真起一条 native 会话（控制通道 + 认证 + 屏障），用完必关。

    刻意**不 mock**：认证、loopback、屏障、v1 信封回显——这些主张只有
    在真连接上才成立得了或塌得掉。
    """
    made: list = []

    @contextmanager
    def _make(target, *, cwd=None, argv=(), target_kind="script", out_dir=None, **kw):
        spec = bridge.spec_for(
            str(target),
            interpreter=user_python,
            argv=tuple(argv),
            target_kind=target_kind,
            cwd=cwd or str(tmp_path),
        )
        sess = bridge.BridgeSession(spec, out_dir=out_dir or (tmp_path / "bridge-out"), **kw)
        made.append(sess)
        sess.start()
        try:
            yield sess
        finally:
            sess.close()

    yield _make
    for sess in made:
        sess.close()
