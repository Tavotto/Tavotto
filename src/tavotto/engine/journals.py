"""内置期刊标准库 —— 期刊 = 覆盖 + 出处（ADR 0154）。

数据的唯一权威是 `src/tavotto/profiles/journals.json`（随 wheel 分发）；TypeScript 侧
`web/src/lib/journals.ts` 经 `@journals` 别名读**同一个文件**，并实现同名同义的加载 /
校验 / 折叠。两侧靠 `tests/golden/journal_vectors.json` 对齐（pytest 与 vitest 各跑
一遍，`scripts/gen_journal_vectors.py` 生成）。

结构（schema 1）::

    catalog_rev: 正整数（库版本，「有新版可同步」的判据）
    publishers{ pid: { label, sources{ sid: {url, kind, retrieved, coverage?, archive_url?, page?} },
                       rules{ "<profile 字段路径>" | "info.<id>": rule } } }
    journals  { jid: { label, publisher, issn?, aliases?, sources?, rules? } }
    rule = {value, src, quote, anchor?, qualifier?, value_alt?, stage?, mark?, derivation?, unset?}

**两类规则共用一层外壳**：键是 `journal-base-v1` 里的字段路径的，折成 `JournalOverride`
进预检（求值器一行不改）；键是 `info.<id>`（闭集 `INFO_IDS`）的只是**展示项**，同样强制
`src` / `quote`，但不折进 override、不过 `validate_spec`。期刊没写的项不补（界面标「该期刊
未规定」，不是缺省值）。`quote` 只存一两句短摘录。

纯标准库；依赖 `profiles`（单向，不反向 import）。
"""

from __future__ import annotations

import copy
import datetime
import json
import os
import re
import unicodedata
from pathlib import Path

from . import profiles as profiles_mod

JOURNALS_FILE = "journals.json"
#: 覆盖整份期刊库文件的位置（测试 / 企业自带一套时用；与 `TAVOTTO_PROFILES_FILE` 同款）
JOURNALS_ENV = "TAVOTTO_JOURNALS_FILE"
#: 期刊覆盖叠在它上面（不是叠在课题组规范上）
BASE_PROFILE_ID = "journal-base-v1"
SCHEMA = 1
SOURCE_KINDS = ("html", "pdf")
#: 来源的覆盖面：出版社通用页 / 期刊专属页 / 只是某一本期刊的样页（后者不得挂出版社 rules）
COVERAGES = ("publisher_general", "journal_specific", "single_journal_sample")
#: 限定词：原文是「恰好 / 最大 / 最小 / 通常 / 区间 / 推荐」。非 exact 的值不会被当成精确判据
QUALIFIERS = ("exact", "max", "min", "typical", "range", "recommended")
#: 适用阶段：初投 / 录用后生产稿 / 不分
STAGES = ("submission", "production", "any")
#: 求值器按「等于」判的路径只收 exact（`max` / `typical` / `range` 的宽度进 info，评估器扩展另起 PR）
EXACT_ONLY_PATHS = (
    "widths_mm.single",
    "widths_mm.double",
    "allowed_aspect_ratios",
    "line_widths_pt",
)
#: 展示项闭集（`info.<id>`）：值类型自由，不进预检。加新 id = 改这里 + TS 同名常量 + ADR
INFO_IDS = (
    "one_half_mm",
    "max_height_mm",
    "width_range_mm",
    "width_range_single_mm",
    "width_range_double_mm",
    "width_single_mm",
    "width_double_mm",
    "min_width_mm",
    "min_line_width_pt",
    "dpi_by_kind",
    "dpi_photo",
    "dpi_halftone",
    "dpi_combination",
    "color_mode",
    "formats_raw",
    "formats_images_raw",
    "font_names_raw",
    "text_height_mm",
    "panel_label",
)
INFO_PREFIX = "info."

#: 规则键 → 它启用的检查项。期刊填了值的检查，若底里是 `suggestion` 就提到 `warn`
#: （只抬不降；期刊显式写了 `severity.<检查>` 的以它为准）。否则「按该期刊预检」在页宽 /
#: 比例 / 线宽上永远只是建议。严格同源对：`journals.ts` 的 `SEVERITY_BUMP`。
SEVERITY_BUMP = {
    "widths_mm.single": "page-width",
    "widths_mm.double": "page-width",
    "allowed_aspect_ratios": "page-aspect",
    "min_effective_font_size_pt": "font-too-small",
    "absolute_min_font_size_pt": "font-below-absolute-floor",
    "max_font_size_pt": "font-too-large",
    "min_raster_dpi": "raster-dpi",
    "line_widths_pt": "line-width-off-preset",
    "font_family.latin_accepted": "font-family-substituted",
    "cjk_fallback.required": "cjk-fallback-missing",
    "axis_policy.tick_direction": "tick-direction",
    "axis_policy.enclosed_spines": "spines-not-enclosed",
    "axis_policy.label_format": "axis-label-format",
    "axis_policy.label_format_regex": "axis-label-format",
    "axis_policy.max_tick_labels": "tick-label-count",
    "legend_policy.frame": "legend-frame",
    "legend_policy.min_font_size_pt": "legend-font-size",
    "legend_policy.max_font_size_pt": "legend-font-size",
    "text_weight_policy": "text-weight-policy",
}
BUMPED_SEVERITY = "warn"
#: 底里值是 null 的字段，类型本来无从比；这几个得是数（栏宽 null = 没规定，写了就是毫米数）
KIND_OVERRIDES = {"widths_mm.single": "number", "widths_mm.double": "number"}

#: 规则键里不许覆盖的身份字段（profile 自己的身份 / 展示字段，不是规则）
_NOT_RULES = frozenset({"profile_id", "version", "label", "source", "listed"})

_URL_RE = re.compile(r"^https://[^\x00-\x20\x7f]+$")
_ANCHOR_RE = re.compile(r"^#:~:text=[^\x00-\x20\x7f]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 失败码闭集。严格同源对：`web/src/lib/journals.ts` 的 `JOURNAL_ERROR_CODES`。
ERROR_CODES = (
    "journal_catalog_schema",  # 顶层形状 / schema / catalog_rev 不对
    "journal_source_invalid",  # source 缺 url / url 非 https / kind、coverage、retrieved、page 不合法
    "journal_source_coverage",  # single_journal_sample 的来源挂在出版社 rules 上
    "journal_publisher_unknown",  # 期刊指向不存在的出版社（含类型不对）
    "journal_alias_conflict",  # 期刊 id / 别名撞车（NFKC + 小写后比）
    "journal_rule_value_missing",  # 规则没有 value（或为 null，且不是 unset）
    "journal_rule_src_missing",  # 规则没写 src
    "journal_rule_src_unknown",  # src 不在可见的 sources 里
    "journal_rule_quote_empty",  # quote 缺失 / 空白
    "journal_rule_anchor_invalid",  # anchor 不是 #:~:text= 开头的 text fragment
    "journal_rule_path_unknown",  # 键路径不是 journal-base-v1 里能覆盖的字段 / 不在 INFO_IDS
    "journal_rule_value_type",  # value / value_alt 的类型与底里该字段不符
    "journal_rule_qualifier_invalid",  # qualifier 不在闭集，或精确判据路径上写了非 exact
    "journal_rule_stage_invalid",  # stage 不在闭集
    "journal_rule_mark_invalid",  # mark 不是 quote 的子串 / derivation 为空白
    "journal_rule_unset_invalid",  # unset 的用法不对（出版社上写、带 value、出版社没这条）
    "journal_merge_invalid",  # 折叠后的结果过不了 validate_spec
    "journal_unknown",  # journal_id 不在库里
)


class JournalCatalogError(profiles_mod.ProfileError):
    """期刊库坏了 / 要的期刊不存在。`code` 是上面的闭集，界面与测试按它分流。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> JournalCatalogError:
    return JournalCatalogError(code, message)


def journals_path() -> Path:
    """期刊库 JSON 的位置：环境变量覆盖 → 包内 → 源码树（同 `profiles.profiles_path`）。"""
    override = (os.environ.get(JOURNALS_ENV) or "").strip()
    if override:
        return Path(override)
    try:
        from importlib.resources import files

        cand = Path(str(files("tavotto").joinpath("profiles", JOURNALS_FILE)))
        if cand.is_file():
            return cand
    except (ImportError, ModuleNotFoundError, TypeError, OSError):
        pass
    return Path(__file__).resolve().parent.parent / "profiles" / JOURNALS_FILE


def _read_document() -> dict:
    path = journals_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _fail("journal_catalog_schema", f"期刊库文件读不到: {path}") from exc
    except ValueError as exc:
        raise _fail("journal_catalog_schema", f"期刊库文件不是合法 JSON（{path}）: {exc}") from exc
    return data


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int_valued(value: object) -> bool:
    """整数值的数（JSON 的 `12.0` 与 `12` 同义；TS 侧 `Number.isInteger` 同判据）。"""
    return _is_number(value) and float(value).is_integer()  # type: ignore[arg-type]


def _kind(value: object) -> str:
    """JSON 类型名；bool 不是 number（与 profiles._is_number 同判据）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if _is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _non_blank(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _alias_key(name: str) -> str:
    """撞名比较的归一：NFKC 后小写（全角 / 兼容字符折成同一个）。TS 侧 `aliasKey` 同义。"""
    return unicodedata.normalize("NFKC", name).strip().lower()


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _base() -> dict:
    return profiles_mod.load(BASE_PROFILE_ID)


def _is_info(path: str) -> bool:
    return path.startswith(INFO_PREFIX)


def _check_path(path: object, base: dict, where: str) -> list[str]:
    """规则键路径：`info.<闭集 id>`，或 `journal-base-v1` 里存在、且 merge 能精确落到的字段。

    merge 是「浅合并 + `_DEEP_KEYS` 子对象深合并一层」：所以深合并子对象要写
    `<子对象>.<底里存在的键>`，其余一律是顶层单段键；写成别的形状 merge 会整体替换掉
    一个子对象，悄悄丢掉没写的兄弟键。
    """
    if not isinstance(path, str) or not path:
        raise _fail("journal_rule_path_unknown", f"{where}: 规则键必须是非空字符串")
    if _is_info(path):
        if path[len(INFO_PREFIX) :] not in INFO_IDS:
            raise _fail("journal_rule_path_unknown", f"{where}: {path} 不在 info 闭集里")
        return [path]
    parts = path.split(".")
    top = parts[0]
    if top in _NOT_RULES or top not in base:
        raise _fail(
            "journal_rule_path_unknown", f"{where}: {path} 不是 {BASE_PROFILE_ID} 里可覆盖的字段"
        )
    if top in profiles_mod._DEEP_KEYS:
        sub = base.get(top)
        if len(parts) != 2 or not isinstance(sub, dict) or parts[1] not in sub:
            raise _fail(
                "journal_rule_path_unknown",
                f"{where}: {path} 必须是 {top}.<{BASE_PROFILE_ID} 里存在的键>",
            )
    elif len(parts) != 1:
        raise _fail(
            "journal_rule_path_unknown", f"{where}: {path} 不是可深合并的子对象，只能整项覆盖 {top}"
        )
    return parts


def _base_leaf(base: dict, parts: list[str]) -> object:
    node: object = base
    for p in parts:
        node = node[p]  # type: ignore[index]
    return node


def _check_sources(sources: object, where: str) -> dict:
    if sources is None:
        return {}
    if not isinstance(sources, dict):
        raise _fail("journal_source_invalid", f"{where}: sources 必须是对象")
    for sid, src in sources.items():
        w = f"{where}.sources.{sid}"
        if not isinstance(src, dict):
            raise _fail("journal_source_invalid", f"{w}: 必须是对象")
        url = src.get("url")
        if not isinstance(url, str) or not _URL_RE.match(url) or "#" in url:
            raise _fail("journal_source_invalid", f"{w}: url 必须是不带 # 的 https:// 链接")
        if src.get("kind") not in SOURCE_KINDS:
            raise _fail("journal_source_invalid", f"{w}: kind 必须是 {'|'.join(SOURCE_KINDS)}")
        if not _is_iso_date(src.get("retrieved")):
            raise _fail("journal_source_invalid", f"{w}: retrieved 必须是 ISO 日期 YYYY-MM-DD")
        if "archive_url" in src and (
            not isinstance(src["archive_url"], str) or not _URL_RE.match(src["archive_url"])
        ):
            raise _fail("journal_source_invalid", f"{w}: archive_url 必须是 https:// 链接")
        if "coverage" in src and src["coverage"] not in COVERAGES:
            raise _fail("journal_source_invalid", f"{w}: coverage 必须是 {'|'.join(COVERAGES)}")
        page = src.get("page")
        if page is not None and not (_is_int_valued(page) and page >= 1):
            raise _fail("journal_source_invalid", f"{w}: page 必须是正整数")
    return sources


def _coverage_of(src: dict, default: str) -> str:
    return src.get("coverage", default)


def _check_rules(
    rules: object,
    visible_sources: dict,
    base: dict,
    where: str,
    *,
    publisher_scope: bool,
    publisher_rules: dict | None = None,
) -> dict:
    if rules is None:
        return {}
    if not isinstance(rules, dict):
        raise _fail("journal_catalog_schema", f"{where}: rules 必须是对象")
    for path, rule in rules.items():
        w = f"{where}.rules[{path}]"
        parts = _check_path(path, base, w)
        info = _is_info(path)
        if not isinstance(rule, dict):
            raise _fail("journal_catalog_schema", f"{w}: 规则必须是对象")
        unset = rule.get("unset")
        if "unset" in rule and unset is not True:
            raise _fail("journal_rule_unset_invalid", f"{w}: unset 只能是 true")
        if unset:
            if publisher_scope:
                raise _fail("journal_rule_unset_invalid", f"{w}: 出版社规则不能 unset")
            if rule.get("value") is not None or "value_alt" in rule:
                raise _fail("journal_rule_unset_invalid", f"{w}: unset 的规则不能带 value")
            if path not in (publisher_rules or {}):
                raise _fail("journal_rule_unset_invalid", f"{w}: 出版社没有 {path} 这条可 unset")
        elif rule.get("value") is None:
            raise _fail("journal_rule_value_missing", f"{w}: 缺 value")
        src = rule.get("src")
        if not _non_blank(src):
            raise _fail("journal_rule_src_missing", f"{w}: 缺 src（出处）")
        if src not in visible_sources:
            raise _fail("journal_rule_src_unknown", f"{w}: src={src!r} 不在 sources 里")
        if not _non_blank(rule.get("quote")):
            raise _fail("journal_rule_quote_empty", f"{w}: quote（原文摘录）不能为空")
        if "anchor" in rule and not (
            isinstance(rule["anchor"], str) and _ANCHOR_RE.match(rule["anchor"])
        ):
            raise _fail("journal_rule_anchor_invalid", f"{w}: anchor 必须以 #:~:text= 开头")
        qualifier = rule.get("qualifier", "exact")
        if qualifier not in QUALIFIERS or (path in EXACT_ONLY_PATHS and qualifier != "exact"):
            raise _fail("journal_rule_qualifier_invalid", f"{w}: qualifier={qualifier!r} 不允许")
        if rule.get("stage", "any") not in STAGES:
            raise _fail("journal_rule_stage_invalid", f"{w}: stage 必须是 {'|'.join(STAGES)}")
        if "mark" in rule and not (_non_blank(rule["mark"]) and rule["mark"] in rule["quote"]):
            raise _fail("journal_rule_mark_invalid", f"{w}: mark 必须是 quote 里的一段原文")
        if "derivation" in rule and not _non_blank(rule["derivation"]):
            raise _fail("journal_rule_mark_invalid", f"{w}: derivation 不能为空串")
        if publisher_scope and _coverage_of(visible_sources[src], "publisher_general") == (
            "single_journal_sample"
        ):
            raise _fail(
                "journal_source_coverage",
                f"{w}: single_journal_sample 的来源不能挂在出版社 rules 上",
            )
        if info or unset:
            continue
        want = KIND_OVERRIDES.get(path) or _kind(_base_leaf(base, parts))
        for key in ("value", "value_alt"):
            if key in rule and want != "null" and _kind(rule[key]) != want:
                raise _fail(
                    "journal_rule_value_type",
                    f"{w}: {key} 应为 {want}，实际 {_kind(rule[key])}",
                )
    return rules


def _fold(path: str, value: object, into: dict) -> None:
    parts = path.split(".")
    node = into
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = copy.deepcopy(value)


def _effective_rules(catalog: dict, journal_id: str) -> dict[str, dict]:
    """出版社 rules 先、期刊 rules 后覆盖；每条附上它解析到的 source、两种归属与被覆盖的出版社原值。

    `rule_scope` = 这个值是谁声明的；`source_scope` = 出处挂在谁身上（期刊的规则可以引用
    出版社的出处，那时两者不同）。
    """
    journal = catalog["journals"][journal_id]
    pub = catalog["publishers"][journal["publisher"]]
    out: dict[str, dict] = {}
    for scope, owner, fallback, default_cov in (
        ("publisher", pub, {}, "publisher_general"),
        ("journal", journal, pub.get("sources") or {}, "journal_specific"),
    ):
        own = owner.get("sources") or {}
        for path, rule in (owner.get("rules") or {}).items():
            sid = rule["src"]
            if sid in own:
                src, src_scope, cov = own[sid], scope, default_cov
            else:
                src, src_scope, cov = fallback[sid], "publisher", "publisher_general"
            out[path] = {
                "rule": rule,
                "src": src,
                "sid": sid,
                "rule_scope": scope,
                "source_scope": src_scope,
                "coverage": _coverage_of(src, cov),
                "unset": rule.get("unset") is True,
                "overrides": out.get(path),
            }
    return out


def validate_catalog(catalog: object) -> dict:
    """校验一份期刊库文档；通过原样返回，否则抛带 `code` 的 `JournalCatalogError`。

    顺序固定（先结构 → 出处 → 规则 → 折叠后整体校验），所以一份文档同时犯几种错时
    报哪一个是确定的——golden 向量里每条只犯一种。
    """
    if not isinstance(catalog, dict) or catalog.get("schema") != SCHEMA:
        raise _fail("journal_catalog_schema", f"期刊库 schema 必须是 {SCHEMA}")
    rev = catalog.get("catalog_rev")
    if not _is_int_valued(rev) or rev < 1:
        raise _fail("journal_catalog_schema", "catalog_rev 必须是正整数")
    publishers, journals = catalog.get("publishers"), catalog.get("journals")
    if not isinstance(publishers, dict) or not isinstance(journals, dict):
        raise _fail("journal_catalog_schema", "期刊库缺 publishers / journals 对象")
    base = _base()
    for pid, pub in publishers.items():
        w = f"publisher {pid}"
        if not isinstance(pub, dict) or not _non_blank(pub.get("label")):
            raise _fail("journal_catalog_schema", f"{w}: 缺 label")
        sources = _check_sources(pub.get("sources"), w)
        _check_rules(pub.get("rules"), sources, base, w, publisher_scope=True)
    seen: dict[str, str] = {}
    for jid, jr in journals.items():
        w = f"journal {jid}"
        if not isinstance(jr, dict) or not _non_blank(jr.get("label")):
            raise _fail("journal_catalog_schema", f"{w}: 缺 label")
        pub_id = jr.get("publisher")
        if not isinstance(pub_id, str) or pub_id not in publishers:
            raise _fail("journal_publisher_unknown", f"{w}: publisher={pub_id!r} 不存在")
        aliases = jr.get("aliases") or []
        if not isinstance(aliases, list) or not all(_non_blank(a) for a in aliases):
            raise _fail("journal_catalog_schema", f"{w}: aliases 必须是非空字符串数组")
        for name in (jid, *aliases):
            key = _alias_key(name)
            if key in seen and seen[key] != jid:
                raise _fail("journal_alias_conflict", f"{w}: {name!r} 与 {seen[key]} 撞名")
            seen[key] = jid
        if "issn" in jr and not _non_blank(jr["issn"]):
            raise _fail("journal_catalog_schema", f"{w}: issn 不能为空串")
        own = _check_sources(jr.get("sources"), w)
        pub = publishers[pub_id]
        pub_sources = pub.get("sources") or {}
        _check_rules(
            jr.get("rules"),
            {**pub_sources, **own},
            base,
            w,
            publisher_scope=False,
            publisher_rules=pub.get("rules") or {},
        )
    for jid in journals:
        override = _override_of(catalog, jid)
        if override:
            try:
                profiles_mod.validate_spec(
                    profiles_mod.merge_journal(base, override), BASE_PROFILE_ID
                )
            except profiles_mod.ProfileError as exc:
                raise _fail(
                    "journal_merge_invalid", f"journal {jid}: 折叠后的规范不合法: {exc}"
                ) from exc
    return catalog


def load_catalog(catalog: dict | None = None) -> dict:
    """取（并校验）期刊库。`catalog` 给定时校验它（测试 / golden 向量），否则读文件。"""
    return validate_catalog(_read_document() if catalog is None else catalog)


def _override_of(catalog: dict, journal_id: str) -> dict:
    """折成嵌套覆盖；再按 `SEVERITY_BUMP` 把期刊填了值的检查从 suggestion 抬到 warn。"""
    out: dict = {}
    folded: list[str] = []
    for path, eff in _effective_rules(catalog, journal_id).items():
        if _is_info(path) or eff["unset"]:
            continue
        _fold(path, eff["rule"]["value"], out)
        folded.append(path)
    base_sev = _base().get("severity") or {}
    for path in folded:
        check = SEVERITY_BUMP.get(path)
        if check is None or check in (out.get("severity") or {}):
            continue
        if base_sev.get(check, profiles_mod.DEFAULT_SEVERITY) == "suggestion":
            out.setdefault("severity", {})[check] = BUMPED_SEVERITY
    return out


def _require(catalog: dict, journal_id: str) -> None:
    if journal_id not in catalog["journals"]:
        raise _fail("journal_unknown", f"期刊库里没有这本期刊: {journal_id!r}")


def catalog_rev(catalog: dict | None = None) -> int:
    """库版本号（正整数）。绑定里存它，「有新版可同步」才有得比。"""
    return int(load_catalog(catalog)["catalog_rev"])


def list_journals(catalog: dict | None = None) -> list[dict]:
    """全部期刊的摘要（选择器用）。按 journal_id 排序，两侧同序。"""
    cat = load_catalog(catalog)
    out = []
    for jid in sorted(cat["journals"]):
        jr = cat["journals"][jid]
        pub = cat["publishers"][jr["publisher"]]
        out.append(
            {
                "journal_id": jid,
                "label": jr["label"],
                "publisher": jr["publisher"],
                "publisher_label": pub["label"],
                "issn": jr.get("issn", ""),
                "aliases": list(jr.get("aliases") or []),
            }
        )
    return out


def journal_override(journal_id: str, catalog: dict | None = None) -> dict:
    """某本期刊折成嵌套的 `JournalOverride`（喂 `profiles.load(BASE, journal=…)`）。

    出版社 rules 先、期刊 rules 后覆盖同一路径；期刊没写的项**不出现**在结果里；
    `info.*` 与被 unset 的项不折进来。
    """
    cat = load_catalog(catalog)
    _require(cat, journal_id)
    return _override_of(cat, journal_id)


def journal_profile(journal_id: str, catalog: dict | None = None) -> dict:
    """`journal-base-v1` + 该期刊的覆盖 = 可直接喂 preflight 的 profile。"""
    return profiles_mod.load(BASE_PROFILE_ID, journal=journal_override(journal_id, catalog))


def source_link(source: dict, anchor: str | None) -> str:
    """「打开原网页」的链接：html 用 text fragment 锚，pdf 用 `#page=N`，否则裸 url。
    严格同源对：`journals.ts` 的 `sourceLink`。"""
    url = source["url"]
    if anchor:
        return url + anchor
    if source.get("kind") == "pdf" and source.get("page"):
        return f"{url}#page={int(source['page'])}"
    return url


def _field_source(eff: dict, path: str) -> dict:
    rule, src = eff["rule"], eff["src"]
    page = src.get("page")
    return {
        "value": copy.deepcopy(rule.get("value")),
        "value_alt": copy.deepcopy(rule.get("value_alt")),
        "qualifier": rule.get("qualifier", "exact"),
        "stage": rule.get("stage", "any"),
        "mark": rule.get("mark", ""),
        "derivation": rule.get("derivation", ""),
        "unset": eff["unset"],
        "quote": rule["quote"],
        "src": eff["sid"],
        "rule_scope": eff["rule_scope"],
        "source_scope": eff["source_scope"],
        "coverage": eff["coverage"],
        "affects": "info" if _is_info(path) else "preflight",
        "url": src["url"],
        "link": source_link(src, rule.get("anchor")),
        "anchor": rule.get("anchor", ""),
        "kind": src["kind"],
        "page": None if page is None else int(page),
        "retrieved": src["retrieved"],
        "archive_url": src.get("archive_url", ""),
    }


def journal_sources(journal_id: str, catalog: dict | None = None) -> dict[str, dict]:
    """每条生效字段 → 出处（前端出处卡与 proof report 用），按路径排序。

    `affects` 是 `preflight`（折进覆盖、参与预检）或 `info`（仅供参考）；`rule_scope`
    说值是出版社还是期刊声明的，`source_scope` 说出处挂在谁身上；期刊改写了出版社的
    值时 `overrides` 带出版社原条目（「出版社默认 90，该刊改为 85」），否则为 None。
    被期刊 `unset` 的出版社规则也在这里（`unset: true`，不参与预检）。
    """
    cat = load_catalog(catalog)
    _require(cat, journal_id)
    out: dict[str, dict] = {}
    for path, eff in sorted(_effective_rules(cat, journal_id).items()):
        item = _field_source(eff, path)
        prev = eff["overrides"]
        item["overrides"] = None if prev is None else _field_source(prev, path)
        out[path] = item
    return out
