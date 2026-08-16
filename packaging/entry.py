"""独立应用（.app / .exe）的入口。

窗口化打包（console=False）下没有终端：Windows 上 `sys.stdout` 直接是 None，
一句 `print()` 就是 AttributeError，应用会在用户眼前一声不响地消失。所以这里
先把 stdout/stderr 接到数据目录的日志文件上，再进正常入口——出问题时用户至少
有一份可以发给我们的日志。
"""
import os
import sys
from pathlib import Path


def _redirect_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return                     # 有真终端（比如从命令行启动 .app 里的可执行文件）
    try:
        from magplot.engine import config
        log_dir = config.data_dir() / "cache"
        log_dir.mkdir(parents=True, exist_ok=True)
        target = open(log_dir / "app.log", "a", encoding="utf-8",
                      errors="replace", buffering=1)
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
    from magplot.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
