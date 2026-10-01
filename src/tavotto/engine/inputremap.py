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
import contextlib
import glob
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from . import config, databinding, figcapture, projectenv

SETTINGS_KEY = "input_remap"

#: 稳定错误码（协议契约；`tests/test_error_codes.py` 读这张表）。
ERROR_CHOSEN_INVALID = "input_remap_chosen_invalid"
ERROR_NOT_FOUND_IN_DIR = "input_remap_not_found_in_dir"
ERROR_REQUESTED_INVALID = "input_remap_requested_invalid"
ERROR_RULE_UNKNOWN = "input_remap_rule_unknown"
#: 在途的工作是按旧改指表跑的、落地之前表已经变了：结果丢弃，**可重试**（界面重排 / 重跑，不是失败）
ERROR_CHANGED = "input_remap_changed"
ERROR_CODES = (
    ERROR_CHOSEN_INVALID,
    ERROR_NOT_FOUND_IN_DIR,
    ERROR_REQUESTED_INVALID,
    ERROR_RULE_UNKNOWN,
    ERROR_CHANGED,
)

#: 载荷里 `others` 最多列几条（弹窗里一眼看得完）。
MAX_OTHERS = 20

#: 缺的东西是怎么被脚本用到的——决定改指救不救得回来。
VIA_OPEN = "open"  # 经四个打开入口读：改指救得回来
VIA_PROBE = "probe"  # exists / listdir / stat / import_file：改指救不回来（ADR 0106 §四）
VIA_GLOB = "glob"  # glob 模式：同上，而且不是一个文件
#: C++ 读取器（h5py / netCDF4 / xarray）的 ENOENT 对上了脚本里的一串常量（ADR 0110 §一）：同样救不回
VIA_NATIVE = "native"
#: 改指表救不回、只能改写脚本里那串常量的几档（ADR 0110 §八：对话框只给它们「改写脚本」）
VIAS_NEED_REWRITE = (VIA_PROBE, VIA_GLOB, VIA_NATIVE)

#: 那条路径**要的是什么**（载荷的 `probe_kind`，ADR 0110 §三）：`dir` 只能指认文件夹（`listdir` 问的
#: 换成一个文件，改写后重跑就是 `NotADirectoryError`）；`file` 是文件；`any` 两者都行。
PROBE_DIR = "dir"
PROBE_FILE = "file"
PROBE_ANY = "any"
#: 探路调用 → 它问的那条路径要是什么。键**恰好**是 databinding 三张探路表的并集
#: （`PATH_PROBE_FUNCS` / `DIR_PROBE_FUNCS` / `PATH_METHOD_PROBES`，同名的语义相同），用例对账——
#: 那边加一个探路函数、这里忘了标，就红。
PROBE_KIND_OF = {
    # os.path.* / os.*：问的路径本身
    "exists": PROBE_ANY,
    "lexists": PROBE_ANY,
    "stat": PROBE_ANY,
    "lstat": PROBE_ANY,
    "getsize": PROBE_ANY,
    "getmtime": PROBE_ANY,
    "isfile": PROBE_FILE,
    "isdir": PROBE_DIR,
    "import_file": PROBE_FILE,  # ovito：读一个数据文件
    # 列目录
    "listdir": PROBE_DIR,
    "scandir": PROBE_DIR,
    "walk": PROBE_DIR,
    # Path(<常量>) 上的方法
    "is_file": PROBE_FILE,
    "is_dir": PROBE_DIR,
    "iterdir": PROBE_DIR,
}


def _merged_kind(kinds) -> str:
    """同一串被几处问过：只要文件夹 / 只要文件的那一边说了算；两边都有或都没说就是 `any`。"""
    kinds = set(kinds or ())
    if PROBE_DIR in kinds and PROBE_FILE not in kinds:
        return PROBE_DIR
    if PROBE_FILE in kinds and PROBE_DIR not in kinds:
        return PROBE_FILE
    return PROBE_ANY


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


def fingerprint(root: str | os.PathLike) -> str:
    """这个项目改指表的指纹（空表 = `""`）：runtime 素材的 cache 记下物化时的这一份，
    表一变（增 / 换 / 删）就对不上 → `possibly_stale`（经旧规则读到的是旧位置的数据）。"""
    rules = rules_for(root)
    if not rules:
        return ""
    canon = json.dumps(
        sorted([r["kind"], r["from"], r["to"]] for r in rules), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canon).hexdigest()


# ---------------------------------------------------------------- 代次与项目互斥锁（ADR 0106 §五）
#
# 改指表每次成功改动（增 / 换 / 删）代次 +1。依赖映射的后端工作（起 worker 时取规则、试运行登记、runtime
# 物化、写回、导出发布）在**开始时**记下代次（`snapshot()` 与规则一起取，二者同一刻），落地时进
# `landing()`：持**这个项目的互斥锁**核对代次，并且**一直持到提交完成**——对不上就丢弃、报
# `input_remap_changed`（可重试）。同一把锁还护着：改指表的读 / 改 / 写与换代、`state()` / `snapshot()` 的
# 「表 + 代次」一起取、注册表文件的整段读改写（`discover.register`）与登记标记（`record_registration`）。
#
# 一把锁而不是读写锁（用户 2026-09-30 拍板）：读写锁下每轮都还能找出新的并发窗口（读者之间的注册表读改写、
# 读表与读代次之间……），逐个补不收敛；一把按项目的互斥锁一次消掉整类竞态。代价：同一项目的写回、导出发布、
# 登记彼此串行——都只是落地那一小段（长活——跑脚本、渲染——在锁外），桌面上感觉不到。
#
# **锁序**：池锁（`pool._lock`）→ 项目锁。池在自己的锁里起会话会调 `snapshot()`（拿项目锁）；所以**持项目锁
# 时绝不取池锁**——锁里不取会话、不起会话、不跑脚本（物化先在锁外取会话，再进锁）。配置锁是叶子。
_META = threading.Lock()
_GENERATIONS: dict[str, int] = {}
#: 代次的起点：进程启动时刻的毫秒数。进程内的代次只增不减，**跨重启也单调**——重启后的任何一代都大于重启前
#: 见过的任何一代（一次重启之间换不了几千代），前端按「代次 ≤ 已见就忽略」去重、重连补拉时发现变大就补一次
#: 作废，不会被重启归零骗过去
_BASE = int(time.time() * 1000)
_MUTEXES: dict[str, threading.RLock] = {}


class RemapChanged(RuntimeError):
    """落地之前改指表已经变了：这份结果是按旧表产出的。"""

    code = ERROR_CHANGED


def _gen_key(root: str | os.PathLike) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(root)))


def project_mutex(root: str | os.PathLike) -> threading.RLock:
    """这个项目的互斥锁（可重入）。持有它时不许取池锁（锁序见上）。"""
    key = _gen_key(root)
    with _META:
        lock = _MUTEXES.get(key)
        if lock is None:
            lock = _MUTEXES[key] = threading.RLock()
        return lock


def generation(root: str | os.PathLike) -> int:
    """这个项目改指表此刻的代次（起点 `_BASE`，只增不减）。单读一个整数；要与规则一起取用 `snapshot()`。"""
    key = _gen_key(root)
    with _META:
        return _GENERATIONS.get(key, _BASE)


def snapshot(root: str | os.PathLike) -> tuple[int, list[dict]]:
    """`(代次, 规则)`——在项目锁里一起取（改表与换代也在这把锁里，读不到半截）。起 worker 的三条路径都从这里取。"""
    with project_mutex(root):
        return generation(root), rules_for(root)


@contextlib.contextmanager
def landing(root: str | os.PathLike, *generations: int | None):
    """持项目锁核对：给的每个代次都还是此刻的代次，才让块里的落地发生；对不上抛 `RemapChanged`。
    `None` = 这份工作不带代次（测试替身 / 与映射无关），不拦。**整个块都在锁里**：块里写的就是提交本身
    （写回的备份 + 替换循环、导出的发布循环、登记、物化），改指等它们落完才能改表。"""
    with project_mutex(root):
        now = generation(root)
        stale = [g for g in generations if g is not None and g != now]
        if stale:
            raise RemapChanged(f"改指表已变（{stale[0]} → {now}）：这份结果按旧表产出，丢弃")
        yield


#: 本机项目设置里记「这个脚本是在哪一张改指表下由试运行登记的」：`{脚本: 指纹}`。注册表
#: （`tavotto_registry.json`）随项目走、不放本机路径的派生物，所以不写进注册表本身；进程内的代次重启就归零，
#: 能跨重启对账的是指纹。
REGISTERED_KEY = "input_remap_registered"


def record_registration(root: str | os.PathLike, script: str) -> None:
    """试运行按此刻的改指表登记了 `script` 的 stems：记下这张表的指纹（在 `landing()` 里调）。"""
    with project_mutex(root):
        raw = config.project_settings(str(root)).get(REGISTERED_KEY)
        marks = dict(raw) if isinstance(raw, dict) else {}
        marks[script] = fingerprint(root)
        config.set_project_settings(str(root), {REGISTERED_KEY: marks})


def registration_stale(root: str | os.PathLike, script: str) -> bool:
    """`script` 的 stems 是在另一张改指表下试运行登记的（stems 可能由数据决定）。

    没有标记的（这个机制之前登记的、或别处登记的）按**保守对账**处理：项目里有任何改指规则就算过期——
    不知道它是在哪张表下登记的，就当不是这一张，下一次 build 按真实产出重新登记一次；没有规则时
    维持原样（它只可能是在「无表」下登记的，Codex 评 #716 P1）。"""
    raw = config.project_settings(str(root)).get(REGISTERED_KEY)
    current = fingerprint(root)
    if not isinstance(raw, dict) or script not in raw:
        return bool(current)
    return raw[script] != current


def _bump(root: str | os.PathLike) -> int:
    """换代（只在项目锁里调）；回新代次。"""
    key = _gen_key(root)
    with _META:
        _GENERATIONS[key] = _GENERATIONS.get(key, _BASE) + 1
        return _GENERATIONS[key]


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
    """设置界面读：`{rules: [{kind, from, to, added_at, target_exists}], generation}`。`generation` 是此刻的
    代次：改指 / 删指的响应带着它，发起的窗口收到就本地作废；事件流重连 / 页面恢复时拉一次，落后就补一次
    （前端按代次去重，ADR 0106 §五）。"""
    # 表与代次在同一次持锁里取：分开取的话，读到旧表 + 新代次，前端记下新代次、把随后的事件当「已见」忽略，
    # 设置里的列表就一直是旧的（Codex 评 #716 P2）
    with project_mutex(root):
        entries = _entries(root)
        gen = generation(root)
    rules = [{**r, "target_exists": os.path.exists(r["to"])} for r in entries]
    return {"rules": rules, "generation": gen}


def _same_source(a: dict, b: dict) -> bool:
    """两条规则的 `from` 是不是同一处：按 `remap_parts` 规范化之后比（`data` / `./data` / `data/`、
    Windows 的反斜杠写法、盘符大小写都是同一处），同 kind 才算。"""
    if a["kind"] != b["kind"]:
        return False
    if a["from"] == b["from"]:
        return True
    pa = figcapture.remap_parts(a["from"]) if a["from"] else (False, ())
    pb = figcapture.remap_parts(b["from"]) if b["from"] else (False, ())
    return pa is not None and pa == pb


def add_rule(root: str | os.PathLike, rule: dict) -> dict:
    """记一条规则。同 kind、`from` 规范化后是同一处的旧规则被替换——用户重新指认了同一处，最新的那条胜出
    （按原串比的话 `./data` 与 `data` 两条并存，`remap_target` 取先到的那条，新指认被旧的遮住，Codex 评 #716 P2）。"""
    clean = figcapture.clean_remap_rules([rule])
    if not clean:
        raise RemapError(ERROR_REQUESTED_INVALID, "改指规则不合法")
    new = {**clean[0], "added_at": time.time()}
    # 读、改、写、换代整段持项目锁：两个窗口同时指认，后写的不许吞掉先写的那条（Codex 评 #716 P2）；
    # 在途的落地（写回 / 导出发布 / 登记 / 物化）先落完
    with project_mutex(root):
        kept = [r for r in _entries(root) if not _same_source(r, new)]
        rules = [*kept, new][-figcapture.MAX_REMAP_RULES :]
        config.set_project_settings(str(root), {SETTINGS_KEY: {"rules": rules}})
        _bump(root)
        return state(root)


def remove_rule(root: str | os.PathLike, kind: str, src: str) -> dict:
    """删一条规则；删了就是回到报错。没有这一条 → `input_remap_rule_unknown`。"""
    with project_mutex(root):
        entries = _entries(root)
        kept = [r for r in entries if not _same_source(r, {"kind": kind, "from": src})]
        if len(kept) == len(entries):
            raise RemapError(ERROR_RULE_UNKNOWN, "没有这条改指规则", source=src)
        config.set_project_settings(str(root), {SETTINGS_KEY: {"rules": kept} if kept else None})
        _bump(root)
        return state(root)


# ---------------------------------------------------------------- 推规则


def chosen_path(chosen: str) -> str:
    """用户亲手选的位置：只接受本机绝对路径；任何磁盘探测之前先判，不改原生大小写 / UNC 写法。"""
    if (
        not isinstance(chosen, str)
        or "\x00" in chosen
        or not os.path.isabs(chosen)
        or (os.path.sep == "\\" and not os.path.splitdrive(chosen)[0])
    ):
        raise RemapError(ERROR_CHOSEN_INVALID, "指认的位置必须是本机的绝对路径", path=str(chosen))
    drive = os.path.splitdrive(chosen)[0]
    if os.path.sep == "\\" and drive.startswith(("\\\\", "//")):
        if len(drive.replace("\\", "/").strip("/").split("/")) < 2:
            raise RemapError(ERROR_CHOSEN_INVALID, "UNC 路径需要指明共享目录", path=chosen)
    return os.path.abspath(chosen)


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
    chosen = chosen_path(chosen)
    if chosen_is_dir:
        if not os.path.isdir(chosen):
            raise RemapError(ERROR_CHOSEN_INVALID, f"不是文件夹: {chosen}", path=chosen)
        rule = None
        for k in range(len(body), 0, -1):
            candidate = projectenv.contained_path(chosen, os.path.join(chosen, *body[-k:]))
            if candidate is not None and os.path.isfile(candidate):
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
                "to": _ancestor(chosen, k),
            }
    target = figcapture.remap_target([rule], requested)
    target = projectenv.contained_path(rule["to"], target) if target is not None else None
    if target is None and not chosen_is_dir:
        # 用户直接选中了指向别处的文件链接：许可的是这个文件，不是它字面上的父目录。
        # 推不出包含实体的目录规则时收窄成精确文件规则，不拒掉这次明确选择。
        rule = {"kind": figcapture.REMAP_FILE, "from": requested, "to": chosen}
        target = projectenv.contained_path(chosen, chosen)
    if target is None or not os.path.isfile(target):
        raise RemapError(
            ERROR_NOT_FOUND_IN_DIR, "推出的规则落不到这个文件上", name=body[-1], path=chosen
        )
    return rule


def _has_glob(text: str) -> bool:
    return any(ch in text for ch in "*?[")


def _looks_like_file(last: str) -> bool:
    return "." in last.strip(".")


def glob_has_match(pattern: str, *, root: str | None = None) -> bool:
    """模式至少匹配一个（`**` 才递归）；改指后的模式只在规则目标内走，进目录前挡软链接逃逸。"""
    try:
        if root is not None:
            base = Path(root).resolve()
            relative = Path(pattern).relative_to(root).as_posix()
            if pattern.endswith(("/", os.sep)):
                relative += "/"
            return (
                databinding._glob_hit(
                    base,
                    relative,
                    base,
                    recursive="**" in pattern,
                    hidden=False,
                    budget=[databinding.MAX_GLOB_SCAN],
                )
                == "found"
            )
        return next(glob.iglob(pattern, recursive="**" in pattern), None) is not None
    except (OSError, ValueError):
        return False


def location_exists(path: str, *, root: str | None = None) -> bool:
    """改写后的目标在不在：glob 至少匹配一个，其余 `os.path.exists`（文件或文件夹都算）。"""
    if not path:
        return False
    if _has_glob(path):
        return glob_has_match(path, root=root)
    try:
        if root is not None:
            path = projectenv.contained_path(root, path)
            if path is None:
                return False
        return os.path.exists(path)
    except (OSError, ValueError):
        return False


def _derive_dir(requested: str, chosen: str) -> dict:
    """条目是个**文件夹**（`listdir("data")` / `exists("data/runs")`），用户指认了文件夹 `chosen`。

    从最长的后缀试起：`chosen/<后缀>` 是文件夹就定规则；都不是 → `chosen` 本身就是那个文件夹。
    带点的名字（`runs.v1`）照样是文件夹：磁盘上**真有**同名文件夹（某个后缀、或 `chosen` 自己就叫这个
    名字）就不看扩展名（Codex 评 #730 P2）。只有要拿一个**改了名**的文件夹去顶替一个像文件名的条目时
    才拒——那多半是文件，文件夹顶替不了文件。
    """
    parsed = figcapture.remap_parts(requested)
    if parsed is None or not parsed[1] or parsed[1][-1] == "..":
        raise RemapError(ERROR_REQUESTED_INVALID, f"说不清脚本要的路径: {requested!r}")
    absolute, rparts = parsed
    body = rparts[1:] if absolute else rparts
    if not body:
        raise RemapError(
            ERROR_NOT_FOUND_IN_DIR, f"这个文件夹里没有 {requested}", name=requested, path=chosen
        )
    for k in range(len(body), 0, -1):
        # 脚本原串里的 `..` 是匹配前缀的一部分，不是准许越过用户选的文件夹；软链接同样按实体判。
        # 只探测 contained_path 回的那个路径，逃出去的后缀继续试更短的一段。
        candidate = projectenv.contained_path(chosen, os.path.join(chosen, *body[-k:]))
        if candidate is not None and os.path.isdir(candidate):
            return {
                "kind": figcapture.REMAP_PREFIX,
                "from": _join(rparts[: len(rparts) - k]),
                "to": chosen,
            }
    same_name = os.path.normcase(os.path.basename(chosen)) == os.path.normcase(body[-1])
    if _looks_like_file(body[-1]) and not same_name:
        raise RemapError(
            ERROR_NOT_FOUND_IN_DIR, f"这个文件夹里没有 {body[-1]}", name=body[-1], path=chosen
        )
    return {"kind": figcapture.REMAP_PREFIX, "from": _join(rparts), "to": chosen}


def derive_location(requested: str, chosen: str, *, chosen_is_dir: bool) -> dict:
    """`derive` 的扩展（ADR 0110 §三）：条目还可能是文件夹或 glob 模式——改写脚本要用。

    * glob（`data/*.csv`）：取不含通配符的目录前缀当「文件夹条目」推（指认的是其中一个文件时取
      它所在的文件夹），推完要求新模式**至少匹配一个**；
    * 文件夹：先按文件推（文件夹里找得到那个文件就是它），找不到再按文件夹推（`_derive_dir`）；
    * 其余就是 `derive`。
    推出的规则一律自检：`remap_target(..., whole=True)` 落到一个存在的位置上。
    """
    chosen = chosen_path(chosen)
    if _has_glob(requested):
        parsed = figcapture.remap_parts(requested)
        if parsed is None or not parsed[1]:
            raise RemapError(ERROR_REQUESTED_INVALID, f"说不清脚本要的路径: {requested!r}")
        absolute, parts = parsed
        i = next(n for n, p in enumerate(parts) if _has_glob(p))
        head = parts[:i]
        if absolute and len(head) < 2:
            raise RemapError(ERROR_REQUESTED_INVALID, f"说不清脚本要的路径: {requested!r}")
        folder = chosen if chosen_is_dir else os.path.dirname(chosen)
        if not os.path.isdir(folder):
            raise RemapError(ERROR_CHOSEN_INVALID, f"不是文件夹: {folder}", path=folder)
        if head:
            rule = _derive_dir(_join(head), folder)
        else:  # `glob("*.csv")`：相对 cwd 的模式——「相对路径都到这里找」
            rule = {"kind": figcapture.REMAP_PREFIX, "from": "", "to": folder}
        target = figcapture.remap_target([rule], requested)
        if target is None or not glob_has_match(target, root=rule["to"]):
            name = "/".join(parts[i:])
            raise RemapError(
                ERROR_NOT_FOUND_IN_DIR, f"这个文件夹里没有 {name}", name=name, path=folder
            )
        return rule
    if not chosen_is_dir:
        return derive(requested, chosen, chosen_is_dir=False)
    if not os.path.isdir(chosen):
        raise RemapError(ERROR_CHOSEN_INVALID, f"不是文件夹: {chosen}", path=chosen)
    try:
        return derive(requested, chosen, chosen_is_dir=True)
    except RemapError as exc:
        if exc.code != ERROR_NOT_FOUND_IN_DIR:
            raise
        rule = _derive_dir(requested, chosen)
    target = figcapture.remap_target([rule], requested, whole=True)
    target = projectenv.contained_path(rule["to"], target) if target is not None else None
    if target is None or not os.path.isdir(target):
        raise RemapError(ERROR_NOT_FOUND_IN_DIR, "推出的规则落不到这个文件夹上", path=chosen)
    return rule


def _join(parts: tuple[str, ...]) -> str:
    return figcapture._join_parts(tuple(parts))


def _ancestor(path: str, levels: int, *, dirname=os.path.dirname) -> str:
    """`path`（用户指认的那个绝对路径，原生分隔符 / 原样大小写）上溯 `levels` 层。

    `to` 是本机真实存在的目录，必须原样保留用户指认那一刻的分隔符与大小写——不能像 `from`
    （`_join`）那样经 `remap_parts` 按路径段重拼：那条路径只用于内部匹配，天生就该规范成正斜杠、
    盘符小写；`to` 用于 `os.path.isfile` / 展示给用户，重拼会把 Windows 路径 `C:\\Users\\…\\moved`
    变成 `c:/Users/…/moved`，与用户真正选中的路径对不上（Codex 评 #716 Windows full-ci 红）。
    `dirname` 只在测试里替换成别的实现（如 `ntpath.dirname`），验证在别的 OS 分隔符下同样成立。
    """
    for _ in range(levels):
        path = dirname(path)
    return path


# ---------------------------------------------------------------- 弹窗载荷


def _probe_via_of_constants(tree: ast.AST, kinds: dict[int, set] | None = None) -> dict[int, str]:
    """`id(Constant)` → `probe` / `glob`：这个常量是探路调用问的那条路径（绝对的也算）。

    与 `databinding.probe_literals` 同一张表（`PATH_PROBE_FUNCS` / `DIR_PROBE_FUNCS` /
    `GLOB_FUNCS` / `Path(<常量>)` 上的 `PATH_METHOD_PROBES` / `PATH_METHOD_GLOBS`）。那边只收相对
    目标；这里要的是**反过来**的事实——一个绝对常量若是被 `exists()` 问的，改指表救不回它，
    对话框不能给它选择器（否则指认 → 重跑 → `exists()` 照样 False → 同一个框再弹）。

    给了 `kinds` 就顺带记下每个常量被问成什么（`PROBE_KIND_OF`；glob 的目标与 `root_dir` 是文件夹）。
    """
    kinds = kinds if kinds is not None else {}

    def _kind(node_id: int, kind: str) -> None:
        kinds.setdefault(node_id, set()).add(kind)

    # 只赋值过一次的名字 → 它的常量（`DATA = "/abs/x.csv"` 再 `exists(DATA)` 是最常见的写法）。
    # 赋值不止一次的不跟：说不清探的是哪一个值
    stores: dict[str, int] = {}
    consts: dict[str, ast.Constant] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores[node.id] = stores.get(node.id, 0) + 1
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            consts[node.targets[0].id] = node.value

    def _const_of(expr: ast.expr | None) -> ast.Constant | None:
        if isinstance(expr, ast.Constant):
            return expr
        if isinstance(expr, ast.Name) and stores.get(expr.id) == 1:
            return consts.get(expr.id)
        return None

    # 别名与 `probe_literals` 同一份（`import glob as g` / `from pathlib import Path as P`）
    aliases = databinding._Aliases(tree)
    out: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = databinding._func_name(node.func)
        first = node.args[0] if node.args else None
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
            ctor = node.func.value
            inner = _const_of(ctor.args[0]) if len(ctor.args) == 1 else None
            if databinding._func_name(ctor.func) in aliases.path_ctors and inner is not None:
                if name in databinding.PATH_METHOD_PROBES:
                    out[id(inner)] = VIA_PROBE
                    _kind(id(inner), PROBE_KIND_OF.get(name, PROBE_ANY))
                elif name in databinding.PATH_METHOD_GLOBS:
                    out[id(inner)] = VIA_GLOB
                    _kind(id(inner), PROBE_DIR)
        if aliases.glob_call(node.func) is not None:
            # `glob("*.csv", root_dir="/moved/data")`：起算目录同样是 glob 在问，改指表救不回它
            root_dir = _const_of(
                next((k.value for k in node.keywords if k.arg == "root_dir"), None)
            )
            if root_dir is not None:
                out[id(root_dir)] = VIA_GLOB
                _kind(id(root_dir), PROBE_DIR)
        target = first
        if target is None:
            target = next(
                (k.value for k in node.keywords if k.arg in ("path", "top", "pathname")), None
            )
        target = _const_of(target)
        if target is None:
            continue
        if name in databinding.PATH_PROBE_FUNCS or name in databinding.DIR_PROBE_FUNCS:
            out[id(target)] = VIA_PROBE
            _kind(id(target), PROBE_KIND_OF.get(name, PROBE_ANY))
        elif aliases.glob_call(node.func) is not None:
            out[id(target)] = VIA_GLOB
    return out


def _probe_kinds_by_text(source: str) -> dict[str, str]:
    """脚本里被探路调用问到的字符串常量 → 它要的是什么（`PROBE_KIND_OF`；同一串几处合并）。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    kinds: dict[int, set] = {}
    _probe_via_of_constants(tree, kinds)
    by_text: dict[str, set] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) in kinds:
            by_text.setdefault(node.value, set()).update(kinds[id(node)])
    return {text: _merged_kind(k) for text, k in by_text.items()}


#: 读数据的调用（取末段名）：它们实参里的字符串常量才算「脚本要读的数据」（`static_missing` 的来源）。
#: 坐标轴标签、存图 / 写出的目标、`.py`、当输出目录用的路径都不在任何读取调用里——不靠事后按字符串长相猜
#: （用户 09-29 截图：`Distance $z$ ($\mu$m)`、`绘图缓存` 目录、`color.txt` 写出目标都进了「一并修好」）。
#: `read_*`（pandas / geopandas / ase / mdtraj……）按前缀认。
READ_FUNCS = frozenset(
    {
        "open",  # 内建 / io / codecs / gzip / bz2 / lzma；Path(...).open 另判（接收者是路径）
        "load",  # np.load / torch.load / joblib.load（json / pickle 的实参是文件对象，没有常量）
        "loadtxt",
        "genfromtxt",
        "fromfile",
        "loadmat",
        "imread",
        "File",  # h5py.File
        "Dataset",  # netCDF4.Dataset
        "open_dataset",
        "open_mfdataset",
        "open_dataarray",
        "load_workbook",
        "import_file",  # ovito
    }
)
#: `open` / `File` / `Dataset` 这几个带「模式」：写 / 追加 / 新建 / 读写的打开不是输入
_MODAL_READS = {"open": 1, "File": 1, "Dataset": 1}
_WRITE_MODE_CHARS = "wax+"


#: 写出 / 建输出目录的调用（取末段名）：它们实参里的路径是**输出**，不是要找的数据，也不许被改写。
#: 存图调用的唯一出处是 `databinding.SAVE_FUNCS`；这里补上写数据文件与建目录的，外加写模式的 `open` 类。
WRITE_FUNCS = frozenset(
    {
        "savetxt",
        "savez",
        "savez_compressed",
        "tofile",
        "to_csv",
        "to_excel",
        "to_parquet",
        "to_json",
        "to_pickle",
        "to_hdf",
        "to_netcdf",
        "to_feather",
        "write_text",
        "write_bytes",
        "makedirs",
        "mkdir",
    }
)


def is_write_call(node: ast.Call) -> bool:
    """存图 / 写出 / 建输出目录，或写 / 追加 / 读写模式的打开——与 `_is_read_call` 同一套模式判据。"""
    name = databinding._func_name(node.func)
    if name in databinding.SAVE_FUNCS or name in WRITE_FUNCS:
        return True
    pos = _MODAL_READS.get(name)
    if pos is None:
        return False
    mode = node.args[pos] if len(node.args) > pos else None
    if mode is None:
        mode = next((k.value for k in node.keywords if k.arg == "mode"), None)
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and any(c in mode.value for c in _WRITE_MODE_CHARS)
    )


def is_read_call(node: ast.Call) -> bool:
    """读取调用（`READ_FUNCS` / `read_*`，带模式的只认读模式）——脚本改写追值的去向时，值进了读取调用就成了数据、
    不再是路径（`scriptedit._Flow`）。与 `static_missing` 同一个判据。"""
    return _is_read_call(node)


def _is_read_call(node: ast.Call) -> bool:
    name = databinding._func_name(node.func)
    if name.startswith("read_"):
        return True
    if name not in READ_FUNCS:
        return False
    pos = _MODAL_READS.get(name)
    if pos is None:
        return True
    mode = node.args[pos] if len(node.args) > pos else None
    if mode is None:
        mode = next((k.value for k in node.keywords if k.arg == "mode"), None)
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return not any(c in mode.value for c in _WRITE_MODE_CHARS)
    return mode is None  # 没写模式 = 读；模式是变量：说不清，不算


def _input_constant_ids(tree: ast.AST) -> set[int]:
    """`id(Constant)`：读数据调用的**路径实参**里，代表那条路径（或它打头的目录）的字符串常量——整条就是
    常量、只赋值过一次的名字（`DATA = "..."` 再 `read_csv(DATA)`）、`Path(<常量>)`、拼路径的打头一段。
    `Path(<常量>).read_text()` / `.read_bytes()` / 读模式的 `.open()` 也算。`.py` 由调用方另外排除。"""
    stores: dict[str, int] = {}
    consts: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stores[node.id] = stores.get(node.id, 0) + 1
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            consts[node.targets[0].id] = node.value

    out: set[int] = set()

    def _take(expr: ast.AST | None, depth: int = 0) -> None:
        # 整条路径是一个常量（常量本身、只赋值一次的名字、`Path(<那样的东西>)`）就是它；拼出来的只认
        # **打头的那一段**（`os.path.join(DATA, "y.h5")` / `Path(DATA) / "y.h5"` / `DATA + "/y.h5"` /
        # `f"{DATA}/y.h5"` 的 DATA）——它是那条路径的前缀（目录常量，改写 / 归因要它）；后面的片段
        # 本身不是一条路径（`"y.h5"` 单拿出来会被当成缺了一个相对文件），不认
        if expr is None or depth > 4:
            return
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            out.add(id(expr))
        elif isinstance(expr, ast.Name) and stores.get(expr.id) == 1 and expr.id in consts:
            _take(consts[expr.id], depth + 1)
        elif (
            isinstance(expr, ast.Call)
            and expr.args
            and (
                databinding._func_name(expr.func) in databinding._PATH_CTORS
                or databinding._func_name(expr.func) == "join"
            )
        ):
            _take(expr.args[0], depth + 1)
        elif isinstance(expr, ast.BinOp) and isinstance(expr.op, (ast.Div, ast.Add)):
            _take(expr.left, depth + 1)
        elif isinstance(expr, ast.JoinedStr) and expr.values:
            head = expr.values[0]
            _take(head.value if isinstance(head, ast.FormattedValue) else head, depth + 1)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Path(<路径>).read_text() / .read_bytes() / .open("r")：路径是接收者
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            ctor = databinding._func_name(func.value.func)
            if ctor in databinding._PATH_CTORS and func.attr in ("read_text", "read_bytes", "open"):
                mode = (
                    node.args[0]
                    if node.args
                    else next((k.value for k in node.keywords if k.arg == "mode"), None)
                )
                writes = (
                    isinstance(mode, ast.Constant)
                    and isinstance(mode.value, str)
                    and any(c in mode.value for c in _WRITE_MODE_CHARS)
                )
                if func.attr != "open" or not writes:
                    for arg in func.value.args:
                        _take(arg)
                continue
        if not _is_read_call(node):
            continue
        # 路径是第一个位置实参（或 file / fname / filename / path 这几个关键字）；模式等其余实参不算
        first = (
            node.args[0]
            if node.args
            else next(
                (
                    k.value
                    for k in node.keywords
                    if k.arg in ("file", "fname", "filename", "filepath_or_buffer", "path", "io")
                ),
                None,
            )
        )
        _take(first)
    return out


def _absolute_literals(source: str, *, reads_only: bool = True) -> list[tuple[str, str]]:
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
    inputs = _input_constant_ids(tree)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        text = node.value
        if text in outputs:
            continue
        # 只有进了读取调用（或被探路调用问）的常量才是「脚本要读的数据」；代码文件不是
        if (reads_only and id(node) not in inputs and id(node) not in probed) or _is_code(text):
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


def _input_texts(source: str) -> set[str]:
    """进了读取调用的字符串常量的值（`_input_constant_ids`）。解析不了回空集。"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    ids = _input_constant_ids(tree)
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) in ids
        and not _is_code(n.value)
    }


def _is_code(text: str) -> bool:
    """`.py` / `.pyc`：`exec(open("helpers/util.py").read())` 读的是代码，不是要找的数据。"""
    return text.lower().endswith((".py", ".pyc", ".pyw"))


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
    script: str,
    root: str | os.PathLike,
    rules: list[dict] | None = None,
    *,
    reads_only: bool = True,
) -> list[dict]:
    """脚本里写着、此刻哪里都找不到的路径：`[{path, absolute, via, probe_kind}]`（按出现顺序，最多
    `MAX_OTHERS`）。`reads_only=False` 只给 C++ 读取器的归因（`native_miss`）用：候选不要求进了已知的读取调用。

    相对的看 `databinding.evidence`：脚本目录与项目根**两处都** `missing`（项目外的 `outside` 不算，
    那里不看）；探路目标同理，`via` 标成 `probe` / `glob`。绝对的只 `os.path.exists`（不读、不列目录）。
    已经被改指表救回来的读取（`via=open`）不列；探路（`probe` / `glob`）的不查改指表，规则解析得了也照列。
    """
    root_path = Path(root)
    script_path = root_path / figcapture.normalize_relative_script(script)
    rules = rules if rules is not None else rules_for(root)
    ev = databinding.evidence(script_path, root_path)
    cands = ev.get("candidates") or {}
    parent = cands.get(databinding.CANDIDATE_SCRIPT_PARENT) or {}
    top = cands.get(databinding.CANDIDATE_PROJECT_ROOT) or {}
    out: list[dict] = []

    source = _script_source(root_path, script_path)
    kinds = _probe_kinds_by_text(source)

    def _add(path: str, absolute: bool, via: str) -> None:
        if len(out) >= MAX_OTHERS or any(o["path"] == path for o in out):
            return
        # 只有真走改指表的读取（`open`）才会被规则救回来；exists / glob / listdir 不查表——规则碰巧能解析
        # 那一串，脚本照样走「不存在」那一支，这一条要留着按「改指救不回」说（Codex 评 #716 P2）
        if via == VIA_OPEN and _resolved(rules, path):
            return
        if via == VIA_GLOB:
            probe_kind = PROBE_DIR  # 指认的是模式所在的文件夹
        elif via == VIA_PROBE:
            # `listdir()` 不带实参列的是 cwd（目标记成 `.`）
            probe_kind = PROBE_DIR if path == "." else kinds.get(path, PROBE_ANY)
        else:
            probe_kind = PROBE_ANY  # open：文件，或它所在的文件夹（derive 在里面找）
        out.append({"path": path, "absolute": absolute, "via": via, "probe_kind": probe_kind})

    probe_targets = set(ev.get("probes") or [])
    read_texts = _input_texts(source)
    for lit in ev.get("reads") or []:
        # `evidence` 的 reads 是「像数据路径的常量」（首开判运行目录用，宁多勿漏）；这里要的是**真进了
        # 读取调用**的那些——标签、写出目标、输出目录、`.py` 都不是缺的数据
        if reads_only and lit not in read_texts and lit not in probe_targets:
            continue
        if lit in (parent.get("missing") or []) and lit in (top.get("missing") or []):
            # 同一串也被 exists / listdir 问过：脚本多半先判再读，改指救不回那一问——按探路算
            _add(lit, False, VIA_PROBE if lit in probe_targets else VIA_OPEN)
    p_probes = (parent.get("probes") or {}).get("missing") or []
    t_probes = (top.get("probes") or {}).get("missing") or []
    for target in ev.get("probes") or []:
        if target in p_probes and target in t_probes:
            via = VIA_GLOB if any(c in target for c in "*?[") else VIA_PROBE
            _add(target, False, via)
    for text, via in _absolute_literals(source, reads_only=reads_only):
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
    script: str,
    root: str | os.PathLike,
    missing: dict | None,
    *,
    rules: list[dict] | None = None,
    via: str = VIA_OPEN,
) -> dict | None:
    """弹窗载荷。`missing` 是 worker 报的 `missing_input` 事实（没有就是「没出图」路径，只剩静态的）。

    `{script, requested, absolute, via, probe_kind, others: [{path, absolute, via, probe_kind}]}`——
    `probe_kind` 是那条路径要的是什么（`dir` 只能指认文件夹）；`requested` 为 None 时
    界面以 `others` 的第一条为主。两样都没有回 None（没什么可问的）。`via` 是主条目的：worker 报的
    `missing_input` 是 `open`；C++ 读取器对上的（`native_miss`）是 `native`。
    """
    rules = rules if rules is not None else rules_for(root)
    others = static_missing(script, root, rules)
    requested = None
    absolute = False
    probe_kind = PROBE_ANY
    if isinstance(missing, dict) and isinstance(missing.get("requested"), str):
        requested = missing["requested"]
        absolute = bool(missing.get("absolute"))
        same = [o for o in others if o["path"] == requested]
        others = [o for o in others if o["path"] != requested]
        probe_kind = _merged_kind(o.get("probe_kind") for o in same if o["via"] != VIA_OPEN)
        # 同一串在脚本里也被 exists / glob 问过：分类跟着静态那一条走（口径同 `static_missing`）——
        # 改指只救得回 open，探路照样落空，给选择器就是「指认 → 重跑 → 照样退出」（Codex 评 #716 P2）
        # （调用方给的 `native` 等主条目分类不被 open 覆盖：只在静态那一条是探路时才换）
        via = next((o["via"] for o in same if o["via"] != VIA_OPEN), via)
    if requested is None and not others:
        return None
    return {
        "script": script,
        "requested": requested,
        "absolute": absolute,
        "via": via,
        "probe_kind": probe_kind,
        "others": others,
    }


def _as_written(filename: str, cwd: str) -> str:
    """异常里的路径 → 脚本写法那一侧：cwd 之内的绝对路径换回相对那段（xarray 先 abspath 再报）。"""
    if cwd and os.path.isabs(filename):
        try:
            rel = os.path.relpath(filename, cwd)
        except ValueError:  # Windows 跨盘
            return filename
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    return filename


def native_miss(
    script: str, root: str | os.PathLike, enoent: dict | None, *, rules: list[dict] | None = None
) -> dict | None:
    """`script_error` 里的 ENOENT 事实（`figcapture.enoent_fact`）→ `missing_input` 事实，对不上回 None。

    ADR 0110 §一：C++ 读取器不经四个打开入口，worker 说不出是哪串常量；这里拿脚本的静态证据对。
    候选只来自 `static_missing`（脚本里以常量出现、此刻哪儿都找不到的）。异常里的路径（`filename`，
    没有就是消息里引号括着的 `named`）与候选按路径段比：整串相等优先，其次候选是它的前缀（目录常量
    拼出来的）、路径段最长的那条。cwd 之内的绝对路径按脚本写法那一侧（换回相对）**与**原样的绝对
    写法两种都试——脚本写的到底是绝对还是 cwd 相对，异常本身说不清（xarray 先 abspath 再报），
    `native_miss` 要求候选与报告的路径同为绝对或同为相对，选错一种就永远对不上脚本自己写的绝对路径
    （与 #716「绝对路径保留绝对身份」同一个口径，Codex 评 #730 P2）；两种写法都对不上任何一条候选——
    不判，调用方照旧是 `script_error`（判不出就别判）。
    """
    if not isinstance(enoent, dict):
        return None
    raw = enoent.get("filename") or enoent.get("named")
    if not isinstance(raw, str) or not raw:
        return None
    cwd = enoent.get("cwd") if isinstance(enoent.get("cwd"), str) else ""
    written = _as_written(raw, cwd)
    forms = [written] if written == raw else [written, raw]
    parsed_forms = [(w, figcapture.remap_parts(w)) for w in forms]
    parsed_forms = [(w, p) for w, p in parsed_forms if p is not None and p[1]]
    if not parsed_forms:
        return None
    rules = rules if rules is not None else rules_for(root)
    best: tuple[int, str, str, bool] | None = None  # (段数, 候选原文, 匹配用的写法, 是否绝对)
    # 归因的候选放宽到「像数据路径的常量」（不要求进了已知的读取调用）：C++ 读取器常包在用户自己的函数里，
    # 而 ENOENT 与常量逐段对上本身就是强证据；弹窗的「一并修好」仍只列读取调用的实参
    for cand in static_missing(script, root, rules, reads_only=False):
        cparsed = figcapture.remap_parts(cand["path"])
        if cparsed is None or not cparsed[1]:
            continue
        for w, parsed in parsed_forms:
            if cparsed[0] != parsed[0]:
                continue  # 一份是绝对一份是相对，说的不是同一种写法，不比
            n = len(cparsed[1])
            if parsed[1][:n] != cparsed[1]:
                continue
            if best is None or n > best[0]:
                best = (n, cand["path"], w, parsed[0])
    if best is None:
        return None
    _, literal, matched_written, absolute = best
    return {
        "requested": matched_written,
        "absolute": absolute,
        "cwd": cwd,
        "literal": literal,
    }
