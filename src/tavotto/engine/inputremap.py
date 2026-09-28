"""数据找不到时，用户指认一次、按项目记住的只读改指表（ADR 0106）——父进程这一侧。

脚本要读的数据不在它写的那个位置（脚本被单独复制出来、数据被挪走、换了电脑），worker 以
`missing_input` 失败并说出缺的是哪一串（`figcapture.missing_input_of`）。界面请用户指认那个
文件或它所在的文件夹，本模块做三件事：

* `derive()` —— 从「脚本要的那串」与「用户指认的位置」推出**一条**规则（最长公共后缀）：
  `data/run1/x.csv` ↔ `/Volumes/B/proj/data/run1/x.csv` 推出「相对路径到 `/Volumes/B/proj` 找」；
  `/Users/a/proj/data/x.csv` ↔ `/Volumes/B/proj/data/x.csv` 推出「`/Users/a/proj` → `/Volumes/B/proj`」；
  文件改了名就只改指这一个文件。**不搜同名、不猜候选**（ADR 0057 / FO08）：位置全由用户指认。
* 规则的存取 —— 本机项目设置 `config.project_settings(<项目>)["input_remap"]`（与 `workdir` 同处，
  ADR 0106 §用户拍板 C）：规则里全是本机绝对路径，不进随项目走的 `tavottofile/`。
  `rules_for()` 是三条 spawn 路径取规则的**唯一出处**（写回的重放必须和热态读同一份数据）。
* `payload_for()` —— 弹窗要的结构化载荷：缺的那一串、脚本里别处写着、此刻也找不到的路径
  （`others`，让一个弹窗尽量一次问完），以及每一条能不能靠改指救回来。

规则怎么匹配（`remap_target`）只在 `figcapture` 里写一份：worker 真正改道、这里推规则后自检、
筛「这次指认会顺带修好哪些」用的是同一个函数。
"""

from __future__ import annotations

import ast
import glob
import os
import time
from pathlib import Path

from . import config, databinding, figcapture, projectenv

SETTINGS_KEY = "input_remap"

#: 稳定错误码（协议契约；`tests/test_error_codes.py` 读这张表）。
ERROR_CHOSEN_INVALID = "input_remap_chosen_invalid"
ERROR_NOT_FOUND_IN_DIR = "input_remap_not_found_in_dir"
ERROR_REQUESTED_INVALID = "input_remap_requested_invalid"
ERROR_RULE_UNKNOWN = "input_remap_rule_unknown"
ERROR_CODES = (
    ERROR_CHOSEN_INVALID,
    ERROR_NOT_FOUND_IN_DIR,
    ERROR_REQUESTED_INVALID,
    ERROR_RULE_UNKNOWN,
)

#: 载荷里 `others` 最多列几条（弹窗里一眼看得完）。
MAX_OTHERS = 20

#: 缺的东西是怎么被脚本用到的——决定改指救不救得回来。
VIA_OPEN = "open"  # 经四个打开入口读：改指救得回来
VIA_PROBE = "probe"  # exists / listdir / stat / import_file：改指救不回来（ADR 0106 §四）
VIA_GLOB = "glob"  # glob 模式：同上，而且不是一个文件


class RemapError(ValueError):
    """推规则失败：带稳定 code 与界面参数（文案归前端）。"""

    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code = code
        self.params = params


# ---------------------------------------------------------------- 存取


def rules_for(root: str | os.PathLike) -> list[dict]:
    """这个项目的改指表（校验过；坏条目丢掉）。三条 spawn 路径都从这里取。"""
    raw = config.project_settings(str(root)).get(SETTINGS_KEY)
    rules = raw.get("rules") if isinstance(raw, dict) else None
    return figcapture.clean_remap_rules(rules or [])


def _entries(root: str | os.PathLike) -> list[dict]:
    raw = config.project_settings(str(root)).get(SETTINGS_KEY)
    rules = raw.get("rules") if isinstance(raw, dict) else None
    out = []
    for r in rules or []:
        clean = figcapture.clean_remap_rules([r])
        if clean:
            out.append({**clean[0], "added_at": r.get("added_at")})
    return out


def state(root: str | os.PathLike) -> dict:
    """设置界面读：`{rules: [{kind, from, to, added_at, target_exists}]}`。"""
    rules = []
    for r in _entries(root):
        rules.append({**r, "target_exists": os.path.exists(r["to"])})
    return {"rules": rules}


def add_rule(root: str | os.PathLike, rule: dict) -> dict:
    """记一条规则（同 kind 同 `from` 的旧规则被替换——用户重新指认了同一处）。"""
    clean = figcapture.clean_remap_rules([rule])
    if not clean:
        raise RemapError(ERROR_REQUESTED_INVALID, "改指规则不合法")
    new = {**clean[0], "added_at": time.time()}
    kept = [
        r for r in _entries(root) if not (r["kind"] == new["kind"] and r["from"] == new["from"])
    ]
    rules = [*kept, new][-figcapture.MAX_REMAP_RULES :]
    config.set_project_settings(str(root), {SETTINGS_KEY: {"rules": rules}})
    return state(root)


def remove_rule(root: str | os.PathLike, kind: str, src: str) -> dict:
    """删一条规则；删了就是回到报错。没有这一条 → `input_remap_rule_unknown`。"""
    entries = _entries(root)
    kept = [r for r in entries if not (r["kind"] == kind and r["from"] == src)]
    if len(kept) == len(entries):
        raise RemapError(ERROR_RULE_UNKNOWN, "没有这条改指规则", source=src)
    config.set_project_settings(str(root), {SETTINGS_KEY: {"rules": kept} if kept else None})
    return state(root)


# ---------------------------------------------------------------- 推规则


def derive(requested: str, chosen: str, *, chosen_is_dir: bool) -> dict:
    """「脚本要的那串」+「用户指认的位置」→ 一条规则。推不出来抛 `RemapError`。

    * 指认的是**文件**：按路径段求最长公共后缀 k。k = 0（改了名）→ `file` 规则只改这一个；
      否则 `prefix` 规则：脚本那一侧去掉后缀的部分 → 指认那一侧去掉后缀的部分（相对路径整串都
      对上时 `from` 是 `""`：「相对路径都到这里找」）。
    * 指认的是**文件夹** D：从最长的后缀试起，第一个在 D 下存在的 `D/<后缀>` 定规则；都不存在
      → `input_remap_not_found_in_dir`（不替用户在 D 里往下搜）。

    推出来的规则当场自检：`remap_target([规则], requested)` 必须落到一个存在的文件上。
    """
    parsed = figcapture.remap_parts(requested)
    if parsed is None or not parsed[1] or parsed[1][-1] == "..":
        raise RemapError(ERROR_REQUESTED_INVALID, f"说不清脚本要的路径: {requested!r}")
    absolute, rparts = parsed
    # 绝对路径的根（`/` / `C:`）不参与后缀匹配
    body = rparts[1:] if absolute else rparts
    if not body:
        raise RemapError(ERROR_REQUESTED_INVALID, f"说不清脚本要的路径: {requested!r}")
    if not isinstance(chosen, str) or not os.path.isabs(chosen):
        raise RemapError(ERROR_CHOSEN_INVALID, "指认的位置必须是本机的绝对路径", path=str(chosen))
    chosen = os.path.abspath(chosen)
    if chosen_is_dir:
        if not os.path.isdir(chosen):
            raise RemapError(ERROR_CHOSEN_INVALID, f"不是文件夹: {chosen}", path=chosen)
        rule = None
        for k in range(len(body), 0, -1):
            if os.path.isfile(os.path.join(chosen, *body[-k:])):
                head = rparts[: len(rparts) - k]
                rule = {"kind": figcapture.REMAP_PREFIX, "from": _join(head), "to": chosen}
                break
        if rule is None:
            raise RemapError(
                ERROR_NOT_FOUND_IN_DIR,
                f"这个文件夹里没有 {body[-1]}",
                name=body[-1],
                path=chosen,
            )
    else:
        if not os.path.isfile(chosen):
            raise RemapError(ERROR_CHOSEN_INVALID, f"不是文件: {chosen}", path=chosen)
        cparsed = figcapture.remap_parts(chosen)
        cparts = cparsed[1] if cparsed else ()
        cbody = cparts[1:]
        k = 0
        while k < len(body) and k < len(cbody) and body[-1 - k] == cbody[-1 - k]:
            k += 1
        if k == 0:
            rule = {"kind": figcapture.REMAP_FILE, "from": requested, "to": chosen}
        else:
            rule = {
                "kind": figcapture.REMAP_PREFIX,
                "from": _join(rparts[: len(rparts) - k]),
                "to": _join(cparts[: len(cparts) - k]),
            }
    target = figcapture.remap_target([rule], requested)
    if target is None or not os.path.isfile(target):  # pragma: no cover — 上面已逐支保证
        raise RemapError(
            ERROR_NOT_FOUND_IN_DIR, "推出的规则落不到这个文件上", name=body[-1], path=chosen
        )
    return rule


def _join(parts: tuple[str, ...]) -> str:
    return figcapture._join_parts(tuple(parts))


# ---------------------------------------------------------------- 弹窗载荷


def _probe_via_of_constants(tree: ast.AST) -> dict[int, str]:
    """`id(Constant)` → `probe` / `glob`：这个常量是探路调用问的那条路径（绝对的也算）。

    与 `databinding.probe_literals` 同一张表（`PATH_PROBE_FUNCS` / `DIR_PROBE_FUNCS` /
    `GLOB_FUNCS` / `Path(<常量>)` 上的 `PATH_METHOD_PROBES` / `PATH_METHOD_GLOBS`）。那边只收相对
    目标；这里要的是**反过来**的事实——一个绝对常量若是被 `exists()` 问的，改指表救不回它，
    对话框不能给它选择器（否则指认 → 重跑 → `exists()` 照样 False → 同一个框再弹）。
    """
    out: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = databinding._func_name(node.func)
        first = node.args[0] if node.args else None
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
            ctor = node.func.value
            if (
                databinding._func_name(ctor.func) in databinding._PATH_CTORS
                and len(ctor.args) == 1
                and isinstance(ctor.args[0], ast.Constant)
            ):
                if name in databinding.PATH_METHOD_PROBES:
                    out[id(ctor.args[0])] = VIA_PROBE
                elif name in databinding.PATH_METHOD_GLOBS:
                    out[id(ctor.args[0])] = VIA_GLOB
        target = first
        if target is None:
            target = next(
                (k.value for k in node.keywords if k.arg in ("path", "top", "pathname")), None
            )
        if not isinstance(target, ast.Constant):
            continue
        if name in databinding.PATH_PROBE_FUNCS or name in databinding.DIR_PROBE_FUNCS:
            out[id(target)] = VIA_PROBE
        elif name in databinding.GLOB_FUNCS and databinding._is_glob_module_call(node.func):
            out[id(target)] = VIA_GLOB
    return out


def _absolute_literals(source: str) -> list[tuple[str, str]]:
    """脚本里**以字符串常量出现**的绝对数据路径（存图调用的实参除外）→ `[(路径, via)]`。只认常量，不求值。

    `via` 按常量所在的调用判：被探路调用问的是 `probe` / `glob`，其余是 `open`；同一串在几处出现、
    有一处是探路就算探路（与相对路径「同一串也被探路问过」同一条规则）。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    outputs: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and databinding._func_name(node.func) in databinding.SAVE_FUNCS
        ):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    outputs.add(arg.value)
    probed = _probe_via_of_constants(tree)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value
        if text in outputs:
            continue
        via = probed.get(id(node), VIA_OPEN)
        if text in out:
            if via != VIA_OPEN:
                out[text] = via
            continue
        parsed = figcapture.remap_parts(text)
        if parsed is None or not parsed[0] or len(parsed[1]) < 2:
            continue
        tail = "/".join(parsed[1][1:])
        # 去掉根之后按「像不像数据路径」的同一份判据判（扩展名 / 目录分隔符）；glob 模式那一判
        # 会因通配符拒掉，它已经知道是 glob 的实参，按 glob 目标收
        if via == VIA_GLOB or databinding.looks_like_relative_data_path(tail):
            if len(out) >= databinding.MAX_LITERALS:
                break
            out[text] = via
    return list(out.items())


def _script_source(root: Path, script_path: Path) -> str:
    real = projectenv.contained_path(root, script_path)
    if real is None:
        return ""
    try:
        return Path(real).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _resolved(rules: list[dict], text: str) -> bool:
    target = figcapture.remap_target(rules, text) if rules else None
    return target is not None and os.path.exists(target)


def static_missing(
    script: str, root: str | os.PathLike, rules: list[dict] | None = None
) -> list[dict]:
    """脚本里写着、此刻哪里都找不到的路径：`[{path, absolute, via}]`（按出现顺序，最多 `MAX_OTHERS`）。

    相对的看 `databinding.evidence`：脚本目录与项目根**两处都** `missing`（项目外的 `outside` 不算，
    那里不看）；探路目标同理，`via` 标成 `probe` / `glob`。绝对的只 `os.path.exists`（不读、不列目录）。
    已经被改指表救回来的不列。
    """
    root_path = Path(root)
    script_path = root_path / figcapture.normalize_relative_script(script)
    rules = rules if rules is not None else rules_for(root)
    ev = databinding.evidence(script_path, root_path)
    cands = ev.get("candidates") or {}
    parent = cands.get(databinding.CANDIDATE_SCRIPT_PARENT) or {}
    top = cands.get(databinding.CANDIDATE_PROJECT_ROOT) or {}
    out: list[dict] = []

    def _add(path: str, absolute: bool, via: str) -> None:
        if len(out) >= MAX_OTHERS or any(o["path"] == path for o in out):
            return
        if _resolved(rules, path):
            return
        out.append({"path": path, "absolute": absolute, "via": via})

    probe_targets = set(ev.get("probes") or [])
    for lit in ev.get("reads") or []:
        if lit in (parent.get("missing") or []) and lit in (top.get("missing") or []):
            # 同一串也被 exists / listdir 问过：脚本多半先判再读，改指救不回那一问——按探路算
            _add(lit, False, VIA_PROBE if lit in probe_targets else VIA_OPEN)
    p_probes = (parent.get("probes") or {}).get("missing") or []
    t_probes = (top.get("probes") or {}).get("missing") or []
    for target in ev.get("probes") or []:
        if target in p_probes and target in t_probes:
            via = VIA_GLOB if any(c in target for c in "*?[") else VIA_PROBE
            _add(target, False, via)
    for text, via in _absolute_literals(_script_source(root_path, script_path)):
        try:
            if via == VIA_GLOB:
                # 父进程里同步判：`**` 递归可能扫整棵树，判不出就不列；其余非递归、见到一个就停
                present = "**" in text or next(glob.iglob(text), None) is not None
            else:
                present = os.path.exists(text)
        except (OSError, ValueError):
            present = True  # 判不出就不列
        if not present:
            _add(text, True, via)
    return out


def payload_for(
    script: str, root: str | os.PathLike, missing: dict | None, *, rules: list[dict] | None = None
) -> dict | None:
    """弹窗载荷。`missing` 是 worker 报的 `missing_input` 事实（没有就是「没出图」路径，只剩静态的）。

    `{script, requested, absolute, via, others: [{path, absolute, via}]}`——`requested` 为 None 时
    界面以 `others` 的第一条为主。两样都没有回 None（没什么可问的）。
    """
    rules = rules if rules is not None else rules_for(root)
    others = static_missing(script, root, rules)
    requested = None
    absolute = False
    via = VIA_OPEN
    if isinstance(missing, dict) and isinstance(missing.get("requested"), str):
        requested = missing["requested"]
        absolute = bool(missing.get("absolute"))
        others = [o for o in others if o["path"] != requested]
    if requested is None and not others:
        return None
    return {
        "script": script,
        "requested": requested,
        "absolute": absolute,
        "via": via,
        "others": others,
    }
