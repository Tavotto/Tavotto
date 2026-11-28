"""pro 协议 v1 的最小实现：`hello`、`echo`、`sleep`（测超时）、`crash`（测崩溃）；加上机理图（ADR 0220）的假图元
`mechanism.catalog` / `mechanism.glyph`：**不含任何真图元几何**，只是形状固定的替身，让开源侧能端到端测协议。
`PRO_ECHO_FEATURES`（逗号分隔，缺省 quick-layout）控制握手里报的功能键；`PRO_ECHO_GLYPH_MODE` = `echo_payload`（把收到的 payload 原样塞进回包的
`seen`，测「只发数字与结构」）。"""

import json
import os
import sys
import time

FAKE_KINDS = [
    {
        "kind": "membrane",
        "versions": [1, 2],
        "default_size_mm": [40.0, 6.0],
        "params": [
            {"name": "curvature", "type": "number", "min": -1.0, "max": 1.0, "default": 0.0},
            {"name": "style", "type": "enum", "values": ["solid", "dots"], "default": "solid"},
        ],
    },
    {
        "kind": "receptor_rtk",
        "versions": [1],
        "default_size_mm": [10.0, 20.0],
        "params": [{"name": "dimer", "type": "bool", "default": True}],
    },
]


def fake_glyph(payload):
    kind, version = payload["kind"], payload["version"]
    if kind == "membrane":  # 两片叶片 = 两条互不相连的开放曲线，按「阵列」放行
        up = [
            {"p": [0.0, 0.2], "out": [0.3, 0.1]},
            {"p": [0.5, 0.2], "in": [0.4, 0.3], "out": [0.6, 0.1]},
            {"p": [1.0, 0.2], "in": [0.8, 0.3]},
        ]
        dn = [
            {"p": [0.0, 0.8], "out": [0.3, 0.7]},
            {"p": [0.5, 0.8], "in": [0.4, 0.9], "out": [0.6, 0.7]},
            {"p": [1.0, 0.8], "in": [0.8, 0.9]},
        ]
        res = {
            "version": version,
            "subpaths": [{"closed": False, "nodes": up}, {"closed": False, "nodes": dn}],
            "array": True,
            "ports": {"outer": [0.5, 0.0], "inner": [0.5, 1.0]},
        }
    else:  # 一条闭合的、不是矩形 / 椭圆的轮廓
        nodes = [
            {"p": [0.2, 0.0]},
            {"p": [0.8, 0.0], "out": [0.9, 0.1], "in": [0.7, 0.0]},
            {"p": [1.0, 0.5]},
            {"p": [0.7, 1.0]},
            {"p": [0.3, 1.0], "out": [0.2, 0.9]},
            {"p": [0.0, 0.4]},
        ]
        res = {
            "version": version,
            "subpaths": [{"closed": True, "nodes": nodes}],
            "ports": {"ligand_site": [0.5, 0.0], "kinase": [0.5, 1.0]},
        }
    if os.environ.get("PRO_ECHO_GLYPH_MODE") == "echo_payload":
        res["seen"] = payload
    return res


def reply(rid, ok, **kw):
    sys.stdout.write(json.dumps({"protocol_version": 1, "request_id": rid, "ok": ok, **kw}) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    req = json.loads(raw)
    rid, cmd, payload = req["request_id"], req["cmd"], req.get("payload") or {}
    if cmd == "hello":
        api = int(os.environ.get("PRO_ECHO_API", "1"))
        if os.environ.get("PRO_ECHO_HELLO_FAIL"):  # 握手回失败信封、自己不退（测宿主回收）
            reply(rid, False, code="unsupported", message="hello refused")
            continue
        reply(
            rid,
            True,
            result={
                "pro_version": "echo",
                "pro_api": api,
                "features": [
                    f for f in os.environ.get("PRO_ECHO_FEATURES", "quick-layout").split(",") if f
                ],
                "platform": sys.platform,
            },
        )
        if os.environ.get("PRO_ECHO_NO_READ"):
            time.sleep(120)  # 握手后不再读 stdin（测宿主写入也受超时约束）
    elif cmd == "mechanism.catalog":
        reply(rid, True, result={"kinds": FAKE_KINDS})
    elif cmd == "mechanism.glyph":
        reply(rid, True, result=fake_glyph(payload))
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

# stdin 关了（宿主 close()）也不退：测宿主 kill 之后会不会回收
if os.environ.get("PRO_ECHO_IGNORE_EOF"):
    time.sleep(120)
