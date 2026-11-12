"""逐段解析的性质测试（用户 2026-11-05 裁决「逐段解析」，ADR 0009 §1b；Codex #1757 三轮绕过的那一类）。

主语：**桥放行的路径，系统真正走过的那条路上没有凭据类对象、且（显式根一档）落点在根内**。不是只钉 Codex 给的几个
构造：固定种子随机生成含目录 / 文件 / 符号链接（链接名是凭据名、凭据名本身是目录、`..` 跨链接、指进 `~/.ssh`、
成环）的小目录树与随机路径，逐组断言。参照解析器（`_reference_walk`）与桥的实现分开写，并且每组先拿内核核对
它：参照的落点必须与 `os.path.realpath` 一致、文件存在时 inode 相同——参照错了这条测试自己先红，不会替桥作证。
失败时贪心缩小构造（删操作、删路径段）后打印最小反例。POSIX 专属：Windows 的 `..` 语义见同目录
`test_mcp_local_scope.py::_POSIX_DOTDOT`。
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "codex-plugin" / "mcp"))

from tavotto.engine.fixtools import readscope  # noqa: E402
from tavotto_mcp import bridge, roots  # noqa: E402
from tavotto_mcp.roots import ROOTS_ENV, SCOPE_ENV, SCOPE_LOCAL, WORKSPACE_ENVS  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX 符号链接语义；Windows 见 _POSIX_DOTDOT"
)

SEED = 1757
CASES = 300
NAMES = ["a", "b", "server.pem", "plain.txt", ".env", "keys"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for name in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bridge, "home_dirs", lambda: (os.path.realpath(h),))
    monkeypatch.setattr(roots, "home_dirs", lambda: (os.path.realpath(h),))
    (tmp_path / "plugin").mkdir()
    cwd = tmp_path / "host-app-dir"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.delenv(ROOTS_ENV, raising=False)
    for name in WORKSPACE_ENVS:
        monkeypatch.delenv(name, raising=False)
    try:
        (tmp_path / "probe").symlink_to(cwd)
    except OSError:
        pytest.skip("这台机器不能建符号链接")
    bridge._ROOT_AUTHORITY.reset()
    yield h
    bridge._ROOT_AUTHORITY.reset()


# ---------------------------------------------------------------- 生成
def _gen_case(rng: random.Random):
    """一组 = (建树操作表, 路径段, 是否绝对)。操作按顺序执行，父目录不存在的那条跳过。"""
    ops = [("dir", "a"), ("dir", "b")]
    if rng.random() < 0.5:
        ops.append(("ssh", ""))  # ~/.ssh/notes.txt
    for _ in range(rng.randint(4, 12)):
        depth = rng.randint(1, 2)
        rel = "/".join(rng.choice(NAMES) for _ in range(depth))
        kind = rng.choices(["dir", "file", "link"], weights=[3, 3, 5])[0]
        if kind != "link":
            ops.append((kind, rel))
            continue
        r = rng.random()
        if r < 0.15:
            target = "@ssh"  # 绝对，指进 ~/.ssh
        elif r < 0.2:
            target = "@self"  # 成环
        else:
            target = "/".join(rng.choice(NAMES + ["..", ".."]) for _ in range(rng.randint(1, 3)))
        ops.append(("link", rel, target))
    parts = [rng.choice(NAMES + ["..", "..", "."]) for _ in range(rng.randint(1, 5))]
    if rng.random() < 0.5:
        parts = ["a", *parts]
    if rng.random() < 0.4:
        # 「根里的目录链接指到别处的深层」+ 紧跟的 `..`：先折叠再解析会把落点算回根里（Codex #1757 r4473502316）
        deep, link = rng.choice(NAMES), rng.choice(NAMES)
        ops.append(("dir", "b/" + deep))
        ops.append(("link", "a/" + link, "../b/" + deep))
        parts = ["a", link, "..", *parts[-2:]]
        if rng.random() < 0.6:
            # 落点那一段：在链接目标那边是「凭据名的链接 → 普通文件」，在字面折叠那边是同名**目录**（Codex #1757
            # r4475737144 的形状；名字随机挑，不只 server.pem）
            name = rng.choice(NAMES)
            ops += [
                ("file", "b/plain.txt"),
                ("link", "b/" + name, "plain.txt"),
                ("dir", "a/" + name),
            ]
            parts = ["a", link, "..", name]
    return ops, parts, rng.random() < 0.5


def _build(base: Path, home: Path, ops) -> None:
    base.mkdir(parents=True)
    for op in ops:
        kind, rel = op[0], op[1]
        if kind == "ssh":
            (home / ".ssh").mkdir(exist_ok=True)
            (home / ".ssh" / "notes.txt").write_text("k", encoding="utf-8")
            continue
        p = base / rel
        if not p.parent.is_dir() or os.path.lexists(p):
            continue
        try:
            if kind == "dir":
                p.mkdir()
            elif kind == "file":
                p.write_text("x", encoding="utf-8")
            else:
                target = op[2]
                if target == "@ssh":
                    target = str(home / ".ssh")
                elif target == "@self":
                    target = p.name
                p.symlink_to(target)
        except OSError:
            continue


# ---------------------------------------------------------------- 参照
def _reference_walk(path: str):
    """与桥分开写的递归解析器：返回 (落点, 沿途对象)。只为当判据；每组都先拿内核核对它。"""
    visited: list[str] = []
    hops = [0]

    def walk(cur: str, parts: list[str]) -> str:
        for i, part in enumerate(parts):
            if part in ("", "."):
                continue
            if part == "..":
                cur = os.path.dirname(cur)
                continue
            nxt = os.path.join(cur, part)
            if os.path.islink(nxt):
                hops[0] += 1
                if hops[0] > 40:
                    raise OSError("loop")
                visited.append(nxt)
                tgt = os.readlink(nxt)
                cur = walk("/" if os.path.isabs(tgt) else cur, tgt.split("/"))
            elif os.path.lexists(nxt):
                visited.append(nxt)
                cur = nxt
            else:
                rest = [x for x in parts[i:] if x not in ("", ".")]
                if ".." in rest:
                    raise OSError("enoent")
                for x in rest:
                    cur = os.path.join(cur, x)
                    visited.append(cur)
                return cur
        return cur

    return walk("/", path.split("/")), visited


def _kernel_agrees(path: str, final: str) -> bool:
    if os.path.realpath(path) != os.path.realpath(final):
        return False
    try:
        a, b = os.stat(path), os.stat(final)
    except OSError:
        return True  # 落点不存在：只比路径
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _denied(home: Path, p: str) -> bool:
    scope = readscope.ReadScope(home=os.path.realpath(home), project_root=os.path.realpath(home))
    return scope.check(p).reason == readscope.REASON_DENIED


# ---------------------------------------------------------------- 一组
def _check_case(case, workdir: Path, home: Path, mode: str, monkeypatch) -> tuple[str | None, str]:
    """返回 (违例描述 | None, 这组的类别)。类别用来证明生成器真的造出了该拦的形状（反空）：
    `refused_bad` = 桥拒了、而参照确认那条路确实碰凭据 / 越根。"""
    ops, parts, absolute = case
    _build(workdir, home, ops)
    if mode == "explicit_roots":
        monkeypatch.setenv(ROOTS_ENV, str(workdir / "a"))
        bridge._ROOT_AUTHORITY.reset()
    rel = "/".join(parts)
    raw = os.path.join(str(workdir), rel)
    try:
        final, visited = _reference_walk(os.path.realpath(str(workdir)) + "/" + rel)
    except OSError:
        return None, "unopenable"  # 成环 / 不存在后又 ..：系统打不开，不构成泄露
    if not _kernel_agrees(raw, final):
        return f"参照解析器与内核不一致：{raw} -> 参照 {final}，内核 {os.path.realpath(raw)}", ""
    real = os.path.realpath(raw)
    if mode == "local":
        bad = [p for p in [*visited, real] if _denied(home, p)]
    else:
        root = os.path.realpath(str(workdir / "a"))
        bad = [] if bridge._within(real, root) else [real]
    roots_now = bridge.allowed_roots()
    if absolute:
        allowed = bridge._argv_path_escapes(raw, roots_now, None) is None
    else:
        allowed = bridge._argv_path_escapes(rel, roots_now, [str(workdir)]) is None
    if allowed and bad:
        what = "经过凭据对象" if mode == "local" else "越出根"
        return f"放行了{what}的路径 {rel!r}（绝对={absolute}）：{bad}", ""
    return None, ("refused_bad" if bad else "ok")


def _shrink(case, fails):
    ops, parts, absolute = case
    changed = True
    while changed:
        changed = False
        for i in range(len(ops)):
            cand = (ops[:i] + ops[i + 1 :], parts, absolute)
            if fails(cand):
                ops, changed = cand[0], True
                break
        for i in range(len(parts)):
            if len(parts) == 1:
                break
            cand = (ops, parts[:i] + parts[i + 1 :], absolute)
            if fails(cand):
                parts, changed = cand[1], True
                break
    return ops, parts, absolute


@pytest.mark.parametrize("mode", ["local", "explicit_roots"])
def test_allowed_paths_never_pass_through_a_credential_or_leave_the_root(home, monkeypatch, mode):
    if mode == "local":
        monkeypatch.setenv(SCOPE_ENV, SCOPE_LOCAL)
        bridge._ROOT_AUTHORITY.reset()
    rng = random.Random(f"{SEED}-{mode}")
    counter = [0]

    def run(case):
        counter[0] += 1
        return _check_case(case, home / "t" / str(counter[0]), home, mode, monkeypatch)

    kinds: dict[str, int] = {}
    for _ in range(CASES):
        case = _gen_case(rng)
        problem, kind = run(case)
        if problem:
            small = _shrink(case, lambda c: run(c)[0] is not None)
            pytest.fail(
                f"{problem}\n最小构造：ops={small[0]!r}\n路径段={small[1]!r} 绝对={small[2]}"
            )
        kinds[kind] = kinds.get(kind, 0) + 1
    # 反空：生成器确实造出了足够多「该拦」的路径，而桥都拦了
    assert kinds.get("refused_bad", 0) >= 20, kinds
    assert kinds.get("ok", 0) >= 20, kinds
