"""TAF 的 `figure_edit` 适配器（Phase 3，ADR 0138）：改图助手在**候选工作区**里改脚本，过了提交前检查才写回项目。

只在实验开关 `ai.taf.enabled`（`config.ai_settings()["taf"]["enabled"] is True`，默认关）打开时由 `ai_bridge.run` 走到；
开关关着时这个模块一行都不执行，旧路径逐字不变。

候选工作区 `data_dir()/cache/ai_candidates/<sid>/`：**只复制**目标脚本（与项目根的 `paper_style.py`，提示词会提到它），
不复制数据、不建链接（符号 / 硬链接会让「改副本」变成「改原件」）。Agent 的 cwd 是候选目录；两个 CLI 把写入挡在 cwd
之外是 CLI 的行为、不是 Tavotto 的保证（`08-risk-register.md` R-03），所以 spawn 前后对原项目做一次有界指纹兜底。

提交前（L1）：候选树里只有目标脚本变了、语法能解析、静态输出名集合不变、原项目没有被动过。全部 verified /
not_applicable 才提交；提交 = 持脚本锁、核原脚本仍是起跑时那一版、`scriptlock.write_script` 原子替换，随后统一刷新。
改前快照是 AI 桥起跑时就拍的那份（回滚沿用 `ai_bridge.revert`）。提交后：刷新结局 + 渲染层验证（`on_verify` 注入；
没接时记 unknown，结局最高 partial——没量过就不说完成）。

纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import ai_prompt_pack, ai_tasks, config, discover, figureverify, registry, scriptlock
from .ai_tasks import FAILED, NOT_APPLICABLE, UNKNOWN, VERIFIED, CheckResult, TaskError

TASK_TYPE = "figure_edit"
CANDIDATE_DIR = config.data_dir() / "cache" / "ai_candidates"
#: 和目标脚本一起复制进候选目录的共享样式（`ai_bridge._build_prompt` 按项目根有没有它决定提不提）
SHARED_STYLE = "paper_style.py"

#: 原项目指纹的上限：文件数、逐字节哈希的单文件上限（更大的只记 size + mtime）。超出文件数 → 这项记 unknown（不提交）
FINGERPRINT_MAX_FILES = 20000
FINGERPRINT_HASH_MAX_BYTES = 256 * 1024
#: 候选目录里最多看多少个文件（Agent 乱建文件时不至于无界遍历）
CANDIDATE_MAX_FILES = 2000
#: 指纹不看的目录名：版本库 / 缓存 / Tavotto 自己的项目收纳处（导出、备份由 Tavotto 写，不是 Agent）
_SKIP_DIRS = frozenset({".git", ".hg", ".svn", "__pycache__", ".ipynb_checkpoints"})


def enabled() -> bool:
    """实验开关：只认显式的 `True`（字符串 "true"、1 都不算），默认关。"""
    taf = config.ai_settings().get("taf")
    return isinstance(taf, dict) and taf.get("enabled") is True


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StaleSession(Exception):
    """`after` 钩子抛它：改后取到的会话执行的不是刚提交的那一版源码（去重回旧会话 / 启动采样不等于实际执行）。"""


# --------------------------------------------------------------------------- 有界指纹
def _readlink(p: Path) -> str | None:
    try:
        return os.readlink(p)
    except OSError:
        return None


def project_fingerprint(root: Path, *, exclude: frozenset[str] = frozenset()) -> dict:
    """原项目目录的有界文件指纹：`{"status": "ok", "files": {相对路径: [size, mtime_ns, sha256 | None, 链接目标?]}}`，
    超出文件数上限回 `{"status": "over_budget"}`。`exclude` 是 posix 相对路径。

    不跟随符号链接，链接本身按 lstat + `readlink` 目标记：文件链接是 `[size, mtime_ns, None, 目标]`（被换指向会变，
    即使 lstat 的 size / mtime 碰巧相同）；目录链接（`os.walk` 把它放进 `dirnames`）是 `["link", 目标, mtime_ns]`，
    不进入。命中 `_SKIP_DIRS` / `tavottofile` 的名字照旧跳过（即使它是链接）。

    边界：经链接写进**链接目标**（在项目外）的内容不在指纹里——cwd 之外的写入由 CLI 沙盒负责，指纹只兜项目内。"""
    root = Path(root)
    skip_names = _SKIP_DIRS | {config.PROJECT_STORE_DIRNAME}
    files: dict[str, list] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_names)
        for d in list(dirnames):
            p = Path(dirpath) / d
            if not p.is_symlink():
                continue
            dirnames.remove(d)  # 不进入链接目录
            rel = p.relative_to(root).as_posix()
            if rel in exclude:
                continue
            if len(files) >= FINGERPRINT_MAX_FILES:
                return {"status": "over_budget"}
            try:
                mtime = p.lstat().st_mtime_ns
            except OSError:
                mtime = None
            files[rel] = ["link", _readlink(p), mtime]
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if rel in exclude:
                continue
            if len(files) >= FINGERPRINT_MAX_FILES:
                return {"status": "over_budget"}
            try:
                st = p.lstat()
                digest = None
                is_link = p.is_symlink()
                if not is_link and st.st_size <= FINGERPRINT_HASH_MAX_BYTES:
                    digest = _sha256(p.read_bytes())
            except OSError:
                files[rel] = [None, None, None]  # 读不了也是一个可比较的事实：前后都读不了才算没变
                continue
            entry = [st.st_size, st.st_mtime_ns, digest]
            if is_link:
                entry.append(_readlink(p))
            files[rel] = entry
    return {"status": "ok", "files": files}


def fingerprint_changes(before: dict, after: dict) -> list[str] | None:
    """两次指纹之间变了的相对路径（新增 / 删除 / 内容或大小或 mtime 变化），按名排序。任一侧超限回 None（判不了）。"""
    if before.get("status") != "ok" or after.get("status") != "ok":
        return None
    a, b = before["files"], after["files"]
    return sorted(k for k in a.keys() | b.keys() if a.get(k) != b.get(k))


def _tree(root: Path) -> dict[str, str] | None:
    """候选目录的 `{相对路径: sha256}`；文件数超限回 None。"""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if len(out) >= CANDIDATE_MAX_FILES:
                return None
            try:
                out[p.relative_to(root).as_posix()] = (
                    "link" if p.is_symlink() else _sha256(p.read_bytes())
                )
            except OSError:
                out[p.relative_to(root).as_posix()] = "unreadable"
    return out


# --------------------------------------------------------------------------- 候选工作区
@dataclass
class Candidate:
    """一次改图任务的候选：目录、目标脚本在项目里和候选里的位置、起跑时的基线。"""

    sid: str
    dir: Path
    project_root: Path
    script_rel: str  # 相对项目根（登记路径，可带子目录）
    script_path: Path  # 项目里的那一份
    base_bytes: bytes
    base_sha256: str
    base_mode: int
    tree_before: dict[str, str]
    project_before: dict
    stems_before: list[str] | None
    #: 执行器填：候选里改后的字节、提交与否、刷新结局
    new_bytes: bytes | None = None
    committed: bool = False
    refresh: dict | None = None
    extras: dict = field(default_factory=dict)

    @property
    def target(self) -> Path:
        return self.dir / self.target_name

    @property
    def target_name(self) -> str:
        return Path(self.script_rel).name


def _static_stems(path: Path, root: Path) -> list[str] | None:
    """宿主解析器静态读出的输出名集合（排序）；不是绘图脚本 / 解析不了 / 读不出名字 → None。不起解释器、不跑脚本。"""
    try:
        res = discover.inspect_script(path, root, glob_disk=False)
    except Exception:  # noqa: BLE001 —— 静态分析器的任何意外都只意味着「这项量不了」
        return None
    info = res.get("info")
    stems = info.get("stems") if isinstance(info, dict) else None
    return sorted(str(s) for s in stems) if isinstance(stems, list) and stems else None


def create_candidate(sid: str, script_path: Path, project_root: Path, script_rel: str) -> Candidate:
    """建候选目录并复制目标脚本（字节复制，不是链接）；同时拍原项目指纹与静态输出名基线。失败时不留半个目录。"""
    project_root = Path(project_root)
    cdir = CANDIDATE_DIR / sid
    try:
        cdir.mkdir(parents=True, exist_ok=False)
        base = script_path.read_bytes()
        # 候选副本（不是用户脚本：用户脚本只经 write_script 写）
        copy_path = cdir / Path(script_rel).name
        copy_path.write_bytes(base)
        style = project_root / SHARED_STYLE
        if Path(script_rel).name != SHARED_STYLE and style.is_file() and not style.is_symlink():
            (cdir / SHARED_STYLE).write_bytes(style.read_bytes())
        tree = _tree(cdir) or {}
        cand = Candidate(
            sid=sid,
            dir=cdir,
            project_root=project_root,
            script_rel=script_rel,
            script_path=script_path,
            base_bytes=base,
            base_sha256=_sha256(base),
            base_mode=script_path.stat().st_mode & 0o7777,
            tree_before=tree,
            project_before=project_fingerprint(project_root, exclude=_own_files(script_rel)),
            stems_before=_static_stems(script_path, project_root),
        )
    except BaseException:
        remove_candidate(cdir)
        raise
    return cand


def _own_files(script_rel: str) -> frozenset[str]:
    """指纹里单独判的文件：目标脚本（`project.target` 那一项专门看它）与 Tavotto 自己写的注册表。"""
    return frozenset(
        {Path(script_rel).as_posix(), registry.REGISTRY_NAME, registry.LEGACY_REGISTRY_NAME}
    )


def remove_candidate(cdir: Path) -> None:
    """删候选目录；只删 `CANDIDATE_DIR` 之内的（防一个算错的路径把别处删掉）。"""
    try:
        real = Path(os.path.realpath(cdir))
        if real.parent != Path(os.path.realpath(CANDIDATE_DIR)):
            return
        shutil.rmtree(real, ignore_errors=True)
    except OSError:
        pass


def prompt_note(cand: Candidate) -> str:
    """附在改图提示词末尾的一段：说明工作目录是副本、原项目只读（文字在 Prompt Pack 里）。"""
    return ai_prompt_pack.candidate_note(ai_prompt_pack.DEFAULT, cand.project_root)


# --------------------------------------------------------------------------- 适配器
def _render(
    st: str, verdict: str, reason: str | None, *, cid="render.figure", not_covered=(), **observed
):
    return CheckResult(
        id=cid,
        layer="L2",
        stage=st,
        role="integrity" if cid == "render.figure" else "preserve",
        verdict=verdict,
        reason_code=reason,
        method="safe worker manifest",
        observed=observed,
        not_covered=tuple(not_covered),
    )


def _scope_for(stem: str, target: dict) -> str:
    """这个 stem 的目标范围：目标 stem 用前端给的 scope（缺省 figure）；别的 stem 里没有目标，除非整张图都是目标。"""
    scope = (
        target.get("scope") if target.get("scope") in ("element", "axes", "figure") else "figure"
    )
    if scope != "figure" and target.get("stem") not in (None, "", stem):
        return "none"
    return scope


def _check(cid: str, stage: str, role: str, verdict: str, reason: str | None = None, **observed):
    return CheckResult(
        id=cid,
        layer="L1",
        stage=stage,
        role=role,
        verdict=verdict,
        reason_code=reason,
        observed=observed,
        kind="persistence" if cid.startswith("project.") else "figure",
    )


class FigureEditAdapter:
    """`figure_edit`：自然语言改一份已登记的绘图脚本（路径 B，`02-architecture.md` §三）。"""

    task_type = TASK_TYPE

    def validate(self, envelope) -> list[str]:
        problems = []
        if envelope.executor == ai_tasks.DETERMINISTIC:
            problems.append("executor_must_be_agent")
        if len(envelope.allowed_writes) != 1:
            problems.append("exactly_one_script")
        return problems

    def plan(self, envelope):
        return ai_tasks.Plan(steps=("execute",))

    def verify(self, stage: str, run, cand: Candidate) -> list[CheckResult]:
        return self._precommit(cand) if stage == "precommit" else self._postcommit(run, cand)

    def _precommit(self, cand: Candidate) -> list[CheckResult]:
        st = "precommit"
        out: list[CheckResult] = []
        tree = _tree(cand.dir)
        if tree is None:
            out.append(_check("candidate.tree", st, "integrity", UNKNOWN, "candidate_over_budget"))
        else:
            others = sorted(
                k
                for k in cand.tree_before.keys() | tree.keys()
                if k != cand.target_name
                and k in cand.tree_before  # 新建的辅助文件不会被提交，不算改动
                and cand.tree_before.get(k) != tree.get(k)
            )
            out.append(
                _check(
                    "candidate.tree", st, "integrity", FAILED, "other_file_changed", files=others
                )
                if others
                else _check("candidate.tree", st, "integrity", VERIFIED)
            )
        try:
            cand.new_bytes = cand.target.read_bytes()
        except OSError:
            cand.new_bytes = None
        if cand.new_bytes is None:
            out.append(_check("candidate.target", st, "integrity", FAILED, "target_missing"))
            # 目标丢了也得先查原项目：否则「删目标 + 动原项目」会被当成可修而进第二轮
            out += self._project_untouched(cand, st)
            return out
        unchanged = cand.new_bytes == cand.base_bytes
        out.append(
            _check(
                "candidate.target",
                st,
                "integrity",
                NOT_APPLICABLE if unchanged else VERIFIED,
                "no_change" if unchanged else None,
            )
        )
        if not unchanged:
            text = cand.new_bytes.decode("utf-8", errors="replace")
            _tree_, problem = discover.parse_source(text)
            out.append(
                _check(
                    "candidate.syntax",
                    st,
                    "integrity",
                    FAILED,
                    "syntax_error",
                    lineno=problem.get("lineno"),
                    detail=problem.get("detail"),
                )
                if problem is not None
                else _check("candidate.syntax", st, "integrity", VERIFIED)
            )
            if cand.stems_before is None:
                out.append(
                    _check("candidate.stems", st, "preserve", NOT_APPLICABLE, "stems_not_static")
                )
            elif problem is not None:
                # 解析不了就读不出静态 stem：那是语法错误的后果，不是第二个失败（修正指令里不该多一条「图名变了」）
                out.append(
                    _check("candidate.stems", st, "preserve", NOT_APPLICABLE, "syntax_error")
                )
            else:
                after = _static_stems(cand.target, cand.dir)
                out.append(
                    _check("candidate.stems", st, "preserve", VERIFIED, stems=after)
                    if after == cand.stems_before
                    else _check(
                        "candidate.stems",
                        st,
                        "preserve",
                        FAILED,
                        "stems_changed",
                        before=cand.stems_before,
                        after=after,
                    )
                )
        out += self._project_untouched(cand, st)
        return out

    def _project_untouched(self, cand: Candidate, st: str) -> list[CheckResult]:
        out = []
        try:
            now = cand.script_path.read_bytes()
        except OSError:
            now = None
        out.append(
            _check("project.target", st, "integrity", VERIFIED)
            if now == cand.base_bytes
            else _check("project.target", st, "integrity", FAILED, "target_changed_in_project")
        )
        changed = fingerprint_changes(
            cand.project_before,
            project_fingerprint(cand.project_root, exclude=_own_files(cand.script_rel)),
        )
        if changed is None:
            out.append(_check("project.untouched", st, "integrity", UNKNOWN, "project_over_budget"))
        elif changed:
            out.append(
                _check(
                    "project.untouched",
                    st,
                    "integrity",
                    FAILED,
                    "project_files_changed",
                    files=changed[:50],
                    total=len(changed),
                )
            )
        else:
            out.append(_check("project.untouched", st, "integrity", VERIFIED))
        return out

    def _postcommit(self, run, cand: Candidate) -> list[CheckResult]:
        st = "postcommit"
        if not cand.committed:
            return [_check("commit.refresh", st, "integrity", NOT_APPLICABLE, "no_change")]
        status = (cand.refresh or {}).get("status")
        out = [
            _check("commit.refresh", st, "integrity", VERIFIED)
            if status == "ok"
            else _check(
                "commit.refresh", st, "integrity", UNKNOWN, f"refresh_{status or 'missing'}"
            )
        ]
        return out + self._render_checks(run, cand, st)

    def _render_checks(self, run, cand: Candidate, st: str) -> list[CheckResult]:
        """渲染层（L2）：改后 manifest 与改前比数据指纹。两份 manifest 都由调用方（app）注入的钩子取：

        * `before`：提交写入之前从**已经 build 过**的那个 worker 读（不执行任何东西）；没有 = 这次会话里没渲染过这张图，
          没有基线 → unknown（`no_baseline`），**也不去渲染改后**——不为了验证多跑一次用户脚本（R-06）；
        * `retire`（可选）：写入之后、刷新之前同步退役该脚本的全部旧会话；没确认（`retired` 不是 True）→ unknown
          （`session_not_retired`），**不调 after**；
        * `after(expected_sha256)`：`expected` 是刚提交字节的 sha256；钩子须核会话**实际执行**的源码 sha256 与它一致，
          不一致抛 `StaleSession` → unknown（`stale_session`）。正确性靠这条判据，不靠 `retire` 时序；
          提交并刷新之后取同一个 safe 会话（同一份运行配置）并 build——就是前端随后本来要做的那次重渲染，前端
          来时会话已经 built，不再执行第二次。渲染失败（改前同一会话能渲染、改后不能）→ failed。

        比两样：数据（`figureverify.data_unchanged`）与「目标范围以外有没有改动」（`changes_outside_target`，范围取自桥放进
        `cand.extras["target"]` 的 `{stem, gid, scope}`；范围外有改动记 unknown、结局最高 partial）。`before` 钩子给的是**无
        override** 的重放基准，与改后同口径；坐标范围 / 刻度 / 版式的目标判定与新出现的版面问题仍不比（列进 not_covered）。"""
        not_covered = ("坐标范围与刻度", "新出现的版面问题", "用户点名的目标")
        hooks_after = cand.extras.get("after")
        if hooks_after is None:
            return [
                _render(st, UNKNOWN, "render_not_wired", not_covered=("数据指纹", *not_covered))
            ]
        before = cand.extras.get("before_manifests")
        if not before:
            return [_render(st, UNKNOWN, "no_baseline", not_covered=("数据指纹", *not_covered))]
        if cand.extras.get("retire") is not None and cand.extras.get("retired") is not True:
            # 旧会话没确认退役：workerd 按 by_hash 会把 after 去重回仍已 built 的旧会话，比出来的是两份改前 manifest
            return [
                _render(st, UNKNOWN, "session_not_retired", not_covered=("数据指纹", *not_covered))
            ]
        run.charge("renders")
        try:
            after = hooks_after(_sha256(cand.new_bytes or b"")) or {}
        except StaleSession:
            return [_render(st, UNKNOWN, "stale_session", not_covered=("数据指纹", *not_covered))]
        except Exception as exc:  # noqa: BLE001 —— 渲染失败本身就是这条检查的结论
            code = getattr(exc, "code", None) or type(exc).__name__
            return [_render(st, FAILED, "render_failed", code=str(code))]
        out = [_render(st, VERIFIED, None, stems=sorted(after), not_covered=not_covered)]
        target = cand.extras.get("target") or {}
        for stem, man in sorted(before.items()):
            if man is None:  # 这个 stem 的改前重放失败：显式缺基线，不能被静默略过
                out.append(
                    _render(
                        st, UNKNOWN, "no_baseline", cid=f"render.{stem}", not_covered=("数据指纹",)
                    )
                )
                continue
            if stem not in after:
                out.append(_render(st, FAILED, "stem_not_rendered", cid=f"render.{stem}"))
                continue
            out += [
                dataclasses.replace(c, id=f"{stem}.{c.id}")
                for c in figureverify.data_unchanged(after[stem], man, stage=st)
            ] + [
                dataclasses.replace(c, id=f"{stem}.{c.id}")
                for c in figureverify.changes_outside_target(
                    after[stem],
                    man,
                    scope=_scope_for(stem, target),
                    gid=target.get("gid"),
                    stage=st,
                )
            ]
        if len(out) == 1:
            out.append(_render(st, NOT_APPLICABLE, "no_data_elements", cid="data"))
        return out

    def correction(self, run, failed: list[CheckResult]) -> dict | None:
        """提交前没过、而且失败的是**Agent 在副本里能自己改回来**的那几类，给一段修正指令；否则 None。

        原项目被动了（`project.*`）不修：那是越过了只读边界，再让同一个 Agent 跑一次只会扩大面；只认闭集里的原因，
        新加的检查默认不可修。指令只写检查自己的观察（行号、stem、副本里的文件名），不回显项目路径以外的内容。"""
        pack = ai_prompt_pack.DEFAULT
        lines = []
        for c in failed:
            if (c.id, c.reason_code) not in ai_prompt_pack.CORRECTABLE:
                return None
            lines.append(ai_prompt_pack.fix_text(pack, (c.id, c.reason_code), c.observed))
        if not lines:
            return None
        return {"prompt": ai_prompt_pack.correction_prompt(pack, lines)}

    def explain(self, outcome: str, unmet: list[dict]) -> dict:
        return {
            "message_key": f"task.{TASK_TYPE}.{outcome}",
            "params": {"unmet": [u["constraint_id"] for u in unmet]},
        }


# --------------------------------------------------------------------------- 执行器（AI 桥的那一次 CLI 运行）
class BridgeExecutor:
    """把 AI 桥已经 spawn 的那次 CLI 运行接进编排：`execute` = 读完输出并等进程退出（桥传进来的 `wait`），
    `commit` = 持锁核基线后原子替换项目里的脚本、再统一刷新（桥传进来的 `refresh`）。"""

    kind = "agent"
    writes_user_files = True

    def __init__(self, executor_id: str, cand: Candidate, *, wait, refresh, respawn=None):
        self.executor_id = executor_id
        self.cand = cand
        self._wait = wait
        self._refresh = refresh
        self._respawn = respawn
        if respawn is None:
            self.correct = None  # 桥没给再跑一次的入口：编排器据此不修正

    def execute(self, run) -> Candidate:
        self._wait()  # 抛 TaskError 表示运行本身没成（超时 / 非零退出），候选作废
        return self.cand

    def correct(self, run, cand: Candidate, feedback: dict) -> Candidate:
        """修正 = 在同一个候选目录里按修正指令再跑一次 CLI（桥传进来的 `respawn`），然后照常等它退出。"""
        self._respawn(feedback["prompt"])
        self._wait()
        return cand

    def commit(self, run, cand: Candidate) -> None:
        if cand.new_bytes is None or cand.new_bytes == cand.base_bytes:
            return  # 没改动：不写文件、不刷新（与旧路径 changed=False 相同）
        with scriptlock.script_guard(cand.script_path):
            try:
                now = cand.script_path.read_bytes()
            except OSError:
                now = None
            if now != cand.base_bytes:
                raise TaskError("script_changed_since_start", cand.script_rel)
            # 改前的渲染事实只能在写之前拿：写完刷新就会作废那个 worker
            cand.extras["before_manifests"] = _call_hook(cand.extras.get("before"))
            scriptlock.write_script(cand.script_path, cand.new_bytes, mode=cand.base_mode)
            cand.committed = True
        # 退役只是让「after 去重回旧会话」的概率变小（尽力而为）；正确性由 after 钩子核 build 实际执行的 source_sha256 保证。
        # 退役旧会话必须在统一刷新之前、锁之外（force 关停可能要等 workerd）：刷新里那次普通 invalidate 才会找不到会话，
        # 前端收到 panel.file_changed 与 after 钩子拿到的才都是新脚本 spawn 的会话。没注入钩子（纯引擎调用方）不记 retired。
        retire = cand.extras.get("retire")
        if retire is not None:
            try:
                retire()
                cand.extras["retired"] = True
            except Exception:  # noqa: BLE001 —— 确认不了就如实记 False，提交本身已落盘、不因旁路失败
                cand.extras["retired"] = False
        cand.refresh = self._refresh()

    def discard(self, run, cand: Candidate) -> None:
        remove_candidate(cand.dir)


def _call_hook(hook):
    """调 app 注入的取事实钩子；没有 / 抛异常 → None（这项量不了，不让提交因为旁路失败）。"""
    if hook is None:
        return None
    try:
        return hook()
    except Exception:  # noqa: BLE001
        return None


def envelope_for(
    *, agent: str, project_key: str, script_rel: str, prompt: str, rid: str | None, base_sha256: str
) -> ai_tasks.TaskEnvelope:
    return ai_tasks.TaskEnvelope.from_dict(
        {
            "schema_version": ai_tasks.SCHEMA_VERSION,
            "task_type": TASK_TYPE,
            "project_ref": {"project_key": project_key},
            "client_request_id": rid,
            "user_intent": prompt,
            "input_refs": [{"kind": "script", "path_rel": script_rel, "sha256": base_sha256}],
            "output_contract": {"allowed_writes": [script_rel]},
            "execution_policy": {"executor": agent},
            # normal 档（`03-contracts.md` §五）：Agent ≤ 1 + 修正 ≤ 1
            "budget": {"max_agent_runs": 2, "max_corrections": 1},
        }
    )


ADAPTER = FigureEditAdapter()


def ensure_registered() -> None:
    """开关打开的那次运行才把适配器登记进 `ai_tasks.ADAPTERS`（不在 import 时登记：开关关着时注册表保持为空）。"""
    if ai_tasks.ADAPTERS.get(TASK_TYPE) is not ADAPTER:
        ai_tasks.register(ADAPTER)
