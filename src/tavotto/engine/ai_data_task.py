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

**v2（ADR 0178，文件在本文件下半）**：同一套 Task Core，换一条落盘形态——用户在备选里选中一个（`ai_data_propose` 的候选会话）后，只新建
`<数据文件夹>/tavotto_figures/<stem>.py`（短脚本）与 `tavotto_plot_<8 位>.py`（内容寻址的公共模块：不存在就建、字节相同就复用、
字节不同按损坏报错且**什么都不写**），都只经 `scriptlock.create_script`。任务类型 `data_to_figure_v2`；v1 的 CSV 同目录路径与用例逐字不变。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import (
    ai_data_figure,
    ai_data_propose,
    ai_data_recipe,
    ai_data_template,
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


# =========================================================================== v2：从备选落盘（ADR 0178）
V2_TASK_TYPE = "data_to_figure_v2"
FIGURES_DIR = "tavotto_figures"


def _stem_free_v2(directory: Path, stem: str, taken: set[str]) -> bool:
    """同 `_stem_free`，但目录还不存在（`tavotto_figures/` 还没建）算空闲；一律 casefold 比较。"""
    key = stem.casefold()
    if key in {t.casefold() for t in taken}:
        return False
    try:
        return not any(p.stem.casefold() == key for p in directory.iterdir())
    except FileNotFoundError:
        return True
    except OSError:
        return False


@dataclass
class PlacementV2:
    """v2 的落点：脚本与公共模块都在 `<数据文件夹>/tavotto_figures/`；`*_rel` 是项目相对 POSIX 路径（注册表键 / 契约）。"""

    folder: Path
    data_path: Path
    data_rel: str
    figures_dir: Path
    script_path: Path
    script_rel: str
    stem: str
    module_name: str
    module_path: Path
    module_rel: str


def place_v2(root: Path, folder_rel: str, table_rel: str, module_name: str) -> PlacementV2:
    """把数据文件夹与数据文件钉在项目内（`contained_path`，realpath 之后判；不跟随指到项目外的链接）并选一个空闲的 stem。
    stem 规则沿用 v1（避开已登记的、同名脚本、同名产物，casefold 比较），只是在 `tavotto_figures/` 里找。"""
    try:
        folder_norm = ai_data_propose.normalize_rel(folder_rel)
        table_norm = ai_data_propose.normalize_rel(table_rel)
    except ValueError:
        raise ai_data_figure.DataSourceError("data_path_outside_project") from None
    real = projectenv.contained_path(root, folder_norm)
    if real is None:
        raise ai_data_figure.DataSourceError("data_path_outside_project")
    folder = Path(real)
    if not folder.is_dir():
        raise ai_data_figure.DataSourceError("data_not_found")
    root_real = Path(os.path.realpath(root))
    data_real = projectenv.contained_path(root, (folder / PurePosixPath(table_norm)).as_posix())
    if data_real is None:
        raise ai_data_figure.DataSourceError("data_path_outside_project")
    data_path = Path(data_real)
    if not data_path.is_file():
        raise ai_data_figure.DataSourceError("data_not_found")
    figures_dir = folder / FIGURES_DIR
    base = ai_data_figure.stem_for(Path(table_norm).name)
    taken = _registered_stems(root)
    for n in range(1, MAX_SUFFIX + 1):
        stem = base if n == 1 else f"{base}_{n}"
        if _stem_free_v2(figures_dir, stem, taken):
            script_path = figures_dir / f"{stem}.py"
            module_path = figures_dir / module_name
            return PlacementV2(
                folder=folder,
                data_path=data_path,
                data_rel=data_path.relative_to(root_real).as_posix(),
                figures_dir=figures_dir,
                script_path=script_path,
                script_rel=script_path.relative_to(root_real).as_posix(),
                stem=stem,
                module_name=module_name,
                module_path=module_path,
                module_rel=module_path.relative_to(root_real).as_posix(),
            )
    raise ai_data_figure.DataSourceError("figure_name_exhausted")


def common_module_state(path: Path, data: bytes) -> str:
    """公共模块此刻的状态：`absent`（要新建）/ `same`（已存在且字节相同，复用）/ `conflict`（存在但字节不同、是链接或不是普通文件：
    按损坏报错，不覆盖）。"""
    try:
        if path.is_symlink():
            return "conflict"
        if not path.exists():
            return "absent"
        if path.is_file() and path.read_bytes() == data:
            return "same"
    except OSError:
        return "conflict"
    return "conflict"


unplotted_rows = ai_data_propose.unplotted_rows


@dataclass(frozen=True)
class Selection:
    """用户选中的那个备选（来自候选会话；**配方在服务端**，前端只回传 id）。"""

    folder_rel: str
    table: str
    sheet: str | None
    data_sha256: str
    confirmed: ai_data_recipe.Confirmed
    recipe: ai_data_recipe.FigureRecipe
    title_code: str


@dataclass
class PreparedV2:
    selection: Selection
    values: ai_data_figure.DataSummary
    entry: ai_data_figure.TableEntry
    placement: PlacementV2
    script: str
    module: bytes
    expected: list[dict]
    coverage: ai_data_template.Coverage
    not_plotted: list[dict]
    cell_stats: list[dict] = field(default_factory=list)


def prepare_v2(root: Path, sel: Selection) -> PreparedV2:
    """重读数据（sha256 必须还是出备选时那一份，否则 `propose_data_changed`）→ 重新校验配方 → 选落点 → 模板出脚本与公共模块 →
    期望数据指纹与覆盖情况。纯读，不写任何东西。"""
    module_name, module_bytes = ai_data_template.runtime_module()
    pl = place_v2(root, sel.folder_rel, sel.table, module_name)
    values = ai_data_figure.inspect_data_source(pl.data_path, sel.sheet)
    if values.sha256 != sel.data_sha256:
        raise ai_data_propose.ProposeError("propose_data_changed")
    entry = ai_data_figure.TableEntry(sel.table, sel.sheet, False, values)
    profile = ai_data_figure.FolderProfile((entry,), (), False, None)
    problems = ai_data_recipe.validate_recipe(
        profile, sel.recipe, confirmed=sel.confirmed, values=values
    )
    if problems:
        raise TaskError("recipe_invalid", problems[0].code)
    rendered = ai_data_template.render_recipe_script(
        entry, sel.recipe, data_rel=ai_data_propose.script_data_rel(sel.table), stem=pl.stem
    )
    return PreparedV2(
        selection=sel,
        values=values,
        entry=entry,
        placement=pl,
        script=rendered.script,
        module=module_bytes,
        expected=ai_data_template.expected_fingerprints(values, sel.recipe),
        coverage=ai_data_template.fingerprint_coverage(sel.recipe),
        not_plotted=unplotted_rows(values, sel.recipe),
        cell_stats=ai_data_propose.cell_stats(values, sel.recipe),
    )


@dataclass
class CandidateV2:
    prepared: PreparedV2
    dir: Path
    committed: bool = False
    refresh: dict | None = None
    extras: dict = field(default_factory=dict)

    @property
    def data(self) -> bytes:
        return self.prepared.script.encode("utf-8")


def _data_check(stage: str, state: str, **observed) -> CheckResult:
    """`data.verifiable`：这张图的数据有没有任何一层能逐字节核对。没有 → unknown（绝不折算成 verified）。"""
    ok = state == "checked"
    return CheckResult(
        id="data.verifiable",
        layer="L2",
        stage=stage,
        role="preserve",
        verdict=VERIFIED if ok else UNKNOWN,
        reason_code=None if ok else "data_not_verifiable",
        method="data_fingerprint",
        observed=observed,
    )


class DataFigureV2Adapter:
    task_type = V2_TASK_TYPE

    def validate(self, envelope) -> list[str]:
        problems = []
        if envelope.executor != ai_tasks.DETERMINISTIC:
            problems.append("executor_must_be_deterministic")
        if not envelope.new_figures or not 1 <= len(envelope.allowed_writes) <= 2:
            problems.append("one_script_and_optional_module")
        if [r.get("kind") for r in envelope.input_refs] != ["data_file"]:
            problems.append("exactly_one_data_file")
        return problems

    def plan(self, envelope):
        """数据自出备选后变了就不执行（`data_changed`）；其余判定都在 `execute` 里（配方在执行器手里，不在契约里）。"""
        ref = envelope.input_refs[0]
        try:
            now = _sha256(projectenv_path(Path(envelope.project_key), ref["path_rel"]).read_bytes())
        except (OSError, ai_data_figure.DataSourceError):
            raise TaskError("data_changed", ref["path_rel"]) from None
        if now != ref.get("sha256"):
            raise TaskError("data_changed", ref["path_rel"])
        return ai_tasks.Plan(steps=("execute",))

    def verify(self, stage: str, run, cand: CandidateV2) -> list[CheckResult]:
        return self._precommit(cand) if stage == "precommit" else self._postcommit(run, cand)

    def _precommit(self, cand: CandidateV2) -> list[CheckResult]:
        st = "precommit"
        prep = cand.prepared
        pl = prep.placement
        out = []
        tree, problem = discover.parse_source(prep.script)
        out.append(
            _check("candidate.syntax", st, "integrity", VERIFIED)
            if problem is None
            else _check("candidate.syntax", st, "integrity", FAILED, "syntax_error")
        )
        probe = cand.dir / pl.script_path.name
        probe.write_text(prep.script, encoding="utf-8")  # 候选副本（在 data_dir 里），不是用户脚本
        stems = ai_figure_edit._static_stems(probe, cand.dir)
        out.append(
            _check("candidate.stems", st, "integrity", VERIFIED, stems=stems)
            if stems == [pl.stem]
            else _check("candidate.stems", st, "integrity", FAILED, "stems_not_static", stems=stems)
        )
        out.append(
            _check("project.target", st, "integrity", VERIFIED)
            if not pl.script_path.exists() and not pl.script_path.is_symlink()
            else _check("project.target", st, "integrity", FAILED, "target_exists")
        )
        try:
            now = _sha256(pl.data_path.read_bytes())
        except OSError:
            now = None
        out.append(
            _check("project.data", st, "integrity", VERIFIED)
            if now == prep.selection.data_sha256
            else _check("project.data", st, "integrity", FAILED, "data_changed")
        )
        fdir = pl.figures_dir
        usable = not fdir.is_symlink() and (fdir.is_dir() or not os.path.lexists(fdir))
        out.append(
            _check("project.figures_dir", st, "integrity", VERIFIED)
            if usable
            else _check("project.figures_dir", st, "integrity", FAILED, "figures_dir_unusable")
        )
        state = common_module_state(pl.module_path, prep.module)
        out.append(
            _check("project.common_module", st, "integrity", VERIFIED, state=state)
            if state != "conflict"
            else _check(
                "project.common_module",
                st,
                "integrity",
                FAILED,
                "common_module_conflict",
                state=state,
            )
        )
        return out

    def _postcommit(self, run, cand: CandidateV2) -> list[CheckResult]:
        st = "postcommit"
        prep = cand.prepared
        pl = prep.placement
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
            ),
            _check(
                "commit.common_module",
                st,
                "integrity",
                VERIFIED,
                created=bool(cand.extras.get("module_created")),
            ),
        ]
        status = (cand.refresh or {}).get("status")
        out.append(
            _check("commit.refresh", st, "integrity", VERIFIED)
            if status == "ok"
            else _check(
                "commit.refresh", st, "integrity", UNKNOWN, f"refresh_{status or 'missing'}"
            )
        )
        rows = prep.not_plotted
        out.append(
            CheckResult(
                id="goal.rows_plotted",
                layer="L2",
                stage=st,
                role="goal",
                verdict=FAILED if rows else VERIFIED,
                reason_code="rows_not_plotted" if rows else None,
                observed={"rows": rows[:50], "total": len(rows)},
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
                    "family": prep.selection.recipe.family,
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
        if not prep.coverage.any_checked or not prep.expected:
            # 没有任何一层能逐字节核对（只有误差线 / 箱 / 小提琴这类）：如实写「数据没法核」，绝不写成 verified
            return out + [_data_check(st, "none", layers=prep.coverage.to_dict()["layers"])]
        return (
            out
            + [_data_check(st, "checked")]
            + figureverify.data_matches_expected(man, prep.expected, stage=st)
        )

    def explain(self, outcome: str, unmet: list[dict]) -> dict:
        return {
            "message_key": f"task.{V2_TASK_TYPE}.{outcome}",
            "params": {"unmet": [u["constraint_id"] for u in unmet]},
        }


def projectenv_path(root: Path, rel: str) -> Path:
    """契约里的项目相对路径 → 项目内的真实路径（`contained_path`）；逃出去按数据问题抛 `DataSourceError`。"""
    real = projectenv.contained_path(root, rel)
    if real is None:
        raise ai_data_figure.DataSourceError("data_path_outside_project")
    return Path(real)


class TemplateExecutorV2:
    """确定性执行器（v2）：`execute` = 重读数据、校验、模板出脚本（不跑、不写项目）；`commit` = 项目锁内按固定顺序**只新建**——
    `tavotto_figures/`（不存在就建）→ 公共模块（不存在新建 / 字节相同复用 / 不同则什么都不写）→ 脚本 → 登记 → 锁外统一刷新。
    登记失败把刚建的脚本删掉（公共模块与目录若是本次新建、内容未被动过也一并撤回；复用的不删）。

    配方来自候选会话（构造时给），**不在任务契约里**：契约只有数据文件引用与允许写入的两个路径。"""

    executor_id = ai_tasks.DETERMINISTIC
    kind = ai_tasks.DETERMINISTIC
    writes_user_files = True
    creates_only = True

    def __init__(self, root: Path, selection: Selection, *, refresh=None, render=None):
        self.root = Path(root)
        self.selection = selection
        self._refresh = refresh
        self._render = render
        self.candidate: CandidateV2 | None = None

    def execute(self, run) -> CandidateV2:
        ref = run.envelope.input_refs[0]
        prep = prepare_v2(self.root, self.selection)
        if prep.values.sha256 != ref.get("sha256"):
            raise TaskError("data_changed", ref["path_rel"])
        if prep.placement.script_rel != run.envelope.allowed_writes[0]:
            raise TaskError("placement_changed", prep.placement.script_rel)
        cdir = ai_figure_edit.CANDIDATE_DIR / run.task_id
        cdir.mkdir(parents=True, exist_ok=False)
        self.candidate = CandidateV2(
            prepared=prep,
            dir=cdir,
            extras={"render": self._render} if self._render is not None else {},
        )
        return self.candidate

    def commit(self, run, cand: CandidateV2) -> None:
        prep = cand.prepared
        pl = prep.placement
        with inputremap.project_mutex(self.root):
            if not _stem_free_v2(pl.figures_dir, pl.stem, _registered_stems(self.root)):
                raise TaskError("stem_taken", pl.stem)
            if pl.figures_dir.is_symlink():
                raise TaskError("figures_dir_unusable", pl.figures_dir.name)
            made_dir = not os.path.lexists(pl.figures_dir)
            if made_dir:
                pl.figures_dir.mkdir()
            elif not pl.figures_dir.is_dir():
                raise TaskError("figures_dir_unusable", pl.figures_dir.name)
            module_created = False
            try:
                with scriptlock.script_guard(pl.module_path):
                    state = common_module_state(pl.module_path, prep.module)
                    if state == "conflict":
                        raise TaskError("common_module_conflict", pl.module_name)
                    if state == "absent":
                        try:
                            scriptlock.create_script(pl.module_path, prep.module)
                        except FileExistsError:
                            raise TaskError("common_module_conflict", pl.module_name) from None
                        module_created = True
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
                            # 登记不上：脚本是刚由我们新建的，内容没被别人动过才删（动过就留着，宁可多一个文件也不删用户的）
                            try:
                                if pl.script_path.read_bytes() == cand.data:
                                    pl.script_path.unlink()
                                    cand.committed = False
                            except OSError:
                                pass
                            raise TaskError("register_failed", type(exc).__name__) from None
            except BaseException:
                if not cand.committed:
                    self._undo(pl, prep, module_created, made_dir)
                raise
            cand.extras["module_created"] = module_created
            cand.extras["registered"] = discover.registered_stems(self.root, pl.script_rel)
        if self._refresh is not None:
            cand.refresh = self._refresh(pl.script_rel)

    @staticmethod
    def _undo(pl: PlacementV2, prep: PreparedV2, module_created: bool, made_dir: bool) -> None:
        """没提交成功时撤回本次新建的东西：只删**本次新建**且字节仍是我们写的那一份（复用的公共模块、别人的文件一律不碰）。"""
        if module_created:
            try:
                if pl.module_path.read_bytes() == prep.module:
                    pl.module_path.unlink()
            except OSError:
                pass
        if made_dir:
            try:
                pl.figures_dir.rmdir()  # 非空就留着（别人刚往里放了东西）
            except OSError:
                pass

    def discard(self, run, cand: CandidateV2) -> None:
        ai_figure_edit.remove_candidate(cand.dir)


def envelope_for_v2(
    *, project_key: str, prepared: PreparedV2, rid: str | None
) -> ai_tasks.TaskEnvelope:
    pl = prepared.placement
    writes = [pl.script_rel]
    if common_module_state(pl.module_path, prepared.module) == "absent":
        writes.append(pl.module_rel)
    return ai_tasks.TaskEnvelope.from_dict(
        {
            "schema_version": ai_tasks.SCHEMA_VERSION,
            "task_type": V2_TASK_TYPE,
            "project_ref": {"project_key": project_key},
            "client_request_id": rid,
            "user_intent": f"data_figure_v2:{prepared.selection.title_code}",
            "input_refs": [
                {
                    "kind": "data_file",
                    "path_rel": pl.data_rel,
                    "sha256": prepared.values.sha256,
                    "size": prepared.values.size,
                }
            ],
            "output_contract": {"allowed_writes": writes, "new_figures": True},
            "execution_policy": {"executor": ai_tasks.DETERMINISTIC},
            "budget": {"max_agent_runs": 0, "max_renders": 1},
        }
    )


ADAPTER_V2 = DataFigureV2Adapter()


def ensure_registered_v2() -> None:
    if ai_tasks.ADAPTERS.get(V2_TASK_TYPE) is not ADAPTER_V2:
        ai_tasks.register(ADAPTER_V2)


def data_verdict(row: dict | None) -> str:
    """一次 v2 落盘任务的「数据核对」结论（读历史库那一行的检查）：任一 `data.*` 指纹检查 failed → `mismatch`；`data.verifiable`
    是 unknown → `unverifiable`（这张图没有任何一层能逐字节核对）；全部可核层 verified 且渲染 verified → `verified`；其余 `unknown`。"""
    try:
        checks = [
            c
            for kind in ("figure", "persistence")
            for c in row["verification"][kind]["checks"]  # type: ignore[index]
        ]
    except (TypeError, KeyError):
        return "unknown"
    by_id = {c.get("id"): c for c in checks}
    fp = [
        c
        for c in checks
        if str(c.get("id", "")).startswith("data.") and c.get("id") != "data.verifiable"
    ]
    if any(c.get("verdict") == FAILED for c in fp):
        return "mismatch"
    ver = by_id.get("data.verifiable")
    if ver is None:
        return "unknown"
    if ver.get("verdict") == UNKNOWN:
        return "unverifiable"
    render = by_id.get("render.figure")
    if (
        fp
        and all(c.get("verdict") == VERIFIED for c in fp)
        and (render is not None and render.get("verdict") == VERIFIED)
    ):
        return "verified"
    return "unknown"
