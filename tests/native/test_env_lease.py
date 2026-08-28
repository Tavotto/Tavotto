"""环境租约：native 会话与 pip 安装的互斥（ADR 0021 §6）。

`#177` 的那把锁装在 `pool` 里、只看得见池里的 worker，而 **native 会话不经过
池**——它由 `tavotto run` CLI 自己 spawn 用户的解释器。所以那把锁对它是
**机制上不可见**的，不是"漏了一个分支"。

两条方向相反的拒绝，各有各的理由：

* 正在装包 → 不许起 native 会话（半装完的 site-packages 上 import 到一半，
  失败会落在**用户自己的脚本**上，他会去怀疑自己的代码）；
* 有活跃 native 会话 → 不许装包，且**绝不自动杀它**（那个进程是用户的，
  里面可能有跑了两小时的计算；装依赖是一件可以等的事）。
"""

from __future__ import annotations

import threading

import pytest

from tavotto.engine import deprepair, envlease, pool, runcodes

PY_A = "/env/a/bin/python"
PY_B = "/env/b/bin/python"


@pytest.fixture(autouse=True)
def _clean():
    envlease.reset_for_tests()
    yield
    envlease.reset_for_tests()


# --------------------------------------------------------------------------
def test_mutation_blocks_native_start():
    with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
        with pytest.raises(envlease.EnvironmentBusy) as exc:
            envlease.acquire_native(PY_A, "s1")
        assert exc.value.code == envlease.ENVIRONMENT_MUTATING
    envlease.acquire_native(PY_A, "s1")  # 装完就放行


def test_active_native_blocks_install():
    envlease.acquire_native(PY_A, "s1")
    with pytest.raises(envlease.EnvironmentBusy) as exc:
        with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
            pass
    assert exc.value.code == runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION
    assert exc.value.extra["native_sessions"] == ["s1"]
    assert "Tavotto Run" in str(exc.value)


def test_an_active_native_session_is_never_killed_by_an_installer():
    """**不自动杀**。装依赖可以等，杀掉用户的脚本不行。

    判据是"拒绝之后那条租约还在"——如果实现改成"先收掉再装"，这条会红。
    """
    envlease.acquire_native(PY_A, "s1")
    with pytest.raises(envlease.EnvironmentBusy):
        with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
            pass
    assert envlease.native_sessions_on(PY_A) == ["s1"]


def test_unrelated_environment_not_blocked():
    """粒度是**一个环境**，不是全局：A 项目在跑脚本，B 项目照常装包。"""
    envlease.acquire_native(PY_A, "s1")
    with envlease.mutating(envlease.env_key_of(PY_B), PY_B):
        assert envlease.is_mutating(PY_B)
        assert not envlease.is_mutating(PY_A)


def test_lease_released_on_exit():
    with envlease.native_lease(PY_A, "s1"):
        assert envlease.native_sessions_on(PY_A) == ["s1"]
    assert envlease.native_sessions_on(PY_A) == []


def test_lease_released_on_crash():
    """会话是异常结束的——租约也必须还回去。

    漏掉一次的后果不是"这条会话还占着"，是**这个环境从此再也装不了依赖**，
    而用户完全看不出为什么（他明明已经关掉了那个终端）。
    """
    with pytest.raises(ZeroDivisionError):
        with envlease.native_lease(PY_A, "s1"):
            raise ZeroDivisionError
    assert envlease.native_sessions_on(PY_A) == []


def test_a_failed_acquire_does_not_release_someone_elses_lease():
    """拿不到租约时**不进 try**——否则 finally 会去释放一条从没登记过的租约，
    把别人那条一起清掉。"""
    envlease.acquire_native(PY_A, "held")
    with envlease.mutating("other-key"):
        pass
    with pytest.raises(envlease.EnvironmentBusy):
        with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
            pass
    assert envlease.native_sessions_on(PY_A) == ["held"]


def test_two_native_sessions_on_one_environment_are_both_counted():
    """两个终端跑同一个 venv 是**正常的**——它们不互斥，只是一起挡住安装。"""
    envlease.acquire_native(PY_A, "s1")
    envlease.acquire_native(PY_A, "s2")
    envlease.release_native(PY_A, "s1")
    assert envlease.native_sessions_on(PY_A) == ["s2"]
    with pytest.raises(envlease.EnvironmentBusy):
        with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
            pass
    envlease.release_native(PY_A, "s2")
    with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
        pass  # 最后一条走了才放行


def test_state_of_reports_the_closed_set():
    assert envlease.state_of(PY_A) == envlease.STATE_IDLE
    assert envlease.state_of(PY_A, safe_workers=2) == envlease.STATE_SAFE_WORKERS
    envlease.acquire_native(PY_A, "s1")
    assert envlease.state_of(PY_A, safe_workers=2) == envlease.STATE_NATIVE_SESSIONS
    envlease.release_native(PY_A, "s1")
    with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
        assert envlease.state_of(PY_A) == envlease.STATE_MUTATING


# --------------------------------------------------------------------------
# 与 pool / deprepair 的接线
# --------------------------------------------------------------------------
def test_pool_is_a_consumer_not_a_second_table():
    """`pool` 的那几个名字现在是 `envlease` 的外壳。

    两张表的形状本身就保证了它们迟早会不一致——这条钉住"只有一张"。
    """
    assert pool.EnvironmentBusy is envlease.EnvironmentBusy
    assert pool.env_key_of is envlease.env_key_of
    assert pool.is_mutating is envlease.is_mutating
    assert pool.ENVIRONMENT_MUTATING == envlease.ENVIRONMENT_MUTATING
    envlease.acquire_native(PY_A, "s1")
    assert pool.is_mutating(PY_A) is False  # native 不是 mutating，两回事
    with pytest.raises(pool.EnvironmentBusy):
        with pool.mutating_environment(pool.env_key_of(PY_A), PY_A):
            pass


def test_deprepair_reports_the_native_case_with_its_own_code():
    """ "另一次安装在跑"与"有人在跑脚本"折成同一个码的话，前端只能给一句
    「忙，稍后再试」——而"稍后"对 native 那一条永远不会到来。"""
    busy = envlease.EnvironmentBusy("x", code=runcodes.ENVIRONMENT_IN_USE_BY_NATIVE_SESSION)
    assert deprepair._busy_error(busy).code == deprepair.ERROR_IN_USE_BY_NATIVE  # noqa: SLF001
    other = envlease.EnvironmentBusy("y")
    assert deprepair._busy_error(other).code == deprepair.ERROR_BUSY  # noqa: SLF001


def test_concurrent_acquire_and_mutate_never_both_win():
    """并发下**只有一个赢**。两张表的时候这条恰恰是最难说清的那一半。"""
    results: list[str] = []
    start = threading.Barrier(2)

    def _native():
        start.wait()
        try:
            envlease.acquire_native(PY_A, "s1")
            results.append("native")
        except envlease.EnvironmentBusy:
            results.append("native-refused")

    def _install():
        start.wait()
        try:
            with envlease.mutating(envlease.env_key_of(PY_A), PY_A):
                results.append("install")
        except envlease.EnvironmentBusy:
            results.append("install-refused")

    for _ in range(40):
        envlease.reset_for_tests()
        results.clear()
        start = threading.Barrier(2)
        threads = [threading.Thread(target=_native), threading.Thread(target=_install)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sorted(results) in (
            ["install", "native"],  # 安装先跑完再起会话，或反过来（各自不重叠）
            ["install-refused", "native"],
            ["install", "native-refused"],
        ), results
