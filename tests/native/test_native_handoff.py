"""一次性 native 交接凭据（ADR 0021 §4）。

这一批看护的是**凭据的作用域**：谁读得到、能用几次、过期之后还算不算、
前端拿到的那份里有没有 token。每一条对应一种"看起来没事"的写法。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time

import pytest

from tavotto.engine import nativehandoff, runcodes
from tavotto.engine.runcodes import RunError

FIELDS = {
    "project_root": "/p",
    "interpreter": "/p/.venv/bin/python",
    "cwd": "/p",
    "target_kind": "script",
    "target_display": "figure.py",
    "arg_count": 2,
    "command_fingerprint": "f" * 32,
    "permission_key": "k" * 32,
    "python_version": "3.13.1",
    "attach_host": "127.0.0.1",
    "attach_port": 51234,
    "attach_token": "TOKEN-SHOULD-NEVER-LEAK",
}


@pytest.fixture(autouse=True)
def _isolated_native_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVOTTO_DATA_DIR", str(tmp_path / "data"))
    yield


def make(**kw) -> str:
    native_id, _ = nativehandoff.create(**{**FIELDS, **kw})
    return native_id


def test_descriptor_private():
    """目录 0700、文件 0600。**能读它的进程本来就能读用户的任何文件**，
    但同机上的**别的用户**不该读得到——那里面有一枚能连上控制通道的 token。"""
    native_id = make()
    path = os.path.join(nativehandoff.native_dir(), f"{native_id}.json")
    if sys.platform == "win32":  # Windows 没有等价位，靠用户 profile 的 ACL
        pytest.skip("POSIX 权限位判据")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(nativehandoff.native_dir()).st_mode) == 0o700


def test_descriptor_single_use():
    native_id = make()
    first = nativehandoff.consume(native_id)
    assert first["attach_token"] == FIELDS["attach_token"]
    with pytest.raises(RunError) as exc:
        nativehandoff.consume(native_id)
    assert exc.value.code == runcodes.NATIVE_HANDOFF_CONSUMED


def test_the_tombstone_keeps_no_token_and_no_metadata():
    """取用之后原文件被一份墓碑替换：**没有 token、没有 metadata、没有端口**。

    留着原文件的表现是：会话早就结束了，那枚 token 还躺在磁盘上。
    """
    native_id = make()
    nativehandoff.consume(native_id)
    path = os.path.join(nativehandoff.native_dir(), f"{native_id}.json")
    raw = open(path, encoding="utf-8").read()
    assert FIELDS["attach_token"] not in raw
    data = json.loads(raw)
    assert data["state"] == nativehandoff.STATE_CONSUMED
    assert "metadata" not in data and "relay" not in data and "attach_token" not in data


def test_descriptor_expiry():
    native_id = make(ttl=1.0, now=1000.0)
    assert nativehandoff.peek(native_id, now=1000.5)
    with pytest.raises(RunError) as exc:
        nativehandoff.peek(native_id, now=1002.0)
    assert exc.value.code == runcodes.NATIVE_HANDOFF_EXPIRED


def test_descriptor_cancel_is_distinguishable_and_idempotent():
    """取消与"已被别处取用"是两种情况，用户的下一步动作完全不同。"""
    native_id = make()
    nativehandoff.cancel(native_id)
    nativehandoff.cancel(native_id)  # 幂等
    with pytest.raises(RunError) as exc:
        nativehandoff.consume(native_id)
    assert exc.value.code == runcodes.NATIVE_ATTACH_CANCELLED


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "  ",
        "not-hex",
        "0" * 31,
        "0" * 33,
        "0123456789ABCDEF0123456789abcdef",  # 大写
        "../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
    ],
)
def test_descriptor_bad_id(bad):
    """ID 格式判据在**最外层**：一个不该被当成 ID 的串根本不该走到 open()。"""
    with pytest.raises(RunError) as exc:
        nativehandoff.peek(bad)
    assert exc.value.code == runcodes.NATIVE_HANDOFF_INVALID


def test_descriptor_path_escape(tmp_path):
    """symlink 把 `<dir>/<id>.json` 指到别处 → 拒绝。

    这个目录是**用户自己**能写的（0700 挡的是别人）。格式判据挡不住
    symlink——判据必须是"realpath 之后仍在这个目录下"。
    """
    if sys.platform == "win32":
        pytest.skip("symlink 判据（Windows 上建 symlink 要额外权限）")
    native_id = make()
    path = os.path.join(nativehandoff.native_dir(), f"{native_id}.json")
    # **symlink 的目标必须是一份完全合法的 pending descriptor**，否则这条用例
    # 量的就不是路径判据了：内容随便写的话，`_check_live` 会因为缺 `state`
    # 而拒绝——抽掉 realpath 那道检查它照样红，判据是空的（第一轮就是这样）。
    outside = tmp_path / "elsewhere.json"
    outside.write_text(open(path, encoding="utf-8").read(), encoding="utf-8")
    os.unlink(path)
    os.symlink(str(outside), path)
    assert json.loads(outside.read_text(encoding="utf-8"))["state"] == (
        nativehandoff.STATE_PENDING
    ), "用例前提：目标是一份本来会被接受的凭据"
    with pytest.raises(RunError) as exc:
        nativehandoff.peek(native_id)
    assert exc.value.code == runcodes.NATIVE_HANDOFF_INVALID


def test_a_descriptor_whose_content_disagrees_with_its_name_is_refused():
    """文件名与内容对不上：被人动过，或者一次半截写入。两种都不该继续。"""
    native_id = make()
    path = os.path.join(nativehandoff.native_dir(), f"{native_id}.json")
    data = json.loads(open(path, encoding="utf-8").read())
    data["native_id"] = "0" * 32
    open(path, "w", encoding="utf-8").write(json.dumps(data))
    with pytest.raises(RunError) as exc:
        nativehandoff.peek(native_id)
    assert exc.value.code == runcodes.NATIVE_HANDOFF_INVALID


def test_descriptor_token_not_frontend():
    """前端拿到的那份里**没有 token、没有端口、没有 host**。

    端口也不给：前端能提交的只有 `native_id`，连哪儿是后端的事。少给一个
    字段就少一处可能被拼进 URL 或日志的地方。
    """
    native_id = make()
    public = nativehandoff.sanitized(native_id)
    blob = json.dumps(public, ensure_ascii=False)
    assert FIELDS["attach_token"] not in blob
    assert "51234" not in blob
    assert "attach_port" not in public and "relay" not in public and "host" not in public
    # 该有的确实有——**判据要证明观测是有效的**，不能只证明"没有坏东西"
    assert public["interpreter"] == FIELDS["interpreter"]
    assert public["target_display"] == "figure.py"
    assert public["arg_count"] == 2


def test_prune_removes_stale_and_keeps_live():
    """陈旧凭据由**下一次 create** 顺手清掉——CLI 崩了不会有人替它收尾，
    而这个目录只有本用户在写、量很小，所以这是唯一稳定会跑到的时机。"""
    old = make(ttl=1.0, now=1000.0)
    assert nativehandoff.peek(old, now=1000.5), "用例前提：它一开始是活的"
    fresh = make(ttl=600.0)  # ← 这一次 create 内部会 prune
    with pytest.raises(RunError):
        nativehandoff.peek(old)
    assert nativehandoff.peek(fresh)
    # 显式调也算数（进程启动时的那一次）
    assert nativehandoff.prune_stale(now=time.time() + 10_000) >= 1


def test_prune_also_removes_unreadable_files():
    """读不懂的文件一律当过期：它对任何人都没有用了。"""
    os.makedirs(nativehandoff.native_dir(), exist_ok=True)
    junk = os.path.join(nativehandoff.native_dir(), "not-a-descriptor.json")
    open(junk, "w", encoding="utf-8").write("{{{")
    nativehandoff.prune_stale()
    assert not os.path.exists(junk)


def test_the_descriptor_never_carries_the_environment():
    """`create()` 的参数表就是"允许放什么"的清单——里面没有 env、没有 argv
    的值、没有 package index、也没有子进程那枚 token。"""
    import inspect

    params = set(inspect.signature(nativehandoff.create).parameters)
    forbidden = {"env", "environ", "argv", "user_argv", "package_index", "child_token", "secret"}
    assert not (params & forbidden), f"descriptor 多了不该有的字段: {params & forbidden}"
