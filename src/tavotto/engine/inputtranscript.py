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
    return os.path.normcase(os.path.normpath(os.path.abspath(str(project_root))))


def store_path(project_root: str | Path) -> Path:
    digest = hashlib.sha256(_norm_project(project_root).encode("utf-8")).hexdigest()[:24]
    return config.data_path("inputtranscripts", f"{digest}.json")


def _key(script: str, run_config: str | None) -> str:
    return f"{script}{_SEP}{run_config or ''}"


def _read(project_root: str | Path) -> dict:
    try:
        data = json.loads(store_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    version = data.get("version")
    if isinstance(version, int) and version > FORMAT_VERSION:
        return {}  # 更新版本写的：读不懂就当没有（冷重放回到上下文匹配，不会静默套用）
    bindings = data.get("bindings")
    return bindings if isinstance(bindings, dict) else {}


def _write(project_root: str | Path, bindings: dict) -> None:
    path = store_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomicio.write_json(path, {"version": FORMAT_VERSION, "bindings": bindings})


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
    project_root: str | Path, script: str, run_config: str | None, records
) -> Transcript | None:
    """一次**成功**执行结束：把它的输入记录绑成这批产物的转录。没有问过任何输入 → 清掉旧绑定（这批图此刻
    来自一次没有输入的执行），回 None；超出上限同样不留。"""
    entries = _clean(records)
    key = _key(script, run_config)
    with _LOCK:
        if not entries:
            if not store_path(project_root).exists():
                return None
            bindings = _read(project_root)
            if bindings.pop(key, None) is not None:
                _write(project_root, bindings)
            return None
        bindings = _read(project_root)
        prev = bindings.get(key)
        if (
            isinstance(prev, dict)
            and isinstance(prev.get("id"), str)
            and _clean(prev.get("entries")) == entries
        ):
            # 同一份问答（热会话上又一次 build 往返 / 重跑得到同样的转录）：不重写、不换 id
            return Transcript(prev["id"], tuple(entries))
        tid = ID_PREFIX + secrets.token_hex(8)
        bindings[key] = {"id": tid, "entries": entries, "created_at": time.time()}
        if len(bindings) > MAX_BINDINGS:
            oldest = sorted(bindings, key=lambda k: float(bindings[k].get("created_at") or 0))
            for k in oldest[: len(bindings) - MAX_BINDINGS]:
                bindings.pop(k, None)
        _write(project_root, bindings)
    return Transcript(tid, tuple(entries))


def lookup(project_root: str | Path, script: str, run_config: str | None) -> Transcript | None:
    """这批产物（脚本 + 运行配置）最近一次成功执行的转录；没有 = None。"""
    with _LOCK:
        rec = _read(project_root).get(_key(script, run_config))
    if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
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
        if not store_path(project_root).exists():
            return False
        bindings = _read(project_root)
        drop = [
            k
            for k in bindings
            if (k.startswith(prefix) if all_configs else k == _key(script, run_config))
        ]
        if not drop:
            return False
        for k in drop:
            bindings.pop(k, None)
        _write(project_root, bindings)
    return True
