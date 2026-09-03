"""设置页的「安装 Codex 集成」按钮（issue #170，ADR 0012）。

这些判据全部**站在两侧之外**（Python 读 Rust 与 TypeScript 的源码），因为它们要
证明的都是「两处说的是同一件事」——那种事在任何一侧的单测里都各自绿：

* **`#[tauri::command]` 的 ACL 漏配是静默失败。** 三处（`build.rs` 的
  `AppManifest::commands`、`capabilities/main.json` 的 `allow-<命令名>`、
  `main.rs` 的 `generate_handler`）少一处，invoke 会被**直接拒**，界面上是
  「点了没反应」——reveal_export 那次就是这么坏的。这里**枚举** main.rs 里所有
  `#[tauri::command]`，不是给某个命令写一条白名单：白名单挡不住下一个新命令。
* **安装器只有一份**（ADR 0012 的「不写第二套安装器」）。按钮的全部动作是 spawn
  `tavotto-cli codex <action> --json`；安装步骤的字面量（marketplace 源、插件
  引用、sparse 路径）在壳与前端里出现一次都算第二权威。那些字面量从
  `engine/brand.py` **现取**——写死一份在这里，本文件自己就成了第三处。
* **失败按 code 翻译，不透传英文 code**（与 #76 的 `unsupported_props` 同一条
  纪律）。齐全性从引擎的 `ERR_*` 与 `_step()` 枚举出来比，两门语言都要有。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAURI = ROOT / "src-tauri"
LOCALES = ROOT / "web" / "src" / "i18n" / "locales"

pytestmark = pytest.mark.skipif(
    not (TAURI / "src" / "main.rs").is_file(),
    reason="没有 src-tauri/（wheel/sdist 里不含桌面壳）",
)

#: 文案在 dialogs 的这一节下（前端 `lib/codexInstall.ts` 的 `ci()` 同一前缀）
TEXT_PREFIX = ("settings", "agents", "codexInstall")


def _read(*parts: str) -> str:
    return (TAURI.joinpath(*parts)).read_text(encoding="utf-8")


def _texts(locale: str) -> dict:
    data = json.loads((LOCALES / locale / "dialogs.json").read_text(encoding="utf-8"))
    for part in TEXT_PREFIX:
        data = data.get(part, {})
    return data


# --------------------------------------------------------------------------- #
# ACL 三处同步（枚举，不是白名单）
# --------------------------------------------------------------------------- #
def _declared_commands() -> list[str]:
    """main.rs 里所有 `#[tauri::command]` 的函数名。"""
    src = _read("src", "main.rs")
    names = re.findall(r"#\[tauri::command\]\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", src)
    assert names, "一个 #[tauri::command] 都没找到——正则或文件结构变了，这道门禁已经空了"
    return names


def test_every_tauri_command_is_declared_in_all_three_places():
    """三处缺一，invoke 就被静默拒绝。**枚举所有命令**，不给某一个写白名单。"""
    build_rs = _read("build.rs")
    main_rs = _read("src", "main.rs")
    cap = json.loads(_read("capabilities", "main.json"))
    handler = main_rs.split("generate_handler![")[1].split("]")[0]

    for name in _declared_commands():
        assert f'"{name}"' in build_rs, f"build.rs 的 AppManifest::commands 里没有 {name}"
        allow = "allow-" + name.replace("_", "-")
        assert allow in cap["permissions"], f"capabilities/main.json 没放行 {name}（{allow}）"
        assert re.search(rf"\b{name}\b", handler), f"generate_handler 里没有 {name}"


def test_the_codex_button_has_a_command_at_all():
    """上一条是「所有命令都齐」，这条钉的是**这个命令存在**——否则删掉它也全绿。"""
    assert "codex_integration" in _declared_commands()


# --------------------------------------------------------------------------- #
# 按钮走的就是那条 CLI（ADR 0012：不写第二套安装器）
# --------------------------------------------------------------------------- #
def test_the_button_spawns_the_engine_cli_with_json():
    """壳只 spawn `tavotto-cli codex <action> --json`，别的什么都不做。"""
    main_rs = _read("src", "main.rs")
    body = main_rs.split("async fn codex_integration")[1].split("\n}\n")[0]
    assert '.arg("codex")' in body, "没有 codex 子命令——那就不是在跑那条安装器"
    assert ".arg(&action)" in body
    assert '.arg("--json")' in body, "不带 --json 的话拿回来的是给人读的文本，不是契约"
    assert "resolve_cli" in body, "没解析 tavotto-cli；GUI 的那个 exe 拿不到 stdout"


def test_the_action_is_a_closed_set():
    """
    `action` 从 webview 来，必须是闭集。放开等于把任意 argv 交给一个能装东西的
    进程；顺带也挡住 `uninstall`——卸载不该是一个按得动的按钮。
    """
    main_rs = _read("src", "main.rs")
    body = main_rs.split("async fn codex_integration")[1].split("\n}\n")[0]
    assert 'action != "install" && action != "doctor"' in body, "action 不是闭集"
    assert '"uninstall"' not in body


# --------------------------------------------------------------------------- #
# 单一权威：安装步骤的字面量只在引擎那一份里
# --------------------------------------------------------------------------- #
def _install_step_literals() -> set[str]:
    """**从 brand.py 现取**：marketplace 源、插件引用，加上携带它们的那个旗标。

    `CODEX_SPARSE_PATHS` 刻意**不在**这个集合里：它们是仓库里的目录名
    （`.agents/plugins` / `codex-plugin`），别处提到它们是正当的——导出来源标签
    就叫 `codex-plugin`。把目录名变成一个安装步骤的是它旁边的 `--sparse`，
    所以钉的是那个旗标。范围宽过它要守的东西，只会被 routinely 违反到没人当回事。
    """
    from tavotto.engine import brand

    return {brand.CODEX_MARKETPLACE, brand.CODEX_PLUGIN_REF, "--sparse"}


#: 粗糙但够用的字符串字面量：单/双引号与反引号，不跨行、不含转义。
#: **按整段相等比**，不是子串——`https://github.com/Tavotto/Tavotto` 这类
#: 仓库地址里天然含 `Tavotto/Tavotto`，子串匹配会把它们全报成第二套安装器。
_LITERAL = re.compile(r'"([^"\\\n]*)"' + r"|'([^'\\\n]*)'" + r"|`([^`\\\n]*)`")

#: 生成物不参与扫描：`resources.d.ts` 是 i18n 类型的生成结果，里面必然出现
#: 文案表的每一个 key。把它放进来，这道门禁的每一次红都来自生成物。
_GENERATED = {"resources.d.ts", "canvas.html"}

_SCAN_ROOTS = (
    ("web/src", (".ts", ".tsx")),
    ("src-tauri/src", (".rs",)),
    ("src/tavotto", (".py",)),
)

#: 唯一允许出现这些字面量的两份源码：安装器本身，与它取常量的那份品牌表。
_INSTALLER = {"src/tavotto/engine/codexinstall.py", "src/tavotto/engine/brand.py"}


def _scan_files():
    for root, exts in _SCAN_ROOTS:
        for f in sorted((ROOT / root).rglob("*")):
            if not f.is_file() or f.suffix not in exts or f.name in _GENERATED:
                continue
            rel = f.relative_to(ROOT).as_posix()
            if rel.endswith((".test.ts", ".test.tsx", ".test.py")):
                continue
            yield rel, f.read_text(encoding="utf-8", errors="replace")


def test_install_step_literals_live_only_in_the_engine():
    """
    ADR 0012 的关闭条件第 1 条：**不存在第二套安装逻辑**。

    「按钮 spawn 那条命令」这件事在实现里天然成立（安装器只有一份），可它挡不住
    下一个人在 Rust 或 React 里「顺手也拼一条 marketplace add」。这条判据挡的
    就是那一步：安装步骤的字面量在引擎之外出现一次就红。
    """
    literals = _install_step_literals()
    offenders: list[str] = []
    for rel, text in _scan_files():
        if rel in _INSTALLER:
            continue
        for m in _LITERAL.finditer(text):
            value = next(g for g in m.groups() if g is not None)
            if value in literals:
                offenders.append(f"{rel}: {value!r}")
    assert not offenders, "安装步骤的字面量出现在引擎之外（第二套安装器）：\n" + "\n".join(
        offenders
    )


def test_no_one_assembles_a_codex_install_command_line():
    """整条命令拼成一个字符串也是第二权威——上一条按字面量比，漏得掉这种形状。"""
    offenders = [
        rel
        for rel, text in _scan_files()
        if rel not in _INSTALLER
        and ("plugin marketplace add" in text or "plugin add tavotto" in text)
    ]
    assert not offenders, f"这些文件里拼了一条 codex 安装命令：{offenders}"


# --------------------------------------------------------------------------- #
# 每个 error_code / step 都有中英文文案（从引擎枚举，不抄清单）
# --------------------------------------------------------------------------- #
def _engine_error_codes() -> set[str]:
    from tavotto.engine import codexinstall

    codes = {
        v for k, v in vars(codexinstall).items() if k.startswith("ERR_") and isinstance(v, str)
    }
    assert len(codes) >= 5, f"引擎的 ERR_* 只找到 {codes}——枚举失效了，这道门禁已经空了"
    return codes


def _engine_step_names() -> set[str]:
    src = (ROOT / "src" / "tavotto" / "engine" / "codexinstall.py").read_text(encoding="utf-8")
    names = set(re.findall(r'_step\(\s*"(\w+)"', src))
    assert names, '一个 _step("…") 都没找到——正则失效了，这道门禁已经空了'
    return names


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_engine_error_code_has_text(locale: str):
    """
    界面上出现的必须是**翻译过的原因**，不是 `codex_cli_missing` 这串下划线
    （#76 的 `unsupported_props` 同一条纪律）。少一条文案，前端会落到 `other`
    的兜底句上——用户看到的是一句正确但没用的话，而没有任何红灯。
    """
    texts = _texts(locale).get("error", {})
    missing = sorted(_engine_error_codes() - set(texts))
    assert not missing, f"{locale} 缺这些 error_code 的文案：{missing}"
    assert "other" in texts, "缺兜底文案，认不出的 code 会把英文原样甩到界面上"
    empty = sorted(k for k, v in texts.items() if not str(v).strip())
    assert not empty, f"{locale} 这些文案是空的：{empty}"


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_every_engine_step_has_a_label(locale: str):
    """步骤名同理：前端查不到会原样显示 `codex_cli`。"""
    labels = _texts(locale).get("step", {})
    missing = sorted(_engine_step_names() - set(labels))
    assert not missing, f"{locale} 缺这些步骤的文案：{missing}"


def test_the_missing_codex_reason_carries_the_searched_locations():
    """
    引擎把「找过哪些位置」放在 `detail` 里；界面必须把它显示出来——只说
    「找不到 codex」对一个把它装在别处的用户什么忙都帮不上。
    """
    src = (ROOT / "web" / "src" / "lib" / "codexInstall.ts").read_text(encoding="utf-8")
    body = src.split("export function codexErrorText")[1]
    assert "codex_cli_missing" in body and "detail" in body, (
        "codexErrorText 没把 codex_cli_missing 的 detail（找过哪些位置）带出来"
    )
