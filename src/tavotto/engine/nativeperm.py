""" "记住这个项目和这个 Python 可以跑 Tavotto Run"（ADR 0021 §7.1）。

## 为什么不是一个全局开关

`native_confirmed = true` 这种形状的问题不在于它宽松，而在于它**回答的不是
用户被问到的那个问题**。用户在确认框里看到的是三行具体的东西：

    Python:            /paper/.venv/bin/python
    Working directory: /paper
    Target:            figure.py

他点"记住"，记住的是"**这个项目里的这条 Python**"。存成全局开关的话，下次
另一个项目里的另一条解释器就直接放行了——而那条解释器可能是他刚从别人那里
clone 下来的仓库带的。

## 绑定哪几维

```text
project identity + interpreter realpath + permission schema version
```

`permission_key` 由 `runspec.RunRequest.permission_key()` 算（唯一出处）。
**不含 target / argv / cwd**：含了的话同一个项目里换个脚本就要重新确认一次，
而那会把确认训练成一个下意识点掉的对话框——它恰恰是唯一一次真正的授权。

schema 版本变了（绑定的维度变了）**全部失效**：旧的那次点击不是对新含义的
授权。这不是保守，是"许可的含义变了就要重新问"这条最朴素的规则。

## 不存什么

argv、环境变量、token、完整命令。**许可不是凭据**——它只回答"这个组合要不要
再问一次"，任何能凭它执行点什么的东西都不该在里面。

纯标准库。
"""

from __future__ import annotations

import time

from . import config, runspec

#: 项目设置里的键。
SETTINGS_KEY = "native_run_permissions"


def _bucket(project_root: str) -> dict:
    raw = config.project_settings(str(project_root)).get(SETTINGS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def is_remembered(project_root: str, permission_key: str) -> bool:
    """这个（项目 × 解释器 × schema）组合之前被记住过吗。"""
    entry = _bucket(project_root).get(permission_key)
    if not isinstance(entry, dict):
        return False
    # schema 各自存一份：升级之后旧条目还在文件里，但 key 已经变了，
    # 于是天然失效——**不需要一次迁移**，也就没有"迁移写错了"这条风险。
    return entry.get("schema") == runspec.PERMISSION_SCHEMA


def remember(project_root: str, permission_key: str, *, interpreter: str) -> dict:
    """记住这个组合。`interpreter` 只用于设置页里显示，不参与判据。"""
    bucket = _bucket(project_root)
    bucket[permission_key] = {
        "schema": runspec.PERMISSION_SCHEMA,
        "interpreter": interpreter,
        "granted_at": time.time(),
    }
    config.set_project_settings(str(project_root), {SETTINGS_KEY: bucket})
    return bucket[permission_key]


def forget(project_root: str, permission_key: str = "") -> int:
    """撤销：给了 key 撤一条，没给就整个项目全撤。返回撤了几条。

    **必须有这条出口**：一次"记住"如果没有对称的"忘掉"，那它就不是一个
    可以放心点的选项（设置页里的撤销入口调的就是它）。
    """
    bucket = _bucket(project_root)
    if permission_key:
        removed = 1 if bucket.pop(permission_key, None) is not None else 0
    else:
        removed = len(bucket)
        bucket = {}
    config.set_project_settings(str(project_root), {SETTINGS_KEY: bucket or None})
    return removed


def listing(project_root: str) -> list[dict]:
    """设置页要显示的那份。**不含任何可执行信息。**"""
    out = []
    for key, entry in sorted(_bucket(project_root).items()):
        if not isinstance(entry, dict):
            continue
        out.append(
            {
                "permission_key": key,
                "interpreter": entry.get("interpreter", ""),
                "granted_at": entry.get("granted_at"),
                "schema": entry.get("schema"),
                "current_schema": entry.get("schema") == runspec.PERMISSION_SCHEMA,
            }
        )
    return out
