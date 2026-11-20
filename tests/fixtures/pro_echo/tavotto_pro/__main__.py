"""pro 协议 v1 的最小实现：`hello`、`echo`、`sleep`（测超时）、`crash`（测崩溃）。"""

import json
import os
import sys
import time


def reply(rid, ok, **kw):
    sys.stdout.write(json.dumps({"protocol_version": 1, "request_id": rid, "ok": ok, **kw}) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    req = json.loads(raw)
    rid, cmd, payload = req["request_id"], req["cmd"], req.get("payload") or {}
    if cmd == "hello":
        api = int(os.environ.get("PRO_ECHO_API", "1"))
        reply(
            rid,
            True,
            result={
                "pro_version": "echo",
                "pro_api": api,
                "features": ["quick-layout"],
                "platform": sys.platform,
            },
        )
    elif cmd == "echo":
        reply(rid, True, result={"payload": payload})
    elif cmd == "sleep":
        time.sleep(float(payload.get("s", 5)))
        reply(rid, True, result={})
    elif cmd == "crash":
        sys.exit(3)
    elif cmd == "wrong_id":
        reply("nope", True, result={})
    else:
        reply(rid, False, code="unsupported", message=cmd)
