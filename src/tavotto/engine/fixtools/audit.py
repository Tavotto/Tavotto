"""本地审计：每次工具调用一行 JSON，写进 `data_dir()/ai/fix_audit.jsonl`（ADR 0149 §五）。

只记「谁、哪个工具、什么档、参数摘要、谁批准的、结果码」。**内容不落盘**：路径类参数保留（排障要知道读了哪），
其余字符串参数只记长度；工具返回的文本一个字也不进来。文件超过上限就滚成 `.1`（只留一代）。
审计失败绝不影响工具调用。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MAX_BYTES = 1024 * 1024
#: 值本身可以进审计的参数名：只有**路径**与**闭集枚举**。`glob` / `module` / `name` / `pattern` 之类模型可填任意文字的字段
#: 可能夹带从上一个工具结果里看到的凭据或文件内容，一律只记长度（Codex #1353）
PATH_KEYS = frozenset({"path", "root"})
ENUM_KEYS = {"mode": frozenset({"script", "native"}), "source": frozenset({"app", "worker"})}
KEEP_KEYS = PATH_KEYS | frozenset(ENUM_KEYS)
_MAX_STR = 300
_lock = threading.Lock()


def default_path() -> Path:
    from .. import config

    return config.data_dir() / "ai" / "fix_audit.jsonl"


def summarize_args(args: object) -> dict:
    if not isinstance(args, dict):
        return {"_": "not_an_object"}
    out: dict = {}
    for k, v in args.items():
        key = str(k)[:40]
        if isinstance(v, bool | int | float) or v is None:
            out[key] = v
        elif isinstance(v, str):
            if key in PATH_KEYS:
                out[key] = v[:_MAX_STR]
            elif key in ENUM_KEYS and v in ENUM_KEYS[key]:
                out[key] = v
            else:
                out[key] = f"<str len={len(v)}>"
        else:
            out[key] = f"<{type(v).__name__}>"
    return out


class AuditLog:
    def __init__(self, path: str | os.PathLike | None = None, *, max_bytes: int = MAX_BYTES):
        self._path = Path(path) if path else None
        self.max_bytes = max_bytes

    @property
    def path(self) -> Path:
        return self._path or default_path()

    def record(
        self,
        *,
        session: str,
        step: int,
        tool: str,
        tier: str,
        args: object,
        approved_by: str,
        result: dict,
    ) -> None:
        line = {
            "ts": round(time.time(), 3),
            "session": session,
            "step": step,
            "tool": tool,
            "tier": tier,
            "args": summarize_args(args),
            "approved_by": approved_by,  # policy | user | blocked
            "result": {
                "ok": bool(result.get("ok")),
                "code": str(result.get("code") or "")[:60],
                "summary": str(result.get("summary") or "")[:120],
            },
        }
        try:
            with _lock:
                p = self.path
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if p.stat().st_size >= self.max_bytes:
                        os.replace(p, p.with_name(p.name + ".1"))
                except FileNotFoundError:
                    pass
                with open(p, "a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 - 审计失败不拖垮工具调用
            return
