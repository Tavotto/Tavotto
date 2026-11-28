"""「外部审阅」客户端（ADR 0187）：把当前这张图 / 这份排版的**画面**分享成一个链接，别人在网页上批，作者在应用里接着看。

服务端是 `Tavotto/infra` 的 `serverless/review/`（CloudBase 云函数，线上格式 v1 见 ADR 0187 §六）。形状照抄 `contactsend.py`
（ADR 0145，同一套出站笼子），这里只写差异：

* **只上传画面**：每个版本只有 `snapshot.png`（走现有导出管线、长边 ≤ 3000 px、≤ 8 MiB）、元素位置表（≤ 4000 项 / 256 KiB，
  画面比例坐标）与标题 / 给审阅者的话。脚本、数据、项目文件、路径、本机信息一律不传。**出图与元素表由 `app.py` 注册的渲染回调
  给**（`set_renderer`；插件的 MCP 进程改由 `start_share` / `start_update` 的 `renderer=` 随这一次分享传入，ADR 0205）：父进程这里不碰 `manifest` / `figsession`（子进程模块）。
* **逐次授权**：上传只由 `start_share` / `start_update`（一次点击）引出；失败、取消后不自己重试。**没有记录时轮询一次网络都不发。**
* **目的地由代码钉死**：`REVIEW_HOSTS = {review.tavotto.cn}`；`TAVOTTO_REVIEW_ENDPOINT` 设了就必须合规（或回环开发模式
  `TAVOTTO_DIAG_DEV_LOOPBACK=1`），不合规整个功能关闭、**不回退内置端点**；`TAVOTTO_NO_REVIEW_SHARE=1` 整个功能不出现、不联网。
  出站只经 `diagsend._send`（平台证书、不跟随重定向、响应体封顶）；直传存储地址另有白名单（`UPLOAD_HOST_PATTERNS` + 回环同源）。
* **设备签名**：每台设备一把 Ed25519 密钥（PyNaCl，延后 import；私钥 32 字节 0600 存 `data_dir()/review/device.key`），
  作者端请求带 `(时间戳, 请求体哈希)` 的签名（线上格式见 ADR / wire-api）。签的就是**发出去的那份字节与路径**，各只构造一次。
* **秘密不落地**：`owner_token`、上传表单 / 头、标题、给审阅者的话、意见文字只在内存或 `reviewinbox` 的 0600 文件里；
  `status()` / 日志里没有。日志模板是字面量，参数只有闭集里的阶段名与结果码。
* **重试幂等**：`client_share_id` / `client_version_id` 与渲染好的 PNG、元素表跟着这次失败的尝试一起留在内存里
  （`_REUSE`），内容没变的重试逐字节相同——服务端对同一 id 不同指纹回 409。init 一拿到令牌就写本机记录（complete 之前）。

纯标准库，Flask 父进程 import 链上的模块（`nacl` 只在签名时延后 import）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import struct
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zlib
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field

from ..rendercore.geometry import clip_polyline
from . import atomicio, config, diagsend, logsafe, reviewinbox

LOG = logging.getLogger("tavotto.reviewshare")

# ---------------------------------------------------------------------------
# 开关与端点
# ---------------------------------------------------------------------------
ENV_HARD_OFF = "TAVOTTO_NO_REVIEW_SHARE"
ENV_ENDPOINT = "TAVOTTO_REVIEW_ENDPOINT"
REVIEW_HOSTS: frozenset[str] = frozenset({"review.tavotto.cn"})
PRODUCTION_ENDPOINT = "https://review.tavotto.cn"
#: 线上服务是否已部署并验通（ADR 0187 §六）。False 时只有显式设了 `TAVOTTO_REVIEW_ENDPOINT`
#: （开发 / 验收）才可用，正式安装里「分享审阅」入口不出现、零网络。
#: 2026-11-16 review.tavotto.cn 部署并端到端验通（建链接、更新、审阅页、额度计数），维护者同意随 0.21.3 打开。
PRODUCTION_LIVE = True

#: 直传存储地址的主机白名单（COS 桶域名 + CloudBase 存储域名）；回环开发模式另允许与端点同源。
UPLOAD_HOST_SUFFIXES: tuple[str, ...] = (".myqcloud.com", ".tcb.qcloud.la", ".tcloudbaseapp.com")
#: 开通 CloudBase 环境、确认真实存储主机后再收紧到具体桶域名
UPLOAD_HOST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^[a-z0-9][a-z0-9-]{0,62}(?:\.[a-z0-9-]{1,63}){1,4}$"),
)

# ---------------------------------------------------------------------------
# 契约上限（ADR 0187 §二 / §五、wire-api）
# ---------------------------------------------------------------------------
LONG_EDGE_PX = 3000
MAX_PNG_BYTES = 8 * 1024 * 1024
MAX_ELEMENTS = 4000
MAX_ELEMENTS_BYTES = 256 * 1024
TITLE_MAX = reviewinbox.TITLE_MAX
NOTE_MAX = reviewinbox.NOTE_MAX
TEXT_MAX = reviewinbox.TEXT_MAX
RENDER_ATTEMPTS = 5
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

API_TIMEOUT_S = 15
UPLOAD_TIMEOUT_S = 90
COMPLETE_TIMEOUT_S = 30
FEED_TIMEOUT_S = 20
COMPLETE_ATTEMPTS = 4
COMPLETE_BACKOFF_S = (1.0, 2.0, 4.0)
RETRY_AFTER_CAP_S = 10
#: feed 响应体上限：≤ 500 条意见 × (500 字 × 4 字节 + 回复若干) 的最坏线缆编码留足余量
FEED_MAX_BYTES = 8 * 1024 * 1024
API_MAX_BYTES = diagsend.MAX_RESPONSE_BYTES
POLL_INTERVAL_S = 30 * 60
POLL_STARTUP_DELAY_S = 30
REFRESH_MIN_S = 20

STAGES = frozenset(
    {"render", "init", "upload", "complete", "feed", "reply", "resolve", "revoke", "quota"}
)

#: 失败码闭集（与前端 `review.failure.*` 对拍，local-api.md 的 FailureCode）。值 = 用户能不能再点一次。
FAILURES: dict[str, bool] = {
    "quota_new_exhausted": False,
    "share_gone": False,
    "too_large": False,
    "rate_limited": True,
    "network": True,
    "server": True,
    "client_error": False,
    "render_failed": True,
}
LOG_RESULTS = frozenset(FAILURES) | {"ok", "cancelled"}
#: 服务端 code 直通的
_PASS_CODES = frozenset({"quota_new_exhausted", "share_gone", "too_large", "rate_limited"})
#: 签名 / 时钟 / 请求不合法：对用户都是「客户端出错」
_CLIENT_CODES = frozenset({"bad_signature", "clock_skew", "invalid"})

_Failure = diagsend._Failure
_Cancelled = diagsend._Cancelled

_SHARE_ID = reviewinbox.SHARE_ID
_REVIEW_PATH = re.compile(r"^/r/[A-Za-z0-9_-]{16,128}$")
_OWNER_TOKEN = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "authorization",
        "cookie",
        "connection",
        "expect",
        "user-agent",
        "te",
        "upgrade",
    }
)
_UA = "Tavotto-review-client/1"


class ReviewError(Exception):
    """对 Flask 层抛的同步错：`code` 稳定、`status` 是本机 HTTP 状态。"""

    def __init__(self, code: str, status: int = 400) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


_DEV_HOSTS = frozenset({"127.0.0.1", "localhost"})


@dataclass(frozen=True)
class _DevTarget(diagsend.Target):
    """**仅回环开发模式**的端点（可以是 `http://`，本机服务端常没有 TLS）。生产 `Target` 永远是 https。"""

    scheme: str = "https"

    @property
    def base(self) -> str:
        default = 443 if self.scheme == "https" else 80
        return f"{self.scheme}://{self.host}" + ("" if self.port == default else f":{self.port}")


def _dev_endpoint(raw: str) -> diagsend.Target | None:
    """回环开发模式下的端点：`http(s)://127.0.0.1|localhost[:端口]`，无用户信息 / 路径 / 查询串。"""
    try:
        parts = urllib.parse.urlsplit(raw.strip())
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or host not in _DEV_HOSTS:
        return None
    if "@" in parts.netloc or parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    return _DevTarget(host, port or (443 if parts.scheme == "https" else 80), True, parts.scheme)


def _is_dev_loopback(target: diagsend.Target) -> bool:
    return isinstance(target, _DevTarget) and target.loopback


def _loopback_url(parts: urllib.parse.SplitResult) -> bool:
    """回环开发模式下 review_url / 上传地址的放行规则：http(s)、主机必须是回环，端口与路径任意。"""
    return (
        parts.scheme in ("http", "https")
        and (parts.hostname or "").lower() in _DEV_HOSTS
        and parts.username is None
        and "@" not in parts.netloc
        and not parts.fragment
    )


def resolve_target() -> diagsend.Target | None:
    """此刻允许连的端点；None = 功能关闭（入口不出现、不联网）。设了 `TAVOTTO_REVIEW_ENDPOINT` 但不合规也是 None，不回退。"""
    if _flag(ENV_HARD_OFF):
        return None
    raw = os.environ.get(ENV_ENDPOINT)
    if raw is not None and raw.strip():
        dev = _flag(diagsend.ENV_DEV_LOOPBACK)
        got = diagsend.parse_endpoint(raw, dev_loopback=dev, hosts=REVIEW_HOSTS)
        if got is not None and got.loopback:
            return _dev_endpoint(raw)  # 回环只有开发模式才走到这里；保留 https 回环的旧行为
        if got is None and dev:
            return _dev_endpoint(raw)
        return got
    if not PRODUCTION_LIVE:
        return None
    return diagsend.parse_endpoint(PRODUCTION_ENDPOINT, dev_loopback=False, hosts=REVIEW_HOSTS)


def enabled() -> bool:
    return resolve_target() is not None


def capability() -> dict:
    """给界面的能力说明（本机判断，**不联网**，不含端点地址）。"""
    return {"enabled": enabled()}


# ---------------------------------------------------------------------------
# PNG 与元素表（纯函数）
# ---------------------------------------------------------------------------
def png_size(data: bytes) -> tuple[int, int] | None:
    """PNG 的像素尺寸（读 IHDR）；魔数 / IHDR 不对 None。"""
    if len(data) < 24 or not data.startswith(_PNG_MAGIC) or data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return (w, h) if w > 0 and h > 0 else None


def _finite4(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    out = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        out.append(float(v))
    return out


def _clamp_box(x0: float, y0: float, x1: float, y1: float) -> list[float] | None:
    """角点框 → 夹进 [0,1]² 的 `[x, y, w, h]`（5 位小数）；夹完没有面积 None。"""
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return [round(x0, 5), round(y0, 5), round(x1 - x0, 5), round(y1 - y0, 5)]


#: 这些角色的 manifest 显示名就是**画在图上的文字**（`manifest` 里按 `artist.get_text()` 现算：标题 / 轴标签 / 文字 / 图例项 /
#: 刻度），可以原样上传；其余角色的显示名来自 artist 的 `label`（脚本里随便写，没进图例就不在画面上，可能是路径 / 文件名），不传。
_RENDERED_TEXT_ROLES = frozenset({"title", "axis_label", "text", "legend_text", "ticklabel"})
#: 其余角色 → 固定的结构性短词（+ 同角色序号）；前端按需要换成自己的显示文案
_STRUCTURAL_NAMES: dict[str, str] = {
    "line": "曲线",
    "scatter": "散点",
    "bar": "柱",
    "bar_series": "柱",
    "fill": "填充",
    "errorbar": "误差棒",
    "stem_series": "茎叶",
    "image": "图像",
    "linecoll": "线组",
    "collection": "集合",
    "patch": "形状",
    "arrow_patch": "箭头",
    "legend": "图例",
    "colorbar": "色条",
    "axes": "子图",
    "axes3d": "子图",
}


#: 上传的路径坐标保留 4 位小数（画面比例；3000 px 长边下 0.3 px），够点选、省体积
_GEOM_DP = 4
Rect = tuple[float, float, float, float]  # x0, y0, x1, y1


def _clip_segment(
    a: tuple[float, float] | list[float], b: tuple[float, float] | list[float], r: Rect
) -> list[list[float]] | None:
    """线段 a→b 裁进矩形 `r`，保持方向；返回 5 位小数的 `[起, 终]`，整段在框外 None。"""
    runs = clip_polyline([(a[0], a[1]), (b[0], b[1])], r)
    if not runs:
        return None
    s, e = runs[0][0], runs[0][-1]
    return [[round(s[0], 5), round(s[1], 5)], [round(e[0], 5), round(e[1], 5)]]


def _clip_polygon(pts: list[tuple[float, float]], r: Rect) -> list[tuple[float, float]]:
    """闭合多边形裁进矩形 `r`（Sutherland–Hodgman）。"""
    out = pts
    for axis, lim, keep_ge in (
        (0, r[0], True),
        (0, r[2], False),
        (1, r[1], True),
        (1, r[3], False),
    ):
        if not out:
            break
        src, out = out, []
        for i, cur in enumerate(src):
            prev = src[i - 1]
            cin = cur[axis] >= lim if keep_ge else cur[axis] <= lim
            pin = prev[axis] >= lim if keep_ge else prev[axis] <= lim
            if cin != pin:
                t = (lim - prev[axis]) / (cur[axis] - prev[axis])
                out.append((prev[0] + (cur[0] - prev[0]) * t, prev[1] + (cur[1] - prev[1]) * t))
            if cin:
                out.append(cur)
    return out


def _visible_geometry(
    geom: object, transform=None, frame: Rect = (0.0, 0.0, 1.0, 1.0)
) -> dict | None:
    """manifest 的 `geometry` → 只剩**画面上看得见的部分**：先（可选）变换坐标，再裁进 `clip ∩ frame`。
    manifest 的路径没裁剪（xlim 之外的数据点也在），原样上传就把图上看不见的数据传出去了——ADR 0187 只许上传画面。"""
    g = reviewinbox.clean_geometry(geom)
    if g is None:
        return None
    r: Rect = frame
    if "clip" in g:
        c = g["clip"]
        cr = (c[0], c[1], c[0] + c[2], c[1] + c[3])
        if transform is not None:
            corners = [transform(cr[0], cr[1]), transform(cr[2], cr[3])]
            cr = (
                min(p[0] for p in corners),
                min(p[1] for p in corners),
                max(p[0] for p in corners),
                max(p[1] for p in corners),
            )
        r = (max(r[0], cr[0]), max(r[1], cr[1]), min(r[2], cr[2]), min(r[3], cr[3]))
    if r[2] <= r[0] or r[3] <= r[1]:
        return None
    paths: list[dict] = []
    for p in g["paths"]:
        pts = [(q[0], q[1]) for q in p["points"]]
        if transform is not None:
            pts = [transform(x, y) for x, y in pts]
        if p["closed"] and g.get("fill"):
            poly = _clip_polygon(pts, r)
            pieces = [(poly, True)] if len(poly) >= 3 else []
        elif p["closed"]:
            # 未填充的闭合路径只画描边：多边形裁剪会在裁剪框边界上凭空造出 matplotlib 没画的边，
            # 所以带上收口那一段当折线裁，出框就断开成开放段
            pieces = [(run, False) for run in clip_polyline(pts + pts[:1], r)]
        else:
            pieces = [(run, False) for run in clip_polyline(pts, r)]
        for run, closed in pieces:
            paths.append(
                {
                    "points": [[round(x, _GEOM_DP), round(y, _GEOM_DP)] for x, y in run],
                    "closed": closed,
                }
            )
    if not paths:
        return None
    g["paths"] = paths
    g["clip"] = [round(r[0], 5), round(r[1], 5), round(r[2] - r[0], 5), round(r[3] - r[1], 5)]
    return g


def _is_hidden(el: dict) -> bool:
    """manifest 里 `visible=false` = 用户已在画布上删掉（非破坏性隐藏）：画面上没有，不进元素表。"""
    ed = el.get("editable")
    return isinstance(ed, list) and any(
        isinstance(f, dict) and f.get("prop") == "visible" and f.get("value") is False for f in ed
    )


def manifest_elements(manifest: object) -> list[dict]:
    """manifest JSON → `[{gid, label, bbox}]`（bbox 是 figure 分数 `[x, y, w, h]`、y 向下，已夹进画面）。
    整张图（`gid == "figure"`）不要：「整体意见」另有锚点。数据图元的未裁剪包围盒可能比图幅大几百倍，所以先夹再算面积。
    **label 只有两种来源**：画在图上的文字（`_RENDERED_TEXT_ROLES`），或按角色的结构性名字 + 序号；artist 的 label 一律不传。"""
    out: list[dict] = []
    counts: dict[str, int] = {}
    els = manifest.get("elements") if isinstance(manifest, dict) else None
    for el in els if isinstance(els, list) else []:
        if not isinstance(el, dict) or _is_hidden(el):
            continue
        gid, bb = el.get("gid"), _finite4(el.get("bbox"))
        if not isinstance(gid, str) or not gid or gid == "figure" or bb is None:
            continue
        box = _clamp_box(bb[0], bb[1], bb[0] + bb[2], bb[1] + bb[3])
        if box is None:
            continue
        role = el.get("role") if isinstance(el.get("role"), str) else ""
        label = el.get("label")
        if role in _RENDERED_TEXT_ROLES and isinstance(label, str) and label:
            name = label
        else:
            word = _STRUCTURAL_NAMES.get(role, "元素")
            counts[word] = counts.get(word, 0) + 1
            name = f"{word} {counts[word]}"
        item: dict = {"gid": gid[:256], "label": name[:120], "bbox": box}
        if role and reviewinbox._ROLE.match(role):
            item["role"] = role
        order = el.get("paint_order")
        if isinstance(order, int) and not isinstance(order, bool) and order >= 0:
            item["paint_order"] = order
        ends = el.get("arrow_endpoints")
        if isinstance(ends, list) and len(ends) >= 2:
            # 端点不能逐坐标夹值（会改箭头方向）：取原值，线段 a→b 与画面求交，起点仍是尾端
            a, b = reviewinbox._point_raw(ends[0]), reviewinbox._point_raw(ends[-1])
            if a and b:
                seg = _clip_segment(a, b, (0.0, 0.0, 1.0, 1.0))
                if seg:
                    item["arrow_endpoints"] = seg
        geom = _visible_geometry(el.get("geometry"))
        if geom is not None:
            item["geometry"] = geom
        out.append(item)
        if role == "axes":
            out.extend(_guide_items(el, item["gid"]))
    return out


#: 边框线 → 显示名（结构性短词，不是用户文字）
_SPINE_NAMES = {"left": "左轴线", "right": "右轴线", "top": "上轴线", "bottom": "下轴线"}
#: 网格线 / 刻度线按轴成组：(manifest 字段, gid 后缀, 角色, 显示名)
_GUIDE_GROUPS = (
    ("grid", "grid", "gridline", "网格线"),
    ("tickmarks", "tickmarks", "tickmark", "刻度线"),
)
#: 水平 / 垂直的线 bbox 单边为 0：垫出的最小厚度（figure 分数）
_GUIDE_MIN_THICK = 0.002


def _guide_item(gid: str, label: str, role: str, geom: dict | None) -> dict | None:
    """一条线形目标 → 元素表项（bbox = 可见几何的包围盒，垫出最小厚度）；看不见就 None。"""
    if geom is None:
        return None
    xs = [q[0] for p in geom["paths"] for q in p["points"]]
    ys = [q[1] for p in geom["paths"] for q in p["points"]]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    pad = _GUIDE_MIN_THICK / 2
    if x1 - x0 < _GUIDE_MIN_THICK:
        x0, x1 = x0 - pad, x1 + pad
    if y1 - y0 < _GUIDE_MIN_THICK:
        y0, y1 = y0 - pad, y1 + pad
    box = _clamp_box(x0, y0, x1, y1)
    if box is None:
        return None
    item: dict = {"gid": gid[:256], "label": label, "bbox": box, "role": role, "geometry": geom}
    if "paint_order" in geom:
        item["paint_order"] = geom["paint_order"]
    return item


def _guide_items(el: dict, gid: str) -> list[dict]:
    """子图的边框线 / 网格线 / 刻度线 → 合成元素（ADR 0187 修订 2026-11-17）。只收**画出来**的部分：
    不可见的边、没开的网格、被关掉的刻度线都没有；几何一律经 `_visible_geometry` 裁进画面。
    gid = `<子图 gid>.spine_left` / `.xgrid` / `.ytickmarks`，审阅页按「.」切出所属子图。"""
    out: list[dict] = []
    spines = el.get("spines")
    for side, name in _SPINE_NAMES.items():
        sp = spines.get(side) if isinstance(spines, dict) else None
        if not isinstance(sp, dict) or sp.get("visible") is not True:
            continue
        a, b = reviewinbox._point_raw(sp.get("from")), reviewinbox._point_raw(sp.get("to"))
        if a is None or b is None:
            continue
        raw: dict = {
            "kind": "polyline",
            "paths": [{"points": [a, b], "closed": False}],
            "fill": False,
            "stroke": True,
        }
        for k in ("stroke_pt", "paint_order"):
            if k in sp:
                raw[k] = sp[k]
        if raw.get("stroke_pt") == 0:
            continue
        item = _guide_item(f"{gid}.spine_{side}", name, "spine", _visible_geometry(raw))
        if item:
            out.append(item)
    for key, suffix, role, word in _GUIDE_GROUPS:
        group = el.get(key)
        for which in ("x", "y"):
            raw = group.get(which) if isinstance(group, dict) else None
            item = _guide_item(
                f"{gid}.{which}{suffix}",
                f"{which.upper()} {word}",
                role,
                _visible_geometry(raw),
            )
            if item:
                out.append(item)
    return out


def manifest_size_mm(manifest: object) -> list[float] | None:
    """manifest 的图幅（mm）：审阅页的点选容差按 mm 算（与画布同一口径）。"""
    sz = manifest.get("size_mm") if isinstance(manifest, dict) else None
    if isinstance(sz, (list, tuple)) and len(sz) == 2:
        vals = [v for v in sz if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if len(vals) == 2 and all(math.isfinite(v) and 0 < v < 10000 for v in vals):
            return [round(float(v), 3) for v in vals]
    return None


def _to_local(
    u: float, v: float, crop, rot: int, flip_h: bool, flip_v: bool
) -> tuple[float, float]:
    """源图分数坐标 → 面板框内分数：先裁剪、再顺时针转（90° 倍数）、再翻转（与 `rendercore/plan._panel_node` 同一顺序）。"""
    if crop:
        u, v = (u - crop[0]) / crop[2], (v - crop[1]) / crop[3]
    if rot == 90:
        u, v = 1 - v, u
    elif rot == 180:
        u, v = 1 - u, 1 - v
    elif rot == 270:
        u, v = v, 1 - u
    if flip_h:
        u = 1 - u
    if flip_v:
        v = 1 - v
    return u, v


_PANEL_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def panel_keys(objects: object) -> dict[int, str]:
    """排版里每个面板（按 `objects` 下标）的**不透明键**：前端在出图描述的对象上带 `object_id`（文档对象自己的随机 id）就用它，
    缺 / 不合规 / 重复就用 `p<第几个面板>`（1 起、按 z 序）。**绝不用对象的 `id`**——导出请求里 `id` 是图的项目内相对路径（`fileId`），
    路径不许出机（ADR 0187 §二），同一张图的两个面板也会撞键。"""
    out: dict[int, str] = {}
    taken: set[str] = set()
    n = 0
    for i, o in enumerate(objects if isinstance(objects, list) else []):
        if not isinstance(o, dict) or o.get("type") != "panel":
            continue
        n += 1
        key = o.get("object_id")
        if not isinstance(key, str) or not _PANEL_KEY.match(key) or key in taken:
            key = f"p{n}"
            while key in taken:
                key += "_"
        taken.add(key)
        out[i] = key
    return out


def layout_elements(
    page_w_mm: object, page_h_mm: object, objects: object, manifests: dict[str, object]
) -> list[dict]:
    """排版 → 元素表：每个面板一个框（gid = `panel_keys` 给的不透明键）+ 面板内元素按面板在页面上的位置换算后的框
    （gid = `<键>::<manifest gid>`）。`manifests` 以面板键为键，缺 / 空 = 该面板只有外框。
    隐藏对象不要；文字 / 形状不是「元素」。裁剪 / 90° 旋转 / 翻转都计入换算；**不认任意角旋转**（面板本就限 90° 倍数）。"""
    try:
        pw, ph = float(page_w_mm), float(page_h_mm)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    if not (math.isfinite(pw) and math.isfinite(ph)) or pw <= 0 or ph <= 0:
        return []
    boxes: list[dict] = []
    inner: list[dict] = []
    keys = panel_keys(objects)
    ordinal = {i: n for n, i in enumerate(sorted(keys), 1)}
    for i, o in enumerate(objects if isinstance(objects, list) else []):
        if i not in keys or o.get("hidden"):
            continue
        oid, n = keys[i], ordinal[i]
        try:
            x, y, w, h = (float(o[k]) for k in ("x_mm", "y_mm", "w_mm", "h_mm"))
        except (KeyError, TypeError, ValueError):
            continue
        if not all(map(math.isfinite, (x, y, w, h))) or w <= 0 or h <= 0:
            continue
        # 显示名只认前端明说的 `label` / `name`；像路径的（含 / 或 \\）一律当没有——路径不许出机
        name = next(
            (
                o[k]
                for k in ("label", "name")
                if isinstance(o.get(k), str) and o[k] and "/" not in o[k] and "\\" not in o[k]
            ),
            "",
        )
        panel = _clamp_box(x / pw, y / ph, (x + w) / pw, (y + h) / ph)
        if panel is None:
            continue
        boxes.append({"gid": oid, "label": (name or f"#{n}")[:120], "bbox": panel})
        crop = o.get("crop")
        crop_t = None
        if isinstance(crop, dict):
            try:
                cx, cy, cw, ch = (float(crop[k]) for k in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError):
                cw = ch = 0.0
            if cw > 0 and ch > 0 and all(map(math.isfinite, (cx, cy, cw, ch))):
                crop_t = (cx, cy, cw, ch)
        try:
            rot = int(round(float(o.get("rotation") or 0) / 90.0)) * 90 % 360
        except (TypeError, ValueError):
            rot = 0
        for el in manifest_elements(manifests.get(oid)):
            bx, by, bw, bh = el["bbox"]
            pts = [
                _to_local(u, v, crop_t, rot, bool(o.get("flip_h")), bool(o.get("flip_v")))
                for u, v in ((bx, by), (bx + bw, by), (bx, by + bh), (bx + bw, by + bh))
            ]
            lx0, lx1 = max(0.0, min(p[0] for p in pts)), min(1.0, max(p[0] for p in pts))
            ly0, ly1 = max(0.0, min(p[1] for p in pts)), min(1.0, max(p[1] for p in pts))
            if lx1 <= lx0 or ly1 <= ly0:
                continue  # 被裁掉的元素
            box = _clamp_box(
                (x + lx0 * w) / pw, (y + ly0 * h) / ph, (x + lx1 * w) / pw, (y + ly1 * h) / ph
            )
            if box is None:
                continue
            item = {"gid": f"{oid}::{el['gid']}"[:256], "label": el["label"], "bbox": box}
            for k in ("role", "paint_order"):
                if k in el:
                    item[k] = el[k]

            def to_page(u: float, v: float, _o=o, _x=x, _y=y, _w=w, _h=h, _c=crop_t, _r=rot):
                lu, lv = _to_local(u, v, _c, _r, bool(_o.get("flip_h")), bool(_o.get("flip_v")))
                return ((_x + lu * _w) / pw, (_y + lv * _h) / ph)

            if "arrow_endpoints" in el:
                # 面板框外（裁掉的部分）看不见：裁进面板在页面上的框
                frame = (
                    max(0.0, x / pw),
                    max(0.0, y / ph),
                    min(1.0, (x + w) / pw),
                    min(1.0, (y + h) / ph),
                )
                a, b = (to_page(*q) for q in el["arrow_endpoints"][:2])
                seg = _clip_segment(a, b, frame)
                if seg:
                    item["arrow_endpoints"] = seg
            if "geometry" in el:
                frame = (
                    max(0.0, x / pw),
                    max(0.0, y / ph),
                    min(1.0, (x + w) / pw),
                    min(1.0, (y + h) / ph),
                )
                geom = _visible_geometry(el["geometry"], to_page, frame)
                if geom is not None:
                    # 排版里同一张图的绘制次序只在面板内可比；跨面板按面板先后（z 序）错开
                    if "paint_order" in geom:
                        geom["paint_order"] += n * 100000
                    item["geometry"] = geom
            if "paint_order" in item:
                item["paint_order"] += n * 100000
            inner.append(item)
    return boxes + inner


def trim_elements(elements: list[dict]) -> tuple[list[dict], int]:
    """超限时先**去形状**（点数最多的先去，该元素退回按 bbox 点选），还超再按面积从小到大丢元素，保持原顺序。
    超过 `MAX_ELEMENTS` 项或 `MAX_ELEMENTS_BYTES` 字节算超限。返回 (保留的, 丢弃个数)。"""
    cleaned = reviewinbox.clean_elements(elements)

    def size(items: list[dict]) -> int:
        return len(json.dumps(items, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    total = size(cleaned)
    if len(cleaned) <= MAX_ELEMENTS and total <= MAX_ELEMENTS_BYTES:
        return cleaned, 0
    if total > MAX_ELEMENTS_BYTES:
        heavy = sorted(
            (i for i, e in enumerate(cleaned) if "geometry" in e),
            key=lambda i: size([cleaned[i]["geometry"]]),
            reverse=True,
        )
        for i in heavy:
            total -= size([cleaned[i]["geometry"]]) + len(',"geometry":')
            del cleaned[i]["geometry"]
            if total <= MAX_ELEMENTS_BYTES:
                break
        if len(cleaned) <= MAX_ELEMENTS and size(cleaned) <= MAX_ELEMENTS_BYTES:
            return cleaned, 0
    order = sorted(
        range(len(cleaned)),
        key=lambda i: cleaned[i]["bbox"][2] * cleaned[i]["bbox"][3],
        reverse=True,
    )
    keep = min(len(order), MAX_ELEMENTS)
    while keep > 0:
        picked = sorted(order[:keep])
        if size([cleaned[i] for i in picked]) <= MAX_ELEMENTS_BYTES:
            return [cleaned[i] for i in picked], len(cleaned) - keep
        keep = int(keep * 0.9) if keep > 10 else keep - 1
    return [], len(cleaned)


# ---------------------------------------------------------------------------
# 设备密钥与签名
# ---------------------------------------------------------------------------
_KEY_LOCK = threading.Lock()
KEY_FILE = "device.key"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signing_key():
    """本机设备密钥（首次使用时生成；32 字节种子，0600）。PyNaCl 缺失 / 写不下去 → `client_error`（不用一次性密钥：那等于每次新设备）。"""
    try:
        from nacl.signing import SigningKey
    except ImportError:
        raise _Failure("client_error") from None
    p = config.data_path(reviewinbox.DIR_NAME, KEY_FILE)
    with _KEY_LOCK:
        try:
            seed = p.read_bytes()
        except OSError:
            seed = b""
        if len(seed) == 32:  # 已存在：无锁读，签名路径不为它抢跨进程锁
            return SigningKey(seed)
        try:
            # 首次生成在跨进程锁里（ADR 0205 §四）：桌面与插件同时第一次用时只生成一把；锁里重读，已存在就用它、不覆盖
            with reviewinbox.file_lock():
                try:
                    seed = p.read_bytes()
                except OSError:
                    seed = b""
                if len(seed) == 32:
                    return SigningKey(seed)
                key = SigningKey.generate()
                p.parent.mkdir(parents=True, exist_ok=True)
                try:
                    p.parent.chmod(0o700)
                except OSError:
                    pass
                atomicio.write_bytes(p, bytes(key), mode=0o600)
                return key
        except OSError:
            LOG.warning("外部审阅: 设备密钥写入失败")
            raise _Failure("client_error") from None


def sign_headers(method: str, path: str, body: bytes, ts: int) -> dict[str, str]:
    key = _signing_key()
    message = (
        f"tavotto-review-v1\n{method}\n{path}\n{ts}\n{hashlib.sha256(body).hexdigest()}"
    ).encode("utf-8")
    return {
        "X-Tavotto-Device": _b64u(bytes(key.verify_key)),
        "X-Tavotto-Timestamp": str(ts),
        "X-Tavotto-Signature": _b64u(key.sign(message).signature),
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_time = time.time


def _interruptible_sleep(cancel: threading.Event, seconds: float) -> None:
    cancel.wait(seconds)


_sleep = _interruptible_sleep


def _transport(fail: _Failure) -> _Failure:
    """传输层失败 → 本模块码：连不上 / 超时 / 证书 都是 `network`。"""
    return _Failure("network") if fail.code in ("network", "timeout", "tls") else fail


def _server_code(resp: diagsend._Response) -> str:
    """错误体：`{"error": {"code", "message"}}`（线上格式）；别的形状不认（认了会掩盖契约漂移）。"""
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    err = obj.get("error") if isinstance(obj, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    return code[:64] if isinstance(code, str) else ""


def classify(status: int, server_code: str) -> str:
    """服务端错误 → `FAILURES` 的键。"""
    if server_code in _PASS_CODES:
        return server_code
    if server_code in _CLIENT_CODES:
        return "client_error"
    if status == 413:
        return "too_large"
    # 裸 404（没有服务端错误码）多半是网关 / 路径出了问题，不是「链接没了」：不能据此标记 revoked，当 `server`（可再试）
    if status == 404:
        return "server"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "server"
    return "client_error"


def _error_of(resp: diagsend._Response) -> _Failure:
    return _Failure(classify(resp.status, _server_code(resp)), diagsend._retry_after(resp))


def _call(
    target: diagsend.Target,
    method: str,
    path: str,
    payload: object | None,
    *,
    bearer: str = "",
    timeout: float = API_TIMEOUT_S,
    limit: int = API_MAX_BYTES,
) -> diagsend._Response:
    """一次签名请求。`path` 含 query；请求体字节与路径各构造一次，签的就是发的。"""
    body = (
        b""
        if payload is None
        else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    headers = {"Accept": "application/json", "User-Agent": _UA}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    headers.update(sign_headers(method, path, body, int(_time())))
    req = urllib.request.Request(  # noqa: S310 — 地址由 Target 钉死为白名单主机的 https
        target.base + path,
        data=body if payload is not None else None,
        method=method,
        headers=headers,
    )
    try:
        return diagsend._send(req, timeout, limit)
    except _Failure as fail:
        raise _transport(fail) from None


def _json(resp: diagsend._Response) -> dict:
    try:
        obj = json.loads(resp.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise _Failure("server") from None
    if not isinstance(obj, dict):
        raise _Failure("server")
    return obj


# ---------------------------------------------------------------------------
# 额度
# ---------------------------------------------------------------------------
def _quota_part(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    v = raw.get("new_left")
    out["new_left"] = v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
    out["update_left"] = None  # 更新同一链接不限次数（只限每天新建 1 个），恒 null
    return out


def quota(share_id: str = "") -> dict:
    """今天还剩几次（弹窗页脚小字用）。联网失败 / 功能关闭 → 两个 None（界面此时不显示剩余次数）。"""
    none = {"new_left": None, "update_left": None}
    target = resolve_target()
    if target is None:
        return none
    path = "/v1/quota"
    if share_id:
        if not _SHARE_ID.match(share_id):
            return none
        path += "?share_id=" + urllib.parse.quote(share_id, safe="")
    try:
        resp = _call(target, "GET", path, None)
        if resp.status != 200:
            raise _error_of(resp)
        got = _quota_part(_json(resp))
    except _Failure as fail:
        _log(fail.code, "quota")
        return none
    _log("ok", "quota")
    return got or none


# ---------------------------------------------------------------------------
# 渲染回调（`app.py` 注册）
# ---------------------------------------------------------------------------
@dataclass
class Rendered:
    png: bytes = field(repr=False)
    elements: list[dict] = field(default_factory=list)
    #: 图幅（mm）：审阅页按 mm 算点选容差；拿不到 None（审阅页退回按像素）
    size_mm: list[float] | None = None


#: `(target, render, long_edge_px) -> Rendered`；`target` = `{"kind", "id"}`，`render` = 前端发来的出图描述（见 `app._review_render`）。
Renderer = Callable[[dict, dict, int], Rendered]
_RENDERER: Renderer | None = None


def set_renderer(fn: Renderer | None) -> None:
    global _RENDERER
    _RENDERER = fn


@dataclass(frozen=True)
class _Artifact:
    png: bytes = field(repr=False)
    elements: list[dict] = field(repr=False)
    size_mm: list[float] | None = None


_RESAMPLE_MAX_PIXELS = 16_000_000


def _decode_png(data: bytes):
    """本机引擎自己编的 PNG（8 bit RGB / RGBA、非交错、滤波 0）→ `RasterBuffer`；别的形态 None。纯标准库。"""
    from ..rendercore.raster import RasterBuffer

    if png_size(data) is None:
        return None
    pos, idat, head = 8, [], None
    while pos + 8 <= len(data):
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        tag, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + n]
        if tag == b"IHDR":
            head = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos += 12 + n
    if head is None or head[2:] not in ((8, 2, 0, 0, 0), (8, 6, 0, 0, 0)):
        return None
    w, h = head[0], head[1]
    ch = 4 if head[3] == 6 else 3
    if w * h > 64_000_000:
        return None
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    rb = w * ch
    if len(raw) != (rb + 1) * h:
        return None
    rows = []
    for y in range(h):
        base = y * (rb + 1)
        if raw[base] != 0:
            return None
        rows.append(raw[base + 1 : base + 1 + rb])
    return RasterBuffer(w, h, ch, b"".join(rows), rb)


def shrink_png(data: bytes, factor: float) -> bytes | None:
    """把本机出的 PNG 等比缩到长边 ≤ `LONG_EDGE_PX` 且 ≤ `MAX_PNG_BYTES`（面积平均；超过重采样像素预算的先隔点抽样）。
    解不了 / 缩完仍超限回 None。"""
    from ..rendercore import raster

    buf = _decode_png(data)
    if buf is None:
        return None
    k = 1
    while (buf.width // k) * (buf.height // k) > _RESAMPLE_MAX_PIXELS:
        k += 1
    if k > 1:
        ch = buf.channels
        rows = []
        for y, row in enumerate(buf.rows()):
            if y % k == 0:
                rows.append(b"".join(row[x * ch : (x + 1) * ch] for x in range(0, buf.width, k)))
        w = (buf.width + k - 1) // k
        buf = raster.RasterBuffer(w, len(rows), ch, b"".join(rows), w * ch)
    scale = min(factor, LONG_EDGE_PX / max(buf.width, buf.height) * 0.995, 1.0)
    for _ in range(4):
        w, h = max(1, int(buf.width * scale)), max(1, int(buf.height * scale))
        try:
            out = raster.encode_png(raster.resample_area(buf, w, h, dpi=None, dpi_y=None))
        except raster.RasterError:
            return None
        if max(w, h) <= LONG_EDGE_PX and len(out) <= MAX_PNG_BYTES:
            return out
        scale *= 0.7
    return None


def _size_mm(res: Rendered) -> list[float] | None:
    return manifest_size_mm({"size_mm": getattr(res, "size_mm", None)})


def _render(spec: "_Spec") -> _Artifact:
    """出图：长边 ≤ 3000 px 且 ≤ 8 MiB，超了降分辨率重出；元素表超限按面积从小到大丢。"""
    fn = spec.renderer or _RENDERER  # 随这一次分享走的回调优先（ADR 0205 §三）；没有才用桌面注册的
    if fn is None:
        raise _Failure("render_failed")
    edge = LONG_EDGE_PX
    prev: tuple[int, int] | None = None
    for _ in range(RENDER_ATTEMPTS):
        try:
            with spec.scope():
                res = fn(dict(spec.target), spec.render, edge)
        except _Failure:
            raise
        except Exception as exc:  # noqa: BLE001 — 渲染回调里的任何意外都收成 render_failed
            LOG.warning(
                "外部审阅: 出图失败 类型=%s", type(exc).__name__
            )  # 不带异常文本：里面可能有路径
            raise _Failure("render_failed") from None
        dims = png_size(res.png) if isinstance(res, Rendered) else None
        if dims is None:
            raise _Failure("render_failed")
        long_edge = max(dims)
        factor = 1.0
        if long_edge > LONG_EDGE_PX:
            factor = min(factor, LONG_EDGE_PX / long_edge * 0.99)
        if len(res.png) > MAX_PNG_BYTES:
            factor = min(factor, 0.7)
        if factor < 1.0 and dims == prev:
            # 降了分辨率出来的尺寸还是一样（位图素材保源像素网格、不认 ppi）：再出也一样，改在本机把 PNG 等比缩小
            # （bbox 是比例坐标，不受影响）。缩完仍超限 / 解不了才 too_large。
            small = shrink_png(res.png, factor)
            if small is None:
                raise _Failure("too_large")
            elements, dropped = trim_elements(res.elements)
            return _Artifact(small, elements, _size_mm(res))
        prev = dims
        if factor >= 1.0:
            elements, dropped = trim_elements(res.elements)
            if dropped:
                LOG.info("外部审阅: 元素表超限，丢弃 %d 项", dropped)
            return _Artifact(res.png, elements, _size_mm(res))
        edge = max(int(edge * factor), 64)
    raise _Failure("too_large")


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
def _log(result: str, stage: str) -> None:
    LOG.info(
        "外部审阅: 阶段=%s 结果=%s",
        logsafe.known(stage, STAGES),
        logsafe.known(result, LOG_RESULTS),
    )


@dataclass(frozen=True)
class _Spec:
    mode: str  # new | update
    target: dict  # {"kind", "id"}
    title: str
    note: str
    render: dict = field(repr=False)
    share_id: str = ""
    project: str = ""
    scope: Callable[[], AbstractContextManager] = field(
        default=nullcontext, repr=False, compare=False
    )
    #: 只管这一次分享的出图回调（ADR 0205：MCP 进程里的会话出图）；None = 用模块级 `set_renderer` 注册的（桌面）
    renderer: Callable | None = field(default=None, repr=False, compare=False)

    def fingerprint(self) -> str:
        blob = json.dumps(
            [
                self.mode,
                self.target,
                self.title,
                self.note,
                self.share_id,
                self.project,
                self.render,
            ],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class Session:
    spec: _Spec = field(repr=False)
    state: str = "running"  # running | done | failed | cancelled
    stage: str = "rendering"  # rendering | uploading
    failure: str = ""
    share_id: str = ""
    gen: int = 0
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)

    def public(self) -> dict:
        share = None
        if self.state == "done" and self.share_id:
            rec = reviewinbox.get(self.share_id)
            share = reviewinbox.summary(rec) if rec else None
        if self.state == "running":
            stage = self.stage
        else:
            stage = self.state if self.state in ("done", "failed") else "idle"
        return {
            "stage": stage,
            "failure": self.failure or None,
            "retryable": FAILURES.get(self.failure, False) if self.state == "failed" else False,
            "share": share,
        }


_LOCK = threading.RLock()
_SESSION: Session | None = None
_GEN = 0
#: 最近一次**尝试**的 (指纹, 幂等 id, 渲染产物)：内容没变的重试复用同一个 id 与同一份字节（服务端对同 id 不同指纹回 409）。
_REUSE: dict | None = None


def status() -> dict:
    with _LOCK:
        if _SESSION is None:
            return {"stage": "idle", "failure": None, "retryable": False, "share": None}
        return _SESSION.public()


def _clean_text(value: object, limit: int, code: str, *, required: bool) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ReviewError(code, 422)
    text = value.replace("\x00", "").strip()
    if len(text) > limit or (required and not text):
        raise ReviewError(code, 422)
    return text


def _clean_target(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ReviewError("target_invalid", 422)
    kind, tid = raw.get("kind"), raw.get("id")
    if (
        kind not in reviewinbox.KINDS
        or not isinstance(tid, str)
        or not reviewinbox._TARGET_ID.match(tid)
    ):
        raise ReviewError("target_invalid", 422)
    return {"kind": kind, "id": tid}


def _clean_render(raw: object) -> dict:
    if not isinstance(raw, dict) or not raw:
        raise ReviewError("render_required", 422)
    return raw


def start_share(
    body: object,
    *,
    scope: Callable[[], AbstractContextManager] = nullcontext,
    project: str = "",
    renderer: Callable | None = None,
) -> dict:
    """**用户点了「生成链接」**：render → init → 直传 → complete。已在进行中 → 409。`scope`：后台线程里包住出图的上下文
    （`app.bound_project(ctx)`——线程里没有 request context，不绑定会落到默认项目）。"""
    if not isinstance(body, dict):
        raise ReviewError("body_invalid", 400)
    if resolve_target() is None:
        raise ReviewError("disabled", 404)
    spec = _Spec(
        "new",
        _clean_target(body.get("target")),
        _clean_text(body.get("title"), TITLE_MAX, "title_invalid", required=True),
        _clean_text(body.get("note"), NOTE_MAX, "note_invalid", required=False),
        _clean_render(body.get("render")),
        project=project,
        scope=scope,
        renderer=renderer,
    )
    return _begin(spec)


def render_digest(render: object) -> str:
    """出图描述的规范化摘要：sha256(JSON，键排序、无空格)。「画布自上个版本以来改过没有」只比它。"""
    blob = json.dumps(
        render, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def changed(share_id: object, render: object, *, project: str = "") -> dict:
    """当前 `render` 与最新版本的摘要不同 = 改过。没有摘要的老记录一律 `changed=True`。只读本机，不联网。"""
    clean = _clean_render(render)
    rec = (
        reviewinbox.get(share_id)
        if isinstance(share_id, str) and _SHARE_ID.match(share_id)
        else None
    )
    if rec is None or not rec["delivered"] or rec["project"] != project:
        raise ReviewError("not_found", 404)
    latest = next((v for v in rec["versions"] if v["v"] == rec["latest_version"]), None)
    digest = latest["digest"] if latest else ""
    return {"changed": not digest or digest != render_digest(clean)}


def start_update(
    body: object,
    *,
    scope: Callable[[], AbstractContextManager] = nullcontext,
    project: str = "",
    renderer: Callable | None = None,
) -> dict:
    """**用户点了「更新到同一链接」**：版本 +1。"""
    if not isinstance(body, dict):
        raise ReviewError("body_invalid", 400)
    if resolve_target() is None:
        raise ReviewError("disabled", 404)
    sid = body.get("share_id")
    rec = reviewinbox.get(sid) if isinstance(sid, str) and _SHARE_ID.match(sid) else None
    # 别的项目里的同名图不能把自己的画面推到这条链接上
    if rec is None or not rec["delivered"] or rec["project"] != project:
        raise ReviewError("not_found", 404)
    if rec["revoked"] or reviewinbox.is_expired(rec):
        raise ReviewError("share_gone", 409)
    note = body.get("note")
    target = {"kind": rec["kind"], "id": rec["target_id"]}
    if (
        body.get("target") is not None
    ):  # 把这张图的画面更新到别的链接：成功后本机记录改指新的 target
        target = _clean_target(body["target"])
        if target["kind"] != rec["kind"]:
            raise ReviewError("target_invalid", 422)
    spec = _Spec(
        "update",
        target,
        rec["title"],
        rec["note"]
        if note is None
        else _clean_text(note, NOTE_MAX, "note_invalid", required=False),
        _clean_render(body.get("render")),
        share_id=rec["share_id"],
        project=project,
        scope=scope,
        renderer=renderer,
    )
    return _begin(spec)


def _begin(spec: _Spec) -> dict:
    global _SESSION, _GEN
    with _LOCK:
        if _SESSION is not None and _SESSION.state == "running":
            raise ReviewError("in_progress", 409)
        _GEN += 1
        sess = Session(spec, gen=_GEN, share_id=spec.share_id)
        _SESSION = sess
        sess.thread = threading.Thread(
            target=_run, args=(sess,), name="tavotto-review", daemon=True
        )
        sess.thread.start()
    return {"ok": True}


def cancel() -> None:
    with _LOCK:
        if _SESSION is not None and _SESSION.state == "running":
            _SESSION.cancel.set()


def dismiss() -> dict:
    """界面看过结果：已结束的会话丢掉展示态（失败的幂等 id 与渲染产物另存在 `_REUSE`）；进行中的不丢。"""
    global _SESSION
    with _LOCK:
        if _SESSION is not None and _SESSION.state != "running":
            _SESSION = None
    return status()


def reset_for_tests() -> None:
    global _SESSION, _REUSE, _RENDERER, _time, _sleep, _LAST_POLL
    with _LOCK:
        cur, _SESSION, _REUSE = _SESSION, None, None
    if cur is not None:
        cur.cancel.set()
        if cur.thread is not None and cur.thread.is_alive():
            cur.thread.join(5)
    _RENDERER = None
    _time, _sleep = time.time, _interruptible_sleep
    _LAST_POLL = 0.0


def _run(sess: Session) -> None:
    code, cancelled = "", False
    try:
        _flow(sess)
    except _Cancelled:
        cancelled = True
    except _Failure as fail:
        code = fail.code if fail.code in FAILURES else "client_error"
    except Exception as exc:  # noqa: BLE001 — 线程里的任何意外都收成 client_error
        LOG.error("外部审阅: 内部错误 类型=%s", type(exc).__name__)
        code = "client_error"
    global _REUSE
    with _LOCK:
        if cancelled:
            sess.state = "cancelled"
        elif code:
            sess.state, sess.failure = "failed", code
            if code == "share_gone" and sess.spec.mode == "update":
                reviewinbox.mark_revoked(sess.spec.share_id)
        else:
            sess.state = "done"
            _REUSE = None
        _log(
            "ok" if sess.state == "done" else ("cancelled" if cancelled else code),
            "complete"
            if sess.state == "done"
            else ("render" if sess.stage == "rendering" else "upload"),
        )


def _check_cancel(sess: Session) -> None:
    if sess.cancel.is_set():
        raise _Cancelled


def _set_stage(sess: Session, stage: str) -> None:
    with _LOCK:
        sess.stage = stage


def _flow(sess: Session) -> None:
    global _REUSE
    spec = sess.spec
    target = resolve_target()
    if target is None:
        raise _Cancelled
    fp = spec.fingerprint()
    _set_stage(sess, "rendering")
    with _LOCK:
        reuse = _REUSE if _REUSE and _REUSE["fp"] == fp else None
    if reuse is None:
        art = _render(spec)
        reuse = {"fp": fp, "id": str(uuid.uuid4()), "art": art}
        with _LOCK:
            _REUSE = reuse
    art = reuse["art"]
    _check_cancel(sess)
    _set_stage(sess, "uploading")
    if spec.mode == "new":
        info = _init_share(target, spec, reuse["id"], art)
        version, share_id, token = 1, info["share_id"], info["owner_token"]
        with _LOCK:
            sess.share_id = share_id
    else:
        share_id = spec.share_id
        token = reviewinbox.owner_token_of(share_id)
        if not token:
            raise _Failure("share_gone")
        info = _init_version(target, spec, reuse["id"], art, token)
        version = info["version"]
    _check_cancel(sess)
    _upload(sess, info["upload"], art.png)
    _check_cancel(sess)
    done = _complete(target, share_id, version, token, sess.cancel)
    exp = reviewinbox.parse_iso(
        done.get("expires_at"), reviewinbox.parse_iso(info.get("expires_at"), _time() + 30 * 86400)
    )
    # 远端已发布：本机记录必须落盘，落不下去就失败（可再试：同一个幂等 id 重放、complete 幂等、再补投递），不能报 done
    try:
        saved = reviewinbox.add_version(
            share_id,
            version,
            art.elements,
            art.png,
            expires_ts=exp,
            note=spec.note if spec.mode == "update" else None,
            frame="page" if spec.target["kind"] == "layout" else "figure",
            target_id=spec.target["id"] if spec.mode == "update" else None,
            digest=render_digest(spec.render),
        )
    except OSError:
        LOG.warning("外部审阅: 本机记录落盘失败")
        raise _Failure("server") from None
    if saved is None:
        raise _Failure("client_error")


def _upload_spec(raw: object, target: diagsend.Target) -> dict:
    """服务端 `Upload` → 校验过的 `{url, method, headers, fields}`；地址不在白名单 / 形状不对一律 `client_error`。"""
    bad = _Failure("client_error")
    if not isinstance(raw, dict):
        raise bad
    method, url = raw.get("method"), raw.get("url")
    if (
        method not in ("PUT", "POST")
        or not isinstance(url, str)
        or not url.isascii()
        or len(url) > 2048
    ):
        raise bad
    if any(c in url for c in "\\\r\n\t "):
        raise bad
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise bad from None
    if _is_dev_loopback(target):
        if not _loopback_url(parts):  # 开发模式：任意回环端口 / 路径、http 也行；非回环主机照旧拒
            raise bad
    elif (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or "@" in parts.netloc
        or parts.fragment
    ):
        raise bad
    if _is_dev_loopback(target):
        pass
    elif target.loopback:
        if host != target.host or (port or 443) != target.port:
            raise bad
    elif (
        not host.endswith(UPLOAD_HOST_SUFFIXES)
        or not any(p.match(host) for p in UPLOAD_HOST_PATTERNS)
        or port not in (None, 443)
    ):
        raise bad
    headers: dict[str, str] = {}
    raw_headers = raw.get("headers")
    if raw_headers is not None:
        if not isinstance(raw_headers, dict) or len(raw_headers) > 16:
            raise bad
        for k, v in raw_headers.items():
            low = k.lower() if isinstance(k, str) else ""
            if (
                not _HEADER_NAME.match(k if isinstance(k, str) else "")
                or low in _FORBIDDEN_HEADERS
                or low.startswith("x-tavotto-")
                or not isinstance(v, str)
                or len(v) > 2048
                or not v.isascii()
                or any(ord(c) < 32 or ord(c) == 127 for c in v)
            ):
                raise bad
            headers[k] = v
    fields: list[tuple[str, str]] = []
    raw_fields = raw.get("fields")
    if method == "POST":
        if not isinstance(raw_fields, dict) or len(raw_fields) > diagsend.MAX_UPLOAD_FIELDS:
            raise bad
        for k, v in raw_fields.items():
            if (
                not isinstance(k, str)
                or not diagsend._FIELD_NAME.match(k)
                or k == "file"
                or not isinstance(v, str)
                or len(v) > diagsend.MAX_FIELD_VALUE
            ):
                raise bad
            fields.append((k, v))
    return {"url": url, "method": method, "headers": headers, "fields": tuple(fields)}


class _PutBody:
    """`PUT` 的请求体：file-like、按块读、读之前查取消。"""

    CHUNK = 64 * 1024

    def __init__(self, data: bytes, cancel: threading.Event) -> None:
        self._data, self._pos, self._cancel = data, 0, cancel
        self.length = len(data)

    def read(self, amt: int = -1) -> bytes:
        if self._cancel.is_set():
            raise _Cancelled
        n = self.CHUNK if amt is None or amt < 0 else min(amt, self.CHUNK)
        out = self._data[self._pos : self._pos + n]
        self._pos += len(out)
        return out


def _upload(sess: Session, raw: object, png: bytes) -> None:
    target = resolve_target()
    if target is None:  # 发送途中开关被关了：不再往外发
        raise _Cancelled
    spec = _upload_spec(raw, target)
    headers = {"User-Agent": _UA}
    if spec["method"] == "POST":
        up = diagsend.UploadSpec(spec["url"], spec["fields"], "file", MAX_PNG_BYTES, 0)
        body: object = diagsend._MultipartBody(
            up, png, sess.cancel, lambda *_: None, filename="snapshot.png", content_type="image/png"
        )
        headers["Content-Type"] = body.content_type  # type: ignore[attr-defined]
        headers["Content-Length"] = str(body.length)  # type: ignore[attr-defined]
    else:
        body = _PutBody(png, sess.cancel)
        headers["Content-Type"] = "image/png"
        headers["Content-Length"] = str(body.length)
    headers.update(spec["headers"])
    req = urllib.request.Request(  # noqa: S310 — spec["url"] 已过 `_upload_spec` 的白名单
        spec["url"], data=body, method=spec["method"], headers=headers
    )
    try:
        resp = diagsend._send(req, UPLOAD_TIMEOUT_S)
    except _Failure as fail:
        raise _transport(fail) from None
    if 200 <= resp.status < 300:
        return
    # 401 / 403 / 408：授权过期之类，重新 init 能拿到新的票据——算「服务端」那一类可再试
    if resp.status in (401, 403, 408) or resp.status >= 500:
        raise _Failure("server")
    if resp.status == 413:
        raise _Failure("too_large")
    raise _Failure("client_error")


def _review_url_ok(url: object, target: diagsend.Target) -> bool:
    if not isinstance(url, str) or len(url) > 512 or not url.isascii():
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        return False
    if _is_dev_loopback(target):
        return _loopback_url(parts)
    if (
        parts.scheme != "https"
        or parts.username is not None
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
    ):
        return False
    if not _REVIEW_PATH.match(parts.path):
        return False
    if target.loopback:
        return host == target.host and (port or 443) == target.port
    return host in REVIEW_HOSTS and port in (None, 443)


def _init_share(target: diagsend.Target, spec: _Spec, client_id: str, art: _Artifact) -> dict:
    _log("ok", "render")
    payload = {
        "client_share_id": client_id,
        "kind": spec.target["kind"],
        "title": spec.title,
        "note": spec.note,
        "png_bytes": len(art.png),
        "elements": art.elements,
    }
    if art.size_mm:
        payload["size_mm"] = art.size_mm
    resp = _call(target, "POST", "/v1/shares", payload)
    if resp.status not in (200, 201):
        raise _error_of(resp)
    obj = _json(resp)
    sid, url, token = obj.get("share_id"), obj.get("review_url"), obj.get("owner_token")
    if (
        not isinstance(sid, str)
        or not _SHARE_ID.match(sid)
        or not _review_url_ok(url, target)
        or not isinstance(token, str)
        or not _OWNER_TOKEN.match(token)
        or obj.get("version") != 1
        or not isinstance(obj.get("upload"), dict)
    ):
        raise _Failure("server")
    exp = reviewinbox.parse_iso(obj.get("expires_at"), _time() + 30 * 86400)
    # 令牌一到手就落盘（complete 之前）：complete 的响应丢了，管理令牌也不会丢
    try:
        pending_ok = reviewinbox.add_pending(
            share_id=sid,
            owner_token=token,
            review_url=url,  # type: ignore[arg-type]
            kind=spec.target["kind"],
            target_id=spec.target["id"],
            title=spec.title,
            note=spec.note,
            expires_ts=exp,
            project=spec.project,
        )
    except OSError:
        LOG.warning("外部审阅: 本机记录落盘失败")
        raise _Failure(
            "server"
        ) from None  # 令牌没落盘：重试用同一个 client id，服务端重放同一份结果
    if not pending_ok:
        raise _Failure("client_error")
    _log("ok", "init")
    return {
        "share_id": sid,
        "owner_token": token,
        "upload": obj["upload"],
        "expires_at": obj.get("expires_at"),
    }


def _init_version(
    target: diagsend.Target, spec: _Spec, client_id: str, art: _Artifact, token: str
) -> dict:
    _log("ok", "render")
    payload: dict = {
        "client_version_id": client_id,
        "png_bytes": len(art.png),
        "elements": art.elements,
    }
    if art.size_mm:
        payload["size_mm"] = art.size_mm
    if spec.note:
        payload["note"] = spec.note
    resp = _call(target, "POST", f"/v1/shares/{spec.share_id}/versions", payload, bearer=token)
    if resp.status not in (200, 201):
        raise _error_of(resp)
    obj = _json(resp)
    v = obj.get("version")
    if (
        isinstance(v, bool)
        or not isinstance(v, int)
        or v < 2
        or not isinstance(obj.get("upload"), dict)
    ):
        raise _Failure("server")
    _log("ok", "init")
    return {"version": v, "upload": obj["upload"], "expires_at": obj.get("expires_at")}


def _complete(
    target: diagsend.Target, share_id: str, version: int, token: str, cancel: threading.Event
) -> dict:
    path = f"/v1/shares/{share_id}/versions/{version}/complete"
    last: _Failure | None = None
    for attempt in range(COMPLETE_ATTEMPTS):
        if attempt:
            wait = min(last.retry_after, RETRY_AFTER_CAP_S) if last and last.retry_after else 0
            _sleep(
                cancel, wait or COMPLETE_BACKOFF_S[min(attempt - 1, len(COMPLETE_BACKOFF_S) - 1)]
            )
        if cancel.is_set():  # 每次发送前查：取消之后（含退避等待中被取消）不再发
            raise _Cancelled
        try:
            resp = _call(target, "POST", path, None, bearer=token, timeout=COMPLETE_TIMEOUT_S)
        except _Failure as fail:
            if fail.code != "network":  # 响应可能丢在路上：complete 幂等，同样的请求再发
                raise
            last = fail
            continue
        if resp.status == 200:
            obj = _json(resp)
            if obj.get("version") != version:
                raise _Failure("server")
            _log("ok", "complete")
            return obj
        fail = _error_of(resp)
        if resp.status >= 500:
            last = fail
            continue
        raise fail
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# 停止分享 / 回复 / 标记已处理（同步，在请求线程里）
# ---------------------------------------------------------------------------
def _owned(share_id: str) -> tuple[diagsend.Target, dict]:
    target = resolve_target()
    if target is None:
        raise ReviewError("disabled", 404)
    rec = reviewinbox.get(share_id) if _SHARE_ID.match(share_id or "") else None
    if rec is None or not rec["delivered"]:
        raise ReviewError("not_found", 404)
    return target, rec


def revoke(share_id: str) -> dict:
    """停止分享：服务端删画面与意见，本机记录标 revoked（意见副本保留）。服务端已经没有它（404）也算成功。"""
    target, rec = _owned(share_id)
    if not rec["revoked"]:
        try:
            resp = _call(
                target, "DELETE", f"/v1/shares/{share_id}", None, bearer=rec["owner_token"]
            )
            if (
                resp.status not in (200, 204)
                and classify(resp.status, _server_code(resp)) != "share_gone"
            ):
                raise _error_of(resp)
        except _Failure as fail:
            _log(fail.code, "revoke")
            return {"ok": False, "failure": fail.code if fail.code in FAILURES else "client_error"}
        reviewinbox.mark_revoked(share_id)
    _log("ok", "revoke")
    return {"ok": True}


def _clean_reply_text(text: object) -> str:
    if not isinstance(text, str):
        raise ReviewError("text_invalid", 422)
    clean = text.replace("\x00", "").strip()
    if not clean or len(clean) > TEXT_MAX:
        raise ReviewError("text_invalid", 422)
    return clean


def reply(share_id: str, comment_id: str, text: object) -> dict:
    clean = _clean_reply_text(text)
    target, rec = _owned(share_id)
    if (
        not reviewinbox._ID.match(comment_id or "")
        or reviewinbox.comment_of(share_id, comment_id) is None
    ):
        raise ReviewError("not_found", 404)
    if rec["revoked"] or reviewinbox.is_expired(rec):
        return {"ok": False, "failure": "share_gone"}
    path = f"/v1/shares/{share_id}/comments/{comment_id}/replies"
    try:
        resp = _call(target, "POST", path, {"text": clean}, bearer=rec["owner_token"])
        if resp.status not in (200, 201):
            raise _error_of(resp)
        r = reviewinbox.clean_reply(_json(resp).get("reply"))
        if r is None:
            raise _Failure("server")
    except _Failure as fail:
        _log(fail.code, "reply")
        return {"ok": False, "failure": fail.code if fail.code in FAILURES else "client_error"}
    comment = reviewinbox.apply_reply(share_id, comment_id, r)
    _log("ok", "reply")
    return {"ok": True, "comment": comment}


def resolve(share_id: str, comment_id: str, resolved: object) -> dict:
    if not isinstance(resolved, bool):
        raise ReviewError("resolved_invalid", 422)
    target, rec = _owned(share_id)
    if (
        not reviewinbox._ID.match(comment_id or "")
        or reviewinbox.comment_of(share_id, comment_id) is None
    ):
        raise ReviewError("not_found", 404)
    if rec["revoked"] or reviewinbox.is_expired(rec):
        return {"ok": False, "failure": "share_gone"}
    path = f"/v1/shares/{share_id}/comments/{comment_id}"
    try:
        resp = _call(target, "PATCH", path, {"resolved": resolved}, bearer=rec["owner_token"])
        if resp.status != 200:
            raise _error_of(resp)
        comment = reviewinbox.apply_comment(share_id, _json(resp).get("comment") or {})
        if comment is None:
            raise _Failure("server")
    except _Failure as fail:
        _log(fail.code, "resolve")
        return {"ok": False, "failure": fail.code if fail.code in FAILURES else "client_error"}
    _log("ok", "resolve")
    return {"ok": True, "comment": comment}


# ---------------------------------------------------------------------------
# feed（轮询）
# ---------------------------------------------------------------------------
_LAST_POLL = 0.0
_POLL_LOCK = threading.Lock()


def _pull_feed(target: diagsend.Target, rec: dict) -> str:
    """问一份记录的 feed 并合并。返回 `ok` 或失败码（`network` / `server` / `share_gone` …）。"""
    path = f"/v1/shares/{rec['share_id']}/feed"
    if rec["cursor"]:
        path += "?since=" + urllib.parse.quote(rec["cursor"], safe="")
    try:
        resp = _call(
            target,
            "GET",
            path,
            None,
            bearer=rec["owner_token"],
            timeout=FEED_TIMEOUT_S,
            limit=FEED_MAX_BYTES + 1,  # 多读 1 字节：读满上限 + 1 = 超限，而不是被静默截成坏 JSON
        )
        if resp.status != 200:
            raise _error_of(resp)
        if len(resp.body) > FEED_MAX_BYTES:
            raise _Failure("server")
        obj = _json(resp)
        if not isinstance(obj.get("events"), list) or not isinstance(obj.get("cursor"), str):
            raise _Failure("server")
    except _Failure as fail:
        if fail.code == "share_gone":
            reviewinbox.mark_revoked(rec["share_id"])  # 服务端没有它了：停止轮询（意见副本保留）
        _log(fail.code, "feed")
        return fail.code
    reviewinbox.merge_feed(rec["share_id"], obj)
    _log("ok", "feed")
    return "ok"


def poll_once(force: bool = False, share_id: str | None = None) -> dict:
    """问所有（或指定一份）仍有效记录的 feed。**没有未到期且未 revoked 的记录 = 一次网络都不发。**
    `force` 以外最少隔 `REFRESH_MIN_S`。返回 `{"polled": n, "ok": bool}`。"""
    global _LAST_POLL
    target = resolve_target()
    recs = reviewinbox.pollable() if target is not None else []
    if share_id:
        recs = [r for r in recs if r["share_id"] == share_id]
    if target is None or not recs:
        return {"polled": 0, "ok": target is not None}
    with _POLL_LOCK:
        now = time.monotonic()
        if not force and _LAST_POLL and now - _LAST_POLL < REFRESH_MIN_S:
            return {"polled": 0, "ok": True, "throttled": True}
        _LAST_POLL = now
        n, ok = 0, True
        for rec in recs:
            res = _pull_feed(target, rec)
            if res == "ok":
                n += 1
            elif res in ("share_gone", "client_error"):
                ok = False
            else:
                ok = False
                break  # 线路不通 / 被限流 / 服务端出错：本轮到此为止
    return {"polled": n, "ok": ok}


_poller_started = threading.Event()
poll_wake = threading.Event()


def start_poller() -> None:
    """启动时一次 + 每 30 分钟一次的后台轮询（守护线程；功能关闭 / 没有记录时什么网络都不发）。幂等。"""
    if _poller_started.is_set() or _flag(ENV_HARD_OFF):
        return
    _poller_started.set()

    def loop() -> None:
        poll_wake.wait(POLL_STARTUP_DELAY_S)
        while True:
            poll_wake.clear()
            try:
                poll_once(force=True)
            except Exception:  # noqa: BLE001 — 后台探测不允许把主进程带下水
                pass
            poll_wake.wait(POLL_INTERVAL_S)

    threading.Thread(target=loop, name="tavotto-review-poll", daemon=True).start()
