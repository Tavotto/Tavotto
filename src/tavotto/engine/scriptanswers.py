"""脚本 `input()` 的答案按项目记住（ADR 0099 §四）。

键 = (脚本相对项目的 POSIX 路径, 运行配置引用, 本次运行里第 N 次读取, 读取方式, 提示原文, 上下文摘要)
（ADR 0099 §九，T08）。只有全部对上才原样复用（`recall().answer`）；提示相同而上下文 / 运行配置不同、或是
T08 之前记下的没有上下文的旧条目，**最多是建议**（`recall().suggestion`），要重新问——菜单换了序、前一问答得
不一样时，旧的「2」指的已经不是同一项。同一 (序号, 运行配置) 的旧条目被新条目替换。
**存放位置只由 `answers_path()` 决定**——用户若改选本机数据目录（ADR §四 B）或「项目里但不进项目包」（C），
只改这一个函数。

**上下文摘要不进项目文件**（T12，ADR 0099 §十）：它是提示 + 上一问后的输出 + 前面回答的无盐 sha256，菜单类输出
熵低、照着脚本就能枚举，不能随项目分享出去。它记在 Tavotto 数据目录的本机侧表（`contexts_path()`），按
(脚本, 运行配置, 序号, 读取方式, 提示) 索引、并绑定它所属的那个答案；项目文件只留答案本身。换了机器 / 答案被别处
改过 → 本机没有对得上的上下文 → 只当建议重新问（安全方向）。项目文件里若带着 `context`（T08 开发版写过，从未
发布），读入时丢弃，下一次写就不再有它。

纯标准库，Flask 父进程侧。写入一律 `atomicio`，读用 `documents.loads_document` 的非有限数纪律；
读坏了当作没有答案（答案丢了只是再问一次，不值得让渲染失败）。答案可能含路径：不进遥测、不进
诊断包、不进 app.log。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from . import atomicio, config, documents

#: 项目收纳目录里的文件名（登记在 `documents.RESERVED_DOCUMENT_FILENAMES`，不会被当成画布列出）。
FILENAME = documents.SCRIPT_INPUTS_FILENAME
#: 2（T08）：条目多了可选的 `context`（上下文摘要）与 `run_config`（运行配置引用）。旧读者忽略它们；
#: 没有 `context` 的条目（版本 1 写的）只当建议。
FORMAT_VERSION = 2
#: 单条答案的长度上限（字符）。答案是人在对话框里敲的，超长多半是粘错了。
MAX_ANSWER_CHARS = 10_000
#: 本机上下文侧表的格式与每个项目的条目上限（超出按写入时间丢最旧的：丢了只是再问一次）。
CONTEXTS_FORMAT_VERSION = 1
MAX_CONTEXTS = 2000
_SEP = "\x00"

_LOCK = threading.Lock()


def answers_path(project_root: str | Path) -> Path:
    """答案文件在哪。**唯一出处**（ADR 0099 §四，当前是 A：项目的 `tavottofile/`）。"""
    return config.project_store_dir(project_root) / FILENAME


def _read(project_root: str | Path) -> dict[str, list[dict]]:
    path = answers_path(project_root)
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    try:
        data = documents.loads_document(raw)
    except ValueError:
        return {}
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for script, entries in scripts.items():
        if not isinstance(script, str) or not isinstance(entries, list):
            continue
        clean = [
            {
                "index": e["index"],
                "prompt": e["prompt"],
                "answer": e["answer"],
                "kind": e.get("kind") if isinstance(e.get("kind"), str) else "input",
                "run_config": e.get("run_config") if isinstance(e.get("run_config"), str) else None,
            }
            for e in entries
            if isinstance(e, dict)
            and isinstance(e.get("index"), int)
            and not isinstance(e.get("index"), bool)
            and isinstance(e.get("prompt"), str)
            and isinstance(e.get("answer"), str)
        ]
        if clean:
            out[script] = sorted(clean, key=lambda e: e["index"])
    return out


def _stored(entry: dict) -> dict:
    """落盘形状：没有运行配置的不写这个键（旧条目原样）。上下文摘要从不写进项目文件。"""
    out = {k: entry[k] for k in ("index", "prompt", "answer", "kind")}
    if entry.get("run_config"):
        out["run_config"] = entry["run_config"]
    return out


def _write(project_root: str | Path, scripts: dict[str, list[dict]]) -> None:
    path = answers_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": FORMAT_VERSION,
        "scripts": {k: [_stored(e) for e in v] for k, v in sorted(scripts.items()) if v},
    }
    atomicio.write_json(path, payload, indent=2)


# ---------------------------------------------------------------- 本机上下文侧表


def contexts_path(project_root: str | Path) -> Path:
    """上下文摘要的本机侧表：Tavotto 数据目录，不写用户项目、不进项目包。"""
    norm = config.normalize_path_identity(os.path.normpath(os.path.abspath(str(project_root))))
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]
    return config.data_path("scriptanswer-contexts", f"{digest}.json")


def _ctx_key(script: str, run_config: str | None, index: int, kind: str, prompt: str) -> str:
    return _SEP.join((script, run_config or "", str(index), kind, prompt))


def _answer_tag(answer: str) -> str:
    """侧表里的上下文属于哪一个答案：项目文件里的答案被别处换掉，上下文就不再对它作数。"""
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()[:32]


def _read_contexts(project_root: str | Path) -> dict[str, dict]:
    try:
        data = json.loads(contexts_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {
        k: v
        for k, v in items.items()
        if isinstance(v, dict)
        and isinstance(v.get("context"), str)
        and isinstance(v.get("answer"), str)
    }


def _write_contexts(project_root: str | Path, items: dict[str, dict]) -> None:
    if len(items) > MAX_CONTEXTS:
        keep = sorted(items, key=lambda k: float(items[k].get("t") or 0))[-MAX_CONTEXTS:]
        items = {k: items[k] for k in keep}
    path = contexts_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomicio.write_json(path, {"version": CONTEXTS_FORMAT_VERSION, "entries": items})


def _local_context(contexts: dict[str, dict], script: str, entry: dict) -> str | None:
    side = contexts.get(
        _ctx_key(script, entry["run_config"], entry["index"], entry["kind"], entry["prompt"])
    )
    if side is None or side["answer"] != _answer_tag(entry["answer"]):
        return None
    return side["context"]


def _public(entry: dict) -> dict:
    return _stored(entry)


def load(project_root: str | Path) -> dict[str, list[dict]]:
    """整个项目记住的答案：`{脚本: [{index, prompt, answer, kind}, …]}`（按序号排）。给界面的答案管理：
    只带运行配置的不透明引用以区分条目；上下文摘要与 argv 不出这个模块。"""
    with _LOCK:
        return {k: [_public(e) for e in v] for k, v in _read(project_root).items()}


def entries(project_root: str | Path, script: str) -> list[dict]:
    return load(project_root).get(script, [])


#: `recall()` 为什么只给建议（进界面事件的 `recheck`，闭集）。
RECHECK_CONTEXT = "context_changed"
RECHECK_CONFIG = "config_changed"
RECHECK_LEGACY = "legacy_answer"
RECHECKS = (RECHECK_CONTEXT, RECHECK_CONFIG, RECHECK_LEGACY)


@dataclasses.dataclass(frozen=True)
class Recall:
    """一问能不能用记住的答案：`answer` = 原样复用；否则 `suggestion` 只是给人看的旧答案（要重新问）。"""

    answer: str | None = None
    suggestion: str | None = None
    recheck: str | None = None


def recall(
    project_root: str | Path,
    script: str,
    index: int,
    prompt: str,
    *,
    kind: str = "input",
    context: str | None = None,
    run_config: str | None = None,
) -> Recall:
    """按 (运行配置, 第 N 问, 读取方式, 提示, 上下文) 找记住的答案（ADR 0099 §九；上下文只认本机侧表，§十）。

    全部对上 → 原样复用。提示与读取方式相同、而上下文 / 运行配置不同，或旧条目没有上下文 → 只给建议：
    「不确定就重新确认」。提示变了 → 新问题，连建议都不给（与 T08 之前同一条判据）。"""
    with _LOCK:
        scripts = _read(project_root)
        contexts = _read_contexts(project_root)
    same = [
        e
        for e in scripts.get(script, [])
        if e["index"] == index and e["prompt"] == prompt and e["kind"] == kind
    ]
    if not same:
        return Recall()
    mine = [e for e in same if e["run_config"] == run_config]
    for e in mine:
        if context and _local_context(contexts, script, e) == context:
            return Recall(answer=e["answer"])
    if mine:
        e = mine[0]
        local = _local_context(contexts, script, e)
        return Recall(
            suggestion=e["answer"], recheck=RECHECK_LEGACY if not local else RECHECK_CONTEXT
        )
    return Recall(suggestion=same[0]["answer"], recheck=RECHECK_CONFIG)


def _check_answer(answer: str) -> str:
    if not isinstance(answer, str):
        raise ValueError("答案必须是字符串")
    if len(answer) > MAX_ANSWER_CHARS:
        raise ValueError(f"答案太长（上限 {MAX_ANSWER_CHARS} 个字符）")
    return answer


def remember(
    project_root: str | Path,
    script: str,
    index: int,
    prompt: str,
    answer: str,
    kind: str = "input",
    *,
    context: str | None = None,
    run_config: str | None = None,
) -> None:
    """记下一问的答案：同一 (序号, 运行配置) 的旧条目（不管提示是否相同）被替换。"""
    _check_answer(answer)
    with _LOCK:
        scripts = _read(project_root)
        kept = [
            e
            for e in scripts.get(script, [])
            if not (e["index"] == index and e["run_config"] == run_config)
        ]
        kept.append(
            {
                "index": index,
                "prompt": prompt,
                "answer": answer,
                "kind": kind,
                "run_config": run_config,
            }
        )
        scripts[script] = sorted(kept, key=lambda e: (e["index"], e["run_config"] or ""))
        _write(project_root, scripts)
        contexts = _read_contexts(project_root)
        prefix = _SEP.join((script, run_config or "", str(index))) + _SEP
        contexts = {k: v for k, v in contexts.items() if not k.startswith(prefix)}
        if context:
            contexts[_ctx_key(script, run_config, index, kind, prompt)] = {
                "context": context,
                "answer": _answer_tag(answer),
                "t": time.time(),
            }
        _write_contexts(project_root, contexts)


def update(
    project_root: str | Path, script: str, index: int, answer: str, *, run_config: str | None = None
) -> bool:
    """答案管理里改一份配置的一条（提示不变）；缺省只改无参数配置。没有这一条回 False。"""
    _check_answer(answer)
    with _LOCK:
        scripts = _read(project_root)
        found = False
        contexts = _read_contexts(project_root)
        for e in scripts.get(script, []):
            if e["index"] == index and e["run_config"] == run_config:
                local = _local_context(contexts, script, e)
                e["answer"] = answer
                found = True
                if local:
                    key = _ctx_key(script, e["run_config"], index, e["kind"], e["prompt"])
                    contexts[key] = {**contexts[key], "answer": _answer_tag(answer)}
        if found:
            _write(project_root, scripts)
            _write_contexts(project_root, contexts)
        return found


def forget(
    project_root: str | Path,
    script: str,
    index: int | None = None,
    *,
    run_config: str | None = None,
) -> bool:
    """删一份配置的一条（缺省无参数）；仅省略 index 时删整个脚本。什么都没删回 False。"""
    if index is None and run_config is not None:
        raise ValueError("指定 run_config 时需要 index")
    with _LOCK:
        scripts = _read(project_root)
        before = scripts.get(script, [])
        after = (
            []
            if index is None
            else [e for e in before if not (e["index"] == index and e["run_config"] == run_config)]
        )
        if len(after) == len(before):
            return False
        scripts[script] = after
        _write(project_root, scripts)
        prefix = script + _SEP
        mine = (
            (lambda k: k.startswith(prefix))
            if index is None
            else (lambda k: k.startswith(_SEP.join((script, run_config or "", str(index))) + _SEP))
        )
        contexts = _read_contexts(project_root)
        _write_contexts(project_root, {k: v for k, v in contexts.items() if not mine(k)})
        return True


def digest(project_root: str | Path, script: str) -> str:
    """这个脚本记住的答案的摘要（数据绑定修订用，ADR 0099 §七）；没有答案回 ""。"""
    items = entries(project_root, script)
    if not items:
        return ""
    canon = json.dumps(
        [[e["index"], e["prompt"], e["answer"]] for e in items],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()
