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

import re
from pathlib import Path

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"
RELEASE = WF / "release.yml"
DESKTOP = WF / "desktop-tauri.yml"
LAB = WF / "lab-ci.yml"
REUSABLE = WF / "_lab-qualification.yml"


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
        body = self.text[m.end():]
        # 下一个顶层键（顶格）为止
        nxt = re.search(r"^\S", body, re.M)
        if nxt:
            body = body[:nxt.start()]
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
        seg = body[m.end():]
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
            for m in re.finditer(r"^([ \t]*)run:[ \t]*(\||>|>-|\|-)?[ \t]*(\S.*)?$",
                                 step, re.M):
                indent = len(m.group(1))
                if m.group(3) and not m.group(2):
                    out.append(m.group(3))          # 单行 `run: cmd`
                    continue
                body = []
                for line in step[m.end():].splitlines():
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
        m = re.search(rf"^\s+{re.escape(key)}:[ \t]*(\S.*?)\s*$", step, re.M)
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
        for line in step[m.end():].splitlines():
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
    assert set(rel.jobs) >= {"trust", "build", "desktop", "lab_release_gate",
                             "validate_artifacts", "github_release", "pypi"}, \
        f"release.yml 解析出的 job：{sorted(rel.jobs)}"
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
            offenders.append(f"{p.name}: …{text[max(0, m.start() - 50):m.end() + 30]}…")
    assert not offenders, (
        "这些地方在查 Release 是否存在——发布链里不该有任何一步等另一条链：\n  "
        + "\n  ".join(offenders))


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
        assert "action-gh-release" not in step, \
            f"desktop-tauri.yml::{jname} 又在自己挂 Release"
    for jname, body in desk.jobs.items():
        assert not re.search(r"^\s+contents:\s*write", body, re.M), \
            f"desktop-tauri.yml::{jname} 要了 contents:write——它不该写任何东西"


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
        "一次真实发布——而 PyPI 上同名文件永远不能重传")


def test_every_publishing_job_is_gated_on_publish():
    """建 Release 与发 PyPI **都**必须挂在 trust 的 publish 输出上。

    漏掉任何一个，演练就会真的发布出去。
    """
    rel = _wf(RELEASE)
    for name in ("github_release", "pypi"):
        body = rel.jobs[name].split("steps:")[0]
        assert "needs.trust.outputs.publish == 'true'" in body, \
            f"{name} 没有挂在 publish 上"


def test_the_dry_run_still_exercises_every_verification_step():
    """演练必须真的跑完 SBOM / checksum / provenance / 清单校验。

    只在「建 Release」那个 job 里做这些，等于**它们只在真发布时才执行**
    ——而那个 job 自 v0.8.0 起一次都没成功跑到过，#63 因此躺了好几周。
    """
    rel = _wf(RELEASE)
    head = rel.jobs["validate_artifacts"].split("steps:")[0]
    assert not re.search(r"^\s+if:", head, re.M), \
        "产物校验不许被 publish 门控——演练正是要跑它"
    blob = "\n".join(rel.steps("validate_artifacts"))
    for needle in ("sbom-action", "SHA-256", "attest-build-provenance",
                   "合并并校验产物清单"):
        assert needle in blob, f"演练里少了：{needle}"


def test_release_only_uses_the_sha_that_trust_resolved():
    """所有 job 只认 trust 输出的 SHA，不各自再解析一次 ref。"""
    rel = _wf(RELEASE)
    for name, body in rel.jobs.items():
        if name == "trust":
            continue
        assert "github.ref_name" not in body, \
            f"{name} 还在用 github.ref_name——发布链只认 trust 验过的 SHA"


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
    assert not offenders, ("这些单值输入拿到了通配符：\n  " + "\n  ".join(offenders))


def test_every_build_leg_emits_a_manifest():
    """两条构建链（Python / 桌面）都要产出自己那份清单。

    少一条，合并那步就少一个平台，而 `--require` 会在那时才报出来——
    可那时整条构建已经跑完了。
    """
    assert "artifact_manifest.py build" in _wf(RELEASE).run_text(), \
        "release.yml 的 Python 腿没造清单"
    assert "artifact_manifest.py build" in _wf(DESKTOP).run_text(), \
        "桌面腿没造清单"


def test_the_merged_manifest_is_verified_against_the_trusted_sha():
    """判据要落在**合并那一步自己**，不是「文件里某处提过 --source-sha」。

    第一版问的是后者，于是把合并步骤里的 `--source-sha` 删掉照样绿
    ——build 那步和 github_release 那步也各有一个，全文搜索被它们满足了。
    判据的主语又错了一次：该问「合并完那一步核不核对」。
    """
    rel = _wf(RELEASE)
    steps = [s for s in rel.steps("validate_artifacts")
             if "合并并校验产物清单" in s]
    assert len(steps) == 1, "找不到「合并并校验产物清单」这一步"
    step = steps[0]
    assert "artifact_manifest.py merge" in step
    assert "artifact_manifest.py verify" in step
    assert "--source-sha" in step, (
        "合并之后必须核对 source_sha——**「同一个 tag」证明不了「同一个 commit」**，"
        "这是唯一能挡住两条构建腿来自不同 commit 的地方")
    assert "--require wheel,sdist,windows-installer,macos-installer" in step, (
        "四个必须的角色少一个，就意味着那个平台的产物没造出来却照发")


def test_the_release_attaches_everything_in_one_go():
    rel = _wf(RELEASE)
    attach = [s for s in rel.steps("github_release") if "action-gh-release" in s]
    assert len(attach) == 1, "挂 Release 只该有一步"
    for needle in ("SHA256SUMS.txt", "tavotto-sbom.spdx.json",
                   "codex-plugin.json", "latest.json"):
        assert needle in attach[0], f"一次性挂载里少了 {needle}"


def test_the_published_artifacts_are_re_verified_before_attaching():
    """下载 artifact 再上传是一次真实的搬运，中间任何一环都可能改内容。"""
    rel = _wf(RELEASE)
    blob = "\n".join(rel.steps("github_release"))
    assert "artifact_manifest.py verify" in blob, (
        "挂上去之前没有重新校验——「Release 上挂的与发行资格验证过的不是"
        "同一个东西」是这条链上最不能接受的失败")


# ── 资格验证只有一份定义 ──────────────────────────────────────────────────

def test_qualification_is_defined_exactly_once():
    """`lab-ci.yml` 与 `release.yml` 调的是**同一个**可复用 workflow。

    从前两边各有一份手抄的 shell。#61 修一个 bug 必须同时改两处，
    而两处的差别实测只有「`$LAB_MODE` vs 字面量 release」和一处换行
    ——它们本来就是同一段逻辑，只是被抄了两遍。
    """
    assert REUSABLE.is_file()
    for caller, job in ((LAB, "qualify"), (RELEASE, "lab_release_gate")):
        body = _wf(caller).jobs[job]
        assert "_lab-qualification.yml" in body, \
            f"{caller.name}::{job} 没有走那份唯一定义"
        assert not re.search(r"^\s+steps:", body, re.M), \
            f"{caller.name}::{job} 还带着自己的步骤"

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
        "lab 档没有候选包，必须自己造一个——那问的是另一个问题")


def test_release_mode_never_overwrites_the_performance_baseline():
    """候选版把基线覆盖掉的话，「和基线比」就变成「和自己比」，永远不会红。"""
    text = _wf(REUSABLE).run_text() + _strip_comments(
        REUSABLE.read_text(encoding="utf-8"))
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
        + "\n  ".join(offenders))
