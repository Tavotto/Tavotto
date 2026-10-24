#!/usr/bin/env python3
"""Windows Lab 的 Mac 端入口（规则全文 docs/rules/ci/windows-lab.md，运维 docs/ci/windows-lab.md）。

日常动词经 `tavotto-win-lab`（非管理员 tavotto-lab，服务器端 ForceCommand 只放行
`agent.py` 登记的动词）执行：

    python3 scripts/winlab/winlab.py doctor
    python3 scripts/winlab/winlab.py status
    python3 scripts/winlab/winlab.py prepare [--ref REF]
    python3 scripts/winlab/winlab.py test TASK [--tests SEL...] [--shard N/M] [--network MODE] [--ref REF]
    python3 scripts/winlab/winlab.py network [--mode all|direct|trojan|hysteria2]
    python3 scripts/winlab/winlab.py reports [--id RUN]
    python3 scripts/winlab/winlab.py clean [--keep N]
    python3 scripts/winlab/winlab.py stop

管理员动词经 `tavotto-win-lab-admin`，只给人工维护用，不进 Claude 的自动放行清单：

    python3 scripts/winlab/winlab.py admin bootstrap [--with-build-tools]
    python3 scripts/winlab/winlab.py admin deploy-agent
    python3 scripts/winlab/winlab.py admin proxy-install

连接参数只在 `~/.ssh/config`；代理凭据只在 `~/.config/tavotto-winlab/proxy.json`（0600），
渲染出的配置直接进服务器的 `C:\\TavottoLab\\secrets\\`，本机不留副本，也不打印。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import agent as lab_agent  # noqa: E402 — 任务登记表的唯一出处（服务器端执行的同一份）

# Windows 上流被重定向（SSH 管道）时退回 ANSI 代码页：中文报文会丢或炸父进程读线程（test_windows_regressions）
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HOST = os.environ.get("TAVOTTO_WINLAB_HOST", "tavotto-win-lab")
ADMIN_HOST = os.environ.get("TAVOTTO_WINLAB_ADMIN_HOST", "tavotto-win-lab-admin")
PROXY_CONF_PATH = Path(
    os.environ.get("TAVOTTO_WINLAB_SECRETS", "~/.config/tavotto-winlab/proxy.json")
).expanduser()
TOOLS_DIR = r"C:\TavottoLab\tools\winlab"
AGENT_CMD = '@"C:\\Program Files\\Python313\\python.exe" -I "%~dp0agent.py" --ssh\r\n'
#: agent.py 复用的结果闭集（agent_verify.py）与它的同目录依赖；改 agent_verify 的 import 时同步这里。
DEPLOYED_CI_MODULES = ("agent_verify.py", "_common.py")
#: 不复用连接：Win32-OpenSSH 在一条复用的连接上只设一次 `SSH_ORIGINAL_COMMAND`，后续会话拿到的是
#: 第一条命令（2026-10-24 在 Lab 上实测：先 doctor 再 status，第二次执行的仍是 doctor）。
#: 保活放宽到 30 s × 10：大下载（首次 clone、pip）把 12 Mbps 链路占满时，保活回包会排队几十秒。
SSH_BASE = [
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=yes",
    "-o",
    "ControlMaster=no",
    "-o",
    "ControlPath=none",
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=10",
]

#: ForceCommand 块：tavotto-lab 只能进 agent，且不许转发、不分配 TTY。
FORCE_BLOCK = (
    "Match User tavotto-lab\r\n"
    "       AuthorizedKeysFile __PROGRAMDATA__/ssh/lab_keys/tavotto-lab.pub\r\n"
    "       ForceCommand C:\\TavottoLab\\tools\\winlab\\agent.cmd\r\n"
    "       AllowTcpForwarding no\r\n"
    "       AllowAgentForwarding no\r\n"
    "       AllowStreamLocalForwarding no\r\n"
    "       GatewayPorts no\r\n"
    "       PermitTunnel no\r\n"
    "       PermitTTY no\r\n"
    "       X11Forwarding no\r\n"
)


# ----------------------------------------------------------------------------- 提交可信度
def git(*args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def trust_decision(
    sha: str, maintainer_email: str, commits: list[tuple[str, str, str]], on_main: bool
) -> tuple[bool, str]:
    """常驻 Lab 只跑维护者审过的代码（docs/rules/ci/windows-lab.md「可信度」）：

    - 已在 origin/main 上（合入过、经过评审）；或
    - origin/main 之后的每个提交，作者与提交者都是本机维护者身份（自己正在写的分支）。
    其余（别人的 PR、fork、不明来源）一律拒绝，去一次性隔离环境。
    """
    if on_main:
        return True, "on origin/main"
    if not commits:
        return False, "commit is neither on origin/main nor ahead of it"
    if len(commits) > 200:
        return False, "more than 200 commits ahead of origin/main"
    foreign = [c for c in commits if c[1] != maintainer_email or c[2] != maintainer_email]
    if foreign:
        return False, f"{len(foreign)} commit(s) not authored+committed by {maintainer_email}"
    return True, f"{len(commits)} maintainer commit(s) ahead of origin/main"


def resolve_trusted(ref: str) -> str:
    for attempt in range(3):  # 本机到 GitHub 偶有 TLS 断连
        if (
            subprocess.run(["git", "-C", str(ROOT), "fetch", "-q", "origin", "main"]).returncode
            == 0
        ):
            break
    else:
        raise SystemExit(
            "git fetch origin main failed 3 times; cannot judge trust without a fresh origin/main"
        )
    sha = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    email = git("config", "user.email")
    on_main = (
        subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", sha, "origin/main"]
        ).returncode
        == 0
    )
    rows = (
        git("log", "--format=%H%x09%ae%x09%ce", f"origin/main..{sha}").splitlines()
        if not on_main
        else []
    )
    commits = [tuple(r.split("\t")) for r in rows if r]
    ok, why = trust_decision(sha, email, commits, on_main)
    if not ok:
        raise SystemExit(
            f"refused: {why}. Untrusted code must run in a disposable environment, not the Lab."
        )
    print(f"[winlab] {sha[:12]} trusted ({why})", file=sys.stderr)
    return sha


# ----------------------------------------------------------------------------- 远端调用
def remote(
    argv: list[str], *, stdin: bytes | None = None, capture: bool = False
) -> subprocess.CompletedProcess:
    cmd = [*SSH_BASE, HOST, shlex.join(argv)]
    return subprocess.run(cmd, input=stdin, capture_output=capture)


RUN_ID_LINE = re.compile(rb"WINLAB_RUN_ID (\d{8}T\d{6}Z-[0-9a-f]{6})")


def remote_json(argv: list[str], *, stdin: bytes | None = None) -> tuple[int, dict]:
    p = remote(argv, stdin=stdin, capture=True)
    m = RUN_ID_LINE.search(p.stderr)
    if p.returncode == 255 and m:
        # 连接断了，但任务在 Lab 上照跑：按 run id 等它的报告，而不是把断线当结论
        return wait_for_report(m.group(1).decode())
    text = p.stdout.decode("utf-8", "replace")
    try:
        start = text.index("{")
        return p.returncode, json.loads(text[start:])
    except ValueError:
        err = p.stderr.decode("utf-8", "replace").strip()
        return p.returncode or 1, {
            "result": "error",
            "summary": f"no JSON from agent (ssh exit {p.returncode}): {err[-400:]}",
        }


def wait_for_report(run_id: str, timeout_s: int = 6 * 3600) -> tuple[int, dict]:
    print(
        f"[winlab] ssh dropped; run {run_id} keeps going on the Lab, polling its report",
        file=sys.stderr,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(30)
        p = remote(["reports", "--id", run_id], capture=True)
        text = p.stdout.decode("utf-8", "replace")
        if p.returncode == 255 or "{" not in text:
            continue  # 还连不上：继续等
        rep = json.loads(text[text.index("{") :])
        if rep.get("run_id") == run_id:
            return lab_agent.av.exit_code_for(rep["result"]), rep
    return 1, {
        "result": "error",
        "summary": f"no report for {run_id} within {timeout_s}s; check `winlab status`",
    }


def show(rc: int, payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return rc


def _is_ancestor(a: str, b: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", a, b], capture_output=True
        ).returncode
        == 0
    )


def make_bundle(sha: str, basis: str | None) -> bytes:
    """`refs/winlab/target`（目标）+ `refs/winlab/main`（origin/main）；Lab 已有的 main 作排除基，只传增量。
    Lab 已经有目标与当前 main 时返回空字节：Lab 端核实提交存在后直接检出。"""
    have_basis = (
        bool(basis)
        and subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{basis}^{{commit}}"], capture_output=True
        ).returncode
        == 0
    )
    main = git("rev-parse", "origin/main")
    if have_basis and _is_ancestor(sha, basis) and _is_ancestor(main, basis):
        return b""
    with tempfile.TemporaryDirectory() as td:
        b = Path(td) / "winlab.bundle"
        # bundle 只收引用名、不收裸 SHA：临时打两个 refs/winlab/*，打完即删
        git("update-ref", "refs/winlab/target", sha)
        git("update-ref", "refs/winlab/main", main)
        try:
            git(
                "bundle",
                "create",
                "-q",
                str(b),
                "refs/winlab/target",
                "refs/winlab/main",
                *([f"^{basis}"] if have_basis else []),
            )
        finally:
            git("update-ref", "-d", "refs/winlab/target")
            git("update-ref", "-d", "refs/winlab/main")
        return b.read_bytes()


def fonts_zip() -> bytes:
    """本机已核 sha256 的字体（`fetch_fonts.py --check` 必须全绿），打成 zip 交给 Lab。"""
    import io
    import zipfile

    fonts = ROOT / "src" / "tavotto" / "resources" / "fonts"
    if not fonts.is_dir():
        main_fonts = (
            Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).parent
            / "src"
            / "tavotto"
            / "resources"
            / "fonts"
        )
        fonts = main_fonts
    chk = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_fonts.py"), "--dest", str(fonts), "--check"],
        capture_output=True,
        text=True,
    )
    if chk.returncode != 0:
        raise SystemExit(f"local fonts fail --check ({fonts}); run scripts/fetch_fonts.py first")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(fonts.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(fonts).as_posix())
    return buf.getvalue()


def cmd_prepare(ref: str) -> int:
    sha = resolve_trusted(ref)
    rc, st = remote_json(["status"])
    if rc != 0 or "lock" not in st:
        return show(rc or 1, st)
    data = make_bundle(sha, st.get("known_main"))
    print(
        f"[winlab] sending bundle {len(data) / 1e6:.1f} MB (basis {str(st.get('known_main'))[:12]})",
        file=sys.stderr,
    )
    rc, out = remote_json(["prepare", "--sha", sha], stdin=data)
    # 第一次（或字体被清掉后）prepare 会卡在 fonts_check：送一次本机核过的字体，再复跑（代码已在，空 bundle）
    if not remote_json(["status"])[1].get("fonts_ok"):
        frc, fout = remote_json(["fonts"], stdin=fonts_zip())
        if frc != 0:
            return show(frc, fout)
        rc, out = remote_json(["prepare", "--sha", sha], stdin=make_bundle(sha, sha))
    return show(rc, out)


def local_doctor() -> dict:
    cfg = subprocess.run(["ssh", "-G", HOST], capture_output=True, text=True).stdout
    kv = dict(line.split(" ", 1) for line in cfg.splitlines() if " " in line)
    key = (
        Path(kv.get("identityfile", "").split()[0]).expanduser() if kv.get("identityfile") else None
    )
    return {
        "ssh_alias": HOST,
        "user": kv.get("user"),
        "strict_host_key_checking": kv.get("stricthostkeychecking"),
        "identity_file_present": bool(key and key.exists()),
        "proxy_secrets_present": PROXY_CONF_PATH.exists(),
        "proxy_secrets_mode_0600": PROXY_CONF_PATH.exists()
        and (PROXY_CONF_PATH.stat().st_mode & 0o077) == 0,
    }


# ----------------------------------------------------------------------------- 管理员动词
def admin_ssh(script: str) -> int:
    """以管理员执行一段 PowerShell。经 `-Command -` 喂 stdin 时，跨行的字符串字面量会让 5.1 一直等续行、
    整段静默不执行，所以先上传成临时 .ps1 再 `-File`（脚本须为 ASCII：5.1 按 ANSI 代码页读无 BOM 文件）。"""
    script.encode("ascii")
    name = f"winlab-admin-{os.getpid()}.ps1"
    remote_path = f"C:\\TavottoLab\\tools\\bootstrap\\{name}"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(
            "$ErrorActionPreference='Stop'\r\n"
            + script.replace("\r\n", "\n").replace("\n", "\r\n"),
            encoding="ascii",
        )
        subprocess.run(
            [
                *SSH_BASE,
                ADMIN_HOST,
                "New-Item -ItemType Directory -Force C:\\TavottoLab\\tools\\bootstrap | Out-Null",
            ],
            check=True,
        )
        admin_scp(p, remote_path)
    try:
        return subprocess.run(
            [
                *SSH_BASE,
                ADMIN_HOST,
                f"powershell -NoProfile -ExecutionPolicy Bypass -File {remote_path}",
            ]
        ).returncode
    finally:
        subprocess.run(
            [*SSH_BASE, ADMIN_HOST, f"Remove-Item -Force {remote_path}"], capture_output=True
        )


def admin_scp(local: Path, remote_path: str) -> None:
    subprocess.run(
        [
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
            str(local),
            f"{ADMIN_HOST}:{remote_path.replace(chr(92), '/')}",
        ],
        check=True,
    )


#: sing-box 官方 Windows zip：没有 Authenticode、国内无镜像、Lab 上取不到 GitHub release 资产。
#: Mac 从 GitHub release 取一次、核这个散列后经 scp 放进 Lab（bootstrap-admin.ps1 再核一次）。
SING_BOX_VERSION = "1.14.3"
SING_BOX_SHA256 = "d8b86ef58936dba9230ec6a57ffa4243933f3c14ef4809828b6c553440ed1518"


def stage_sing_box() -> None:
    name = f"sing-box-{SING_BOX_VERSION}-windows-amd64.zip"
    url = f"https://github.com/SagerNet/sing-box/releases/download/v{SING_BOX_VERSION}/{name}"
    cache = Path("~/.cache/tavotto-winlab").expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    p = cache / name
    if not (p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == SING_BOX_SHA256):
        subprocess.run(
            [
                "curl",
                "-fsSL",
                "--retry",
                "4",
                "--retry-all-errors",
                "--max-time",
                "300",
                "-o",
                str(p),
                url,
            ],
            check=True,
        )
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != SING_BOX_SHA256:
        p.unlink()
        raise SystemExit(f"sing-box zip sha256 mismatch: {got}")
    admin_ssh("New-Item -ItemType Directory -Force C:\\TavottoLab\\cache\\installers | Out-Null")
    admin_scp(p, f"C:\\TavottoLab\\cache\\installers\\{name}")


def admin_bootstrap(with_build_tools: bool) -> int:
    stage_sing_box()
    admin_scp(HERE / "bootstrap-admin.ps1", r"C:\TavottoLab\tools\bootstrap\bootstrap-admin.ps1")
    flag = " -WithBuildTools" if with_build_tools else ""
    return subprocess.run(
        [
            *SSH_BASE,
            ADMIN_HOST,
            f"powershell -NoProfile -ExecutionPolicy Bypass -File C:\\TavottoLab\\tools\\bootstrap\\bootstrap-admin.ps1{flag}",
        ]
    ).returncode


def admin_deploy_agent() -> int:
    admin_ssh(f"New-Item -ItemType Directory -Force {TOOLS_DIR} | Out-Null")
    with tempfile.TemporaryDirectory() as td:
        cmdf = Path(td) / "agent.cmd"
        cmdf.write_bytes(AGENT_CMD.encode("ascii"))
        for src in (
            HERE / "agent.py",
            HERE / "installer-check.ps1",
            HERE / "gui_host.py",
            HERE / "mcp_recorder.py",
            *(ROOT / "scripts" / "ci" / n for n in DEPLOYED_CI_MODULES),
            cmdf,
        ):
            admin_scp(src, f"{TOOLS_DIR}\\{src.name}")
    block = FORCE_BLOCK.replace("'", "''")
    ps = f"""
$ErrorActionPreference='Stop'
icacls '{TOOLS_DIR}' /inheritance:r /grant 'Administrators:(OI)(CI)F' /grant 'SYSTEM:(OI)(CI)F' /grant 'tavotto-lab:(OI)(CI)RX' | Out-Null
$c='C:\\ProgramData\\ssh\\sshd_config'
$t=Get-Content $c -Raw
$t=[regex]::Replace($t, '(?ms)^Match User tavotto-lab\\r?\\n(?:[ \\t]+\\S.*\\r?\\n?)*', '')
$t=$t.TrimEnd() + "`r`n" + '{block}'
Copy-Item $c "$c.prev" -Force
Set-Content $c $t -Encoding ascii -NoNewline
& C:\\Windows\\System32\\OpenSSH\\sshd.exe -t
if ($LASTEXITCODE -ne 0) {{ Copy-Item "$c.prev" $c -Force; throw 'sshd -t failed; restored previous config' }}
Restart-Service sshd
'deployed'
"""
    return admin_ssh(ps)


MODEL_CONF_PATH = Path(
    os.environ.get("TAVOTTO_WINLAB_MODEL_SECRET", "~/.config/tavotto-winlab/model.json")
).expanduser()
MODEL_PROVIDERS = {"deepseek": "https://api.deepseek.com/anthropic"}


def load_model_secret() -> bytes | None:
    """真实模型调用的凭据：本机 0600 文件，经 stdin 交给 Lab 的那一次运行，Lab 不落盘、不打印。"""
    if not MODEL_CONF_PATH.exists():
        print(
            f"missing {MODEL_CONF_PATH}: run `python3 scripts/winlab/winlab.py secret-init deepseek` in your own terminal",
            file=sys.stderr,
        )
        return None
    if MODEL_CONF_PATH.stat().st_mode & 0o077:
        print(f"{MODEL_CONF_PATH} must be chmod 600", file=sys.stderr)
        return None
    return MODEL_CONF_PATH.read_bytes()


def secret_init(provider: str) -> int:
    """在用户自己的终端里录入（getpass 不回显），写 0600 文件；值从不进聊天或命令行参数。"""
    import getpass

    key = getpass.getpass(f"{provider} API key (input hidden): ").strip()
    if not key:
        return 2
    MODEL_CONF_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(MODEL_CONF_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"provider": provider, "base_url": MODEL_PROVIDERS[provider], "api_key": key}, f)
    os.chmod(MODEL_CONF_PATH, 0o600)
    print(f"saved to {MODEL_CONF_PATH} (0600)")
    return 0


def render_proxy_config(secrets: dict, template: dict) -> dict:
    """`${section.key}` 占位符 → 凭据文件里的值（保留类型）。缺值即报错，绝不留占位符。"""

    def sub(v):
        if isinstance(v, dict):
            return {k: sub(x) for k, x in v.items()}
        if isinstance(v, list):
            return [sub(x) for x in v]
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            section, key = v[2:-1].split(".", 1)
            val = secrets.get(section, {}).get(key)
            if val in (None, ""):
                raise KeyError(f"{section}.{key}")
            return int(val) if key == "server_port" else val
        return v

    return sub(template)


def _sub_optional(template: dict, secrets: dict) -> dict:
    # obfs 可选：凭据里没有就先从模板里拿掉，再渲染
    t = json.loads(json.dumps(template))
    if not secrets.get("hysteria2", {}).get("obfs_type"):
        for ob in t["outbounds"]:
            if ob.get("type") == "hysteria2":
                ob.pop("obfs", None)
    return t


def admin_proxy_install() -> int:
    if not PROXY_CONF_PATH.exists():
        print(
            f"missing {PROXY_CONF_PATH}. Create it yourself (chmod 600) with this shape, values from your own client config:\n"
            + json.dumps(
                {
                    "trojan": {"server": "", "server_port": 0, "password": "", "server_name": ""},
                    "hysteria2": {
                        "server": "",
                        "server_port": 0,
                        "password": "",
                        "server_name": "",
                        "obfs_type": "",
                        "obfs_password": "",
                        "alpn": ["h3"],
                    },
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 3
    if PROXY_CONF_PATH.stat().st_mode & 0o077:
        print(f"{PROXY_CONF_PATH} must be chmod 600", file=sys.stderr)
        return 3
    secrets = json.loads(PROXY_CONF_PATH.read_text())
    template = json.loads((HERE / "sing-box.template.json").read_text())
    try:
        cfg = render_proxy_config(secrets, _sub_optional(template, secrets))
    except KeyError as e:
        print(f"proxy secrets missing field {e}", file=sys.stderr)
        return 3
    # 出口身份只存散列：探测时比对，不把 DMIT 地址写进报告
    egress_sha = hashlib.sha256(secrets["trojan"]["server"].encode()).hexdigest()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sing-box.json"
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        admin_scp(p, r"C:\TavottoLab\secrets\sing-box.json")
    ps = f"""
$ErrorActionPreference='Stop'
$cfg='C:\\TavottoLab\\secrets\\sing-box.json'
icacls $cfg /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
& C:\\TavottoLab\\tools\\sing-box\\sing-box.exe check -c $cfg
if ($LASTEXITCODE -ne 0) {{ throw 'sing-box check failed' }}
Set-Content C:\\TavottoLab\\state\\proxy-egress.sha256 '{egress_sha}' -Encoding ascii
Stop-ScheduledTask -TaskName TavottoLab-SingBox -EA SilentlyContinue
Start-ScheduledTask -TaskName TavottoLab-SingBox
Start-Sleep 3
(Get-ScheduledTask -TaskName TavottoLab-SingBox).State
Get-NetTCPConnection -State Listen -LocalPort 7890,7891 -EA SilentlyContinue | % {{ "listen $($_.LocalAddress):$($_.LocalPort)" }}
"""
    return admin_ssh(ps)


#: 腾讯云 CAM 子账号（只授轻量快照 + 只读）：密钥只在本机钥匙串，经环境变量交给 tccli、不进命令行参数。
TENCENT_KEYCHAIN_SERVICE = "tavotto-lab-tencent"
LIGHTHOUSE_REGION = os.environ.get("TAVOTTO_WINLAB_REGION", "ap-guangzhou")


def _keychain(account: str) -> str | None:
    out = subprocess.run(
        ["security", "find-generic-password", "-s", TENCENT_KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def _tccli(*args: str) -> dict:
    sid, skey = _keychain("SecretId"), _keychain("SecretKey")
    if not (sid and skey):
        raise SystemExit(
            f"keychain service {TENCENT_KEYCHAIN_SERVICE!r} needs SecretId and SecretKey "
            f"(security add-generic-password -s {TENCENT_KEYCHAIN_SERVICE} -a SecretId -w)"
        )
    env = {**os.environ, "TENCENTCLOUD_SECRET_ID": sid, "TENCENTCLOUD_SECRET_KEY": skey}
    out = subprocess.run(
        ["tccli", "lighthouse", *args, "--region", LIGHTHOUSE_REGION, "--output", "json"],
        capture_output=True,
        text=True,
        env=env,
    )
    if out.returncode != 0:
        raise SystemExit(f"tccli lighthouse {args[0]} failed: {out.stderr.strip()[-400:]}")
    return json.loads(out.stdout)


def lab_public_ip() -> str:
    cfg = subprocess.run(
        ["ssh", "-G", f"{ADMIN_HOST}-public"], capture_output=True, text=True
    ).stdout
    kv = dict(line.split(" ", 1) for line in cfg.splitlines() if " " in line)
    return kv["hostname"]


def admin_snapshot(name: str) -> int:
    ip = lab_public_ip()
    found = _tccli(
        "DescribeInstances",
        "--Filters",
        json.dumps([{"Name": "public-ip-address", "Values": [ip]}]),
    )
    insts = found.get("InstanceSet") or []
    if len(insts) != 1:
        raise SystemExit(
            f"expected exactly one Lighthouse instance with the Lab's public IP, got {len(insts)}"
        )
    iid = insts[0]["InstanceId"]
    made = _tccli("CreateInstanceSnapshot", "--InstanceId", iid, "--SnapshotName", name)
    sid = made["SnapshotId"]
    print(f"[winlab] snapshot {sid} of {iid} requested", file=sys.stderr)
    deadline = time.monotonic() + 3600
    while time.monotonic() < deadline:
        time.sleep(30)
        snaps = (
            _tccli("DescribeSnapshots", "--SnapshotIds", json.dumps([sid])).get("SnapshotSet") or []
        )
        state = snaps[0].get("SnapshotState") if snaps else "UNKNOWN"
        pct = snaps[0].get("Percent") if snaps else None
        print(f"[winlab] {sid} {state} {pct}%", file=sys.stderr)
        if state == "NORMAL":
            return show(
                0, {"result": "passed", "snapshot_id": sid, "instance_id": iid, "name": name}
            )
        if state in ("CREATE_FAILED", "FAILED"):
            return show(1, {"result": "failed", "snapshot_id": sid, "state": state})
    return show(1, {"result": "error", "summary": f"snapshot {sid} not NORMAL within an hour"})


GUI_SETUP_PS = r"""
$wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
# autologon for tavotto-lab; the password goes into the LSA secret DefaultPassword, never the registry
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class Lsa {
  [StructLayout(LayoutKind.Sequential)] public struct U { public ushort L; public ushort M; public IntPtr B; }
  [StructLayout(LayoutKind.Sequential)] public struct A { public int Len; public IntPtr R; public IntPtr N; public uint At; public IntPtr S; public IntPtr Q; }
  [DllImport("advapi32.dll")] public static extern uint LsaOpenPolicy(IntPtr s, ref A a, int acc, out IntPtr h);
  [DllImport("advapi32.dll")] public static extern uint LsaStorePrivateData(IntPtr h, ref U k, ref U d);
  [DllImport("advapi32.dll")] public static extern uint LsaClose(IntPtr h);
  [DllImport("advapi32.dll")] public static extern int LsaNtStatusToWinError(uint s);
  static U Mk(string s) { var u = new U(); u.B = Marshal.StringToHGlobalUni(s); u.L = (ushort)(s.Length * 2); u.M = (ushort)(u.L + 2); return u; }
  public static int Store(string key, string val) {
    var a = new A(); IntPtr h; uint st = LsaOpenPolicy(IntPtr.Zero, ref a, 0x000F0FFF, out h);
    if (st != 0) return LsaNtStatusToWinError(st);
    var k = Mk(key); var d = Mk(val); st = LsaStorePrivateData(h, ref k, ref d); LsaClose(h);
    Marshal.FreeHGlobal(k.B); Marshal.FreeHGlobal(d.B); return LsaNtStatusToWinError(st);
  }
}
'@
$rc = [Lsa]::Store('DefaultPassword', '__PW__')
if ($rc -ne 0) { throw "LsaStorePrivateData failed: $rc" }
Set-ItemProperty $wl AutoAdminLogon '1'
Set-ItemProperty $wl DefaultUserName 'tavotto-lab'
Set-ItemProperty $wl DefaultDomainName $env:COMPUTERNAME
Remove-ItemProperty $wl DefaultPassword -EA SilentlyContinue
Remove-ItemProperty $wl AutoLogonCount -EA SilentlyContinue
# keep the console desktop drawable: no display timeout, no idle lock
powercfg /change monitor-timeout-ac 0 | Out-Null
$pol = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
Set-ItemProperty $pol InactivityTimeoutSecs 0 -Type DWord
# WebView2 debug port for the Tavotto shells (loopback; same policy the nightly sets per run)
$wv = 'HKLM:\SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments'
New-Item $wv -Force | Out-Null
foreach ($n in 'Tavotto.exe', 'Tavotto beta.exe') {
  New-ItemProperty $wv -Name $n -PropertyType String -Force -Value '--remote-debugging-port=9222 --remote-allow-origins=*' | Out-Null
}
'gui-setup done: reboot to start the autologon session'
"""


def admin_gui_setup() -> int:
    """一次性：tavotto-lab 开机自动登录控制台（密码进 LSA 机密，取自本机钥匙串）、桌面不锁、WebView2 调试端口策略。"""
    pw = subprocess.run(
        ["security", "find-generic-password", "-a", "tavotto-lab", "-s", "tavotto-win-lab", "-w"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not pw or "'" in pw:
        print(
            "tavotto-lab password missing from the keychain (service tavotto-win-lab)",
            file=sys.stderr,
        )
        return 3
    return admin_ssh(GUI_SETUP_PS.replace("__PW__", pw))


# ----------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="winlab", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("doctor")
    sub.add_parser("status")
    p = sub.add_parser("prepare")
    p.add_argument("--ref", default="HEAD")
    t = sub.add_parser("test")
    t.add_argument("task", choices=sorted(lab_agent.TASKS))
    t.add_argument("--tests", nargs="+", default=[])
    t.add_argument("--shard")
    t.add_argument("--network")
    t.add_argument("--package", help="installer：cache\\packages\\ 里的安装包名")
    t.add_argument("--from", dest="frm", help="installer：先装这个旧包再升级")
    t.add_argument("--mode", help="corpus：smoke | all；hosts-live：claude-code | codex-cli")
    t.add_argument("--ref", help="先 prepare 到这个提交（经可信度检查）")
    n = sub.add_parser("network")
    n.add_argument("--mode", default="all", choices=["all", *lab_agent.NETWORK_MODES])
    r = sub.add_parser("reports")
    r.add_argument("--id")
    c = sub.add_parser("clean")
    c.add_argument("--keep", type=int, default=20)
    sub.add_parser("stop")
    si = sub.add_parser("secret-init", help="（在你自己的终端里）录入真实模型调用的 API key")
    si.add_argument("provider", choices=sorted(MODEL_PROVIDERS))
    a = sub.add_parser("admin")
    asub = a.add_subparsers(dest="admin_verb", required=True)
    b = asub.add_parser("bootstrap")
    b.add_argument("--with-build-tools", action="store_true")
    asub.add_parser("deploy-agent")
    asub.add_parser("proxy-install")
    asub.add_parser(
        "gui-setup",
        help="交互会话：tavotto-lab 自动登录 + 不锁屏 + WebView2 调试端口策略（之后需重启）",
    )
    sn = asub.add_parser("snapshot", help="黄金快照（腾讯云轻量；不做回滚 / 删除）")
    sn.add_argument("--name", required=True)
    return ap


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    if ns.verb == "doctor":
        local = local_doctor()
        rc, rem = remote_json(["doctor"])
        return show(rc, {"local": local, "remote": rem})
    if ns.verb in ("status", "stop"):
        return show(*remote_json([ns.verb]))
    if ns.verb == "reports":
        return show(*remote_json(["reports", *(["--id", ns.id] if ns.id else [])]))
    if ns.verb == "clean":
        return show(*remote_json(["clean", "--keep", str(ns.keep)]))
    if ns.verb == "prepare":
        return cmd_prepare(ns.ref)
    if ns.verb == "network":
        return show(*remote_json(["test", "network", "--network", ns.mode]))
    if ns.verb == "test":
        task = lab_agent.TASKS[ns.task]
        network = ns.network or task.networks[0]
        err = lab_agent.validate_test_args(
            task, ns.tests, ns.shard, network, ns.package, ns.frm, ns.mode
        )
        if err:
            print(err, file=sys.stderr)
            return 2
        if ns.ref:
            rc = cmd_prepare(ns.ref)
            if rc != 0:
                return rc
        stdin = None
        if task.needs_model_conf:
            stdin = load_model_secret()
            if stdin is None:
                return 3
        argv = ["test", ns.task, "--network", network]
        if ns.tests:
            argv += ["--tests", *ns.tests]
        if ns.shard:
            argv += ["--shard", ns.shard]
        if ns.package:
            argv += ["--package", ns.package]
        if ns.frm:
            argv += ["--from", ns.frm]
        if ns.mode:
            argv += ["--mode", ns.mode]
        return show(*remote_json(argv, stdin=stdin))
    if ns.verb == "secret-init":
        return secret_init(ns.provider)
    if ns.verb == "admin":
        if ns.admin_verb == "bootstrap":
            return admin_bootstrap(ns.with_build_tools)
        if ns.admin_verb == "deploy-agent":
            return admin_deploy_agent()
        if ns.admin_verb == "gui-setup":
            return admin_gui_setup()
        if ns.admin_verb == "snapshot":
            return admin_snapshot(ns.name)
        return admin_proxy_install()
    return 2


if __name__ == "__main__":
    sys.exit(main())
