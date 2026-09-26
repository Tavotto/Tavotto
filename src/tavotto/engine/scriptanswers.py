"""脚本 `input()` 的答案按项目记住（ADR 0099 §四）。

键 = (脚本相对项目的 POSIX 路径, 本次运行里第 N 次读取, 提示原文)。提示文字变了就当成新问题；同一序号的
旧条目被新条目替换。**存放位置只由 `answers_path()` 决定**——用户若改选本机数据目录（ADR §四 B）或
「项目里但不进项目包」（C），只改这一个函数。

纯标准库，Flask 父进程侧。写入一律 `atomicio`，读用 `documents.loads_document` 的非有限数纪律；
读坏了当作没有答案（答案丢了只是再问一次，不值得让渲染失败）。答案可能含路径：不进遥测、不进
诊断包、不进 app.log。
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from . import atomicio, config, documents

#: 项目收纳目录里的文件名（登记在 `documents.RESERVED_DOCUMENT_FILENAMES`，不会被当成画布列出）。
FILENAME = documents.SCRIPT_INPUTS_FILENAME
FORMAT_VERSION = 1
#: 单条答案的长度上限（字符）。答案是人在对话框里敲的，超长多半是粘错了。
MAX_ANSWER_CHARS = 10_000

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


def _write(project_root: str | Path, scripts: dict[str, list[dict]]) -> None:
    path = answers_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": FORMAT_VERSION,
        "scripts": {k: v for k, v in sorted(scripts.items()) if v},
    }
    atomicio.write_json(path, payload, indent=2)


def load(project_root: str | Path) -> dict[str, list[dict]]:
    """整个项目记住的答案：`{脚本: [{index, prompt, answer, kind}, …]}`（按序号排）。"""
    with _LOCK:
        return _read(project_root)


def entries(project_root: str | Path, script: str) -> list[dict]:
    return load(project_root).get(script, [])


def lookup(project_root: str | Path, script: str, index: int, prompt: str) -> str | None:
    """(脚本, 第 N 问, 提示原文) 记住的答案；提示变了 / 没记过 → None。"""
    for e in entries(project_root, script):
        if e["index"] == index and e["prompt"] == prompt:
            return e["answer"]
    return None


def _check_answer(answer: str) -> str:
    if not isinstance(answer, str):
        raise ValueError("答案必须是字符串")
    if len(answer) > MAX_ANSWER_CHARS:
        raise ValueError(f"答案太长（上限 {MAX_ANSWER_CHARS} 个字符）")
    return answer


def remember(
    project_root: str | Path, script: str, index: int, prompt: str, answer: str, kind: str = "input"
) -> None:
    """记下一问的答案：同一序号的旧条目（不管提示是否相同）被替换。"""
    _check_answer(answer)
    with _LOCK:
        scripts = _read(project_root)
        kept = [e for e in scripts.get(script, []) if e["index"] != index]
        kept.append({"index": index, "prompt": prompt, "answer": answer, "kind": kind})
        scripts[script] = sorted(kept, key=lambda e: e["index"])
        _write(project_root, scripts)


def update(project_root: str | Path, script: str, index: int, answer: str) -> bool:
    """答案管理里改一条（提示不变）。没有这一条回 False。"""
    _check_answer(answer)
    with _LOCK:
        scripts = _read(project_root)
        found = False
        for e in scripts.get(script, []):
            if e["index"] == index:
                e["answer"] = answer
                found = True
        if found:
            _write(project_root, scripts)
        return found


def forget(project_root: str | Path, script: str, index: int | None = None) -> bool:
    """删掉一条（`index`）或这个脚本的全部答案。什么都没删回 False。"""
    with _LOCK:
        scripts = _read(project_root)
        before = scripts.get(script, [])
        after = [] if index is None else [e for e in before if e["index"] != index]
        if len(after) == len(before):
            return False
        scripts[script] = after
        _write(project_root, scripts)
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
