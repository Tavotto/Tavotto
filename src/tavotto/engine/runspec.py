"""`tavotto run` 的**严格 invocation 解析**（ADR 0021 §2）。

    tavotto run [Tavotto 选项] -- <python> <脚本.py> [用户参数…]
    tavotto run [Tavotto 选项] -- <python> -m <模块>  [用户参数…]

## `--` 为什么是强制的

因为用户脚本完全可以有自己的 `--project` / `--quiet` / `--status-file`。
一个会猜"哪些参数属于 Tavotto、哪些属于脚本"的 parser，猜错时的表现是
**Tavotto 吃掉了用户的参数**——脚本照样跑完、照样出图，只是用的是一个它没
要求的配置。那种错误没有任何信号，用户要靠"结果不对"去发现它。

`--` 是没有歧义的那条线。缺了就报 `run_command_missing` 并给出带 `--` 的
示例，绝不"尽力而为地猜一下"。

## 为什么只认 `python 文件.py` / `python -m 模块`

ADR 0014 §7 / 0020 §10 已裁决：任意 shell 命令（管道、env 前缀、Makefile、
bash 包装、`poetry run`、`conda run`）的解析与注入面是无界的。支持它就要对
每一种形态回答"捕获层注得进去吗、argv/env 语义还原了吗"——做不到就会变成
**静默半支持**：命令跑了，图没出来，而 Tavotto 说不清为什么。

所以不认识的一律**显式拒绝**，附上稳定错误码。

## 三件事分得很开

* `cwd`：用户终端的工作目录。**原样继承，一个字节不动**（相对路径、
  `sys.path`、`python -m` 全靠它）；
* `project_root`：Tavotto 用来组织图库/文档/素材的目录。`--project` 显式给出
  才不用猜；猜的时候**绝不越过 `MAX_PARENTS` 层**（否则一个 home 目录会被
  当成图库整棵扫）；
* 用户 argv：`--` 之后目标后面的全部，**runner 一个字都不解释**。

纯标准库（CLI 不 import Flask、不 import matplotlib）。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from . import runcodes
from .runcodes import RunError

#: 目标解释器的最低版本。与 `projectenv.PYTHON_MIN` / `pyproject.requires-python`
#: 同一个下界——用户的 Python 跑的是 `bridge_runner.py`，而那份代码按 3.10 写。
PYTHON_MIN = (3, 10)

#: 认得的解释器命令：按 **basename** 判。
#: `python` / `python3` / `python3.11` / `python.exe` / `python3.11.exe`。
#: 绝对路径与 venv 里的 symlink 都走这条（它们的 basename 就长这样）。
#: `py`（Windows launcher）**刻意不在内**：`py -3.12` 的版本选择语义要靠
#: 再解析一层 launcher 参数，那是另一个产品决定。
_INTERPRETER_RE = re.compile(r"^python(\d+(\.\d+)?)?(\.exe)?$", re.IGNORECASE)

#: 向上找注册表的层数——与 `handoff.MAX_PARENTS` 同一个理由和同一个值。
MAX_PARENTS = 3

#: 体检探针。**只读**：不 import matplotlib、不碰用户目录、不写任何东西。
#: 一行 `-c`，输出一行 JSON。
_PROBE_SRC = (
    "import json,sys;"
    "print(json.dumps({"
    "'version':list(sys.version_info[:3]),"
    "'implementation':sys.implementation.name,"
    "'executable':sys.executable,"
    "'prefix':sys.prefix}))"
)
PROBE_TIMEOUT = 30.0

TARGET_SCRIPT = "script"
TARGET_MODULE = "module"


@dataclasses.dataclass(frozen=True)
class RunOptions:
    """`--` **左边**那些——Tavotto 自己的选项。"""

    project: str = ""
    quiet: bool = False
    status_file: str = ""


@dataclasses.dataclass(frozen=True)
class RunRequest:
    """一条已经**校验过**的 `tavotto run` 请求。

    构造出它就意味着：命令形态认得、解释器找得到且体检通过、目标存在、
    项目根定得下来。**此时用户脚本一行都还没跑。**
    """

    interpreter: str  # 解释器绝对路径（体检过的那一个）
    interpreter_word: str  # 用户原样敲的那个词（提示文案用）
    target_kind: str  # TARGET_SCRIPT | TARGET_MODULE
    raw_target: str  # 用户原样敲的目标串（argv[0] 的对拍口径，ADR 0020 §4）
    user_argv: tuple[str, ...]  # 目标后面的全部，一个字都不解释
    cwd: str  # 用户的工作目录（原样）
    project_root: str  # Tavotto 的项目目录（不影响执行语义）
    options: RunOptions
    python_version: tuple[int, int, int]

    @property
    def target_display(self) -> str:
        """给界面看的一行目标描述。"""
        if self.target_kind == TARGET_MODULE:
            return f"python -m {self.raw_target}"
        return os.path.basename(self.raw_target) or self.raw_target

    def command_fingerprint(self) -> str:
        """这条 invocation 的稳定指纹。

        **只由公开且稳定的部分组成**：解释器 realpath、目标种类与目标、
        argv 的**数量**、cwd、项目根。**不含 argv 的值**——那里面可能有
        路径、样本名、甚至凭据（ADR 0021 §4）。
        """
        blob = json.dumps(
            {
                "interpreter": os.path.realpath(self.interpreter),
                "target_kind": self.target_kind,
                "target": self.raw_target,
                "argv_count": len(self.user_argv),
                "cwd": os.path.realpath(self.cwd),
                "project_root": os.path.realpath(self.project_root),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def permission_key(self) -> str:
        """ "记住这个项目和这个 Python" 的绑定键（ADR 0021 §7.1）。

        **刻意不含 target / argv / cwd**：用户记住的是「这个项目里的这条
        Python 可以跑 native」，不是「这一条命令」。含了 target 的话，同一个
        项目里换一个脚本就要重新确认一次——那会把确认训练成一个下意识点掉
        的对话框，而它恰恰是唯一一次真正的授权。
        """
        blob = json.dumps(
            {
                "schema": PERMISSION_SCHEMA,
                "project_root": os.path.realpath(self.project_root),
                "interpreter": os.path.realpath(self.interpreter),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


#: 许可绑定的 schema 版本。**加一维就要升**——升了之后旧许可全部失效、
#: 重新确认一次。这是刻意的：许可的含义变了，旧的那次点击就不再是对
#: 新含义的授权（ADR 0021 §7.1）。
PERMISSION_SCHEMA = 1


# --------------------------------------------------------------------------
# 1. argv 切分
# --------------------------------------------------------------------------
def split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """按**第一个** `--` 切成 (Tavotto 选项, invocation)。

    只切第一个：用户的命令里完全可以再出现 `--`（`python fig.py -- extra`），
    那些属于他。找不到 `--` 时 invocation 为空，由调用方报
    `run_command_missing`。
    """
    if "--" not in argv:
        return list(argv), []
    cut = argv.index("--")
    return list(argv[:cut]), list(argv[cut + 1 :])


def looks_like_interpreter(word: str) -> bool:
    """这个词是不是一个 Python 解释器命令（**按 basename 判**）。"""
    base = os.path.basename(word.replace("\\", "/"))
    return bool(_INTERPRETER_RE.match(base))


def parse_invocation(words: list[str]) -> tuple[str, str, str, tuple[str, ...]]:
    """`[python, 目标, 用户参数…]` → `(解释器词, target_kind, raw_target, argv)`。

    **纯语法层**：这里不碰磁盘、不起进程。存在性与体检在 `build_request`。
    """
    if not words:
        raise RunError(runcodes.RUN_COMMAND_MISSING)
    interp_word, rest = words[0], words[1:]
    if not looks_like_interpreter(interp_word):
        raise RunError(runcodes.UNSUPPORTED_RUN_COMMAND, command=interp_word)
    if not rest:
        raise RunError(runcodes.RUN_COMMAND_MISSING)
    head = rest[0]
    if head == "-m":
        if len(rest) < 2 or not rest[1]:
            raise RunError(runcodes.INVALID_MODULE_NAME, target="")
        return interp_word, TARGET_MODULE, rest[1], tuple(rest[2:])
    if head.startswith("-"):
        # **绝不静默丢 flag**：`python -O fig.py` 与 `python fig.py` 是两种
        # 语义（assert 被去掉）。丢掉它跑出来的图可能真的不一样。
        raise RunError(runcodes.UNSUPPORTED_PYTHON_OPTION, option=head)
    return interp_word, TARGET_SCRIPT, head, tuple(rest[1:])


# --------------------------------------------------------------------------
# 2. 解释器解析与体检
# --------------------------------------------------------------------------
def resolve_interpreter(word: str, *, cwd: str = "") -> str:
    """解释器词 → 绝对路径。找不到就抛，**绝不回退到 Tavotto 自己的 Python**。

    静默换解释器是 native 档最不该有的行为：用户看到的会是"跑起来了但缺包 /
    结果不对"，而他以为跑的是自己的环境（ADR 0020 §4）。
    """
    if os.path.sep in word or (os.path.altsep and os.path.altsep in word):
        cand = os.path.abspath(os.path.join(cwd or os.getcwd(), word))
        if not os.path.exists(cand):
            raise RunError(runcodes.INTERPRETER_NOT_FOUND, interpreter=word)
        if not os.path.isfile(cand):
            raise RunError(runcodes.INTERPRETER_NOT_EXECUTABLE, interpreter=word)
        if os.name != "nt" and not os.access(cand, os.X_OK):
            raise RunError(runcodes.INTERPRETER_NOT_EXECUTABLE, interpreter=word)
        return cand
    found = shutil.which(word, path=os.environ.get("PATH"))
    if not found:
        raise RunError(runcodes.INTERPRETER_NOT_FOUND, interpreter=word)
    return os.path.abspath(found)


def probe_interpreter(interpreter: str, *, run=subprocess.run) -> dict:
    """跑一次只读探针，回 `{version, implementation, executable, prefix}`。

    **不 import matplotlib**：它可能装在脚本运行时才生效的路径上，而真正的
    答案由真实运行给出——`ModuleNotFoundError` 会原样出现在用户终端里，
    与他自己敲那条命令完全一样。在这里抢着报一个我们编的，反而挡住了那条
    诚实的错误。
    """
    try:
        proc = run(
            [interpreter, "-c", _PROBE_SRC],
            capture_output=True,
            text=True,
            # **Windows 上必须钉 encoding**：默认按系统区域解码（cp936/cp1252），
            # 而 sys.prefix 里完全可能有非 ASCII。仓库有一条门禁盯着这个
            # （test_source_hygiene::test_windows_bound_subprocesses_pin_their_decoding）。
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunError(runcodes.INTERPRETER_NOT_EXECUTABLE, interpreter=interpreter) from exc
    if proc.returncode != 0:
        raise RunError(runcodes.INTERPRETER_NOT_EXECUTABLE, interpreter=interpreter)
    try:
        info = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        # 能跑、但吐不出我们要的那行 JSON：它多半不是 CPython（或者是个
        # 包装脚本）。报"实现不支持"而不是"不可执行"——后者会把用户送去
        # 检查文件权限，方向完全错。
        raise RunError(
            runcodes.UNSUPPORTED_PYTHON_IMPLEMENTATION, implementation="unknown"
        ) from exc
    impl = str(info.get("implementation") or "")
    if impl != "cpython":
        raise RunError(runcodes.UNSUPPORTED_PYTHON_IMPLEMENTATION, implementation=impl or "unknown")
    version = tuple(int(x) for x in (info.get("version") or [0, 0, 0]))[:3]
    if version < PYTHON_MIN:
        raise RunError(
            runcodes.UNSUPPORTED_PYTHON_VERSION,
            version=".".join(str(v) for v in version),
            minimum=".".join(str(v) for v in PYTHON_MIN),
        )
    return {**info, "version": version}


# --------------------------------------------------------------------------
# 3. 项目根
# --------------------------------------------------------------------------
def _has_registry(folder: str) -> bool:
    # 注册表文件名的唯一出处在 `registry`；这里 late import 是因为本模块要能
    # 在不 import Flask 的 CLI 里用（registry 本身也是纯标准库，只是层级更深）。
    from . import registry as engine_registry  # noqa: PLC0415

    for name in (engine_registry.REGISTRY_NAME, engine_registry.LEGACY_REGISTRY_NAME):
        if os.path.isfile(os.path.join(folder, name)):
            return True
    return False


def resolve_project_root(*, explicit: str, target_kind: str, script_abspath: str, cwd: str) -> str:
    """定出 Tavotto 的项目根。**它不影响任何执行语义**（cwd 才影响）。

    优先级（ADR 0021 §2.5）：

    1. `--project` 显式值——不猜；
    2. script 目标：从脚本所在目录向上找现有注册表，**最多 MAX_PARENTS 层**；
    3. script 目标兜底：脚本所在目录；
    4. module 目标：当前 cwd。

    **绝不静默越到用户 home 或整个磁盘**：那会把一整棵源码树当图库扫一遍
    （与 `handoff._project_root` 同一条纪律，同一个层数）。
    """
    if explicit:
        root = os.path.abspath(os.path.join(cwd, explicit))
        if not os.path.isdir(root):
            raise RunError(runcodes.PROJECT_ROOT_INVALID, project=explicit)
        if not os.access(root, os.R_OK):
            raise RunError(runcodes.PROJECT_UNREADABLE, project=explicit)
        return root
    if target_kind != TARGET_SCRIPT:
        return os.path.abspath(cwd)
    folder = os.path.dirname(script_abspath) or os.path.abspath(cwd)
    probe = folder
    for _ in range(MAX_PARENTS + 1):
        if _has_registry(probe):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return folder


# --------------------------------------------------------------------------
# 4. 组装
# --------------------------------------------------------------------------
def build_request(
    options: RunOptions,
    invocation: list[str],
    *,
    cwd: str = "",
    run=subprocess.run,
) -> RunRequest:
    """把一条命令行化成校验过的 `RunRequest`。**这一步之后才允许 spawn。**

    顺序是刻意的：语法 → 解释器 → 目标 → 项目根。先做便宜且能给出最准确
    指引的那些——`tavotto run -- make all` 该立刻告诉用户"只能跑 Python"，
    而不是先花 30 秒去体检一个根本不存在的解释器。
    """
    cwd = os.path.abspath(cwd or os.getcwd())
    interp_word, kind, raw_target, user_argv = parse_invocation(invocation)
    interpreter = resolve_interpreter(interp_word, cwd=cwd)
    info = probe_interpreter(interpreter, run=run)

    script_abspath = ""
    if kind == TARGET_SCRIPT:
        script_abspath = os.path.abspath(os.path.join(cwd, raw_target))
        if not os.path.exists(script_abspath):
            raise RunError(runcodes.SCRIPT_TARGET_MISSING, target=raw_target)
        if not os.path.isfile(script_abspath):
            raise RunError(runcodes.SCRIPT_TARGET_NOT_FILE, target=raw_target)
    else:
        parts = raw_target.split(".")
        if not raw_target or not all(p.isidentifier() for p in parts):
            raise RunError(runcodes.INVALID_MODULE_NAME, target=raw_target)

    project_root = resolve_project_root(
        explicit=options.project, target_kind=kind, script_abspath=script_abspath, cwd=cwd
    )
    return RunRequest(
        interpreter=interpreter,
        interpreter_word=interp_word,
        target_kind=kind,
        raw_target=raw_target,
        user_argv=user_argv,
        cwd=cwd,
        project_root=project_root,
        options=options,
        python_version=tuple(info["version"]),
    )


def status_payload(
    request: RunRequest | None,
    *,
    script_exit_code: int | None = None,
    figures_captured: int | None = None,
    session_result: str = "",
    error_code: str = "",
) -> dict:
    """`--status-file` 写的那份（ADR 0021 §11）。

    **刻意不含**：token、完整环境、argv 的值、解释器的完整路径之外的任何
    机器细节。argv 只记**数量**——「脚本收到了几个参数」足以排障，而参数值
    里可能有样本名、路径甚至凭据。
    """
    out: dict = {
        "schema": 1,
        "product": "tavotto run",
        "beta": True,
        "script_exit_code": script_exit_code,
        "figures_captured": figures_captured,
        "session_result": session_result,
        "error_code": error_code or None,
    }
    if request is not None:
        out.update(
            {
                "target_kind": request.target_kind,
                "arg_count": len(request.user_argv),
                "python_version": ".".join(str(v) for v in request.python_version),
                "command_fingerprint": request.command_fingerprint(),
            }
        )
    return out


def write_status_file(path: str, payload: dict) -> None:
    """原子写：临时文件 + `os.replace`。

    非原子写的坏处在这里很具体——调用方（CI 脚本、编辑器）会在 CLI 还在跑
    的时候去读它，读到半截 JSON 就是一次 `JSONDecodeError`，而那个失败与
    "Tavotto 挂了"长得一模一样。
    """
    path = os.path.abspath(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def usage_text() -> str:
    """`tavotto run` 的用法文本。**Beta 与边界写在这里，不在别处**。

    同一段文字有两个流向，取决于**是谁要的**：`--help` 是用户要的输出，写
    stdout 且退 0；用法错误（缺 `--`、不认识的选项）是用户没要的诊断，写
    stderr 且退 2（issue #198）。调用方各自指定流，这里只负责措辞。
    """
    return (
        "用法：tavotto run [选项] -- <python> <脚本.py|-m 模块> [脚本自己的参数…]\n"
        "\n"
        "  tavotto run -- python figure.py\n"
        "  tavotto run -- python figure.py --sample A\n"
        "  tavotto run -- /path/to/.venv/bin/python figure.py\n"
        "  tavotto run -- python -m paper.figures.xps --sample A\n"
        "\n"
        "选项：\n"
        "  --project <路径>       Tavotto 用哪个项目目录组织图库（不改变工作目录）\n"
        "  --quiet                不打印 Tavotto 自己的状态行\n"
        "  --status-file <路径>   把机器可读的结果写到这个文件\n"
        "\n"
        "`--` 是必须的：它左边是 Tavotto 的选项，右边整条原样交给 Python。\n"
    )


def stderr_banner(request: RunRequest) -> str:
    """开跑前写到 **stderr** 的那几行（`--quiet` 时不写）。

    **绝不写 stdout**：那是用户程序的。
    """
    return (
        "[Tavotto Run · Beta]\n"
        f"Python: {request.interpreter}\n"
        f"Working directory: {request.cwd}\n"
        f"Target: {request.target_display}\n"
    )


def eprint(text: str, *, quiet: bool = False, stream=None) -> None:
    if quiet:
        return
    print(text, file=stream or sys.stderr, end="" if text.endswith("\n") else "\n", flush=True)
