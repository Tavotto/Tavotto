"""safe 档的工作目录模式：项目级开关「在脚本目录里运行」（ADR 0047）。

worker 默认把 cwd 切到会话沙盒——那是**写入**边界：脚本用相对路径写出 / 删除
的东西不碰真实图库。代价是脚本用相对路径**读**数据时只有 Python 的 `open`
被回退救得回来：`os.path.exists("1/data.txt")`、`glob("./**")`、ovito / h5py 的
C++ 读取器都在盲区，这类脚本在 Tavotto 里一张图都画不出来（真实来源：
2026-09-06 的九个 ovito 脚本）。而**脚本永远不改**——兼容性从产品侧来。

本模块只管一件事：这个项目的 safe worker 用哪个 cwd。取值只有两个
（`execspec.CWD_MODES`）：

* `sandbox`（默认）——现状；
* `project`——脚本自己所在的目录。解释器链、savefig 捕获（不落盘）、
  unlink / write_text 守卫、写回全部照旧；**只有脚本用相对路径写的中间文件会
  像终端里一样落进项目目录**。首次开启要在界面上确认一次（文案与机制逐条一致）。

设置存项目设置（`config.project_settings(<项目>)["workdir"]`），不写全局：
A 项目的脚本形状不该决定 B 项目的写入边界。**不是 native 档**：进程仍是
Tavotto 自己起的 safe worker（ADR 0021 §1 的所有权约束一个字没动）。
"""

from __future__ import annotations

import time
from pathlib import Path

from . import config, execspec

SETTINGS_KEY = "workdir"

MODE_SANDBOX = execspec.CWD_SANDBOX
MODE_PROJECT = execspec.CWD_PROJECT
MODES = execspec.CWD_MODES

#: 稳定错误码（协议契约）。`ERROR_CODES` 是给 `test_error_codes` 门禁读的注册表
#: ——它按字面量扫源码，端点里用常量它就看不见；注册表让它看**真正的出处**。
ERROR_MODE_INVALID = "workdir_mode_invalid"
ERROR_CODES = (ERROR_MODE_INVALID,)


def mode_for(figures_dir: str | Path) -> str:
    """这个项目的 safe worker 该用哪个 cwd。不认识的值一律当默认（沙盒）——
    设置文件被手改坏了不该让写入边界悄悄消失。"""
    stored = (config.project_settings(str(Path(figures_dir))) or {}).get(SETTINGS_KEY)
    mode = stored.get("mode") if isinstance(stored, dict) else None
    return mode if mode in MODES else MODE_SANDBOX


#: 授权记录的键（ADR 0053 / FO-047）。开到 `project` 那一下就是「真实 cwd 写入
#: 许可」的授予动作：确认文案在前端，**记账在这里**——之前后端不记「谁授权过」，
#: PreparationPlan 的 grant 字段无从填起。只记时刻不记人：本机单用户，没有第二个
#: 主体可区分；记一个 `user` 字面量是假信息。
GRANT_KEY = "granted_at"


def set_mode(figures_dir: str | Path, mode: str) -> dict:
    """记住这个项目的模式（项目级，不写全局）。默认模式 = 清掉这个键。

    切到 `project` 时随模式记下授予时刻（`granted_at`，epoch 秒）；已经是
    `project` 的再设一次**不刷新**时刻——授权是那一次点头，不是每次保存。
    切回 `sandbox` = 撤销：键整个清掉，授权记录随之消失。
    """
    if mode not in MODES:
        raise ValueError(f"workdir mode 非法: {mode!r}（可选 {MODES}）")
    root = str(Path(figures_dir))
    if mode == MODE_SANDBOX:
        stored = None
    else:
        stored = {"mode": mode}
        previous = (config.project_settings(root) or {}).get(SETTINGS_KEY)
        if isinstance(previous, dict) and previous.get("mode") == mode:
            granted = previous.get(GRANT_KEY)
            stored[GRANT_KEY] = granted if isinstance(granted, (int, float)) else time.time()
        else:
            stored[GRANT_KEY] = time.time()
    config.set_project_settings(root, {SETTINGS_KEY: stored})
    return state(root)


def grant_for(figures_dir: str | Path) -> dict:
    """这个项目「在脚本目录里运行」的授权记录——LaunchContext 的 `grant`。

    `granted` 只在模式真的是 `project` 时为 True；`granted_at` 是那一次授予的
    epoch 秒（老设置里没记过的回 `None`：**「授予过但没记时刻」与「没授予」是
    两个答案**，不许压成一个）。
    """
    stored = (config.project_settings(str(Path(figures_dir))) or {}).get(SETTINGS_KEY)
    granted = isinstance(stored, dict) and stored.get("mode") == MODE_PROJECT
    at = stored.get(GRANT_KEY) if granted else None
    return {
        "cwd_write": {
            "granted": bool(granted),
            "granted_at": at if isinstance(at, (int, float)) else None,
        }
    }


def state(figures_dir: str | Path) -> dict:
    """给环境状态 API 与诊断包：只读设置，不起任何子进程。"""
    return {"mode": mode_for(figures_dir), "modes": list(MODES), "grant": grant_for(figures_dir)}
