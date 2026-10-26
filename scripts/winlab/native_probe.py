"""桌面宿主原生 Agent 的首屏探查（交互会话一侧；规则全文 docs/rules/ci/windows-lab.md「AI 宿主分层」）。

    python native_probe.py --exe <主程序> --root <安装目录> --out <结果.json> --shots <证据目录> [--vscode-profile <目录>]

由 `gui_host.py` 在 tavotto-lab 的交互会话里拉起（`agent.run_in_gui`）。**只看、不点、不登录、不调模型**：
起宿主（调试端口只绑回环，同 desktop_driver），等主窗口，再等界面稳定，记下每个 CDP 页面的文字与截图、
（可选）点 agent 登记的唯一一个入口按钮看下一屏、
所有顶层窗口的标题，然后按 Job 结束进程树。宿主不开放调试端口时只记窗口标题与 GDI 截图，如实写 `cdp: false`。
结论（要不要账户、有没有自定义模型入口）在 agent.py 里按文字判，这里不下结论。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent as A  # noqa: E402 — 同目录部署：Job Object 的唯一出处
import desktop_driver as D  # noqa: E402 — CDP / 主窗口 / 回环检查的唯一出处
import window_probe as P  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 主窗口出现后至少等多久、最多等多久：首屏文字连续两次取样不变（且不是「准备中」）才取证。
#: WorkBuddy 首启要先准备运行环境、TRAE 首屏晚渲染，30 s 不够（run 20261026T140943Z-44977d / 140642Z-e82eb9）
SETTLE_MIN_S = 20
SETTLE_MAX_S = 240
SETTLE_POLL_S = 10
NOT_READY_RE = re.compile(r"preparing|starting\.\.\.|loading|正在准备|加载中|初始化", re.I)
TEXT_CHARS = 6000


def host_args(port: int | None, profile: str | None, workspace: str | None) -> list[str]:
    """调试端口只给 `--remote-debugging-port`（不传 address，Chromium 默认只绑 127.0.0.1）；`port=None` = 不开。
    VS Code 系用 run 目录里的独立 profile，不碰账户的真实配置。"""
    args = [f"--remote-debugging-port={port}"] if port else []
    if profile:
        args += [
            f"--user-data-dir={Path(profile) / 'data'}",
            f"--extensions-dir={Path(profile) / 'ext'}",
            "--skip-welcome",
            "--disable-workspace-trust",
            "--new-window",
        ]
        if workspace:
            args.append(workspace)
    return args


def _page_texts(port: int) -> str:
    """一次性取所有页面的文字（只读）；没有调试端口就返回空串。"""
    try:
        version = D.http_json(port, "/json/version")
    except (OSError, ValueError):
        return ""
    cdp = D.CDP(D.WebSocket(port, "/" + version["webSocketDebuggerUrl"].split("/", 3)[3]))
    try:
        texts = []
        for t in cdp.call("Target.getTargets").get("targetInfos", []):
            if t.get("type") != "page":
                continue
            sid = cdp.call("Target.attachToTarget", {"targetId": t["targetId"], "flatten": True})[
                "sessionId"
            ]
            texts.append(cdp.evaluate(sid, D.TEXT_JS) or "")
        return "\n".join(texts)
    finally:
        cdp.ws.close()


def settle(port: int, deadline: float) -> float:
    """等首屏稳定：连续两次取样文字相同、非空、且不是「准备中 / 加载中」。返回等了多久（到上限也如实返回）。"""
    t0, last = time.monotonic(), None
    while time.monotonic() < deadline:
        try:
            cur = _page_texts(port)
        except Exception:  # noqa: BLE001 — 取样失败当没稳定，继续等到上限
            cur = None
        if cur and cur == last and not NOT_READY_RE.search(cur):
            break
        last = cur
        time.sleep(SETTLE_POLL_S)
    return round(time.monotonic() - t0, 1)


#: 点一个按钮：文字必须与 agent 登记的那一个逐字相等（叶子元素，点它最近的可点祖先）
PEEK_JS = (
    "(() => {const want = %s;"
    "for (const e of document.querySelectorAll('body *')) {"
    " if (e.children.length) continue; if ((e.textContent || '').trim() !== want || e.offsetParent === null) continue;"
    " (e.closest('button,[role=button],a,[onclick],[tabindex]') || e).click(); return true; }"
    "return false;})()"
)


def peek(cdp, sid: str, button: str, shots: Path) -> dict:
    """只点 agent 登记的那一个入口按钮（如 ZCode 的「Use API key」），看下一屏要填什么；不填、不提交、不登录。"""
    clicked = bool(cdp.evaluate(sid, PEEK_JS % json.dumps(button)))
    time.sleep(5)
    res = {
        "button": button,
        "clicked": clicked,
        "text": (cdp.evaluate(sid, D.TEXT_JS) or "")[:TEXT_CHARS],
    }
    with contextlib.suppress(D.CDPError, TimeoutError, OSError):
        shot = cdp.call("Page.captureScreenshot", {"format": "png"}, session=sid, timeout=20)
        D.save_png(shot["data"], shots / "native-peek.png")
        res["screenshot"] = "native-peek.png"
    return res


def probe(ns: argparse.Namespace) -> dict:
    """先带调试端口起；起不出主窗口就再起一次、不带调试端口（有的宿主拒绝这个开关，
    run 20261026T141609Z-f9207d 的 Claude Desktop），那一次只有窗口标题与截图。"""
    first = probe_once(ns, D.free_port())
    if first.get("window"):
        return first
    second = probe_once(ns, None)
    second["debug_port_attempt"] = {"window": None, "error": first.get("error")}
    return second


def probe_once(ns: argparse.Namespace, port: int | None) -> dict:
    root, shots = Path(ns.root), Path(ns.shots)
    shots.mkdir(parents=True, exist_ok=True)
    out: dict = {"window": None, "cdp": False, "pages": [], "titles": [], "debug_port": bool(port)}
    t0 = time.monotonic()
    with open(shots / "native-host-output.log", "wb") as host_out:
        tree = A.ProcessTree(
            [ns.exe, *host_args(port, ns.vscode_profile, ns.workspace)],
            cwd=Path(ns.workspace or root),
            env=dict(os.environ),
            stdout=host_out,
            stderr=host_out,
        )
        try:
            out["window"] = D.wait_main_window(root, tree, ns.timeout)
            if not out["window"]:
                out["error"] = "no main window"
                return out
            if port and D.non_loopback_reachable(port):
                out["error"] = "debug port reachable on a non-loopback address; aborted"
                return out
            time.sleep(SETTLE_MIN_S)
            if port:
                out["settle_s"] = settle(port, time.monotonic() + SETTLE_MAX_S - SETTLE_MIN_S)
            else:
                time.sleep(SETTLE_MAX_S / 4)  # 没有文字可比：固定再等一会儿再截图
            out["titles"] = sorted({w.get("title", "") for w in P.top_windows(root)} - {""})[:20]
            with contextlib.suppress(Exception):
                P._screenshot(out["window"]["rect"], shots / "native-window.png")
            version = None
            if port:
                with contextlib.suppress(OSError, ValueError):
                    version = D.http_json(port, "/json/version")
            if not version:
                return out  # 宿主不开放调试端口：只有窗口标题与截图
            out["cdp"] = True
            ws_path = "/" + version["webSocketDebuggerUrl"].split("/", 3)[3]
            cdp = D.CDP(D.WebSocket(port, ws_path))
            try:
                pages = [
                    t
                    for t in cdp.call("Target.getTargets").get("targetInfos", [])
                    if t.get("type") == "page"
                ]
                for i, t in enumerate(pages[:6]):
                    sid = cdp.call(
                        "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
                    )["sessionId"]
                    cdp.sessions.setdefault(sid, {"type": "page"})
                    page = {
                        "title": str(t.get("title", ""))[:120],
                        "scheme": str(t.get("url", "")).split(":", 1)[0],
                        "text": (cdp.evaluate(sid, D.TEXT_JS) or "")[:TEXT_CHARS],
                    }
                    with contextlib.suppress(D.CDPError, TimeoutError, OSError):
                        shot = cdp.call(
                            "Page.captureScreenshot", {"format": "png"}, session=sid, timeout=20
                        )
                        D.save_png(shot["data"], shots / f"native-page-{i}.png")
                        page["screenshot"] = f"native-page-{i}.png"
                    out["pages"].append(page)
                    if ns.peek_button and i == 0:
                        out["peek"] = peek(cdp, sid, ns.peek_button, shots)
            finally:
                cdp.ws.close()
            return out
        except Exception as exc:  # noqa: BLE001 — 探查出错如实写进结果
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
            return out
        finally:
            out["seconds"] = round(time.monotonic() - t0, 1)
            tree.close()


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--exe", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--shots", required=True)
    p.add_argument("--vscode-profile")
    p.add_argument("--workspace")
    p.add_argument("--timeout", type=float, default=120)
    #: agent 按宿主登记的唯一一个入口按钮（NATIVE_PEEK）；不给就只看
    p.add_argument("--peek-button")
    ns = p.parse_args(argv)
    res = probe(ns)
    Path(ns.out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
