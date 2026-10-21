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
