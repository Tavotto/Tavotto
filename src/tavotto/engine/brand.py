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
#: 稀疏检出：市场快照只需要市场清单——插件本体来自发行分支（`git-subdir → plugin-stable`，
#: ADR 0043），不再从源码 checkout 里取，所以不必再把 `codex-plugin` 拉到用户机器上。
CODEX_SPARSE_PATHS = (".agents/plugins",)
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
#: 没有 git 的机器（只装了 Codex 桌面版的 Windows 最常见，#722）装插件的来源：发行分支的
#: GitHub 源码压缩包。分支本身就是一个本地市场（根上 `.agents/plugins/marketplace.json` →
#: `local ./codex-plugin`），解压后 `codex plugin marketplace add <目录>` 不需要 git。
#: GitHub 把这个地址 302 到 codeload，zip 注释里是那个提交的 SHA。
CODEX_PLUGIN_STABLE_ARCHIVE_URL = f"{REPO_URL}/archive/refs/heads/{CODEX_PLUGIN_STABLE_BRANCH}.zip"
#: 解压后顶层目录名（GitHub 源码压缩包的固定形状 `<仓库名>-<分支名>`）。`tavotto codex install`
#: 与 README 手动步骤（`Expand-Archive`）落到同一个名字，两条路装出来的是同一份本地市场。
CODEX_PLUGIN_STABLE_ARCHIVE_DIR = f"{REPO_NAME}-{CODEX_PLUGIN_STABLE_BRANCH}"
#: 每个 release 附带的插件构建清单（`.github/workflows/release.yml` 把 staging 的
#: `plugin-build.json` 拷成这个名字）。压缩包的 content_digest 要与它对上——发行分支与
#: release 附件由两道不同的工序写出，两边一致才算这份压缩包是那次发行的产物。
CODEX_PLUGIN_BUILD_ASSET = "codex-plugin-build.json"

#: Claude Code 插件的安装参数（ADR 0103）。**同一份插件目录、同一条发行分支**：
#: 仓库根 `.claude-plugin/marketplace.json` 的 `git-subdir` 指向
#: `CODEX_PLUGIN_STABLE_BRANCH` 上的 `CODEX_PLUGIN_SUBDIR`，插件里多一份
#: `.claude-plugin/plugin.json`。稀疏检出只要市场清单那一个目录。
#: 看护：`tests/test_claude_plugin.py`
CLAUDE_MARKETPLACE = CODEX_MARKETPLACE
CLAUDE_SPARSE_PATHS = (".claude-plugin",)
#: `claude plugin install` 的目标（插件名@marketplace 名）；两个名字各自的唯一出处是
#: `.claude-plugin/marketplace.json` 的 `plugins[0].name` 与 `name`
CLAUDE_PLUGIN_REF = "tavotto@tavotto"
#: WorkBuddy 装的是**同一份** Claude 插件（ADR 0106）：它的「插件市场 → 添加市场」收 `owner/repo`，
#: 读仓库根 `.claude-plugin/marketplace.json`，装 `CLAUDE_PLUGIN_REF`。界面里没有稀疏检出选项。
#: 看护：`tests/test_claude_plugin.py`
WORKBUDDY_MARKETPLACE = CODEX_MARKETPLACE

#: DeepSeek Harness 的 bundle（ADR 0104）。同一份插件目录兼作 npm 包：`package.json`
#: 声明 `dsh.bundle.patch`，由 `dsh plugin --profile <名> add <规格>` 经 pnpm 装进 profile。
#: 规格是 pnpm 的 git 子目录写法，与两个市场指向同一条发行分支、同一个目录。
#: 看护：`tests/test_dsh_bundle.py`
DSH_BUNDLE_NAME = "tavotto-dsh"
DSH_PLUGIN_SPEC = (
    f"git+{CODEX_PLUGIN_SOURCE_URL}#{CODEX_PLUGIN_STABLE_BRANCH}&path:/{CODEX_PLUGIN_SUBDIR}"
)
#: README 里示范的 profile（`dsh web` 用的那个）；换 profile 只换这个名字
DSH_DEFAULT_PROFILE = "web"

# 桌面壳的 bundle 标识，与 src-tauri/tauri.conf.json 的 identifier 严格同源。
# 桌面日志目录（tauri 的 app_log_dir）按它推导：macOS 是
# ~/Library/Logs/<id>/，Windows 是 %LOCALAPPDATA%\<id>\logs\。
DESKTOP_BUNDLE_ID = "com.tavotto.tavotto"
