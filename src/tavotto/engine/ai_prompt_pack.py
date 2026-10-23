"""TAF Prompt Pack（ADR 0138 Phase 6，`03-contracts.md` §九）：改图任务写给编码 Agent 的全部文字，版本化成一份可校验的数据。

Harness Lab 能优化的**只有**这里的文字（提示词模板、任务上下文、修正指令）。不可自动优化的东西——CLI 的 sandbox /
permission 参数、写入范围与 `output_contract`、safe / native、L1 / L2 必检项、隐私与遥测白名单、备份与回滚——**不在
schema 里**：`load()` 用白名单字段校验，任何不认识的字段（含嵌套）一律拒绝，所以候选 Pack 不可能借道改它们。

* `V1` 是基线：与 Phase 6 之前 `ai_bridge._build_prompt` / `ai_figure_edit.prompt_note` / 修正指令**逐字节相同**
  （`tests/test_ai_prompt_pack.py` 用冻结的旧实现对拍）；
* 产品只用 `DEFAULT`（= `V1`，代码里的常量）。工具（`scripts/dev/taf_bench.py`）可以加载候选 Pack 做对比，但**不能**
  改默认值——换默认 Pack 只能走普通 PR + 独立审查；
* 占位符是闭集（每个模板各自允许哪些）：`load()` 要求每个模板恰好用全它的占位符（缺了会静默丢掉用户需求 / 目标脚本名），
  且去掉合法占位符后不许剩任何花括号（`{user-prompt}`、`{USER_PROMPT}`、`{x!r}`、未闭合都会被拒，不会被当字面量原样输出）；渲染是单遍替换：用户原文里出现 `{script}` 之类的字样不会被再展开。

纯标准库，Flask 父进程 import。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

SCHEMA_VERSION = 1

#: 模板 → 它允许的占位符。字段名本身就是白名单：`load()` 只认这里列出的
_TEMPLATES: dict[str, frozenset[str]] = {
    "intro": frozenset(),
    "target": frozenset({"script"}),
    "rules_heading": frozenset(),
    "rule_matplotlib": frozenset(),
    "rule_stems": frozenset(),
    "rule_scope": frozenset(),
    "rule_fonts": frozenset(),
    "rule_shared_style": frozenset(),
    "context_stem": frozenset({"stem"}),
    "context_element": frozenset({"gid", "label"}),
    "context_overrides": frozenset({"overrides"}),
    "request": frozenset({"user_prompt"}),
    "candidate_note": frozenset({"project_root"}),
    "correction_header": frozenset(),
    "correction_item": frozenset({"text"}),
    "fix_syntax": frozenset({"where", "detail"}),
    "fix_syntax_where_line": frozenset({"lineno"}),
    "fix_syntax_where_unknown": frozenset(),
    "fix_stems": frozenset({"before", "after"}),
    "fix_other_files": frozenset({"files"}),
    "fix_target_missing": frozenset(),
}
_META = {"schema_version", "pack_id", "version", "source"}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class PackError(ValueError):
    """Prompt Pack 不合格（未知字段、缺字段、类型不对、用了不允许的占位符）。"""


@dataclass(frozen=True)
class PromptPack:
    pack_id: str
    version: int
    source: str
    templates: MappingProxyType

    def render(self, name: str, **values) -> str:
        """单遍替换：模板里的 `{name}` 换成值；值里的花括号原样保留（用户原文不会被再展开）。"""
        allowed = _TEMPLATES[name]
        missing = allowed - values.keys()
        if missing:
            raise PackError(f"{name} 缺占位符的值：{sorted(missing)}")
        return _PLACEHOLDER.sub(
            lambda m: str(values[m.group(1)]) if m.group(1) in allowed else m.group(0),
            self.templates[name],
        )


def load(data: dict) -> PromptPack:
    """校验并冻结一份 Pack。白名单：顶层只许 `_META` + `templates`，`templates` 只许 `_TEMPLATES` 的键且一个不能少。"""
    if not isinstance(data, dict):
        raise PackError("pack 不是对象")
    unknown = set(data) - _META - {"templates"}
    if unknown:
        raise PackError(f"未知字段：{sorted(unknown)}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise PackError(f"schema_version={data.get('schema_version')!r}")
    for key in ("pack_id", "source"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise PackError(f"{key} 不是非空字符串")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PackError(f"version={version!r}")
    templates = data.get("templates")
    if not isinstance(templates, dict):
        raise PackError("templates 不是对象")
    unknown = set(templates) - _TEMPLATES.keys()
    missing = _TEMPLATES.keys() - set(templates)
    if unknown or missing:
        raise PackError(f"templates 未知 {sorted(unknown)} / 缺 {sorted(missing)}")
    for name, text in templates.items():
        if not isinstance(text, str):
            raise PackError(f"{name} 不是字符串")
        used = set(_PLACEHOLDER.findall(text))
        if used - _TEMPLATES[name]:
            raise PackError(f"{name} 用了不允许的占位符：{sorted(used - _TEMPLATES[name])}")
        if "{" in _PLACEHOLDER.sub("", text) or "}" in _PLACEHOLDER.sub("", text):
            raise PackError(f"{name} 含不合法的花括号")
        if used != _TEMPLATES[name]:
            raise PackError(f"{name} 没用全它的占位符：缺 {sorted(_TEMPLATES[name] - used)}")
    return PromptPack(
        pack_id=data["pack_id"],
        version=version,
        source=data["source"],
        templates=MappingProxyType(dict(templates)),
    )


#: 基线 Pack：Phase 6 之前的提示词逐字节（来源见 `source`）
V1 = {
    "schema_version": SCHEMA_VERSION,
    "pack_id": "figure_edit",
    "version": 1,
    "source": "baseline：ai_bridge._build_prompt + ai_figure_edit.prompt_note / 修正指令（ADR 0138 Phase 6 之前的原文）",
    "templates": {
        "intro": "你在修改一篇论文的图表脚本（Python）。",
        "target": "目标脚本：{script}（在当前工作目录中）。",
        "rules_heading": "硬性要求（外部系统靠这些约定解析产物，违反任何一条都会让图表无法再编辑）：",
        "rule_matplotlib": (
            "1. 图必须用 matplotlib 生成，并经 Figure.savefig(...)（或图库自带的保存封装）"
            "落盘。禁止改用 PIL/Pillow、plotly、bokeh、seaborn 之外叠加的其他渲染后端、"
            "手写 SVG/PDF、subprocess 调外部绘图工具等方式出图——外部系统靠拦截 "
            "matplotlib 的 savefig 实现参数化编辑，其他方式生成的图完全无法再编辑。"
        ),
        "rule_stems": "2. 保持既有输出文件名（stem）与出图数量不变。",
        "rule_scope": "3. 只修改目标脚本；不要运行脚本（渲染由外部系统负责）；不要修改其他脚本或数据文件。",
        "rule_fonts": (
            "4. 设置字体用字体族名（rcParams['font.family'] 或 fontfamily= / "
            "FontProperties(family=...)），不要用 FontProperties(fname=...) 指定字体文件路径："
            "fname 会让 matplotlib 只从那一个文件取字形，西文字体里没有的中文会画成方框；"
            "用族名时外部系统会自动给中文接上中文字体。"
        ),
        "rule_shared_style": (
            "5. 图库有共享样式 paper_style.py：沿用它既有的字体/字号/配色/"
            "版面规范，不要修改 paper_style.py 本身。"
        ),
        "context_stem": "用户当前编辑的是该脚本的输出面板：{stem}。",
        "context_element": "用户在界面上选中的元素：{gid}（{label}）。",
        "context_overrides": (
            "用户已在界面上做了这些非破坏性修改（渲染时叠加的 override，"
            "代表期望状态，供参考）：{overrides}"
        ),
        "request": "用户需求：{user_prompt}",
        "candidate_note": (
            "注意：当前工作目录是目标脚本的工作副本；原项目目录 {project_root} 只供只读参考"
            "（数据文件、同目录模块在那里），不要在原项目目录里创建或修改任何文件——改完的副本由外部系统核对后写回。"
        ),
        "correction_header": (
            "上一次修改没有通过写回前的检查，原项目没有被改动。请在当前工作副本里只修正下面的问题，"
            "保留用户要求的修改，不要运行脚本："
        ),
        "correction_item": "- {text}",
        "fix_syntax": "目标脚本{where}有语法错误（{detail}），改成能被 Python 解析的代码。",
        "fix_syntax_where_line": "第 {lineno} 行",
        "fix_syntax_where_unknown": "脚本里",
        "fix_stems": "脚本输出的图名变了（原来 {before}，现在 {after}）；保持原来的输出文件名。",
        "fix_other_files": (
            "除目标脚本外还改了 {files}；把它们恢复成原样（原件在原项目目录里，只读参考），只改目标脚本。"
        ),
        "fix_target_missing": "目标脚本在工作副本里不见了；按原项目里的同名脚本恢复它，再在它上面做用户要求的修改。",
    },
}

DEFAULT = load(V1)


# --------------------------------------------------------------------------- 组装（结构由代码决定，文字由 Pack 决定）
def figure_edit_prompt(
    pack: PromptPack,
    *,
    script: str,
    user_prompt: str,
    context: dict | None,
    shared_style: bool,
) -> str:
    """改图提示词：引言 → 目标 → 硬性要求（共享样式按存在与否）→ 界面上下文（有才写）→ 用户需求。"""
    lines = [
        pack.render("intro"),
        pack.render("target", script=script),
        "",
        pack.render("rules_heading"),
        pack.render("rule_matplotlib"),
        pack.render("rule_stems"),
        pack.render("rule_scope"),
        pack.render("rule_fonts"),
    ]
    if shared_style:
        lines.append(pack.render("rule_shared_style"))
    ctx = context or {}
    ctx_lines = []
    if ctx.get("stem"):
        ctx_lines.append(pack.render("context_stem", stem=ctx["stem"]))
    if ctx.get("gid"):
        ctx_lines.append(pack.render("context_element", gid=ctx["gid"], label=ctx.get("label", "")))
    if ctx.get("overrides"):
        ctx_lines.append(pack.render("context_overrides", overrides=ctx["overrides"]))
    if ctx_lines:
        lines += ["", *ctx_lines]
    lines += ["", pack.render("request", user_prompt=user_prompt)]
    return "\n".join(lines)


def candidate_note(pack: PromptPack, project_root) -> str:
    return pack.render("candidate_note", project_root=project_root)


def correction_prompt(pack: PromptPack, items: list[str]) -> str:
    return (
        pack.render("correction_header")
        + "\n"
        + "\n".join(pack.render("correction_item", text=s) for s in items)
    )


def fix_text(pack: PromptPack, key: tuple[str, str], observed: dict) -> str | None:
    """可修正失败 `(检查 id, 原因)` → 写给 Agent 的那一句；不在闭集里回 None（= 不可修）。"""
    o = observed or {}
    if key == ("candidate.syntax", "syntax_error"):
        where = (
            pack.render("fix_syntax_where_line", lineno=o["lineno"])
            if o.get("lineno")
            else pack.render("fix_syntax_where_unknown")
        )
        return pack.render("fix_syntax", where=where, detail=o.get("detail") or "SyntaxError")
    if key == ("candidate.stems", "stems_changed"):
        return pack.render("fix_stems", before=o.get("before"), after=o.get("after"))
    if key == ("candidate.tree", "other_file_changed"):
        return pack.render("fix_other_files", files=o.get("files"))
    if key == ("candidate.target", "target_missing"):
        return pack.render("fix_target_missing")
    return None


#: 可修正的失败闭集（与 `fix_text` 的分支一一对应；`ai_figure_edit` 据此判可修）
CORRECTABLE = frozenset(
    {
        ("candidate.syntax", "syntax_error"),
        ("candidate.stems", "stems_changed"),
        ("candidate.tree", "other_file_changed"),
        ("candidate.target", "target_missing"),
    }
)


# --------------------------------------------------------------------------- 「生成可编辑版本」（convert_to_editable，ADR 0143）
# 这一节不进 `_TEMPLATES` / `V1`：它是另一个任务类型，Harness Lab 的候选 Pack 契约（figure_edit 的全部键）保持不变。
# 占位符是闭集，渲染同样是单遍替换（用户脚本文件名里出现 `{x}` 不会被再展开）。
_CONVERT_TEMPLATES: dict[str, tuple[frozenset[str], str]] = {
    "intro": (
        frozenset({"library"}),
        "你在为一篇论文的图表脚本（Python）生成「可编辑的 Matplotlib 版本」。原脚本用 {library} 把图直接画成了图片，"
        "外部系统无法再逐元素编辑它。",
    ),
    "target": (
        frozenset({"script", "target"}),
        "原脚本：{script}（当前工作目录里的只读副本，不要修改它）。请新建且只新建一个文件：{target}。",
    ),
    "reference": (
        frozenset({"reference"}),
        "原脚本画出的图片的参考副本：{reference}（只读）。让新图的尺寸、字号、配色、标注位置尽量与它一致。",
    ),
    "rules_heading": (frozenset(), "硬性要求："),
    "rule_keep_data": (
        frozenset(),
        "1. 保留原脚本的数据读取与计算部分（读文件、拟合、统计……）原样沿用，只把「绘图」换成 Matplotlib。"
        "例如 ROOT / RooFit 脚本的拟合仍用 ROOT，从 RooPlot / RooCurve / RooHist 取出点再用 Matplotlib 画；"
        "Pillow 画的热图用 imshow / pcolormesh 复现色标、格子标注、标题与脚注。",
    ),
    "rule_matplotlib": (
        frozenset(),
        "2. 图必须用 matplotlib 生成，并经 Figure.savefig(...) 落盘（沿用原脚本的输出文件名与格式）。"
        "禁止再用 PIL / OpenCV / plotly / ROOT 画布出图。",
    ),
    "rule_size": (
        frozenset(),
        "3. 输出图幅（figsize × dpi）与字号尽量对齐原图。字体用字体族名（rcParams['font.family']），"
        "不要用 FontProperties(fname=...) 指定字体文件。",
    ),
    "rule_scope": (
        frozenset(),
        "4. 不要运行脚本（渲染与校验由外部系统负责）；不要修改工作目录里的其他任何文件，只写上面那一个新文件。",
    ),
    # --- Pillow 来源（ADR 0143 修订，2026-10-23）：library == "pillow" 时在硬性要求后追加的对照表；别的库不出现
    "pillow_table": (
        frozenset(),
        "Pillow → Matplotlib 对照（原脚本是 Pillow 直接画的；Pillow 的原点在左上角、y 向下，搬运时务必处理坐标翻转）：\n"
        "- ImageDraw.line / rectangle / ellipse / polygon → ax.plot / matplotlib.patches.Rectangle / Ellipse / Polygon；"
        "坐标翻转：画布像素坐标直接沿用，并设 ax.set_xlim(0, W); ax.set_ylim(H, 0)（或自行换算 y = H - y）。\n"
        "- ImageDraw.text + ImageFont → ax.text；字号按 px 换算成 pt：pt = px × 72 / dpi。\n"
        "- Image.new((w, h), color) → fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)，背景色设到 figure / axes，"
        'ax.axis("off")，fig.subplots_adjust(0, 0, 1, 1)，让坐标轴铺满整张图。\n'
        "- 已有照片 / Image.open / paste → ax.imshow(np.asarray(img), extent=...)，extent 按像素位置给。\n"
        "- 遇到没有可靠对应的 Pillow 操作（滤镜、混合模式、复杂变换……）：不要近似替换。此时停下，"
        "你的回答第一行写 `TAVOTTO_NEEDS_INPUT: ` 紧跟一句话说清是哪一步换不了、为什么，并且不要写任何新文件。",
    ),
    "note": (
        frozenset({"project_root"}),
        "注意：当前工作目录是工作副本；原项目目录 {project_root} 只供只读参考（数据文件、同目录模块在那里），"
        "不要在原项目目录里创建或修改任何文件——写好的新文件由外部系统校验后，经用户确认才放进项目。",
    ),
    # --- R / MATLAB 来源（ADR 0143 修订，2026-10-23）：同一节的变体，只换引言与硬性要求里的“怎么搬数据”，
    # 目标 / 参考图 / 图幅 / 范围 / 工作副本说明沿用上面同一份。Tavotto 从不运行用户的 R / MATLAB。
    "intro_foreign": (
        frozenset({"language"}),
        "你在为一篇论文的图表脚本（{language}）生成「可编辑的 Matplotlib 版本」：用 Python 把它的数据读取、计算与绘图"
        "重写一遍。原脚本是 {language} 写的，Tavotto 不会运行它，所以你写的新脚本必须自己能独立读到数据、算出结果、画出图。",
    ),
    "rule_foreign_steps": (
        frozenset(),
        "1. 按原脚本的顺序逐步搬运：读数据 → 整理 / 筛选 / 分组 → 统计与拟合 → 绘图。数据文件沿用原脚本里的路径写法"
        "（相对路径照旧）；每一步用 Python 里最直接、可核对的对应物，不要“顺便优化”或改动计算的科学含义。",
    ),
    "rule_foreign_mapping_r": (
        frozenset(),
        "2. 常用对照（R → Python）：read.csv / readr::read_csv / readxl::read_excel → pandas.read_csv / read_excel；"
        "dplyr 的 filter / mutate / group_by + summarise → pandas 的布尔索引 / assign / groupby + agg；"
        "lm / glm → statsmodels.formula.api 的 ols / glm（公式写法一致）；"
        "ggplot 的 geom_point / geom_line / geom_col / geom_errorbar / geom_boxplot / geom_histogram → ax.scatter / plot / bar / errorbar / boxplot / hist；"
        'geom_smooth(method = "loess") → statsmodels.nonparametric.smoothers_lowess.lowess（把 span 对应到 frac，并在代码注释里写明）；'
        "facet_wrap / facet_grid → plt.subplots 的多个子图；scale_*_log10 → ax.set_xscale / set_yscale('log')；"
        "ggsave(width, height, dpi) → figsize 与 dpi（英寸）。",
    ),
    "rule_foreign_mapping_matlab": (
        frozenset(),
        "2. 常用对照（MATLAB → Python）：readtable / readmatrix / csvread / xlsread → pandas.read_csv / read_excel；"
        "plot / scatter / errorbar / bar / histogram / boxplot → ax.plot / scatter / errorbar / bar / hist / boxplot；"
        "xlabel / ylabel / title / legend / xlim / ylim / grid → ax.set_xlabel / set_ylabel / set_title / legend / set_xlim / set_ylim / grid；"
        "subplot / tiledlayout → plt.subplots；polyfit / polyval → numpy.polyfit / polyval；"
        "fitlm → statsmodels.formula.api.ols；linspace / meshgrid → numpy 同名函数；"
        "MATLAB 的下标从 1 开始、矩阵按列优先、`.^` `.*` 是逐元素运算——搬运时逐处核对下标与形状。",
    ),
    "rule_foreign_needs_input": (
        frozenset(),
        "3. 遇到 Python 里没有可靠逐一对应的统计方法或函数（例如特定的混合效应模型、专用的非参数检验、某个包独有的拟合器），"
        "不要用近似方法替换，也不要编一个相似的结果。此时停下：你的回答第一行写 `TAVOTTO_NEEDS_INPUT: ` 紧跟一句话，"
        "说清是哪一步（原脚本里的函数名）换不了、为什么；并且不要写任何新文件。",
    ),
    "fix_no_matplotlib": (
        frozenset({"target"}),
        "{target} 里没有 import matplotlib；改成用 matplotlib 绘图。",
    ),
    "fix_no_figure": (
        frozenset({"target"}),
        "{target} 跑完后没有捕获到任何 Matplotlib 图；确认它用 matplotlib.pyplot 创建了 Figure 并 savefig 了。",
    ),
    "fix_run_failed": (
        frozenset({"target", "detail"}),
        "{target} 运行时出错：{detail}。修正它（数据读取沿用原脚本的写法）。",
    ),
    "fix_target_missing": (
        frozenset({"target"}),
        "新文件 {target} 没有写出来（或是空的）；在当前工作目录里创建它。",
    ),
    "fix_extra_files": (
        frozenset({"files"}),
        "工作目录里除了新文件还改动了 {files}；把它们恢复成原样，只写那一个新文件。",
    ),
}


def _convert_render(name: str, **values) -> str:
    allowed, text = _CONVERT_TEMPLATES[name]
    missing = allowed - values.keys()
    if missing:
        raise PackError(f"{name} 缺占位符的值：{sorted(missing)}")
    return _PLACEHOLDER.sub(
        lambda m: str(values[m.group(1)]) if m.group(1) in allowed else m.group(0), text
    )


def _renumbered(text: str, old: int, new: int) -> str:
    """共用的硬性要求在变体里排在后面：序号换一下，正文不复制。"""
    prefix = f"{old}. "
    if not text.startswith(prefix):
        raise PackError(f"硬性要求的序号前缀不是 {prefix!r}")
    return f"{new}. " + text[len(prefix) :]


#: 非 Python 来源的显示名（提示词里的“原脚本是 X 写的”）
_FOREIGN_NAMES = {"r": "R", "matlab": "MATLAB"}


def convert_requirements() -> list[str]:
    """convert 任务里与「谁来执行」无关的三条硬性要求（保留数据与计算 / 必须 Matplotlib 出图 / 图幅字号对齐）。

    CLI 提示词（`convert_prompt`）与内置执行器（`agent_runner` 的 convert 系统提示）共用这一份文字，不各抄一份。"""
    return [
        _convert_render("rule_keep_data"),
        _convert_render("rule_matplotlib"),
        _convert_render("rule_size"),
    ]


def convert_prompt(
    *,
    script: str,
    target: str,
    library: str | None = None,
    reference: str | None,
    project_root,
    source_language: str = "python",
) -> str:
    """「生成可编辑版本」提示词：引言 → 目标 → 参考图（有才写）→ 硬性要求 → 工作副本说明。

    `source_language` 为 `r` / `matlab` 时同一节换成“逐步搬运 + 对照表 + 没有可靠对应就停下”的变体（目标 / 参考图 / 图幅 /
    范围 / 工作副本说明是同一份模板，不复制）。"""
    foreign = _FOREIGN_NAMES.get(source_language)
    if source_language != "python" and foreign is None:
        raise PackError(f"未知的来源语言：{source_language!r}")
    lines = [
        _convert_render("intro_foreign", language=foreign)
        if foreign
        else _convert_render("intro", library=library or ""),
        _convert_render("target", script=script, target=target),
    ]
    if reference:
        lines.append(_convert_render("reference", reference=reference))
    lines += ["", _convert_render("rules_heading")]
    if foreign:
        lines += [
            _convert_render("rule_foreign_steps"),
            _convert_render(f"rule_foreign_mapping_{source_language}"),
            _convert_render("rule_foreign_needs_input"),
            _renumbered(_convert_render("rule_matplotlib"), 2, 4),
            _renumbered(_convert_render("rule_size"), 3, 5),
            _renumbered(_convert_render("rule_scope"), 4, 6),
        ]
    else:
        lines += [*convert_requirements(), _convert_render("rule_scope")]
    if not foreign and (library or "").lower() == "pillow":
        lines += ["", _convert_render("pillow_table")]
    lines += ["", _convert_render("note", project_root=project_root)]
    return "\n".join(lines)


def convert_fix_text(key: tuple[str, str | None], observed: dict) -> str | None:
    """convert 任务特有的可修正失败 → 写给 Agent 的一句；不在闭集里回 None。"""
    o = observed or {}
    target = o.get("target") or "新文件"
    if key == ("candidate.matplotlib", "no_matplotlib_import"):
        return _convert_render("fix_no_matplotlib", target=target)
    if key == ("render.capture", "no_matplotlib_figure"):
        return _convert_render("fix_no_figure", target=target)
    if key == ("render.capture", "run_failed"):
        detail = (o.get("detail") or o.get("code") or "")[:300]
        return _convert_render("fix_run_failed", target=target, detail=detail)
    if key == ("candidate.target", "target_missing"):
        return _convert_render("fix_target_missing", target=target)
    if key == ("candidate.tree", "other_file_changed"):
        return _convert_render("fix_extra_files", files=o.get("files"))
    return None
