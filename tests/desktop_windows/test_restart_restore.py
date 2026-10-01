"""#542 第 9 / 12 项：关掉再开、强杀再开之后，排版回来。

#715 修好之前这两条必红：sidecar 绑 `127.0.0.1:0`，每次启动端口都变，而端口是 Web Storage
origin 的一部分——「上次打开的排版」与崩溃兜底副本都只记在 localStorage 里，换了 origin 就读不到。
修复分两半（ADR 0108）：PR-A #718 让端口尽量稳定——同端口 = 同 origin，这两条随之转绿（nightly
36666759203 上是 XPASS(strict)）；PR-B #719 再把「上次开着哪份」以数据目录为准，端口被占、origin 仍变时
也回得来。原先的 `xfail(strict=True)` 标记在 #751 去掉，这两条改为必绿。

失败形状只认「排版没回来」（`LayoutNotRestored`）：前提没摆好（壳起不来、项目没打开、没落盘）
抛的是别的异常，一眼能分开。

对照（同一台机器、同一个 origin 里 `location.reload()`）同样必须绿：它证明判据量得到「排版回来了」。
"""

from __future__ import annotations

import json
import shutil
import time

from _canvas import object_ids, place_figure, wait_on_disk


class LayoutNotRestored(AssertionError):
    """重开之后画布上不是重开之前那几个对象。"""


def _assert_restored(d, before: list[str], how: str, origins: tuple[str, str]) -> None:
    # 重开后排版恢复是异步的（先起项目、再读排版）：给它时间，但最终以对象 id 集合为准
    try:
        d.wait_js(
            "const want = arguments[0]; const got = [...document.querySelectorAll('[data-object-id]')]"
            ".map(e => e.getAttribute('data-object-id')).sort();"
            " return JSON.stringify(got) === JSON.stringify(want)",
            sorted(before),
            timeout=30,
            what="重开前的对象全部回到画布",
        )
    except TimeoutError:
        raise LayoutNotRestored(
            f"{how}之后画布上是 {sorted(object_ids(d))}，重开前是 {sorted(before)}；origin {origins[0]} → {origins[1]}"
        ) from None


def _prepare(desktop_app, project_dir):
    d = desktop_app.launch("--open", str(project_dir))
    oid = place_figure(d, "Fig2_yield.pdf")
    wait_on_disk(desktop_app.data_dir, oid)
    # 前提：「上次开着哪份」记在**这条用例自己的**数据目录里（#719，`TAVOTTO_DATA_DIR` → state/）。
    # 记到全机共享的地方的话，清掉浏览器存储也隔离不了用例，下面「回来了」量的可能是上一条留下的
    session = desktop_app.data_dir / "state" / "layout-sessions.json"
    deadline = time.monotonic() + 30
    while _last_recorded(session) is False and time.monotonic() < deadline:
        time.sleep(0.25)
    assert _last_recorded(session), (
        f"会话状态没落在本用例的数据目录里（或没记上一份排版）：{session}"
    )
    return d, object_ids(d), desktop_app.origin()


def _last_recorded(session) -> bool:
    """会话文件已经在、而且记着一份「上次开着的」排版。"""
    try:
        data = json.loads(session.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    groups = [*data.get("projects", {}).values(), data.get("no_project", {})]
    return any(g.get("last") for g in groups if isinstance(g, dict))


def test_close_then_reopen_restores_the_layout(desktop_app, project_dir):
    _d, before, origin0 = _prepare(desktop_app, project_dir)

    code = desktop_app.close_window()
    assert code == 0, f"WM_CLOSE 之后壳的退出码是 {code}"
    assert desktop_app.leftovers() == [], "关窗之后还有子进程活着（孤儿）"

    # 用户第二天从开始菜单点开：不带任何参数
    d = desktop_app.launch(expect="project")
    _assert_restored(d, before, "关掉再开", (origin0, desktop_app.origin()))


def test_kill_then_reopen_restores_the_layout(desktop_app, project_dir):
    _d, before, origin0 = _prepare(desktop_app, project_dir)

    desktop_app.kill()
    assert desktop_app.leftovers() == [], "壳被强杀之后 sidecar / WebView2 没跟着退（孤儿）"

    d = desktop_app.launch(expect="project")
    _assert_restored(d, before, "强杀再开", (origin0, desktop_app.origin()))


def test_control_same_origin_reload_restores_the_layout(desktop_app, project_dir):
    """对照：origin 不变时，同一套判据是绿的。"""
    d, before, origin0 = _prepare(desktop_app, project_dir)
    d.js("window.__dwBeforeReload = 1; location.reload()")
    d.wait_js("return !window.__dwBeforeReload", what="页面真的重新加载了")
    desktop_app.ready("project")
    assert desktop_app.origin() == origin0
    _assert_restored(d, before, "同源刷新", (origin0, desktop_app.origin()))


def test_opening_another_project_does_not_inherit_the_layout(desktop_app, project_dir, tmp_path):
    """#715 Windows 真机验收 P1（2026-10-01，nightly 36771293696）：关掉项目 F、打开新项目 G，
    G 一打开就是 F 的排版，G 的目录里写出了同一个 doc_id 的时间线节点。

    稳定端口（#718）之后两次启动是同一个 origin、同一份 localStorage；启动恢复在「G 后端没记过」时
    退回全局 `tavotto.currentDoc`（F 最后开着的那份）而不问它属于哪个项目。改之前这条红在第一个断言上。
    """
    _d, before, _origin = _prepare(desktop_app, project_dir)
    slot = wait_on_disk(desktop_app.data_dir, before[0])
    doc_f = slot.stem
    code = desktop_app.close_window()
    assert code == 0, f"WM_CLOSE 之后壳的退出码是 {code}"

    proj_g = tmp_path / "另一个 项目"
    proj_g.mkdir()
    for p in sorted(project_dir.glob("*.pdf")):
        shutil.copy2(p, proj_g / p.name)
    d = desktop_app.launch("--open", str(proj_g))
    # 恢复是异步的（先起项目、问后端、再读盘）：给它与「关掉再开」同样的时间，期间一直不许出现对象
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        leaked = object_ids(d)
        assert not leaked, (
            f"新项目一打开画布上就有对象 {sorted(leaked)}（项目 F 的排版 {doc_f} 漏进了项目 G；"
            f"F 里是 {sorted(before)}）"
        )
        time.sleep(0.5)
    versions = proj_g / "tavottofile" / "versions"
    copies = sorted(p.name for p in versions.glob(f"{doc_f}.*")) if versions.is_dir() else []
    assert copies == [], f"项目 G 的目录里出现了 F 那份排版的副本：{copies}"
    session = json.loads(
        (desktop_app.data_dir / "state" / "layout-sessions.json").read_text(encoding="utf-8")
    )
    lasts = {k: (g.get("last") or {}).get("doc_id") for k, g in session.get("projects", {}).items()}
    assert sum(1 for v in lasts.values() if v == doc_f) <= 1, (
        f"F 的排版 {doc_f} 被记成了不止一个项目的「上次开着的」：{lasts}"
    )
