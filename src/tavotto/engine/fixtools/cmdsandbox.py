"""`run_command` 第①档的加固沙盒（ADR 0149 §七「#1374 修订」）。

与 `trial.py` 里给「试」用的沙盒不是一回事：试跑的目的是**不改用户的东西**，命令的目的是**能装包**，所以要联网，
而联网 + 任意命令 = 逃逸面比试跑大得多。第①档在「只许写清单内目录」之上再堵**宿主代理逃逸**——
命令不直接写项目，但可以托宿主里一个本来就有权限的进程去写（#1371 的 Codex 评审点名的几条）：

* Linux（bwrap）：`/run` 换空 tmpfs（`/run/user/<uid>/bus` 的 user D-Bus、`systemd-run --user`、docker.sock、`/run/dbus` 全都不可见；
  `/var/run` 若是独立目录同样覆盖，常见情形它是指向 `/run` 的链接），`/tmp` 换空 tmpfs（X11 的 `/tmp/.X11-unix` 路径套接字不可见），
  `--unshare-ipc / --unshare-pid / --unshare-uts`、`--new-session`（命令拿不到控制终端，没法 TIOCSTI 注入按键）、`--die-with-parent`；
  环境里 `DBUS_*` / `XDG_RUNTIME_DIR` / `DISPLAY` / `WAYLAND_DISPLAY` / `SSH_AUTH_SOCK` / `GPG_AGENT_INFO` 等一律不传。
  **已知盲区**：不 `--unshare-net`（要联网装包），所以**抽象命名空间的 unix 套接字**（以 NUL 开头的地址，X11 的 `@/tmp/.X11-unix/X0` 在 Linux 上也是抽象的）
  仍可连。seccomp-bpf 看不到 `connect` 的地址（地址在用户内存里，BPF 读不了指针指向的内容），所以没法「只拦抽象地址」；能做的只有
  整个禁掉 `socket(AF_UNIX, …)`，那会同时弄坏 asyncio / multiprocessing / 许多工具，且 `socketpair` + 数据报 `connect` 还能绕——不值得，**不做**，
  如实写进文档，真要在沙盒外做事走第②档（单独点头）。
* macOS（sandbox-exec）：写清单之外 `deny file-write*`；`deny appleevent-send`；`deny mach-lookup` 然后只放行 `MACH_LOOKUP_ALLOW`；
  `deny process-exec` 指向 `EXEC_DENY` 里的代理入口；`deny network-outbound (remote unix-socket)` 只放 DNS 的 mDNSResponder（ssh-agent / docker.sock / 各种本机服务的套接字都不通）；
  `deny signal` 只放 self / children / same-sandbox（不能给别的进程发信号；写成 `(target others)` 实测拦不住同用户进程）。

纯标准库；不 import app。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from . import trial

# --------------------------------------------------------------------------- 环境变量
#: 子进程环境里一律不传的变量（宿主代理的入口：总线、显示、认证代理）。键名精确匹配（不分大小写）
AGENT_ENV_VARS = frozenset(
    {
        "DBUS_SESSION_BUS_ADDRESS",
        "DBUS_SYSTEM_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GPG_AGENT_INFO",
        "GNOME_KEYRING_CONTROL",
        "SESSION_MANAGER",
        "XDG_SESSION_ID",
        "XDG_SESSION_TYPE",
        "KRB5CCNAME",
        "DOCKER_HOST",
        "XPC_SERVICE_NAME",
        "__CFBundleIdentifier",
    }
)

_SECRET_ENV = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|_PWD$|CREDENTIAL|AUTH|COOKIE|SIGNATURE|PRIVATE)",
    re.IGNORECASE,
)


def clean_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """子进程的环境：去掉密钥类变量（名字带 KEY / TOKEN / SECRET / PASSWORD / AUTH 等）与宿主代理入口变量，其余原样。"""
    src = os.environ if base is None else base
    return {
        k: v
        for k, v in src.items()
        if not _SECRET_ENV.search(k) and k.upper() not in AGENT_ENV_VARS
    }


# --------------------------------------------------------------------------- macOS
#: `deny mach-lookup` 之后放行的服务。**来源**：Apple 自带的 `/System/Library/Sandbox/Profiles/system.sb`——每个 App 沙盒都继承的基础块
#: 与 `(system-network)` 块——取其中**只读查询**的最小子集，另加 Python 的 `_scproxy` 读系统代理要的 configd（国内常见的系统代理 / TUN 环境下 pip 靠它）。
#: 刻意**不放**：`cfprefsd`（能代写用户偏好 = 宿主代写）、`launchservicesd` / `lsd`（打开应用）、`pasteboard`、`securityd` / `SecurityServer`（钥匙串）、
#: `coreservices.*`、`com.apple.tccd`、任何 `*.xpc` 应用服务。先严再按用例放。
MACH_LOOKUP_ALLOW: tuple[str, ...] = (
    "com.apple.dnssd.service",  # DNS（getaddrinfo → mDNSResponder）；system.sb (system-network)
    "com.apple.SystemConfiguration.configd",  # 读系统代理 / 网络配置（SCDynamicStoreCopyProxies）
    "com.apple.SystemConfiguration.DNSConfiguration",  # system.sb (system-network) 同系
    "com.apple.SystemConfiguration.SCNetworkReachability",  # system.sb (system-network)
    "com.apple.networkd",  # system.sb (system-network)
    "com.apple.trustd",  # 证书链校验（curl / git / 系统 TLS 客户端）；system.sb 基础块
    "com.apple.trustd.agent",  # 同上
    "com.apple.system.opendirectoryd.libinfo",  # getpwuid / getlogin 等只读目录查询；system.sb 基础块
    "com.apple.system.opendirectoryd.membership",  # 同上
    "com.apple.system.DirectoryService.libinfo_v1",  # 同上
)

#: 不许 exec 的代理入口（命中硬拦表也会被拒；这里是沙盒内的第二道，防止拼路径 / 换名绕过硬拦表的名字匹配）
EXEC_DENY: tuple[str, ...] = (
    "/usr/bin/osascript",
    "/usr/bin/open",
    "/bin/launchctl",
    "/usr/bin/security",
    "/usr/bin/defaults",
    "/usr/bin/crontab",
    "/usr/bin/at",
    "/usr/bin/sudo",
    "/usr/bin/su",
    "/usr/bin/shortcuts",
    "/usr/bin/automator",
    "/usr/bin/systemd-run",  # Linux 的同类入口，写在这里只为表一致（macOS 上不存在，deny 无副作用）
    "/bin/systemd-run",
)

#: 允许连的 unix 套接字（DNS）。其余本机服务套接字（ssh-agent / docker / 各种 IPC）一律不通
UNIX_SOCKET_ALLOW: tuple[str, ...] = ("/private/var/run/mDNSResponder",)


def _sbpl_quote(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(
    scratch: str, extra_writable: Sequence[str] = (), hidden: Sequence[str] = ()
) -> str:
    """加固的 sandbox-exec profile 文本。可写：命令自己的临时目录 + `extra_writable`（环境前缀、pip / conda 缓存）+ `/dev/null`；其余一切只读。
    `hidden`：凭据位置（`readscope.DENY_HOME_RELATIVE` 里实际存在的）——联网 + 任意命令 = 可以外传，所以连读都拒。"""
    dirs = [os.path.realpath(scratch), *(os.path.realpath(d) for d in extra_writable)]
    subpaths = " ".join(f"(subpath {_sbpl_quote(d)})" for d in dict.fromkeys(dirs))
    lookups = " ".join(f'(global-name "{n}")' for n in MACH_LOOKUP_ALLOW)
    execs = " ".join(f"(literal {_sbpl_quote(p)})" for p in EXEC_DENY)
    socks = " ".join(f"(path {_sbpl_quote(p)})" for p in UNIX_SOCKET_ALLOW)
    hide = "".join(
        f"(deny file-read* (subpath {_sbpl_quote(os.path.realpath(h))}))" for h in hidden
    )
    return (
        "(version 1)(allow default)"
        f"{hide}"
        "(deny file-write*)"
        f'(allow file-write* {subpaths} (literal "/dev/null"))'
        "(deny appleevent-send)"
        f"(deny mach-lookup)(allow mach-lookup {lookups})"
        f"(deny process-exec {execs})"
        f"(deny network-outbound (remote unix-socket))(allow network-outbound (remote unix-socket {socks}))"
        "(deny signal)(allow signal (target self) (target children) (target same-sandbox))"
    )


# --------------------------------------------------------------------------- Linux
#: 重新放回 `/run` 空 tmpfs 里的只读文件：`/etc/resolv.conf` 在 systemd-resolved / resolvconf / NetworkManager 的机器上是指向 `/run` 的链接，
#: 不放回去沙盒里没法解析域名。只放文件，不放整个目录（目录里有 resolved 的 varlink 套接字、NM 的 private 套接字）
RUN_READONLY_FILES: tuple[str, ...] = (
    "/run/systemd/resolve/stub-resolv.conf",
    "/run/systemd/resolve/resolv.conf",
    "/run/resolvconf/resolv.conf",
    "/run/NetworkManager/resolv.conf",
)


def bwrap_args(
    tool: str,
    scratch: str,
    extra_writable: Sequence[str] = (),
    *,
    readonly_rebind: Sequence[str] = (),
    hidden: Sequence[str] = (),
    var_run_is_dir: bool | None = None,
) -> list[str]:
    """加固的 bwrap 前缀。`readonly_rebind`：`/tmp` 换空 tmpfs 之后要重新只读挂回来的路径（项目目录恰好在 /tmp 下时）。
    `var_run_is_dir`：`/var/run` 是不是独立目录（None = 现场看；常见情形是指向 /run 的链接，不用再盖一层）。"""
    if var_run_is_dir is None:
        var_run_is_dir = os.path.isdir("/var/run") and not os.path.islink("/var/run")
    args = [
        tool,
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/run",
    ]  # fmt: skip
    if var_run_is_dir:
        args += ["--tmpfs", "/var/run"]
    for f in RUN_READONLY_FILES:
        args += ["--ro-bind-try", f, f]
    for d in readonly_rebind:
        real = os.path.realpath(d)
        args += ["--ro-bind", real, real]
    for h in hidden:  # 凭据位置：目录换空 tmpfs，文件盖成 /dev/null
        real = os.path.realpath(h)
        args += ["--tmpfs", real] if os.path.isdir(real) else ["--ro-bind", "/dev/null", real]
    for d in dict.fromkeys(
        [os.path.realpath(scratch), *(os.path.realpath(x) for x in extra_writable)]
    ):
        args += ["--bind", d, d]
    args += [
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--new-session",
        "--die-with-parent",
    ]
    return args


# --------------------------------------------------------------------------- 探测与前缀
_PROBE: dict[tuple[str, str], bool] = {}


def sandbox_kind() -> tuple[str, str] | None:
    """这台机器上可用的沙盒 `(名字, 工具路径)`，没有回 None（Windows 永远是 None；测试里 monkeypatch 它）。"""
    for kind in ("macos-sandbox-exec", "bwrap"):
        tool = trial._find_tool(kind)
        if tool and _probe(kind, tool):
            return kind, tool
    return None


def _args(
    kind: str,
    tool: str,
    scratch: str,
    extra: Sequence[str],
    rebind: Sequence[str],
    hidden: Sequence[str] = (),
) -> list[str]:
    if kind == "macos-sandbox-exec":
        return [tool, "-p", macos_profile(scratch, extra, hidden)]
    return bwrap_args(tool, scratch, extra, readonly_rebind=rebind, hidden=hidden)


def _probe(kind: str, tool: str) -> bool:
    """加固后的沙盒真能起，且里面能跑 Python（嵌套沙盒、用户命名空间被禁、`--unshare-pid` 被拒都会在这里现形）。同一个工具只探一次。"""
    key = (kind, tool)
    if key not in _PROBE:
        try:
            probe_dir = str(trial._make_trial_dir("cmd-probe"))
        except OSError:
            _PROBE[key] = False
            return False
        try:
            r = subprocess.run(  # noqa: S603 - 固定的系统沙盒命令
                [*_args(kind, tool, probe_dir, (), ()), sys.executable, "-c", "pass"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=20,
                check=False,
            )
            _PROBE[key] = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            _PROBE[key] = False
        finally:
            trial._remove_tree(Path(probe_dir))
    return _PROBE[key]


def prefix(
    kind: str,
    tool: str,
    scratch: Path,
    extra_writable: Sequence[str] = (),
    *,
    project: str | None = None,
    hidden: Sequence[str] = (),
) -> list[str]:
    """命令前缀。`project`：项目根（bwrap 下若在 /tmp 里，`/tmp` 换空 tmpfs 会把它盖没，要只读挂回来）。"""
    rebind: list[str] = []
    if project and kind == "bwrap":
        real = os.path.realpath(project)
        if real == "/tmp" or real.startswith("/tmp/"):
            rebind.append(real)
    return _args(kind, tool, str(scratch), extra_writable, rebind, hidden)
