"""项目整夹挪走后，按路径作键的本机设置随认领迁到新路径（ADR 0211 第一期 5）。

什么时候迁：**只在确认是挪走**时——项目内时间线认领成功、且归属里记的旧项目路径确定已不在
（`projecthistory.owner_path_gone`）。复制（旧路径还在）不迁：旧项目的设置原样留给旧项目。

迁什么（ADR 0211「按路径失联的本机状态」表）：

| 状态 | 处置 |
| --- | --- |
| 改指表 `config.json` `projects[<路径>].input_remap` | 迁：规则的 `to` 落在旧项目根之内的换成新根，项目外的原样 |
| 运行配置 `runconfigs/<摘要>.json` | 迁：配置 / 默认 / 捕获史；**启动记录不迁**（解释器证据） |
| 输入转录 `inputtranscripts/<摘要>.json` | 迁 |
| 渲染缓存 / baked_overrides | 不迁（派生物，重建） |
| 运行许可 / 执行策略 / 记住的解释器 | **不迁**（ADR 0021 §7.1：项目移动后重新确认） |

纪律：每一项各自幂等（新键下已有就不覆盖）；一项失败不挡别的项、也不让认领失败——只记日志，
新项目的设置里留一个待办标记 `moved_from`，下次打开项目时重试，全部成功才摘掉。旧键原样保留
（现有配置没有按项目清理的逻辑）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import config, inputremap, inputtranscript, runconfig

LOG = logging.getLogger("tavotto.projectmigrate")

#: 新项目设置里的待办标记：`{"from": <旧项目路径>}`。迁移没做完（某一项失败）时留着，下次打开项目时重试。
PENDING_KEY = "moved_from"

#: 明确**不迁**的本机项目设置（运行许可、执行策略、记住的解释器）。只用来给用例钉住，迁移代码不读它们。
NOT_MIGRATED_SETTINGS = ("native_run_permissions", "execution_policy", "environment")


def _identity(path: str | os.PathLike) -> str:
    """路径身份：先解析符号链接（`realpath` 对不存在的末段也照样解析已存在的前缀——macOS 的 `/var` →
    `/private/var`：用户指认的 `/var/…/raw` 与归属记的 `/private/var/…` 是同一处），再按卷归一大小写。"""
    return config.normalize_path_identity(os.path.realpath(os.path.abspath(os.fspath(path))))


def old_root_for(owner_key: str) -> str:
    """槽位归属里记的项目键（归一化身份，大小写不敏感的卷上是小写）→ 本机设置里那个项目的原始路径写法。

    配置的项目设置按 `str(Path(路径))` 作键（保留大小写），与身份串可能只差大小写：在已有的键里找身份相同的
    那一个；找不到就用身份串本身（运行配置 / 转录按身份串作摘要，本来就对得上）。"""
    target = _identity(owner_key)
    try:
        keys = list(config.load()["projects"].keys())
    except Exception:  # noqa: BLE001 —— 读不出配置：退回身份串
        keys = []
    for key in keys:
        if isinstance(key, str) and _identity(key) == target:
            return key
    return owner_key


def rebase_path(path: str, old_root: str, new_root: str) -> str:
    """`path` 落在旧项目根之内（含根本身）→ 换成新根下的同一个相对位置；否则原样。按身份串比（大小写按卷）。"""
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        return path
    old_id = _identity(old_root)
    pid = _identity(path)
    if pid != old_id and not pid.startswith(old_id.rstrip(os.sep) + os.sep):
        return path
    old_parts = Path(os.path.realpath(os.path.abspath(old_root))).parts
    parts = Path(os.path.realpath(path)).parts
    return os.path.join(os.fspath(new_root), *parts[len(old_parts) :])


def migrate(old_root: str, new_root: str) -> dict:
    """把三份本机设置从旧路径迁到新路径。回 `{"input_remap": 新加条数, "runconfigs": bool, "inputtranscripts":
    bool, "failed": [失败的项]}`。任何一项失败只记日志（不抛），由调用方决定要不要留待办标记。"""
    result: dict = {"input_remap": 0, "runconfigs": False, "inputtranscripts": False, "failed": []}

    def rebase(rule: dict) -> dict:
        return {**rule, "to": rebase_path(rule["to"], old_root, new_root)}

    steps = (
        ("input_remap", lambda: inputremap.migrate_rules(old_root, new_root, rebase)),
        ("runconfigs", lambda: runconfig.migrate_store(old_root, new_root)),
        ("inputtranscripts", lambda: inputtranscript.migrate_store(old_root, new_root)),
    )
    for name, step in steps:
        try:
            result[name] = step()
        except Exception:  # noqa: BLE001 —— 一项失败不挡别的、不让认领失败
            LOG.warning("挪走项目的本机设置迁移失败（%s），下次打开项目再试", name, exc_info=True)
            result["failed"].append(name)
    return result


def claim_moved(owner_key: str, new_root: str | os.PathLike) -> dict:
    """认领确认是挪走之后调：先记待办标记，再迁；全部成功就摘标记。不抛。"""
    new_root = os.fspath(new_root)
    old_root = old_root_for(owner_key)
    if _identity(old_root) == _identity(new_root):
        return {"input_remap": 0, "runconfigs": False, "inputtranscripts": False, "failed": []}
    try:
        config.set_project_settings(new_root, {PENDING_KEY: {"from": old_root}})
    except Exception:  # noqa: BLE001
        LOG.warning("记迁移待办失败", exc_info=True)
    return _run_and_settle(old_root, new_root)


def retry_pending(new_root: str | os.PathLike) -> dict | None:
    """打开项目时调：有待办标记（上次迁移没做完）就再迁一次。没有标记回 None。不抛。"""
    new_root = os.fspath(new_root)
    try:
        pending = config.project_settings(new_root).get(PENDING_KEY)
    except Exception:  # noqa: BLE001
        return None
    old_root = pending.get("from") if isinstance(pending, dict) else None
    if not isinstance(old_root, str) or not old_root:
        return None
    return _run_and_settle(old_root, new_root)


def _run_and_settle(old_root: str, new_root: str) -> dict:
    result = migrate(old_root, new_root)
    if not result["failed"]:
        try:
            config.set_project_settings(new_root, {PENDING_KEY: None})
        except Exception:  # noqa: BLE001
            LOG.warning("摘迁移待办失败（下次打开项目会再迁一次，迁移幂等）", exc_info=True)
    return result
