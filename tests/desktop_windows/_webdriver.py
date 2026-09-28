"""最小的 W3C WebDriver 客户端：经 msedgedriver 的 `debuggerAddress` **附着**到真壳里的 WebView2。

为什么不用 Selenium：多一个依赖就多一种「用例没跑起来」的方式，而我们要的只有十来个端点。
为什么不用 tauri-driver：它没有预编译二进制，而它在 Windows 上做的事本来就是「转给
msedgedriver」——附着模式直接拿到同一条通道，还不需要由驱动来起应用（起应用、关应用、
强杀都必须由用例自己控制，那正是第 9 / 12 项要量的东西）。

输入的主语：`actions` 端点经 CDP `Input.dispatch*Event` 派进**这个 WebView 的渲染进程**，
不经过操作系统的输入队列，前台是谁都打不到别处——这是它与 `SendInput` 的根本区别，
也是它**量不到**的东西：OS 层的加速键表查表、IME 组字、真实的系统拖放（盲点表见
`docs/rules/ci/verification-chain.md`）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"

#: W3C 规范里的特殊键码点
KEY_CONTROL = ""
KEY_SHIFT = ""
KEY_ESCAPE = ""
KEY_TAB = ""
KEY_ENTER = ""


class WebDriverError(RuntimeError):
    pass


class Driver:
    """一个附着会话。`close()` 只断开驱动，**不关应用**（附着模式下 DELETE /session 不杀目标）。"""

    def __init__(self, base: str, debugger_address: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        caps = {
            "capabilities": {
                "alwaysMatch": {"ms:edgeOptions": {"debuggerAddress": debugger_address}}
            }
        }
        value = self._call("POST", "/session", caps, prefix="")
        self.sid = value["sessionId"]

    # -------------------------------------------------------------- 传输

    def _call(self, method: str, path: str, body: Any = None, *, prefix: str | None = None) -> Any:
        pre = f"/session/{self.sid}" if prefix is None else prefix
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + pre + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise WebDriverError(f"{method} {path} → HTTP {e.code}: {detail[:800]}") from None
        value = payload.get("value")
        if isinstance(value, dict) and "error" in value and "message" in value:
            raise WebDriverError(f"{method} {path} → {value['error']}: {value['message'][:800]}")
        return value

    def close(self) -> None:
        try:
            self._call("DELETE", "")
        except Exception:  # noqa: BLE001 - 断开时应用可能已经没了，那正是某些用例要的
            pass

    # -------------------------------------------------------------- 脚本

    def js(self, script: str, *args: Any) -> Any:
        return self._call("POST", "/execute/sync", {"script": script, "args": list(args)})

    def js_async(self, script: str, *args: Any) -> Any:
        """脚本体最后一个参数是回调（W3C execute/async 约定）。"""
        return self._call("POST", "/execute/async", {"script": script, "args": list(args)})

    def wait_js(self, script: str, *args: Any, timeout: float = 30.0, what: str = "") -> Any:
        """轮询到脚本返回真值；超时抛出时带上最后一次的返回值，失败信息说得出人话。"""
        deadline = time.monotonic() + timeout
        last: Any = None
        while True:
            try:
                last = self.js(script, *args)
            except WebDriverError as e:
                last = f"<js 出错: {e}>"
            if last and not (isinstance(last, str) and last.startswith("<js 出错")):
                return last
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"{timeout:.0f}s 内没等到 {what or script[:80]!r}；最后一次返回 {last!r}"
                )
            time.sleep(0.25)

    # -------------------------------------------------------------- 元素

    def find(self, css: str) -> str:
        v = self._call("POST", "/element", {"using": "css selector", "value": css})
        return v[ELEMENT_KEY]

    def find_all(self, css: str) -> list[str]:
        v = self._call("POST", "/elements", {"using": "css selector", "value": css})
        return [e[ELEMENT_KEY] for e in v]

    def click(self, element: str) -> None:
        self._call("POST", f"/element/{element}/click", {})

    def rect(self, element: str) -> dict[str, float]:
        return self._call("GET", f"/element/{element}/rect")

    # -------------------------------------------------------------- 输入（进渲染进程，不进 OS 输入队列）

    def perform(self, *sources: dict) -> None:
        self._call("POST", "/actions", {"actions": list(sources)})
        self._call("DELETE", "/actions")

    def chord(self, *keys: str) -> None:
        """按下再倒序抬起：`chord(KEY_CONTROL, "c")` = Ctrl+C。"""
        down = [{"type": "keyDown", "value": k} for k in keys]
        up = [{"type": "keyUp", "value": k} for k in reversed(keys)]
        self.perform({"type": "key", "id": "kbd", "actions": down + up})

    def drag(
        self, x0: float, y0: float, x1: float, y1: float, *, steps: int = 12, ms: int = 400
    ) -> None:
        """视口坐标上的一次真实指针拖动（pointerdown → 多次 move → pointerup）。"""
        moves = [
            {
                "type": "pointerMove",
                "duration": max(1, ms // steps),
                "origin": "viewport",
                "x": round(x0 + (x1 - x0) * i / steps),
                "y": round(y0 + (y1 - y0) * i / steps),
            }
            for i in range(1, steps + 1)
        ]
        self.perform(
            {
                "type": "pointer",
                "id": "mouse",
                "parameters": {"pointerType": "mouse"},
                "actions": [
                    {
                        "type": "pointerMove",
                        "duration": 0,
                        "origin": "viewport",
                        "x": round(x0),
                        "y": round(y0),
                    },
                    {"type": "pointerDown", "button": 0},
                    {"type": "pause", "duration": 80},
                    *moves,
                    {"type": "pause", "duration": 80},
                    {"type": "pointerUp", "button": 0},
                ],
            }
        )

    def click_at(self, x: float, y: float) -> None:
        self.perform(
            {
                "type": "pointer",
                "id": "mouse",
                "parameters": {"pointerType": "mouse"},
                "actions": [
                    {
                        "type": "pointerMove",
                        "duration": 0,
                        "origin": "viewport",
                        "x": round(x),
                        "y": round(y),
                    },
                    {"type": "pointerDown", "button": 0},
                    {"type": "pointerUp", "button": 0},
                ],
            }
        )
