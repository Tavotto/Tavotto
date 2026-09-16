"""发布编排的结构性契约。

这些判据都来自 2026-08-22 那次审计里**真实发生过**的失败
（`docs/audit/2026-08-22-v1-release-process-audit.md`）：

* v0.9.0 与 v0.9.1 两个正式 tag 都在发布链上第一次被执行时炸掉，
  而 tag ruleset 是 immutable——它们至今改不动也删不掉；
* 桌面链构建完轮询 190 分钟等另一条 workflow 建 Release；
* SBOM 那步把 `dist/*.whl` 喂给了只认单个路径的 syft；
* 发行资格验证在两个文件里各有一份手抄，修一个 bug 要改两处。

**不用 PyYAML。** 它不在 `.venv` 里，也不在 `[dev]` / `[ci]` 任何一个 extras
里（Flask 那侧的依赖边界一个字都不能松）。第一版写成
`pytest.importorskip("yaml")`，结果**整个模块在本地与 CI 上一起静默跳过**
——那正是这套 CI 一直在消灭的空门禁，而且这次是我自己造的。

替代方案是下面这个**只认本仓库这几个 workflow 的缩进形状**的小解析器。
它的精度写在明处（见 `_Workflow` 的 docstring），并且**解析不出预期形状时
当场抛**——一个安静地什么都没找到的判据比没有判据更坏。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"
RELEASE = WF / "release.yml"
#: 发布链的第二段（F.2，2026-09-16）：只有 workflow_dispatch，由 ci-infra 在 lab 绿后派发；
#: trust2 重验 → validate_artifacts → 发布 job。发布相关判据的主语从 RELEASE 扩到它。
PUBLISH = WF / "release-publish.yml"
DESKTOP = WF / "desktop-tauri.yml"
LAB = WF / "lab-ci.yml"
REUSABLE = WF / "_lab-qualification.yml"
PLUGIN_STABLE = WF / "plugin-stable.yml"

#: 本模块直接读 / 直接跑的仓库级文件——少一个，靠它的那几条判据就没有主语。
_NEEDED = {
    WF: (
        "release.yml",
        "release-publish.yml",
        "desktop-tauri.yml",
        "lab-ci.yml",
        "_lab-qualification.yml",
        "plugin-stable.yml",
    ),
    SCRIPTS: ("check_pending_release_notes.py",),
}

# 本模块的输入是**仓库级**的 `.github/workflows` 与 `scripts`，而 sdist 只带
# `tests` / `src/tavotto` / `web/src`（`[tool.hatch.build.targets.sdist].include`）。
# 从 sdist 解出来跑时这两个目录根本不存在——不接住的话，读 workflow 的那些用例
# 崩在 `FileNotFoundError` 上、跑 `scripts/` 的那两条崩在「子进程找不到脚本」上，
# 报的都不是真正的成因（issue #269）。
#
# 「读不到」有两种成因，它们把人送去的方向相反，所以必须分开报：
#   * 整个目录不在 → **这个环境里没有这些输入**（sdist 布局），如实跳过并点名
#     缺的是哪些目录，别去找一个不存在的重命名；
#   * 目录在、单个文件不在 → **路径真的变了**（重命名 / 挪走），当场抛并点名
#     是哪个文件——这种情况不该被跳过糊过去。
#
# 守卫的前提由 `test_the_skip_premise_still_holds` 钉住：哪天 sdist 带上了
# `.github` 或 `scripts`，这个 skip 就是多余的，而**一个多余的 skip 会在本该
# 跑得动的环境里安静地关掉整组判据**。
_MISSING = [str(d.relative_to(ROOT)) for d in _NEEDED if not d.is_dir()]
if _MISSING:
    pytest.skip(
        f"当前环境里没有 {_MISSING}——本模块的判据是仓库级发布编排契约，"
        "只在**源码检出**里有意义（sdist 只带 tests / src/tavotto / web/src）。"
        "这不是「路径变了」，别去找重命名。",
        allow_module_level=True,
    )

_RENAMED = [
    str((d / name).relative_to(ROOT))
    for d, names in _NEEDED.items()
    for name in names
    if not (d / name).is_file()
]
assert not _RENAMED, (
    f"目录都在，但读不到 {_RENAMED}——这是**路径变了**（被重命名或挪走），"
    "去找那个新名字。「这个环境里根本没有这些目录」是另一回事，"
    "由上面的 skip 守卫接住。"
)


def _sdist_include() -> list[str]:
    """读 pyproject 的 sdist include 列表；**只认列表项，不认注释里的散文**。"""
    body = re.search(
        r"(?ms)^\[tool\.hatch\.build\.targets\.sdist\]\n(.*?)^\[",
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert body, "pyproject 里切不出 [tool.hatch.build.targets.sdist] 段"
    entries = re.findall(r'(?m)^\s*"([^"]+)",\s*$', body.group(1))
    assert entries, "sdist 段里一个 include 条目都读不出来——列表的写法变了？"
    return entries


def test_the_skip_premise_still_holds():
    """守卫的前提：sdist 确实带 `tests`、确实不带 `.github` / `scripts`。

    前提一变，这里当场红——那时模块顶上的 skip 就多余了，而多余的 skip 会在
    **本该跑得动**的环境里安静地关掉整组判据。与
    `tests/test_e2e_leg_topology.py` 的同名判据同一形状（issue #269）。
    """
    include = _sdist_include()
    assert "tests" in include, "sdist 不再带 tests——本模块根本不会被解出来，这个守卫也就没有主语了"
    for shipped in (".github", "scripts"):
        assert not any(e == shipped or e.startswith(shipped + "/") for e in include), (
            f"sdist 现在带上了 {shipped}——模块顶上的 skip 守卫已经多余。"
            "留着它等于在一个本该能跑的环境里安静地关掉整组判据"
        )


def _strip_comments(text: str) -> str:
    """去掉整行注释与行尾注释；引号内的 `#` 不是注释。

    判据只该看**会被执行的那部分**。被自己的说明文字满足，是本仓库明令
    禁止的形状（CLAUDE.md「判据的主语」一节）。
    """
    out = []
    for line in text.splitlines():
        buf, quote = [], None
        for ch in line:
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                buf.append(ch)
            elif ch == "#":
                break
            else:
                buf.append(ch)
        out.append("".join(buf).rstrip())
    return "\n".join(out)


class _Workflow:
    """本仓库 workflow 的最小结构读取器。

    **精度写在明处**，别把它当成 YAML 解析器：

    * job = `jobs:` 之下缩进 2 空格的键；
    * step = job 里以 `      - ` 开头的块（本仓库这几个文件缩进一致）；
    * `run:` 取块标量的全文，`with:`/`if:`/`uses:`/`name:` 取同块内的标量。

    它逮得住「某处写了 glob」「某步在 always() 里引用了 venv」这类**块内**
    的事实，逮不住跨文件的语义（比如 `uses:` 指的那个文件里到底有什么）。
    构造时会自检形状，找不到预期数量的 job/step 就**抛**——这条很重要：
    缩进一变，一个安静地什么都没找到的判据会把所有用例变成假绿。
    """

    def __init__(self, path: Path):
        self.path = path
        self.raw = path.read_text(encoding="utf-8")
        self.text = _strip_comments(self.raw)
        self.jobs = self._split_jobs()
        if not self.jobs:
            raise AssertionError(f"{path.name}: 一个 job 都没解析出来——缩进形状变了？")

    def _split_jobs(self) -> dict[str, str]:
        m = re.search(r"^jobs:\s*$", self.text, re.M)
        if not m:
            return {}
        body = self.text[m.end() :]
        # 下一个顶层键（顶格）为止
        nxt = re.search(r"^\S", body, re.M)
        if nxt:
            body = body[: nxt.start()]
        jobs: dict[str, str] = {}
        parts = re.split(r"^  ([A-Za-z_][\w-]*):\s*$", body, flags=re.M)
        for i in range(1, len(parts), 2):
            jobs[parts[i]] = parts[i + 1]
        return jobs

    def steps(self, job: str) -> list[str]:
        body = self.jobs[job]
        m = re.search(r"^    steps:\s*$", body, re.M)
        if not m:
            return []
        seg = body[m.end() :]
        blocks = re.split(r"\n(?=      - )", seg)
        return [b for b in blocks if b.strip()]

    def all_steps(self) -> list[tuple[str, str]]:
        return [(j, s) for j in self.jobs for s in self.steps(j)]

    def runs(self) -> list[str]:
        """所有 `run:` 的正文（注释已剥）。

        块标量按**缩进**取到底：`run: |` 之后所有比 `run:` 这个键更深的行。
        第一版用一条带 lookahead 的正则去截，结果几乎每个块都截成了空串
        ——而上面那条解析器自检当场把它逮住了。这就是自检存在的理由：
        一个安静地什么都没找到的判据，会让整个模块变成假绿。
        """
        return [r for job in self.jobs for r in self.job_runs(job)]

    def job_runs(self, job: str) -> list[str]:
        """**一个 job** 的全部 `run:` 正文——「哪个 job 在派发」这种判据要按 job 问，
        整文件的 `runs()` 会把别的 job 的命令也算进来。"""
        out = []
        for step in self.steps(job):
            for m in re.finditer(r"^([ \t]*)run:[ \t]*(\||>|>-|\|-)?[ \t]*(\S.*)?$", step, re.M):
                indent = len(m.group(1))
                if m.group(3) and not m.group(2):
                    out.append(m.group(3))  # 单行 `run: cmd`
                    continue
                body = []
                for line in step[m.end() :].splitlines():
                    if not line.strip():
                        body.append("")
                        continue
                    if len(line) - len(line.lstrip()) <= indent:
                        break
                    body.append(line)
                out.append("\n".join(body))
        return out

    def run_text(self) -> str:
        return "\n".join(self.runs())

    @staticmethod
    def field(step: str, key: str) -> str | None:
        """取步骤里某个标量键。

        **步骤块的第一行是 ``      - name: …``**，键前面还有一个 ``- ``。
        第一版的正则少了那一段，于是它对**每个步骤的 name 都返回 None**
        —— 而调用方多半写着 ``field(step, "name") or "?"``，
        于是这个失效一直没有症状。判据静默失效的又一种形状。
        """
        m = re.search(rf"^\s+(?:-\s+)?{re.escape(key)}:[ \t]*(\S.*?)\s*$", step, re.M)
        return m.group(1) if m else None

    @staticmethod
    def with_scalars(step: str) -> dict[str, str]:
        """步骤 `with:` 下的单行标量。多行块（如 `files: |`）不在此列。"""
        m = re.search(r"^(\s+)with:\s*$", step, re.M)
        if not m:
            # 行内映射 `with: { name: dist, path: dist }`
            inline = re.search(r"^\s+with:\s*\{(.*)\}\s*$", step, re.M)
            if not inline:
                return {}
            return dict(re.findall(r"([\w-]+)\s*:\s*([^,}]+)", inline.group(1)))
        indent = len(m.group(1))
        out = {}
        for line in step[m.end() :].splitlines():
            if not line.strip():
                continue
            cur = len(line) - len(line.lstrip())
            if cur <= indent:
                break
            kv = re.match(r"\s*([\w-]+):[ \t]+(\S.*?)\s*$", line)
            if kv:
                out[kv.group(1)] = kv.group(2)
        return out


def _wf(p: Path) -> _Workflow:
    return _Workflow(p)


def test_the_parser_itself_still_sees_what_it_should():
    """**解析器自检。**

    这条排在最前面，因为它决定了下面所有用例是不是在真的判断什么。
    缩进一变，一个什么都没找到的解析器会让整个模块变成假绿——
    这正是本仓库反复强调的空门禁形状。
    """
    rel = _wf(RELEASE)
    assert set(rel.jobs) == {"trust", "build", "desktop", "dispatch_lab"}, (
        f"release.yml 解析出的 job：{sorted(rel.jobs)}——第一段到 dispatch_lab 为止（F.2）"
    )
    assert "artifact_manifest.py build" in rel.run_text()

    pub = _wf(PUBLISH)
    assert set(pub.jobs) == {
        "trust2",
        "validate_artifacts",
        "github_release",
        "n1_update_windows",
        "pypi",
        "plugin_stable",
    }, f"release-publish.yml 解析出的 job：{sorted(pub.jobs)}"
    assert len(pub.steps("validate_artifacts")) >= 10
    assert len(pub.steps("trust2")) >= 5, (
        "trust2 应是 checkout + 形状 + blocker + resolve + lab status + 摘要"
    )
    assert "artifact_manifest.py" in pub.run_text()

    reusable = _wf(REUSABLE)
    assert len(reusable.steps("qualify")) >= 15, "可复用资格验证的步骤太少了"
    assert "lab_preflight.py" in reusable.run_text()

    desk = _wf(DESKTOP)
    assert len(desk.jobs) >= 4


# ── 单一编排：没有跨 workflow 轮询 ────────────────────────────────────────


def test_no_workflow_polls_for_a_github_release():
    """**没有任何 workflow 等另一个 workflow 建 Release。**

    从前 desktop-tauri.yml 构建完就 `for i in $(seq 1 380); … sleep 30`
    等 release.yml（最长 190 分钟）。那不是「等太久」的问题：
    lab gate 的**排队**时间本身没有上界，#62 的注释自己承认
    「没有任何固定上限是够的」。
    """
    offenders = []
    for p in sorted(WF.glob("*.yml")):
        text = _wf(p).run_text()
        for m in re.finditer(r"gh release (view|list)", text):
            offenders.append(f"{p.name}: …{text[max(0, m.start() - 50) : m.end() + 30]}…")
    assert not offenders, (
        "这些地方在查 Release 是否存在——发布链里不该有任何一步等另一条链：\n  "
        + "\n  ".join(offenders)
    )


def test_no_sleep_polling_loops_anywhere_in_the_release_chain():
    """两段链 + 桌面链 + lab 两份，一个 `sleep N` / `seq 1 NN` 都不许有。第二段读 lab 结论
    只许一次 API 调用（`test_trust2_reads_the_lab_status_exactly_once_and_never_waits`）。"""
    for p in (RELEASE, PUBLISH, DESKTOP, LAB, REUSABLE):
        text = _wf(p).run_text()
        assert not re.search(r"seq\s+1\s+\d{2,}", text), f"{p.name}: 还有轮询循环"
        assert not re.search(r"\bsleep\s+\d+\b", text), f"{p.name}: 还有 sleep 轮询"


def test_the_tag_has_exactly_one_entry_point():
    """`v*` tag 只触发 release.yml 一条链。

    两条链各自被同一个 tag 触发、各自 checkout，就等于**「同一个 tag」
    被当成了「同一个 commit」的证明**——而 tag 是可移动的引用。
    """
    entries = []
    for p in sorted(WF.glob("*.yml")):
        text = _strip_comments(p.read_text(encoding="utf-8"))
        head = text.split("\njobs:")[0]
        if re.search(r"^\s*push:\s*\n\s*tags:\s*\[?\s*[\"']?v\*", head, re.M):
            entries.append(p.name)
    assert entries == ["release.yml"], f"tag 的入口应当只有 release.yml，实际 {entries}"
    # 第二段不是入口：没有 push、没有 schedule、没有 workflow_call——只能被派发
    head = _strip_comments(PUBLISH.read_text(encoding="utf-8")).split("\njobs:")[0]
    on = re.search(r"(?ms)^on:\s*\n(.*?)(?=^\S)", head)
    assert on, "release-publish.yml 切不出 on: 块"
    events = re.findall(r"(?m)^  ([a-z_]+):", on.group(1))
    assert events == ["workflow_dispatch"], (
        f"release-publish.yml 的事件是 {events}——第二段只许被派发，多一个入口就多一份漂开的信任判断"
    )


def test_desktop_is_reachable_only_through_release():
    head = _strip_comments(DESKTOP.read_text(encoding="utf-8")).split("\njobs:")[0]
    assert re.search(r"^\s*workflow_call:", head, re.M), "桌面链必须可被 workflow_call 复用"
    assert not re.search(r"^\s*push:", head, re.M), "桌面链不该再由 tag 自己触发"
    rel = _wf(RELEASE)
    assert "desktop-tauri.yml" in rel.jobs["desktop"]


def test_desktop_never_writes_to_a_release_itself():
    """挂 Release 只有一条路：release.yml 的 github_release。

    从前 wheel/SBOM 由 release.yml 挂、桌面产物由桌面链自己挂、
    latest.json 由第三个 job 挂——三方各写一次同一个 Release，还要互相等。
    """
    desk = _wf(DESKTOP)
    for jname, step in desk.all_steps():
        assert "action-gh-release" not in step, f"desktop-tauri.yml::{jname} 又在自己挂 Release"
    for jname, body in desk.jobs.items():
        assert not re.search(r"^\s+contents:\s*write", body, re.M), (
            f"desktop-tauri.yml::{jname} 要了 contents:write——它不该写任何东西"
        )


# ── publish=false：正式 tag 不再承担首测 ─────────────────────────────────


def test_release_supports_a_publish_false_dry_run():
    head = _strip_comments(RELEASE.read_text(encoding="utf-8")).split("\njobs:")[0]
    assert re.search(r"^\s+ref:\s*$", head, re.M), "演练必须能指定 exact SHA"
    m = re.search(r"^\s+publish:\s*\n(.*?)(?=^\s{6}\w|\Z)", head, re.M | re.S)
    assert m, "workflow_dispatch 没有 publish 输入"
    block = m.group(1)
    assert "type: boolean" in block
    assert re.search(r"default:\s*false", block), (
        "**publish 必须默认 false。** 默认发布的话，「跑一次看看」就会变成"
        "一次真实发布——而 PyPI 上同名文件永远不能重传"
    )


#: 「发布」在 workflow 里长这三样：挂 Release 的 action、PyPI 的 OIDC 发布 action、
#: 真推发行分支的发布器。第一段一样都不许有——它到派发 lab 就结束（F.2）。
_PUBLISHING_MARKERS = ("action-gh-release", "gh-action-pypi-publish", "plugin_publish.py")


def test_every_publishing_job_is_gated_on_publish():
    """发布 job 全在第二段，且**都**挂在 `trust2` 重算出的 publish 上。

    漏掉任何一个，演练就会真的发布出去。主语（F.2 起）是 `release-publish.yml`：
    `publish` 由 `trust2` 按同一规则重算，不是载荷里那个字符串——所以这里钉的是
    `needs.trust2.outputs.publish`，任何 job 都不许直接读 `inputs.publish` 当门。
    """
    pub = _wf(PUBLISH)
    for name in ("github_release", "pypi", "n1_update_windows"):
        body = pub.jobs[name].split("steps:")[0]
        assert "needs.trust2.outputs.publish == 'true'" in body, (
            f"{name} 没有挂在 trust2 的 publish 上"
        )
    stable = pub.jobs["plugin_stable"].split("steps:")[0]
    assert "needs.trust2.outputs.publish != 'true'" in stable, (
        "plugin_stable 的演练 / 真推分岔不再看 trust2 的 publish"
    )
    for name, body in pub.jobs.items():
        if name == "trust2":
            continue
        assert "inputs.publish" not in body, (
            f"{name} 直接读了 inputs.publish——载荷里的值只能进 trust2 重算"
        )


def test_the_first_segment_has_no_publishing_job():
    """**第一段到 dispatch_lab 为止。** 发布 job 一个都不在 release.yml 里，
    「lab 没绿就不发」靠的是第二段从未开始，而不是某个 `if:`。

    正面形式：job 集合 == 四个；三种发布标记都不出现；没有任何 job 要
    `contents: write`（第一段只造产物、只派发）。
    """
    rel = _wf(RELEASE)
    assert set(rel.jobs) == {"trust", "build", "desktop", "dispatch_lab"}, sorted(rel.jobs)
    for marker in _PUBLISHING_MARKERS:
        assert marker not in rel.text, (
            f"release.yml 里出现了 {marker}——发布 job 应全在 release-publish.yml"
        )
    for name, body in rel.jobs.items():
        assert not re.search(r"^\s+contents:\s*write", body, re.M), (
            f"release.yml::{name} 要了 contents: write"
        )
    # 发布标记在第二段真的都在（否则上面那条是在一个空集合上恒真）
    pub = _wf(PUBLISH)
    for marker in _PUBLISHING_MARKERS:
        assert marker in pub.text, f"release-publish.yml 里没有 {marker}——发布 job 搬丢了"


def test_the_dry_run_still_exercises_every_verification_step():
    """演练必须真的跑完 SBOM / checksum / provenance / 清单校验。

    只在「建 Release」那个 job 里做这些，等于**它们只在真发布时才执行**
    ——而那个 job 自 v0.8.0 起一次都没成功跑到过，#63 因此躺了好几周。
    """
    pub = _wf(PUBLISH)
    head = pub.jobs["validate_artifacts"].split("steps:")[0]
    assert not re.search(r"^\s+if:", head, re.M), "产物校验不许被 publish 门控——演练正是要跑它"
    blob = "\n".join(pub.steps("validate_artifacts"))
    for needle in ("sbom-action", "SHA-256", "attest-build-provenance", "合并并校验产物清单"):
        assert needle in blob, f"演练里少了：{needle}"


def test_release_only_uses_the_sha_that_trust_resolved():
    """所有 job 只认 trust / trust2 输出的 SHA，不各自再解析一次 ref。

    第二段多一条：除 `trust2` 外没有任何 job 读 `inputs.sha`（载荷是输入不是结论），
    每个 checkout 的 `ref:` 都是 `needs.trust2.outputs.sha`。
    """
    rel = _wf(RELEASE)
    for name, body in rel.jobs.items():
        if name == "trust":
            continue
        assert "github.ref_name" not in body, (
            f"{name} 还在用 github.ref_name——发布链只认 trust 验过的 SHA"
        )
    pub = _wf(PUBLISH)
    checkouts = 0
    for name, body in pub.jobs.items():
        if name == "trust2":
            continue
        for needle in ("github.ref_name", "inputs.sha", "github.sha"):
            assert needle not in body, (
                f"release-publish.yml::{name} 用了 {needle}——第二段只认 trust2 输出的 SHA"
            )
        for step in pub.steps(name):
            if "actions/checkout@" not in step:
                continue
            checkouts += 1
            assert _Workflow.with_scalars(step).get("ref") == "${{ needs.trust2.outputs.sha }}", (
                f"release-publish.yml::{name} 的 checkout 不是 trust2 的 SHA"
            )
    assert checkouts >= 4, f"第二段只找到 {checkouts} 个 checkout——选择器缩水了"


# ── 产物清单：下游不再猜文件名 ────────────────────────────────────────────


def test_single_value_action_inputs_come_from_the_manifest():
    """只收**一个路径**的 action 输入，必须来自清单解出来的具体路径。

    #63：`anchore/sbom-action` 的 `file:` 写成 `dist/*.whl`，
    syft 把那串字符原样当文件名，报
    `no source providers were able to resolve the input`。
    """
    SINGLE = {"file", "image", "artifact-name", "output-file"}
    offenders = []
    for p in sorted(WF.glob("*.yml")):
        wf = _wf(p)
        for jname, step in wf.all_steps():
            for k, v in wf.with_scalars(step).items():
                if k not in SINGLE:
                    continue
                # 剥掉表达式之后再看：`${{ … }}/dist/*.whl` 里 GitHub 只替换
                # 表达式、**不做 shell 展开**，剩下的 `*` 会原样交给 syft。
                bare = re.sub(r"\$\{\{[^}]*\}\}", "", str(v))
                if any(c in bare for c in "*?"):
                    offenders.append(f"{p.name}::{jname} {k}: {v}")
    assert not offenders, "这些单值输入拿到了通配符：\n  " + "\n  ".join(offenders)


def test_an_action_output_directory_exists_before_the_action_writes_to_it():
    """写文件的 action 之前，那个目录必须已经建好。

    2026-08-22 实测（run 32578844828，`github_release` 这个 job **有史以来
    第一次真正执行**）：#63 的修复生效了，syft 拿到具体路径并成功扫完
    ——然后 sbom-action 写输出时报

        ENOENT: no such file or directory, open 'out/tavotto-sbom.spdx.json'

    因为 `mkdir -p out` 排在**下一步**（SHA-256 那步）。整条链就是这么
    一步一步依次失败的：每一步都是第一次执行。

    **判据只盯 `output-file` 这一类「action 自己写文件」的输入**——
    `run:` 里的重定向由 shell 负责，那是另一回事，混在一起判会把大量
    正当写法判红。
    """
    offenders = []
    for p_ in sorted(WF.glob("*.yml")):
        wf = _wf(p_)
        for job in wf.jobs:
            steps = wf.steps(job)
            for idx, step in enumerate(steps):
                out = wf.with_scalars(step).get("output-file")
                if not out or "/" not in out:
                    continue
                d = out.rsplit("/", 1)[0]
                # 这一步之前（同 job 内）有没有把这个目录建出来？
                made = any(
                    f"mkdir -p {d}" in prior or f"mkdir -p ./{d}" in prior for prior in steps[:idx]
                )
                if not made:
                    name = _Workflow.field(step, "name") or "?"
                    offenders.append(
                        f"{p_.name}::{job} 步骤「{name}」写 {out}，"
                        f"而前面没有任何一步 `mkdir -p {d}`"
                    )
    assert not offenders, (
        "这些 action 要往一个还不存在的目录里写文件（实测报 ENOENT）：\n  " + "\n  ".join(offenders)
    )


def test_every_build_leg_emits_a_manifest():
    """两条构建链（Python / 桌面）都要产出自己那份清单。

    少一条，合并那步就少一个平台，而 `--require` 会在那时才报出来——
    可那时整条构建已经跑完了。
    """
    assert "artifact_manifest.py build" in _wf(RELEASE).run_text(), (
        "release.yml 的 Python 腿没造清单"
    )
    assert "artifact_manifest.py build" in _wf(DESKTOP).run_text(), "桌面腿没造清单"


def test_the_merged_manifest_is_verified_against_the_trusted_sha():
    """判据要落在**合并那一步自己**，不是「文件里某处提过 --source-sha」。

    第一版问的是后者，于是把合并步骤里的 `--source-sha` 删掉照样绿
    ——build 那步和 github_release 那步也各有一个，全文搜索被它们满足了。
    判据的主语又错了一次：该问「合并完那一步核不核对」。
    """
    pub = _wf(PUBLISH)
    steps = [s for s in pub.steps("validate_artifacts") if "合并并校验产物清单" in s]
    assert len(steps) == 1, "找不到「合并并校验产物清单」这一步"
    step = steps[0]
    assert "artifact_manifest.py merge" in step
    assert "artifact_manifest.py verify" in step
    assert "--source-sha" in step, (
        "合并之后必须核对 source_sha——**「同一个 tag」证明不了「同一个 commit」**，"
        "这是唯一能挡住两条构建腿来自不同 commit 的地方"
    )
    assert "--require wheel,sdist,windows-installer,macos-installer" in step, (
        "四个必须的角色少一个，就意味着那个平台的产物没造出来却照发"
    )


def test_the_release_attaches_everything_in_one_go():
    pub = _wf(PUBLISH)
    attach = [s for s in pub.steps("github_release") if "action-gh-release" in s]
    assert len(attach) == 1, "挂 Release 只该有一步"
    for needle in ("assets/dist/*", "SHA256SUMS.txt", "tavotto-sbom.spdx.json", "latest.json"):
        assert needle in attach[0], f"一次性挂载里少了 {needle}"
    # Codex 插件（zip + codex-plugin.json + 随包清单）在 dist/ 里随 `assets/dist/*` 挂上
    # （ADR 0043：由 build job 造、validate 成对验过），所以它们必须是 validate 的必需 role
    validate = "\n".join(pub.steps("validate_artifacts"))
    for role in ("codex-plugin", "codex-plugin-manifest", "codex-plugin-build"):
        assert role in validate, f"validate_artifacts 的 --require 里少了 {role}"


def test_the_release_carries_the_project_licence():
    """Release 资产里要有项目 `LICENSE`（AGPL 全文），与 SHA256SUMS 同路（#182）。

    v0.14.0 的 16 个 Release 资产里没有它。它**不进产物清单**：清单记的是构建腿
    造出来的东西，而 LICENSE 是 trust 验过的那个 SHA 上的源码文件——由
    validate_artifacts 从自己的 checkout 拷进 out/（演练也走，改名当场红），
    随 release-assets 搬到 github_release，再一次挂全部。判据钉两头：
    拷进 out/ 那一步在、挂载清单里有那一行；少任一头都是「登记了却没挂上」。
    """
    pub = _wf(PUBLISH)
    staged = [s for s in pub.steps("validate_artifacts") if "cp LICENSE out/LICENSE" in s]
    assert len(staged) == 1, "validate_artifacts 里没有把 LICENSE 拷进 out/ 的那一步"
    assert "GNU AFFERO" in staged[0], "拷之前不再核对它是 AGPL 全文——空文件或改错源也会照挂"
    attach = [s for s in pub.steps("github_release") if "action-gh-release" in s]
    assert len(attach) == 1, "挂 Release 只该有一步"
    assert re.search(r"^\s+assets/out/LICENSE\s*$", attach[0], re.M), (
        "github_release 的 files 清单里没有 assets/out/LICENSE——Release 页面上拿不到许可证全文"
    )


def test_the_published_artifacts_are_re_verified_before_attaching():
    """下载 artifact 再上传是一次真实的搬运，中间任何一环都可能改内容。"""
    pub = _wf(PUBLISH)
    blob = "\n".join(pub.steps("github_release"))
    assert "artifact_manifest.py verify" in blob, (
        "挂上去之前没有重新校验——「Release 上挂的与发行资格验证过的不是"
        "同一个东西」是这条链上最不能接受的失败"
    )


# ── 资格验证只有一份定义 ──────────────────────────────────────────────────


def test_a_repo_variable_cannot_weaken_the_release_gate():
    """**仓库级开关不许把发布门禁一起放倒。**

    `LAB_VISUAL_GATE=false` 的本意是让日常 lab run 在基线漂移期间不被
    视觉回归挡住。合并两份资格定义**之前**，release 那份的 Golden 步骤
    永远是阻断的；合并之后同一个仓库变量就顺手管到了发布链——而设它的人
    多半只是想让 nightly 别再刷红，根本不知道自己放行了一次带视觉回归的发版。

    判据是 `continue-on-error` 的表达式里**必须含 mode 判断**，不是
    「文件里提没提 release」——后者被同文件任何一处 release 满足。
    """
    t = REUSABLE.read_text(encoding="utf-8")
    m = re.search(r"id:\s*visual\b.*?continue-on-error:\s*(.+)", t, re.S)
    assert m, "读不出 Golden 视觉回归那步的 continue-on-error"
    expr = m.group(1).splitlines()[0]
    assert "inputs.mode" in expr and "release" in expr, (
        f"视觉门禁的 continue-on-error 没有按 mode 收窄：{expr!r}\n"
        "—— 仓库变量 LAB_VISUAL_GATE=false 会连发布门禁一起放倒"
    )


#: 派发到私有仓库 ci-infra 的那条命令的开头——**派发方**的判据主语（F 组，2026-09-16）。
#: 命令、目标仓库、workflow 文件名三段一起钉：任何一段变了都不再算「派发方」，
#: 下面的集合当场缩水变红，而不是安静地把一个改了名的 job 放过去。
_DISPATCH_CMD = "gh workflow run lab-qualification.yml -R Tavotto/ci-infra"
#: 公开仓库里直接 `uses` reusable 的写法——**调用方**的判据主语。
_REUSABLE_USES = "uses: ./.github/workflows/_lab-qualification.yml"
#: 被验的代码永远来自这个仓库——reusable 被 ci-infra 跨仓库调用时默认 repository 是调用方。
_PUBLIC_REPO = "Tavotto/Tavotto"
#: PR B（F-6，2026-09-16）之后公开仓库里**没有**直接 `uses` reusable 的 job：`release.yml`
#: 的 `lab_release_gate` 改成了派发 + 回调（`dispatch_lab` → ci-infra → `release-publish.yml`）。
#: 这是 F-8 注销公开仓库 runner 的前提：谁再把派发改回 `uses`，runner 不在这里，它会永远排队。
#: 两张表一起改，别只改一张。
_REUSABLE_CALLERS: set[tuple[str, str]] = set()
_DISPATCHERS = {("lab-ci.yml", "dispatch"), ("release.yml", "dispatch_lab")}


def _callers() -> dict[tuple[str, str], _Workflow]:
    """公开仓库里 `uses` reusable 的 (workflow, job)。`uses:` 是 job 级键，注释已剥。"""
    out: dict[tuple[str, str], _Workflow] = {}
    for p in sorted(WF.glob("*.yml")):
        if p.name == REUSABLE.name:
            continue
        wf = _wf(p)
        for job, body in wf.jobs.items():
            if re.search(rf"(?m)^\s+{re.escape(_REUSABLE_USES)}\s*$", body):
                out[(p.name, job)] = wf
    return out


def _dispatchers() -> dict[tuple[str, str], _Workflow]:
    """公开仓库里派发 ci-infra 的 (workflow, job)：**那个 job 的** `run:` 正文里有 `_DISPATCH_CMD`。"""
    out: dict[tuple[str, str], _Workflow] = {}
    for p in sorted(WF.glob("*.yml")):
        wf = _wf(p)
        for job in wf.jobs:
            if any(_DISPATCH_CMD in run for run in wf.job_runs(job)):
                out[(p.name, job)] = wf
    return out


_NEEDS_SHA = re.compile(r"\$\{\{\s*needs\.([\w-]+)\.outputs\.sha\s*\}\}")


def _sha_source_job(wf: _Workflow, job: str) -> str:
    """调用方 / 派发方的 SHA 来自哪个 job：

    * 调用方：`with:` 里 `sha: ${{ needs.X.outputs.sha }}`；
    * 派发方：命令里 `-f sha=<值>`，值要么直接是那个表达式，要么是 `"$VAR"`，而 VAR
      在**同一个 job** 的 `env:` 里等于那个表达式（多一层间接就抛——别让判据去猜）。
    """
    body = wf.jobs[job]
    direct = re.search(r"(?m)^\s*sha:\s*(\$\{\{.*?\}\})\s*$", body)
    if direct:
        m = _NEEDS_SHA.fullmatch(direct.group(1).strip())
        assert m, f"{wf.path.name}::{job} 的 sha 不是 needs.<job>.outputs.sha：{direct.group(1)!r}"
        return m.group(1)
    runs = "\n".join(wf.job_runs(job))
    arg = re.search(r"-f\s+sha=(\S+)", runs)
    assert arg, (
        f"{wf.path.name}::{job} 既没有 `sha:`，命令里也没有 `-f sha=`——它把什么交给了实验室？"
    )
    value = arg.group(1).strip("\"'")
    m = _NEEDS_SHA.fullmatch(value)
    if m:
        return m.group(1)
    var = re.fullmatch(r"\$\{?([A-Za-z_]\w*)\}?", value)
    assert var, f"{wf.path.name}::{job} 的 -f sha= 既不是 needs 表达式也不是一个环境变量：{value!r}"
    env = re.search(rf"(?m)^\s+{re.escape(var.group(1))}:\s*(\$\{{\{{.*?\}}\}})\s*$", body)
    assert env, f"{wf.path.name}::{job} 的 -f sha=${var.group(1)}，而 job 的 env 里没有这个变量"
    m = _NEEDS_SHA.fullmatch(env.group(1).strip())
    assert m, (
        f"{wf.path.name}::{job} 的 {var.group(1)} 不是 needs.<job>.outputs.sha：{env.group(1)!r}"
    )
    return m.group(1)


def test_every_caller_gates_the_sha_through_a_trust_job():
    """**可复用资格 workflow 的安全性由调用方兜底——那就必须有东西看着调用方。**

    `_lab-qualification.yml` 把 `inputs.sha` 直接 checkout 到常驻的
    self-hosted runner 上。单看这个文件，那个 sha 是任意的——CodeQL 的
    「cache poisoning via execution of untrusted code」正是这么读的，
    而它读得没错：**保证不在这个文件里**。

    保证在调用方：先跑一个 trust job，拒绝既不是 `origin/main` 祖先、又没有
    tag 指向的 commit。问题是从前没有任何东西要求**下一个** caller 也这么做
    ——加一个直接传 `inputs.ref` 的调用方，长期 runner 就开始执行未经 review
    的代码，而且没有一条用例会红。

    主语（F 组起）是**调用方 ∪ 派发方**：直接 `uses` reusable 的 job，和用
    `gh workflow run … -R Tavotto/ci-infra` 把 SHA 交给私有仓库的 job——后者
    同样决定了实验室 runner 会 checkout 哪个 commit。每一个的 sha 都必须来自
    某个 job 的输出，且那个 job 里真的有 ancestry 判断。ci-infra 自己那侧的
    trust-check 归 ci-infra 的合同测试（ADMIN_HANDOFF F.3）。
    """
    parties = {**_callers(), **_dispatchers()}
    assert parties, "既没有调用方也没有派发方——这条用例本身失效了"
    for (name, job), wf in sorted(parties.items()):
        src = _sha_source_job(wf, job)
        trust = wf.jobs.get(src)
        assert trust and "--is-ancestor" in trust, (
            f"{name}::{job} 的 sha 来自 {src}，但那个 job 里没有 ancestry 判断——"
            "常驻 runner 会执行一个没人验过的 commit"
        )


def test_the_release_gate_cannot_be_evicted_by_a_routine_lab_run():
    """**发布门禁与日常 lab run 不许共用一个并发槽。**

    GitHub 每个 group 只保留**一个运行中 + 一个待定**，第三个排进来会
    *取代*那个待定的——`cancel-in-progress: false` 只保护正在跑的，
    保护不了在排队的。两条链共用一个槽时：发布门禁正等在一次长 lab run
    后面，这时一次 push to main 或定时任务进来，**日常 run 把待定的发布
    门禁挤掉，发版当场中止**，而且看起来像「被取消了」，没有原因。

    槽名要区分两维（F 组起）：`github.workflow`——同仓库两条调用链各排各的；
    `inputs.mode`——ci-infra 那边**一个** workflow 服务四档，`github.workflow`
    对它们是同一个名字，不带档位 release 与 nightly 就共槽了。

    另一侧同样要守：`lab-ci.yml` 顶层**不许**再声明同名的固定组。
    workflow 级与它自己调用的 job 级申请同一个槽 = run 在等自己，
    表现是 8 秒失败、runner_name 为 null、零步骤、日志空白（#66 撞过）。

    机器独占不由这个槽负责：带 `tavotto-lab` 标签的 runner 只有一台，
    runner 端另有 flock。槽只负责同一条链、同一档位内部去重。
    """
    qual = _strip_comments(REUSABLE.read_text(encoding="utf-8"))
    groups = re.findall(r"^\s*group:\s*(.+)$", qual, re.M)
    assert len(groups) == 1, f"可复用资格定义里 concurrency group 应恰好一处，读到 {groups}"
    group = groups[0].strip()
    assert "github.workflow" in group, (
        f"槽名 {group!r} 不区分调用方——发布门禁会和日常 lab run 抢同一个槽，"
        "排队中的那个会被后来的挤掉"
    )
    assert "inputs.mode" in group, (
        f"槽名 {group!r} 不区分档位——ci-infra 一个 workflow 服务四档，release 会和 nightly 共槽"
    )

    # 调用方 / 派发方顶层不许再有固定的同名组（那会让 run 等自己）
    for path in (LAB, RELEASE, PUBLISH):
        text = path.read_text(encoding="utf-8")
        top = re.search(r"^concurrency:\s*\n(?:\s+#.*\n)*\s+group:\s*(.+)$", text, re.M)
        if top:
            assert "lab-qualification" not in top.group(1), (
                f"{path.name} 顶层又声明了 lab-qualification 组——"
                "workflow 级与它调用的 job 级同名，job 会等一个自己已经持有的槽"
            )


def test_qualification_is_defined_exactly_once():
    """资格验证的步骤只有 `_lab-qualification.yml` 一份；谁执行它按两张表点名。

    从前两边各有一份手抄的 shell。#61 修一个 bug 必须同时改两处，
    而两处的差别实测只有「`$LAB_MODE` vs 字面量 release」和一处换行
    ——它们本来就是同一段逻辑，只是被抄了两遍。

    F 组（2026-09-16）起实验室 runner 在私有仓库 ci-infra 上，公开仓库里：

    * **调用方集合**（直接 `uses` reusable）== `_REUSABLE_CALLERS`——PR B 之后是**空集**
      （并行期曾只剩 `release.yml::lab_release_gate`）；
    * **派发方集合**（`gh workflow run lab-qualification.yml -R Tavotto/ci-infra`）
      == `_DISPATCHERS`——`lab-ci.yml::dispatch` 与 `release.yml::dispatch_lab`。

    判的是**集合相等**，不是「有没有」：多一个 `uses`（有人把派发改回直接调用，
    runner 不在公开仓库上会永远排队）或少一个派发（lab 没人跑）都红。
    """
    assert REUSABLE.is_file()
    callers = _callers()
    dispatchers = _dispatchers()
    assert set(callers) == _REUSABLE_CALLERS, (
        f"公开仓库里 uses reusable 的 job 集合变了：{sorted(callers)}（期望 {sorted(_REUSABLE_CALLERS)}）"
    )
    assert set(dispatchers) == _DISPATCHERS, (
        f"派发 ci-infra 的 job 集合变了：{sorted(dispatchers)}（期望 {sorted(_DISPATCHERS)}）"
    )
    for (name, job), wf in callers.items():
        assert not re.search(r"^\s+steps:", wf.jobs[job], re.M), f"{name}::{job} 还带着自己的步骤"

    # 那段逻辑不许在别处再出现一次
    for p in (LAB, RELEASE, PUBLISH):
        text = _wf(p).run_text()
        assert "lab_preflight.py" not in text, f"{p.name}: 又抄了一份体检"
        assert "summarize.py" not in text, f"{p.name}: 又抄了一份汇总"


def _reusable_step(needle: str) -> str:
    steps = [s for s in _wf(REUSABLE).steps("qualify") if needle in s]
    assert len(steps) == 1, f"reusable 里含 {needle!r} 的步骤应恰好一步，实际 {len(steps)}"
    return steps[0]


def test_the_reusable_pins_the_public_repository_for_cross_repository_callers():
    """**被 ci-infra 跨仓库调用时，checkout 与 download-artifact 都要显式钉回 Tavotto/Tavotto。**

    reusable 的 job 在**调用方**上下文里跑：`actions/checkout` 与 `download-artifact`
    的 `repository` 默认都是 `github.repository`——ci-infra 调它时那是 ci-infra，
    checkout 会去拉一个没有产品代码的仓库、发行档会去 ci-infra 的 run 里找 `dist`。

    判据是三个 `with:` 字段的**字符串相等**（ADMIN_HANDOFF F.3），不是「含不含某个词」：

    * `run-id: ${{ inputs.source_run_id || github.run_id }}`——跨仓库时取公开仓库那次
      release run，同仓库时落回本 run；
    * `github-token: ${{ secrets.TAVOTTO_PUBLIC_TOKEN }}`——**刻意没有 `|| github.token`**。
      download-artifact@v4 只在 token **非空**时切到跨仓库的 REST 路径（源码
      `if (inputs.token)`），空串走今天那条同 run 内部路径；而本文件的 GITHUB_TOKEN 只有
      `contents: read`、没有 `actions: read`——兜到它头上，同仓库调用的发行档会在下一次
      发版时 403，且只在那时发作。
    """
    co = _reusable_step("actions/checkout@")
    w = _Workflow.with_scalars(co)
    assert w.get("repository") == _PUBLIC_REPO, f"checkout 没钉公开仓库：{w}"
    assert w.get("ref") == "${{ inputs.sha }}", f"checkout 的 ref 不再是 inputs.sha：{w}"

    dl = _reusable_step("actions/download-artifact@")
    w = _Workflow.with_scalars(dl)
    assert w.get("name") == "dist", f"发行档取的不是 dist：{w}"
    assert w.get("repository") == _PUBLIC_REPO, f"download-artifact 没钉公开仓库：{w}"
    assert w.get("run-id") == "${{ inputs.source_run_id || github.run_id }}", (
        f"run-id 的表达式变了：{w.get('run-id')!r}"
    )
    assert w.get("github-token") == "${{ secrets.TAVOTTO_PUBLIC_TOKEN }}", (
        f"github-token 的表达式变了：{w.get('github-token')!r}——"
        "非空 token 会把同仓库调用也切到需要 actions: read 的 REST 路径"
    )


def test_the_reusable_declares_the_cross_repository_input_and_secret_as_optional():
    """`source_run_id` 与 `TAVOTTO_PUBLIC_TOKEN` 都是 **required: false**：同仓库调用
    （release.yml 并行期）一个都不传，行为逐字不变；ci-infra 调用时经 `secrets:` 传入。
    actionlint 会把 reusable 里用到而没声明的 `secrets.X` 报红，这里钉的是**可选**这一位。"""
    head = _strip_comments(REUSABLE.read_text(encoding="utf-8")).split("\njobs:")[0]
    inp = re.search(r"(?ms)^      source_run_id:\s*\n(.*?)(?=^      \w|^    \w|\Z)", head)
    assert inp, "workflow_call 没有 source_run_id 输入"
    assert re.search(r"^\s+type:\s*string\s*$", inp.group(1), re.M), inp.group(1)
    assert re.search(r'^\s+default:\s*""\s*$', inp.group(1), re.M), inp.group(1)
    assert re.search(r"^\s+required:\s*false\s*$", inp.group(1), re.M), inp.group(1)

    sec = re.search(
        r"(?ms)^    secrets:\s*\n      TAVOTTO_PUBLIC_TOKEN:\s*\n(.*?)(?=^    \w|^\S|\Z)", head
    )
    assert sec, "workflow_call 没有声明 secrets.TAVOTTO_PUBLIC_TOKEN"
    assert re.search(r"^\s+required:\s*false\s*$", sec.group(1), re.M), (
        "TAVOTTO_PUBLIC_TOKEN 不是可选的——同仓库调用会因缺 secret 而失败"
    )


@pytest.mark.parametrize(
    ("path", "job", "trust_job"),
    [(LAB, "dispatch", "trust-check"), (RELEASE, "dispatch_lab", "trust")],
    ids=["lab-ci", "release"],
)
def test_the_lab_dispatch_uses_the_ci_infra_pat_and_reds_when_it_is_missing(path, job, trust_job):
    """两个派发方（`lab-ci.yml::dispatch` / `release.yml::dispatch_lab`）：托管机、PAT 来自
    `secrets.TAVOTTO_CI_INFRA_TOKEN`、secret 为空**必须红**，且没有任何让它不跑的条件。

    「secret 为空就跳过」是本仓库明令禁止的空门禁形状：读的人会以为 lab 在跑，而它
    根本没被派出去。判据写成正面形式——命令正文里在 `gh workflow run` **之前**必须有
    一段 `if [ -z "$GH_TOKEN" ]` → `::error::…TAVOTTO_CI_INFRA_TOKEN…` → `exit 1`。
    """
    wf = _wf(path)
    body = wf.jobs[job]
    header = body.split("steps:")[0]
    assert re.search(r"^\s+runs-on:\s*ubuntu-latest\s*$", header, re.M), "派发要在托管机上"
    needs = re.search(r"^\s+needs:\s*(.+?)\s*$", header, re.M)
    assert needs and trust_job in re.findall(r"[\w-]+", needs.group(1)), (
        f"{path.name}::{job} 的 needs 里没有 {trust_job}——派发必须排在信任判断之后"
    )
    assert not re.search(r"^\s+if:", header, re.M), "派发 job 不许带条件——lab 没人跑必须红在这里"
    assert "continue-on-error" not in body, "派发失败不许被 continue-on-error 吞掉"
    assert re.search(
        r"^\s+GH_TOKEN:\s*\$\{\{\s*secrets\.TAVOTTO_CI_INFRA_TOKEN\s*\}\}\s*$", header, re.M
    ), "GH_TOKEN 不是来自 secrets.TAVOTTO_CI_INFRA_TOKEN——派发用的是 PAT，不是 GITHUB_TOKEN"

    runs = "\n".join(wf.job_runs(job))
    guard = re.search(
        r'if \[ -z "\$\{?GH_TOKEN[^\]]*\]; then\n(?P<block>(?:.*\n)*?)\s*fi\b',
        runs,
    )
    assert guard, '派发命令之前没有 `if [ -z "$GH_TOKEN" ]` 这道守卫'
    block = guard.group("block")
    assert re.search(r"::error::.*TAVOTTO_CI_INFRA_TOKEN", block), "守卫没有点名是哪个 secret 为空"
    assert re.search(r"^\s*exit 1\s*$", block, re.M), "守卫没有 exit 1——只打印不退出等于跳过"
    assert guard.end() < runs.index(_DISPATCH_CMD), "守卫写在派发命令之后——那时匿名请求已经发出去了"

    cmd_line = runs[runs.index(_DISPATCH_CMD) :]
    assert re.search(r"\s-r\s+main\b", cmd_line), "派发没有钉 ci-infra 的 ref=main"
    for field in ("mode", "sha", "baseline_tag", "use_prebuilt_dist"):
        assert re.search(rf"-f\s+{field}=", cmd_line), f"派发命令少了 -f {field}="


def test_release_mode_verifies_the_exact_artifact():
    """发行档验的必须是 build 产出的**那一份** wheel；lab 档没有候选包，必须自己造。

    F.2 起发行档由 `release.yml::dispatch_lab` 派发：命令里 `-f mode=release`、
    `-f use_prebuilt_dist=true`、`-f source_run_id=` 是**本 run** 的 id（ci-infra 那边的
    reusable 按它跨仓库取 `dist`）——三样缺一，lab 验的就不是将要发出去的那一份。
    """
    rel = "\n".join(_wf(RELEASE).job_runs("dispatch_lab"))
    assert re.search(r"-f\s+mode=release\b", rel), "release.yml 没有按 release 档派发"
    assert re.search(r"-f\s+use_prebuilt_dist=true\b", rel), "发行档必须验 build 产出的那一份 dist"
    src = re.search(r"-f\s+source_run_id=(\S+)", rel)
    assert src, "派发命令少了 -f source_run_id="
    assert src.group(1).strip("\"'") in (
        "$GITHUB_RUN_ID",
        "${GITHUB_RUN_ID}",
        "${{ github.run_id }}",
    ), f"source_run_id 不是本 run 的 id：{src.group(1)!r}——ci-infra 会到别的 run 里找 dist"
    lab = "\n".join(_wf(LAB).job_runs("dispatch"))
    assert re.search(r"-f\s+use_prebuilt_dist=false\b", lab), (
        "lab 档没有候选包，必须让 ci-infra 自己造一个——那问的是另一个问题"
    )


# ── 发布链切两段（F.2，2026-09-16）────────────────────────────────────────
#
# 第一段 release.yml 到 dispatch_lab 为止；第二段 release-publish.yml 由 ci-infra 在 lab 绿后
# 派发，先 trust2 重验再发布。下面这组钉的是「切开之后哪些保证一条都没少」：载荷不被
# 信任、publish 只能被压成 false、产物只从第一段那次 run 取、lab 结论读一次不等。

#: 第二段的 workflow_dispatch inputs——与 ci-infra `report` 的派发命令共用的接口，**一个字不能偏**。
_PUBLISH_INPUTS = ("sha", "source_run_id", "lab_run_id", "publish", "ack_open_blockers")


def _dispatch_inputs(path: Path) -> dict[str, dict[str, str]]:
    """`on.workflow_dispatch.inputs` → {名字: {键: 值}}（注释已剥，单行标量）。"""
    head = _strip_comments(path.read_text(encoding="utf-8")).split("\njobs:")[0]
    m = re.search(r"(?ms)^    inputs:\s*\n(.*?)(?=^\S|^  [a-z_]+:)", head)
    assert m, f"{path.name}: 读不到 workflow_dispatch.inputs 块"
    out: dict[str, dict[str, str]] = {}
    parts = re.split(r"(?m)^      ([A-Za-z_]+):\s*$", m.group(1))
    for i in range(1, len(parts), 2):
        out[parts[i]] = dict(re.findall(r"(?m)^\s+([\w-]+):[ \t]+(\S.*?)\s*$", parts[i + 1]))
    assert out, f"{path.name}: inputs 块里一个输入都没切出来"
    return out


def _shell_block(shell: str, opener: str) -> str:
    """shell 里以 `opener` 那一行开头、到**同一缩进**的 `fi` 为止的块（不含首尾两行）。

    嵌套的 `if … fi` 用非贪婪正则切会停在内层的 `fi` 上——第一版就这么切错的，
    于是外层分支尾部的赋值被判成「不在分支里」。按缩进切没有这个问题。
    """
    lines = shell.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.strip() == opener]
    assert len(starts) == 1, f"开头行 {opener!r} 应恰好一处，实际 {len(starts)}"
    i = starts[0]
    indent = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i + 1, len(lines)):
        if lines[j].strip() == "fi" and len(lines[j]) - len(lines[j].lstrip()) == indent:
            return "\n".join(lines[i + 1 : j])
    raise AssertionError(f"{opener!r} 之后找不到同缩进的 fi")


def test_the_second_segment_is_dispatch_only_with_the_pinned_inputs():
    """`release-publish.yml`：名字 `Release (publish)`；五个 string 输入，名字与顺序钉死。

    ci-infra 的 `report` 按这五个名字 `-f name=` 派发过来——少一个、改一个名字，派发当场 422，
    而那发生在 lab 已经绿了之后，是整条链上最贵的失败位置。
    """
    raw = PUBLISH.read_text(encoding="utf-8")
    assert re.search(r"(?m)^name: Release \(publish\)\s*$", raw), "第二段的 name 变了"
    got = _dispatch_inputs(PUBLISH)
    assert tuple(got) == _PUBLISH_INPUTS, (
        f"第二段的输入是 {tuple(got)}，接口钉的是 {_PUBLISH_INPUTS}"
    )
    for name, fields in got.items():
        assert fields.get("type") == "string", (
            f"{name}.type = {fields.get('type')!r}——载荷经两跳 -f 传，全是字符串"
        )
    for name in ("sha", "source_run_id", "lab_run_id"):
        assert got[name].get("required") == "true", f"{name} 不是必填——缺了它 trust2 没有主语"
    assert got["publish"].get("default") == '"false"', (
        f'publish 的默认值不是 "false"：{got["publish"]}'
    )


def test_the_first_segment_ends_by_dispatching_the_lab_with_publish_and_ack_forwarded():
    """`release.yml::dispatch_lab`：`needs` 含 build / desktop / trust；命令里七个 `-f` 齐全；
    `publish` 来自 trust 的输出、`ack_open_blockers` 原样来自输入——第二段 trust2 要拿它们
    重算 / 再核。第一段**不等结果**：job 里没有任何 `gh run watch` / `gh run view`。
    """
    wf = _wf(RELEASE)
    header = wf.jobs["dispatch_lab"].split("steps:")[0]
    needs = re.search(r"^\s+needs:\s*\[(.+?)\]\s*$", header, re.M)
    assert needs, "dispatch_lab 的 needs 不是列表写法"
    assert set(re.findall(r"[\w-]+", needs.group(1))) == {"build", "desktop", "trust"}, needs.group(
        1
    )
    env = dict(re.findall(r"(?m)^\s+([A-Z_]+):\s*(\$\{\{.*?\}\})\s*$", header))
    assert env.get("LAB_PUBLISH") == "${{ needs.trust.outputs.publish }}", env
    assert env.get("LAB_ACK") == "${{ inputs.ack_open_blockers }}", env
    runs = "\n".join(wf.job_runs("dispatch_lab"))
    cmd = runs[runs.index(_DISPATCH_CMD) :].split("\n\n")[0]
    for field, value in (
        ("mode", "release"),
        ("sha", '"$LAB_SHA"'),
        ("baseline_tag", '""'),
        ("use_prebuilt_dist", "true"),
        ("source_run_id", '"$GITHUB_RUN_ID"'),
        ("publish", '"$LAB_PUBLISH"'),
        ("ack_open_blockers", '"$LAB_ACK"'),
    ):
        m = re.search(rf"-f\s+{field}=(\S+)", cmd)
        assert m, f"派发命令少了 -f {field}="
        assert m.group(1) == value, f"-f {field}= 的值是 {m.group(1)!r}，期望 {value!r}"
    assert "gh run watch" not in runs and "gh run view" not in runs, "第一段不许等 lab 的结果"


def test_trust2_reverifies_ancestry_tag_and_blockers_with_the_first_segments_ack():
    """`trust2` 三段判断齐全：ancestry（`--is-ancestor`）、tag（已存在必须指向同一 SHA）、
    blocker（对**此刻** open 的清单重跑 `release_blockers.py`，输入是第一段的 ack）。

    判据落在**那个 job 自己的 run: 正文**里，不是「文件里有没有这个词」——release.yml 的
    trust 里同样三段都有，整文件搜索会被它满足。blocker 那步的 `ACK` 必须来自
    `inputs.ack_open_blockers`：第二段不新增签字入口，它只认第一段传下来的那份。
    """
    wf = _wf(PUBLISH)
    shell = "\n".join(wf.job_runs("trust2"))
    assert "git merge-base --is-ancestor" in shell, "trust2 少了 origin/main 祖先判断"
    assert "refs/tags/${REL_TAG}^{commit}" in shell and 'EXISTING" != "$SHA' in shell, (
        "trust2 少了 tag 指向判断"
    )
    assert "scripts/ci/release_blockers.py" in shell, "trust2 没有再核 release:blocker"
    blocker = [st for st in wf.steps("trust2") if "release_blockers.py" in st]
    assert len(blocker) == 1, "trust2 里跑 release_blockers.py 的步骤应恰好一步"
    # 三段判断没有一段可以被条件关掉：trust2 的步骤一律不带 if / continue-on-error
    # （变异「blocker 那步加 if: false」曾存活——步骤还在、判据还在、就是不执行）
    for st in wf.steps("trust2"):
        name = _Workflow.field(st, "name") or st.strip().splitlines()[0]
        assert _Workflow.field(st, "if") is None, (
            f"trust2 步骤「{name}」带了 if:——判断不许被条件关掉"
        )
        assert "continue-on-error" not in st, f"trust2 步骤「{name}」带了 continue-on-error"
    env = dict(re.findall(r"(?m)^\s+([A-Z_]+):\s*(\$\{\{.*?\}\})\s*$", blocker[0]))
    assert env.get("ACK") == "${{ inputs.ack_open_blockers }}", (
        f"blocker 门禁的 ACK 不是第一段传来的那份：{env}"
    )
    assert re.search(r"labels=release:blocker&state=open", blocker[0]), (
        "查的不是此刻 open 的 release:blocker"
    )
    assert re.search(r'--ack\s+"\$\{ACK:-\}"', blocker[0]), "release_blockers.py 没拿到 ACK"
    # 三道门各自都要 exit 1——「判了却不挡」。按缩进切块，不用非贪婪正则
    # （嵌套 if 会让它停在内层 fi 或跳到下一处 exit 1 上）
    for opener in (
        'if ! git merge-base --is-ancestor "$SHA" origin/main; then',
        'if [ "$EXISTING" != "$SHA" ]; then',
    ):
        block = _shell_block(shell, opener)
        assert re.search(r"^\s*exit 1\s*$", block, re.M), f"这道门里没有 exit 1：{opener}"
    # checkout 要完整历史，否则 --is-ancestor 判不了
    co = [st for st in wf.steps("trust2") if "actions/checkout@" in st]
    assert len(co) == 1 and _Workflow.with_scalars(co[0]).get("fetch-depth") == "0", (
        "trust2 的 checkout 不是 fetch-depth: 0"
    )


def test_trust2_recomputes_publish_and_the_payload_can_only_press_it_to_false():
    """**publish 由 trust2 按 tag 规则重算，载荷只能把 true 压成 false。**

    正面钉法：trust2 的 shell 里对 `PUBLISH` 的赋值只有两处——`PUBLISH=true` 恰好一次，且
    它所在的 `if` 条件同时要求 `TAG_PUBLISH` 与 `PUBLISH_IN` 都是 true；`TAG_PUBLISH=true`
    恰好一次，且在「已存在的 tag 指向同一 SHA」那个分支里。任何 `PUBLISH="$PUBLISH_IN"`
    这种直连都会让赋值集合多出一个非 true/false 的值，当场红。
    """
    shell = "\n".join(_wf(PUBLISH).job_runs("trust2"))
    assigns = re.findall(r"(?m)^\s*PUBLISH=(\S+)\s*$", shell)
    assert sorted(assigns) == ["false", "true"], (
        f"trust2 对 PUBLISH 的赋值是 {assigns}——只许 true 一次、false 一次"
    )
    gate = re.search(
        r'if \[ "\$TAG_PUBLISH" = "true" \] && \[ "\$PUBLISH_IN" = "true" \]; then\n\s*PUBLISH=true\n\s*else\n\s*PUBLISH=false\n\s*fi',
        shell,
    )
    assert gate, "PUBLISH=true 不在「TAG_PUBLISH 与 PUBLISH_IN 同时为 true」那个分支里"
    tag_assigns = re.findall(r"(?m)^\s*TAG_PUBLISH=(\S+)\s*$", shell)
    assert sorted(tag_assigns) == ["false", "true"], f"TAG_PUBLISH 的赋值是 {tag_assigns}"
    branch = _shell_block(
        shell,
        'if EXISTING="$(git rev-parse --verify "refs/tags/${REL_TAG}^{commit}" 2>/dev/null)"; then',
    )
    assert re.search(r"(?m)^\s*TAG_PUBLISH=true\s*$", branch), (
        "TAG_PUBLISH=true 不在 tag 已存在的分支里"
    )
    assert 'EXISTING" != "$SHA' in branch, "tag 已存在的分支里没有「必须指向同一 SHA」"
    assert not re.search(r"(?m)^\s*TAG_PUBLISH=true\s*$", shell.replace(branch, "")), (
        "TAG_PUBLISH=true 在 tag 已存在的分支之外还有一处"
    )
    # PUBLISH_IN 只在那个 && 条件里被读——不许在别处成为 true 的来源
    reads = [ln for ln in shell.splitlines() if "$PUBLISH_IN" in ln and "echo" not in ln]
    assert len(reads) == 1 and "TAG_PUBLISH" in reads[0], f"PUBLISH_IN 在这些地方被读：{reads}"


def test_trust2_reads_the_lab_status_exactly_once_and_never_waits():
    """lab 结论**读一次**：trust2 里对 `commits/<sha>/status` 的 `gh api` 恰好一处、不在循环里；
    判 `lab/release` 的 `state == success`，且 `target_url` 与载荷 `lab_run_id` 对得上。
    第二段没有任何 `gh run watch` / `gh run view`。
    """
    wf = _wf(PUBLISH)
    shell = "\n".join(wf.job_runs("trust2"))
    calls = re.findall(r'gh api "repos/\$\{GITHUB_REPOSITORY\}/commits/\$\{SHA\}/status"', shell)
    assert len(calls) == 1, f"读 commit status 的调用应恰好一次，实际 {len(calls)}"
    step = [st for st in wf.steps("trust2") if "commits/${SHA}/status" in st]
    assert len(step) == 1
    body = step[0]
    assert not re.search(r"^\s*(for|while|until)\b", body, re.M), (
        "读 status 的步骤里有循环——那是轮询"
    )
    assert 'select(.context == "lab/release")' in body, "没按 lab/release 这个 context 取"
    shell_step = "\n".join(wf.job_runs("trust2"))
    for opener, why in (
        ('if [ -z "$LAB_STATE" ]; then', "status 不存在（lab 没跑完 / 没派发）"),
        ('if [ "$LAB_STATE" != "success" ]; then', "state != success"),
    ):
        block = _shell_block(shell_step, opener)
        assert re.search(r"^\s*exit 1\s*$", block, re.M), f"{why} 那一支里没有 exit 1——判了却不挡"
    env = dict(re.findall(r"(?m)^\s+([A-Z_]+):\s*(\$\{\{.*?\}\})\s*$", body))
    assert env.get("LAB_RUN_ID") == "${{ inputs.lab_run_id }}", env
    assert env.get("SHA") == "${{ steps.resolve.outputs.sha }}", (
        f"读 status 的主语不是 trust2 解析出的 SHA：{env}"
    )
    assert "/Tavotto/ci-infra/actions/runs/${LAB_RUN_ID}" in body, (
        "没把 target_url 与 lab_run_id 那个 ci-infra run 对上"
    )
    case = re.search(
        r'case "\$LAB_URL" in\n\s*"\$EXPECTED_URL"\|"\$EXPECTED_URL"/\*\) ;;\n\s*\*\)\n(?P<rest>.*?);;',
        body,
        re.S,
    )
    assert case, "target_url 的 case 只许两支：期望值（含子路径）放行、其余进 *)"
    assert re.search(r"^\s*exit 1\s*$", case.group("rest"), re.M), "target_url 对不上时没有 exit 1"
    whole = wf.run_text()
    assert "gh run watch" not in whole and "gh run view" not in whole, "第二段不许等任何 run"


def test_validate_artifacts_downloads_the_first_segments_artifacts_by_source_run_id():
    """第二段的 `validate_artifacts` 从**第一段那次 run** 取全部产物：每个 download-artifact 都带
    `run-id: ${{ inputs.source_run_id }}` + `github-token: ${{ github.token }}`，job 有 `actions: read`。
    后面三个发布 job 取的是本 run 自己上传的 `release-assets`——**不带** run-id（带了就是去第一段
    找一个不存在的 artifact）。
    """
    wf = _wf(PUBLISH)
    dl = [st for st in wf.steps("validate_artifacts") if "actions/download-artifact@" in st]
    assert len(dl) == 4, (
        f"validate_artifacts 应有 4 个 download-artifact（dist / desktop / manifests / updater），实际 {len(dl)}"
    )
    for st in dl:
        w = _Workflow.with_scalars(st)
        assert w.get("run-id") == "${{ inputs.source_run_id }}", f"没从第一段的 run 取：{w}"
        assert w.get("github-token") == "${{ github.token }}", f"跨 run 取 artifact 要 token：{w}"
    header = wf.jobs["validate_artifacts"].split("steps:")[0]
    assert re.search(r"^\s+actions:\s*read\s*$", header, re.M), (
        "validate_artifacts 没有 actions: read——跨 run 的 REST 路径会 403"
    )
    for job in ("github_release", "pypi", "plugin_stable"):
        own = [st for st in wf.steps(job) if "actions/download-artifact@" in st]
        assert own, f"{job} 没有 download-artifact"
        for st in own:
            w = _Workflow.with_scalars(st)
            assert w.get("name") == "release-assets" and "run-id" not in w, (
                f"{job} 取的不是本 run 的 release-assets：{w}"
            )


def test_release_mode_never_overwrites_the_performance_baseline():
    """候选版把基线覆盖掉的话，「和基线比」就变成「和自己比」，永远不会红。"""
    text = _wf(REUSABLE).run_text() + _strip_comments(REUSABLE.read_text(encoding="utf-8"))
    assert "--no-update" in text
    assert "inputs.mode == 'release'" in text, "不写基线必须只在发行档生效"


# ── 诊断在最需要时仍可用 ──────────────────────────────────────────────────


def test_always_steps_never_depend_on_a_step_that_may_not_have_run():
    """`always()` 的收尾步骤不能依赖某个**可能没跑过**的步骤的输出。

    #61：汇总写的是 `${{ steps.venv.outputs.python }} …`，体检先失败时
    那一步没跑，变量是空串，命令退化成直接执行 100644 的脚本 → exit 126。
    **这个「总是要跑」的汇总，恰恰在真的有失败要汇总时自己挂掉。**
    """
    offenders = []
    for p in sorted(WF.glob("*.yml")):
        wf = _wf(p)
        for jname, step in wf.all_steps():
            if not re.search(r"^\s+if:.*(always\(\)|failure\(\))", step, re.M):
                continue
            for m in re.finditer(r"steps\.venv\.outputs\.python(.*?)\}\}", step, re.S):
                if "||" not in m.group(1):
                    name = _Workflow.field(step, "name") or "?"
                    offenders.append(f"{p.name}::{jname} 步骤「{name}」")
    assert not offenders, (
        "这些步骤在前序失败时照跑，却依赖「建验证环境」的输出（那时是空串）：\n  "
        + "\n  ".join(offenders)
    )


# ── Codex 第一轮逮到的三条 P1（2026-08-23）──────────────────────────────


def test_a_manually_created_tag_is_pinned_to_the_trusted_sha():
    """`action-gh-release` 在 tag 不存在时会**替我们建一个**。

    不给 `target_commitish`，它用的是 `GITHUB_SHA` —— dispatch 时的 ref
    （例如 `main` 的当前 HEAD），而不是 `trust` 解析并验证过的那个 SHA。
    「dispatch 之后、publish 之前 main 又前进了」时这是两个 commit，
    而它发生在整条链**最不可逆的那一步**：tag ruleset 是 immutable，
    建错了改不动也删不掉（仓库里已经躺着两个这样的 tag）。
    """
    pub = _wf(PUBLISH)
    steps = [s for s in pub.steps("github_release") if "action-gh-release" in s]
    assert len(steps) == 1
    w = _Workflow.with_scalars(steps[0])
    assert w.get("target_commitish") == "${{ needs.trust2.outputs.sha }}", (
        f"建 Release 没有把 tag 钉在受信 SHA 上：target_commitish={w.get('target_commitish')!r}"
    )


def test_the_dry_run_still_exercises_signing_and_the_updater_manifest():
    """**演练必须验签名与 updater 清单** —— 那是发布链上最容易悄悄坏掉的两段。

    从前 release.yml 把 `publish` 传给桌面链，而那边同一个值控制着三件事：
    签名凭据门禁、provenance、整个 `updater-manifest` job。于是
    `publish=false` 的演练把它们一起关掉了 —— 演练照样全绿，而
    「没配 minisign 私钥」「latest.json 拼不出来」这两种失败要等到正式
    发版当天才现形。v0.7.0 就是带着一份只有 windows 的 latest.json 发出去的。

    「是不是发行构建」与「挂不挂 Release」是两件事。
    """
    rel = _wf(RELEASE)
    desktop = rel.jobs["desktop"]
    assert re.search(r"release_build:\s*true", desktop), (
        "桌面链没有恒以发行构建模式运行 —— 演练会跳过签名与更新包"
    )
    assert "publish" not in desktop.split("secrets:")[0].replace("release_build", ""), (
        "桌面链又跟着 publish 走了"
    )

    _wf(DESKTOP)  # 构造即自检形状：切不出预期的 job/step 就抛
    head = _strip_comments(DESKTOP.read_text(encoding="utf-8")).split("\njobs:")[0]
    assert "release_build:" in head
    assert "inputs.publish" not in _strip_comments(DESKTOP.read_text(encoding="utf-8")), (
        "桌面链里还有 inputs.publish —— 它不该知道挂不挂 Release"
    )


def test_a_missing_updater_manifest_can_never_pass_silently():
    """少了 latest.json，桌面用户永远查不到新版本，而整条链全绿。"""
    pub = _wf(PUBLISH)
    for step in pub.steps("validate_artifacts"):
        if "updater-manifest" not in step:
            continue
        assert "continue-on-error" not in step, (
            "取 updater 清单允许失败 —— 那会把一次 artifact 传输故障"
            "变成「发了一个没有 latest.json 的 Release」"
        )
        break
    else:
        raise AssertionError("找不到取 updater-manifest 的那一步")


def test_compatbench_runs_on_the_lock_pinned_interpreter():
    """CompatBench 必须用这一轮刚建的 venv，不能走 pool 的优先级链。

    实验室 runner 是**持久**的：`TAVOTTO_WORKER_PYTHON` 或设置里存下来的
    解释器一旦存在，`_worker_python(None)` 就会拿它去跑 —— 像素基线于是
    比的是另一套 matplotlib，而报告不会说。
    """
    step = [s for s in _wf(REUSABLE).steps("qualify") if "compat_matrix.py" in s]
    assert step, "找不到 CompatBench 那一步"
    assert "--python" in step[0], "CompatBench 没有钉解释器"


def test_pypi_gets_exactly_the_two_files_the_manifest_names():
    """**PyPI 那一步不许 glob。**

    摊平之后 `dist/` 里的 `*.tar.gz` **同时匹配 Python sdist 与 macOS 的
    `Tavotto.app.tar.gz`**（桌面更新包）。用 glob 就会把一个桌面更新包
    交给 PyPI —— 而 PyPI 上同名文件永远不能重传，失败时前面的可能已经传上去了。

    这是「七个下游步骤各自猜文件名」（#63）的复发，而且发生在**刚刚引入
    产物清单的这条 PR 里** —— 清单存在的意义就是让这种猜测不可能。
    """
    pub = _wf(PUBLISH)
    steps = [s for s in pub.steps("pypi") if "PyPI" in (_Workflow.field(s, "name") or "")]
    assert steps, "找不到把产物交给 PyPI 的那一步"
    body = "\n".join(steps)
    assert "artifact_manifest.py path" in body, "PyPI 的输入不是从清单解出来的"
    assert "*.tar.gz" not in body, (
        "PyPI 那一步还有 `*.tar.gz` —— 它会同时匹配 macOS 的 Tavotto.app.tar.gz"
    )
    assert "*.whl" not in body


def test_the_pypi_job_can_actually_run_the_manifest_script():
    """它要跑仓库里的脚本，就得先有仓库。

    只改「从清单取路径」而忘了 checkout，症状是 `No such file or directory`
    —— 发生在整条链的最后一步，且此时 GitHub Release 已经建好了。
    """
    pub = _wf(PUBLISH)
    assert any("actions/checkout" in s for s in pub.steps("pypi")), (
        "pypi job 没有 checkout，却要跑 scripts/ci/artifact_manifest.py"
    )


def test_an_existing_tag_pointing_elsewhere_is_refused():
    """`target_commitish` 只在 tag **不存在**时起作用。

    tag 已存在的话 `action-gh-release` 直接复用现有那个，而现有 tag 完全
    可能指向别处 —— 那时 Release 挂的 tag 与产物来自两个 commit，
    而没有任何一步会报错。

    这不是假想：仓库里此刻就躺着 v0.9.0 与 v0.9.1 两个指向旧 commit、
    且因为 immutable ruleset 改不动也删不掉的 tag。
    """
    for path, job in ((RELEASE, "trust"), (PUBLISH, "trust2")):
        trust = _wf(path).jobs[job]
        assert "refs/tags/${REL_TAG}" in trust, f"{path.name}::{job} 没有检查 tag 是否已存在"
        assert 'EXISTING" != "$SHA' in trust, f"{path.name}::{job} 存在的 tag 没有与本次 SHA 比对"


def test_pending_release_notes_cannot_slip_past_a_tag():
    """待发条目没并进这一版的正文，就不许发。

    issue #244：#215 修好标注旋转的导出方向后，存量文档里手工补偿过角度的
    用户升级会拿到反向的导出——那句迁移提示只写在 PR 正文的「遗留」段里，
    发行说明是发版那天写的，没人回头翻，于是一版都没发出去。用户一个字
    看不到，而整条发布链全绿。

    闸必须在**读手写正文之前**：读完再查，`has_notes` 已经写出去了。
    """
    step = [
        s
        for s in _wf(PUBLISH).steps("validate_artifacts")
        if 'F="docs/release-notes/${TAG}.md"' in s
    ]
    assert step, "找不到拼 release body 的那一步"
    run = step[0]
    assert "scripts/check_pending_release_notes.py" in run, (
        "拼 release body 时没有检查待发条目 —— 漏掉的迁移提示会静默发不出去"
    )
    assert run.index("check_pending_release_notes.py") < run.index(
        'F="docs/release-notes/${TAG}.md"'
    ), "待发条目的检查跑在读手写正文之后 —— 那时该发的正文已经定了"


#: 「退出码对了」与「报文说得出话」是两个维度，只钉前者会漏掉整条报文。
#: 这条报文本身也要说人话：`err is None` 撞进 `"x" in err` 报的是 TypeError，
#: 读的人只看得见「用例坏了」，看不见「stderr 一个字节都没捕到」。
_NO_STDERR = (
    "没捕到子进程的 stderr —— 退出码对不代表报文还在（Windows 上解码异常会被 _readerthread 吞掉）"
)


def _check_pending(tmp_path, body: str | None, write: bool = True):
    """按发布链的用法跑一次闸：返回 (退出码, stderr)。

    `body is None` = 暂存文件根本不存在；`write=False` = 文件已由调用方摆好。
    """
    pending = tmp_path / "UNRELEASED.md"
    if body is not None and write:
        pending.write_text(body, encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(WF.parents[1] / "scripts" / "check_pending_release_notes.py"),
            "--pending",
            str(pending),
            "--tag",
            "v9.9.9",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return proc.returncode, proc.stderr


def test_the_pending_notes_gate_reds_on_an_unmerged_entry(tmp_path):
    """还有 `## ` 段落 = 还没并入。退出码判定，不看它打印了什么。"""
    code, err = _check_pending(tmp_path, "<!-- 说明 -->\n\n## Notes\n\n**Rotation.**\n")
    assert code == 1, f"带着未并入的条目却放行了（exit {code}）"
    assert err is not None, _NO_STDERR
    assert "v9.9.9" in err, "错误信息没说该并到哪一版去"


def test_the_pending_notes_gate_greens_once_the_entries_are_moved_out(tmp_path):
    """并入 = 段落搬走，说明性注释留在原处。

    判据要是写成「文件非空」，这份留下来的注释会把闸永远钉红，
    第一个撞上的人就会把它删掉——门禁于是消失得无声无息。
    """
    code, _ = _check_pending(tmp_path, "<!-- 说明：发版时把 `## ` 段落搬进 vX.Y.Z.md -->\n")
    assert code == 0, f"条目已并入却仍然红（exit {code}）"


def test_the_pending_notes_gate_reds_when_the_staging_file_is_gone(tmp_path):
    """**「找不到」不是「已迁移」。**

    闸的第一版把文件不存在读成了「没有待发条目」，于是删掉
    `UNRELEASED.md` 就能让它绿——而它守的恰恰是那份文件里的东西：
    迁移提示跟着文件一起消失，发布链全绿，用户升级后一个字也看不到。
    判据在，但它要读的那个东西不在时它不红——本仓库反复清理的那个家族
    （#238 的 `faulthandler_exit_on_timeout` 退化成一条 warning 是同一形状）。

    M4 钉的是对称的另一半（判成「文件非空」→ 永远红 → 被人删掉），
    两个方向缺一条这道闸就是摆设。
    """
    code, err = _check_pending(tmp_path, None)
    assert code == 1, f"暂存文件不见了却放行（exit {code}）"
    assert err is not None, _NO_STDERR
    assert "找不到" in err, "报文没说清缺的是这份文件 —— 报错文案也是断言"
    assert "Traceback" not in err, "读不到时甩了个栈：看到栈的人会以为脚本坏了，然后把这一步拿掉"


def test_the_pending_notes_gate_reds_when_the_staging_file_is_unreadable(tmp_path):
    """读不出来也不算已并入，且报的是原因不是栈。"""
    bad = tmp_path / "UNRELEASED.md"
    bad.write_bytes(b"## Notes\n\xff\xfe not utf-8\n")
    code, err = _check_pending(tmp_path, None, write=False)
    assert code == 1, f"文件解不出来却放行（exit {code}）"
    assert err is not None, _NO_STDERR
    assert "读不了" in err and "Traceback" not in err, err


def test_the_gate_message_survives_a_non_utf8_default_encoding(tmp_path):
    """报文的编码由脚本自己钉，不能由平台挑。

    这条闸的报文是中文的，而它**永远是被 `capture_output=True` 读走的**——
    stdout/stderr 永远是管道，而 Windows 上管道退回系统 ANSI 代码页
    （runner 上 cp1252）。那时中文走 `backslashreplace` 变 ASCII 转义，
    `——` 却**能**编成单字节 `0x97`，父进程按 UTF-8 严格解就炸在那个字节上。

    **而那个异常没人接**：Windows 的 `communicate()` 在 `_readerthread` 里解码，
    线程死掉、缓冲区留空，`subprocess.run` 照常返回——`returncode` 是对的、
    `stderr` 是 `None`。2026-09-04 #253 的 windows 腿实测：`assert code == 1`
    三条全过，只有读报文的那三条炸在 TypeError 上。

    **判据不看源码里有没有 reconfigure，也不看父进程解出了什么**：前者换个
    写法就漏，后者的行为按平台分岔（POSIX 抛异常、Windows 静默给 None）。
    这里量的是**子进程吐出来的字节**——不进文本模式，自己解一次。
    量字节这一维在任何平台上都一样，所以这条用例在 mac/Linux 上就能抓到
    只在 Windows 上发作的那个缺陷。
    """
    pending = tmp_path / "UNRELEASED.md"
    pending.write_text("<!-- 说明 -->\n\n## Notes\n\n**Rotation.**\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(WF.parents[1] / "scripts" / "check_pending_release_notes.py"),
            "--pending",
            str(pending),
            "--tag",
            "v9.9.9",
        ],
        capture_output=True,  # 刻意不进文本模式：要量的是字节，不是父进程的解码器
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},  # runner 上的默认编码
    )
    assert proc.returncode == 1, f"cp1252 下闸没红（exit {proc.returncode}）"
    assert proc.stderr, "cp1252 下一个字节都没吐出来"
    try:
        text = proc.stderr.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError(
            f"报文不是 UTF-8（{exc}）—— 脚本没钉自己的输出编码，"
            "Windows 上这段字节会让父进程的 stderr 静默变成 None"
        ) from None
    assert "v9.9.9" in text, f"报文没说该并到哪一版：{text!r}"


def _real_publisher_pushes(wf: _Workflow) -> list[tuple[str, int]]:
    """真推发行分支的步骤：跑 plugin_publish.py、带 `--yes`（或 `$YES`）、且没有
    `--remote`（演练用 `--remote "$R"` 指向临时 bare 仓库，只读 plan 没有 `--yes`）。"""
    out = []
    for job in wf.jobs:
        for i, step in enumerate(wf.steps(job)):
            if "plugin_publish.py" not in step:
                continue
            if not re.search(r"--yes|\$YES", step) or "--remote" in step:
                continue
            out.append((job, i))
    return out


def test_the_plugin_publisher_has_push_credentials_before_it_pushes():
    """发布器在**自己的临时仓库**里 push，actions/checkout 写进 checkout 本地 config
    的凭据对它不可见。首次真跑（plugin-stable.yml run 33979476158）死在
    `could not read Username for 'https://github.com'`——读回正确报了 not_landed，
    分支没建出来，但 bootstrap 一步都没往前走。临时 bare 仓库上的演练永远抓不到这
    件事：file:// 不要凭据。

    判据：每个真推发布器的步骤，同一 job 里**前面**必须有一步把 github.com 的
    extraheader 配进全局 git config（与 actions/checkout 同一形态）。
    """
    found = 0
    for wf in (_wf(PUBLISH), _wf(PLUGIN_STABLE)):
        pushes = _real_publisher_pushes(wf)
        for job, i in pushes:
            found += 1
            earlier = "\n".join(wf.steps(job)[:i])
            assert re.search(
                r'git config --global "?http\.https://github\.com/\.extraheader"?\s+"AUTHORIZATION: basic',
                earlier,
            ), f"{wf.path.name}/{job}: 真推发布器的步骤前没有配推送凭据"
    # release-publish.yml 的 promote + plugin-stable.yml 的手动发布器；数目变了说明选择器或
    # workflow 形状变了，两种都要人看一眼，而不是让判据静默缩到零
    assert found == 2, f"真推发布器步骤数 {found} != 2"


def test_a_job_that_configures_global_credentials_keeps_no_local_copy():
    """第二次真跑（plugin-stable.yml run 34005899795）死在 `remote: Duplicate header:
    "Authorization"` → HTTP 400：job 给 git 配了全局 extraheader，而 actions/checkout 默认
    又把同一凭据留在 checkout 的本地 config；发布器在 checkout 目录里跑 `ls-remote`，git
    把两份都发了出去。

    判据：任何 job 里只要有一步 `git config --global … extraheader`，同一 job 的
    actions/checkout 必须 `persist-credentials: false`——凭据只留一份。
    """
    seen = 0
    for wf in (_wf(PUBLISH), _wf(PLUGIN_STABLE)):
        for job in wf.jobs:
            steps = wf.steps(job)
            if not any(re.search(r"git config --global .*extraheader", s) for s in steps):
                continue
            seen += 1
            checkouts = [s for s in steps if "actions/checkout@" in s]
            assert checkouts, f"{wf.path.name}/{job}: 没有 checkout 步骤？"
            for co in checkouts:
                assert _Workflow.with_scalars(co).get("persist-credentials") == "false", (
                    f"{wf.path.name}/{job}: checkout 还留着本地凭据，发布器会发两份 Authorization"
                )
    assert seen == 2, f"配全局凭据的 job 数 {seen} != 2"
