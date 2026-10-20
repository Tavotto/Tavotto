"""TAF 数据出图任务（`data_to_figure`，ADR 0138 Phase 5b）：把 `ai_data_figure` 的确定性内核接进 Task Core。纯标准库，Flask 父进程 import。

流程（`02-architecture.md` §四）：选项目内的 CSV → 受控摘要 → 确定性列映射（推不出科学含义 → `needs_input`，什么都不写）→
模板生成脚本 → 提交前 L1（解析、静态输出名、目标不存在、数据没变）→ **只新建**脚本（`scriptlock.create_script`，已存在就失败）
并登记 → 统一刷新 → 提交后用 safe worker 渲染一次（就是前端随后本来要做的那次），manifest 的数据指纹与父进程从 CSV 算出的
期望值逐字节相等才是 verified。

这里**不调模型**：执行器是确定性的（`executor = deterministic`），被执行的代码完全出自模板（v1 硬边界）。Task Core 的
`run_task` 只为「确定性执行器只新建文件」放行写入（`ai_tasks.run_task`）。

结局：数据画对且每行都画了 → completed；有行不是数（`<0.1`、`n/a`）没画 → partial（目标「每行都画了」failed，逐行报告）；
数据指纹对不上 → failed；渲染量不到（会话执行的不是刚写的那一版、没接渲染）→ 最高 partial。缺失值（空 / NaN）是数据的
一部分：折线在那里断开，不算没画。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    ai_data_figure,
    ai_figure_edit,
    ai_tasks,
    discover,
    figureverify,
    inputremap,
    projectenv,
    registry,
    scriptlock,
)
from .ai_tasks import FAILED, UNKNOWN, VERIFIED, CheckResult, TaskError

TASK_TYPE = "data_to_figure"
#: 同一个 stem 已被占用时最多试几个后缀（`_2` … `_N`）
MAX_SUFFIX = 50


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _registered_stems(root: Path) -> set[str]:
    """注册表文件里此刻登记的全部 stem（读不到 = 空）。要与随后的写成对时由调用方持项目锁。"""
    path = registry.existing_registry_path(root)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError):
        return set()
    scripts = cfg.get("scripts") if isinstance(cfg, dict) else None
    if not isinstance(scripts, dict):
        return set()
    return {str(s) for e in scripts.values() if isinstance(e, dict) for s in e.get("stems") or []}


def _stem_free(directory: Path, stem: str, taken: set[str]) -> bool:
    """这个 stem 能不能用：没被登记、同目录没有任何 `<stem>.*`（含 `.py`）产物（不让新图冒认旧文件）。

    一律按 casefold 比较（不分平台）：macOS / Windows 默认大小写不敏感，`Foo_figure.pdf` 与 `foo_figure` 是同一个磁盘名；
    大小写敏感的系统上多避开一个名字无害。
    """
    key = stem.casefold()
    if key in {t.casefold() for t in taken}:
        return False
    try:
        return not any(p.stem.casefold() == key for p in directory.iterdir())
    except OSError:
        return False


@dataclass
class Placement:
    """新脚本放哪：与 CSV 同目录，`<stem>.py`；`script_rel` 是项目相对 POSIX 路径（注册表键）。"""

    data_path: Path
    data_rel: str
    script_path: Path
    script_rel: str
    stem: str


def place(root: Path, data_rel: str) -> Placement:
    """把用户给的数据路径钉在项目内（`contained_path`）并选一个空闲的 stem。"""
    real = projectenv.contained_path(root, data_rel)
    if real is None:
        raise ai_data_figure.DataSourceError("data_path_outside_project")
    data_path = Path(real)
    if not data_path.is_file():
        raise ai_data_figure.DataSourceError("data_not_found")
    if data_path.suffix.lower() != ".csv":
        raise ai_data_figure.DataSourceError("data_not_csv")
    root_real = Path(os.path.realpath(root))
    base = ai_data_figure.stem_for(data_path.name)
    taken = _registered_stems(root)
    for n in range(1, MAX_SUFFIX + 1):
        stem = base if n == 1 else f"{base}_{n}"
        if _stem_free(data_path.parent, stem, taken):
            script_path = data_path.parent / f"{stem}.py"
            return Placement(
                data_path=data_path,
                data_rel=data_path.relative_to(root_real).as_posix(),
                script_path=script_path,
                script_rel=script_path.relative_to(root_real).as_posix(),
                stem=stem,
            )
    raise ai_data_figure.DataSourceError("figure_name_exhausted")


@dataclass
class Prepared:
    summary: ai_data_figure.DataSummary
    plan: ai_data_figure.Mapping | ai_data_figure.NeedsInput | ai_data_figure.Unsupported
    placement: Placement


def prepare(root: Path, data_rel: str, instruction: str) -> Prepared:
    placement = place(root, data_rel)
    summary = ai_data_figure.inspect_data_source(placement.data_path)
    return Prepared(summary, ai_data_figure.plan_mapping(summary, instruction), placement)


# --------------------------------------------------------------------------- 候选
@dataclass
class Candidate:
    prepared: Prepared
    mapping: ai_data_figure.Mapping
    text: str
    dir: Path
    expected: list[dict]
    plotted: ai_data_figure.Plotted
    committed: bool = False
    refresh: dict | None = None
    extras: dict = field(default_factory=dict)

    @property
    def data(self) -> bytes:
        return self.text.encode("utf-8")


def _check(cid: str, stage: str, role: str, verdict: str, reason: str | None = None, **observed):
    return CheckResult(
        id=cid,
        layer="L1",
        stage=stage,
        role=role,
        verdict=verdict,
        reason_code=reason,
        observed=observed,
        kind="persistence" if cid.startswith("commit.") else "figure",
    )


def _render(st: str, verdict: str, reason: str | None = None, **observed):
    return CheckResult(
        id="render.figure",
        layer="L2",
        stage=st,
        role="integrity",
        verdict=verdict,
        reason_code=reason,
        method="safe worker manifest",
        observed=observed,
    )


class DataToFigureAdapter:
    task_type = TASK_TYPE

    def validate(self, envelope) -> list[str]:
        problems = []
        if envelope.executor != ai_tasks.DETERMINISTIC:
            problems.append("executor_must_be_deterministic")
        if not envelope.new_figures or len(envelope.allowed_writes) != 1:
            problems.append("exactly_one_new_script")
        if [r.get("kind") for r in envelope.input_refs] != ["data_file"]:
            problems.append("exactly_one_data_file")
        return problems

    def plan(self, envelope):
        """映射在这里重新算一次（确定性、纯读）：推不出科学含义 → `needs_input`（不执行、不写）；画不了 → TaskError。"""
        ref = envelope.input_refs[0]
        prepared = prepare(Path(envelope.project_key), ref["path_rel"], envelope.user_intent)
        if prepared.summary.sha256 != ref.get("sha256"):
            raise TaskError("data_changed", ref["path_rel"])
        p = prepared.plan
        if isinstance(p, ai_data_figure.NeedsInput):
            return ai_tasks.Plan(needs_input=p.reason)
        if isinstance(p, ai_data_figure.Unsupported):
            raise TaskError(p.reason)
        return ai_tasks.Plan(steps=("execute",))

    def verify(self, stage: str, run, cand: Candidate) -> list[CheckResult]:
        return self._precommit(cand) if stage == "precommit" else self._postcommit(run, cand)

    def _precommit(self, cand: Candidate) -> list[CheckResult]:
        st = "precommit"
        pl = cand.prepared.placement
        out = []
        tree, problem = discover.parse_source(cand.text)
        out.append(
            _check("candidate.syntax", st, "integrity", VERIFIED)
            if problem is None
            else _check("candidate.syntax", st, "integrity", FAILED, "syntax_error")
        )
        probe = cand.dir / pl.script_path.name
        probe.write_text(cand.text, encoding="utf-8")  # 候选副本（在 data_dir 里），不是用户脚本
        stems = ai_figure_edit._static_stems(probe, cand.dir)
        out.append(
            _check("candidate.stems", st, "integrity", VERIFIED, stems=stems)
            if stems == [pl.stem]
            else _check("candidate.stems", st, "integrity", FAILED, "stems_not_static", stems=stems)
        )
        out.append(
            _check("project.target", st, "integrity", VERIFIED)
            if not pl.script_path.exists()
            else _check("project.target", st, "integrity", FAILED, "target_exists")
        )
        try:
            now = _sha256(pl.data_path.read_bytes())
        except OSError:
            now = None
        out.append(
            _check("project.data", st, "integrity", VERIFIED)
            if now == cand.prepared.summary.sha256
            else _check("project.data", st, "integrity", FAILED, "data_changed")
        )
        return out

    def _postcommit(self, run, cand: Candidate) -> list[CheckResult]:
        st = "postcommit"
        pl = cand.prepared.placement
        out = [
            _check("commit.registered", st, "integrity", VERIFIED)
            if cand.extras.get("registered") == [pl.stem]
            else _check(
                "commit.registered",
                st,
                "integrity",
                FAILED,
                "not_registered",
                stems=cand.extras.get("registered"),
            )
        ]
        status = (cand.refresh or {}).get("status")
        out.append(
            _check("commit.refresh", st, "integrity", VERIFIED)
            if status == "ok"
            else _check(
                "commit.refresh", st, "integrity", UNKNOWN, f"refresh_{status or 'missing'}"
            )
        )
        missing = cand.plotted.missing
        not_plotted = list(cand.plotted.not_plotted)
        out.append(
            CheckResult(
                id="goal.rows_plotted",
                layer="L2",
                stage=st,
                role="goal",
                verdict=FAILED if not_plotted else VERIFIED,
                reason_code="rows_not_plotted" if not_plotted else None,
                observed={"rows": not_plotted[:50], "total": len(not_plotted), "missing": missing},
            )
        )
        out.append(
            CheckResult(
                id="goal.figure_created",
                layer="L1",
                stage=st,
                role="goal",
                verdict=VERIFIED,
                observed={
                    "script": pl.script_rel,
                    "stem": pl.stem,
                    "mapping": cand.mapping.to_dict(),
                },
            )
        )
        render = cand.extras.get("render")
        if render is None:
            return out + [_render(st, UNKNOWN, "render_not_wired")]
        run.charge("renders")
        try:
            mans = render(_sha256(cand.data)) or {}
        except ai_figure_edit.StaleSession:
            return out + [_render(st, UNKNOWN, "stale_session")]
        except Exception as exc:  # noqa: BLE001 —— 模板脚本渲染失败本身就是结论
            code = getattr(exc, "code", None) or type(exc).__name__
            return out + [_render(st, FAILED, "render_failed", code=str(code))]
        man = mans.get(pl.stem)
        if man is None:
            return out + [_render(st, FAILED, "stem_not_rendered", stems=sorted(mans))]
        out.append(_render(st, VERIFIED, None, stem=pl.stem))
        return out + figureverify.data_matches_expected(man, cand.expected, stage=st)

    def explain(self, outcome: str, unmet: list[dict]) -> dict:
        return {
            "message_key": f"task.{TASK_TYPE}.{outcome}",
            "params": {"unmet": [u["constraint_id"] for u in unmet]},
        }


# --------------------------------------------------------------------------- 执行器
class TemplateExecutor:
    """确定性执行器：`execute` = 由模板生成脚本文本（不跑、不写项目）；`commit` = 项目锁内核 stem 仍空闲 → **新建**脚本 →
    登记（`discover.register`）→ 锁外统一刷新。登记失败就删掉刚建出来、内容未变的那份文件，不留半截。"""

    executor_id = ai_tasks.DETERMINISTIC
    kind = ai_tasks.DETERMINISTIC
    writes_user_files = True
    creates_only = True

    def __init__(self, root: Path, *, refresh=None, render=None):
        self.root = Path(root)
        self._refresh = refresh
        self._render = render
        self.candidate: Candidate | None = None

    def execute(self, run) -> Candidate:
        ref = run.envelope.input_refs[0]
        prepared = prepare(self.root, ref["path_rel"], run.envelope.user_intent)
        if prepared.summary.sha256 != ref.get("sha256"):
            raise TaskError("data_changed", ref["path_rel"])
        mapping = prepared.plan
        if not isinstance(
            mapping, ai_data_figure.Mapping
        ):  # plan 已经挡掉；两次之间数据变了才会到这里
            raise TaskError("mapping_changed")
        pl = prepared.placement
        if pl.script_rel != run.envelope.allowed_writes[0]:
            raise TaskError("placement_changed", pl.script_rel)
        data_rel = os.path.relpath(pl.data_path, pl.script_path.parent).replace(os.sep, "/")
        text = ai_data_figure.render_script(
            prepared.summary, mapping, data_rel=data_rel, stem=pl.stem
        )
        plotted = ai_data_figure.plotted_data(prepared.summary, mapping)
        cdir = ai_figure_edit.CANDIDATE_DIR / run.task_id
        cdir.mkdir(parents=True, exist_ok=False)
        self.candidate = Candidate(
            prepared=prepared,
            mapping=mapping,
            text=text,
            dir=cdir,
            expected=ai_data_figure.expected_fingerprints(plotted),
            plotted=plotted,
            extras={"render": self._render} if self._render is not None else {},
        )
        return self.candidate

    def commit(self, run, cand: Candidate) -> None:
        pl = cand.prepared.placement
        with inputremap.project_mutex(self.root):
            # place() 之后同目录可能又出现了未登记的 `<stem>.*`：锁内重跑完整的空闲检查
            if not _stem_free(pl.script_path.parent, pl.stem, _registered_stems(self.root)):
                raise TaskError("stem_taken", pl.stem)
            with scriptlock.script_guard(pl.script_path):
                try:
                    scriptlock.create_script(pl.script_path, cand.data)
                except FileExistsError:
                    raise TaskError("target_exists", pl.script_rel) from None
                cand.committed = True
                try:
                    discover.register(
                        self.root, pl.script_rel, [pl.stem], entry="main", cost="light"
                    )
                except Exception as exc:
                    # 登记不上：这份文件是刚由我们新建的，内容没被别人动过才删（动过就留着，宁可多一个文件也不删用户的）
                    try:
                        if pl.script_path.read_bytes() == cand.data:
                            pl.script_path.unlink()
                            cand.committed = False
                    except OSError:
                        pass
                    raise TaskError("register_failed", type(exc).__name__) from None
            cand.extras["registered"] = discover.registered_stems(self.root, pl.script_rel)
        if self._refresh is not None:
            cand.refresh = self._refresh(pl.script_rel)

    def discard(self, run, cand: Candidate) -> None:
        ai_figure_edit.remove_candidate(cand.dir)


def envelope_for(
    *, project_key: str, prepared: Prepared, instruction: str, rid: str | None
) -> ai_tasks.TaskEnvelope:
    pl = prepared.placement
    return ai_tasks.TaskEnvelope.from_dict(
        {
            "schema_version": ai_tasks.SCHEMA_VERSION,
            "task_type": TASK_TYPE,
            "project_ref": {"project_key": project_key},
            "client_request_id": rid,
            "user_intent": instruction,
            "input_refs": [
                {
                    "kind": "data_file",
                    "path_rel": pl.data_rel,
                    "sha256": prepared.summary.sha256,
                    "size": prepared.summary.size,
                }
            ],
            "output_contract": {"allowed_writes": [pl.script_rel], "new_figures": True},
            "execution_policy": {"executor": ai_tasks.DETERMINISTIC},
            "budget": {"max_agent_runs": 0, "max_renders": 1},
        }
    )


ADAPTER = DataToFigureAdapter()


def ensure_registered() -> None:
    """开关打开的那次请求才登记适配器（与 figure_edit 同一纪律：开关关着时生产注册表为空）。"""
    if ai_tasks.ADAPTERS.get(TASK_TYPE) is not ADAPTER:
        ai_tasks.register(ADAPTER)
