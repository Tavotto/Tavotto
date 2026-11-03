"""`python -m tavotto.engine.agent_runner`：薄壳，实现全在 `runner_cli`。

为什么不把实现放在这里：PyInstaller 冻结产物**不收包里的 `__main__` 子模块**，
`tavotto-cli agent-run` 若 `from .agent_runner import __main__` 就当场 ImportError——
0.19.0–0.19.2 的安装包里内置执行器因此一次都没跑起来（TVD-BCZ0）。
产品代码一律 import `runner_cli`；`tests/test_frozen_entrypoints.py` 看护。
"""

import sys

from .runner_cli import main

if __name__ == "__main__":
    sys.exit(main())
