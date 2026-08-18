#!/usr/bin/env python3
"""把刚画好的图交给 Magplot：登记 → （必要时）跑脚本 → 唤起界面。

    python3 scripts/handoff.py figures/fig_removal_rate.py
    python3 scripts/handoff.py figures/Fig1_removal_rate.pdf --run never

输出一行 JSON（技能读的就是它）：

    {"ok": true, "ran": true, "project": "...", "stem": "Fig1_removal_rate",
     "parameterizable": true, "launch": "desktop", "conflicts": [], "dynamic_names": []}

判据只有一个：**parameterizable 为 true 才算交接成功**。false 说明这张图在
Magplot 里双击进不去——多半是脚本没跟产物放在同一个目录，或产物名要到运行期
才知道（见 SKILL.md 的约定 1 与 3）。

退出码（**不可参数化也是非零**：图出来了但只是一张死图，那不是成功，
用 0 报出去等于把「要修」写在一行 JSON 里等人自己发现）：

    0  交接成功且可参数化      3  用户机器上没装 Magplot
    1  脚本运行失败            4  交接了，但这张图不可参数化
    2  路径不对 / magplot open 失败

真正干活的是 Magplot 自己的 `magplot open`（`src/magplot/engine/handoff.py`）：
路径解析、注册表合并、唤起桌面 App 还是浏览器，全部在那边裁决。**本脚本不做
第二套判断**，它只负责「要不要先把脚本跑一遍」和把结果整理成一行 JSON。

纯标准库，Python 3.8+。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

#: 与 Magplot 的静态扫描同源的产物后缀
OUT_EXTS = (".pdf", ".png", ".svg", ".jpg", ".jpeg", ".eps", ".tif", ".tiff")
INSTALL_HINT = (
    "没找到 Magplot。桌面版在 https://github.com/erwanjun/magplot/releases 下载；"
    "命令行版 `pipx install magplot`（或 `pip install magplot`）。"
    "装好后重新执行同一条 handoff 命令即可——图已经画出来了。"
)


def magplot_cmd() -> list[str] | None:
    """定位 magplot CLI：显式覆盖 → PATH → 当前解释器里的模块。"""
    override = (os.environ.get("MAGPLOT_CLI") or "").strip()
    if override:
        return [override]
    found = shutil.which("magplot")
    if found:
        return [found]
    probe = subprocess.run([sys.executable, "-c", "import magplot"],
                           capture_output=True)
    return [sys.executable, "-m", "magplot"] if probe.returncode == 0 else None


def run_magplot_open(cmd: list[str], path: str, *, launch: bool) -> dict:
    argv = [*cmd, "open", path, "--json"]
    if not launch:
        argv.append("--no-launch")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        # MAGPLOT_CLI 指到了不存在的东西：说清楚是哪一条，别抛 traceback
        return {"ok": False, "error": f"执行不了 {argv[0]}: {exc}"}
    line = (proc.stdout or "").strip().splitlines()
    try:
        return json.loads(line[-1]) if line else {
            "ok": False, "error": (proc.stderr or "").strip() or "magplot open 没有输出"}
    except ValueError:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "").strip()[:500]}


def product_of(project: str, stem: str) -> str | None:
    for ext in OUT_EXTS:
        candidate = os.path.join(project, stem + ext)
        if os.path.isfile(candidate):
            return candidate
    return None


def needs_run(script: str, project: str, stem: str | None, mode: str) -> bool:
    if mode == "always":
        return True
    if mode == "never" or not script.endswith(".py"):
        return False
    if stem is None:
        return True                       # 还不知道产物是什么：跑一遍才有得看
    product = product_of(project, stem)
    if product is None:
        return True
    return os.path.getmtime(product) < os.path.getmtime(script)


def run_script(python: str, script: str) -> tuple[bool, str]:
    """在脚本自己的目录里跑它——脚本里的相对路径按这个目录解析。"""
    proc = subprocess.run([python, script], capture_output=True, text=True,
                          cwd=os.path.dirname(os.path.abspath(script)) or ".")
    if proc.returncode == 0:
        return True, ""
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-25:]
    return False, "\n".join(tail)


def emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", help="脚本（.py）或它的产物（.pdf/.png…）")
    ap.add_argument("--run", choices=("auto", "always", "never"), default="auto",
                    help="是否先跑一遍脚本：auto=产物缺失或比脚本旧才跑")
    ap.add_argument("--python", default=sys.executable,
                    help="跑脚本用的解释器（默认与本脚本相同）")
    ap.add_argument("--no-launch", action="store_true",
                    help="只登记与自检，不唤起 Magplot 界面")
    args = ap.parse_args(argv)

    path = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.exists(path):
        return emit({"ok": False, "error": f"路径不存在: {path}"}, 2)

    cmd = magplot_cmd()
    if cmd is None:
        # Magplot 没装：图还是要画出来，然后如实告诉调用方缺什么。
        ran, err = (False, "")
        if args.run != "never" and path.endswith(".py"):
            ran, err = run_script(args.python, path)
            if not ran:
                return emit({"ok": False, "error": "脚本运行失败", "stderr": err}, 1)
        return emit({"ok": False, "magplot_missing": True, "ran": ran,
                     "script": path, "hint": INSTALL_HINT}, 3)

    # 1. 先问 Magplot：这是哪个项目、哪个 stem（顺手把注册表补齐）
    probe = run_magplot_open(cmd, path, launch=False)
    if not probe.get("ok"):
        return emit({"ok": False, "error": probe.get("error", "magplot open 失败")}, 2)

    # 2. 需要的话跑一遍脚本
    ran = False
    if needs_run(path, probe["project"], probe.get("stem"), args.run):
        ran, err = run_script(args.python, path)
        if not ran:
            return emit({"ok": False, "error": "脚本运行失败", "stderr": err,
                         "project": probe["project"]}, 1)

    # 3. 交接。**必须再解析一次**：脚本刚跑出来的产物可能带来新的 stem，
    #    第一次探测时它还不在磁盘上（登记与定位都会落空）。
    final = run_magplot_open(cmd, path, launch=not args.no_launch)
    if not final.get("ok"):
        return emit({"ok": False, "ran": ran,
                     "error": final.get("error", "magplot open 失败")}, 2)

    reg = final.get("registry", {})
    payload = {
        "ok": True,
        "ran": ran,
        "project": final["project"],
        "stem": final.get("stem"),
        "parameterizable": reg.get("parameterizable"),
        "launch": (final.get("launch") or {}).get("mode"),
        "conflicts": reg.get("conflicts", []),
        "dynamic_names": reg.get("dynamic_names", []),
    }
    if payload["parameterizable"] is not True:
        payload["hint"] = (
            "这张图没有对应脚本，在 Magplot 里只能当素材排版。"
            "把产出它的 .py 放到产物同一个目录，并让产物名是脚本里的字面量"
            "（不要来自 sys.argv / 时间戳），然后重新交接。")
        return emit(payload, 4)
    return emit(payload, 0)


if __name__ == "__main__":
    raise SystemExit(main())
