"""`.git-blame-ignore-revs` 的形状判据。

这个文件的两种写错方式后果不同，而且**都不会自己喊疼**——实测过：

* 缩写 SHA（`091751c`）→ 配了 `blame.ignoreRevsFile` 的人执行任何
  `git blame` 都会 `fatal: invalid object name`，整台机器上的 blame 全废；
* 不存在的全长 SHA → git 静默 exit 0，**什么都不跳过**。文件里躺着那一行，
  看上去一切正常，实际一点用没有。

第二种正是这次迁移的现实风险：合并队列强制 SQUASH，PR 分支上那个提交的 SHA
和最终落到 main 上的**不是同一个**。忘了回来换，就得到一个永远静默失效的文件。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REVS = Path(__file__).resolve().parents[1] / ".git-blame-ignore-revs"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def _entries() -> list[tuple[int, str]]:
    """(行号, SHA)，跳过注释与空行。"""
    out = []
    for n, raw in enumerate(REVS.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append((n, line))
    return out


def test_the_file_lives_at_the_repo_root():
    """GitHub 只认根目录下的这一个路径；挪走等于网页版 blame 不再跳过。"""
    assert REVS.is_file(), f"{REVS} 不在仓库根目录"


def test_every_entry_is_a_full_lowercase_sha():
    entries = _entries()
    assert entries, ".git-blame-ignore-revs 里一条 SHA 都没有——加过又删了？"
    for n, sha in entries:
        assert FULL_SHA.match(sha), (
            f"第 {n} 行不是 40 位小写十六进制：{sha!r}。"
            "缩写会让所有配了 blame.ignoreRevsFile 的人 git blame 直接 fatal。"
        )


def test_no_duplicate_entries():
    shas = [sha for _, sha in _entries()]
    dupes = {s for s in shas if shas.count(s) > 1}
    assert not dupes, f"重复登记：{sorted(dupes)}"


def test_every_entry_points_at_a_commit_that_exists():
    """挡住「静默失效」那一种（开发机上；CI 由 workflow 里那一步负责）。

    **只在完整克隆上跑得动**：CI 的 `actions/checkout` 默认 `fetch-depth: 1`，
    历史里的对象根本没下载下来，`cat-file` 必然查不到——那种情况下这条判据
    无法区分「SHA 写错了」和「对象没 fetch」，于是显式 skip。

    **但「把盲点写在明处」不等于「补上了」。** 只有这一条时，一个不存在的
    40 位 SHA 能通过全部 CI 门禁——正是它声称要防的那种静默失效（Codex 在
    #176 上指出的就是这个）。补法在 `.github/workflows/ci.yml` 的 `python-lint`
    里：那一步按 SHA 做**定向 fetch**（GitHub 允许，实测存在返回 0、不存在
    返回 128），所以浅克隆也拦得住，且不必把 checkout 换成 `fetch-depth: 0`。
    `TestPythonLint.test_blame_ignore_revs_existence_is_gated_in_ci` 盯着那一步
    别被删掉。
    """
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REVS.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()
    if shallow == "true":
        pytest.skip("浅克隆：历史对象没下载，存在性无法与「写错了」区分开")

    for n, sha in _entries():
        got = subprocess.run(
            ["git", "cat-file", "-t", sha],
            cwd=REVS.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert got.returncode == 0 and got.stdout.strip() == "commit", (
            f"第 {n} 行的 {sha} 在本仓库里不是一个存在的 commit。"
            "git 对这种情况**静默放过**，文件会一直摆在那儿却什么都不跳过。"
            "合并队列强制 SQUASH——PR 分支上的 SHA 和落到 main 的不是同一个，"
            "多半是忘了回来换成 squash 之后的那个。"
        )
