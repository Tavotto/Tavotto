"""真浏览器 e2e 的被测进程入口：`python tests/support/e2e_offline_app.py --port … --figures …`。

与 `python -m tavotto` 唯一的区别：进程内先套上 `offline_world.apply_offline_managed_env`——
让受管环境能在**没有网络**的机器上建出来（基础栈换成空表，宿主 site-packages 用 `.pth` 挂进新环境，
pip 只从 `PIP_FIND_LINKS` 指的本地 wheelhouse 取包）。放宽了什么、没放宽什么见该函数与
`support.dependency_repair.offline_managed_env` 的说明；生产上的隔离与最小性另有单元用例逐字节钉着。

纯标准库 + tavotto；**不 import pytest**（CI 的 posix-e2e 没装）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support import offline_world  # noqa: E402
from tavotto.cli_entry import main  # noqa: E402
from tavotto.engine import pool as engine_pool  # noqa: E402

if __name__ == "__main__":
    offline_world.apply_offline_managed_env(setattr, engine_pool.find_worker_python())
    main()
