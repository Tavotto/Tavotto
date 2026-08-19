"""`python -m tavotto` 与 `tavotto` 命令共用同一个入口。

指向 `cli_entry` 而不是 `app`：子命令要在 import Flask 之前分派掉，
理由写在 `cli_entry` 的模块说明里。
"""
from .cli_entry import main

if __name__ == "__main__":
    main()
