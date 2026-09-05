"""品牌与格式标识常量（纯标准库；Flask 父进程与 worker 都可 import）。

产品正式名称 Tavotto，拼写与大小写固定。

**这一档没有 LEGACY_*，是有意的。** 2026-08-20 从 Magplot 改名时选的是干净
断裂：`magplot-package` / `.magplot` / `magplot-proof` 一律不再认，Magic Matplot
时代那一档（`magic-matplot-package` / `.mmpack.zip`）也一并去掉了——只认两代前
的名字、却不认上一代的，那种半吊子状态比干净断裂更难向用户解释。
存量项目包需要能打开时，做法是写一个一次性转换脚本，而不是把读取端摊成三档。
（文档 schema 的迁移是另一回事，`migrateToProject` 那条链照旧。）
"""

PRODUCT_NAME = "Tavotto"

PACKAGE_KIND = "tavotto-package"
PROOF_KIND = "tavotto-proof"
PACKAGE_EXT = ".tavotto"

# 分发标识：检查更新与 About 里的链接都从这里取，别处不得再手写仓库地址。
DIST_NAME = "tavotto"  # PyPI / wheel 包名
REPO_OWNER = "Tavotto"
REPO_NAME = "Tavotto"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
RELEASES_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases"

#: Codex 集成的安装参数。**唯一出处**——README 首用章节、`tavotto codex install`
#: 与 `docs/codex-plugin-distribution.md` 都从这里派生；两处手写就会漂，而漂了
#: 之后的症状是「照文档做装不上」，用户没法自己发现是哪一边错。
#: 看护：`tests/test_codex_install_cli.py::test_readme_and_cli_use_the_same_command`
CODEX_MARKETPLACE = f"{REPO_OWNER}/{REPO_NAME}"
#: 稀疏检出：只取插件本体与它的注册目录，不把整个仓库拉到用户机器上
CODEX_SPARSE_PATHS = (".agents/plugins", "codex-plugin")
#: **配置后的 marketplace 名**——与源 `Tavotto/Tavotto` 不是一回事：
#: `codex plugin marketplace remove` 收的是这个名字，给它 `owner/repo` 会被拒
#: （`/` 不是合法的 marketplace 名）。唯一出处是 `.agents/plugins/marketplace.json`
#: 的 `name`，看护在 `tests/test_codex_install_cli.py`。
CODEX_MARKETPLACE_NAME = "tavotto"
#: `codex plugin add` 的目标（插件名@marketplace 名）
CODEX_PLUGIN_REF = "tavotto@tavotto"
#: 插件在 Codex 那边的名字（`codex plugin list` 里的那一列）
CODEX_PLUGIN_NAME = "tavotto"
#: 插件在仓库里 / 发行分支里的目录名（marketplace 清单的 `path` 指向它）
CODEX_PLUGIN_SUBDIR = "codex-plugin"
#: 机器维护的发行分支：完整插件（含内嵌画布）的投影，由 scripts/plugin_publish.py 推进
#: （ADR 0043）。marketplace 的 `git-subdir` 来源指到它；源码分支上不再跟踪画布产物。
CODEX_PLUGIN_STABLE_BRANCH = "plugin-stable"
#: `git-subdir` 来源里的仓库地址（Codex 对 https://github.com/… 会自动补 .git，这里直接写全）
CODEX_PLUGIN_SOURCE_URL = f"{REPO_URL}.git"

# 桌面壳的 bundle 标识，与 src-tauri/tauri.conf.json 的 identifier 严格同源。
# 桌面日志目录（tauri 的 app_log_dir）按它推导：macOS 是
# ~/Library/Logs/<id>/，Windows 是 %LOCALAPPDATA%\<id>\logs\。
DESKTOP_BUNDLE_ID = "com.tavotto.tavotto"
