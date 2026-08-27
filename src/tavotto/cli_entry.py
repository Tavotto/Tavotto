"""`tavotto` 命令与 `python -m tavotto` 的入口。**纯标准库，不 import app。**

为什么不直接指向 `tavotto.app:main`：`open` / `doctor` 这两个子命令是给外部
程序用的（Codex 插件、安装器、编辑器、别的 Agent），一次交接一个 HTTP 端点
都用不上，却要先把 Flask、PyMuPDF 和整个 `app.py` 装进内存。冻结产物的入口
（`packaging/entry.py`）早就是先分派后 import，pip / pipx 装出来的这条却还是
`tavotto.app:main` —— 同一条命令在两种安装形态下行为不同：

* 插件的交接会连着调两次 `tavotto open`，每次都白付一整个应用的冷启动；
* `doctor` 本该是「装坏了怎么查」的那把工具，可只要某个界面依赖 import 失败
  （缺 DLL、装了一半、pymupdf 的 wheel 与解释器不匹配），它自己也起不来——
  最需要它的时候正好用不了。

分派放在 argparse **之前**：主入口是纯 flag 形态（`tavotto --figures …`），
改成 subparsers 会把既有命令行整个换掉。
"""

import sys


def main() -> None:
    from .engine import cli as engine_cli

    # 输出里全是中文，而 Windows 上 stdout 不是真控制台时会退回系统区域编码
    # ——**在任何一句 print 之前**先把流钉成 UTF-8（实现只有一份，见那边）。
    engine_cli.use_utf8_streams()
    rc = engine_cli.dispatch(sys.argv[1:])
    if rc is not None:
        sys.exit(rc)
    from .app import main as app_main

    app_main()


if __name__ == "__main__":
    main()
