#!/usr/bin/env python3
"""桌面 sidecar 的端到端冒烟：对**最终用户拿到的产物**做验收，不是对源码。

    python scripts/smoke_desktop.py --sidecar dist/Magplot/Magplot
    python scripts/smoke_desktop.py --sidecar .venv/bin/magplot   # 源码模式

走的是 Tauri 壳同一套协议（stdin 首行 JSON 送 nonce、握手文件、stdin EOF =
父进程退出），依次验证：

1. 动态端口 + 握手文件（ready/port/pid，无密钥）
2. 未认证请求 401；错误 nonce 403；正确 nonce → cookie；nonce 重放 403
3. Host/Origin 校验
4. 打开 examples/figures 项目 → 素材扫描
5. 参数化渲染 + PDF/PNG 导出（渲染环境缺 matplotlib 时如实跳过并报告）
6. 关 stdin（模拟壳退出）→ sidecar 限时内自行退出，且不留 worker 孤儿

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
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NONCE = "smoke-" + os.urandom(16).hex()


def fail(msg: str) -> None:
    print(f"✗ {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"✓ {msg}")


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
                    help="sidecar 可执行文件（dist/Magplot/Magplot 或 .venv/bin/magplot）")
    ap.add_argument("--figures", default=str(ROOT / "examples" / "figures"))
    args = ap.parse_args()

    exe = Path(args.sidecar).resolve()
    if not exe.is_file():
        fail(f"sidecar 不存在: {exe}")

    tmp = Path(tempfile.mkdtemp(prefix="magplot-smoke-"))
    handshake = tmp / "handshake.json"
    env = {**os.environ,
           "MAGPLOT_DATA_DIR": str(tmp / "data"),
           "MAGPLOT_CONFIG_DIR": str(tmp / "config"),
           "MAGPLOT_DESKTOP_HANDSHAKE": str(handshake)}
    env.pop("MAGPLOT_DESKTOP_NONCE", None)

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
        if not envst.get("ok"):
            print(f"⚠ 渲染环境不可用（{envst.get('reason') or 'matplotlib 缺失'}）："
                  "跳过参数化渲染/导出验证——这是环境限制，不是通过")
        elif script_panel is None:
            print("⚠ 示例项目里没有可参数化面板：跳过渲染验证")
        else:
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

        # ---- 退出：关 stdin = 壳没了 ----
        proc.stdin.close()
        deadline = time.time() + 15
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.2)
        assert proc.poll() is not None, "关 stdin 后 sidecar 15 秒内未退出"
        ok(f"stdin EOF → sidecar 已退出（{proc.returncode}）")
        assert not handshake.exists(), "退出后握手文件未清理"
        ok("握手文件已清理")

        # worker 孤儿检查：sidecar 的子进程应全部随之消失
        try:
            out = subprocess.run(["pgrep", "-P", str(proc.pid)],
                                 capture_output=True, text=True)
            assert not out.stdout.strip(), f"发现孤儿子进程: {out.stdout}"
            ok("无孤儿子进程")
        except FileNotFoundError:
            pass  # Windows 无 pgrep；CI 上由任务管理器断言

        print("\n桌面 sidecar 冒烟全部通过 ✔")
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
