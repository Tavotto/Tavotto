"""macOS 桌面版 PATH 补全（engine/loginpath）：只读 /etc/paths 与 /etc/paths.d，只追加缺的存在目录。"""

import os
import subprocess
import sys

import pytest

from tavotto import desktop
from tavotto.engine import loginpath, pool, runtime

SEP = os.pathsep


@pytest.fixture
def etc(tmp_path):
    """伪造的 /etc：返回 (root, mkdir)——mkdir 建出真实存在的目录并返回路径。"""
    root = tmp_path / "etc"
    (root / "paths.d").mkdir(parents=True)

    def mk(name):
        d = tmp_path / "dirs" / name
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    return root, mk


def _aug(current, root):
    return loginpath.augmented_path(current, etc_root=str(root), platform="darwin")


def test_paths_then_paths_d_sorted_by_filename(etc):
    root, mk = etc
    a, b, c = mk("a"), mk("b"), mk("c")
    (root / "paths").write_text(f"{a}\n")
    (root / "paths.d" / "20-z").write_text(f"{c}\n")
    (root / "paths.d" / "10-y").write_text(f"{b}\n")
    assert _aug("/usr/bin", root) == SEP.join(["/usr/bin", a, b, c])


def test_blank_lines_and_comments_are_ignored(etc):
    root, mk = etc
    a = mk("a")
    (root / "paths").write_text(f"\n# note\n   \n  {a}  \n")
    assert _aug("/usr/bin", root) == SEP.join(["/usr/bin", a])


def test_missing_directory_is_not_added(etc, tmp_path):
    root, mk = etc
    a = mk("a")
    (root / "paths").write_text(f"{tmp_path}/nope\n{a}\n")
    assert _aug("/usr/bin", root) == SEP.join(["/usr/bin", a])


def test_already_present_is_not_duplicated_and_order_untouched(etc):
    root, mk = etc
    a, b = mk("a"), mk("b")
    (root / "paths").write_text(f"{a}\n{b}\n")
    current = SEP.join([b, "/usr/bin", b, a])  # 既有项的顺序与重复都不动
    assert _aug(current, root) == current


def test_nothing_to_add_returns_input_unchanged(etc):
    root, _ = etc
    assert _aug("/usr/bin:/bin", root) == "/usr/bin:/bin"
    assert _aug(None, root) is None
    assert _aug("/usr/bin", root / "missing-root") == "/usr/bin"


def test_unset_path_gets_the_listed_dirs(etc):
    root, mk = etc
    a = mk("a")
    (root / "paths").write_text(f"{a}\n")
    assert _aug(None, root) == a


@pytest.mark.parametrize("plat", ["linux", "win32"])
def test_non_macos_is_unchanged(etc, plat):
    root, mk = etc
    (root / "paths").write_text(mk("a") + "\n")
    assert loginpath.augmented_path("/usr/bin", etc_root=str(root), platform=plat) == "/usr/bin"
    env = {"PATH": "/usr/bin"}
    assert loginpath.apply_to_environ(env, etc_root=str(root), platform=plat) is False
    assert env == {"PATH": "/usr/bin"}


def test_apply_to_environ_reports_change(etc):
    root, mk = etc
    a = mk("a")
    (root / "paths").write_text(f"{a}\n")
    env = {"PATH": "/usr/bin", "OTHER": "1"}
    assert loginpath.apply_to_environ(env, etc_root=str(root), platform="darwin") is True
    assert env == {"PATH": f"/usr/bin{SEP}{a}", "OTHER": "1"}
    assert loginpath.apply_to_environ(env, etc_root=str(root), platform="darwin") is False  # 幂等


@pytest.fixture
def patched_environ(etc, monkeypatch):
    root, mk = etc
    texbin = mk("texbin")
    (root / "paths.d" / "TeX").write_text(f"{texbin}\n")
    monkeypatch.setattr(loginpath, "_ETC", str(root))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("PATH", SEP.join(["/usr/bin", "/bin"]))  # Windows 的分隔符是 `;`
    return texbin


def test_desktop_run_applies_it_before_anything_else(patched_environ, monkeypatch):
    for k in ("TAVOTTO_DESKTOP_NONCE", "TAVOTTO_DESKTOP_HANDSHAKE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(desktop, "read_launch_credentials", lambda: (None, None, None, None))
    assert desktop.run(object()) == 2  # 无凭据拒绝启动——但 PATH 已先补好
    assert os.environ["PATH"].split(SEP)[-1] == patched_environ


def _child_path(env):
    out = subprocess.run(
        [sys.executable, "-c", "import os;print(os.environ['PATH'])"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    return out.stdout.strip().split(SEP)


@pytest.mark.parametrize("source", [pool.SOURCE_SYSTEM, pool.SOURCE_BUNDLED])
def test_children_of_every_spawn_path_see_the_added_dir(patched_environ, source):
    loginpath.apply_to_environ()
    # worker 的 env：用户的环境 = None（原样继承 os.environ）；内置 runtime 在 os.environ 上叠 child_env
    env = pool.worker_env(sys.executable, source)
    child = _child_path(env)
    assert child[:2] == ["/usr/bin", "/bin"] and child[-1] == patched_environ


def test_probe_env_and_worker_env_agree(patched_environ):
    loginpath.apply_to_environ()
    for bundled in (False, True):
        source = pool.SOURCE_BUNDLED if bundled else pool.SOURCE_SYSTEM
        probe = runtime.probe_env(sys.executable, bundled=bundled)
        worker = pool.worker_env(sys.executable, source)
        p = (probe if probe is not None else os.environ)["PATH"]
        w = (worker if worker is not None else os.environ)["PATH"]
        assert p == w and p.split(SEP)[-1] == patched_environ
