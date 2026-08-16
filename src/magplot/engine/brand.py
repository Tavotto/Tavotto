"""品牌与格式标识常量（纯标准库；Flask 父进程与 worker 都可 import）。

产品正式名称 Magplot，拼写与大小写固定。写出端一律用新标识；
读取端同时接受 LEGACY_*——Magic Matplot 时代的项目包必须继续可打开。
"""
PRODUCT_NAME = "Magplot"

PACKAGE_KIND = "magplot-package"
PROOF_KIND = "magplot-proof"
PACKAGE_EXT = ".magplot"

LEGACY_PACKAGE_KIND = "magic-matplot-package"
LEGACY_PROOF_KIND = "magic-matplot-proof"
LEGACY_PACKAGE_EXT = ".mmpack.zip"

# 分发标识：检查更新与 About 里的链接都从这里取，别处不得再手写仓库地址。
DIST_NAME = "magplot"                      # PyPI / wheel 包名
REPO_OWNER = "erwanjun"
REPO_NAME = "magplot"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
RELEASES_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
RELEASES_URL = f"{REPO_URL}/releases"
