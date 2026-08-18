"""`magplot` 的子命令分派：`open` 与 `doctor`。

**为什么单独一个模块。** 这两条命令是给别的程序调的（Codex 插件、安装器、
编辑器），它们只需要纯标准库的那点逻辑，却曾经只能从 `app.main()` 进——
那条路会 import Flask、pymupdf 和整个 app.py。装在用户机器上的
`magplot-cli.exe` 每次交接都要付这份冷启动，而它一个 HTTP 端点都用不上。

所以分派提到这里，`packaging/entry.py` 与 `app.main()` 都先问它一句；
它答不上来（不是子命令）才走原来的 argparse 主入口。**必须在 argparse
之前**——主入口是纯 flag 形态（`magplot --figures …`），改成 subparsers
会把既有命令行整个换掉。

纯标准库。
"""
from __future__ import annotations

import argparse
import json
import sys

#: 认得的子命令。放在这里是为了让「它是不是子命令」这个判断只有一处。
COMMANDS = ("open", "doctor")


def dispatch(argv: list[str]) -> int | None:
    """argv[0] 是子命令就执行并返回退出码；不是就返回 None（交回主入口）。"""
    if not argv or argv[0] not in COMMANDS:
        return None
    if argv[0] == "open":
        from . import handoff
        return handoff.cli(argv[1:])
    return doctor(argv[1:])


# ---------------------------------- doctor --------------------------------
def doctor(argv: list[str]) -> int:
    """`magplot doctor`：不起界面、不起服务的健康检查 + 安装清单维护。

    三种用法，都只在本机读写一个 JSON 文件：

      magplot doctor --json                     只体检（安装器装完跑这一条就够）
      magplot doctor --json --write-manifest    体检并把 install.json 刷新成「我在这儿」
      magplot doctor --json --remove-manifest   卸载时清掉 install.json

    退出码：0 = 这套装置能用（找得到自己、CLI 可执行）；1 = 有硬伤（下面
    `problems` 里逐条说）。**安装器拿它当验收**：装完连自己都定位不到，
    那这个包发出去也只会在用户机器上表现为「Codex 找不到 Magplot」。
    """
    ap = argparse.ArgumentParser(
        prog="magplot doctor",
        description="检查这台机器上的 Magplot 安装，并维护交接用的安装清单")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    ap.add_argument("--write-manifest", action="store_true",
                    help="把安装清单刷新成当前这套安装（安装器/升级时用）")
    ap.add_argument("--remove-manifest", action="store_true",
                    help="删除安装清单（卸载时用）")
    args = ap.parse_args(argv)

    if args.write_manifest and args.remove_manifest:
        print("--write-manifest 与 --remove-manifest 不能同时给", file=sys.stderr)
        return 2

    from . import locate
    from .. import __version__

    problems: list[str] = []
    me = locate.describe_self()
    report = {
        "ok": True,
        "product": "Magplot",
        "version": __version__,
        "protocol": locate.PROTOCOL_VERSION,
        "executable": sys.executable,
        "frozen": bool(me["frozen"]),
        "cli": me["cli"],
        "desktop": me["desktop"],
        "install_dir": me["install_dir"],
        "manifest": {"path": locate.manifest_path(), "action": "read",
                     "written": False, "removed": False},
        "problems": problems,
    }

    if args.remove_manifest:
        report["manifest"]["action"] = "remove"
        report["manifest"]["removed"] = locate.remove_manifest()
    elif args.write_manifest:
        report["manifest"]["action"] = "write"
        try:
            path = locate.write_manifest({**me, "source": "installer"})
            report["manifest"]["path"] = path
            report["manifest"]["written"] = True
        except OSError as exc:
            # 清单写不出来不代表这台机器不能用：已知安装位置那条腿还在。
            # 但要如实说，别让安装器以为一切正常。
            problems.append(f"安装清单写不出来（{exc}）；"
                            "外部程序仍可按已知安装位置发现 Magplot")
    else:
        found = locate.read_manifest()
        report["manifest"]["found"] = bool(found)
        if found:
            report["manifest"]["stale"] = found["stale"]
            report["manifest"]["cli"] = found["cli"]
            report["manifest"]["desktop"] = found["desktop"]

    if me["frozen"] and not me["cli"]:
        # 冻结产物里没有 console 版 CLI = 这个安装包漏打了 magplot-cli，
        # 外部程序（Codex 插件）只能看到一个不能当 CLI 用的 GUI 可执行文件。
        problems.append(
            "这套安装里没有 magplot-cli（console 版命令行）——"
            "外部程序将无法通过安装位置发现 Magplot，请重新安装最新版本")
    report["ok"] = not problems

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"* Magplot {report['version']}（交接协议 v{report['protocol']}）")
        print(f"* 可执行文件: {report['executable']}")
        print(f"* 命令行入口: {report['cli'] or '（无）'}")
        print(f"* 桌面应用:   {report['desktop'] or '（未安装或未找到）'}")
        print(f"* 安装清单:   {report['manifest']['path']}"
              f"（{report['manifest']['action']}）")
        for line in problems:
            print(f"! {line}")
    return 0 if report["ok"] else 1
