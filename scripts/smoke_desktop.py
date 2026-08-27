#!/usr/bin/env python3
"""桌面 sidecar 的端到端冒烟：对**最终用户拿到的产物**做验收，不是对源码。

    python scripts/smoke_desktop.py --sidecar dist/Tavotto/Tavotto
    python scripts/smoke_desktop.py --sidecar .venv/bin/tavotto   # 源码模式

走的是 Tauri 壳同一套协议（stdin 首行 JSON 送 nonce、握手文件、stdin EOF =
父进程退出），依次验证：

1. 动态端口 + 握手文件（ready/port/pid，无密钥）
2. 未认证请求 401；错误 nonce 403；正确 nonce → cookie；nonce 重放 403
3. Host/Origin 校验
4. 打开 examples/figures 项目 → 素材扫描
5. 渲染控制面：产物里带着 tavotto-workerd，且渲染**真的走了它**
6. 参数化渲染 + PDF/PNG 导出（渲染环境缺 matplotlib 时如实跳过并报告）
7. 关 stdin（模拟壳退出）→ sidecar 限时内自行退出，且不留 worker 孤儿

数据/配置目录全程隔离在临时目录，绝不碰真实用户数据。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Windows 上 stdout 被重定向成管道时会退回系统区域编码（cp1252/cp936），
# 打印带中文或 ✓ 的进度就会 UnicodeEncodeError——冒烟明明通过却以非零退出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
NONCE = "smoke-" + os.urandom(16).hex()

sys.path.insert(0, str(ROOT / "scripts"))
# 「按命令行内容找残留 worker」两个冒烟脚本共用**同一把尺**，别再写第二份。
# 这里另有一条更精确的判据（`_descendants` 的 pid 快照），两条互补。
from smoke_app import _leftover_workers  # noqa: E402


def _descendants(pid: int) -> set[int]:
    """`pid` 的**全部后代**（递归）。

    必须趁父进程**还活着**时问：进程一终止，它还活着的孩子立刻被重新挂到
    init/launchd（PID 1）名下，进程树当场断开。这也正是
    `pgrep -P <已退出的 pid>` 恒等于空的原因。

    比按命令行扫更准：sidecar 起的 `tavotto-workerd` 是
    `Popen([exe])`——命令行里没有本次运行的任何标识，按名字扫会把用户自己
    开着的另一个 Tavotto 算成残留。假报一次，下次真出问题时这条提示就已经
    被学会无视了。
    没有 `ps` 的平台（Windows）回空集：那儿由 `_leftover_workers` 兜底。
    """
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            child, parent = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        kids.setdefault(parent, []).append(child)
    seen: set[int] = set()
    stack = [pid]
    while stack:
        for child in kids.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                      # 还在，只是不归我们管
    except OSError:
        return False
    return True


def fail(msg: str) -> None:
    print(f"✗ {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"✓ {msg}")


def is_packaged(exe: Path) -> bool:
    """这是不是 PyInstaller 打出来的真产物（旁边有 _internal/）。

    与 smoke_app.py 找内置 runtime 清单用的是同一条线索，别再发明第二种判法。
    源码模式（`--sidecar .venv/bin/tavotto`）下 workerd 只是「开发机上建过就
    有」，那时缺失只警告；真产物缺失一律判失败。
    """
    return (exe.parent / "_internal").is_dir()


def check_control_plane(port: int, cookie: str, packaged: bool,
                        rendered: bool) -> None:
    """渲染控制面必须是 workerd（Rust supervisor），而且渲染真的走了它。

    回退到 Python 池是**静默**的（`pool._new_worker()` 的 except 分支），所以
    「二进制没打进去」「打进去了但起不来」这两件事在界面上完全看不出来——
    功能一样不缺，只是慢。这一步就是把它们变成一次可见的失败。
    """
    st, env = request(port, "GET", "/api/engine/environment", cookie=cookie)
    assert st == 200, f"读渲染环境失败: {st} {env}"
    cp = env.get("control_plane") or {}
    selected, sessions = cp.get("selected"), cp.get("sessions") or []

    if selected != "workerd":
        msg = (f"渲染控制面是 {selected!r} 而不是 workerd——"
               "产物里没有 tavotto-workerd，渲染会静默回退到 Python 渲染池")
        if packaged:
            fail(msg)
        print(f"⚠ {msg}；源码模式下先跑 "
              "cargo build --release --manifest-path workerd/Cargo.toml")
        return
    ok(f"渲染控制面: workerd（{cp.get('path')}）")

    if not rendered:
        print("⚠ 本次没跑渲染（环境缺 matplotlib）：只验到「带着 workerd」，"
              "没验到「渲染真走了它」")
        return
    if "workerd" not in sessions:
        fail(f"渲染跑完了，但会话走的是 Python 渲染池: {sessions}"
             "（workerd 起不来？看数据目录 cache/workerd.log）")
    ok(f"渲染会话确实跑在 workerd 上: {sessions}")


def request(port: int, method: str, path: str, body: dict | None = None,
            cookie: str | None = None, host: str | None = None,
            origin: str | None = None) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    try:
        payload = json.dumps(body).encode() if body is not None else None
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        if origin:
            conn.putheader("Origin", origin)
        if cookie:
            conn.putheader("Cookie", cookie)
        if payload is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(payload)))
        conn.endheaders(payload)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {"_raw": raw[:200].decode("utf-8", "replace")}
        set_cookie = resp.getheader("Set-Cookie")
        if set_cookie:
            data["_set_cookie"] = set_cookie
        return resp.status, data
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sidecar", required=True,
                    help="sidecar 可执行文件（dist/Tavotto/Tavotto 或 .venv/bin/tavotto）")
    ap.add_argument("--figures", default=str(ROOT / "examples" / "figures"))
    args = ap.parse_args()

    exe = Path(args.sidecar).resolve()
    if not exe.is_file():
        fail(f"sidecar 不存在: {exe}")

    tmp = Path(tempfile.mkdtemp(prefix="tavotto-smoke-"))
    handshake = tmp / "handshake.json"
    env = {**os.environ,
           "TAVOTTO_DATA_DIR": str(tmp / "data"),
           "TAVOTTO_CONFIG_DIR": str(tmp / "config"),
           # CI / 冒烟绝不产生真实的产品事件
           "TAVOTTO_NO_TELEMETRY": "1",
           "TAVOTTO_DESKTOP_HANDSHAKE": str(handshake)}
    env.pop("TAVOTTO_DESKTOP_NONCE", None)

    proc = subprocess.Popen(
        [str(exe), "--desktop-sidecar"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, cwd=str(tmp))
    try:
        # 凭据走 stdin 首行（与 Tauri 壳同一协议）；管道保持打开
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"nonce": NONCE}).encode() + b"\n")
        proc.stdin.flush()

        deadline = time.time() + 60
        hs = None
        while time.time() < deadline:
            if handshake.is_file():
                try:
                    hs = json.loads(handshake.read_text(encoding="utf-8"))
                    break
                except ValueError:
                    pass
            if proc.poll() is not None:
                fail(f"sidecar 提前退出: {proc.returncode}")
            time.sleep(0.1)
        if hs is None:
            fail("等待握手超时")
        if not hs.get("ready"):
            fail(f"sidecar 报告启动失败: {hs.get('error')}")
        port = hs["port"]
        if NONCE in handshake.read_text(encoding="utf-8"):
            fail("握手文件里泄漏了 nonce")
        ok(f"握手完成: 端口 {port}（动态分配），pid {hs['pid']}，无密钥")

        # ---- 认证 ----
        st, body = request(port, "GET", "/api/panels")
        assert st == 401, f"未认证应 401，得到 {st}"
        ok("未认证请求 → 401")
        st, _ = request(port, "GET", "/api/version", host="evil.example:80")
        assert st == 403, f"异常 Host 应 403，得到 {st}"
        ok("异常 Host → 403")
        st, _ = request(port, "POST", "/api/desktop/bootstrap", {"nonce": "wrong"})
        assert st == 403, f"错误 nonce 应 403，得到 {st}"
        st, body = request(port, "POST", "/api/desktop/bootstrap", {"nonce": NONCE})
        assert st == 200 and "_set_cookie" in body, f"bootstrap 失败: {st} {body}"
        cookie = body["_set_cookie"].split(";", 1)[0]
        st, _ = request(port, "POST", "/api/desktop/bootstrap", {"nonce": NONCE})
        assert st == 403, f"nonce 重放应 403，得到 {st}"
        ok("bootstrap：错误 nonce 403 / 正确换 cookie / 重放 403")

        # ---- 项目与素材 ----
        st, body = request(port, "POST", "/api/projects/open",
                           {"path": args.figures}, cookie=cookie)
        assert st == 200, f"打开项目失败: {st} {body}"
        st, body = request(port, "GET", "/api/panels", cookie=cookie)
        assert st == 200 and body.get("panels"), f"素材扫描失败: {st}"
        panels = body["panels"]
        ok(f"项目打开 + 素材扫描: {len(panels)} 个面板")

        # ---- 渲染环境 → 参数化渲染 + 导出 ----
        st, envst = request(port, "GET", "/api/engine/environment", cookie=cookie)
        script_panel = next((p for p in panels if p.get("script")), None)
        rendered = False
        if not envst.get("ok"):
            print(f"⚠ 渲染环境不可用（{envst.get('reason') or 'matplotlib 缺失'}）："
                  "跳过参数化渲染/导出验证——这是环境限制，不是通过")
        elif script_panel is None:
            print("⚠ 示例项目里没有可参数化面板：跳过渲染验证")
        else:
            rendered = True
            st, body = request(port, "POST", "/api/engine/render",
                               {"id": script_panel["id"], "patches": []},
                               cookie=cookie)
            assert st == 200 and body.get("manifest"), f"渲染失败: {st} {body}"
            ok(f"参数化渲染: {script_panel['id']}（manifest "
               f"{len(body['manifest'].get('elements', []))} 元素）")

            w = script_panel.get("native_w_mm", 80)
            h = script_panel.get("native_h_mm", 60)
            st, body = request(port, "POST", "/api/export", {
                "page_w_mm": w + 10, "page_h_mm": h + 10, "dpi": 200,
                "formats": ["png", "pdf"], "stem": "smoke",
                "objects": [{"type": "panel", "id": script_panel["id"],
                             "x_mm": 5, "y_mm": 5, "w_mm": w, "h_mm": h}],
            }, cookie=cookie)
            assert st == 200 and len(body.get("files", [])) == 2, \
                f"导出失败: {st} {body}"
            for f in body["files"]:
                p = Path(body["export_dir"]) / f["name"]
                assert p.is_file() and p.stat().st_size > 0, f"导出文件缺失: {p}"
            ok(f"导出 PDF+PNG: {[f['name'] for f in body['files']]}")

        # ---- 渲染控制面（放在渲染之后问：那时池里才有真会话）----
        check_control_plane(port, cookie, is_packaged(exe), rendered)

        # ---- 退出：关 stdin = 壳没了 ----
        # 关停**之前**把整棵后代快照下来（见 `_descendants`：父进程一死树就断）。
        # 覆盖的不只是 python worker，还有 sidecar 起的 tavotto-workerd——
        # 会话都正常关了、supervisor 自己没退出，同样是孤儿。
        descendants = _descendants(proc.pid)
        proc.stdin.close()
        deadline = time.time() + 15
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.2)
        assert proc.poll() is not None, "关 stdin 后 sidecar 15 秒内未退出"
        ok(f"stdin EOF → sidecar 已退出（{proc.returncode}）")
        assert not handshake.exists(), "退出后握手文件未清理"
        ok("握手文件已清理")

        # worker 孤儿检查：sidecar 的子进程应全部随之消失。
        #
        # **不能用 `pgrep -P <sidecar pid>`**：进程一终止，它还活着的子进程
        # 立刻被系统重新挂到 init/launchd（PID 1）名下——PPID 在父进程死亡
        # 的那一刻就变了，不是等到查询那一刻。而这段代码跑在
        # `proc.poll() is not None` 之后，也就是父进程**必然已经退出**。
        # 于是不管有没有真的泄漏，这条断言都查不到任何东西，恒真。
        # 空转的门禁比没有门禁更坏——它还在报平安。
        # 改成两条互补的判据：
        #   ① 退出前快照下来的后代，事后逐个查存活——精确到 pid，
        #      连没有任何命令行特征的 tavotto-workerd 也盖得住；
        #   ② 按命令行内容做全局扫描，兜住父子关系没记全的那些
        #      （与 `smoke_app._leftover_workers` 同一把尺）。
        survivors = sorted(p for p in descendants if _alive(p))
        assert not survivors, f"sidecar 退出后仍活着的后代进程: {survivors}"
        leftover = _leftover_workers(tmp)
        assert not leftover, f"发现孤儿 worker 子进程: {leftover}"
        ok(f"无孤儿子进程（退出前快照 {len(descendants)} 个后代，全部已退出）")

        print("\n桌面 sidecar 冒烟全部通过 ✔")
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
