"""`tavotto` 的子命令分派：`open` 与 `doctor`。

**为什么单独一个模块。** 这两条命令是给别的程序调的（Codex 插件、安装器、
编辑器），它们只需要纯标准库的那点逻辑，却曾经只能从 `app.main()` 进——
那条路会 import Flask、pymupdf 和整个 app.py。装在用户机器上的
`tavotto-cli.exe` 每次交接都要付这份冷启动，而它一个 HTTP 端点都用不上。

所以分派提到这里，`packaging/entry.py` 与 `app.main()` 都先问它一句；
它答不上来（不是子命令）才走原来的 argparse 主入口。**必须在 argparse
之前**——主入口是纯 flag 形态（`tavotto --figures …`），改成 subparsers
会把既有命令行整个换掉。

纯标准库。
"""
from __future__ import annotations

import argparse
import json
import sys

#: 认得的子命令。放在这里是为了让「它是不是子命令」这个判断只有一处。
COMMANDS = ("open", "doctor")


def use_utf8_streams() -> None:
    """把 stdout/stderr 钉成 UTF-8。**每个入口在做任何输出之前都要调一次。**

    Windows 上 stdout 一旦不是真控制台（被重定向到文件、由安装器或 Codex
    接管管道），就退回系统区域编码（cp1252 / cp936）。`doctor` 的结论、
    `open` 的提示、启动信息全是中文，一句 `print` 就是 UnicodeEncodeError
    直接打死进程——用户看到的是「启动即崩」或者一堆 traceback，而调用方
    等的是那行 JSON。

    这一份是**唯一出处**：`app.main()`、`tavotto/cli_entry.py` 与
    `packaging/entry.py` 都调它。分派提前到 cli_entry 之后，`doctor` 会跑在
    `app.main()` 那次重配**之前**——同一个坑必须由入口自己先填上
    （`test_cli_entry_survives_a_non_utf8_console` 看护）。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass          # 已经被接管成不可重配的对象：不值得为它崩


def dispatch(argv: list[str]) -> int | None:
    """argv[0] 是子命令就执行并返回退出码；不是就返回 None（交回主入口）。"""
    if not argv or argv[0] not in COMMANDS:
        return None
    # 子命令的输出全是中文，而它们可能跑在 `app.main()` 的重配之前
    use_utf8_streams()
    if argv[0] == "open":
        from . import handoff
        return handoff.cli(argv[1:])
    return doctor(argv[1:])


# ------------------------- doctor --migrate（P1-08） -----------------------
def _doctor_migrate(args) -> int:
    """Magplot 0.7 → Tavotto 的数据迁移入口（实现见 engine/migrate.py）。

    退出码：0 = 完成（含「没什么可迁」）；1 = 有冲突（部分文件因目标已存在
    被跳过，逐条列出）；2 = 参数冲突。dry-run 永远 0。
    """
    from . import migrate

    if args.migrate and args.rollback_migration:
        msg = "--migrate 与 --rollback-migration 不能同时给"
        if args.json:
            print(json.dumps({"ok": False, "code": "bad_migrate_action",
                              "error": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2

    if args.rollback_migration:
        result = migrate.rollback()
        if args.json:
            print(json.dumps({"ok": result["rolled_back"],
                              "code": (None if result["rolled_back"]
                                       else "rollback_unavailable"),
                              **result},
                             ensure_ascii=False))
        else:
            if result["rolled_back"]:
                print(f"* 已回滚迁移：删除 {len(result['removed'])} 个文件"
                      f"（Magplot 旧数据从头到尾没动过）")
            else:
                print(f"* 无法回滚：{result['reason']}")
        return 0 if result["rolled_back"] else 1

    report = migrate.execute(dry_run=args.dry_run)
    plan = report["plan"]
    if args.json:
        print(json.dumps({"ok": True, "code": None, **report},
                         ensure_ascii=False))
    else:
        if plan["nothing_to_migrate"]:
            print("* 没找到可迁移的 Magplot 数据"
                  f"（找过 {plan['legacy_config_dir']} 与 {plan['legacy_data_dir']}）")
        elif args.dry_run:
            print(f"* 迁移计划（dry-run，一个字节没写）：")
            print(f"  将复制 {len(plan['copies'])} 个文件 → {plan['target_data_dir']}")
            if plan["config_merge"]:
                print(f"  将合并配置 {plan['config_merge']}（只补缺，不覆盖）")
            for rel in plan["conflicts"]:
                print(f"  ⚠ 目标已存在且内容不同，将跳过: {rel}")
        else:
            print(f"* 已复制 {len(report['created'])} 个文件"
                  f" → {plan['target_data_dir']}")
            if report.get("config", {}) and report["config"].get("merged"):
                a = report["config"]["added"]
                print(f"* 配置已合并：补入 {a['recent_projects']} 条最近项目、"
                      f"{a['projects']} 个项目设置")
            for rel in plan["conflicts"]:
                print(f"⚠ 跳过（目标已存在且内容不同）: {rel}")
            print(f"* 迁移报告: {migrate.report_path()}"
                  "（回滚用 tavotto doctor --rollback-migration）")
            print("* Magplot 旧数据原样保留，确认无误后可自行删除")
    return 1 if (not args.dry_run and plan["conflicts"]) else 0


# ---------------------------------- doctor --------------------------------
def doctor(argv: list[str]) -> int:
    """`tavotto doctor`：不起界面、不起服务的健康检查 + 安装清单维护。

    三种用法，都只在本机读写一个 JSON 文件：

      tavotto doctor --json                     只体检（安装器装完跑这一条就够）
      tavotto doctor --json --write-manifest    体检并把 install.json 刷新成「我在这儿」
      tavotto doctor --json --remove-manifest   卸载时清掉 install.json

    退出码：0 = 这套装置能用（找得到自己、CLI 可执行）；1 = 有硬伤（下面
    `problems` 里逐条说）。**安装器拿它当验收**：装完连自己都定位不到，
    那这个包发出去也只会在用户机器上表现为「Codex 找不到 Tavotto」。

    `problems` 的每一条都是 `{"code", "message"}`：**code 稳定，message 随时
    可改**（与 `tavotto open` 的失败同一条纪律）。只给一句中文的话，调用方
    要区分「清单写不出来」和「这个包漏打了 CLI」就只能去匹配字符串——而这两
    件事的处置完全不同：前者还能用，后者得重装。
    """
    ap = argparse.ArgumentParser(
        prog="tavotto doctor",
        description="检查这台机器上的 Tavotto 安装，并维护交接用的安装清单")
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    ap.add_argument("--write-manifest", action="store_true",
                    help="把安装清单刷新成当前这套安装（安装器/升级时用）")
    ap.add_argument("--remove-manifest", action="store_true",
                    help="删除安装清单（卸载时用）")
    ap.add_argument("--migrate", action="store_true",
                    help="把 Magplot 0.7 的用户数据（配置/布局/版本历史/AI 记录）"
                         "迁入 Tavotto。只复制不覆盖，旧数据一个字节不动")
    ap.add_argument("--dry-run", action="store_true",
                    help="与 --migrate 连用：只输出迁移计划，不写任何东西")
    ap.add_argument("--rollback-migration", action="store_true",
                    help="按上次迁移报告删除迁移时创建的文件（旧数据无关）")
    args = ap.parse_args(argv)

    from . import locate
    from .. import __version__

    if args.write_manifest and args.remove_manifest:
        # `--json` 一旦给了，**失败也必须是一行 JSON**（与 `tavotto open` 同一条
        # 纪律）。只往 stderr 写一句中文，机器调用方就拿不到 code，只能去匹配
        # 字符串——而这条恰恰是它自己把参数拼错了，最该被程序读懂。
        msg = "--write-manifest 与 --remove-manifest 不能同时给"
        if args.json:
            print(json.dumps({"ok": False, "protocol": locate.PROTOCOL_VERSION,
                              "code": "bad_manifest_action", "error": msg,
                              "problems": [{"code": "bad_manifest_action",
                                            "message": msg}]},
                             ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2

    if args.migrate or args.rollback_migration:
        return _doctor_migrate(args)

    problems: list[dict] = []
    me = locate.describe_self()
    report = {
        "ok": True,
        "product": "Tavotto",
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
            problems.append({
                "code": "manifest_write_failed",
                "message": f"安装清单写不出来（{exc}）；"
                           "外部程序仍可按已知安装位置发现 Tavotto"})
    else:
        found = locate.read_manifest()
        report["manifest"]["found"] = bool(found)
        if found:
            report["manifest"]["stale"] = found["stale"]
            report["manifest"]["cli"] = found["cli"]
            report["manifest"]["desktop"] = found["desktop"]

    if me["frozen"] and not me["cli"]:
        # 冻结产物里没有 console 版 CLI = 这个安装包漏打了 tavotto-cli，
        # 外部程序（Codex 插件）只能看到一个不能当 CLI 用的 GUI 可执行文件。
        problems.append({
            "code": "bundled_cli_missing",
            "message": "这套安装里没有 tavotto-cli（console 版命令行）——"
                       "外部程序将无法通过安装位置发现 Tavotto，请重新安装最新版本"})
    # `notes` 是**不翻 ok 的提示**：装置能用，但有一件事值得知道。
    # 2026-08-20 实测的那台机器就是这样：旧 Magplot.app 目录被就地升级成了
    # Tavotto 0.8.0，文件都在、签名有效，doctor 报「一切正常」——而桌面启动
    # 异常时这条目录名错位正是第一线索，不说出来用户毫无抓手。
    notes: list[dict] = report.setdefault("notes", [])
    install_dir = me["install_dir"] or ""
    if sys.platform == "darwin" and install_dir.endswith(".app") \
            and not install_dir.endswith("/Tavotto.app"):
        notes.append({
            "code": "bundle_dir_renamed",
            "message": f"安装目录名与产品不符：{install_dir}"
                       "（多半是旧版本目录被就地升级）。功能不受影响；"
                       "如桌面启动异常，先卸载这份、重装到 Tavotto.app"})
    # 旧 Magplot 数据还在、又没迁移过：明说有一条产品化的迁移路，
    # 别让最早那批用户以为升级 = 从零开始（P1-08）。
    try:
        from . import migrate
        if migrate.legacy_found() and not migrate.report_path().is_file():
            notes.append({
                "code": "magplot_data_found",
                "message": "检测到 Magplot 0.7 的用户数据。"
                           "运行 `tavotto doctor --migrate` 可把配置/布局/"
                           "版本历史/AI 记录迁入 Tavotto（只复制不覆盖，"
                           "旧数据一个字节不动；--dry-run 先看计划）"})
    except Exception:  # noqa: BLE001 — 体检的附注绝不能把体检本身弄挂
        pass
    report["ok"] = not problems
    # 顶层也给一个 code：调用方最常问的就是「这次到底哪儿不对」，
    # 不该逼它先去翻数组。多个问题时取第一个（严重程度按追加顺序）。
    report["code"] = problems[0]["code"] if problems else None

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"* Tavotto {report['version']}（交接协议 v{report['protocol']}）")
        print(f"* 可执行文件: {report['executable']}")
        print(f"* 命令行入口: {report['cli'] or '（无）'}")
        print(f"* 桌面应用:   {report['desktop'] or '（未安装或未找到）'}")
        print(f"* 安装清单:   {report['manifest']['path']}"
              f"（{report['manifest']['action']}）")
        for problem in problems:
            print(f"! [{problem['code']}] {problem['message']}")
        for note in report["notes"]:
            print(f"~ [{note['code']}] {note['message']}")
    return 0 if report["ok"] else 1
