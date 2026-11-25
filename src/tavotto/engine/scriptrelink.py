"""接回挪走的脚本时，按脚本作键的本机状态跟着带上（ADR 0211 第一期 6，维护者 2026-11-26 拍板）。

接回 = 对新位置的脚本做一次现有的试运行登记（跑成了才改注册表）。需要参数 / 回答 `input()` 的脚本，验证试跑要用
**旧脚本键下**记着的那几样，否则跑不起来、永远接不上：

| 状态 | 键 | 处置 |
| --- | --- | --- |
| 运行配置（argv）与默认配置 | `runconfigs/<项目>.json`，记录里的 `script` | 按旧脚本的每条配置给新脚本登记一份（新引用），默认配置随之映射 |
| 答案 | 项目 `tavottofile/` 的答案文件 + 本机上下文侧表，键里有脚本与配置引用 | 复制到新脚本、配置引用换成映射后的 |
| 输入转录 | `inputtranscripts/<项目>.json`，键 `脚本\\0配置` 与作废令牌 | 同上 |
| 运行许可 / 解释器信任 / 启动记录 | — | **不带**（ADR 0021 §7.1） |

**只有接回成功才算数**：`prepare` 先把这些复制到新脚本键下并在本机项目设置里记一笔待办（`PENDING_KEY`，带着这次
加了什么）；`finish(ok=True)` 且注册表里新脚本确实认领了图 → 摘掉待办（状态留在新键下）；其余一律 `abort`——
把这次加的原样删掉，新键下不留残留。旧键一个字节不动。浏览器关掉 / 进程退出在两步之间：下一次 `prepare` 或
`settle_stale` 按注册表此刻的事实收尾（新脚本认领了图 = 成功，否则撤销），不靠内存里的令牌。

**新脚本键下本来就有状态**（用户已经在新位置配过参数 / 答过题）就什么都不带：用户新给的更新，也免得撤销时误删。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from . import config, inputtranscript, registry, runconfig, scriptanswers

LOG = logging.getLogger("tavotto.scriptrelink")

#: 本机项目设置里的待办：`{新脚本: {"from": 旧脚本, "configs": {旧引用: 新引用}, "answers": bool, "transcripts": [键…], "gens": [键…]}}`
PENDING_KEY = "script_relink_pending"


def _pending(root: str) -> dict:
    raw = config.project_settings(root).get(PENDING_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _set_pending(root: str, pending: dict) -> None:
    config.set_project_settings(root, {PENDING_KEY: pending or None})


def _has_state(root: str, script: str) -> bool:
    if runconfig.configs_of(root, script):
        return True
    if scriptanswers.entries(root, script):
        return True
    with inputtranscript._LOCK:
        bindings, gens = inputtranscript._load(root)
    prefix = script + inputtranscript._SEP
    return any(k == script or k.startswith(prefix) for k in [*bindings, *gens])


def prepare(project_root: str | Path, old: str, new: str) -> dict:
    """把旧脚本键下的运行配置 / 答案 / 输入转录复制到新脚本键下（待办），回 `{"run_config": 新的默认配置引用 | None,
    "carried": bool}`。新键下本来就有状态 / 旧键下什么都没有 → 什么都不做（`carried: False`）。"""
    root = os.fspath(project_root)
    settle_stale(root)
    if old == new or _has_state(root, new):
        return {"run_config": None, "carried": False}
    mapping: dict[str, str] = {}
    for cfg in runconfig.configs_of(root, old):
        if cfg.argv is None:
            continue  # 敏感值已不在本进程：带不过去（照旧要用户重新提供）
        moved = runconfig.put(root, new, list(cfg.argv), sensitive=cfg.sensitive, source=cfg.source)
        if moved is not None:
            mapping[cfg.id] = moved.id
    try:
        default = runconfig.default_selection(root, old)
    except runconfig.RunConfigError:
        default = None
    record = {
        "from": old,
        "configs": mapping,
        "answers": _copy_answers(root, old, new, mapping),
        **_copy_transcripts(root, old, new, mapping),
    }
    carried = bool(mapping or record["answers"] or record["transcripts"] or record["gens"])
    if carried:
        pending = _pending(root)
        pending[new] = record
        _set_pending(root, pending)
    run_config = mapping.get(default.config_id) if default is not None else None
    return {"run_config": run_config, "carried": carried}


def _copy_answers(root: str, old: str, new: str, mapping: dict[str, str]) -> bool:
    with scriptanswers._LOCK:
        scripts = scriptanswers._read(root, strict=True)
        entries = scripts.get(old) or []
        if not entries or scripts.get(new):
            return False
        old_ctx = scriptanswers._read_contexts(root, strict=True)
        new_ctx = dict(old_ctx)
        copied = []
        for e in entries:
            rc = e.get("run_config")
            moved = {**e, "run_config": mapping.get(rc, rc) if rc else None}
            copied.append(moved)
            key = scriptanswers._ctx_key(old, rc, e["index"], e["kind"], e["prompt"])
            if key in old_ctx:
                new_key = scriptanswers._ctx_key(
                    new, moved["run_config"], e["index"], e["kind"], e["prompt"]
                )
                new_ctx[new_key] = dict(old_ctx[key])
        scripts = {**scripts, new: copied}
        scriptanswers._commit(root, scripts, old_ctx, new_ctx)
        return True


def _copy_transcripts(root: str, old: str, new: str, mapping: dict[str, str]) -> dict:
    sep = inputtranscript._SEP

    def rekey(key: str) -> str | None:
        if key == old:
            return new
        if key.startswith(old + sep):
            rc = key[len(old) + len(sep) :]
            return new + sep + mapping.get(rc, rc)
        return None

    with inputtranscript._LOCK:
        bindings, gens = inputtranscript._load(root)
        added_b = {rekey(k): v for k, v in bindings.items() if rekey(k)}
        added_g = {rekey(k): v for k, v in gens.items() if rekey(k)}
        added_b = {k: v for k, v in added_b.items() if k not in bindings}
        added_g = {k: v for k, v in added_g.items() if k not in gens}
        if added_b or added_g:
            inputtranscript._write(root, {**bindings, **added_b}, {**gens, **added_g})
    return {"transcripts": sorted(added_b), "gens": sorted(added_g)}


def finish(project_root: str | Path, old: str, new: str, ok: bool) -> str:
    """接回的终局。`ok` 且注册表里新脚本确实认领了图 → `committed`（状态留在新键下）；否则 `aborted`（删掉这次加的）。
    没有待办 → `none`。"""
    root = os.fspath(project_root)
    pending = _pending(root)
    record = pending.get(new)
    if not isinstance(record, dict) or record.get("from") != old:
        return "none"
    if ok and _owns_figures(root, new):
        pending.pop(new, None)
        _set_pending(root, pending)
        return "committed"
    _undo(root, new, record)
    pending.pop(new, None)
    _set_pending(root, pending)
    return "aborted"


def settle_stale(project_root: str | Path) -> None:
    """上一次接回停在两步之间（关了页面 / 进程退出 / 等用户在弹框里定的那一下没回来）：按注册表此刻的事实收尾。
    不抛——收尾失败只记日志，待办留着下次再收。"""
    root = os.fspath(project_root)
    try:
        for new, record in list(_pending(root).items()):
            if isinstance(record, dict) and isinstance(record.get("from"), str):
                finish(root, record["from"], new, ok=_owns_figures(root, new))
    except Exception:  # noqa: BLE001
        LOG.warning("接回脚本的待办收尾失败（下次再收）", exc_info=True)


def _owns_figures(root: str, script: str) -> bool:
    reg = registry.Registry()
    try:
        reg.load(root)
    except (FileNotFoundError, RuntimeError, OSError):
        return False
    return bool(reg.stems_of(script))


def _undo(root: str, new: str, record: dict) -> None:
    ids = {v for v in (record.get("configs") or {}).values() if isinstance(v, str)}
    if ids:
        runconfig.forget_configs(root, new, ids)
    if record.get("answers"):
        with scriptanswers._LOCK:
            scripts = scriptanswers._read(root, strict=True)
            old_ctx = scriptanswers._read_contexts(root, strict=True)
            prefix = new + scriptanswers._SEP
            new_ctx = {k: v for k, v in old_ctx.items() if not k.startswith(prefix)}
            scripts = {k: v for k, v in scripts.items() if k != new}
            scriptanswers._commit(root, scripts, old_ctx, new_ctx)
    drop_b = set(record.get("transcripts") or [])
    drop_g = set(record.get("gens") or [])
    if drop_b or drop_g:
        with inputtranscript._LOCK:
            bindings, gens = inputtranscript._load(root)
            inputtranscript._write(
                root,
                {k: v for k, v in bindings.items() if k not in drop_b},
                {k: v for k, v in gens.items() if k not in drop_g},
            )
