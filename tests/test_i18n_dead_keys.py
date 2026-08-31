"""errors.json 的**反向**扫描：每个键都得有一个发射点。

现有两道相关门禁都是单向的：

* `test_error_codes.py` 只查「发射的码有没有翻译」，不查「翻译键有没有发射点」；
* `pnpm i18n:check` 只对 `resources.d.ts` 的生成一致性负责，与发射点无关。

于是一个**认真但错误**的三方冲突解法可以通过全部门禁（#134 的现场）：把已无任何
发射点的 `unknown_agent` 键按「取并集」塞回 errors.json → `test_error_codes` 静默
放行；`i18n:check` 红，但红的理由是 types 未重新生成——老老实实重生成后即全绿。

死键不只是卫生问题。三方冲突里（main 有 / A 保留 / B 删除并替换）**正确解法依赖
「读源码数发射点」这个人肉步骤**；换个没做这一步的人，死键就静默复活。

## 判「有发射点」的三种写法

| 写法 | 例子 | 匹配 |
|---|---|---|
| 引号字面量 | `{"code": "no_project"}` | `"no_project"` / `'no_project'` |
| i18n 键路径 | `t('errors:update.checkFailed')` | 子路径 `update.checkFailed` |
| 模板串前缀 | `` en(`sourceLabel.${env.source}`) `` | 见 `_DYNAMIC_CONTAINERS` |

## 扫描范围里两个不许算数的文件

* `web/src/i18n/resources.d.ts` 是**从 locales 生成的**，里面含全部键——把它算进
  扫描范围，这道门禁就恒绿（一开始就是这么写的，自检时当场发现）；
* `codex-plugin/mcp/widget/canvas.html` 是受管产物，同理。

反过来，`codex-plugin/mcp/tavotto_mcp/` **必须**在范围内：MCP server 是
`errors:preflight.*` 的第二个发射点（`exportRasterDpi` 只在那儿发）。少扫一个真
发射点，门禁就会把活键报成死键——那比漏报更糟，它会逼人删掉正在用的文案。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

pytestmark = pytest.mark.skipif(
    not (LOCALES / "zh-CN" / "errors.json").is_file(),
    reason="没有 web/（wheel/sdist 里不含前端源码）",
)

#: **按容器**豁免：键由代码动态拼出来（`` en(`sourceLabel.${env.source}`) ``），
#: 逐键的字面量扫描看不见它们。
#:
#: 但豁免不是「整片放行」——那样某个取值被删掉之后，它的文案会静默变成死键。
#: 每个容器必须配一个**闭集出处**：子键要与那个出处逐字相等，多一个少一个都红。
#: （第一版写的是「子键的闭集由 TS 联合类型看护，少一个当场类型错误」——不成立：
#: `en()` 收的是普通 string，没有任何类型把联合成员映到文案对象的子键上。
#: Codex 在 PR #158 上指出，成立。）
_DYNAMIC_CONTAINERS: dict[str, str] = {
    "engine.sourceLabel": "`EngineEnvironmentCard.tsx` / `PrivacyAboutSettings.tsx` 用 "
    "en(`sourceLabel.${env.source || 'unknown'}`) 拼；闭集出处是 "
    "`web/src/lib/api.ts` 的 EngineSource 联合类型（空串记作 unknown）。",
}

#: 容器 → 闭集的取法。返回子键应有的**精确集合**。
_CLOSED_SETS = {
    "engine.sourceLabel": lambda: _engine_source_values(),
}


def _engine_source_values() -> set[str]:
    """从 `web/src/lib/api.ts` 现取 `EngineSource` 的成员（空串记作 unknown）。

    现取而不是在这里再抄一份：抄一份就又多了一个会漂的出处。
    """
    src = (ROOT / "web" / "src" / "lib" / "api.ts").read_text(encoding="utf-8")
    body = src.split("export type EngineSource =", 1)[1]
    body = body.split("\n\n", 1)[0]
    members = set(re.findall(r"\|\s*'([a-z_]*)'", body))
    assert members, "没解析出 EngineSource 的成员——判据本身坏了"
    return {m or "unknown" for m in members}


#: **按键**豁免（[[exemption-granularity]]：按键不按文件——整文件豁免会把它内部
#: 的回归一起放行）。目前是空的：2026-08-27 首轮全量清点，162 个节点零死键。
#: 新增条目必须写明「为什么没有发射点」与「什么时候能删掉这条豁免」。
_EXEMPT: dict[str, str] = {}


def _load(locale: str) -> dict:
    return json.loads((LOCALES / locale / "errors.json").read_text(encoding="utf-8"))


def _leaves(table: dict, prefix: str = "") -> list[str]:
    out: list[str] = []
    for key, value in table.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.append(path)  # 容器本身也可能是动态拼接的落点
            out.extend(_leaves(value, path))
        else:
            out.append(path)
    return out


def _sources() -> str:
    parts: list[str] = []
    for p in (ROOT / "src" / "tavotto").rglob("*.py"):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    for p in (ROOT / "src" / "tavotto" / "profiles").rglob("*.json"):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    for p in (ROOT / "codex-plugin").rglob("*.py"):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    for p in (ROOT / "web" / "src").rglob("*"):
        if not p.is_file() or p.suffix not in {".ts", ".tsx"}:
            continue
        s = p.as_posix()
        if "i18n/locales" in s or s.endswith("resources.d.ts"):
            continue  # 生成物：算进来这道门禁就恒绿
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


#: i18next 的复数形态后缀。带 count 的 key 由 i18next **在运行时**补上后缀，
#: 源码里出现的是**基名**（`pr('fixAll', { count })` 发的是 `fixAll_other`）。
#: 剥掉后缀再找一次基名——这不是放宽，基名本身仍然必须有真发射点。
_PLURAL_SUFFIXES = ("_zero", "_one", "_two", "_few", "_many", "_other")


def _stem(leaf: str) -> str:
    for suffix in _PLURAL_SUFFIXES:
        if leaf.endswith(suffix) and len(leaf) > len(suffix):
            return leaf[: -len(suffix)]
    return leaf


def _has_emitter(path: str, blob: str) -> bool:
    """这个键在源码里有发射点吗？

    两种形状，**都要求引号或完整键路径**：

    * 引号字面量 `"no_project"` / `'rasterDpi'` —— Python 的错误码、以及前端
      那些自带命名空间前缀的短助手（`pf('rasterDpi')` 里 pf 已经补了
      `preflight.`）；
    * **完整键路径** `update.checkFailed` —— `t('errors:update.checkFailed')`
      这种写全的用法。

    刻意**不**接受「剥掉第一段之后的裸子串」：那样 `backend.checkFailed` 会被
    `errors:update.checkFailed` 里的 `checkFailed` 喂活，一个死键就能靠另一个
    命名空间的活键蒙混过关（Codex 在 PR #158 上指出，成立）。

    **复数形态例外**：`fixAll_one` / `fixAll_other` 在源码里永远找不到——后缀
    是 i18next 按 `count` 在运行时补的。所以带这类后缀的叶子额外用**基名**
    再找一次；基名没有发射点的话照样报死键。
    """
    leaf = path.rsplit(".", 1)[-1]
    candidates = {leaf, _stem(leaf)}
    stem_path = path.rsplit(".", 1)[0] + "." + _stem(leaf) if "." in path else _stem(leaf)
    for name in candidates:
        if f'"{name}"' in blob or f"'{name}'" in blob or f"`{name}`" in blob or f"`{name}." in blob:
            return True
    return path in blob or stem_path in blob


def _all_paths() -> list[str]:
    # 两种语言取并集：只死在一侧的键同样是死键
    paths: set[str] = set()
    for locale in ("zh-CN", "en-US"):
        paths.update(_leaves(_load(locale)))
    return sorted(paths)


def test_every_errors_json_key_has_an_emitter():
    """errors.json 的每个键都能在源码里找到至少一处使用。"""
    blob = _sources()
    covered = tuple(f"{c}." for c in _DYNAMIC_CONTAINERS)
    dead = [
        p
        for p in _all_paths()
        if p not in _EXEMPT
        and not p.startswith(covered)  # 子键由闭集用例逐个核对，见下
        and not _has_emitter(p, blob)
    ]
    assert not dead, (
        "errors.json 里这些键没有任何发射点（合并冲突时盲目取并集就是这么留下的）：\n"
        + "\n".join(f"  {p}" for p in dead)
        + "\n修掉它，或加进 _EXEMPT 并写明理由。"
    )


def test_the_scan_range_is_not_quietly_hollow():
    """自检：扫描范围既不许含生成物，也不许漏掉真发射点。

    从没被自己的判据验过的扫描很容易恒绿——本文件第一版就把生成的
    `resources.d.ts` 算进了范围，于是**每个键都「有发射点」**，162 个节点零死键，
    看上去一切正常。判据里三条各自独立，缺一条就是一种静默失灵：

    * 生成物进了范围 → 门禁恒绿（漏报）；
    * MCP server 出了范围 → 活键被报成死键（误报，比漏报更糟：它逼人删掉正在
      用的文案）；
    * 判据本身失灵 → 连真发射点都认不出来。
    """
    blob = _sources()
    assert "automatically generated by i18next-cli" not in blob, (
        "生成的 resources.d.ts 进了扫描范围——它含全部键，这道门禁会恒绿"
    )
    assert "def export_raster_issues(" in blob, (
        "codex-plugin 的 MCP server 掉出了扫描范围——它是 errors:preflight.* 的第二个发射点"
    )
    assert _has_emitter("backend.no_project", blob), "连真发射点都找不到，判据坏了"
    assert not _has_emitter("backend.zzz_no_such_error_code_xyz", blob)
    # 复数后缀的剥离**不是万能钥匙**：基名没有发射点的照样是死键
    assert not _has_emitter("backend.zzz_no_such_error_code_xyz_other", blob)
    assert _has_emitter("problems.fixAll_other", blob), "复数形态的基名有发射点却被报成死键"


def test_every_exemption_carries_a_reason():
    """豁免必须写理由——没有理由的豁免下次就没人敢删。"""
    for table, what in ((_EXEMPT, "键"), (_DYNAMIC_CONTAINERS, "动态容器")):
        blank = sorted(k for k, v in table.items() if len(str(v).strip()) < 10)
        assert not blank, f"这些{what}豁免没写理由：{blank}"


def test_dynamic_container_children_match_their_closed_set():
    """动态容器的子键必须与闭集出处**逐字相等**。

    整片豁免会放过两种死法：取值被删/改名而文案留着（死键），以及文案漏写而
    取值仍在（界面显示原始 code）。两边都要红。
    """
    for path, closed in _CLOSED_SETS.items():
        expected = closed()
        for locale in ("zh-CN", "en-US"):
            node: object = _load(locale)
            for part in path.split("."):
                node = node[part]  # type: ignore[index]
            assert isinstance(node, dict)
            assert set(node) == expected, (
                f"{locale} 的 {path} 子键与闭集对不上："
                f"多出 {sorted(set(node) - expected)}，缺 {sorted(expected - set(node))}"
            )


def test_dynamic_containers_are_really_containers():
    """动态容器豁免只对**真的存在且真是容器**的路径成立。

    键被删掉或从容器降成叶子之后，那条豁免就变成一张盖住整片区域的空头支票。
    """
    tables = {locale: _load(locale) for locale in ("zh-CN", "en-US")}
    for path in _DYNAMIC_CONTAINERS:
        for locale, table in tables.items():
            node: object = table
            for part in path.split("."):
                assert isinstance(node, dict) and part in node, (
                    f"{locale}: 动态容器豁免 {path} 指向一个不存在的键"
                )
                node = node[part]
            assert isinstance(node, dict) and node, f"{locale}: {path} 已经不是容器了，这条豁免该删"
