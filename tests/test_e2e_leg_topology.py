"""Playwright e2e 在 CI 里**跑在哪几条腿上**——以及谁在依赖这个事实。

「收得到 ≠ 跑得过」：`web/e2e/` 收着一整套 spec，但一条按平台跳过的用例，
只有在**存在一条会执行它的腿**时才真的被执行过。腿的拓扑与 `test.skip` 的
平台判据是一对，配不上就是恒真——而恒真的 skip 不会有任何门禁说话
（`.github/AGENTS.md`：空转的门禁比没有门禁更坏）。

这个前提曾经被**写反**：`web/e2e/error-recovery-en.spec.ts` 末尾的注释说
「e2e workflow 目前只有 Ubuntu 腿」，并据此决定不写 Windows 文件占用
（`file_locked`）的界面用例——而当时唯一存在的那条腿恰恰是 Windows。前提反了，
从它推出来的那个「不写」的决定也就跟着错了；同一份文件里两条
`test.skip(win32)` 则相反，它们在唯一的那条腿上恒跳过，从来没有进过断言
（issue #30）。

所以把这件事**做进结构**，别再留在散落的注释里：

* 腿逐条枚举（job → runner），拓扑一变这里当场红；
* **每条按平台跳过的用例都必须点得出一条会执行它的腿**——这是本模块真正的
  不变式，它不随拓扑改变而失效；
* skip 的理由里必须写出那条腿的 job 名，读代码的人当场知道去哪儿看结论。

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

#: CI 里执行 `pnpm e2e` 的每一条腿：job id → runner。
#: 加腿 / 删腿 / 换 runner 都必须回到这里，顺便重估下面每一条 skip。
E2E_LEGS = {
    "windows-exe-smoke": "windows-latest",
    "posix-e2e": "ubuntu-latest",
}

#: runner → 它是不是 win32。判据只关心这一个维度（`process.platform`）。
_IS_WINDOWS = {"windows-latest": True, "ubuntu-latest": False, "macos-latest": False}

#: 带 `test.skip(process.platform …)` 的 spec 与条数。这是**枚举**不是白名单：
#: 新增一条按平台跳过的用例就必须回到这里，顺便被问一句「哪条腿会执行它」。
PLATFORM_SKIPS = {"error-recovery-en.spec.ts": 3}


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
    def test_every_invocation_belongs_to_a_declared_leg(self):
        """多一处 / 少一处都说明拓扑变了——依赖它的每处 skip 都要重估。"""
        hits = _e2e_invocations()
        assert len(hits) == len(E2E_LEGS), (
            f"`pnpm e2e` 的调用点有 {len(hits)} 处，声明的腿有 {len(E2E_LEGS)} 条：{hits}。"
            "腿的拓扑变了，请回去重估 web/e2e 里每一条按平台跳过的用例（issue #30），"
            "再更新 E2E_LEGS。"
        )
        assert {f for f, _ in hits} == {"ci.yml"}, f"`pnpm e2e` 挪出了 ci.yml：{hits}"

    def test_each_declared_leg_exists_and_runs_e2e(self):
        ci = _code((WF / "ci.yml").read_text(encoding="utf-8"))
        for job_id, runner in E2E_LEGS.items():
            block = _job(ci, job_id)
            assert re.search(rf"(?m)^\s+runs-on: {re.escape(runner)}\s*$", block), (
                f"job `{job_id}` 不再跑在 {runner} 上——"
                "web/e2e 里按平台跳过的用例要跟着重估（issue #30）"
            )
            assert re.search(r"(?m)^\s*pnpm e2e\b", block), (
                f"job `{job_id}` 里没有 `pnpm e2e` 了——本模块的前提整个变了"
            )

    def test_both_platform_classes_are_covered(self):
        """两侧各要有一条腿：只钉一侧的门禁，反方向越界时不会响。"""
        classes = {_IS_WINDOWS[r] for r in E2E_LEGS.values()}
        assert classes == {True, False}, (
            f"e2e 的腿只覆盖了 {classes}——另一侧平台上的用例会恒跳过（issue #30）"
        )


class TestPlatformSkipsHaveALegThatRunsThem:
    """按平台跳过的用例，必须点得出一条会执行它的腿。"""

    #: `test.skip(process.platform <op> 'win32', '<理由>')`
    PAT = r"test\.skip\(\s*process\.platform (===|!==) 'win32'\s*,\s*'([^']*)'"

    @classmethod
    def _skips(cls, path: Path) -> list[tuple[str, str]]:
        return re.findall(cls.PAT, path.read_text(encoding="utf-8"))

    @staticmethod
    def _covering_legs(op: str) -> list[str]:
        """`=== 'win32'` 跳过 Windows → 要非 Windows 的腿；`!==` 反之。"""
        want_windows = op == "!=="
        return [j for j, r in E2E_LEGS.items() if _IS_WINDOWS[r] is want_windows]

    def test_the_enumeration_still_matches_the_specs(self):
        """枚举与现实漂开 = 判据看不见新增的那一条。"""
        found = {
            p.name: len(self._skips(p)) for p in sorted(E2E.glob("*.spec.ts")) if self._skips(p)
        }
        assert found == PLATFORM_SKIPS, (
            f"web/e2e 里按平台跳过的用例分布变了：{found} != {PLATFORM_SKIPS}。"
            "确认过「哪条腿会执行它」再更新本枚举。"
        )

    def test_each_skip_has_a_leg_that_runs_it(self):
        for name in PLATFORM_SKIPS:
            skips = self._skips(E2E / name)
            assert skips, f"{name} 里读不出 test.skip(process.platform …) —— 形状变了？"
            for op, reason in skips:
                legs = self._covering_legs(op)
                assert legs, (
                    f"{name} 的 `test.skip(platform {op} 'win32')` 没有任何一条腿会执行它，"
                    f"它在 CI 里恒跳过（issue #30）。现有的腿：{E2E_LEGS}"
                )
                assert any(leg in reason for leg in legs), (
                    f"{name} 的 skip 理由「{reason}」没点出哪条腿会执行它；"
                    f"理由里必须出现 {legs} 之一（issue #30）"
                )
