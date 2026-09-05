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

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"
RELEASE = WF / "release.yml"
DESKTOP = WF / "desktop-tauri.yml"
LAB = WF / "lab-ci.yml"
REUSABLE = WF / "_lab-qualification.yml"
PLUGIN_STABLE = WF / "plugin-stable.yml"


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
        out = []
        for _, step in self.all_steps():
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
    assert set(rel.jobs) >= {
        "trust",
        "build",
        "desktop",
        "lab_release_gate",
        "validate_artifacts",
        "github_release",
        "pypi",
    }, f"release.yml 解析出的 job：{sorted(rel.jobs)}"
    assert len(rel.steps("validate_artifacts")) >= 10
    assert "artifact_manifest.py" in rel.run_text()

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
    for p in (RELEASE, DESKTOP, LAB, REUSABLE):
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


def test_every_publishing_job_is_gated_on_publish():
    """建 Release 与发 PyPI **都**必须挂在 trust 的 publish 输出上。

    漏掉任何一个，演练就会真的发布出去。
    """
    rel = _wf(RELEASE)
    for name in ("github_release", "pypi"):
        body = rel.jobs[name].split("steps:")[0]
        assert "needs.trust.outputs.publish == 'true'" in body, f"{name} 没有挂在 publish 上"


def test_the_dry_run_still_exercises_every_verification_step():
    """演练必须真的跑完 SBOM / checksum / provenance / 清单校验。

    只在「建 Release」那个 job 里做这些，等于**它们只在真发布时才执行**
    ——而那个 job 自 v0.8.0 起一次都没成功跑到过，#63 因此躺了好几周。
    """
    rel = _wf(RELEASE)
    head = rel.jobs["validate_artifacts"].split("steps:")[0]
    assert not re.search(r"^\s+if:", head, re.M), "产物校验不许被 publish 门控——演练正是要跑它"
    blob = "\n".join(rel.steps("validate_artifacts"))
    for needle in ("sbom-action", "SHA-256", "attest-build-provenance", "合并并校验产物清单"):
        assert needle in blob, f"演练里少了：{needle}"


def test_release_only_uses_the_sha_that_trust_resolved():
    """所有 job 只认 trust 输出的 SHA，不各自再解析一次 ref。"""
    rel = _wf(RELEASE)
    for name, body in rel.jobs.items():
        if name == "trust":
            continue
        assert "github.ref_name" not in body, (
            f"{name} 还在用 github.ref_name——发布链只认 trust 验过的 SHA"
        )


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
    rel = _wf(RELEASE)
    steps = [s for s in rel.steps("validate_artifacts") if "合并并校验产物清单" in s]
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
    rel = _wf(RELEASE)
    attach = [s for s in rel.steps("github_release") if "action-gh-release" in s]
    assert len(attach) == 1, "挂 Release 只该有一步"
    for needle in ("assets/dist/*", "SHA256SUMS.txt", "tavotto-sbom.spdx.json", "latest.json"):
        assert needle in attach[0], f"一次性挂载里少了 {needle}"
    # Codex 插件（zip + codex-plugin.json + 随包清单）在 dist/ 里随 `assets/dist/*` 挂上
    # （ADR 0043：由 build job 造、validate 成对验过），所以它们必须是 validate 的必需 role
    validate = "\n".join(rel.steps("validate_artifacts"))
    for role in ("codex-plugin", "codex-plugin-manifest", "codex-plugin-build"):
        assert role in validate, f"validate_artifacts 的 --require 里少了 {role}"


def test_the_published_artifacts_are_re_verified_before_attaching():
    """下载 artifact 再上传是一次真实的搬运，中间任何一环都可能改内容。"""
    rel = _wf(RELEASE)
    blob = "\n".join(rel.steps("github_release"))
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


def test_every_caller_gates_the_sha_through_a_trust_job():
    """**可复用资格 workflow 的安全性由调用方兜底——那就必须有东西看着调用方。**

    `_lab-qualification.yml` 把 `inputs.sha` 直接 checkout 到常驻的
    self-hosted runner 上。单看这个文件，那个 sha 是任意的——CodeQL 的
    「cache poisoning via execution of untrusted code」正是这么读的，
    而它读得没错：**保证不在这个文件里**。

    保证在调用方：两个 caller 都先跑一个 trust job，拒绝既不是 `origin/main`
    祖先、又没有 tag 指向的 commit。问题是从前没有任何东西要求**下一个**
    caller 也这么做——加一个直接传 `inputs.ref` 的调用方，长期 runner 就
    开始执行未经 review 的代码，而且没有一条用例会红。

    这条用例把那份口头约定变成结构约束：**每个** caller 的 `sha:` 必须来自
    某个 job 的输出，且那个 job 里真的有 ancestry 判断。
    """
    callers = [
        p
        for p in WF.glob("*.yml")
        if "_lab-qualification.yml" in p.read_text(encoding="utf-8")
        and p.name != "_lab-qualification.yml"
    ]
    assert callers, "没找到任何调用方——这条用例本身失效了"
    for path in callers:
        wf = _wf(path)
        job = next((n for n, b in wf.jobs.items() if "_lab-qualification.yml" in b), None)
        assert job, f"{path.name}: 找不到调用 job"
        m = re.search(r"^\s*sha:\s*\$\{\{\s*needs\.([\w-]+)\.outputs\.sha", wf.jobs[job], re.M)
        assert m, (
            f"{path.name}::{job} 的 sha 不是来自某个 job 的输出——"
            "常驻 runner 会执行一个没人验过的 commit"
        )
        trust = wf.jobs.get(m.group(1))
        assert trust and "--is-ancestor" in trust, (
            f"{path.name}: sha 来自 {m.group(1)}，但那个 job 里没有 ancestry 判断"
        )


def test_the_release_gate_cannot_be_evicted_by_a_routine_lab_run():
    """**发布门禁与日常 lab run 不许共用一个并发槽。**

    GitHub 每个 group 只保留**一个运行中 + 一个待定**，第三个排进来会
    *取代*那个待定的——`cancel-in-progress: false` 只保护正在跑的，
    保护不了在排队的。两条链共用一个槽时：发布门禁正等在一次长 lab run
    后面，这时一次 push to main 或定时任务进来，**日常 run 把待定的发布
    门禁挤掉，发版当场中止**，而且看起来像「被取消了」，没有原因。

    另一侧同样要守：`lab-ci.yml` 顶层**不许**再声明同名的固定组。
    workflow 级与它自己调用的 job 级申请同一个槽 = run 在等自己，
    表现是 8 秒失败、runner_name 为 null、零步骤、日志空白（#66 撞过）。

    机器独占不由这个槽负责：带 `tavotto-lab` 标签的 runner 只有一台，
    runner 端另有 flock。槽只负责同一条链内部去重。
    """
    qual = REUSABLE.read_text(encoding="utf-8")
    m = re.search(r"^\s*group:\s*(.+)$", qual, re.M)
    assert m, "可复用资格定义里读不出 concurrency group"
    group = m.group(1).strip()
    assert "github.workflow" in group, (
        f"槽名 {group!r} 不区分调用方——发布门禁会和日常 lab run 抢同一个槽，"
        "排队中的那个会被后来的挤掉"
    )

    # 调用方顶层不许再有固定的同名组（那会让 run 等自己）
    for path in (LAB, RELEASE):
        text = path.read_text(encoding="utf-8")
        top = re.search(r"^concurrency:\s*\n(?:\s+#.*\n)*\s+group:\s*(.+)$", text, re.M)
        if top:
            assert "lab-qualification" not in top.group(1), (
                f"{path.name} 顶层又声明了 lab-qualification 组——"
                "workflow 级与它调用的 job 级同名，job 会等一个自己已经持有的槽"
            )


def test_qualification_is_defined_exactly_once():
    """`lab-ci.yml` 与 `release.yml` 调的是**同一个**可复用 workflow。

    从前两边各有一份手抄的 shell。#61 修一个 bug 必须同时改两处，
    而两处的差别实测只有「`$LAB_MODE` vs 字面量 release」和一处换行
    ——它们本来就是同一段逻辑，只是被抄了两遍。
    """
    assert REUSABLE.is_file()
    for caller, job in ((LAB, "qualify"), (RELEASE, "lab_release_gate")):
        body = _wf(caller).jobs[job]
        assert "_lab-qualification.yml" in body, f"{caller.name}::{job} 没有走那份唯一定义"
        assert not re.search(r"^\s+steps:", body, re.M), f"{caller.name}::{job} 还带着自己的步骤"

    # 那段逻辑不许在别处再出现一次
    for p in (LAB, RELEASE):
        text = _wf(p).run_text()
        assert "lab_preflight.py" not in text, f"{p.name}: 又抄了一份体检"
        assert "summarize.py" not in text, f"{p.name}: 又抄了一份汇总"


def test_the_reusable_workflow_needs_no_write_permission():
    """长期 runner 拿不到任何签发能力。"""
    text = _strip_comments(REUSABLE.read_text(encoding="utf-8"))
    head = text.split("\njobs:")[0]
    assert re.search(r"^permissions:\s*\n\s+contents:\s*read\s*$", head, re.M)
    assert not re.search(r":\s*write\s*$", text, re.M), "实验室 job 不该有写权限"


def test_release_mode_verifies_the_exact_artifact():
    """发行档验的必须是 build 产出的**那一份** wheel。"""
    rel = _wf(RELEASE).jobs["lab_release_gate"]
    assert re.search(r"use_prebuilt_dist:\s*true", rel)
    assert re.search(r"mode:\s*release", rel)
    lab = _wf(LAB).jobs["qualify"]
    assert re.search(r"use_prebuilt_dist:\s*false", lab), (
        "lab 档没有候选包，必须自己造一个——那问的是另一个问题"
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
    rel = _wf(RELEASE)
    steps = [s for s in rel.steps("github_release") if "action-gh-release" in s]
    assert len(steps) == 1
    w = _Workflow.with_scalars(steps[0])
    assert w.get("target_commitish") == "${{ needs.trust.outputs.sha }}", (
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
    rel = _wf(RELEASE)
    for step in rel.steps("validate_artifacts"):
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
    rel = _wf(RELEASE)
    steps = [s for s in rel.steps("pypi") if "PyPI" in (_Workflow.field(s, "name") or "")]
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
    rel = _wf(RELEASE)
    assert any("actions/checkout" in s for s in rel.steps("pypi")), (
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
    trust = _wf(RELEASE).jobs["trust"]
    assert "refs/tags/${REL_TAG}" in trust, "trust 没有检查 tag 是否已存在"
    assert 'EXISTING" != "$SHA' in trust, "存在的 tag 没有与本次 SHA 比对"


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
        for s in _wf(RELEASE).steps("validate_artifacts")
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
    for wf in (_wf(RELEASE), _wf(PLUGIN_STABLE)):
        pushes = _real_publisher_pushes(wf)
        for job, i in pushes:
            found += 1
            earlier = "\n".join(wf.steps(job)[:i])
            assert re.search(
                r'git config --global "?http\.https://github\.com/\.extraheader"?\s+"AUTHORIZATION: basic',
                earlier,
            ), f"{wf.path.name}/{job}: 真推发布器的步骤前没有配推送凭据"
    # release.yml 的 promote + plugin-stable.yml 的手动发布器；数目变了说明选择器或
    # workflow 形状变了，两种都要人看一眼，而不是让判据静默缩到零
    assert found == 2, f"真推发布器步骤数 {found} != 2"
