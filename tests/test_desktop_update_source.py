"""桌面应用内更新的来源编排（ADR 0121）里**跨语言、跨文件**的那几条同源关系。

Rust 侧的顺序规则与回退状态机由 `src-tauri/src/app_update.rs` / `app_update_flow_tests.rs` 看护（`cargo test`）；
这里只看 Python 测试能看到、而 cargo 看不到的东西：

* 前端不再直接用 `@tauri-apps/plugin-updater`，壳也不再放 `updater:*` 权限——否则 webview 能绕过编排只查单一来源；
* 两个新命令三处登记齐全（`test_desktop_codex_button.py` 的枚举已覆盖「齐全」，这里钉「主窗口拿得到」）；
* 镜像地址的**唯一**常量在 `app_update.rs`，ADR / 隐私声明里写的主机必须是它（面向用户的断言要指得出兑现它的那行代码）；
* 面向用户的出网说明（隐私声明、两份 README）提到了镜像。

判据的主语：TS 里**真正的 import 语句**（注释行不算）与 JSON 权限清单。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from support.rustsrc import TAURI, rust_code, tauri_commands

ROOT = Path(__file__).resolve().parent.parent
WEB_SRC = ROOT / "web" / "src"
APP_UPDATE_RS = TAURI / "src" / "app_update.rs"


# `import … from '…'` / `import('…')`：关键字前不许出现 `/` 或 `*`，于是 `//` 与 `* ` 开头的注释行不算。
# 已知边界：不是 TS 解析器（tsconst 的扫描器在个别文件的正则字面量上会读错位，不适合整库扫）；
# 写成多行 import 且把关键字放在行首以外也认——只要这一行有 from/import 紧跟插件名。
_PLUGIN_IMPORT = re.compile(
    r"""^[^/*\n]*\b(?:from|import)\s*\(?\s*['"]@tauri-apps/plugin-updater['"]""", re.M
)


def _rust_const_string(name: str) -> str:
    code = rust_code(APP_UPDATE_RS.read_text(encoding="utf-8"), keep_strings=True)
    m = re.findall(rf'pub const {name}: &str = "([^"]*)";', code)
    assert len(m) == 1, (name, m)
    return m[0]


def _rust_string_list(name: str) -> list[str]:
    code = rust_code(APP_UPDATE_RS.read_text(encoding="utf-8"), keep_strings=True)
    m = re.search(rf"const {name}: &\[&str\] = &\[(.*?)\];", code, re.S)
    assert m, name
    return re.findall(r'"([^"]+)"', m.group(1))


def test_the_frontend_never_imports_the_updater_plugin_directly():
    offenders = []
    for f in WEB_SRC.rglob("*"):
        if f.suffix not in {".ts", ".tsx"} or ".test." in f.name:
            continue
        src = f.read_text(encoding="utf-8")
        if _PLUGIN_IMPORT.search(src):
            offenders.append(f.relative_to(ROOT).as_posix())
    assert not offenders, f"前端绕过了壳的来源编排：{offenders}"


def test_main_window_may_call_the_update_commands_and_not_the_plugin_directly():
    perms = json.loads((TAURI / "capabilities" / "main.json").read_text(encoding="utf-8"))[
        "permissions"
    ]
    assert "allow-desktop-update-check" in perms
    assert "allow-desktop-update-install" in perms
    assert not [p for p in perms if p.startswith("updater:")], perms
    assert {"desktop_update_check", "desktop_update_install"} <= set(tauri_commands())
    # 远程实例窗口（ADR 0105）一个更新权限都不许有
    for cap in (TAURI / "capabilities").glob("remote-*.json"):
        remote = json.loads(cap.read_text(encoding="utf-8"))["permissions"]
        assert not [p for p in remote if "update" in p], (cap.name, remote)


def test_the_mirror_constant_is_https_and_the_docs_name_its_host():
    url = urlparse(_rust_const_string("MIRROR_MANIFEST_URL"))
    assert url.scheme == "https" and url.hostname, url
    adr = (ROOT / "docs" / "adr" / "0121-app-update-source-order-and-fallback.md").read_text(
        encoding="utf-8"
    )
    assert _rust_const_string("MIRROR_MANIFEST_URL") in adr, "ADR 0121 里的镜像地址必须与常量一致"
    for doc in ("docs/privacy.md", "README.md", "README.zh-CN.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert url.hostname in text, f"{doc} 的出网说明没有提到镜像主机 {url.hostname}"


def test_adr_lists_every_mainland_timezone_the_code_recognises():
    adr = (ROOT / "docs" / "adr" / "0121-app-update-source-order-and-fallback.md").read_text(
        encoding="utf-8"
    )
    zones = _rust_string_list("MAINLAND_TIMEZONES")
    assert "Asia/Shanghai" in zones and len(zones) >= 5, zones
    missing = [z for z in zones if z not in adr]
    assert not missing, f"ADR 0121 没写出代码认的时区：{missing}"


def test_tauri_conf_keeps_github_as_the_only_configured_endpoint():
    """发布链（`updater_consumer_check.py`）读 endpoints；镜像不进配置，只在 app_update.rs 的常量里。"""
    conf = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    eps = conf["plugins"]["updater"]["endpoints"]
    assert len(eps) == 1 and urlparse(eps[0]).hostname == "github.com", eps
