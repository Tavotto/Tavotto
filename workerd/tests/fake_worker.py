#!/usr/bin/env python3
"""说 worker 协议 v1 的假 worker——**只给 workerd 的 cargo test 用**。

真 worker（`src/tavotto/engine/worker.py`）要科学栈，起一次几秒钟，而这里要验的
全是 supervisor 自己的行为：队列合并、超时强杀、代序隔离、回显错位。用真 worker
既慢又会把「是 supervisor 错了还是 matplotlib 慢了」搅在一起。

信封与响应形状严格照 `docs/adr/0003-worker-protocol-v1.md`；下面那些 --flag
是**故意的失灵开关**，每一个都对应一条 supervisor 必须接住的失败路径。
"""

import argparse
import json
import os
import sys
import time


def reply(req, extra=None, *, wrong_request_id=False, bad_version=False):
    out = {
        "ok": True,
        "protocol_version": 2 if bad_version else 1,
        "request_id": "r-别人的" if wrong_request_id else req.get("request_id"),
    }
    for key in ("worker_generation", "render_revision", "canonical_patch_hash"):
        if key in req:
            out[key] = req[key]
    out.update(extra or {})
    return out


def error(req, code, message, retryable=False):
    return {
        "ok": False,
        "protocol_version": 1,
        "request_id": req.get("request_id"),
        "worker_generation": req.get("worker_generation"),
        "render_revision": req.get("render_revision"),
        "error": {"code": code, "retryable": retryable, "message": message, "traceback": ""},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stems", default="Fig1,Fig2")
    ap.add_argument("--sleep-ms", type=float, default=0.0, help="每条 render/export 处理前睡多久")
    ap.add_argument(
        "--first-sleep-ms",
        type=float,
        default=0.0,
        help="只有第一条 render/export 睡（用来把后续请求逼进队列）",
    )
    ap.add_argument("--hang", action="store_true", help="render 永不返回（模拟死循环脚本）")
    ap.add_argument(
        "--hang-handshake", action="store_true", help="连 ping 都不回（模拟起来了但不说话的解释器）"
    )
    ap.add_argument("--wrong-request-id", action="store_true")
    ap.add_argument("--bad-protocol-version", action="store_true")
    ap.add_argument("--garbage", action="store_true", help="往 stdout 写一行非 JSON")
    ap.add_argument(
        "--garbage-bytes",
        action="store_true",
        help="往 fd 1 直接写一行**非 UTF-8 字节**（cp936 的子进程输出 / C 扩展 printf 的形状）",
    )
    ap.add_argument(
        "--garbage-inside-json",
        action="store_true",
        help="render 的响应本身合法，但 result 里的一个字串夹着非 UTF-8 字节（C 扩展往 fd 1 写的几个字节恰好夹进正在输出的响应）",
    )
    ap.add_argument(
        "--exit-code",
        type=int,
        default=0,
        help="配合 --die-on-render：退出时用这个退出码（模拟脚本 sys.exit(N) / 致命错误）",
    )
    ap.add_argument(
        "--die-on-render", action="store_true", help="收到 render 就直接退出（模拟 worker 崩溃）"
    )
    ap.add_argument(
        "--linger-after-close-ms",
        type=float,
        default=0.0,
        help="配合 --die-on-render：关掉 stdout（管道 EOF）之后**先赖着**"
        "不退这么久。真实崩溃里这个窗口是几微秒、只是偶尔被撞上；"
        "把它拉长，'try_wait() 还回 Ok(None)' 就从抖动变成必然，"
        "让「EOF 之后必须重建」这条有确定性的用例可证。",
    )
    ap.add_argument("--trace", default="", help="把收到的每条请求追加到这个文件（一行一条 JSON）")
    args = ap.parse_args()

    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    stems = {name: {"size_mm": [80.0, 60.0]} for name in args.stems.split(",")}
    seen_heavy = 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        cmd = req.get("cmd")
        if args.trace:
            with open(args.trace, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "cmd": cmd,
                            "stem": req.get("stem"),
                            "request_id": req.get("request_id"),
                            "worker_generation": req.get("worker_generation"),
                            "render_revision": req.get("render_revision"),
                            "canonical_patch_hash": req.get("canonical_patch_hash"),
                            "payload": req.get("payload"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        if cmd == "shutdown":
            return
        if cmd == "ping":
            if args.hang_handshake:
                time.sleep(3600)
            resp = reply(req)
        elif cmd == "build":
            resp = reply(req, {"stems": stems})
        elif cmd in ("render", "render_png", "preview_png", "export"):
            if args.die_on_render:
                if args.linger_after_close_ms:
                    # 先制造 EOF，再赖着不退：进程对象仍然「活着」，而协议管道
                    # 已经断了——两个判据在这个窗口里给出相反的答案。
                    sys.stdout.close()
                    os.close(1)
                    time.sleep(args.linger_after_close_ms / 1000.0)
                sys.exit(args.exit_code)
            if args.hang:
                time.sleep(3600)
            seen_heavy += 1
            delay = args.sleep_ms + (args.first_sleep_ms if seen_heavy == 1 else 0.0)
            if delay:
                time.sleep(delay / 1000.0)
            stem = req.get("stem")
            if stem not in stems:
                resp = error(req, "unknown_stem", f"stem 不存在: {stem}")
            elif cmd == "render":
                resp = reply(
                    req,
                    {"manifest": {"stem": stem, "elements": []}, "warnings": []},
                    wrong_request_id=args.wrong_request_id,
                    bad_version=args.bad_protocol_version,
                )
            elif cmd == "export":
                resp = reply(req, {"path": req["payload"]["path"], "warnings": []})
            else:
                resp = reply(req, {"path": f"/tmp/{stem}.png"})
        else:
            resp = error(req, "unknown_cmd", f"未知指令: {cmd}")

        if args.garbage:
            sys.stdout.write("这不是 JSON\n")
        if args.garbage_bytes:
            # 绕开 TextIOWrapper：这一行就是要坏的字节，不能让 errors="replace" 洗干净
            sys.stdout.flush()
            os.write(1, "这不是 JSON".encode("gbk") + b"\n")
        if args.garbage_inside_json and cmd == "render" and resp.get("ok"):
            # 合法信封、正确的 request_id，只是 stem 这个字串里夹了一个 0xFF：
            # lossy 解码后它是 U+FFFD，JSON 仍然合法——不先判 UTF-8 就会被当成正常响应收下
            raw = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            raw = raw.replace(stem.encode("utf-8"), stem.encode("utf-8") + b"\xff", 1)
            sys.stdout.flush()
            os.write(1, raw + b"\n")
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
