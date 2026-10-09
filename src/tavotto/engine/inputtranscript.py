"""执行的输入转录（T08，ADR 0099 §九）：一次真实执行里脚本实际问了什么、实际得到了什么——冷重放用它。

运行时才产生的答案不能在启动前假装已经冻结：worker 在 build 里把每一问按顺序记下来（`scriptinput.Channel.record`），
build **成功结束**后这份记录成为这一次执行的不可变转录，绑到 (项目, 脚本, 运行配置引用) 上——也就是这次执行
产出的那批图（runtime 素材的身份正是脚本 + 图名 + 运行配置，见 T03）。之后热会话没了、要从零再跑一遍脚本
重建这些图时（重开、导出、会话被回收），答案从**这份转录**来，而不是从项目答案文件（之后可能被同步 / 手改）。

* 转录**只在成功执行之后**写；失败的执行不改它（那些图仍是上一次成功执行的产物）。新的成功执行 = 新的转录，
  整条替换（这批图此刻代表的就是它）。答案管理里改 / 删答案是「明确要用新答案重算」：作废这个脚本的转录。
* 口令那一问只有 `secret: true`，没有值（worker 根本没交出来）：重放到它时重新问，没人能答就明确失败。
* 上下文摘要对不上（菜单换了序、数据变了、前一问答得不一样）：不按转录作答——有界面就重新问（旧答案只当建议），
  没有就明确失败。不保证任意脚本的确定性，只保证对不上时不静默套用。
* 有界：每个项目最多 `MAX_BINDINGS` 条，单份转录最多 `MAX_ENTRIES` 问 / `MAX_CHARS` 字符；超出的执行**不留转录**
  （冷重放回到项目答案文件 + 上下文匹配，同样不会静默套错）。

**读者校验（结构性不变量，Codex #816 r2/r3/r8/r9 同一类缺陷的收口）**：转录绑定时带一份「答案状态基线」`basis`
——两个作废令牌（整脚本级 / 该运行配置级），由答案管理的改 / 删（`forget`）换新。基线在 build **开始**时取
（`serving()` 进门冻结），绑定时带着它；`lookup()` 把转录的基线与**当前**令牌比：对不上 = 转录当它不存在
（回到项目答案 + 上下文匹配），绝不重放旧值。所以「作废 / 绑定 / 写盘」的先后、build 在飞时被改答案、
写盘失败，都不再是正确性前提——最坏只是转录没了。

存放：`<data_dir>/inputtranscripts/<项目摘要>.json`，Tavotto 自己的数据目录——不写用户项目、不进项目包、
不进诊断与遥测。答案可能含路径：只在这台机器上。纯标准库；Flask 父进程侧。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

from . import atomicio, config

ID_PREFIX = "it_"
FORMAT_VERSION = 1
MAX_BINDINGS = 400
MAX_ENTRIES = 256
MAX_CHARS = 256 * 1024
_SEP = "\x00"

_LOCK = threading.Lock()


@dataclasses.dataclass(frozen=True)
class Transcript:
    """一次成功执行的输入转录（不可变）。`entries` 每条：index / kind / prompt / context / answer / secret。"""

    id: str
    entries: tuple[dict, ...]

    def counts(self) -> dict:
        """公开事实：只有计数，没有提示、答案、上下文。"""
        return {
            "id": self.id,
            "count": len(self.entries),
            "secret": sum(1 for e in self.entries if e.get("secret")),
        }


def _norm_project(project_root: str | Path) -> str:
    """项目身份串：与 `app._project_id()` / `pool._norm_dir()` / `runconfig` 共用 `config.normalize_path_identity`
    （按卷判大小写；macOS 上 `os.path.normcase` 是 no-op，大小写别名会哈希到两份存储）。"""
    return config.normalize_path_identity(os.path.normpath(os.path.abspath(str(project_root))))


def store_path(project_root: str | Path) -> Path:
    digest = hashlib.sha256(_norm_project(project_root).encode("utf-8")).hexdigest()[:24]
    return config.data_path("inputtranscripts", f"{digest}.json")


def _key(script: str, run_config: str | None) -> str:
    return f"{script}{_SEP}{run_config or ''}"


class StoreUnreadable(OSError, ValueError):
    """存储读不出来 / 损坏 / 是更新版本写的。**不是「空」**：凡是据此做决定的读（比基线、决定绑不绑、
    lookup 是否成立）都必须把它当失败——不绑、lookup 当没有；写也不能在这种读上「读-改-写」（会抹掉令牌）。"""


def _load(project_root: str | Path) -> tuple[dict, dict]:
    """严格读：(绑定表, 作废令牌表)。**只有文件不存在才是初始状态**（空）；读失败 / 非 JSON / 形状不对 /
    更新版本写的一律抛 `StoreUnreadable`，绝不折成空值（Codex #816 r4224223190 / r4224295365）。"""
    try:
        text = store_path(project_root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, {}
    except OSError as exc:
        raise StoreUnreadable(f"转录存储读不出来: {exc!r}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise StoreUnreadable(f"转录存储损坏: {exc!r}") from exc
    if not isinstance(data, dict):
        raise StoreUnreadable("转录存储形状不对")
    version = data.get("version")
    if isinstance(version, int) and version > FORMAT_VERSION:
        raise StoreUnreadable("转录存储是更新版本写的")  # 读不懂：不套用，也不覆盖
    bindings = data.get("bindings", {})
    gens = data.get("generations", {})
    if not isinstance(bindings, dict) or not isinstance(gens, dict):
        raise StoreUnreadable("转录存储形状不对")
    return bindings, gens


def _write(project_root: str | Path, bindings: dict, gens: dict) -> None:
    """写必须带着**严格读到的** `gens`（读-改-写），没有「缺省去读」——读失败时调用方已经中止，绝不写 `{}`。"""
    path = store_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomicio.write_json(
        path, {"version": FORMAT_VERSION, "bindings": bindings, "generations": gens}
    )


def _basis_of(gens: dict, script: str, run_config: str | None) -> list:
    return [gens.get(script), gens.get(_key(script, run_config))]


def basis(project_root: str | Path, script: str, run_config: str | None) -> list:
    """此刻这份 (脚本, 运行配置) 的答案状态基线（build 开始时取，绑定时带上；读者拿它对当前令牌）。

    **严格读**：存储文件不存在 = 还没人改过（空令牌）；读不出来 / 损坏 = 抛错——不能把「读失败」当成
    「没人改过」，否则之后绑定会把基线误当成当前（Codex #816 r4224223190）。调用方拿不到基线就不绑转录。"""
    with _LOCK:
        _bindings, gens = _load(project_root)
        return _basis_of(gens, script, run_config)


class _CurrentBasis:
    """`bind` 的 basis 缺省哨兵：调用方明确不关心基线（直接绑定当前状态，测试 / 非 build 路径）。"""


CURRENT = _CurrentBasis()


class StaleTranscriptError(OSError):
    """新转录没落盘、旧绑定也没能作废：这次执行不能算「已绑定」（冷重放可能套上旧值）。"""


def _commit(project_root: str | Path, bindings: dict, gens: dict, key: str) -> None:
    """写回绑定表（带着严格读到的令牌）；写不下去（`os.replace` 被占、磁盘满）就**保证旧绑定不再可用**：先试
    去掉这一条重写；再不行，只有**没有任何令牌**时才删掉整份存储（删了不丢令牌）——有令牌就不能删（会让
    之后的基线回到「没人改过」），明确失败。作废成功 → 重抛原错；作废不了 → `StaleTranscriptError`。"""
    try:
        _write(project_root, bindings, gens)
        return
    except OSError as exc:
        original = exc
    try:
        _write(project_root, {k: v for k, v in bindings.items() if k != key}, gens)
    except OSError as inval:
        if gens:
            raise StaleTranscriptError(
                f"转录写入失败且旧绑定无法作废: {original!r}; {inval!r}"
            ) from original
        try:
            store_path(project_root).unlink(missing_ok=True)
        except OSError as inval2:
            raise StaleTranscriptError(
                f"转录写入失败且旧绑定无法作废: {original!r}; {inval2!r}"
            ) from original
    raise original


def _clean(records) -> list[dict] | None:
    """worker 的 `script_inputs` → 转录条目；口令那一问不带答案。超出上限回 None（不留转录）。"""
    out: list[dict] = []
    chars = 0
    for r in records or []:
        if not isinstance(r, dict) or not isinstance(r.get("index"), int):
            continue
        secret = bool(r.get("secret"))
        answer = None if secret else r.get("answer")
        entry = {
            "index": r["index"],
            "kind": str(r.get("kind") or "input"),
            "prompt": str(r.get("prompt") or ""),
            "context": r.get("context") if isinstance(r.get("context"), str) else None,
            "answer": answer if isinstance(answer, str) else None,
        }
        if isinstance(r.get("prompt_id"), str):
            entry["prompt_id"] = r["prompt_id"]
        if secret:
            entry["secret"] = True
        chars += len(entry["prompt"]) + len(entry["answer"] or "")
        out.append(entry)
    if len(out) > MAX_ENTRIES or chars > MAX_CHARS:
        return None
    return out


def bind(
    project_root: str | Path,
    script: str,
    run_config: str | None,
    records,
    basis: list | None | _CurrentBasis = CURRENT,
) -> Transcript | None:
    """一次**成功**执行结束：把它的输入记录绑成这批产物的转录。没有问过任何输入 → 清掉旧绑定（这批图此刻
    来自一次没有输入的执行），回 None；超出上限同样不留。"""
    entries = _clean(records)
    key = _key(script, run_config)
    with _LOCK:
        try:
            bindings, gens = _load(project_root)  # 读不出来 / 损坏 = 抛错：不绑，也不写
        except StoreUnreadable as exc:
            if basis is None:
                raise StaleTranscriptError(f"没有基线且存储读不出来: {exc!r}") from exc
            raise
        current = _basis_of(gens, script, run_config)
        if basis is None:
            # 防御层（正常不会发生：取不到基线 build 就失败了）：无法证明答案没被改过——不绑，而且**作废这份
            # (脚本, 配置) 现有的绑定**，否则冷重放会继续用旧值；作废不了就明确失败（Codex #816 r4224357981）
            if key in bindings:
                try:
                    _write(project_root, {k: v for k, v in bindings.items() if k != key}, gens)
                except OSError as exc:
                    raise StaleTranscriptError(f"没有基线且旧绑定无法作废: {exc!r}") from exc
            return None
        if isinstance(basis, _CurrentBasis):
            basis = current
        elif list(basis) != current:
            return None  # build 在飞时答案被改过：这份转录基于旧答案，不绑（读者也会拒收）
        if not entries:
            if bindings.pop(key, None) is not None:
                _commit(project_root, bindings, gens, key)
            return None
        prev = bindings.get(key)
        if (
            isinstance(prev, dict)
            and isinstance(prev.get("id"), str)
            and _clean(prev.get("entries")) == entries
            and prev.get("basis") == basis
        ):
            # 同一份问答（热会话上又一次 build 往返 / 重跑得到同样的转录）：不重写、不换 id
            return Transcript(prev["id"], tuple(entries))
        tid = ID_PREFIX + secrets.token_hex(8)
        bindings[key] = {
            "id": tid,
            "entries": entries,
            "created_at": time.time(),
            "basis": basis,
        }
        if len(bindings) > MAX_BINDINGS:
            oldest = sorted(bindings, key=lambda k: float(bindings[k].get("created_at") or 0))
            for k in oldest[: len(bindings) - MAX_BINDINGS]:
                bindings.pop(k, None)
        _commit(project_root, bindings, gens, key)
    return Transcript(tid, tuple(entries))


def lookup(project_root: str | Path, script: str, run_config: str | None) -> Transcript | None:
    """这批产物（脚本 + 运行配置）最近一次成功执行的转录；没有 = None。"""
    with _LOCK:
        try:
            bindings, gens = _load(project_root)
        except StoreUnreadable:
            return None  # 读不出来 / 损坏：当没有转录（回到项目答案 + 上下文匹配），绝不重放
        rec = bindings.get(_key(script, run_config))
        current_basis = _basis_of(gens, script, run_config)
    if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
        return None
    # 读者校验：转录的基线对不上当前答案状态 = 当它不存在（不重放旧值）
    if rec.get("basis", [None, None]) != current_basis:
        return None
    entries = _clean(rec.get("entries"))
    if not entries:
        return None
    return Transcript(rec["id"], tuple(entries))


def forget(
    project_root: str | Path,
    script: str,
    *,
    run_config: str | None = None,
    all_configs: bool = True,
) -> bool:
    """明确重算：默认作废整个脚本；单条答案管理只作废它那份配置（包括无参数）的转录。"""
    prefix = script + _SEP
    with _LOCK:
        bindings, gens = _load(
            project_root
        )  # 读不出来 = 抛错、不写：不在读失败的基础上抹掉别的令牌
        gens = dict(gens)
        # 先换令牌：之后任何基于旧答案的转录（含 build 在飞、稍后才绑定的）读者都不认
        gens[script if all_configs else _key(script, run_config)] = "g_" + secrets.token_hex(8)
        drop = [
            k
            for k in bindings
            if (k.startswith(prefix) if all_configs else k == _key(script, run_config))
        ]
        for k in drop:
            bindings.pop(k, None)
        _write(project_root, bindings, gens)
    return bool(drop)
