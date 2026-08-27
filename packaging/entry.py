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


def main() -> None:
    _redirect_streams()
    # 冻结应用里 sys.path 上没有源码树；datas 把包放在了 _MEIPASS 下
    base = getattr(sys, "_MEIPASS", None)
    if base and base not in sys.path:
        sys.path.insert(0, base)
    # 子命令（open / doctor）只用纯标准库那点逻辑，**在这里就分派掉**：
    # 走 app.main() 会 import Flask + pymupdf + 整个 app.py，而一次交接
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
