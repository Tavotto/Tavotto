#!/usr/bin/env python3
"""实验室 runner 的开跑前体检。**纯标准库**。

为什么值得单独一个门禁：self-hosted runner 是长期存在的机器，它会累积状态——
上一轮 CI 崩掉后留下的 worker 进程、没释放的锁、快满的磁盘、被别人改掉的
locale。这些东西不会让 job 立刻失败，只会让后面的 benchmark 数字变得没有意义、
让 soak 的泄漏判定误报、让视觉比对因为字体回退而整片变红。**在源头报出来，
比在十分钟后拿着一份可疑的报告猜要便宜得多。**

每一项都给出可执行的处置建议——「preflight failed」本身不解决任何问题。

用法：
    python scripts/ci/lab_preflight.py --mode nightly
    python scripts/ci/lab_preflight.py --mode main --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:                                    # Windows 上没有 resource 模块
    import resource                     # noqa: PLC0415
except ImportError:                     # pragma: no cover - 只在 Windows 走到
    resource = None                     # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402
    CiError, ensure_layout, find_ci_owned_tavotto, run_metadata, state_root,
    summary, summary_table, write_report,
)

# 门槛按「够不够跑得出可信数字」定，不是按「机器好不好」。
# 4C/8G 以下时 benchmark 与 soak 的并发假设不再成立，宁可明确拒绝。
MIN_CPU = 4
MIN_RAM_GIB = 8.0
MIN_DISK_GIB = 20.0
# 每个模式各自的磁盘胃口：golden + visual + upgrade fixture 会占不少。
MODE_DISK_GIB = {"main": 20.0, "nightly": 40.0, "release": 40.0, "weekly": 60.0}


class Check:
    """一条体检项。ok=False 即阻断；warn 只记录不阻断。"""

    def __init__(self, name: str, ok: bool, detail: str, *,
                 warn: bool = False, remedy: str = "") -> None:
        self.name, self.ok, self.detail = name, ok, detail
        self.warn, self.remedy = warn, remedy

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "warn": self.warn, "remedy": self.remedy}


# --------------------------------------------------------------------------
def check_hardware() -> list[Check]:
    meta = run_metadata()
    cpu, ram = meta["cpu_count"], meta["ram_gib"]
    checks = [
        Check("CPU 核数", cpu >= MIN_CPU, f"{cpu} 核（要求 ≥ {MIN_CPU}）",
              remedy="给 VM 分配更多 vCPU；低于门槛时 benchmark 并发假设不成立"),
    ]
    # ram_gib == 0 是「读不出来」，不是「零内存」。把未知当成不足，会让这条
    # 门禁在任何非 Linux 开发机上恒红——而恒红的门禁很快就会被加进忽略列表。
    if ram <= 0:
        checks.append(Check("内存", True, "读不到物理内存（非 Linux 或 /proc 不可用），跳过判定",
                            warn=True))
    else:
        checks.append(Check("内存", ram >= MIN_RAM_GIB, f"{ram} GiB（要求 ≥ {MIN_RAM_GIB}）",
                            remedy="给 VM 分配更多内存"))
    return checks


def check_state_root(mode: str) -> list[Check]:
    checks: list[Check] = []
    root = state_root()
    try:
        ensure_layout(root)
        checks.append(Check("持久化根目录", True, f"{root} 可写，布局完整"))
    except CiError as exc:
        checks.append(Check("持久化根目录", False, exc.message,
                            remedy=f"sudo install -d -o $(whoami) -g $(whoami) {root}"))
        return checks

    need = MODE_DISK_GIB.get(mode, MIN_DISK_GIB)
    try:
        free = shutil.disk_usage(root).free / 1024 ** 3
    except OSError as exc:
        checks.append(Check("磁盘余量", False, f"读不到 {root} 的用量：{exc}"))
        return checks
    checks.append(Check("磁盘余量", free >= need, f"{free:.1f} GiB 可用（{mode} 模式要求 ≥ {need}）",
                        remedy="跑 scripts/ci/cleanup.py，或扩容 VM 磁盘"))
    return checks


def check_toolchain(mode: str) -> list[Check]:
    """按模式要什么查什么——main 模式不碰 Rust，就别拿 Rust 缺席去拦它。"""
    checks = [
        Check("Python", sys.version_info >= (3, 10),
              f"{sys.version.split()[0]}（要求 ≥ 3.10，与 pyproject 的 requires-python 同源）"),
    ]
    for exe, why in (("git", "checkout 与版本元数据"), ("node", "前端"), ("pnpm", "前端")):
        found = shutil.which(exe)
        checks.append(Check(f"可执行文件 {exe}", bool(found), found or "未找到",
                            remedy=f"装 {exe}（{why}）；见 docs/ci/self-hosted-runner.md"))
    if mode in ("nightly", "release", "weekly"):
        cargo = shutil.which("cargo")
        checks.append(Check("Rust cargo", bool(cargo), cargo or "未找到",
                            remedy="装 rustup，并确认 runner 服务的 PATH 里有 ~/.cargo/bin"
                                   "（systemd 的最小 PATH 不含它）"))
    return checks


def check_environment() -> list[Check]:
    """locale / 时区 / FD 上限。这三样错了不会崩，只会让结果变得不可复现。"""
    checks: list[Check] = []

    lang = os.environ.get("LC_ALL") or os.environ.get("LANG") or ""
    checks.append(Check(
        "locale", "UTF-8" in lang.upper(), f"LANG/LC_ALL = {lang or '(未设置)'}",
        warn=True,
        remedy="lab workflow 顶层已统一设 LANG=C.UTF-8；这里为空说明没经由该 workflow 启动"))

    tz = os.environ.get("TZ", "")
    checks.append(Check("时区", tz == "UTC", f"TZ = {tz or '(未设置，跟随系统)'}",
                        warn=True,
                        remedy="非 UTC 会让带时间戳的产物出现无意义 diff"))

    if resource is None:
        checks.append(Check("文件描述符上限", True, "本平台无 resource 模块（Windows），跳过",
                            warn=True))
    else:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        # 1024 是常见默认值。soak 要同时开多个 worker + HTTP 连接，撞上限时的
        # 症状是「随机的 OSError: Too many open files」，极难与真实泄漏区分。
        checks.append(Check("文件描述符上限", soft >= 4096,
                            f"soft={soft} hard={hard}（要求 soft ≥ 4096）",
                            remedy="在 runner 的 systemd unit 里设 LimitNOFILE=65536"))
    return checks


def check_stale_processes(reap: bool = False) -> list[Check]:
    """上一轮 CI 漏下的 Tavotto 进程。

    **判归属靠的是 CI 持久化根与 runner 工作目录，不是进程名**（唯一实现
    `_common.is_ci_owned_tavotto`）。直接 `pgrep tavotto` 会误伤同一台机器上
    维护者自己开着的实例——假报一次，下次真出问题时这条提示就已经被学会无视了。

    **`reap=True` 时自愈。** 这条检查从 2026-08-22 05:55 起把**每一次** lab run
    挡在门外：一个手工探测留下的进程
    （`/srv/tavotto-ci/tmp/venv-manual-probe/… -m tavotto`）躺在那儿，而唯一的
    解法是有人 SSH 上去手动清。`cleanup.py`（会 kill）排在体检**之后**，
    体检失败它就永远轮不到——**自愈动作排在检测它的那道门之后，等于没有自愈**。

    自愈之后**必须真的复检**，不能「发了信号就当它死了」：SIGTERM 对一个卡在
    C 扩展里的 worker 可能毫无作用，而「报告已清理、其实还在」会让 soak 的
    泄漏判定与 benchmark 拿着被污染的机器继续跑——那比直接失败糟糕得多。
    """
    stale = find_ci_owned_tavotto()
    if not stale:
        return [Check("上一轮遗留的 Tavotto 进程", True, "无")]

    if not reap:
        return [Check("上一轮遗留的 Tavotto 进程", False,
                      f"{len(stale)} 个：" + "; ".join(f"pid={p} {c[:100]}"
                                                      for p, c in stale[:3]),
                      remedy="这些进程会污染本轮的泄漏判定与 benchmark；"
                             "确认无人正在用后 kill 掉，跑 "
                             "scripts/ci/cleanup.py --kill-stale，"
                             "或给体检加 --reap-stale 让它自己清")]

    reaped, survivors = reap_stale_processes(stale)
    if survivors:
        return [Check("上一轮遗留的 Tavotto 进程", False,
                      f"清掉 {len(reaped)} 个，仍有 {len(survivors)} 个没死："
                      + "; ".join(f"pid={p} {c[:80]}" for p, c in survivors[:3]),
                      remedy="SIGKILL 之后仍在（多半是 D 状态，卡在内核里）。"
                             "这台机器需要人上去看一眼，别让它继续跑门禁")]
    return [Check("上一轮遗留的 Tavotto 进程", True,
                  f"发现 {len(reaped)} 个并已清理（--reap-stale）："
                  + "; ".join(f"pid={p}" for p, _ in reaped[:5]),
                  warn=True,   # 通过，但**必须在 summary 里看得见**
                  remedy="上一轮没退干净。偶发可以接受，反复出现要查 worker 的退出路径")]


# SIGTERM 之后给多久，再上 SIGKILL。worker 收到 TERM 会走正常关闭
# （落盘、通知父进程），太短会把正常关闭截断成半个状态。
_REAP_GRACE_S = 10.0


def reap_stale_processes(stale: list[tuple[int, str]],
                         grace_s: float = _REAP_GRACE_S,
                         sleep=time.sleep) -> tuple[list, list]:
    """SIGTERM → 等 → SIGKILL → **重新按判据扫一遍**。

    回 `(清掉的, 还活着的)`。最后一步是重扫而不是「记账说杀过了」：
    判断依据必须是**这一刻机器上真实的进程表**，不是我们的意图。
    """
    import signal as _sig

    # **`SIGKILL` 在 Windows 上不存在**（AttributeError: module 'signal' has no
    # attribute 'SIGKILL'）。这段只在实验室的 Linux runner 上真跑
    # （`find_ci_owned_tavotto` 没有 /proc 就回空），但**模块要在所有平台上
    # import 得动、用例要在所有平台上跑得了**——CI 的 windows 腿当场逮到了。
    # Windows 上 `os.kill(pid, SIGTERM)` 走的是 TerminateProcess，本来就是
    # 强制终止，退化成它是正确的语义。
    _SIGKILL = getattr(_sig, "SIGKILL", _sig.SIGTERM)

    targets = {pid for pid, _ in stale}
    for pid in targets:
        try:
            os.kill(pid, _sig.SIGTERM)
        except OSError:
            pass                       # 已经没了，正常

    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not [p for p, _ in find_ci_owned_tavotto() if p in targets]:
            break
        sleep(0.25)

    for pid, _ in find_ci_owned_tavotto():
        if pid in targets:
            try:
                os.kill(pid, _SIGKILL)
            except OSError:
                pass
    sleep(0.5)

    survivors = [(p, c) for p, c in find_ci_owned_tavotto() if p in targets]
    dead = {p for p, _ in survivors}
    reaped = [(p, c) for p, c in stale if p not in dead]
    return reaped, survivors


def check_stale_locks() -> list[Check]:
    """锁文件残留。

    锁本身是 flock 持有的，进程一死内核就释放了——所以**文件存在不等于被锁住**。
    这里只在文件老得离谱时报警：真正的互斥由 flock 保证，这条是给人看的线索。
    """
    lock_dir = state_root() / "locks"
    if not lock_dir.is_dir():
        return [Check("锁目录", True, "尚未创建（首次运行）")]
    old: list[str] = []
    now = time.time()
    for f in lock_dir.glob("*.lock"):
        try:
            age_h = (now - f.stat().st_mtime) / 3600
        except OSError:
            continue
        if age_h > 6:
            old.append(f"{f.name}（{age_h:.1f} 小时未更新）")
    return [Check("陈旧锁文件", True,  # 恒不阻断：flock 才是权威
                  "无" if not old else "; ".join(old), warn=bool(old),
                  remedy="仅作线索；互斥由 flock 保证，进程退出即释放")]


def check_workspace() -> list[Check]:
    """工作目录必须是干净 checkout。

    self-hosted runner **不会**自动清空工作目录。上一轮留下的 build 产物会让
    「这次真的重新构建了吗」变得无法回答，而且是静默的。
    """
    ws = os.environ.get("GITHUB_WORKSPACE")
    if not ws:
        return [Check("工作目录", True, "非 GitHub Actions 环境，跳过")]
    p = Path(ws)
    if not (p / "pyproject.toml").is_file():
        return [Check("工作目录", False, f"{p} 里没有 pyproject.toml，checkout 可能不完整")]
    try:
        dirty = subprocess.run(["git", "-C", ws, "status", "--porcelain"],
                               capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return [Check("工作目录", True, f"git status 跑不了（{exc}），跳过", warn=True)]
    lines = [ln for ln in dirty.splitlines() if ln.strip()]
    return [Check("工作目录干净", not lines,
                  "干净" if not lines else f"{len(lines)} 处未提交改动：" + "; ".join(lines[:3]),
                  warn=True,  # 不阻断：workflow 里可能刻意生成过文件
                  remedy="确认 workflow 用了 actions/checkout 的 clean 语义")]


# --------------------------------------------------------------------------
def run_all(mode: str, reap: bool = False) -> list[Check]:
    checks: list[Check] = []
    checks += check_hardware()
    checks += check_state_root(mode)
    checks += check_toolchain(mode)
    checks += check_environment()
    checks += check_stale_processes(reap=reap)
    checks += check_stale_locks()
    checks += check_workspace()
    return checks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="实验室 runner 开跑前体检")
    ap.add_argument("--mode", default="main",
                    choices=["main", "nightly", "release", "weekly"])
    ap.add_argument("--json", action="store_true", help="把结果打到 stdout")
    ap.add_argument("--no-report", action="store_true",
                    help="不写报告文件（持久化目录还没建好时用）")
    ap.add_argument("--reap-stale", action="store_true",
                    help="发现归属本 CI 的遗留 Tavotto 进程时自己清掉，"
                         "**并复检确认真的清干净了**。CI 用；人工排查时别开，"
                         "那时你多半想先看看它们是谁")
    args = ap.parse_args(argv)

    checks = run_all(args.mode, reap=args.reap_stale)
    blocking = [c for c in checks if not c.ok and not c.warn]
    warnings = [c for c in checks if c.warn and not c.ok] + [c for c in checks if c.warn and c.detail not in ("无",)]

    payload = {
        "ok": not blocking,
        "mode": args.mode,
        "metadata": run_metadata(args.mode),
        "checks": [c.as_dict() for c in checks],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not args.no_report:
        try:
            write_report("preflight.json", payload)
        except CiError:
            pass  # 根目录不可写本身已经是上面的一条 check，不必二次爆炸

    rows = []
    for c in checks:
        mark = "✅" if c.ok else ("⚠️" if c.warn else "❌")
        rows.append((c.name, mark, c.detail))
    summary(f"### Preflight · {args.mode}\n\n" + summary_table(rows))

    for c in checks:
        mark = "OK  " if c.ok else ("WARN" if c.warn else "FAIL")
        print(f"[{mark}] {c.name}: {c.detail}")
        if not c.ok and c.remedy:
            print(f"       → {c.remedy}")

    if blocking:
        names = "、".join(c.name for c in blocking)
        print(f"\npreflight 未通过：{names}", file=sys.stderr)
        summary(f"\n> **preflight 阻断** — {names}\n")
        return 1
    print("\npreflight 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
