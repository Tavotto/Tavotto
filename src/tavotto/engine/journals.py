"""内置期刊标准库 —— 期刊 = 覆盖 + 出处（ADR 0154）。

数据的唯一权威是 `src/tavotto/profiles/journals.json`（随 wheel 分发）；TypeScript 侧
`web/src/lib/journals.ts` 经 `@journals` 别名读**同一个文件**，并实现同名同义的加载 /
校验 / 折叠。两侧靠 `tests/golden/journal_vectors.json` 对齐（pytest 与 vitest 各跑
一遍，`scripts/gen_journal_vectors.py` 生成）。

结构（schema 1）::

    publishers{ pid: { label, sources{ sid: {url, kind, retrieved, archive_url?, page?} },
                       rules{ "<profile 字段路径>": {value, src, quote, anchor?} } } }
    journals  { jid: { label, publisher, issn?, aliases?, sources?, rules? } }

**期刊只记它自己写了的项**：规则键直接是 `journal-base-v1` 里存在的字段路径
（`widths_mm.double`、`min_raster_dpi`……），折成 `JournalOverride` 后走
`profiles.merge_journal()`——求值器一行不改。期刊没写的项不补、不拿课题组规范去填
（「该期刊未规定」是界面的事实，不是缺省值）。`quote` 只存一两句短摘录。

本模块只做加载 / 校验 / 折叠，纯标准库；依赖 `profiles`（单向，不反向 import）。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import profiles as profiles_mod

JOURNALS_FILE = "journals.json"
#: 覆盖整份期刊库文件的位置（测试 / 企业自带一套时用；与 `TAVOTTO_PROFILES_FILE` 同款）
JOURNALS_ENV = "TAVOTTO_JOURNALS_FILE"
#: 期刊覆盖叠在它上面（不是叠在课题组规范上）
BASE_PROFILE_ID = "journal-base-v1"
SCHEMA = 1
SOURCE_KINDS = ("html", "pdf")

#: 规则键里不许覆盖的身份字段（profile 自己的身份 / 展示字段，不是规则）
_NOT_RULES = frozenset({"profile_id", "version", "label", "source", "listed"})

#: 失败码闭集。严格同源对：`web/src/lib/journals.ts` 的 `JOURNAL_ERROR_CODES`。
ERROR_CODES = (
    "journal_catalog_schema",  # 顶层形状 / schema 版本不对
    "journal_source_invalid",  # source 缺 url / kind 不合法 / retrieved 空 / url 带 #
    "journal_publisher_unknown",  # 期刊指向不存在的出版社
    "journal_alias_conflict",  # 期刊 id / 别名撞车
    "journal_rule_value_missing",  # 规则没有 value（或为 null）
    "journal_rule_src_missing",  # 规则没写 src
    "journal_rule_src_unknown",  # src 不在可见的 sources 里
    "journal_rule_quote_empty",  # quote 缺失 / 空白
    "journal_rule_path_unknown",  # 键路径不是 journal-base-v1 里能覆盖的字段
    "journal_rule_value_type",  # value 的类型与底里该字段不符
    "journal_merge_invalid",  # 折叠后的结果过不了 validate_spec
    "journal_unknown",  # journal_id 不在库里
)


class JournalCatalogError(profiles_mod.ProfileError):
    """期刊库坏了 / 要的期刊不存在。`code` 是上面的闭集，界面与测试按它分流。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
        raise JournalCatalogError("journal_catalog_schema", f"期刊库文件读不到: {path}") from exc
    except ValueError as exc:
        raise JournalCatalogError(
            "journal_catalog_schema", f"期刊库文件不是合法 JSON（{path}）: {exc}"
        ) from exc
    return data


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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


def _base() -> dict:
    return profiles_mod.load(BASE_PROFILE_ID)


def _split_path(path: str) -> list[str]:
    return path.split(".")


def _check_path(path: object, base: dict, where: str) -> list[str]:
    """规则键路径必须是 journal-base-v1 里**存在、且 merge 能精确落到**的字段。

    merge 是「浅合并 + `_DEEP_KEYS` 子对象深合并一层」：所以深合并子对象要写
    `<子对象>.<键>`（键须在底里存在），其余一律是顶层单段键；写成别的形状
    merge 会整体替换掉一个子对象，悄悄丢掉没写的兄弟键。
    """
    if not isinstance(path, str) or not path:
        raise JournalCatalogError("journal_rule_path_unknown", f"{where}: 规则键必须是非空字符串")
    parts = _split_path(path)
    top = parts[0]
    if top in _NOT_RULES or top not in base:
        raise JournalCatalogError(
            "journal_rule_path_unknown", f"{where}: {path} 不是 {BASE_PROFILE_ID} 里可覆盖的字段"
        )
    if top in profiles_mod._DEEP_KEYS:
        sub = base.get(top)
        if len(parts) != 2 or not isinstance(sub, dict) or parts[1] not in sub:
            raise JournalCatalogError(
                "journal_rule_path_unknown",
                f"{where}: {path} 必须是 {top}.<{BASE_PROFILE_ID} 里存在的键>",
            )
    elif len(parts) != 1:
        raise JournalCatalogError(
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
        raise JournalCatalogError("journal_source_invalid", f"{where}: sources 必须是对象")
    for sid, src in sources.items():
        w = f"{where}.sources.{sid}"
        if not isinstance(src, dict):
            raise JournalCatalogError("journal_source_invalid", f"{w}: 必须是对象")
        url = src.get("url")
        if not _non_blank(url) or "#" in url:
            raise JournalCatalogError("journal_source_invalid", f"{w}: url 必须非空且不带 #")
        if src.get("kind") not in SOURCE_KINDS:
            raise JournalCatalogError(
                "journal_source_invalid", f"{w}: kind 必须是 {'|'.join(SOURCE_KINDS)}"
            )
        if not _non_blank(src.get("retrieved")):
            raise JournalCatalogError("journal_source_invalid", f"{w}: 缺 retrieved（采集日期）")
        if "archive_url" in src and not _non_blank(src["archive_url"]):
            raise JournalCatalogError("journal_source_invalid", f"{w}: archive_url 不能为空串")
        page = src.get("page")
        if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1):
            raise JournalCatalogError("journal_source_invalid", f"{w}: page 必须是正整数")
    return sources


def _check_rules(rules: object, visible_sources: dict, base: dict, where: str) -> dict:
    if rules is None:
        return {}
    if not isinstance(rules, dict):
        raise JournalCatalogError("journal_catalog_schema", f"{where}: rules 必须是对象")
    for path, rule in rules.items():
        w = f"{where}.rules[{path}]"
        parts = _check_path(path, base, w)
        if not isinstance(rule, dict):
            raise JournalCatalogError("journal_catalog_schema", f"{w}: 规则必须是对象")
        if rule.get("value") is None:
            raise JournalCatalogError("journal_rule_value_missing", f"{w}: 缺 value")
        src = rule.get("src")
        if not _non_blank(src):
            raise JournalCatalogError("journal_rule_src_missing", f"{w}: 缺 src（出处）")
        if src not in visible_sources:
            raise JournalCatalogError(
                "journal_rule_src_unknown", f"{w}: src={src!r} 不在 sources 里"
            )
        if not _non_blank(rule.get("quote")):
            raise JournalCatalogError("journal_rule_quote_empty", f"{w}: quote（原文摘录）不能为空")
        if "anchor" in rule and not _non_blank(rule["anchor"]):
            raise JournalCatalogError("journal_catalog_schema", f"{w}: anchor 不能为空串")
        want = _kind(_base_leaf(base, parts))
        if want != "null" and _kind(rule["value"]) != want:
            raise JournalCatalogError(
                "journal_rule_value_type", f"{w}: value 应为 {want}，实际 {_kind(rule['value'])}"
            )
    return rules


def _fold(path: str, value: object, into: dict) -> None:
    parts = _split_path(path)
    node = into
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = copy.deepcopy(value)


def _effective_rules(catalog: dict, journal_id: str) -> dict[str, dict]:
    """出版社 rules 先、期刊 rules 后覆盖；每条附上它解析到的 source 与来源范围。"""
    journal = catalog["journals"][journal_id]
    pub = catalog["publishers"][journal["publisher"]]
    out: dict[str, dict] = {}
    for scope, owner, fallback in (
        ("publisher", pub, {}),
        ("journal", journal, pub.get("sources") or {}),
    ):
        own = owner.get("sources") or {}
        for path, rule in (owner.get("rules") or {}).items():
            sid = rule["src"]
            if sid in own:
                src, src_scope = own[sid], scope
            else:
                src, src_scope = fallback[sid], "publisher"
            out[path] = {"rule": rule, "src": src, "sid": sid, "scope": src_scope}
    return out


def validate_catalog(catalog: object) -> dict:
    """校验一份期刊库文档；通过原样返回，否则抛带 `code` 的 `JournalCatalogError`。

    顺序固定（先结构 → 出处 → 规则 → 折叠后整体校验），所以一份文档同时犯几种错时
    报哪一个是确定的——golden 向量里每条只犯一种。
    """
    if not isinstance(catalog, dict) or catalog.get("schema") != SCHEMA:
        raise JournalCatalogError("journal_catalog_schema", f"期刊库 schema 必须是 {SCHEMA}")
    publishers, journals = catalog.get("publishers"), catalog.get("journals")
    if not isinstance(publishers, dict) or not isinstance(journals, dict):
        raise JournalCatalogError("journal_catalog_schema", "期刊库缺 publishers / journals 对象")
    base = _base()
    for pid, pub in publishers.items():
        w = f"publisher {pid}"
        if not isinstance(pub, dict) or not _non_blank(pub.get("label")):
            raise JournalCatalogError("journal_catalog_schema", f"{w}: 缺 label")
        sources = _check_sources(pub.get("sources"), w)
        _check_rules(pub.get("rules"), sources, base, w)
    seen: dict[str, str] = {}
    for jid, jr in journals.items():
        w = f"journal {jid}"
        if not isinstance(jr, dict) or not _non_blank(jr.get("label")):
            raise JournalCatalogError("journal_catalog_schema", f"{w}: 缺 label")
        if jr.get("publisher") not in publishers:
            raise JournalCatalogError(
                "journal_publisher_unknown", f"{w}: publisher={jr.get('publisher')!r} 不存在"
            )
        aliases = jr.get("aliases") or []
        if not isinstance(aliases, list) or not all(_non_blank(a) for a in aliases):
            raise JournalCatalogError(
                "journal_catalog_schema", f"{w}: aliases 必须是非空字符串数组"
            )
        for name in (jid, *aliases):
            key = name.strip().lower()
            if key in seen and seen[key] != jid:
                raise JournalCatalogError(
                    "journal_alias_conflict", f"{w}: {name!r} 与 {seen[key]} 撞名"
                )
            seen[key] = jid
        if "issn" in jr and not _non_blank(jr["issn"]):
            raise JournalCatalogError("journal_catalog_schema", f"{w}: issn 不能为空串")
        own = _check_sources(jr.get("sources"), w)
        pub_sources = publishers[jr["publisher"]].get("sources") or {}
        _check_rules(jr.get("rules"), {**pub_sources, **own}, base, w)
    for jid in journals:
        override = _override_of(catalog, jid)
        if override:
            try:
                profiles_mod.validate_spec(
                    profiles_mod.merge_journal(base, override), BASE_PROFILE_ID
                )
            except profiles_mod.ProfileError as exc:
                raise JournalCatalogError(
                    "journal_merge_invalid", f"journal {jid}: 折叠后的规范不合法: {exc}"
                ) from exc
    return catalog


def load_catalog(catalog: dict | None = None) -> dict:
    """取（并校验）期刊库。`catalog` 给定时校验它（测试 / golden 向量），否则读文件。"""
    return validate_catalog(_read_document() if catalog is None else catalog)


def _override_of(catalog: dict, journal_id: str) -> dict:
    out: dict = {}
    for path, eff in _effective_rules(catalog, journal_id).items():
        _fold(path, eff["rule"]["value"], out)
    return out


def _require(catalog: dict, journal_id: str) -> None:
    if journal_id not in catalog["journals"]:
        raise JournalCatalogError("journal_unknown", f"期刊库里没有这本期刊: {journal_id!r}")


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

    出版社 rules 先、期刊 rules 后覆盖同一路径；期刊没写的项**不出现**在结果里。
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
        return f"{url}#page={source['page']}"
    return url


def journal_sources(journal_id: str, catalog: dict | None = None) -> dict[str, dict]:
    """每条生效字段 → 出处（前端出处卡与 proof report 用）。

    `{路径: {value, quote, src, scope, url, link, anchor, kind, page, retrieved,
    archive_url}}`；`scope` 说明这条出处挂在出版社还是期刊上。按路径排序。
    """
    cat = load_catalog(catalog)
    _require(cat, journal_id)
    out: dict[str, dict] = {}
    for path, eff in sorted(_effective_rules(cat, journal_id).items()):
        rule, src = eff["rule"], eff["src"]
        out[path] = {
            "value": copy.deepcopy(rule["value"]),
            "quote": rule["quote"],
            "src": eff["sid"],
            "scope": eff["scope"],
            "url": src["url"],
            "link": source_link(src, rule.get("anchor")),
            "anchor": rule.get("anchor", ""),
            "kind": src["kind"],
            "page": src.get("page"),
            "retrieved": src["retrieved"],
            "archive_url": src.get("archive_url", ""),
        }
    return out
