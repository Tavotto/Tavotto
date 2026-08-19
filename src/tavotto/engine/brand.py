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
DIST_NAME = "tavotto"                      # PyPI / wheel 包名
REPO_OWNER = "Tavotto"
REPO_NAME = "Tavotto"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
RELEASES_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases"
