#!/usr/bin/env python3
"""用户的出图偏好：开工三问（画幅宽度 / 字体 / 图例加不加框）的答案落在这儿。

    python3 scripts/prefs.py --json                          # 读（永远成功）
    python3 scripts/prefs.py --set font="Times New Roman" --json
    python3 scripts/prefs.py --set width=double --set legend_frame=off --json
    python3 scripts/prefs.py --unset width --json            # 退回「下次再问」

SKILL.md 的约定：三个键里**记录过的就不再问**，没记录的用提问工具问用户；
用户在提问里选了「记住」才写进来。所以这个文件的语义是「用户点过头的默认值」，
不是「上一次碰巧选了什么」。

## 三条底线（与 update_check.py 同一套纪律）

1. **绝不写插件目录。** 那儿归 Codex 管、可能只读、升级时整个被换掉。
   落点是 Tavotto 的用户配置目录（`handoff.config_dir()`，同一份规则不抄第三遍），
   文件名 `codex-plugin-figure-prefs.json`。
2. **读永远成功。** 文件缺失、是垃圾、schema 对不上，一律当成「什么都没记」
   ——大不了重新问一遍，比崩掉强。
3. **键是闭集。** 只认 `width` / `font` / `legend_frame`，别的键当场拒绝：
   偏好文件不是杂物抽屉，写进来的每个键都得有 SKILL.md 里对应的问题。

纯标准库，Python 3.8+。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

SCHEMA = 1
PREFS_NAME = "codex-plugin-figure-prefs.json"

#: 键的闭集。值也各有约束——见 `_valid`。
#:   width        "single"（单栏 80mm）/ "double"（双栏 150mm）/ "ask"（每次都问）
#:   font         字体名（"Times New Roman" / "Arial" / 用户点名的其它字体）
#:   legend_frame "on"（图例加框）/ "off"（无框）
_ENUMS = {
    "width": {"single", "double", "ask"},
    "legend_frame": {"on", "off"},
}
KEYS = ("width", "font", "legend_frame")


def prefs_path(environ: dict | None = None) -> str:
    from handoff import config_dir  # 目录规则只有一份
    return os.path.join(config_dir(environ=environ), PREFS_NAME)


def read_prefs(path: str | None = None) -> dict:
    """读偏好。**任何失败都回空 dict**——重新问一遍即可，绝不为此报错。"""
    if path is None:
        try:
            path = prefs_path()
        except Exception:                            # config_dir 本身炸了也一样
            return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    prefs = data.get("prefs")
    if not isinstance(prefs, dict):
        return {}
    return {k: v for k, v in prefs.items()
            if k in KEYS and isinstance(v, str) and _valid(k, v)}


def _valid(key: str, value: str) -> bool:
    allowed = _ENUMS.get(key)
    if allowed is not None:
        return value in allowed
    return bool(value.strip())                       # font：非空字符串即可


def write_prefs(prefs: dict, path: str | None = None) -> bool:
    """写偏好（原子替换）。写不进去回 False——下次会重新问，不算灾难。"""
    if path is None:
        try:
            path = prefs_path()
        except Exception:
            return False
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema": SCHEMA, "prefs": prefs}, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError):
        return False


def _parse_set(raw: str) -> tuple[str, str]:
    key, sep, value = raw.partition("=")
    key = key.strip()
    if not sep or key not in KEYS:
        raise SystemExit(f"不认识的偏好键: {raw!r}（只认 {', '.join(KEYS)}）")
    value = value.strip()
    if not _valid(key, value):
        allowed = _ENUMS.get(key)
        hint = f"（可选值: {', '.join(sorted(allowed))}）" if allowed else "（不能为空）"
        raise SystemExit(f"偏好 {key} 的值不合法: {value!r}{hint}")
    return key, value


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="读写出图偏好（画幅宽度/字体/图例加框），落在 Tavotto 用户配置目录")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help=f"记录一条偏好（键: {', '.join(KEYS)}）")
    ap.add_argument("--unset", action="append", default=[], metavar="KEY",
                    help="删除一条偏好（下次重新问）")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args(argv)

    prefs = read_prefs()
    saved = True
    if args.set or args.unset:
        for raw in args.set:
            key, value = _parse_set(raw)
            prefs[key] = value
        for key in args.unset:
            if key not in KEYS:
                raise SystemExit(f"不认识的偏好键: {key!r}（只认 {', '.join(KEYS)}）")
            prefs.pop(key, None)
        saved = write_prefs(prefs)

    out = {"schema": SCHEMA, "prefs": prefs, "saved": saved}
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        if not prefs:
            print("还没有记录任何偏好（三个问题下次都会问）")
        for key in KEYS:
            if key in prefs:
                print(f"{key} = {prefs[key]}")
        if not saved:
            print("警告：偏好没保存成功（目录不可写？），下次会重新问", file=sys.stderr)
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())
