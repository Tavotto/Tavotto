"""桌面宿主 L3–L5 的驱动器（交互会话一侧；规则全文 docs/rules/ci/windows-lab.md「AI 宿主分层」）。

    python desktop_driver.py --exe <Code.exe> --root <安装目录> --user-data-dir … --extensions-dir …
        --workspace <项目目录> --prompt-file <提示词.txt> --rec-log <mcp-recorder.jsonl> --out-dir <证据目录> --label round1

由 `gui_host.py` 在 tavotto-lab 的交互会话里拉起（`agent.run_in_gui`，模型密钥经命名管道进环境，不在 argv 里）。
只做四件事，**不下任何结论**（判定在 agent.py 的 `judge_desktop_live`，只看录制器记下的 Tavotto 一侧事实）：

1. 起宿主：`--remote-debugging-port=<随机端口>` 只绑回环（不传 `--remote-debugging-address`，并实测非回环地址连不上），
   等主窗口（与 `window_probe` 同一判据）；
2. 经 CDP（`Target.setAutoAttach` flatten 进 webview 的 iframe）打开扩展面板，找到输入框；
3. 把提示词填进输入框并点发送；
4. 等录制器安静（首个流量之后 `--quiet-s` 秒没有新行，或超过 `--max-s`），存证（截图 + 面板文字），
   然后按 Job 结束整棵进程树，端口随进程一起关掉。

CDP 客户端与 WebSocket 都是标准库实现（venv 里不加依赖）。每一步的结果如实写进 `<label>.json`。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent as A  # noqa: E402 — 同目录部署：Job Object 的唯一出处
import window_probe as P  # noqa: E402 — 主窗口判据的唯一出处

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

LOOPBACK = "127.0.0.1"
STABLE_S = 3.0
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
#: 扩展面板里「消息输入框」的选择器（固定版本的扩展 webview：contentEditable 的 role=textbox）
INPUT_LABEL = "Message input"
SEND_LABEL = "Send message"
OPEN_URI = "vscode://anthropic.claude-code/open"
PALETTE_COMMAND = "Claude Code: Open in New Tab"


# ----------------------------------------------------------------------------- 纯函数（测试直接调）
def free_port() -> int:
    """向系统要一个空闲回环端口（每次 run 都不同）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


def code_args(user_data_dir: str, extensions_dir: str, workspace: str, port: int) -> list[str]:
    """VS Code 的启动参数。调试端口只给 `--remote-debugging-port`：不传 `--remote-debugging-address`，
    Chromium 默认只绑 127.0.0.1；不带任何密钥（模型凭据只在进程环境里）。"""
    return [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        f"--extensions-dir={extensions_dir}",
        "--skip-welcome",
        "--skip-release-notes",
        "--disable-workspace-trust",
        "--disable-updates",
        "--new-window",
        workspace,
    ]


def uri_open_args(user_data_dir: str, extensions_dir: str, uri: str = OPEN_URI) -> list[str]:
    """把 URI 交给已在运行的同一实例（同一个 user-data-dir）：扩展的 URI 处理器打开面板。"""
    return [
        f"--user-data-dir={user_data_dir}",
        f"--extensions-dir={extensions_dir}",
        "--open-url",
        uri,
    ]


def ws_accept(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = 0x1, mask_key: bytes | None = None) -> bytes:
    """客户端 → 服务器的 WebSocket 帧（RFC 6455：客户端必须掩码）。"""
    mask_key = mask_key or os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return head + mask_key + masked


def decode_frame(buf: bytes) -> tuple[int, bool, bytes, int] | None:
    """服务器 → 客户端的一帧（不掩码）：(opcode, fin, payload, 消耗的字节数)；数据不够返回 None。"""
    if len(buf) < 2:
        return None
    fin, opcode = bool(buf[0] & 0x80), buf[0] & 0x0F
    n, pos = buf[1] & 0x7F, 2
    if n == 126:
        if len(buf) < 4:
            return None
        n, pos = struct.unpack(">H", buf[2:4])[0], 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n, pos = struct.unpack(">Q", buf[2:10])[0], 10
    if len(buf) < pos + n:
        return None
    return opcode, fin, buf[pos : pos + n], pos + n


class WebSocket:
    def __init__(self, port: int, path: str):
        self.sock = socket.create_connection((LOOPBACK, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {LOOPBACK}:{port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("websocket handshake: connection closed")
            data += chunk
        head, _, self.buf = data.partition(b"\r\n\r\n")
        text = head.decode("latin-1")
        if " 101 " not in text.split("\r\n", 1)[0] or ws_accept(key).lower() not in text.lower():
            raise OSError("websocket handshake refused")
        self._partial = b""

    def send_text(self, text: str) -> None:
        self.sock.sendall(encode_frame(text.encode("utf-8")))

    def recv_text(self, timeout: float) -> str | None:
        """下一条文本消息；超时返回 None（已读到的半帧留在缓冲里，下次接着读）。"""
        deadline = time.monotonic() + timeout
        while True:
            got = decode_frame(self.buf)
            if got is not None:
                opcode, fin, payload, used = got
                self.buf = self.buf[used:]
                if opcode == 0x8:
                    raise OSError("websocket closed by the host")
                if opcode == 0x9:
                    self.sock.sendall(encode_frame(payload, 0xA))
                    continue
                if opcode in (0x1, 0x0):
                    self._partial += payload
                    if fin:
                        text, self._partial = self._partial.decode("utf-8", "replace"), b""
                        return text
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65536)
            except (TimeoutError, socket.timeout):
                return None
            if not chunk:
                raise OSError("websocket closed by the host")
            self.buf += chunk

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


class CDPError(Exception):
    pass


class CDP:
    """单线程同步的 CDP 客户端：flatten 模式下所有会话共用一条 WebSocket，靠 sessionId 区分。"""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self._id = 0
        #: sessionId → targetInfo（Target.attachedToTarget 事件）
        self.sessions: dict[str, dict] = {}
        self._pending_attach: list[str] = []

    def _event(self, msg: dict) -> None:
        m = msg.get("method")
        params = msg.get("params") or {}
        if m == "Target.attachedToTarget":
            sid = params.get("sessionId")
            self.sessions[sid] = params.get("targetInfo") or {}
            self._pending_attach.append(sid)
        elif m == "Target.detachedFromTarget":
            self.sessions.pop(params.get("sessionId"), None)

    def call(
        self,
        method: str,
        params: dict | None = None,
        session: str | None = None,
        timeout: float = 30,
    ) -> dict:
        self._id += 1
        mid = self._id
        msg: dict = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send_text(json.dumps(msg))
        deadline = time.monotonic() + timeout
        while True:
            text = self.ws.recv_text(max(deadline - time.monotonic(), 0.01))
            if text is None:
                raise TimeoutError(f"CDP {method} timed out")
            reply = json.loads(text)
            if reply.get("id") == mid:
                if "error" in reply:
                    raise CDPError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result") or {}
            self._event(reply)

    def pump(self, seconds: float) -> None:
        """读一会儿事件；顺手让新出现的子目标（webview 的 iframe）也开启自动附加。"""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            text = self.ws.recv_text(min(0.25, max(end - time.monotonic(), 0.01)))
            if text is not None:
                self._event(json.loads(text))
            while self._pending_attach:
                sid = self._pending_attach.pop(0)
                with contextlib.suppress(CDPError, TimeoutError):
                    self.call(
                        "Target.setAutoAttach",
                        {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
                        session=sid,
                        timeout=10,
                    )

    def evaluate(self, session: str, expression: str):
        try:
            r = self.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                session=session,
                timeout=20,
            )
        except (CDPError, TimeoutError):
            return None
        return (r.get("result") or {}).get("value")


# ----------------------------------------------------------------------------- 页面里跑的脚本
#: 在当前文档与全部同源 iframe 里找消息输入框（webview 的内容在 iframe 里，外层与内层同源）
INPUT_SELECTOR = '[role="textbox"][aria-label="%s"]' % INPUT_LABEL
#: 原生 Agent（ZCode）：宿主自己的聊天输入框，没有扩展钉死的 aria-label——认第一个可见的 textarea / 可编辑区
NATIVE_INPUT_SELECTOR = 'textarea, [contenteditable="true"], [role="textbox"]'

_WALK = """
function walk(doc) {
  const el = Array.from(doc.querySelectorAll(%(input)s)).find(e => e.offsetParent !== null);
  if (el) return {doc: doc, el: el};
  for (const f of doc.querySelectorAll('iframe')) {
    try { const d = f.contentDocument; if (d) { const r = walk(d); if (r) return r; } } catch (e) {}
  }
  return null;
}
""" % {"input": json.dumps(INPUT_SELECTOR)}

FIND_JS = "(() => {" + _WALK + "const r = walk(document); return {found: !!r};})()"

#: 填字：聚焦输入框，用 execCommand('insertText') 走浏览器自己的输入路径（触发 input 事件，React 才看得到）
TYPE_JS = (
    "(() => {"
    + _WALK
    + "const r = walk(document); if (!r) return {typed: false, why: 'no input'};"
    + "r.el.focus(); r.doc.execCommand('selectAll'); r.doc.execCommand('delete');"
    + "const ok = r.doc.execCommand('insertText', false, %s);"
    + "return {typed: !!ok, length: (r.el.value ?? r.el.innerText ?? '').length};})()"
)

#: 点发送；没有按钮时退回在输入框上派发 Enter
SUBMIT_JS = (
    "(() => {"
    + _WALK
    + "const r = walk(document); if (!r) return {submitted: false, why: 'no input'};"
    + "const b = r.doc.querySelector('button[aria-label=\"%(send)s\"]');"
    + "if (b && !b.disabled) { b.click(); return {submitted: true, via: 'button'}; }"
    + "r.el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true}));"
    + "return {submitted: true, via: 'enter'};})()"
) % {"send": SEND_LABEL}

#: 「在忙」= 有停止按钮（扩展是 aria-label="Stop"；原生宿主按 aria-label / title 含 Stop / 停止 认）
BUSY_SELECTOR = 'button[aria-label="Stop"], button[aria-label*="Stop"], button[title*="Stop"], button[aria-label*="停止"], button[title*="停止"]'

#: 提交后输入框是否已清空（发出去了）、是否在忙（有 Stop 按钮）
STATE_JS = (
    "(() => {"
    + _WALK
    + "const r = walk(document); if (!r) return null;"
    + "return {input_length: (r.el.value ?? r.el.innerText ?? '').trim().length,"
    + "busy: !!r.doc.querySelector(%(busy)s)};})()"
) % {"busy": json.dumps(BUSY_SELECTOR)}


def use_input_selector(selector: str) -> None:
    """换输入框选择器（原生宿主）：重建依赖它的几段 JS。只在进程启动时调一次。"""
    global _WALK, FIND_JS, TYPE_JS, SUBMIT_JS, STATE_JS
    old = json.dumps(INPUT_SELECTOR)
    new = json.dumps(selector)
    _WALK = _WALK.replace(old, new)
    FIND_JS, TYPE_JS, SUBMIT_JS = (x.replace(old, new) for x in (FIND_JS, TYPE_JS, SUBMIT_JS))
    STATE_JS = STATE_JS.replace(old, new)


#: 宿主在等人点权限确认框（ZCode：「Permission required / Awaiting approval」；中文界面「需要授权 / 等待批准」）
PERMISSION_PROMPT_RE = re.compile(r"Awaiting approval|Permission required|等待批准|需要授权")

#: 面板文字（登录页、错误提示都在里面），每个文档最多 8000 字
TEXT_JS = (
    "(() => {const out = [];"
    "function dump(doc) { try { const t = (doc.body && doc.body.innerText) || ''; if (t.trim()) out.push(t.slice(0, 8000));"
    " for (const f of doc.querySelectorAll('iframe')) { try { if (f.contentDocument) dump(f.contentDocument); } catch (e) {} } } catch (e) {} }"
    "dump(document); return out.join('\\n-----\\n');})()"
)


# ----------------------------------------------------------------------------- 流程
class Progress:
    """每一步先写盘再做：`<label>-progress.jsonl` 一行一步（时间戳、步骤名、细节、异常），崩在哪一步一目了然。"""

    def __init__(self, path: Path):
        self.path = path
        self.t0 = time.monotonic()

    def step(self, name: str, **detail) -> None:
        rec = {"t": round(time.monotonic() - self.t0, 1), "step": name, **detail}
        with contextlib.suppress(OSError), open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def http_json(port: int, path: str, timeout: float = 5):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 回环永不进代理
    with opener.open(f"http://{LOOPBACK}:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_main_window(root: Path, tree, timeout: float) -> dict | None:
    t0 = time.monotonic()
    window, since = None, None
    while time.monotonic() - t0 < timeout:
        if tree.proc.poll() is not None and not P.top_windows(root):
            return None
        wins = P.top_windows(root)
        if wins:
            best = max(
                wins, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1])
            )
            if window is None or best["hwnd"] != window["hwnd"]:
                window, since = best, time.monotonic()
            elif time.monotonic() - since >= STABLE_S:
                return dict(window, seconds=round(time.monotonic() - t0, 1))
        else:
            window, since = None, None
        time.sleep(0.5)
    return None


def non_loopback_reachable(port: int) -> bool:
    """调试端口从本机的非回环地址连得上就是暴露了：实测一次。"""
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            try:
                socket.create_connection((ip, port), timeout=2).close()
                return True
            except OSError:
                continue
    return False


def find_input_session(cdp: CDP) -> str | None:
    for sid in list(cdp.sessions):
        v = cdp.evaluate(sid, FIND_JS)
        if isinstance(v, dict) and v.get("found"):
            return sid
    return None


def connect_cdp(port: int, timeout: float) -> CDP | None:
    """连上浏览器端的 CDP（开启目标发现）；`timeout` 秒内连不上返回 None。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with contextlib.suppress(OSError, ValueError, CDPError, TimeoutError):
            version = http_json(port, "/json/version")
            cdp = CDP(WebSocket(port, "/" + version["webSocketDebuggerUrl"].split("/", 3)[3]))
            cdp.call("Target.setDiscoverTargets", {"discover": True})
            return cdp
        time.sleep(1)
    return None


def is_workbench_url(url: str) -> bool:
    """VS Code 系主窗口的工作台页面：VS Code / Cursor 是 `vscode-file://…/workbench.html`；
    别的分支可能改了文件名，但都走 `vscode-file:` 协议。"""
    if "setup" in url.rsplit("/", 1)[-1]:
        return False  # TRAE 首启的欢迎页 setup.html 不是工作台（run 20261026T142555Z-89dd46）
    return "workbench" in url or url.startswith("vscode-file:")


#: 首启引导页上**只许点**的按钮（逐字匹配，去掉首尾空白）：开始 / 跳过 / 下一步 / 继续 / 稍后。
#: 不点任何登录、注册、授权、同意条款之外的东西；点了哪些如实进 progress。
ONBOARDING_BUTTONS = (
    "开始",
    "跳过",
    "下一步",
    "继续",
    "稍后",
    "以后再说",
    "Start",
    "Skip",
    "Next",
    "Continue",
    "Later",
)
#: 原生 Agent（ZCode）首启：「Use API key」→「Skip for now」→ 问卷「Skip」。不含「Continue」：API Key 表单上的
#: Continue 会提交（空的）key；不含任何 Connect / 登录（run 20261026T152056Z-fb05c7 的下一屏）
NATIVE_ONBOARDING_BUTTONS = ("Use API key", "Skip for now", "Skip", "跳过", "稍后", "Later")
#: 先找 button / role=button / a；TRAE 的「跳过」是普通元素（run 20261026T144322Z-291b6a），再找文字恰好等于白名单、
#: 没有子元素的叶子节点，点它最近的可点祖先（没有就点它自己）
ONBOARDING_CLICK_JS = (
    "(() => {const want = %s; const vis = e => e.offsetParent !== null;"
    "for (const b of document.querySelectorAll('button, [role=button], a')) {"
    " const t = (b.innerText || '').trim(); if (want.includes(t) && vis(b)) { b.click(); return t; } }"
    "for (const e of document.querySelectorAll('body *')) {"
    " if (e.children.length) continue; const t = (e.textContent || '').trim();"
    " if (want.includes(t) && vis(e)) { (e.closest('button,[role=button],a,[onclick],[tabindex]') || e).click(); return t; } }"
    "return null;})()"
)


def _url_shape(url: str) -> str:
    """诊断用：协议 + 最后一段文件名（不记完整路径与查询串）。"""
    scheme, _, rest = url.partition(":")
    return f"{scheme}:{rest.split('?', 1)[0].rsplit('/', 1)[-1][:40]}"


def native_ready(cdp: CDP, page: str, timeout: float, onboarding: list, prog) -> str | None:
    """原生 Agent（ZCode）：主页面就是聊天界面。首启引导（「Use API key」之后的「Skip for now」、问卷的「Skip」）
    只点 NATIVE_ONBOARDING_BUTTONS 白名单，直到出现可见的输入框。返回输入框所在会话。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        cdp.pump(1.0)
        sid = find_input_session(cdp)
        if sid:
            return sid
        clicked = cdp.evaluate(
            page, ONBOARDING_CLICK_JS % json.dumps(list(NATIVE_ONBOARDING_BUTTONS))
        )
        if clicked and len(onboarding) < 12:
            onboarding.append(clicked)
            prog.step("onboarding_click", button=clicked)
        time.sleep(1.5)
    return None


def open_panel(
    cdp: CDP, workbench: str, exe: str, args: list[str], timeout: float
) -> tuple[str | None, str | None]:
    """先走扩展的 URI 入口，不行再走命令面板（键盘事件经 CDP）。返回 (输入框所在会话, 用了哪条路)。
    `args` 为 None = 只走命令面板（Cursor 打开扩展 URI 前会弹模态确认框，弹出来就挡住命令面板）。"""
    p = (
        subprocess.Popen(
            [exe, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ)
        )
        if args is not None
        else None
    )
    try:
        end = time.monotonic() + (timeout if p is not None else 0)
        while time.monotonic() < end:
            cdp.pump(1.0)
            sid = find_input_session(cdp)
            if sid:
                return sid, "uri"
        # 命令面板最多试三次、每次前先 Esc：工作台刚出来时扩展可能还没激活，命令还不在列表里
        # （TRAE：run 20261026T145631Z-d4aaf2 一次成功，20261026T150227Z-682f81 同样流程一次没打开）
        for _attempt in range(3):
            with contextlib.suppress(CDPError, TimeoutError):
                _key(cdp, workbench, "keyDown", "Escape", "Escape", 27)
                _key(cdp, workbench, "keyUp", "Escape", "Escape", 27)
            _palette(cdp, workbench)
            end = time.monotonic() + timeout / 3
            while time.monotonic() < end:
                cdp.pump(1.0)
                sid = find_input_session(cdp)
                if sid:
                    return sid, "palette"
        return None, None
    finally:
        with contextlib.suppress(OSError):
            if p is not None and p.poll() is None:
                p.kill()  # 只结束自己起的这一个转发进程


def _key(
    cdp: CDP,
    session: str,
    typ: str,
    key: str,
    code: str,
    vk: int,
    modifiers: int = 0,
    text: str | None = None,
) -> None:
    params = {
        "type": typ,
        "key": key,
        "code": code,
        "windowsVirtualKeyCode": vk,
        "modifiers": modifiers,
    }
    if text is not None:
        params["text"] = text
    cdp.call("Input.dispatchKeyEvent", params, session=session, timeout=10)


def _palette(cdp: CDP, session: str) -> None:
    with contextlib.suppress(CDPError, TimeoutError):
        cdp.call("Page.bringToFront", session=session, timeout=10)
        _key(cdp, session, "keyDown", "P", "KeyP", 80, modifiers=2 | 8)  # Ctrl+Shift+P
        _key(cdp, session, "keyUp", "P", "KeyP", 80, modifiers=2 | 8)
        time.sleep(1.0)
        cdp.call("Input.insertText", {"text": PALETTE_COMMAND}, session=session, timeout=10)
        time.sleep(1.5)
        _key(cdp, session, "keyDown", "Enter", "Enter", 13, text="\r")
        _key(cdp, session, "keyUp", "Enter", "Enter", 13)


def recorder_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


def wait_quiet(
    rec_log: Path,
    base_lines: int,
    quiet_s: float,
    max_s: float,
    first_s: float,
    alive,
    busy=lambda: False,
    blocked=lambda: False,
) -> tuple[str, int]:
    """首个流量之后 quiet_s 秒没有新行、且面板不在忙（没有 Stop 按钮）→ quiet；一直没有流量 →
    first_traffic_timeout；超过 max_s → max；宿主进程没了 → host_exited。返回 (原因, 最终行数)。
    只看录制器会在模型想得久的两次工具调用之间提前收场（run 20261026T130906Z-feb0dd 第一轮停在 busy）。"""
    t0 = time.monotonic()
    last_n, last_change = recorder_lines(rec_log), time.monotonic()
    seen = last_n > base_lines
    while True:
        time.sleep(2)
        n = recorder_lines(rec_log)
        now = time.monotonic()
        if n != last_n:
            last_n, last_change = n, now
            seen = seen or n > base_lines
        if not alive():
            return "host_exited", n
        if blocked():
            return "permission_prompt", n  # 面板在等人点权限框：没人会点，如实收场，不干等到上限
        if now - t0 > max_s:
            return "max", n
        if seen and now - last_change >= quiet_s and not busy():
            return "quiet", n
        if not seen and now - t0 > first_s:
            return "first_traffic_timeout", n


def save_png(b64: str, path: Path) -> None:
    path.write_bytes(base64.b64decode(b64))


def drive(ns: argparse.Namespace) -> dict:
    out_dir = Path(ns.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    root, rec_log = Path(ns.root), Path(ns.rec_log)
    result: dict = {
        "label": ns.label,
        "window": None,
        "input_found": False,
        "typed": False,
        "submitted": False,
    }
    t0 = time.monotonic()
    prog = Progress(out_dir / f"{ns.label}-progress.jsonl")
    prog.step("start", exe=ns.exe, workspace=ns.workspace)
    port = free_port()
    result["port"] = port
    prompt = Path(ns.prompt_file).read_text(encoding="utf-8")
    base_lines = recorder_lines(rec_log)
    cdp = None
    log = out_dir / f"{ns.label}-host-output.log"
    with open(log, "wb") as host_out:
        prog.step("launch_host", port=port)
        launch = (
            [f"--remote-debugging-port={port}"]
            if ns.kind == "native"
            else code_args(ns.user_data_dir, ns.extensions_dir, ns.workspace, port)
        )
        tree = A.ProcessTree(
            [ns.exe, *launch],
            cwd=Path(ns.workspace),
            env=dict(os.environ),
            stdout=host_out,
            stderr=host_out,
        )
        try:
            prog.step("wait_window", host_pid=tree.proc.pid)
            result["window"] = wait_main_window(root, tree, ns.window_timeout)
            prog.step("window", found=bool(result["window"]), host_exit=tree.proc.poll())
            if not result["window"]:
                result["error"] = "no main window"
                return result
            result["loopback_only"] = not non_loopback_reachable(port)
            prog.step("loopback_check", loopback_only=result["loopback_only"])
            if not result["loopback_only"]:
                result["error"] = "debug port reachable on a non-loopback address; aborted"
                return result
            version = None
            end = time.monotonic() + 60
            while time.monotonic() < end and version is None:
                with contextlib.suppress(OSError, ValueError):
                    version = http_json(port, "/json/version")
                time.sleep(0.5)
            prog.step("cdp_endpoint", answered=bool(version))
            if not version:
                result["error"] = "CDP endpoint never answered"
                return result
            ws_path = "/" + version["webSocketDebuggerUrl"].split("/", 3)[3]
            cdp = CDP(WebSocket(port, ws_path))
            cdp.call("Target.setDiscoverTargets", {"discover": True})
            workbench = None
            end = time.monotonic() + 120
            onboarding: list[str] = []
            while time.monotonic() < end and workbench is None:
                try:
                    targets = cdp.call("Target.getTargets").get("targetInfos", [])
                except (OSError, CDPError, TimeoutError):
                    # TRAE 跳过引导后关掉欢迎窗、另开工作台，浏览器端连接随之断（run 20261026T145044Z-553df4）：重连
                    prog.step("cdp_reconnect")
                    with contextlib.suppress(OSError):
                        cdp.ws.close()
                    time.sleep(2)
                    cdp = connect_cdp(port, 30)
                    if cdp is None:
                        result["error"] = "CDP endpoint went away after onboarding"
                        return result
                    continue
                for t in targets:
                    url = str(t.get("url", ""))
                    if t.get("type") == "page" and (
                        is_workbench_url(url)
                        if ns.kind != "native"
                        else not url.startswith("devtools:")
                    ):
                        workbench = cdp.call(
                            "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
                        )["sessionId"]
                        break
                    if t.get("type") == "page" and "setup" in url.rsplit("/", 1)[-1]:
                        sid = cdp.call(
                            "Target.attachToTarget", {"targetId": t["targetId"], "flatten": True}
                        )["sessionId"]
                        clicked = cdp.evaluate(
                            sid, ONBOARDING_CLICK_JS % json.dumps(list(ONBOARDING_BUTTONS))
                        )
                        if clicked and len(onboarding) < 8:
                            onboarding.append(clicked)
                            prog.step("onboarding_click", button=clicked)
                if workbench is None:
                    time.sleep(2)
            result["onboarding_clicked"] = onboarding
            prog.step(
                "workbench_attach",
                attached=workbench is not None,
                pages=[
                    _url_shape(str(t.get("url", "")))
                    for t in cdp.call("Target.getTargets").get("targetInfos", [])
                    if t.get("type") == "page"
                ][:8],
            )
            if workbench is None:
                result["error"] = "workbench page not found"
                return result
            cdp.sessions.setdefault(workbench, {"type": "page"})
            cdp.call(
                "Target.setAutoAttach",
                {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
                session=workbench,
            )
            cdp.pump(5.0)  # 给扩展宿主一点激活时间，webview 目标陆续出现
            if ns.kind == "native":
                sid, via = (
                    native_ready(cdp, workbench, ns.panel_timeout, onboarding, prog),
                    "native",
                )
            else:
                sid, via = open_panel(
                    cdp,
                    workbench,
                    ns.exe,
                    uri_open_args(ns.user_data_dir, ns.extensions_dir, ns.open_uri)
                    if ns.open_uri != "none"
                    else None,
                    ns.panel_timeout,
                )
            result["panel_opened_via"] = via
            prog.step("open_panel", via=via, sessions=len(cdp.sessions))
            result["target_types"] = sorted({str(i.get("type")) for i in cdp.sessions.values()})
            if not sid:
                result["error"] = "panel input not found"
                return result
            result["input_found"] = True
            typed = cdp.evaluate(sid, TYPE_JS % json.dumps(prompt)) or {}
            result["typed"] = bool(typed.get("typed"))
            prog.step("type_prompt", result=typed)
            if not result["typed"]:
                result["error"] = f"typing failed: {typed}"
                return result
            time.sleep(0.5)
            sub = cdp.evaluate(sid, SUBMIT_JS) or {}
            result["submitted"] = bool(sub.get("submitted"))
            result["submitted_via"] = sub.get("via")
            prog.step("submit", result=sub)
            time.sleep(3)
            result["state_after_submit"] = cdp.evaluate(sid, STATE_JS)
            reason, n = wait_quiet(
                rec_log,
                base_lines,
                ns.quiet_s,
                ns.max_s,
                ns.first_traffic_s,
                # TRAE 跳过引导后换进程重开窗口：起的那个进程退了，宿主还在（run 20261026T145631Z-d4aaf2）
                lambda: tree.proc.poll() is None or bool(P.top_windows(root)),
                lambda: bool((cdp.evaluate(sid, STATE_JS) or {}).get("busy")),
                lambda: bool(PERMISSION_PROMPT_RE.search(str(cdp.evaluate(sid, TEXT_JS) or ""))),
            )
            result["wait_ended"] = reason
            prog.step("wait_done", reason=reason, lines=n - base_lines)
            result["recorder_lines"] = n - base_lines
            result["final_state"] = cdp.evaluate(sid, STATE_JS)
            return result
        except Exception as exc:  # noqa: BLE001 — 驱动器出错如实写进结果，不当通过
            result["error"] = f"{type(exc).__name__}: {exc}"[:300]
            prog.step("exception", error=result["error"])
            return result
        finally:
            prog.step("teardown")
            result["seconds"] = round(time.monotonic() - t0, 1)
            if cdp is not None:
                try:
                    _evidence(cdp, out_dir, ns.label, result)
                    prog.step("evidence", screenshot=result.get("screenshot"))
                except (OSError, CDPError, TimeoutError) as exc:  # 存证失败不能吞掉前面的结果
                    prog.step("evidence_failed", error=f"{type(exc).__name__}: {exc}"[:200])
                with contextlib.suppress(OSError):
                    cdp.ws.close()
            tree.close()  # Job 结束整棵进程树，调试端口随之关闭


def _evidence(cdp: CDP, out_dir: Path, label: str, result: dict) -> None:
    """存证（不参与判定）：工作台截图、每个会话里的面板文字。出错只记一笔。"""
    texts = []
    for sid, info in list(cdp.sessions.items()):
        t = cdp.evaluate(sid, TEXT_JS)
        if t:
            texts.append(f"=== {info.get('type')} {str(info.get('url', ''))[:60]}\n{t}")
    (out_dir / f"{label}-panel.txt").write_text("\n".join(texts), encoding="utf-8")
    # 只有扩展自己的 webview 的文字：判「扩展要登录」只看这里，宿主自己的登录提示（Cursor 的 AI 功能）不算
    web = [
        cdp.evaluate(sid, TEXT_JS) or ""
        for sid, info in list(cdp.sessions.items())
        if str(info.get("url", "")).startswith("vscode-webview:")
    ]
    (out_dir / f"{label}-webview.txt").write_text("\n".join(web), encoding="utf-8")
    for sid, info in list(cdp.sessions.items()):
        if info.get("type") == "page" and is_workbench_url(str(info.get("url", ""))):
            with contextlib.suppress(CDPError, TimeoutError, OSError):
                shot = cdp.call(
                    "Page.captureScreenshot", {"format": "png"}, session=sid, timeout=20
                )
                save_png(shot["data"], out_dir / f"{label}-window.png")
                result["screenshot"] = f"{label}-window.png"
            break
    result["targets"] = [
        {"type": i.get("type"), "scheme": str(i.get("url", "")).split(":", 1)[0]}
        for i in cdp.sessions.values()
    ]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--exe", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--user-data-dir", required=True)
    p.add_argument("--extensions-dir", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--prompt-file", required=True)
    p.add_argument("--rec-log", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--window-timeout", type=float, default=120)
    p.add_argument("--panel-timeout", type=float, default=120)
    p.add_argument("--quiet-s", type=float, default=45)
    p.add_argument("--max-s", type=float, default=1200)
    p.add_argument("--first-traffic-s", type=float, default=300)
    #: 扩展的 URI 入口（宿主自己的 URL 协议）；`none` = 只走命令面板；URI 不认也会退回命令面板
    p.add_argument("--open-uri", default=OPEN_URI)
    #: vscode = VS Code 系宿主里的扩展面板；native = 宿主自己的聊天界面（ZCode）
    p.add_argument("--kind", choices=("vscode", "native"), default="vscode")
    ns = p.parse_args(argv)
    if ns.kind == "native":
        use_input_selector(NATIVE_INPUT_SELECTOR)
    try:
        res = drive(ns)
    except Exception as exc:  # noqa: BLE001 — 启动阶段就崩也要留下结果
        res = {"label": ns.label, "window": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
        Progress(Path(ns.out_dir) / f"{ns.label}-progress.jsonl").step("fatal", error=res["error"])
    out = Path(ns.out_dir) / f"{ns.label}.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
