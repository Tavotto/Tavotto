"""脚本里的 `input()` / `sys.stdin` / `getpass` 桥接——worker 侧（ADR 0099）。

safe worker 的 `sys.stdin` 就是协议管道：脚本一 `input()` 就阻塞在协议 stdin 上，干等到静默看门狗
把它杀掉；更坏的是父进程这时写进来的下一条命令会被当成答案读走。这里在跑用户代码之前把三样东西
换掉，每一问经**会话缓存目录里的文件会合**交给父进程（`inputbroker`）去答：

* 发问：`<会合目录>/req-<n>.json`（tmp + `os.replace`）；
* 等答：每 `WORKER_POLL` 秒看一次 `reply-<n>.json`，最多 `INPUT_WAIT_TIMEOUT` 秒；
* 定案：人答上与等到超时只能有一方算数——谁先用 `O_EXCL` 建成 `claim-<n>.json` 谁赢（`claim()`）。
  超时赢了，迟到的答案既不回给脚本也不记住，父进程据此收起那一问；答题方赢了，worker 等它把回复写完；
* 回复三种形状：`{"answer": "…"}` / `{"eof": true}` / `{"no_answer": true, "reason": "…"}`。
* 上下文（T08）：每一问带一个 `context` 摘要——提示、读取方式、**上一问之后**脚本打印的那段输出（编号清单
  就在这里）与本次运行里前面每一问的回答（口令只记占位，不记值也不哈希它）。父进程按它判断旧答案还能不能
  原样用：菜单换了序、前一问答得不一样，摘要就不同，旧答案只能当建议、要重新问。
* 口令（`getpass`）只交给脚本：记账里只留 `secret: true`，**没有答案**——build 响应、热会话、执行转录都
  拿不到它；重放需要时重新问。

会合目录是 worker 的 `out_dir/script-input/`——Tavotto 自己的会话缓存，**不是用户目录**。
写请求文件没有走 `atomicio`：worker 的装载闭包刻意不含它，而这里写的是一次性的进程间信号，
不是文档；父进程写回复走 `atomicio`。

纯标准库、**不 import 任何兄弟模块**：它既装进 worker 的私有包（`worker._ENGINE_MODULES`），
也被 Flask 父进程 `from . import scriptinput` 取常量（文件名、上限、超时）——两侧同一份。
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import hmac
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
#: 到点时界面恰好已经定案（正在落盘答案）：再给它这么久把回复写出来。
ANSWER_GRACE = 30.0
#: 带给界面的 stdout 片段上限：最近多少行、最多多少字符；提示本身的上限。
TAIL_LINES = 40
TAIL_CHARS = 4000
PROMPT_CHARS = 2000
#: 读取方式（进请求与 `script_inputs` 记录）。
KINDS = ("input", "readline", "read", "getpass")
#: 上下文摘要的格式版本：算法变了旧摘要一律对不上（= 重新问），不会被误当成同一问。
CONTEXT_VERSION = 1
#: 前一问的回答在上下文里的占位：口令不进摘要（低熵值的哈希可猜），EOF 与真答案区分开。
SECRET_MARK = "<secret>"
EOF_MARK = "<eof>"


def context_digest(kind: str, prompt: str, segment: str, earlier: list[dict]) -> str:
    """一问的上下文摘要（T08）：读取方式 + 提示 + 上一问之后的输出 + 前面每一问的回答。

    只在本机比对「是不是同一问」用：父进程拿它决定旧答案能不能原样复用。**不进**界面事件、诊断、遥测。"""
    answers = [
        [
            r.get("kind"),
            SECRET_MARK
            if r.get("secret")
            else (EOF_MARK if r.get("answer") is None else r["answer"]),
        ]
        for r in earlier
    ]
    canon = json.dumps(
        [CONTEXT_VERSION, kind, prompt, segment[-TAIL_CHARS:], answers],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "ctx1:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def request_name(index: int) -> str:
    return f"req-{index}.json"


def reply_name(index: int) -> str:
    return f"reply-{index}.json"


def claim_name(index: int) -> str:
    return f"claim-{index}.json"


#: `claim-<n>.json` 的两种内容：界面答上了 / worker 等到超时、按 EOF 往下跑了。
CLAIM_ANSWER = "answer"
CLAIM_TIMEOUT = "timeout"


def claim(directory: str | os.PathLike, index: int, who: str) -> bool:
    """给第 `index` 问定案：`O_EXCL` 建 `claim-<n>.json`，建成的一方赢，另一方回 False。

    会合目录已经不在（build 结束被删）时抛 `OSError`，由调用方决定算输还是算赢。"""
    path = Path(directory) / claim_name(index)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(who)
    return True


def claimed_by(directory: str | os.PathLike, index: int) -> str | None:
    """第 `index` 问由谁定的案；还没定（或刚建、内容未写完）回 None。"""
    try:
        return (Path(directory) / claim_name(index)).read_text(encoding="utf-8") or None
    except OSError:
        return None


def release(directory: str | os.PathLike, index: int) -> None:
    """答题方定案后没能落盘（答案不合法等）：放掉定案，这一问照旧在等。"""
    with contextlib.suppress(OSError):
        (Path(directory) / claim_name(index)).unlink()


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

    def __init__(self, prompt: str, reason: str = "", kind: str = ""):
        super().__init__(prompt)
        self.prompt = prompt
        self.reason = reason
        #: 这一问的读取方式（`input` / `getpass`…）：`getpass` 一律是口令，无论 `reason` 是首问的
        #: `no_interactive_client` 还是重放时的 `secret_required`（Codex #818 r4220889705）
        self.kind = kind


class StdoutTail(io.TextIOBase):
    """脚本 stdout 的转发器：原样写进 `target`（worker.log），同时留最近一段给界面看。

    编号清单（`1. xxx  2. yyy`）就是脚本 print 出来的——用户要看到它才能选。有界：
    `TAIL_LINES` 行 / `TAIL_CHARS` 字符，超出从头丢。
    """

    def __init__(self, target):
        self._target = target
        self._buf = ""
        #: 上一问之后打印的那段（有界）：上下文摘要用它，编号清单就在这里
        self._since = ""

    def writable(self) -> bool:
        return True

    def write(self, text) -> int:
        text = str(text)
        self._target.write(text)
        self._buf = (self._buf + text)[-TAIL_CHARS * 2 :]
        self._since = (self._since + text)[-TAIL_CHARS:]
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

    def segment(self) -> str:
        """上一问（或 build 开始）之后打印的输出，有界；`mark()` 之后从空开始。"""
        return self._since

    def mark(self) -> None:
        self._since = ""


class Channel:
    """一次 build 的问答通道：发问、等答、记账。"""

    def __init__(
        self,
        directory: str | os.PathLike,
        script: str,
        tail: StdoutTail | None = None,
        *,
        private_key: str = "",
    ):
        self.dir = Path(directory)
        self.script = script
        self.tail = tail
        self.private_key = private_key
        self.count = 0
        #: 本次 build 实际用到的每一问（build 响应的 `script_inputs`）。
        self.record: list[dict] = []
        #: build 跑完之后置上：再有人读 stdin 一律 EOF，绝不再发问。
        self.closed = False

    def _mkdir(self) -> None:
        # 敏感会话：会合目录只给本用户（POSIX；Windows 上 mode 被忽略），明文答案短暂经过这里
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700 if self.private_key else 0o777)

    def reset(self) -> None:
        self._mkdir()
        for p in self.dir.iterdir():
            try:
                p.unlink()
            except OSError:
                pass

    def _write_request(self, index: int, payload: dict) -> None:
        self._mkdir()
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

    def _claim_timeout(self, index: int) -> bool:
        """到点了：抢「超时」这一案。抢不到 = 界面正在答（它会写回复）；会合目录没了 = 没人会答。"""
        try:
            return claim(self.dir, index, CLAIM_TIMEOUT)
        except OSError:
            return True

    def ask(self, prompt: str, kind: str) -> str | None:
        """问一次；回答案字符串，EOF 回 None；没人能答抛 `ScriptNeedsInput`。"""
        if self.closed:
            return None
        self.count += 1
        index = self.count
        # Private-run prompts retain a keyed identity without exposing argv in
        # the disk rendezvous. Replay must compare the real question as well as
        # its output/answer context, even though the displayed prompt is masked.
        identity = {"prompt": clip_prompt(prompt)}
        context = context_digest(
            kind,
            identity["prompt"],
            self.tail.segment() if self.tail is not None else "",
            self.record,
        )
        if self.private_key:
            key = bytes.fromhex(self.private_key)
            identity = {
                "prompt": "[sensitive run: input prompt omitted]",
                "prompt_id": hmac.new(key, prompt.encode("utf-8"), hashlib.sha256).hexdigest(),
            }
            context = "ctx1:" + hmac.new(key, context.encode("utf-8"), hashlib.sha256).hexdigest()
        prompt = identity["prompt"]
        self._write_request(
            index,
            {
                "index": index,
                "kind": kind,
                **identity,
                "script": self.script,
                "stdout_tail": self.tail.tail()
                if self.tail is not None and not self.private_key
                else "",
                "context": context,
            },
        )
        if self.tail is not None:
            self.tail.mark()  # 下一问的上下文只看这一问之后打印的
        self._log(f"[input] 第 {index} 问等待作答\n")
        reply_path = self.dir / reply_name(index)
        # 口令与敏感会话里的任何作答：回复文件里是明文答案，**无论走哪条出口**（读到、超时、解析失败、
        # 抛 ScriptNeedsInput / KeyboardInterrupt）离开这一问时都删掉，不留在长期存在的会话缓存里
        # （Codex #812 P1）。非敏感的普通 input 保持原样（文件随会合目录在 build 结束时清掉）。
        scrub = kind == "getpass" or bool(self.private_key)
        try:
            return self._await_reply(index, kind, identity, context, prompt, reply_path)
        finally:
            if scrub:
                with contextlib.suppress(OSError):
                    reply_path.unlink()

    def _await_reply(
        self,
        index: int,
        kind: str,
        identity: dict,
        context: str,
        prompt: str,
        reply_path: Path,
    ) -> str | None:
        deadline = time.monotonic() + wait_timeout()
        # 答题方定了案却迟迟写不出回复（不该发生）时的兜底：再等这么久就按超时处理
        hard_stop = deadline + ANSWER_GRACE
        reply = None
        while True:
            if reply_path.exists():
                try:
                    reply = json.loads(reply_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    reply = None  # 写到一半不可能（原子写），读失败就下一轮再读
                if isinstance(reply, dict):
                    break
            now = time.monotonic()
            if now >= deadline and (now >= hard_stop or self._claim_timeout(index)):
                # 超时定了案：父进程据此收起这一问，迟到的答案不再算数（Codex #680 P2）
                reply = None
                break
            time.sleep(WORKER_POLL)
        base = {"index": index, "kind": kind, **identity, "context": context}
        if not isinstance(reply, dict):
            self._log(f"[input] 第 {index} 问等待超时，按 EOF 处理\n")
            self.record.append({**base, "answer": None, "timed_out": True})
            return None
        if reply.get("no_answer"):
            self._log(f"[input] 第 {index} 问没有可用的答案\n")
            raise ScriptNeedsInput(prompt, str(reply.get("reason") or ""), kind)
        if reply.get("eof") or not isinstance(reply.get("answer"), str):
            self._log(f"[input] 第 {index} 问：EOF\n")
            self.record.append({**base, "answer": None})
            return None
        answer = reply["answer"]
        if kind == "getpass":
            # 口令绝不落盘：worker.log 活得比会合目录久，还会进诊断包与错误里的日志尾巴（Codex #680 P1）。
            # 只写一行固定的标记——看门狗照样在作答这一点清零；回复文件由 ask() 的 finally 删掉。
            # 记账里也**没有**它（T08）：build 响应、热会话、执行转录都拿不到，重放需要时重新问
            self._log(f"[input] 第 {index} 问已作答（口令不转录）\n")
            self.record.append({**base, "answer": None, "secret": True})
            return answer
        if self.private_key:
            # 敏感会话的答案同样不进日志（Codex #812 P1）；记账照旧带着答案供重放比对
            self._log(f"[input] 第 {index} 问已作答（不转录）\n")
        else:
            # 转录「提示 → 答案」：和终端里看到的一样
            self._log(f"{answer}\n")
        self.record.append({**base, "answer": answer})
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
        # 只转发提示；界面用密码框作答（ADR 0099 §九），答案不进记账
        answer = channel.ask(text, "getpass")
        if answer is None:
            raise EOFError("EOF when reading a line")
        return answer

    builtins.input = bridged_input
    sys.stdin = BridgedStdin(channel)
    import getpass  # noqa: PLC0415 — 只在真要装的时候 import

    getpass.getpass = bridged_getpass
