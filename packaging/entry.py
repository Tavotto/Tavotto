"""独立应用（.app / .exe）的入口。**两个可执行文件共用这一份。**

`packaging/tavotto.spec` 从同一个 Analysis 出两个 exe，只差 console 子系统：

  * `Tavotto(.exe)`  —— `console=False`。双击不弹黑窗；桌面壳启动它当 sidecar。
  * `tavotto-cli(.exe)` —— `console=True`。**外部程序（Codex 插件、安装器、
    编辑器）唯一能当命令行调的那个**：GUI 子系统的 exe 在没有真终端时
    `sys.stdout is None`，下面 `_redirect_streams` 会把输出改道到 app.log，
    调用方 `capture_output` 拿到的是空的 stdout 而不是那行 JSON。

两个 exe 共用同一份 `_internal/`，代价只是多一个 ~1.5 MB 的 bootloader。

窗口化打包（console=False）下没有终端：Windows 上 `sys.stdout` 直接是 None，
一句 `print()` 就是 AttributeError，应用会在用户眼前一声不响地消失。所以这里
先把 stdout/stderr 接到数据目录的日志文件上，再进正常入口——出问题时用户至少
有一份可以发给我们的日志。
"""

import os
import sys

#: macOS 自带、随系统更新维护的 CA 证书包（`security` 钥匙串里的根证书导出）。
MACOS_CA_BUNDLE = "/etc/ssl/cert.pem"


def _ensure_ca_bundle(environ=os.environ, *, platform=sys.platform, exists=os.path.isfile):
    """macOS 冻结父进程找不到任何 CA 时，把 OpenSSL 指到系统证书包（#439）。

    PyInstaller 带走的 libcrypto 里 OPENSSLDIR 是**构建机**上 python.org 框架的
    绝对路径，用户机器上不存在；CPython 在 macOS 上又不读系统钥匙串，于是
    `load_default_certs()` 一张 CA 都装不进来，所有公网 HTTPS 都
    CERTIFICATE_VERIFY_FAILED——遥测按设计静默丢弃，平台指标里整个 macOS 桌面
    版是零。OpenSSL 在**建上下文时**读 `SSL_CERT_FILE`，所以必须在任何 HTTPS
    之前设好；用户自己设了 `SSL_CERT_FILE` / `SSL_CERT_DIR` 的一律不碰。
    Windows 由 CPython 从系统证书库补 CA，不受影响；Linux 没有桌面版。
    修在打包入口而不是 `engine/telemetry.py`：引擎保持纯标准库、不为冻结态引依赖。
    返回设上的路径（没动就是 None），供测试与日志用。
    """
    if platform != "darwin":
        return None
    if environ.get("SSL_CERT_FILE") or environ.get("SSL_CERT_DIR"):
        return None
    if not exists(MACOS_CA_BUNDLE):
        return None
    environ["SSL_CERT_FILE"] = MACOS_CA_BUNDLE
    return MACOS_CA_BUNDLE


def _redirect_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return  # 有真终端（比如从命令行启动 .app 里的可执行文件）
    try:
        from tavotto.engine import config

        log_dir = config.data_dir() / "cache"
        log_dir.mkdir(parents=True, exist_ok=True)
        target = open(log_dir / "app.log", "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        target = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = target
    if sys.stderr is None:
        sys.stderr = target


#: render child 的自起标志（`tavotto.rendercore.renderchild.child_argv()` 在冻结产物里给的形状，ADR 0066 / 0072）。
RENDER_CHILD_FLAG = "--render-child"


def main() -> None:
    # 冻结应用里 sys.path 上没有源码树；datas 把包放在了 _MEIPASS 下
    base = getattr(sys, "_MEIPASS", None)
    if base and base not in sys.path:
        sys.path.insert(0, base)
    # **render child 最先分派**（U10，ADR 0072）：父进程用同一个 exe 加 `--render-child` 起 PDFium 子进程
    # （冻结产物里没有 `-m`），它在 stdin / stdout 上说行分隔 JSON——**不能**先走 `_redirect_streams`
    # （那会把 GUI exe 的管道改道进 app.log，父进程永远收不到响应），也不能进 Flask。
    if sys.argv[1:2] == [RENDER_CHILD_FLAG]:
        from tavotto.rendercore import renderchild

        sys.exit(renderchild.child_main(sys.argv[2:]))
    _redirect_streams()
    # HTTPS 之前把 OpenSSL 指到系统证书（#541）；render child 不发网络请求，所以放在它的分派之后
    _ensure_ca_bundle()
    # 子命令（open / doctor）只用纯标准库那点逻辑，**在这里就分派掉**：
    # 走 app.main() 会 import Flask + RenderCore + 整个 app.py，而一次交接
    # 一个 HTTP 端点都用不上——那份冷启动全是白付的。
    from tavotto.engine import cli as engine_cli

    # Windows 上冻结的 console exe 被安装器 / Codex 用管道接管时，stdout 退回
    # cp1252/cp936——中文一出现就 UnicodeEncodeError，调用方等的那行 JSON
    # 一个字节都收不到。实现只有一份（engine/cli.py）。
    engine_cli.use_utf8_streams()
    rc = engine_cli.dispatch(sys.argv[1:])
    if rc is not None:
        sys.exit(rc)
    from tavotto.app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
