"""脚本里的 `input()` / `sys.stdin` / `getpass` 桥接——worker 侧（ADR 0099）。

safe worker 的 `sys.stdin` 就是协议管道：脚本一 `input()` 就阻塞在协议 stdin 上，干等到静默看门狗
把它杀掉；更坏的是父进程这时写进来的下一条命令会被当成答案读走。这里在跑用户代码之前把三样东西
换掉，每一问经**会话缓存目录里的文件会合**交给父进程（`inputbroker`）去答：

* 发问：`<会合目录>/req-<n>.json`（tmp + `os.replace`）；
* 等答：每 `WORKER_POLL` 秒看一次 `reply-<n>.json`，最多 `INPUT_WAIT_TIMEOUT` 秒；
* 回复三种形状：`{"answer": "…"}` / `{"eof": true}` / `{"no_answer": true, "reason": "…"}`。

会合目录是 worker 的 `out_dir/script-input/`——Tavotto 自己的会话缓存，**不是用户目录**。
写请求文件没有走 `atomicio`：worker 的装载闭包刻意不含它，而这里写的是一次性的进程间信号，
不是文档；父进程写回复走 `atomicio`。

纯标准库、**不 import 任何兄弟模块**：它既装进 worker 的私有包（`worker._ENGINE_MODULES`），
也被 Flask 父进程 `from . import scriptinput` 取常量（文件名、上限、超时）——两侧同一份。
"""

from __future__ import annotations

import builtins
import io
import json
import os
import sys
import time
from pathlib import Path

#: 会合目录名（在 worker 的 `out_dir` 下）。
DIRNAME = "script-input"
#: 等人作答的上限（秒）。**必须小于** `pool.BUILD_IDLE_TIMEOUT`：到点先由这里回 EOF，
#: 静默看门狗永远轮不到杀一个正在等人的 worker（`tests/test_script_input.py` 钉着这条不等式）。
INPUT_WAIT_TIMEOUT = 600.0
#: 测试把等待缩到秒级用的环境变量（worker 从自己的环境读）。
TIMEOUT_ENV = "TAVOTTO_SCRIPT_INPUT_TIMEOUT"
#: worker 看回复文件的间隔（秒）。
WORKER_POLL = 0.1
#: 带给界面的 stdout 片段上限：最近多少行、最多多少字符；提示本身的上限。
TAIL_LINES = 40
TAIL_CHARS = 4000
PROMPT_CHARS = 2000
#: 读取方式（进请求与 `script_inputs` 记录）。
KINDS = ("input", "readline", "read", "getpass")


def request_name(index: int) -> str:
    return f"req-{index}.json"


def reply_name(index: int) -> str:
    return f"reply-{index}.json"


def index_of(name: str, prefix: str) -> int | None:
    """`req-3.json` → 3；形状不对回 None。"""
    if not (name.startswith(prefix + "-") and name.endswith(".json")):
        return None
    core = name[len(prefix) + 1 : -len(".json")]
    return int(core) if core.isdigit() else None


def clip_prompt(text: str) -> str:
    return text if len(text) <= PROMPT_CHARS else text[:PROMPT_CHARS] + "…"


def wait_timeout() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "")
    try:
        value = float(raw)
    except ValueError:
        return INPUT_WAIT_TIMEOUT
    return value if value > 0 else INPUT_WAIT_TIMEOUT


class ScriptNeedsInput(BaseException):
    """脚本要输入而没有人能答（非交互场景 / 重放缺答案）。

    继承 **BaseException**：它和 `SystemExit` / `KeyboardInterrupt` 同类，是「这次运行由外部终止」，
    不是脚本能处理的错误。继承 `Exception` 的话，脚本里的 `except Exception:` 会把它吞掉、拿着空选择
    继续跑，画出一张错的图而不报错（ADR 0099 §五）。
    """

    def __init__(self, prompt: str, reason: str = ""):
        super().__init__(prompt)
        self.prompt = prompt
        self.reason = reason


class StdoutTail(io.TextIOBase):
    """脚本 stdout 的转发器：原样写进 `target`（worker.log），同时留最近一段给界面看。

    编号清单（`1. xxx  2. yyy`）就是脚本 print 出来的——用户要看到它才能选。有界：
    `TAIL_LINES` 行 / `TAIL_CHARS` 字符，超出从头丢。
    """

    def __init__(self, target):
        self._target = target
        self._buf = ""

    def writable(self) -> bool:
        return True

    def write(self, text) -> int:
        text = str(text)
        self._target.write(text)
        self._buf = (self._buf + text)[-TAIL_CHARS * 2 :]
        return len(text)

    def flush(self) -> None:
        try:
            self._target.flush()
        except (OSError, ValueError):
            pass

    @property
    def encoding(self):  # noqa: D401 — 有些库会问 sys.stdout.encoding
        return getattr(self._target, "encoding", "utf-8")

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._target.fileno()

    def tail(self) -> str:
        lines = self._buf.splitlines()[-TAIL_LINES:]
        text = "\n".join(lines)
        return text[-TAIL_CHARS:]


class Channel:
    """一次 build 的问答通道：发问、等答、记账。"""

    def __init__(self, directory: str | os.PathLike, script: str, tail: StdoutTail | None = None):
        self.dir = Path(directory)
        self.script = script
        self.tail = tail
        self.count = 0
        #: 本次 build 实际用到的每一问（build 响应的 `script_inputs`）。
        self.record: list[dict] = []
        #: build 跑完之后置上：再有人读 stdin 一律 EOF，绝不再发问。
        self.closed = False

    def reset(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for p in self.dir.iterdir():
            try:
                p.unlink()
            except OSError:
                pass

    def _write_request(self, index: int, payload: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        final = self.dir / request_name(index)
        tmp = self.dir / f".{final.name}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, final)

    def _log(self, text: str) -> None:
        # 进 worker.log：静默看门狗在发问与作答这两个点各清零一次（ADR 0099 §三）
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass

    def ask(self, prompt: str, kind: str) -> str | None:
        """问一次；回答案字符串，EOF 回 None；没人能答抛 `ScriptNeedsInput`。"""
        if self.closed:
            return None
        self.count += 1
        index = self.count
        prompt = clip_prompt(prompt)
        self._write_request(
            index,
            {
                "index": index,
                "kind": kind,
                "prompt": prompt,
                "script": self.script,
                "stdout_tail": self.tail.tail() if self.tail is not None else "",
            },
        )
        self._log(f"[input] 第 {index} 问等待作答\n")
        reply_path = self.dir / reply_name(index)
        deadline = time.monotonic() + wait_timeout()
        reply = None
        while time.monotonic() < deadline:
            if reply_path.exists():
                try:
                    reply = json.loads(reply_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    reply = None  # 写到一半不可能（原子写），读失败就下一轮再读
                if isinstance(reply, dict):
                    break
            time.sleep(WORKER_POLL)
        if not isinstance(reply, dict):
            self._log(f"[input] 第 {index} 问等待超时，按 EOF 处理\n")
            self.record.append(
                {"index": index, "kind": kind, "prompt": prompt, "answer": None, "timed_out": True}
            )
            return None
        if reply.get("no_answer"):
            self._log(f"[input] 第 {index} 问没有可用的答案\n")
            raise ScriptNeedsInput(prompt, str(reply.get("reason") or ""))
        if reply.get("eof") or not isinstance(reply.get("answer"), str):
            self._log(f"[input] 第 {index} 问：EOF\n")
            self.record.append({"index": index, "kind": kind, "prompt": prompt, "answer": None})
            return None
        answer = reply["answer"]
        # 转录「提示 → 答案」：和终端里看到的一样（getpass 也记——ADR 写明不掩码）
        self._log(f"{answer}\n")
        self.record.append({"index": index, "kind": kind, "prompt": prompt, "answer": answer})
        return answer


class BridgedStdin(io.TextIOBase):
    """顶替 `sys.stdin`：`readline()` 问一次（回答补换行），`read()` 问一次后一律 EOF。"""

    def __init__(self, channel: Channel):
        self._channel = channel
        self._eof = False

    def readable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self):
        return "utf-8"

    def readline(self, size=-1) -> str:
        if self._eof:
            return ""
        answer = self._channel.ask("", "readline")
        if answer is None:
            self._eof = True
            return ""
        return answer + "\n"

    def read(self, size=-1) -> str:
        if self._eof:
            return ""
        answer = self._channel.ask("", "read")
        self._eof = True
        return answer or ""

    def readlines(self, hint=-1) -> list[str]:
        out = []
        while True:
            line = self.readline()
            if not line:
                return out
            out.append(line)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        line = self.readline()
        if not line:
            raise StopIteration
        return line


def install(channel: Channel) -> None:
    """换掉 `builtins.input` / `sys.stdin` / `getpass.getpass`。装上就不卸（ADR 0099 §一）。"""

    def bridged_input(prompt="") -> str:
        text = str(prompt)
        if text:
            sys.stdout.write(text)
            try:
                sys.stdout.flush()
            except (OSError, ValueError):
                pass
        answer = channel.ask(text, "input")
        if answer is None:
            raise EOFError("EOF when reading a line")
        return answer

    def bridged_getpass(prompt="Password: ", stream=None) -> str:
        text = str(prompt)
        # 只转发提示、不掩码：界面上那个框是明文（ADR 0099 §一）
        answer = channel.ask(text, "getpass")
        if answer is None:
            raise EOFError("EOF when reading a line")
        return answer

    builtins.input = bridged_input
    sys.stdin = BridgedStdin(channel)
    import getpass  # noqa: PLC0415 — 只在真要装的时候 import

    getpass.getpass = bridged_getpass
