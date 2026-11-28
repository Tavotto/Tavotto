"""仅开发可用的故障注入（ADR 0221 §六）：评测「环境故障」家族用，产品行为里没有入口。

两道门**同时**成立才生效：`TAVOTTO_DEV=1`，加上 `TAVOTTO_DEV_FAULT_<名>=1`。任何一个没设、或设成别的值，
`active()` 一律 False，调用点原样走产品路径。名字是闭集 `FAULTS`：调用点写了不在表里的名字，
`active()` 直接 `KeyError`（测试抓得到），不会静默成「永远关」。

只放**必须改产品代码才能模拟**的故障。能用假 wheel / 假索引 / 假环境变量做出来的（只有别的 Python 版本的 wheel、
import 时动态库失败、目录权限被拒、Conda 激活失败）一律不在这里，见 `scripts/eval/fix_compat/envfaults.py`。

纯标准库（Flask 父进程 import 链上）。不读任何用户数据，不写任何东西。
"""

from __future__ import annotations

import os

#: 开关变量。只认字面 `1`，免得 `TAVOTTO_DEV=0` / `false` 被当成开。
ENV_DEV = "TAVOTTO_DEV"
ENV_FAULT_PREFIX = "TAVOTTO_DEV_FAULT_"

#: 闭集。加一个名字 = 加一个产品里的注入点，要同时在测试里钉「不设 TAVOTTO_DEV 时完全不生效」。
FAULT_SELFTEST_BLOCKED = "selftest_blocked"  # worker_self_test 回 error_reply + safe_file_operation_blocked（TVD-FTH9 形）
FAULTS = (FAULT_SELFTEST_BLOCKED,)


def active(name: str) -> bool:
    """`name` 这条故障此刻是否被注入。`name` 必须是 `FAULTS` 里的。"""
    if name not in FAULTS:
        raise KeyError(f"未登记的开发故障: {name!r}")
    if os.environ.get(ENV_DEV) != "1":
        return False
    return os.environ.get(ENV_FAULT_PREFIX + name.upper()) == "1"
