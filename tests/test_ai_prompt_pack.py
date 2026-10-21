"""Prompt Pack（ADR 0138 Phase 6a）：V1 与 Phase 6 之前的提示词**逐字节相同**，schema 白名单挡住越权字段。

下面 `_legacy_*` 是 Phase 6 之前 `ai_bridge._build_prompt` 与 `ai_figure_edit` 修正指令 / 副本说明的**原文冻结**（从
`85641ea19` 逐字拷入，只改了函数名）。V1 若有一个字节不同，这里就红——换文字要新开一个版本，而不是悄悄改 V1。
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from tavotto.engine import ai_bridge, ai_figure_edit, ai_prompt_pack
from tavotto.engine.ai_tasks import CheckResult


# --------------------------------------------------------------------------- 冻结的旧实现
def _legacy_build_prompt(
    script: str, user_prompt: str, context: dict | None, figures_dir: str | None = None
) -> str:
    """给编程助手的规范化提示词。

    硬性约束单独成段、编号列出：Tavotto 的参数化编辑靠拦截 matplotlib 的
    savefig 才成立，助手一旦改用 PIL / plotly / 手写 SVG 等其他方式出图，
    整条 live-figure 链路（manifest / override / 导出）全部失效——这必须是
    对助手的硬性要求，而不是含在叙述里的默认假设。
    图库细节（如 paper_style.py）按实际存在与否动态给出，不硬编码任何
    具体图库的私有规范。"""
    lines = [
        "你在修改一篇论文的图表脚本（Python）。",
        f"目标脚本：{script}（在当前工作目录中）。",
        "",
        "硬性要求（外部系统靠这些约定解析产物，违反任何一条都会让图表无法再编辑）：",
        "1. 图必须用 matplotlib 生成，并经 Figure.savefig(...)（或图库自带的保存封装）"
        "落盘。禁止改用 PIL/Pillow、plotly、bokeh、seaborn 之外叠加的其他渲染后端、"
        "手写 SVG/PDF、subprocess 调外部绘图工具等方式出图——外部系统靠拦截 "
        "matplotlib 的 savefig 实现参数化编辑，其他方式生成的图完全无法再编辑。",
        "2. 保持既有输出文件名（stem）与出图数量不变。",
        "3. 只修改目标脚本；不要运行脚本（渲染由外部系统负责）；不要修改其他脚本或数据文件。",
        # fname 锁死单个字体文件时 matplotlib 不做逐字形回退：换成 Times 之类的
        # 西文字体后图里的中文全变方框（用户反馈）。引擎会尽量把 fname 换回族名
        # （`overrides._release_font_file`），但提示词这一侧先别把它写出来。
        "4. 设置字体用字体族名（rcParams['font.family'] 或 fontfamily= / "
        "FontProperties(family=...)），不要用 FontProperties(fname=...) 指定字体文件路径："
        "fname 会让 matplotlib 只从那一个文件取字形，西文字体里没有的中文会画成方框；"
        "用族名时外部系统会自动给中文接上中文字体。",
    ]
    if figures_dir and (Path(figures_dir) / "paper_style.py").is_file():
        lines.append(
            "5. 图库有共享样式 paper_style.py：沿用它既有的字体/字号/配色/"
            "版面规范，不要修改 paper_style.py 本身。"
        )
    ctx = context or {}
    ctx_lines = []
    if ctx.get("stem"):
        ctx_lines.append(f"用户当前编辑的是该脚本的输出面板：{ctx['stem']}。")
    if ctx.get("gid"):
        ctx_lines.append(f"用户在界面上选中的元素：{ctx['gid']}（{ctx.get('label', '')}）。")
    if ctx.get("overrides"):
        ctx_lines.append(
            "用户已在界面上做了这些非破坏性修改（渲染时叠加的 override，"
            f"代表期望状态，供参考）：{ctx['overrides']}"
        )
    if ctx_lines:
        lines += ["", *ctx_lines]
    lines += [
        "",
        f"用户需求：{user_prompt}",
    ]
    return "\n".join(lines)


def _legacy_syntax_line(o: dict) -> str:
    where = f"第 {o['lineno']} 行" if o.get("lineno") else "脚本里"
    return f"目标脚本{where}有语法错误（{o.get('detail') or 'SyntaxError'}），改成能被 Python 解析的代码。"


#: 可修正的提交前失败（检查 id, 原因）→ 写给 Agent 的那一句。闭集：不在表里的失败一律不修正
_LEGACY_CORRECTABLE = {
    ("candidate.syntax", "syntax_error"): _legacy_syntax_line,
    ("candidate.stems", "stems_changed"): lambda o: (
        f"脚本输出的图名变了（原来 {o.get('before')}，现在 {o.get('after')}）；保持原来的输出文件名。"
    ),
    ("candidate.tree", "other_file_changed"): lambda o: (
        f"除目标脚本外还改了 {o.get('files')}；把它们恢复成原样（原件在原项目目录里，只读参考），只改目标脚本。"
    ),
    ("candidate.target", "target_missing"): lambda o: (
        "目标脚本在工作副本里不见了；按原项目里的同名脚本恢复它，再在它上面做用户要求的修改。"
    ),
}


def _legacy_prompt_note(project_root) -> str:
    return (
        f"注意：当前工作目录是目标脚本的工作副本；原项目目录 {project_root} 只供只读参考"
        "（数据文件、同目录模块在那里），不要在原项目目录里创建或修改任何文件——改完的副本由外部系统核对后写回。"
    )


def _legacy_correction(lines: list[str]) -> str:
    return (
        "上一次修改没有通过写回前的检查，原项目没有被改动。请在当前工作副本里只修正下面的问题，"
        "保留用户要求的修改，不要运行脚本：\n" + "\n".join(f"- {s}" for s in lines)
    )


# --------------------------------------------------------------------------- 逐字节对拍
CONTEXTS = [
    None,
    {},
    {"stem": "Fig1"},
    {"gid": "axes_0.lines_0"},
    {"gid": "axes_0.title", "label": "标题"},
    {
        "stem": "Fig1",
        "gid": "axes_0.lines_0",
        "label": "signal",
        "overrides": [{"gid": "x", "prop": "color"}],
    },
    {"overrides": {"a": 1}},
]
PROMPTS = ["把标题改成 new", "用 {script} 与 {user_prompt} 这几个字", "多行\n指令"]
SCRIPTS = ["fig1.py", "sub/dir/fig 2.py"]


@pytest.mark.parametrize(
    ("script", "prompt", "context", "style"),
    list(itertools.product(SCRIPTS, PROMPTS, CONTEXTS, [False, True])),
)
def test_v1_figure_edit_prompt_is_byte_identical(tmp_path, script, prompt, context, style):
    if style:
        (tmp_path / "paper_style.py").write_text("x = 1\n", encoding="utf-8")
    want = _legacy_build_prompt(script, prompt, context, str(tmp_path))
    assert ai_bridge._build_prompt(script, prompt, context, str(tmp_path)) == want
    assert ai_bridge._build_prompt(script, prompt, context, None) == _legacy_build_prompt(
        script, prompt, context, None
    )


def test_v1_candidate_note_is_byte_identical(tmp_path):
    cand = ai_figure_edit.Candidate.__new__(ai_figure_edit.Candidate)
    object.__setattr__(cand, "project_root", tmp_path / "项目 {x}")
    assert ai_figure_edit.prompt_note(cand) == _legacy_prompt_note(tmp_path / "项目 {x}")


OBSERVED = {
    ("candidate.syntax", "syntax_error"): [
        {"lineno": 3, "detail": "invalid syntax"},
        {"lineno": None, "detail": None},
        {},
    ],
    ("candidate.stems", "stems_changed"): [
        {"before": ["Fig1"], "after": ["Other"]},
        {"before": None, "after": None},
    ],
    ("candidate.tree", "other_file_changed"): [{"files": ["paper_style.py"]}, {}],
    ("candidate.target", "target_missing"): [{}],
}


@pytest.mark.parametrize(("key", "observed"), [(k, o) for k, os in OBSERVED.items() for o in os])
def test_v1_correction_lines_are_byte_identical(key, observed):
    assert ai_prompt_pack.fix_text(ai_prompt_pack.DEFAULT, key, observed) == _LEGACY_CORRECTABLE[
        key
    ](observed)


def test_v1_correction_prompt_is_byte_identical():
    failed = [
        CheckResult(
            "candidate.syntax",
            "L1",
            "precommit",
            "integrity",
            "failed",
            reason_code="syntax_error",
            observed={"lineno": 2, "detail": "x"},
        ),
        CheckResult(
            "candidate.tree",
            "L1",
            "precommit",
            "integrity",
            "failed",
            reason_code="other_file_changed",
            observed={"files": ["a.py"]},
        ),
    ]
    got = ai_figure_edit.ADAPTER.correction(None, failed)
    want = _legacy_correction(
        [_LEGACY_CORRECTABLE[(c.id, c.reason_code)](c.observed) for c in failed]
    )
    assert got == {"prompt": want}


def test_the_correctable_set_is_unchanged():
    assert ai_prompt_pack.CORRECTABLE == set(_LEGACY_CORRECTABLE)
    assert (
        ai_prompt_pack.fix_text(
            ai_prompt_pack.DEFAULT, ("project.untouched", "project_files_changed"), {}
        )
        is None
    )


# --------------------------------------------------------------------------- schema 白名单
def _v1(**over):
    import copy

    d = copy.deepcopy(ai_prompt_pack.V1)
    d.update(over)
    return d


@pytest.mark.parametrize(
    "bad",
    [
        _v1(sandbox="danger-full-access"),  # 不在 schema 里的东西（CLI 权限）候选 Pack 改不了
        _v1(allowed_writes=["*"]),
        _v1(schema_version=2),
        _v1(version=0),
        _v1(pack_id=""),
        _v1(templates={**ai_prompt_pack.V1["templates"], "rule_permissions": "随便写"}),
        _v1(
            templates={k: v for k, v in ai_prompt_pack.V1["templates"].items() if k != "rule_scope"}
        ),
        _v1(
            templates={
                **ai_prompt_pack.V1["templates"],
                "target": "目标：{script}，根目录 {project_root}",
            }
        ),
        _v1(templates={**ai_prompt_pack.V1["templates"], "intro": 3}),
        _v1(templates={**ai_prompt_pack.V1["templates"], "request": "用户需求：{user-prompt}"}),
        _v1(templates={**ai_prompt_pack.V1["templates"], "request": "用户需求：{USER_PROMPT}"}),
        _v1(templates={**ai_prompt_pack.V1["templates"], "request": "用户需求：{user_prompt!r}"}),
        _v1(templates={**ai_prompt_pack.V1["templates"], "request": "用户需求：{user_prompt"}),
        _v1(templates={**ai_prompt_pack.V1["templates"], "request": "用户需求：}"}),
        _v1(templates={**ai_prompt_pack.V1["templates"], "request": "用户需求："}),
        _v1(
            templates={**ai_prompt_pack.V1["templates"], "request": "用户需求：{user_prompt} {oops"}
        ),
        _v1(templates={**ai_prompt_pack.V1["templates"], "request": "用户需求：{user_prompt}}"}),
    ],
    ids=[
        "sandbox",
        "writes",
        "schema",
        "version",
        "pack_id",
        "unknown-template",
        "missing-template",
        "foreign-placeholder",
        "non-string",
        "brace-hyphen",
        "brace-upper",
        "brace-conversion",
        "brace-unclosed",
        "brace-stray-close",
        "placeholder-dropped",
        "extra-open-brace",
        "extra-close-brace",
    ],
)
def test_the_loader_rejects_anything_outside_the_whitelist(bad):
    with pytest.raises(ai_prompt_pack.PackError):
        ai_prompt_pack.load(bad)


def test_user_text_is_never_re_expanded():
    out = ai_prompt_pack.DEFAULT.render("request", user_prompt="{user_prompt}{script}")
    assert out == "用户需求：{user_prompt}{script}"


def test_the_product_default_is_the_frozen_baseline():
    assert ai_prompt_pack.DEFAULT == ai_prompt_pack.load(ai_prompt_pack.V1)
    assert ai_prompt_pack.DEFAULT.version == 1 and ai_prompt_pack.DEFAULT.pack_id == "figure_edit"
    src = Path(ai_prompt_pack.__file__).read_text(encoding="utf-8")
    assert src.count("DEFAULT = ") == 1, "默认 Pack 只有这一处赋值"
