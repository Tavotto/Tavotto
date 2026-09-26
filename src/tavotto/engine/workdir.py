"""safe 档的工作目录模式：项目级开关「在脚本目录里运行」（ADR 0047）与「在项目根运行」
（ADR 0057），以及首开时那一次确认的判据。

worker 默认把 cwd 切到会话沙盒——那是**写入**边界：脚本用相对路径写出 / 删除
的东西不碰真实图库。代价是脚本用相对路径**读**数据时只有 Python 的 `open`
被回退救得回来：`os.path.exists("1/data.txt")`、`glob("./**")`、ovito / h5py 的
C++ 读取器都在盲区，这类脚本在 Tavotto 里一张图都画不出来（真实来源：
2026-09-06 的九个 ovito 脚本）。而**脚本永远不改**——兼容性从产品侧来。

本模块只管一件事：这个项目的 safe worker 用哪个 cwd。取值只有三个
（`execspec.CWD_MODES`）：

* `sandbox`（默认）——现状；
* `project`——脚本自己所在的目录。解释器链、savefig 捕获（不落盘）、
  unlink / write_text 守卫、写回全部照旧；**只有脚本用相对路径写的中间文件会
  像终端里一样落进项目目录**。首次开启要在界面上确认一次（文案与机制逐条一致）；
* `project_root`——项目根（Tavotto 打开的那个目录），其余与 `project` 相同。
  `paper/scripts/figure.py` 读 `data/x.csv` 的项目（FO02）要的是这一档。

设置存项目设置（`config.project_settings(<项目>)["workdir"]`），不写全局：
A 项目的脚本形状不该决定 B 项目的写入边界。**不是 native 档**：进程仍是
Tavotto 自己起的 safe worker（ADR 0021 §1 的所有权约束一个字没动）。

## 决定过没有（U03，ADR 0057）

设置里**没有** `workdir` 键 = 这个项目还没决定过。此前「没决定」与「决定用沙盒」是同一个
答案（键不存在），于是首开时没法知道该不该问。现在三个答案分开：

* 键不存在 —— 没决定：第一次起 worker 之前按脚本的静态证据（`databinding.evidence`）
  判要不要问，问了就记住；
* `{"mode": "sandbox", "decided_at": t}` —— 决定过：继续用沙盒（用户在确认框里选的，或在设置里
  切回来的）。没有授权记录；
* `{"mode": "project" | "project_root", "granted_at": t}` —— 决定过且授予了真实 cwd 写入许可。

`resolve_mode()` 是三条 spawn 路径**之前**的那道门：决定过的直接回模式；没决定过而证据说
默认（沙盒 + 只读回退到脚本目录）不够用（数据只在项目根；或脚本用 `glob` / `listdir` / `exists`
探路、只在脚本目录找得到——回退救不回它们，ADR 0084）、或两处都有同名而内容不同的数据时，抛
`ConfirmationRequired`（带选项与证据）——**不猜、不就近替换、不自动切到真实 cwd**
（ADR 0047「不自动切换」原样成立）。证据说不出话（没有相对路径字面量 / 一处都找不到）
时走默认，真跑出来的失败仍经既有的 `no_figures_captured` 路径可见。
"""

from __future__ import annotations

import time
from pathlib import Path

from . import config, databinding, execspec, figcapture

SETTINGS_KEY = "workdir"

MODE_SANDBOX = execspec.CWD_SANDBOX
MODE_PROJECT = execspec.CWD_PROJECT
MODE_PROJECT_ROOT = execspec.CWD_PROJECT_ROOT
MODES = execspec.CWD_MODES
#: 会授予「真实 cwd 写入许可」的两档。
GRANTING_MODES = (MODE_PROJECT, MODE_PROJECT_ROOT)

#: 稳定错误码（协议契约）。`ERROR_CODES` 是给 `test_error_codes` 门禁读的注册表
#: ——它按字面量扫源码，端点里用常量它就看不见；注册表让它看**真正的出处**。
ERROR_MODE_INVALID = "workdir_mode_invalid"
#: 首开需要用户决定 cwd（U03）：不是失败，是「需要输入」。四类入口同一个 code：
#: HTTP 渲染 / 准备接口的 `needs_input` / MCP 的结构化错误 / probe 的失败原因。
ERROR_CONFIRMATION_REQUIRED = "workdir_confirmation_required"
ERROR_CODES = (ERROR_MODE_INVALID, ERROR_CONFIRMATION_REQUIRED)

#: 需要确认的两种理由（闭集；界面按它换文案）。
REASON_PROJECT_ROOT_EVIDENCE = "project_root_evidence"
REASON_AMBIGUOUS_DATA = "ambiguous_data"
#: 脚本用 `glob` / `listdir` / `exists` 这类探路调用找数据、只有脚本目录下找得到（ADR 0084）：
#: 沙盒的只读回退救不回它们，推荐「脚本所在目录」。
REASON_SCRIPT_DIR_EVIDENCE = "script_dir_evidence"
CONFIRMATION_REASONS = (
    REASON_PROJECT_ROOT_EVIDENCE,
    REASON_AMBIGUOUS_DATA,
    REASON_SCRIPT_DIR_EVIDENCE,
)
#: 结论 → 要问的理由；不在表里的结论不问。
_REASON_OF_VERDICT = {
    databinding.VERDICT_PROJECT_ROOT: REASON_PROJECT_ROOT_EVIDENCE,
    databinding.VERDICT_AMBIGUOUS: REASON_AMBIGUOUS_DATA,
    databinding.VERDICT_SCRIPT_PARENT: REASON_SCRIPT_DIR_EVIDENCE,
}
_MESSAGE_OF_REASON = {
    REASON_PROJECT_ROOT_EVIDENCE: "脚本读的数据只有在项目根目录下才找得到，请先选择它的运行目录",
    REASON_AMBIGUOUS_DATA: "脚本目录与项目根目录各有一份同名数据且内容不同，请先选择它的运行目录",
    REASON_SCRIPT_DIR_EVIDENCE: (
        "脚本在当前目录里查找数据文件（glob / listdir / exists），在沙盒里找不到，请先选择它的运行目录"
    ),
}


def _stored(figures_dir: str | Path) -> dict | None:
    stored = (config.project_settings(str(Path(figures_dir))) or {}).get(SETTINGS_KEY)
    return stored if isinstance(stored, dict) else None


def mode_for(figures_dir: str | Path) -> str:
    """这个项目的 safe worker 该用哪个 cwd。不认识的值一律当默认（沙盒）——
    设置文件被手改坏了不该让写入边界悄悄消失。"""
    stored = _stored(figures_dir)
    mode = stored.get("mode") if stored else None
    return mode if mode in MODES else MODE_SANDBOX


def decided(figures_dir: str | Path) -> bool:
    """这个项目决定过工作目录模式没有（键在且模式合法）。「没决定」≠「决定用沙盒」。"""
    stored = _stored(figures_dir)
    return bool(stored) and stored.get("mode") in MODES


#: 授权记录的键（ADR 0053 / FO-047）。开到 `project` / `project_root` 那一下就是「真实
#: cwd 写入许可」的授予动作：确认文案在前端，**记账在这里**——之前后端不记「谁授权过」，
#: PreparationPlan 的 grant 字段无从填起。只记时刻不记人：本机单用户，没有第二个
#: 主体可区分；记一个 `user` 字面量是假信息。
GRANT_KEY = "granted_at"
#: 「决定继续用沙盒」的时刻——它不是授权（沙盒不需要授权），只是「问过了、答了」。
DECIDED_KEY = "decided_at"


def set_mode(figures_dir: str | Path, mode: str) -> dict:
    """记住这个项目的模式（项目级，不写全局）。

    切到 `project` / `project_root` 时随模式记下授予时刻（`granted_at`，epoch 秒）；
    已经是同一个模式的再设一次**不刷新**时刻——授权是那一次点头，不是每次保存；
    从一个授权模式换到另一个（脚本目录 ↔ 项目根）是新的一次点头，时刻重记。
    切到 `sandbox` = 撤销授权：`granted_at` 消失，但**决定本身记下来**
    （`decided_at`）——首开的确认框不会因为用户选了沙盒就每次再问一遍。
    """
    if mode not in MODES:
        raise ValueError(f"workdir mode 非法: {mode!r}（可选 {MODES}）")
    root = str(Path(figures_dir))
    previous = _stored(root)
    if mode == MODE_SANDBOX:
        stored = {"mode": mode, DECIDED_KEY: time.time()}
    else:
        stored = {"mode": mode}
        if previous is not None and previous.get("mode") == mode:
            granted = previous.get(GRANT_KEY)
            stored[GRANT_KEY] = granted if isinstance(granted, (int, float)) else time.time()
        else:
            stored[GRANT_KEY] = time.time()
    config.set_project_settings(root, {SETTINGS_KEY: stored})
    return state(root)


def forget(figures_dir: str | Path) -> None:
    """回到「没决定过」：键整个清掉（测试与「重新询问」用）。"""
    config.set_project_settings(str(Path(figures_dir)), {SETTINGS_KEY: None})


def grant_for(figures_dir: str | Path) -> dict:
    """这个项目「在真实目录里运行」的授权记录——LaunchContext 的 `grant`。

    `granted` 只在模式真的是 `project` / `project_root` 时为 True；`granted_at` 是那一次
    授予的 epoch 秒（老设置里没记过的回 `None`：**「授予过但没记时刻」与「没授予」是
    两个答案**，不许压成一个）。`mode` 是授予的是哪一档；`decided` 是这个项目决定过没有
    （沙盒也算决定过，只是没有授权）。
    """
    stored = _stored(figures_dir)
    mode = stored.get("mode") if stored else None
    granted = mode in GRANTING_MODES
    at = stored.get(GRANT_KEY) if granted else None
    return {
        "cwd_write": {
            "granted": bool(granted),
            "granted_at": at if isinstance(at, (int, float)) else None,
            "mode": mode if granted else None,
        },
        "decided": mode in MODES,
    }


def state(figures_dir: str | Path) -> dict:
    """给环境状态 API 与诊断包：只读设置，不起任何子进程。"""
    return {
        "mode": mode_for(figures_dir),
        "modes": list(MODES),
        "decided": decided(figures_dir),
        "grant": grant_for(figures_dir),
    }


# ---------------------------------------------------------------- 首开的那一道门


class ConfirmationRequired(Exception):
    """首开需要用户决定工作目录（U03）。`payload` 是给四类入口共用的结构化「需要输入」。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.code = ERROR_CONFIRMATION_REQUIRED
        super().__init__(payload.get("message", "需要先选择脚本的运行目录"))


def confirmation_payload(script: str, evidence: dict) -> dict:
    """证据 → 「需要输入」的结构化载荷（机器路径一个都不带；`found` 只有字面量）。

    `options` 按固定顺序列三档（项目根 / 脚本目录 / 沙盒），每档带**这一档下找得到的
    字面量**（探路目标——glob 模式、列的目录——也算，但沙盒那档不算：回退救不回它们）；
    `recommended` 只在证据唯一指向一个目录时给（项目根 / 脚本目录；歧义时 None——界面不
    预选，机器不裁决）。
    """
    cands = evidence.get("candidates") or {}
    root_cand = cands.get(databinding.CANDIDATE_PROJECT_ROOT) or {}
    parent_cand = cands.get(databinding.CANDIDATE_SCRIPT_PARENT) or {}
    in_root = sorted(root_cand.get("found") or {})
    in_parent = sorted(parent_cand.get("found") or {})
    probes_root = list((root_cand.get("probes") or {}).get("found") or [])
    probes_parent = list((parent_cand.get("probes") or {}).get("found") or [])
    verdict = evidence.get("verdict")
    reason = _REASON_OF_VERDICT.get(verdict, REASON_PROJECT_ROOT_EVIDENCE)
    recommended = {
        databinding.VERDICT_PROJECT_ROOT: MODE_PROJECT_ROOT,
        databinding.VERDICT_SCRIPT_PARENT: MODE_PROJECT,
    }.get(verdict)
    options = [
        {
            "mode": MODE_PROJECT_ROOT,
            "cwd_origin": execspec.CWD_ORIGIN_PROJECT_ROOT,
            "write_mode": execspec.WRITE_MODE_PROJECT_DIR,
            "found": in_root + probes_root,
            "recommended": recommended == MODE_PROJECT_ROOT,
        },
        {
            "mode": MODE_PROJECT,
            "cwd_origin": execspec.CWD_ORIGIN_SCRIPT_PARENT,
            "write_mode": execspec.WRITE_MODE_PROJECT_DIR,
            "found": in_parent + probes_parent,
            "recommended": recommended == MODE_PROJECT,
        },
        {
            "mode": MODE_SANDBOX,
            "cwd_origin": execspec.CWD_ORIGIN_SANDBOX,
            "write_mode": execspec.WRITE_MODE_SANDBOXED,
            # 沙盒的只读回退看的是脚本目录，所以它「找得到」的是脚本目录那档的打开类字面量；
            # 探路目标不算——回退救不回 glob / listdir / exists（ADR 0084）
            "found": in_parent,
            "recommended": False,
        },
    ]
    return {
        "kind": "workdir",
        "code": ERROR_CONFIRMATION_REQUIRED,
        "script": script.replace("\\", "/"),
        "reason": reason,
        "recommended": recommended,
        "options": options,
        "conflicts": list(evidence.get("conflicts") or []),
        "reads": list(evidence.get("reads") or []),
        "probes": list(evidence.get("probes") or []),
        "message": _MESSAGE_OF_REASON[reason],
        # 怎么回答：四类入口同一条路（PATCH /api/engine/workdir {mode}）
        "answer": {"http": "PATCH /api/engine/workdir", "body": {"mode": "<options[*].mode>"}},
    }


def decision_for(figures_dir: str | Path, script: str) -> dict:
    """这个项目 × 这个脚本此刻的工作目录决定——**只读**，不抛。

    回 `{"mode", "decided", "needs_confirmation", "evidence", "confirmation"}`：
    决定过 → `mode` 是记住的那一档、`needs_confirmation=False`；没决定过 → 按证据：
    要问的 `needs_confirmation=True` 且 `confirmation` 是载荷（`mode` 仍是默认沙盒——
    那只是「没问到之前的默认」，**不是**决定）。准备计划与 `resolve_mode()` 读同一份。
    """
    root = str(Path(figures_dir))
    if decided(root):
        return {
            "mode": mode_for(root),
            "decided": True,
            "needs_confirmation": False,
            "evidence": None,
            "confirmation": None,
        }
    script_path = Path(root) / figcapture.normalize_relative_script(script)
    ev = databinding.evidence(script_path, root)
    needs = ev["verdict"] in _REASON_OF_VERDICT
    return {
        "mode": MODE_SANDBOX,
        "decided": False,
        "needs_confirmation": needs,
        "evidence": ev,
        "confirmation": confirmation_payload(script, ev) if needs else None,
    }


def resolve_mode(figures_dir: str | Path, script: str) -> str:
    """spawn 之前的那道门：决定过就用记住的；没决定过而证据要求确认就抛
    `ConfirmationRequired`；否则默认沙盒。"""
    decision = decision_for(figures_dir, script)
    if decision["needs_confirmation"]:
        raise ConfirmationRequired(decision["confirmation"])
    return decision["mode"]
