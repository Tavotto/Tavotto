"""在装好的 Tavotto 桌面版里真点「接入 AI 编辑器」那一行的按钮（交互会话一侧；规则 docs/rules/ci/windows-lab.md「AI 宿主分层」）。

    python connect_driver.py --exe <Tavotto.exe> --root <安装目录> --open <给 Tavotto 打开的项目> --host <hostconnect 的宿主 id>
        --action connect|disconnect --folder <要授权给宿主的项目文件夹> --out-dir <证据目录> --label <名字>

由 `gui_host.py` 在 tavotto-lab 的交互会话里拉起（`agent.run_in_gui`），环境（宿主的用户目录、Tavotto 的数据 / 配置目录）
由 agent 定好。只做界面上的事，**不下结论**（配置文件写对没有、宿主能不能用，由 agent 读文件、跑宿主来判）：

1. 起 `Tavotto.exe --open <项目>`，等它的主窗口；WebView2 的调试端口由 HKLM 策略常开在 127.0.0.1:9222
   （与 desktop-window 同一条），连上 sidecar 源上的那个页面；
2. 真实鼠标事件依次点：左栏「设置」→「AI」分区 → 这个宿主那一行的「接入」（connect）或「⋯」→「断开」（disconnect）；
3. connect 时第一次接入会弹系统的「选择文件夹」框：把 `--folder` 写进去、按「选择文件夹」（与 WorkBuddy 选工作区同一个
   `fill_folder_dialog`）；
4. 等那一行落定（已接入 / 失败提示 / 接入按钮回来），存证（截图 + 这一组的文字），按 Job 结束整棵进程树。

每一步先写进 `<label>-progress.jsonl` 再做；结果写 `<label>.json`。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent as A  # noqa: E402 — 同目录部署：Job Object 的唯一出处
import desktop_driver as D  # noqa: E402 — CDP 客户端、选文件夹框、截图的唯一出处
import window_probe as P  # noqa: E402 — 窗口截图

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

#: 页面「起来了」并落在项目编辑器上（与 tests/desktop_windows/_app.py 的 PAGE_READY / PROJECT_OPEN 同一判据）
PAGE_READY_JS = (
    "location.protocol === 'http:' && document.readyState === 'complete'"
    " && !!document.querySelector('[data-project-switcher]')"
)
#: 「⋯」菜单里的「断开」：菜单项没有 data 属性，按 role + 两种界面语言的文字认（zh-CN / en-US 的 settings.hosts.disconnect）
DISCONNECT_LABELS = ("断开", "Disconnect")


def row_sel(host: str) -> str:
    return f'[data-host-row="{host}"]'


def connect_sel(host: str) -> str:
    return f'[data-host-connect="{host}"]'


#: 这一行此刻的样子：connected = 有「✓ 已接入」；button = 「接入」按钮（及它是否可点、上面的字）；text = 整行文字
ROW_STATE_JS = """(() => {
  const row = document.querySelector(%(row)s);
  if (!row) return {present: false};
  const btn = row.querySelector(%(btn)s);
  return {
    present: true,
    missing: !!row.querySelector('[data-host-missing]'),
    connected: !!row.querySelector('[data-host-connected]'),
    more: !!row.querySelector('[data-host-more]'),
    button: btn ? {text: btn.innerText.trim(), disabled: btn.disabled || btn.getAttribute('aria-busy') === 'true'} : null,
    text: row.innerText.slice(0, 2000),
  };
})()"""

#: 元素滚进视野后的中心坐标（真实鼠标事件要坐标）；找不到 / 不可见返回 null
RECT_JS = """(() => {
  const el = %(find)s;
  if (!el || el.offsetParent === null) return null;
  el.scrollIntoView({block: 'center', inline: 'center'});
  const b = el.getBoundingClientRect();
  if (!b.width || !b.height) return null;
  return {x: b.x + b.width / 2, y: b.y + b.height / 2};
})()"""


def _q(sel: str) -> str:
    return f"document.querySelector({json.dumps(sel)})"


def _menuitem(labels: tuple[str, ...]) -> str:
    return (
        "Array.from(document.querySelectorAll('[role=\"menuitem\"]'))"
        f".find(e => {json.dumps(list(labels))}.includes(e.innerText.trim()))"
    )


def row_state(cdp: D.CDP, sid: str, host: str) -> dict:
    v = cdp.evaluate(
        sid,
        ROW_STATE_JS % {"row": json.dumps(row_sel(host)), "btn": json.dumps(connect_sel(host))},
    )
    return v if isinstance(v, dict) else {"present": False}


#: connect：按钮回来可点之后，界面还要再跑一次 `hosts list`（冻结 CLI 冷启动几秒）才会换成「已接入」。按钮可点却一直
#: 没换过来这么久，才当成失败落定（Lab run 20261028T111218Z-773916：一可点就判，抢在刷新之前）
CONNECT_REFRESH_GRACE_S = 90.0


def settled(state: dict, action: str, enabled_for: float = 0.0) -> bool:
    """这一行落定了没有：connect → 已接入，或按钮可点且已过了列表刷新的宽限（失败提示 / 重试）；
    disconnect → 接入按钮回来。`enabled_for` = 按钮连续可点了多少秒。"""
    if not state.get("present"):
        return False
    btn = state.get("button")
    enabled = bool(btn and not btn.get("disabled"))
    if action == "connect":
        return bool(state.get("connected")) or (enabled and enabled_for >= CONNECT_REFRESH_GRACE_S)
    return enabled and not state.get("connected")


def click(cdp: D.CDP, sid: str, find_js: str) -> bool:
    """真实鼠标：移过去、按下、松开（React 的 onClick 走的是浏览器自己的事件路径，不是 el.click()）。"""
    pt = cdp.evaluate(sid, RECT_JS % {"find": find_js})
    if not isinstance(pt, dict):
        return False
    for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
        params = {"type": typ, "x": pt["x"], "y": pt["y"]}
        if typ != "mouseMoved":
            params.update(button="left", clickCount=1)
        cdp.call("Input.dispatchMouseEvent", params, session=sid, timeout=10)
    return True


def wait_js(cdp: D.CDP, sid: str, expr: str, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cdp.evaluate(sid, f"!!({expr})") is True:
            return True
        time.sleep(0.5)
    return False


def page_session(cdp: D.CDP, timeout: float) -> str | None:
    """sidecar 的 http 源上的那个页面（不是 tauri:// 的启动页）：附着进去，返回 sessionId。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with contextlib.suppress(D.CDPError, TimeoutError):
            for t in cdp.call("Target.getTargets").get("targetInfos", []):
                if t.get("type") == "page" and str(t.get("url", "")).startswith("http://127.0.0.1"):
                    return cdp.call(
                        "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
                    )["sessionId"]
        time.sleep(1)
    return None


def operate(cdp: D.CDP, sid: str, ns: argparse.Namespace, out_dir: Path, prog) -> dict:
    res: dict = {}
    if not wait_js(cdp, sid, PAGE_READY_JS, ns.page_timeout):
        return {"error": "project editor never appeared"}
    prog.step("page_ready")
    if not click(cdp, sid, _q('[data-rail="settings"]')):
        return {"error": "no settings button in the left rail"}
    if not wait_js(cdp, sid, _q("[data-settings-shell]"), 30):
        return {"error": "settings dialog did not open"}
    if not click(cdp, sid, _q('[data-section="ai"]')):
        return {"error": "no AI section in settings"}
    # 这一组的每一行来自 `tavotto-cli hosts list --json`（冻结 CLI 冷启动要几秒）
    if not wait_js(cdp, sid, _q(row_sel(ns.host)), ns.list_timeout):
        res["section_text"] = cdp.evaluate(
            sid, "(document.querySelector('[data-hosts-section]')||{}).innerText||''"
        )
        return {**res, "error": f"no row for {ns.host} in 「接入 AI 编辑器」"}
    res["before"] = row_state(cdp, sid, ns.host)
    prog.step("row_before", state=res["before"])
    D._page_shot(cdp, sid, out_dir / f"{ns.label}-before.png")
    if ns.action == "connect":
        if not res["before"].get("button"):
            return {**res, "error": "no 「接入」 button on the row"}
        if not click(cdp, sid, _q(connect_sel(ns.host))):
            return {**res, "error": "could not click 「接入」"}
    else:
        if not click(cdp, sid, _q(f"{row_sel(ns.host)} [data-host-more]")):
            return {**res, "error": "no ⋯ button on the row (not shown as connected?)"}
        if not wait_js(cdp, sid, _menuitem(DISCONNECT_LABELS), 10):
            return {**res, "error": "no 「断开」 item in the ⋯ menu"}
        time.sleep(
            0.8
        )  # 菜单弹出有动画：位置稳定了再点（Lab run 20261028T111218Z-773916 点了没反应）
        if not click(cdp, sid, _menuitem(DISCONNECT_LABELS)):
            return {**res, "error": "could not click 「断开」"}
        time.sleep(1)
        if cdp.evaluate(sid, f"!!({_menuitem(DISCONNECT_LABELS)})") is True:
            # 菜单还开着 = 鼠标没点中：聚焦那一项、按真实的回车（Radix 菜单项认回车）
            cdp.evaluate(sid, f"({_menuitem(DISCONNECT_LABELS)}).focus()")
            for typ in ("keyDown", "keyUp"):
                D._key(cdp, sid, typ, "Enter", "Enter", 13)
            res["disconnect_via"] = "enter"
    prog.step("clicked", action=ns.action)
    # 第一次接入：按钮先弹系统的选文件夹框（Tauri 的 dialog 插件）；它在不在都要看一眼——已记住文件夹时不弹
    if ns.action == "connect":
        diag: dict = {}
        res["folder_dialog"] = D.fill_folder_dialog(ns.folder, timeout=8, diag=diag)
        res["folder_dialog_diag"] = diag
        prog.step("folder_dialog", outcome=res["folder_dialog"], title=diag.get("title"))
    end = time.monotonic() + ns.settle_timeout
    state = row_state(cdp, sid, ns.host)
    time.sleep(1)  # 点下去到按钮变「正在接入…」之间有一帧：别把点之前的样子当成落定
    enabled_since = None
    while time.monotonic() < end:
        state = row_state(cdp, sid, ns.host)
        btn = state.get("button")
        if btn and not btn.get("disabled"):
            enabled_since = enabled_since or time.monotonic()
        else:
            enabled_since = None
        held = time.monotonic() - enabled_since if enabled_since else 0.0
        if settled(state, ns.action, held):
            break
        time.sleep(1)
    else:
        res["timed_out"] = True
    res["after"] = state
    prog.step("row_after", state=state)
    # 逐步结论折叠在「步骤」里：展开后连文字一起存（失败时看是哪一步）
    with contextlib.suppress(D.CDPError, TimeoutError):
        click(cdp, sid, _q(f"{row_sel(ns.host)} [data-host-steps] button"))
        time.sleep(0.5)
    res["section_text"] = cdp.evaluate(
        sid, "(document.querySelector('[data-hosts-section]')||{}).innerText||''"
    )
    D._page_shot(cdp, sid, out_dir / f"{ns.label}-after.png")
    return res


def debug_endpoint_up(port: int) -> bool:
    try:
        D.http_json(port, "/json/version", timeout=2)
    except (OSError, ValueError):
        return False
    return True


def drive(ns: argparse.Namespace) -> dict:
    out_dir = Path(ns.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prog = D.Progress(out_dir / f"{ns.label}-progress.jsonl")
    result: dict = {"label": ns.label, "action": ns.action, "host": ns.host, "window": None}
    if debug_endpoint_up(ns.port):
        # 别的 Tavotto 已经占着调试端口：单实例会把这次启动转交给它，附着上去的就不是这次起的窗口
        return {
            **result,
            "error": f"127.0.0.1:{ns.port} already has a debug endpoint before launch",
        }
    root = Path(ns.root)
    with open(out_dir / f"{ns.label}-tavotto.log", "wb") as log:
        prog.step("launch", exe=ns.exe)
        tree = A.ProcessTree(
            [ns.exe, "--open", ns.open],
            cwd=Path(ns.open),
            env=dict(os.environ),
            stdout=log,
            stderr=log,
        )
        try:
            result["window"] = D.wait_main_window(root, tree, ns.window_timeout)
            prog.step("window", found=bool(result["window"]))
            if not result["window"]:
                return {**result, "error": "Tavotto main window never appeared"}
            cdp = D.connect_cdp(ns.port, ns.page_timeout)
            if cdp is None:
                # 证据文件不回传 Mac：窗口截图（winlab shot 取）与壳的输出尾部进结果
                P._screenshot(result["window"]["rect"], out_dir / f"{ns.label}-window.png")
                log.flush()
                tail = (out_dir / f"{ns.label}-tavotto.log").read_bytes()[-1500:]
                return {
                    **result,
                    "error": f"no WebView2 debug endpoint on {ns.port} (HKLM policy?)",
                    "shell_log_tail": tail.decode("utf-8", "replace"),
                    "shell_alive": tree.proc.poll() is None,
                }
            if D.non_loopback_reachable(ns.port):
                return {
                    **result,
                    "error": "debug port reachable on a non-loopback address; aborted",
                }
            sid = page_session(cdp, ns.page_timeout)
            if sid is None:
                return {**result, "error": "no page on the sidecar origin"}
            result.update(operate(cdp, sid, ns, out_dir, prog))
        finally:
            tree.close()  # Job 结束整棵进程树（壳、sidecar、WebView2、它拉起的 tavotto-cli）
    return result


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--exe", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--open", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--action", choices=("connect", "disconnect"), required=True)
    p.add_argument("--folder", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--port", type=int, default=A.DEBUG_PORT)
    p.add_argument("--window-timeout", type=float, default=180)
    p.add_argument("--page-timeout", type=float, default=180)
    p.add_argument("--list-timeout", type=float, default=120)
    #: 接入要跑一次服务端握手（health：冻结 CLI 冷启动 + 列工具），慢的时候一分多钟
    p.add_argument("--settle-timeout", type=float, default=300)
    ns = p.parse_args(argv)
    try:
        res = drive(ns)
    except Exception as exc:  # noqa: BLE001 — 启动阶段就崩也要留下结果
        res = {"label": ns.label, "window": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
        D.Progress(Path(ns.out_dir) / f"{ns.label}-progress.jsonl").step(
            "fatal", error=res["error"]
        )
    out = Path(ns.out_dir) / f"{ns.label}.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
