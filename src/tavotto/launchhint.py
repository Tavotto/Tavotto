"""浏览器模式启动时终端上给人看的那几行：开不开浏览器、最后打印什么。

两个真实踩到的坑（Ubuntu 20.04 经 SSH，2026-10-22）：

* **SSH 里自动开浏览器开出了 lynx**。没有图形会话时 `webbrowser.open` 不报错，而是退到
  `www-browser` / lynx / w3m 这类终端浏览器——它占满当前终端、跑不了 JS，用户看到一个空白页，
  退出它之后才看见上面早已打印的登录地址。所以没有图形会话时根本不调 `webbrowser.open`。
* **带 `#dnonce=` 的登录地址被埋在日志上面**。最后几行原本是 werkzeug 的红色「development
  server」警告和不带口令的 `Running on …`——最像链接的那条恰恰登录不了。所以登录地址放在服务
  已 listen 之后、作为最后一块打印，服务器上再把要在自己电脑上跑的 `ssh -L` 命令一并填好。

纯标准库、不碰网络与文件：判据只看传进来的环境变量与平台名，便于单测。
"""

from __future__ import annotations

import getpass
import os
import sys
from collections.abc import Mapping

# OpenSSH 的 sshd 给会话设的三个变量；任一存在就是「人坐在另一台机器前」
_SSH_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")


def over_ssh(env: Mapping[str, str] = os.environ) -> bool:
    return any(env.get(k) for k in _SSH_VARS)


def can_open_browser(env: Mapping[str, str] = os.environ, platform: str = sys.platform) -> bool:
    """这个进程开得出一个**用户看得见的图形浏览器**吗？

    * 经 SSH 登录：不开。即使有 X 转发（`DISPLAY=localhost:10.0`），开出来的也是服务器上的浏览器经
      隧道慢慢画；macOS / Windows 经 SSH 开的则是服务器屏幕上的窗口，人不在那儿。
    * macOS / Windows 本机：开（系统自带打开 URL 的机制，不会退到终端浏览器）。
    * 其余（Linux / BSD）：有 `DISPLAY` 或 `WAYLAND_DISPLAY` 才开；都没有就是纯终端，
      `webbrowser` 只剩 lynx / w3m 可选。
    """
    if over_ssh(env):
        return False
    if platform == "darwin" or platform.startswith("win"):
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def ssh_forward_command(
    port: int, env: Mapping[str, str] = os.environ, user: str | None = None
) -> str:
    """在用户自己电脑上要跑的端口转发命令。

    服务器地址取 `SSH_CONNECTION` 的第三段（客户端连进来时用的那个服务器地址，客户端一定连得上它），
    sshd 不在 22 端口时补 `-p`。不是经 SSH 登录的（本机控制台上跑）就留占位符。

    局限：经 NAT / 跳板机 / 端口映射登录时，第三段是**服务器侧 socket 的地址**（常是内网地址），
    用户电脑未必连得上；服务器也拿不到客户端原本敲的主机名。所以这里只填「检测到的 user@地址」
    （直连场景下它可直接复制），并由 `ssh_host_hint` 在命令下面提醒用户换成自己平时登录用的那个。
    """
    if user is None:
        try:
            user = getpass.getuser()
        except Exception:  # noqa: BLE001 — 拿不到用户名只是少填一格，不该让启动失败
            user = "<用户名>"
    host = "<这台服务器的地址>"
    ssh_port = "22"
    parts = (env.get("SSH_CONNECTION") or "").split()
    if len(parts) == 4:
        host, ssh_port = parts[2], parts[3]
    opt = "" if ssh_port == "22" else f" -p {ssh_port}"
    return f"ssh -N -L {port}:127.0.0.1:{port}{opt} {user}@{host}"


def ssh_host_hint(port: int, env: Mapping[str, str] = os.environ, user: str | None = None) -> str:
    """跟在 `ssh -L` 命令后面的一句提示：命令里的 `user@地址` 只是服务器侧看到的，可能要换。

    横幅的服务器分支和「端口复用」分支都用它，措辞只此一份。
    """
    target = ssh_forward_command(port, env, user).rsplit(" ", 1)[1]
    return (
        "  （如果你平时用别的地址、域名或 ~/.ssh/config 里的别名登录这台机器，"
        f"把命令里的 {target} 换成你登录时用的那个。）"
    )


def ready_banner(url: str, port: int, *, opened: bool, env: Mapping[str, str] = os.environ) -> str:
    """服务已经在 listen 之后打印的最后一块。`url` 是带 `#dnonce=` 的登录地址，单独占一行，原样可复制。

    三种：自动开了浏览器 / 本机图形会话里显式 `--no-browser` / 服务器（经 SSH 或没有图形会话）。
    """
    if opened:
        lines = [
            "* Tavotto 已启动，正在浏览器中打开。",
            "  没有自动打开的话，在浏览器里打开：",
            f"    {url}",
        ]
    elif can_open_browser(env):
        lines = ["* Tavotto 已启动。在浏览器里打开：", f"    {url}"]
    else:
        lines = [
            "* Tavotto 已启动。这台机器没有图形界面，请在你自己的电脑上使用：",
            "  1. 在你自己的电脑上新开一个终端，运行（保持它开着）：",
            f"    {ssh_forward_command(port, env)}",
            ssh_host_hint(port, env),
            "  2. 在你自己电脑的浏览器里打开：",
            f"    {url}",
        ]
    lines.append("  按 Ctrl+C 停止 Tavotto。")
    return "\n".join(lines)
