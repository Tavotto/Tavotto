"""Playwright e2e 在 CI 里**跑在哪条腿上**——以及谁在依赖这个事实。

「收得到 ≠ 跑得过」：`web/e2e/` 收着一整套 spec，但整个 CI 里真正执行
`pnpm e2e` 的只有一处。哪条腿在跑，直接决定了哪些 `test.skip` 会**恒真**，
而恒真的 skip 不会有任何门禁说话（`.github/AGENTS.md`：空转的门禁比没有门禁
更坏）。

这个前提曾经被**写反**：`web/e2e/error-recovery-en.spec.ts` 末尾的注释说
「e2e workflow 目前只有 Ubuntu 腿」，并据此决定不写 Windows 文件占用
（`file_locked`）的界面用例——而唯一存在的那条腿恰恰是 Windows
（`.github/workflows/ci.yml` 的 `windows-exe-smoke`）。前提反了，从它推出来的
那个「不写」的决定也就跟着错了；同一份文件里两条 `test.skip(win32)` 则相反，
它们在唯一的那条腿上恒跳过，从来没有进过断言（issue #30）。

所以把这个前提**做进结构**，别再留在散落的注释里：

* 腿的拓扑一变（加一条 POSIX 腿 / 换 runner / 多出第二处 `pnpm e2e`），这里
  当场红，提醒回去重新评估每一处依赖它的 skip；
* 每条 `test.skip(process.platform === 'win32', …)` 的理由里必须写清「它因此
  在 CI 里一次都不会执行」，别让下一个人再从注释里读到一个反的前提。

与 `tests/test_merge_queue_workflows.py` 同一条纪律：**不用 PyYAML**（它不在
`.venv` 里，`importorskip` 会让整个模块静默跳过——那正是空门禁），用只认本仓库
缩进形状的字符串判据，解析不出预期形状时当场抛。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
E2E = ROOT / "web" / "e2e"

#: 唯一执行 `pnpm e2e` 的 job，以及它的 runner。
E2E_JOB = "windows-exe-smoke"
E2E_RUNNER = "windows-latest"

#: 带 `test.skip(process.platform === 'win32')` 的 spec，以及各自的条数。
#: 这是**枚举**不是白名单：新增一条 POSIX-only 用例就必须回到这里，
#: 顺便被提醒它在 CI 里不会执行。
WIN32_SKIPS = {"error-recovery-en.spec.ts": 2}

#: 上面每一条 skip 的理由里必须出现的那句话。
NEVER_RUNS_MARKER = "CI 无 POSIX e2e 腿"


def _code(text: str) -> str:
    """剥掉注释行——判据只看会被执行的部分。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _job(text: str, job_id: str) -> str:
    """按缩进切出一个 job 块；切不出来当场抛（安静的空判据比没有更坏）。"""
    m = re.search(rf"(?m)^  {re.escape(job_id)}:\n(.*?)(?=^  [\w-]+:|\Z)", text, re.S)
    assert m, f"ci.yml 里切不出 job `{job_id}`——缩进形状变了？"
    return m.group(0)


def _workflows() -> list[Path]:
    files = sorted(WF.glob("*.yml"))
    assert files, ".github/workflows 下一个 workflow 都没读到——路径变了？"
    return files


def _e2e_invocations() -> list[tuple[str, int]]:
    """全仓 workflow 里会被执行的 `pnpm e2e` 调用点（文件名, 行号）。"""
    hits: list[tuple[str, int]] = []
    for wf in _workflows():
        for i, ln in enumerate(_code(wf.read_text(encoding="utf-8")).splitlines(), 1):
            if re.match(r"^\s*pnpm e2e\b", ln):
                hits.append((wf.name, i))
    return hits


class TestLegTopology:
    def test_pnpm_e2e_has_exactly_one_invocation(self):
        """多一处就说明拓扑变了——依赖「只有一条腿」的每处 skip 都要重估。"""
        hits = _e2e_invocations()
        assert len(hits) == 1, (
            f"`pnpm e2e` 的调用点从 1 处变成了 {len(hits)} 处：{hits}。"
            "腿的拓扑变了，请回去重估 web/e2e 里每一条按平台跳过的用例"
            "（issue #30），再更新本判据。"
        )
        assert hits[0][0] == "ci.yml", f"`pnpm e2e` 挪出了 ci.yml：{hits}"

    def test_the_only_leg_is_windows(self):
        """腿在哪个平台，决定了哪些 skip 恒真。写反过一次（issue #30）。"""
        ci = _code((WF / "ci.yml").read_text(encoding="utf-8"))
        block = _job(ci, E2E_JOB)
        assert re.search(rf"(?m)^\s+runs-on: {re.escape(E2E_RUNNER)}\s*$", block), (
            f"job `{E2E_JOB}` 不再跑在 {E2E_RUNNER} 上——"
            "web/e2e 里按平台跳过的用例要跟着重估（issue #30）"
        )
        assert re.search(r"(?m)^\s*pnpm e2e\b", block), (
            f"`pnpm e2e` 不在 job `{E2E_JOB}` 里了——本模块的前提整个变了"
        )


class TestWin32SkipsDeclareTheyNeverRun:
    """恒跳过的用例必须自己说出「我在 CI 里不会执行」。"""

    @staticmethod
    def _skips(path: Path) -> list[str]:
        pat = r"test\.skip\(\s*process\.platform === 'win32'\s*,\s*'([^']*)'"
        return re.findall(pat, path.read_text(encoding="utf-8"))

    def test_the_enumeration_still_matches_the_specs(self):
        """枚举与现实漂开 = 判据看不见新增的那一条。"""
        found = {
            p.name: len(self._skips(p)) for p in sorted(E2E.glob("*.spec.ts")) if self._skips(p)
        }
        assert found == WIN32_SKIPS, (
            f"web/e2e 里 `test.skip(win32)` 的分布变了：{found} != {WIN32_SKIPS}。"
            f"CI 只有 {E2E_RUNNER} 一条 e2e 腿，新增的这条同样一次都不会执行——"
            "确认过再更新本枚举。"
        )

    def test_every_win32_skip_says_it_never_runs_in_ci(self):
        for name in WIN32_SKIPS:
            reasons = self._skips(E2E / name)
            assert reasons, f"{name} 里读不出 test.skip(win32) 的理由字符串"
            for reason in reasons:
                assert NEVER_RUNS_MARKER in reason, (
                    f"{name} 的 skip 理由「{reason}」没写清它在 CI 里不会执行；"
                    f"理由里必须出现「{NEVER_RUNS_MARKER}」（issue #30）"
                )
