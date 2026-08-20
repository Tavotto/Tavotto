#!/usr/bin/env python3
"""实验室 runner 的磁盘清理。**纯标准库**。

self-hosted runner 与一次性 runner 最大的区别就是**它会越跑越满**，而磁盘满了
之后的失败长得千奇百怪（wheel 装一半、SVG 写截断、git checkout 报错），没人
第一时间会想到是磁盘。

这个脚本的每一次删除都必须先过 `assert_within()`：

    绝不执行 `rm -rf "$SOME_VAR"`。

变量拼错一次就是从根目录开始删——CI 上没有人盯着，等发现时已经晚了。所以
路径先 `resolve()`（解开符号链接与 `..`），再断言落在持久化根之内，且不等于
根本身。删不掉的不当作失败：清理是**尽力而为**，因为一个文件锁不住而让整轮
qualification 变红，是把手段当成了目的。

保留与删除的分界是**明确列出来的**，不是靠猜：

    保留：cache/ baselines/ upgrade/      ← 跨 run 有意义
    清理：tmp/ reports/ 里过期的那些      ← 只对当次 run 有意义

用法：
    python scripts/ci/cleanup.py                 # 按默认保留期清理
    python scripts/ci/cleanup.py --dry-run       # 只报告不动手
    python scripts/ci/cleanup.py --kill-stale    # 顺便收掉遗留进程
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    CiError, assert_within, ensure_layout, safe_rmtree, state_root, summary,
)

# 保留期。tmp 短是因为它本来就只服务当次 run；reports 长一点是为了让人能
# 回头翻上个月那次失败到底长什么样。
RETENTION_DAYS = {
    "tmp": 2,
    "reports": 30,
}
# 这些子树在任何情况下都不动——它们正是持久化的意义所在。
PROTECTED = ("cache", "baselines", "upgrade", "locks")


def _age_days(p: Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 86400
    except OSError:
        return 0.0


def sweep(root: Path, dry_run: bool = False) -> list[dict]:
    """按保留期清理 tmp/ 与 reports/。返回处置清单。"""
    actions: list[dict] = []
    for sub, days in RETENTION_DAYS.items():
        base = root / sub
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            age = _age_days(entry)
            if age <= days:
                continue
            record = {"path": str(entry), "age_days": round(age, 1),
                      "reason": f"{sub} 保留 {days} 天", "removed": False}
            if not dry_run:
                try:
                    record["removed"] = safe_rmtree(entry, root)
                except CiError as exc:
                    # 路径安全检查没过 —— 这是真正需要人看的情况，不能静默。
                    record["error"] = exc.message
                    print(f"::warning::拒绝删除 {entry}：{exc.message}", file=sys.stderr)
                except OSError as exc:
                    record["error"] = str(exc)
            actions.append(record)
    return actions


def sweep_workspace_leftovers(root: Path, dry_run: bool = False) -> list[dict]:
    """清掉 tmp/ 下一次性 venv 与解包出来的 artifact。

    它们本来就该随 run 消失，但脚本崩在中途时会留下来，而且个头很大
    （一个装了科学栈的 venv 轻松几百 MB）。
    """
    actions: list[dict] = []
    tmp = root / "tmp"
    if not tmp.is_dir():
        return actions
    for entry in sorted(tmp.glob("venv-*")) + sorted(tmp.glob("artifact-*")):
        record = {"path": str(entry), "age_days": round(_age_days(entry), 1),
                  "reason": "一次性 venv / 解包产物", "removed": False}
        if not dry_run:
            try:
                record["removed"] = safe_rmtree(entry, root)
            except (CiError, OSError) as exc:
                record["error"] = str(exc)
        actions.append(record)
    return actions


def kill_stale_processes(root: Path, dry_run: bool = False) -> list[dict]:
    """收掉归属于本 CI 持久化根的遗留 Tavotto 进程。

    **归属判定只认 CI 持久化根出现在命令行里**——不按进程名。同一台机器上
    维护者自己开着的 Tavotto 与本 CI 无关，误杀一次就再没人敢开这个开关。
    """
    killed: list[dict] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return killed
    marker = str(root.resolve())
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if marker not in cmd:
            continue
        if "tavotto" not in cmd:
            continue
        record = {"pid": pid, "cmd": cmd.strip()[:160], "killed": False}
        if not dry_run:
            try:
                os.kill(pid, signal.SIGTERM)
                record["killed"] = True
            except OSError as exc:
                record["error"] = str(exc)
        killed.append(record)
    return killed


def disk_report(root: Path) -> dict:
    import shutil as _sh
    try:
        usage = _sh.disk_usage(root)
    except OSError:
        return {}
    return {"total_gib": round(usage.total / 1024 ** 3, 1),
            "free_gib": round(usage.free / 1024 ** 3, 1),
            "used_pct": round(usage.used / usage.total * 100, 1)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="实验室 runner 磁盘清理")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不删除")
    ap.add_argument("--kill-stale", action="store_true",
                    help="顺便 SIGTERM 掉归属本 CI 根的遗留 Tavotto 进程")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = ensure_layout()
    before = disk_report(root)
    actions = sweep(root, args.dry_run) + sweep_workspace_leftovers(root, args.dry_run)
    procs = kill_stale_processes(root, args.dry_run) if args.kill_stale else []
    after = disk_report(root)

    payload = {
        "ok": True,
        "state_root": str(root),
        "dry_run": args.dry_run,
        "protected": list(PROTECTED),
        "removed": [a for a in actions if a.get("removed")],
        "skipped": [a for a in actions if not a.get("removed")],
        "killed_processes": procs,
        "disk_before": before,
        "disk_after": after,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    n_rm = len(payload["removed"])
    verb = "将删除" if args.dry_run else "已删除"
    print(f"{verb} {n_rm} 项；保留 {', '.join(PROTECTED)}")
    if procs:
        print(f"遗留进程 {len(procs)} 个" + ("（dry-run 未处理）" if args.dry_run else "，已 SIGTERM"))
    if after:
        print(f"磁盘：{after['free_gib']} GiB 可用（{after['used_pct']}% 已用）")
    summary(f"\n**清理** — {verb} {n_rm} 项，剩余 {after.get('free_gib', '?')} GiB\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
